/**
 * P2.5 web half: barge-in through the real PlaybackScheduler (fake AudioContext clock) and the
 * real CallController, so the order of local stop, stale-audio drop and `played` is checked end
 * to end in the browser logic.
 */
import { describe, expect, it } from "vitest";
import { PlaybackScheduler, type PlaybackContext } from "../audio/scheduler";
import { CallController, type Capture, type Transport } from "./controller";

class Src {
  buffer: { length: number; duration: number; getChannelData(): Float32Array } | null = null;
  onended: (() => void) | null = null;
  stopped = false;
  connect(): void {}
  start(): void {}
  stop(): void {
    this.stopped = true;
    this.onended?.();
  }
}

class Ctx implements PlaybackContext {
  currentTime = 0;
  destination = {};
  sources: Src[] = [];
  createBuffer(_c: number, length: number, rate: number) {
    const data = new Float32Array(length);
    return { length, duration: length / rate, getChannelData: () => data };
  }
  createBufferSource(): Src {
    const s = new Src();
    this.sources.push(s);
    return s;
  }
}

class Wire implements Transport {
  out: Array<string | ArrayBuffer> = [];
  onMessage: (d: string | ArrayBuffer) => void = () => {};
  onClose: (r: string) => void = () => {};
  send(d: string | ArrayBuffer): void {
    this.out.push(d);
  }
  close(): void {}
  types(): string[] {
    return this.out.filter((d): d is string => typeof d === "string").map((d) => JSON.parse(d).type);
  }
  playedMsgs(): Array<[number, number]> {
    return this.out
      .filter((d): d is string => typeof d === "string")
      .map((d) => JSON.parse(d))
      .filter((m) => m.type === "played")
      .map((m) => [m.turn, m.sentence]);
  }
  serve(m: object): void {
    this.onMessage(JSON.stringify(m));
  }
}

class NoMic implements Capture {
  onFrame: (f: ArrayBuffer) => void = () => {};
  start(): void {}
  async flush(): Promise<ArrayBuffer | null> {
    return null;
  }
}

const chunk = () => new ArrayBuffer(4800); // 100 ms at 24 kHz

function setup() {
  const ctx = new Ctx();
  const wire = new Wire();
  const c = new CallController(wire, new PlaybackScheduler(ctx), new NoMic());
  return { ctx, wire, c };
}

describe("barge-in (controller + real scheduler)", () => {
  it("reports only the sentences that finished before the interruption", () => {
    const { ctx, wire, c } = setup();
    // turn 4, sentence 0: fully played
    wire.serve({ type: "audio_start", turn: 4, sentence: 0, rate: 24000 });
    wire.onMessage(chunk());
    wire.serve({ type: "audio_end", turn: 4, sentence: 0 });
    ctx.sources[0]!.onended!();
    // turn 4, sentence 1: still playing when the trainee presses talk
    wire.serve({ type: "audio_start", turn: 4, sentence: 1, rate: 24000 });
    wire.onMessage(chunk());
    wire.serve({ type: "audio_end", turn: 4, sentence: 1 });
    c.pttStart();
    expect(ctx.sources[1]!.stopped).toBe(true);
    expect(wire.playedMsgs()).toEqual([[4, 0]]);
    expect(wire.types().at(-1)).toBe("ptt_start");
  });

  it("drops audio of the interrupted turn that was already in flight", () => {
    const { ctx, wire, c } = setup();
    wire.serve({ type: "audio_start", turn: 4, sentence: 1, rate: 24000 });
    wire.onMessage(chunk());
    c.pttStart();
    const before = ctx.sources.length;
    // late frames of turn 4 arrive before the backend has processed ptt_start
    wire.onMessage(chunk());
    wire.serve({ type: "audio_start", turn: 4, sentence: 2, rate: 24000 });
    wire.onMessage(chunk());
    wire.serve({ type: "audio_end", turn: 4, sentence: 2 });
    expect(ctx.sources.length).toBe(before);
    expect(wire.playedMsgs()).toEqual([]);
  });

  it("plays the next borrower turn normally after the barge-in", async () => {
    const { ctx, wire, c } = setup();
    wire.serve({ type: "audio_start", turn: 4, sentence: 0, rate: 24000 });
    wire.onMessage(chunk());
    c.pttStart();
    await c.pttEnd();
    wire.serve({ type: "stop_audio" }); // backend confirms
    wire.serve({ type: "audio_start", turn: 5, sentence: 0, rate: 24000 });
    wire.onMessage(chunk());
    wire.serve({ type: "audio_end", turn: 5, sentence: 0 });
    const last = ctx.sources.at(-1)!;
    expect(last.stopped).toBe(false);
    last.onended!();
    expect(wire.playedMsgs()).toEqual([[5, 0]]);
  });

  it("a stopped source firing ended late never produces played", () => {
    const { ctx, wire, c } = setup();
    wire.serve({ type: "audio_start", turn: 2, sentence: 0, rate: 24000 });
    wire.onMessage(chunk());
    wire.serve({ type: "audio_end", turn: 2, sentence: 0 });
    const src = ctx.sources[0]!;
    const ended = src.onended!;
    c.pttStart(); // stop() already fired onended once
    ended(); // and the browser fires it again
    expect(wire.playedMsgs()).toEqual([]);
  });
});
