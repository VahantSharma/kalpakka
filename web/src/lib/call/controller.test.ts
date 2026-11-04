import { describe, expect, it } from "vitest";
import type { Flag } from "../protocol/types";
import { CallController, type Capture, type Player, type Transport } from "./controller";

const log: string[] = [];

class FakeTransport implements Transport {
  sent: Array<string | ArrayBuffer> = [];
  closed = false;
  onMessage: (data: string | ArrayBuffer) => void = () => {};
  onClose: (reason: string) => void = () => {};
  send(data: string | ArrayBuffer): void {
    log.push(typeof data === "string" ? `send:${JSON.parse(data).type}` : "send:frame");
    this.sent.push(data);
  }
  close(): void {
    this.closed = true;
  }
  json(): Array<Record<string, unknown>> {
    return this.sent.filter((d): d is string => typeof d === "string").map((d) => JSON.parse(d));
  }
  frames(): ArrayBuffer[] {
    return this.sent.filter((d): d is ArrayBuffer => typeof d !== "string");
  }
  serve(msg: object): void {
    this.onMessage(JSON.stringify(msg));
  }
}

class FakePlayer implements Player {
  calls: string[] = [];
  minTurn = 0;
  onPlayed: (turn: number, sentence: number) => void = () => {};
  begin(turn: number, sentence: number, rate: number): void {
    this.calls.push(`begin ${turn}.${sentence}@${rate}`);
  }
  push(chunk: ArrayBuffer): void {
    this.calls.push(`push ${chunk.byteLength}`);
  }
  end(turn: number, sentence: number): void {
    this.calls.push(`end ${turn}.${sentence}`);
  }
  stopAll(): void {
    log.push("player:stopAll");
    this.calls.push("stopAll");
  }
  dropTurnsBelow(turn: number): void {
    this.minTurn = turn;
    this.calls.push(`drop<${turn}`);
  }
}

class FakeCapture implements Capture {
  onFrame: (frame: ArrayBuffer) => void = () => {};
  starts = 0;
  remainder: ArrayBuffer | null = new ArrayBuffer(1024);
  start(): void {
    this.starts++;
  }
  async flush(): Promise<ArrayBuffer | null> {
    return this.remainder;
  }
}

function setup() {
  log.length = 0;
  const transport = new FakeTransport();
  const player = new FakePlayer();
  const capture = new FakeCapture();
  const c = new CallController(transport, player, capture);
  return { transport, player, capture, c };
}

const threat: Flag = {
  id: "f1",
  turn: 3,
  segment: 0,
  t: 41.5,
  rule: "threat",
  quote: "police bhej denge ghar pe",
  layer: "lexicon",
  status: "possible",
};

describe("CallController: start and briefing", () => {
  it("sends start_call with persona, difficulty and anon_id", () => {
    const { transport, c } = setup();
    c.start("ramesh", "medium", "anon-7f3a");
    expect(transport.json()).toEqual([
      { type: "start_call", persona: "ramesh", difficulty: "medium", anon_id: "anon-7f3a" },
    ]);
    expect(c.state.phase).toBe("connecting");
  });

  it("shows the queue position while busy", () => {
    const { transport, c } = setup();
    c.start("ramesh", "medium", "a");
    transport.serve({ type: "busy", position: 3, eta_s: 240 });
    expect(c.state.phase).toBe("queued");
    expect(c.state.queue).toEqual({ position: 3, eta_s: 240 });
  });

  it("enters the call on briefing", () => {
    const { transport, c } = setup();
    c.start("ramesh", "medium", "a");
    transport.serve({
      type: "briefing",
      call_id: "c-01",
      sim_clock: "2026-11-09T18:40",
      facts: {
        id: "ramesh",
        version: 1,
        display_name: "Ramesh bhaiya",
        language: "Hinglish",
        difficulty_default: "medium",
        profile: { age: 41, occupation: "auto-rickshaw driver", city: "Delhi" },
        loan: { lender: "SwiftFin", product: "two-wheeler loan", emi: 2500, overdue_amount: 12500, dpd: 23, past_promises: 2 },
      },
    });
    expect(c.state.phase).toBe("in_call");
    expect(c.state.briefing?.sim_clock).toBe("2026-11-09T18:40");
    expect(c.state.briefing?.call_id).toBe("c-01");
    expect(c.state.briefing?.card.loan.overdue_amount).toBe(12500);
  });
});

