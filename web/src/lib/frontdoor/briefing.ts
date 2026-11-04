import type { Difficulty, PublicCard } from "../protocol/types";

const INR = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

/** ₹ with Indian digit grouping: ₹12,500, ₹1,25,000. */
export function formatRupees(amount: number): string {
  return `₹${INR.format(amount)}`;
}

export function promiseHistory(pastPromises: number): string {
  if (pastPromises <= 0) return "No earlier promises to pay";
  if (pastPromises === 1) return "Promised once, did not pay";
  if (pastPromises === 2) return "Promised twice, did not pay";
  return `Promised ${pastPromises} times, did not pay`;
}

/** What a real agent's screen shows before the call (04, step 2). Never hidden state. */
export function briefingFacts(card: PublicCard): Array<[string, string]> {
  const { profile: p, loan } = card;
  return [
    ["Borrower", `${card.display_name}, ${p.age}, ${p.occupation}, ${p.city}`],
    ["Lender", loan.lender],
    ["Product", loan.product],
    ["EMI", formatRupees(loan.emi)],
    ["Overdue", `${formatRupees(loan.overdue_amount)} · ${loan.dpd} DPD`],
    ["History", promiseHistory(loan.past_promises)],
    ["Language", card.language],
  ];
}

export const DIFFICULTY_INFO: Record<Difficulty, { label: string; summary: string }> = {
  easy: { label: "Easy", summary: "Calm borrower, no traps. Opens up once you build some trust." },
  medium: { label: "Medium", summary: "Irritated borrower, one random trap. Needs real trust before the truth comes out." },
  hard: {
    label: "Hard",
    summary: "Angry borrower, two traps. The truth needs high trust and an open question about their situation.",
  },
};

/** The 3-line rules reminder shown on the briefing (RBI harsh-practice rules, 02/07). */
export const RULES_REMINDER = [
  "No threats, abuse or humiliation, and nothing misleading about consequences.",
  "Talk about the debt only with the borrower. Never tell family, neighbours or colleagues.",
  "No recovery calls after 7 PM, and back off during bereavement or a pending grievance.",
] as const;
