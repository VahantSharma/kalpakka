import { describe, expect, it } from "vitest";
import type { Flag } from "../protocol/types";
import { RULE_LABELS, endReasonText, flagView, formatClock, formatSimClock } from "./flags";

const flag = (status: Flag["status"], rule: Flag["rule"] = "threat"): Flag => ({
  id: "f1",
  turn: 5,
  segment: 0,
  t: 86.4,
  rule,
  quote: "police bhej denge ghar pe",
  layer: "lexicon",
  status,
});

describe("flagView", () => {
  it("shows a possible flag in amber", () => {
    expect(flagView(flag("possible"))).toMatchObject({ tone: "amber", statusLabel: "possible", note: null });
  });

  it("shows a confirmed flag in red", () => {
    expect(flagView(flag("confirmed"))).toMatchObject({ tone: "red", statusLabel: "confirmed", note: null });
  });

  it("fades a withdrawn flag and says it was fine on reflection", () => {
    expect(flagView(flag("withdrawn"))).toMatchObject({
      tone: "faded",
      statusLabel: "withdrawn",
      note: "on reflection, this was fine",
    });
  });

  it("names the rule in plain words and quotes the agent's words", () => {
    const v = flagView(flag("possible", "third_party_disclosure"));
    expect(v.ruleLabel).toBe(RULE_LABELS.third_party_disclosure);
    expect(v.quote).toBe("“police bhej denge ghar pe”");
    expect(v.at).toBe("1:26");
  });

  it("has a label for every rule in the catalogue", () => {
    for (const label of Object.values(RULE_LABELS)) expect(label.length).toBeGreaterThan(3);
    expect(Object.keys(RULE_LABELS)).toHaveLength(8);
  });
});

describe("formatSimClock", () => {
  it("shows the time of day from the backend's ISO simulated clock", () => {
    expect(formatSimClock("2026-11-09T18:40")).toBe("6:40 PM");
    expect(formatSimClock("2026-11-09T09:05")).toBe("9:05 AM");
    expect(formatSimClock("2026-11-09T00:15")).toBe("12:15 AM");
    expect(formatSimClock("2026-11-09T12:00")).toBe("12:00 PM");
  });

  it("passes through anything it cannot parse", () => {
    expect(formatSimClock("evening")).toBe("evening");
  });
});

describe("formatClock", () => {
  it("formats seconds from call start as m:ss", () => {
    expect(formatClock(0)).toBe("0:00");
    expect(formatClock(9.8)).toBe("0:09");
    expect(formatClock(63)).toBe("1:03");
    expect(formatClock(600.2)).toBe("10:00");
  });
});

describe("endReasonText", () => {
  it("explains every end reason the backend sends", () => {
    const reasons = [
      "agent_hangup", "disconnected", "bad_start", "angry", "silence", "closed_after_promise",
      "bereavement_pressure", "time_cap", "llm_unavailable", "tts_unavailable", "stt_unavailable", "error",
    ];
    for (const r of reasons) expect(endReasonText(r, "Ramesh bhaiya")).not.toBe(r);
    expect(endReasonText("angry", "Ramesh bhaiya")).toBe("Ramesh bhaiya hung up, angry.");
  });

  it("falls back to the raw reason for one it does not know", () => {
    expect(endReasonText("meteor", "Ramesh bhaiya")).toBe("Call ended (meteor).");
  });
});
