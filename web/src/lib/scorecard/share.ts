import type { Scorecard } from "./types";

export interface ShareCard {
  score: string;
  title: string;
  facts: string[];
  text: string;
}

const earned = (card: Scorecard, id: string) => (card.checks.find((c) => c.id === id)?.earned ?? 0) > 0;

/** Share line from the verified scorecard only: nothing the judge did not score. */
export function shareCard(card: Scorecard, borrower: string): ShareCard {
  const possible = card.sections.reduce((n, s) => n + s.possible, 0);
  const v = card.compliance.deductions.length;
  const facts = [`${v} RBI violation${v === 1 ? "" : "s"}`];
  if (earned(card, "secured_promise")) facts.push("Secured a promise");
  if (earned(card, "discovered_situation")) facts.push("Found the real reason");
  const score = `${card.total}/${possible}`;
  return {
    score,
    title: `Loan-recovery call with ${borrower}`,
    facts,
    text: `I scored ${score} on a practice loan-recovery call with ${borrower}, an AI borrower · ${facts.join(" · ")}`,
  };
}