describe("CallController: borrower audio", () => {
  it("routes audio_start, binary chunks and audio_end to the player in order", () => {
    const { transport, player, c } = setup();
    transport.serve({ type: "audio_start", turn: 1, sentence: 0, rate: 24000 });
    transport.onMessage(new ArrayBuffer(4800));
    transport.serve({ type: "audio_end", turn: 1, sentence: 0 });
    expect(player.calls).toEqual(["begin 1.0@24000", "push 4800", "end 1.0"]);
    expect(c.state.borrowerSpeaking).toBe(true);
  });

  it("sends played for each sentence the player reports finished", () => {
    const { transport, player } = setup();
    player.onPlayed(1, 0);
    player.onPlayed(1, 1);
    expect(transport.json()).toEqual([
      { type: "played", turn: 1, sentence: 0 },
      { type: "played", turn: 1, sentence: 1 },
    ]);
  });

  it("stops all audio on stop_audio", () => {
    const { transport, player } = setup();
    transport.serve({ type: "stop_audio" });
    expect(player.calls).toEqual(["stopAll"]);
  });
});

describe("CallController: push-to-talk and barge-in", () => {
  it("stops playback locally before sending ptt_start", async () => {
    const { transport, c } = setup();
    transport.serve({ type: "audio_start", turn: 2, sentence: 0, rate: 24000 });
    log.length = 0;
    c.pttStart();
    expect(log).toEqual(["player:stopAll", "send:ptt_start"]);
  });

  it("drops stale audio of the interrupted turn and earlier", () => {
    const { transport, player, c } = setup();
    transport.serve({ type: "audio_start", turn: 2, sentence: 1, rate: 24000 });
    c.pttStart();
    expect(player.minTurn).toBe(3);
  });

  it("streams mic frames only while talk is held", async () => {
    const { transport, capture, c } = setup();
    capture.onFrame(new ArrayBuffer(1024)); // before press: not sent
    c.pttStart();
    capture.onFrame(new ArrayBuffer(1024));
    capture.onFrame(new ArrayBuffer(1024));
    await c.pttEnd();
    capture.onFrame(new ArrayBuffer(1024)); // after release: not sent
    // two live frames + the zero-padded remainder from flush
    expect(transport.frames()).toHaveLength(3);
    expect(transport.frames().every((f) => f.byteLength === 1024)).toBe(true);
  });

  it("sends the flushed remainder before ptt_end", async () => {
    const { c } = setup();
    c.pttStart();
    log.length = 0;
    await c.pttEnd();
    expect(log).toEqual(["send:frame", "send:ptt_end"]);
  });

  it("sends ptt_end even when nothing is left to flush", async () => {
    const { transport, capture, c } = setup();
    capture.remainder = null;
    c.pttStart();
    await c.pttEnd();
    expect(transport.json().map((m) => m.type)).toEqual(["ptt_start", "ptt_end"]);
  });

  it("ignores a second press while already talking (spacebar auto-repeat)", () => {
    const { transport, capture, c } = setup();
    c.pttStart();
    c.pttStart();
    c.pttStart();
    expect(transport.json().filter((m) => m.type === "ptt_start")).toHaveLength(1);
    expect(capture.starts).toBe(1);
  });

  it("a re-press during the release flush starts talking right after ptt_end", async () => {
    const { transport, c } = setup();
    c.pttStart();
    const releasing = c.pttEnd();
    c.pttStart(); // pressed again while the flush is in flight
    expect(transport.json().map((m) => m.type)).toEqual(["ptt_start"]);
    await releasing;
    expect(transport.json().map((m) => m.type)).toEqual(["ptt_start", "ptt_end", "ptt_start"]);
    expect(c.state.talking).toBe(true);
  });

  it("a re-press that is released again during the flush is dropped", async () => {
    const { transport, c } = setup();
    c.pttStart();
    const releasing = c.pttEnd();
    c.pttStart();
    void c.pttEnd();
    await releasing;
    expect(transport.json().map((m) => m.type)).toEqual(["ptt_start", "ptt_end"]);
    expect(c.state.talking).toBe(false);
  });

  it("ignores borrower audio that starts while talk is held (reply already in flight)", () => {
    const { transport, player, c } = setup();
    transport.serve({ type: "audio_start", turn: 4, sentence: 0, rate: 24000 });
    c.pttStart();
    player.calls.length = 0;
    transport.serve({ type: "audio_start", turn: 5, sentence: 0, rate: 24000 });
    transport.onMessage(new ArrayBuffer(4800));
    transport.serve({ type: "audio_end", turn: 5, sentence: 0 });
    expect(player.calls.filter((x) => x.startsWith("begin") || x.startsWith("push"))).toEqual([]);
    expect(c.state.borrowerSpeaking).toBe(false);
  });

  it("drops the ignored turn too, so its late sentences never play after release", async () => {
    const { transport, player, c } = setup();
    transport.serve({ type: "audio_start", turn: 4, sentence: 0, rate: 24000 });
    c.pttStart();
    transport.serve({ type: "audio_start", turn: 5, sentence: 0, rate: 24000 });
    await c.pttEnd();
    expect(player.minTurn).toBe(6);
  });

  it("stop_audio drops every turn seen so far", () => {
    const { transport, player } = setup();
    transport.serve({ type: "audio_start", turn: 7, sentence: 2, rate: 24000 });
    transport.serve({ type: "stop_audio" });
    expect(player.minTurn).toBe(8);
  });

  it("does not send ptt_end after a hang-up during the release flush", async () => {
    const { transport, c } = setup();
    c.pttStart();
    const releasing = c.pttEnd();
    c.hangUp();
    await releasing;
    expect(transport.json().map((m) => m.type)).toEqual(["ptt_start", "end_call"]);
  });

  it("dispose mid-call sends end_call, silences audio and closes the socket", () => {
    const { transport, player, c } = setup();
    c.start("ramesh", "medium", "a");
    c.dispose();
    expect(transport.json().map((m) => m.type)).toEqual(["start_call", "end_call"]);
    expect(player.calls).toContain("stopAll");
    expect(transport.closed).toBe(true);
  });

  it("dispose after the scorecard only closes the socket", () => {
    const { transport, c } = setup();
    transport.serve({ type: "scorecard", scorecard: { total: 61 } });
    transport.closed = false;
    c.dispose();
    expect(transport.json()).toEqual([]);
    expect(transport.closed).toBe(true);
  });

  it("ignores a release without a press", async () => {
    const { transport, c } = setup();
    await c.pttEnd();
    expect(transport.sent).toHaveLength(0);
  });

  it("resets the capture pipeline at each press", () => {
    const { capture, c } = setup();
    c.pttStart();
    expect(capture.starts).toBe(1);
    expect(c.state.talking).toBe(true);
  });
});

