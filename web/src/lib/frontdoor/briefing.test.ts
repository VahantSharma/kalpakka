import { describe, expect, it } from "vitest";
import { MOCK_PERSONAS } from "../mock/fixtures";
import { DIFFICULTY_INFO, briefingFacts, formatRupees, promiseHistory } from "./briefing";

const ramesh = MOCK_PERSONAS[0]!;

describe("formatRupees", () => {
  it("uses Indian digit grouping", () => {
    expect(formatRupees(12500)).toBe("₹12,500");
    expect(formatRupees(4000)).toBe("₹4,000");
    expect(formatRupees(125000)).toBe("₹1,25,000");
    expect(formatRupees(950)).toBe("₹950");
  });
});

describe("promiseHistory", () => {
  it("says how many earlier promises were broken", () => {
    expect(promiseHistory(0)).toBe("No earlier promises to pay");
    expect(promiseHistory(1)).toBe("Promised once, did not pay");
    expect(promiseHistory(2)).toBe("Promised twice, did not pay");
    expect(promiseHistory(3)).toBe("Promised 3 times, did not pay");
  });
});

describe("briefingFacts", () => {
  it("shows what a real agent's screen shows for Ramesh", () => {
    expect(briefingFacts(ramesh)).toEqual([
      ["Borrower", "Ramesh bhaiya, 41, auto-rickshaw driver, Delhi"],
      ["Lender", "SwiftFin"],
      ["Product", "two-wheeler loan"],
      ["EMI", "₹2,500"],
      ["Overdue", "₹12,500 · 23 DPD"],
      ["History", "Promised twice, did not pay"],
      ["Language", "Hinglish"],
    ]);
  });
});

describe("DIFFICULTY_INFO", () => {
  it("describes the three levels as in the product spec", () => {
    expect(Object.keys(DIFFICULTY_INFO)).toEqual(["easy", "medium", "hard"]);
    expect(DIFFICULTY_INFO.easy.summary).toMatch(/no traps/i);
    expect(DIFFICULTY_INFO.medium.summary).toMatch(/one .*trap/i);
    expect(DIFFICULTY_INFO.hard.summary).toMatch(/two traps/i);
  });
});
