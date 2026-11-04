import { describe, expect, it } from "vitest";
import { MOCK_SCORECARD } from "../mock/fixtures";
import type { Scorecard } from "./types";
import { STATUS_TEXT, scorecardView } from "./view";

const clone = (): Scorecard => JSON.parse(JSON.stringify(MOCK_SCORECARD));

describe("scorecardView", () => {
  it("orders the four sections as the product spec does: outcome, compliance, discovery, clarity", () => {
    const v = scorecardView(MOCK_SCORECARD);
    expect(v.sections.map((s) => s.name)).toEqual(["outcome", "compliance", "discovery_empathy", "clarity"]);
    expect(v.sections.map((s) => s.title)).toEqual(["Outcome", "Compliance", "Discovery & empathy", "Clarity"]);
  });

  it("shows the total out of 100", () => {
    expect(scorecardView(MOCK_SCORECARD).total).toEqual({ earned: 61, possible: 100 });
  });

  it("ties every earned mark to its quote and timestamp", () => {
    const outcome = scorecardView(MOCK_SCORECARD).sections[0]!;
    const promise = outcome.items.find((i) => i.id === "secured_promise")!;
    expect(promise).toMatchObject({ earned: 10, points: 10, statusText: STATUS_TEXT.earned });
    expect(promise.evidence[0]).toEqual({
      quote: "Chaar hazaar abhi de sakta hoon",
      line: "Chaar hazaar abhi de sakta hoon, baaki bees tareekh tak.",
      at: "0:58",
      verified: true,
      note: null,
    });
  });

  it("explains an unverified judge claim instead of giving points", () => {
    const c = clone();
    const k = c.checks.find((x) => x.id === "offered_option")!;
    k.status = "unverified";
    k.evidence = [{ turn: 9, quote: "EMI plan bana dete hain", verified: false, line: null, t: null }];
    const item = scorecardView(c).sections[0]!.items.find((i) => i.id === "offered_option")!;
    expect(item.statusText).toBe(STATUS_TEXT.unverified);
    expect(item.evidence[0]).toEqual({ quote: "EMI plan bana dete hain", line: null, at: null, verified: false, note: null });
  });

  it("lists each compliance deduction with the quote and the time of its flag", () => {
    const comp = scorecardView(MOCK_SCORECARD).sections[1]!;
    expect(comp.earned).toBe(20);
    expect(comp.items).toEqual([
      {
        id: "threat",
        label: "Threat or intimidation",
        points: 20,
        earned: -20,
        statusText: "−20",
        evidence: [{ quote: "police bhej denge ghar pe", line: null, at: "0:26", verified: true, note: null }],
      },
    ]);
  });

  it("says when compliance was clean", () => {
    const c = clone();
    c.compliance = { earned: 40, possible: 40, deductions: [], unverified: [] };
    c.sections[3]!.earned = 40;
    c.total = 81;
    expect(scorecardView(c).sections[1]!.empty).toBe("No RBI rule broken. Full 40.");
  });

  it("builds a flag timeline in time order, withdrawn flags included", () => {
    const v = scorecardView(MOCK_SCORECARD);
    expect(v.timeline.map((f) => [f.at, f.statusLabel])).toEqual([
      ["0:14", "withdrawn"],
      ["0:26", "confirmed"],
    ]);
  });

  it("offers the better line only when the rewrite passed the lexicon", () => {
    expect(scorecardView(MOCK_SCORECARD).betterLine).toMatchObject({
      said: "Agar aaj payment nahi hua toh police bhej denge ghar pe.",
      playable: true,
      at: "0:26", // from the flag raised on that line
    });
    const c = clone();
    c.worst_line!.rewrite_ok = false;
    expect(scorecardView(c).betterLine?.playable).toBe(false);
    c.worst_line = null;
    expect(scorecardView(c).betterLine).toBeNull();
  });

  it("does not present an unverified worst line as something the trainee said", () => {
    const c = clone();
    c.worst_line!.quote_verified = false;
    expect(scorecardView(c).betterLine).toMatchObject({ verified: false, at: null });
  });

  it("has no time for the worst line when no flag was raised on it", () => {
    const c = clone();
    c.flags = [];
    expect(scorecardView(c).betterLine?.at).toBeNull();
  });

  it("explains contradicted as the engine recording no promise", () => {
    expect(STATUS_TEXT.contradicted).toBe("no points: no promise was recorded on the call");
  });

  it("explains a judge violation that overrides a withdrawn live flag", () => {
    const c = clone();
    c.compliance.deductions[0]!.occurrences[0]!.overrides_withdrawn_flag = true;
    const ev = scorecardView(c).sections[1]!.items[0]!.evidence[0]!;
    expect(ev.note).toBe("the judge overruled the live withdrawal");
  });

  it("counts violations for the share line", () => {
    expect(scorecardView(MOCK_SCORECARD).violations).toBe(1);
  });
});