describe("CallController: transcript, flags, mood", () => {
  it("appends transcript lines as captions", () => {
    const { transport, c } = setup();
    transport.serve({ type: "transcript", role: "borrower", text: "Haan, hello? Kaun bol raha hai?", t: 0.4 });
    transport.serve({ type: "transcript", role: "agent", text: "Namaste Ramesh ji, main SwiftFin se Pooja bol rahi hoon", t: 3.1 });
    expect(c.state.transcript.map((l) => l.role)).toEqual(["borrower", "agent"]);
  });

  it("adds a flag and updates it in place by id", () => {
    const { transport, c } = setup();
    transport.serve({ type: "flag", flag: threat });
    transport.serve({ type: "flag_update", flag: { ...threat, status: "confirmed", layer: "llm" } });
    expect(c.state.flags).toEqual([{ ...threat, status: "confirmed", layer: "llm" }]);
  });

  it("keeps a withdrawn flag visible with its withdrawn status", () => {
    const { transport, c } = setup();
    transport.serve({ type: "flag", flag: threat });
    transport.serve({ type: "flag_update", flag: { ...threat, status: "withdrawn" } });
    expect(c.state.flags[0]?.status).toBe("withdrawn");
  });

  it("treats a flag_update for an unseen id as a new flag", () => {
    const { transport, c } = setup();
    transport.serve({ type: "flag_update", flag: threat });
    expect(c.state.flags).toHaveLength(1);
  });

  it("shows the coarse mood label", () => {
    const { transport, c } = setup();
    transport.serve({ type: "mood", label: "irritated" });
    expect(c.state.mood).toBe("irritated");
  });
});

