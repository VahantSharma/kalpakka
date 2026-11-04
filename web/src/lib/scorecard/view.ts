/**
 * Scorecard -> what the page shows. No scoring here: points come from the backend (judge.py);
 * this only orders, labels and attaches quotes and timestamps.
 */

import type { Flag } from "../protocol/types";
import { RULE_LABELS, flagView, formatClock, type FlagView } from "../ui/flags";
import type { CheckStatus, Scorecard, Section } from "./types";

export const SECTION_ORDER: readonly Section[] = ["outcome", "compliance", "discovery_empathy", "clarity"];
export const SECTION_TITLES: Record<Section, string> = {
  outcome: "Outcome",
  compliance: "Compliance",
  discovery_empathy: "Discovery & empathy",
  clarity: "Clarity",
};

export const STATUS_TEXT: Record<CheckStatus, string> = {
  earned: "earned",
  not_earned: "not earned",
  unverified: "no points: the judge's quote was not found in what was said",
  contradicted: "no points: no promise was recorded on the call",
  not_applicable: "not applicable to this call",
  missing: "not assessed",
};

export interface EvidenceView {
  quote: string;
  line: string | null;
  at: string | null;
  verified: boolean;
  note: string | null;
}

export interface ItemView {
  id: string;
  label: string;
  points: number;
  earned: number;
  statusText: string;
  evidence: EvidenceView[];
}

export interface SectionView {
  name: Section;
  title: string;
  earned: number;
  possible: number;
  items: ItemView[];
  empty: string | null;
}

export interface ScorecardView {
  total: { earned: number; possible: number };
  sections: SectionView[];
  timeline: Array<FlagView & { id: string }>;
  betterLine: { said: string; better: string; playable: boolean; verified: boolean; at: string | null } | null;
  violations: number;
}

function flagTime(flags: Flag[], turn: number, rule: string): string | null {
  const f = flags.find((x) => x.turn === turn && x.rule === rule);
  return f ? formatClock(f.t) : null;
}

export function scorecardView(card: Scorecard): ScorecardView {
  const sections = SECTION_ORDER.map((name): SectionView => {
    const score = card.sections.find((s) => s.name === name) ?? { earned: 0, possible: 0 };
    if (name === "compliance") {
      const items = card.compliance.deductions.map(
        (d): ItemView => ({
          id: d.rule,
          label: RULE_LABELS[d.rule],
          points: d.points,
          earned: -d.points,
          statusText: `−${d.points}`,
          evidence: d.occurrences.map((o) => ({
            quote: o.quote,
            line: null,
            at: flagTime(card.flags, o.turn, d.rule),
            verified: true,
            note: o.overrides_withdrawn_flag ? "the judge overruled the live withdrawal" : null,
          })),
        }),
      );
      return {
        name,
        title: SECTION_TITLES[name],
        earned: score.earned,
        possible: score.possible,
        items,
        empty: items.length === 0 ? `No RBI rule broken. Full ${score.possible}.` : null,
      };
    }
    const items = card.checks
      .filter((c) => c.section === name)
      .map(
        (c): ItemView => ({
          id: c.id,
          label: c.label,
          points: c.points,
          earned: c.earned,
          statusText: STATUS_TEXT[c.status],
          evidence: c.evidence.map((e) => ({
            quote: e.quote,
            line: e.line,
            at: e.t === null ? null : formatClock(e.t),
            verified: e.verified,
            note: null,
          })),
        }),
      );
    return { name, title: SECTION_TITLES[name], earned: score.earned, possible: score.possible, items, empty: null };
  });

  const timeline = [...card.flags].sort((a, b) => a.t - b.t).map((f) => ({ id: f.id, ...flagView(f) }));

  const w = card.worst_line;
  const betterLine = w
    ? {
        said: w.quote,
        better: w.rewrite,
        playable: w.rewrite_ok,
        verified: w.quote_verified,
        // only a flag on that turn is the agent's line; check evidence on the same turn may be the borrower's
        at: w.quote_verified ? (card.flags.find((f) => f.turn === w.turn)?.t ?? null) : null,
      }
    : null;

  return {
    total: { earned: card.total, possible: card.sections.reduce((n, s) => n + s.possible, 0) },
    sections,
    timeline,
    betterLine: betterLine && { ...betterLine, at: betterLine.at === null ? null : formatClock(betterLine.at) },
    violations: card.compliance.deductions.length,
  };
}
