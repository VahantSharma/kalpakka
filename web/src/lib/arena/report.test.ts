import { describe, expect, it } from "vitest";
import { MOCK_ARENA_RUN } from "../mock/arena";
import { BOT_NAMES, compareBots, countOf, decodeArenaRun, headline } from "./report";

const clone = () => JSON.parse(JSON.stringify(MOCK_ARENA_RUN));

describe("countOf (same text as arena.Rate.text)", () => {
  it("always shows the count with the percentage, rounded half up", () => {
    expect(countOf({ count: 7, of: 30 })).toBe("7 of 30 (23%)");
    expect(countOf({ count: 1, of: 30 })).toBe("1 of 30 (3%)");
    expect(countOf({ count: 1, of: 8 })).toBe("1 of 8 (13%)"); // 12.5 -> 13
    expect(countOf({ count: 0, of: 40 })).toBe("0 of 40 (0%)");
  });

  it("says n/a when no call could break the rule", () => {
    expect(countOf({ count: 0, of: 0 })).toBe("0 of 0 (n/a)");
  });

  it("prefers the backend's own text when it is present", () => {
    expect(countOf({ count: 7, of: 30, text: "7 of 30 (23%)" })).toBe("7 of 30 (23%)");
  });
});

describe("decodeArenaRun", () => {
  it("accepts a run file shaped like run_arena.py output and keeps the mock marker", () => {
    const r = decodeArenaRun(clone());
    expect(r.ok && r.run.mock).toBe(true);
    expect(r.ok && Object.keys(r.run.report)).toEqual(["A", "B"]);
  });

  it("ignores the per-call records it does not show", () => {
    const run = { ...clone(), calls: [{ transcript: [] }] };
    const r = decodeArenaRun(run);
    expect(r.ok && "calls" in r.run).toBe(false);
  });

  it("rejects a rate whose count exceeds its total", () => {
    const bad = clone();
    bad.report.A.violation_rate.threat = { count: 121, of: 120 };
    expect(decodeArenaRun(bad)).toEqual({ ok: false, error: "run.report.A.violation_rate.threat: 121 is more than 120" });
  });

  it("rejects an unknown rule id", () => {
    const bad = clone();
    bad.report.A.violation_rate.rudeness = { count: 1, of: 2 };
    expect(decodeArenaRun(bad).ok).toBe(false);
  });
});

describe("compareBots", () => {
  it("lines bot A and bot B up rule by rule, in the report's order, with counts", () => {
    const { A, B } = MOCK_ARENA_RUN.report;
    const rows = compareBots(A!, B!);
    expect(rows.map((r) => r.rule)).toEqual(Object.keys(A!.violation_rate));
    expect(rows.find((r) => r.rule === "bereavement_pressure")).toMatchObject({ a: "7 of 30 (23%)", b: "1 of 30 (3%)" });
  });
});

describe("headline", () => {
  it("names the rule with the largest gap, in counts", () => {
    const { A, B } = MOCK_ARENA_RUN.report;
    expect(headline(A!, B!)).toBe(
      `${BOT_NAMES.A}: recovery talk during bereavement in 7 of 30 calls; ${BOT_NAMES.B}: 1 of 30.`,
    );
  });

  it("returns null when the bots never differ", () => {
    const { A } = MOCK_ARENA_RUN.report;
    expect(headline(A!, A!)).toBeNull();
  });
});
