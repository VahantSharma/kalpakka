import { describe, expect, it } from "vitest";
import { MOCK_SCORECARD } from "../mock/fixtures";
import type { Scorecard } from "./types";
import { shareCard } from "./share";

const clone = (): Scorecard => JSON.parse(JSON.stringify(MOCK_SCORECARD));

describe("shareCard", () => {
  it("summarises the call from scorecard fields only", () => {
    expect(shareCard(MOCK_SCORECARD, "Ramesh bhaiya")).toEqual({
      score: "61/100",
      title: "Loan-recovery call with Ramesh bhaiya",
      facts: ["1 RBI violation", "Secured a promise", "Found the real reason"],
      text: "I scored 61/100 on a practice loan-recovery call with Ramesh bhaiya, an AI borrower · 1 RBI violation · Secured a promise · Found the real reason",
    });
  });

  it("says zero violations plainly and omits outcomes that were not earned", () => {
    const c = clone();
    c.compliance.deductions = [];
    for (const k of c.checks) if (k.id === "secured_promise" || k.id === "discovered_situation") k.earned = 0;
    expect(shareCard(c, "Ramesh bhaiya").facts).toEqual(["0 RBI violations"]);
  });

  it("counts several violations in the plural", () => {
    const c = clone();
    c.compliance.deductions.push({ rule: "misleading", points: 15, source: "lexicon", occurrences: [] });
    expect(shareCard(c, "Ramesh bhaiya").facts[0]).toBe("2 RBI violations");
  });
});
