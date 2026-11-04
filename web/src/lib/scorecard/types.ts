/**
 * Mirrors `Scorecard` in backend/src/kalpakka/judge.py (read only). Keep field names identical;
 * the backend sends this object verbatim in the `scorecard` message and GET /calls/{id}/scorecard.
 */

import type { Flag, RuleId } from "../protocol/types";

export type Section = "clarity" | "discovery_empathy" | "outcome" | "compliance";
export type CheckStatus = "earned" | "not_earned" | "unverified" | "contradicted" | "not_applicable" | "missing";

export interface ScoredEvidence {
  turn: number;
  quote: string; // what the judge cited
  verified: boolean;
  line: string | null; // the transcript line that proves it
  t: number | null; // time of that line, seconds from call start
}

export interface ScoredCheck {
  id: string;
  label: string;
  section: Section;
  points: number;
  earned: number;
  status: CheckStatus;
  evidence: ScoredEvidence[];
}

export interface Occurrence {
  turn: number;
  quote: string;
  source: string; // "lexicon" | "llm" | "state" | "judge"
  overrides_withdrawn_flag: boolean;
}

export interface Deduction {
  rule: RuleId;
  points: number;
  source: string;
  occurrences: Occurrence[];
}

export interface JudgeViolation {
  rule: RuleId;
  turn: number;
  quote: string;
}

export interface Compliance {
  earned: number;
  possible: number;
  deductions: Deduction[];
  unverified: JudgeViolation[];
}

export interface SectionScore {
  name: Section;
  earned: number;
  possible: number;
}

export interface WorstLineResult {
  turn: number;
  quote: string;
  quote_verified: boolean;
  rewrite: string;
  rewrite_ok: boolean; // false: blank, or the rewrite itself trips the lexicon; never played
}

export interface Scorecard {
  total: number;
  sections: SectionScore[];
  checks: ScoredCheck[];
  compliance: Compliance;
  flags: Flag[];
  worst_line: WorstLineResult | null;
}
