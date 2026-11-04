import { describe, expect, it } from "vitest";
import { MOCK_SCORECARD } from "../mock/fixtures";
import { decodeScorecard } from "./decode";

const clone = () => JSON.parse(JSON.stringify(MOCK_SCORECARD));

describe("decodeScorecard", () => {
  it("accepts a scorecard shaped like judge.Scorecard", () => {
    expect(decodeScorecard(clone())).toEqual({ ok: true, card: MOCK_SCORECARD });
  });

  it("accepts a call with no worst line", () => {
    const c = clone();
    c.worst_line = null;
    expect(decodeScorecard(c).ok).toBe(true);
  });

  it("rejects a non-object", () => {
    expect(decodeScorecard("61")).toEqual({ ok: false, error: "scorecard: expected object" });
  });

  it("rejects a check with an unknown status", () => {
    const c = clone();
    c.checks[0].status = "great";
    expect(decodeScorecard(c)).toEqual({ ok: false, error: "scorecard.checks[0].status: unexpected value great" });
  });

  it("rejects evidence without a verified flag", () => {
    const c = clone();
    delete c.checks[0].evidence[0].verified;
    expect(decodeScorecard(c)).toEqual({ ok: false, error: "scorecard.checks[0].evidence[0].verified: expected boolean" });
  });

  it("rejects a deduction for a rule outside the catalogue", () => {
    const c = clone();
    c.compliance.deductions[0].rule = "rudeness";
    expect(decodeScorecard(c).ok).toBe(false);
  });

  it("rejects a scorecard without exactly the four sections", () => {
    const c = clone();
    c.sections.pop();
    expect(decodeScorecard(c)).toEqual({
      ok: false,
      error: "scorecard.sections: expected clarity, discovery_empathy, outcome, compliance once each",
    });
  });

  it("rejects a total that is not the sum of the sections", () => {
    const c = clone();
    c.total = 99;
    expect(decodeScorecard(c)).toEqual({ ok: false, error: "scorecard.total: 99 is not the sum of sections (61)" });
  });
});