describe("CallController: ending", () => {
  it("hang-up silences locally, then sends end_call", () => {
    const { c } = setup();
    log.length = 0;
    c.hangUp();
    expect(log).toEqual(["player:stopAll", "send:end_call"]);
  });

  it("call_ended stops audio and waits for the scorecard", () => {
    const { transport, player, c } = setup();
    transport.serve({ type: "call_ended", reason: "borrower_hangup" });
    expect(player.calls).toContain("stopAll");
    expect(c.state.phase).toBe("ended");
    expect(c.state.endReason).toBe("borrower_hangup");
  });

  it("stores the scorecard and closes the socket", () => {
    const { transport, c } = setup();
    transport.serve({ type: "call_ended", reason: "agent_hangup" });
    transport.serve({ type: "scorecard", scorecard: { total: 61 } });
    expect(c.state.phase).toBe("scored");
    expect(c.state.scorecard).toEqual({ total: 61 });
    expect(transport.closed).toBe(true);
  });

  it("socket close mid-call ends the call with the close reason", () => {
    const { transport, c } = setup();
    c.start("ramesh", "easy", "a");
    transport.onClose("socket_closed");
    expect(c.state.phase).toBe("ended");
    expect(c.state.endReason).toBe("socket_closed");
  });

  it("socket close after call_ended without a scorecard marks the scorecard unavailable", () => {
    const { transport, c } = setup();
    transport.serve({ type: "call_ended", reason: "agent_hangup" });
    expect(c.state.scoreUnavailable).toBe(false);
    transport.onClose("closed");
    expect(c.state.phase).toBe("ended");
    expect(c.state.endReason).toBe("agent_hangup");
    expect(c.state.scoreUnavailable).toBe(true);
  });

  it("socket close after the scorecard keeps the scored state", () => {
    const { transport, c } = setup();
    transport.serve({ type: "scorecard", scorecard: { total: 61 } });
    transport.onClose("socket_closed");
    expect(c.state.phase).toBe("scored");
  });
});

describe("CallController: errors", () => {
  it("records a recoverable error without ending the call", () => {
    const { transport, c } = setup();
    transport.serve({ type: "error", code: "stt_slow", message: "Prisma is slow" });
    expect(c.state.error).toEqual({ code: "stt_slow", message: "Prisma is slow" });
    expect(c.state.phase).toBe("idle");
  });

  it("records a malformed server frame as a protocol error and keeps going", () => {
    const { transport, c } = setup();
    transport.onMessage('{"type":"teleport"}');
    expect(c.state.error?.code).toBe("protocol");
    transport.serve({ type: "mood", label: "calmer" });
    expect(c.state.mood).toBe("calmer");
  });

  it("notifies subscribers on every state change", () => {
    const { transport, c } = setup();
    let n = 0;
    c.subscribe(() => n++);
    transport.serve({ type: "mood", label: "calmer" });
    transport.serve({ type: "transcript", role: "borrower", text: "Theek hai", t: 1 });
    expect(n).toBe(2);
  });
});
