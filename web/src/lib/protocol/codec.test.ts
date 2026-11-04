import { describe, expect, it } from "vitest";
import { decodeServer, encodeClient } from "./codec";

const ramesh = {
  id: "ramesh",
  version: 1,
  display_name: "Ramesh bhaiya",
  language: "Hinglish",
  difficulty_default: "medium",
  profile: { age: 41, occupation: "auto-rickshaw driver", city: "Delhi" },
  loan: {
    lender: "SwiftFin",
    product: "two-wheeler loan",
    emi: 2500,
    overdue_amount: 12500,
    dpd: 23,
    past_promises: 2,
  },
};

describe("encodeClient", () => {
  it("encodes start_call with persona, difficulty and anon_id", () => {
    const text = encodeClient({
      type: "start_call",
      persona: "ramesh",
      difficulty: "medium",
      anon_id: "anon-7f3a",
    });
    expect(JSON.parse(text)).toEqual({
      type: "start_call",
      persona: "ramesh",
      difficulty: "medium",
      anon_id: "anon-7f3a",
    });
  });

  it("encodes played with turn and sentence", () => {
    expect(JSON.parse(encodeClient({ type: "played", turn: 3, sentence: 1 }))).toEqual({
      type: "played",
      turn: 3,
      sentence: 1,
    });
  });

  it("encodes the field-less messages as a bare type", () => {
    for (const type of ["ptt_start", "ptt_end", "end_call"] as const) {
      expect(JSON.parse(encodeClient({ type }))).toEqual({ type });
    }
  });
});

describe("decodeServer", () => {
  it("decodes a borrower transcript line in Devanagari Hinglish", () => {
    const r = decodeServer(
      JSON.stringify({ type: "transcript", role: "borrower", text: "Haan, hello? कौन बोल रहा है?", t: 1.2 }),
    );
    expect(r).toEqual({
      ok: true,
      msg: { type: "transcript", role: "borrower", text: "Haan, hello? कौन बोल रहा है?", t: 1.2 },
    });
  });

  it("decodes audio_start with turn, sentence and rate", () => {
    const r = decodeServer('{"type":"audio_start","turn":2,"sentence":0,"rate":24000}');
    expect(r).toEqual({ ok: true, msg: { type: "audio_start", turn: 2, sentence: 0, rate: 24000 } });
  });

  it("decodes audio_end without a rate", () => {
    const r = decodeServer('{"type":"audio_end","turn":2,"sentence":0}');
    expect(r).toEqual({ ok: true, msg: { type: "audio_end", turn: 2, sentence: 0 } });
  });

  it("decodes a flag carrying the tripwire flag object", () => {
    const flag = {
      id: "f1",
      turn: 4,
      segment: 0,
      t: 41.5,
      rule: "threat",
      quote: "police bhej denge ghar pe",
      layer: "lexicon",
      status: "possible",
    };
    expect(decodeServer(JSON.stringify({ type: "flag", flag }))).toEqual({
      ok: true,
      msg: { type: "flag", flag },
    });
    expect(decodeServer(JSON.stringify({ type: "flag_update", flag: { ...flag, status: "withdrawn" } }))).toEqual({
      ok: true,
      msg: { type: "flag_update", flag: { ...flag, status: "withdrawn" } },
    });
  });

  it("decodes a briefing with the call id, the public card and the simulated clock", () => {
    const r = decodeServer(
      JSON.stringify({ type: "briefing", call_id: "c-01", facts: ramesh, sim_clock: "2026-11-09T18:40" }),
    );
    expect(r).toEqual({
      ok: true,
      msg: { type: "briefing", call_id: "c-01", facts: ramesh, sim_clock: "2026-11-09T18:40" },
    });
  });

  it("drops hidden fields from the briefing card even if the backend sent them", () => {
    const r = decodeServer(
      JSON.stringify({ type: "briefing", call_id: "c-01", facts: { ...ramesh, hidden: { truth: "x" } }, sim_clock: "2026-11-09T18:40" }),
    );
    expect(r.ok && r.msg.type === "briefing" && Object.keys(r.msg.facts)).not.toContain("hidden");
  });

  it("decodes busy, mood, stop_audio, call_ended and error", () => {
    expect(decodeServer('{"type":"busy","position":3,"eta_s":240}')).toEqual({
      ok: true,
      msg: { type: "busy", position: 3, eta_s: 240 },
    });
    expect(decodeServer('{"type":"mood","label":"irritated"}')).toEqual({
      ok: true,
      msg: { type: "mood", label: "irritated" },
    });
    expect(decodeServer('{"type":"stop_audio"}')).toEqual({ ok: true, msg: { type: "stop_audio" } });
    expect(decodeServer('{"type":"call_ended","reason":"agent_hangup"}')).toEqual({
      ok: true,
      msg: { type: "call_ended", reason: "agent_hangup" },
    });
    expect(decodeServer('{"type":"error","code":"stt_down","message":"Prisma unreachable"}')).toEqual({
      ok: true,
      msg: { type: "error", code: "stt_down", message: "Prisma unreachable" },
    });
  });

  it("rejects text that is not JSON", () => {
    expect(decodeServer("not json").ok).toBe(false);
  });

  it("rejects an unknown type", () => {
    const r = decodeServer('{"type":"teleport"}');
    expect(r).toEqual({ ok: false, error: "unknown type: teleport" });
  });

  it("rejects a known type with a wrongly typed field", () => {
    const r = decodeServer('{"type":"audio_start","turn":"2","sentence":0,"rate":24000}');
    expect(r).toEqual({ ok: false, error: "audio_start.turn: expected integer" });
  });

  it("rejects a flag whose status is not possible, confirmed or withdrawn", () => {
    const r = decodeServer(
      JSON.stringify({
        type: "flag",
        flag: { id: "f1", turn: 1, segment: 0, t: 1, rule: "threat", quote: "x", layer: "lexicon", status: "maybe" },
      }),
    );
    expect(r.ok).toBe(false);
  });

  it("rejects a transcript role other than agent or borrower", () => {
    expect(decodeServer('{"type":"transcript","role":"system","text":"x","t":0}').ok).toBe(false);
  });
});
