/**
 * Validates the `scorecard` payload against the judge.Scorecard shape before the UI trusts it.
 * Rebuilds every object field by field (unknown fields are dropped) and checks the one invariant
 * the page relies on: the total is the sum of the four sections.
 */

import { FLAG_STATUSES, LAYERS, RULE_IDS, type Flag, type RuleId } from "../protocol/types";
import type { CheckStatus, Scorecard, Section } from "./types";

export type DecodedScorecard = { ok: true; card: Scorecard } | { ok: false; error: string };

const SECTIONS: readonly Section[] = ["clarity", "discovery_empathy", "outcome", "compliance"];
const STATUSES: readonly CheckStatus[] = ["earned", "not_earned", "unverified", "contradicted", "not_applicable", "missing"];

class Bad extends Error {}
type Obj = Record<string, unknown>;

const obj = (v: unknown, p: string): Obj => {
  if (typeof v !== "object" || v === null || Array.isArray(v)) throw new Bad(`${p}: expected object`);
  return v as Obj;
};
const arr = (v: unknown, p: string): unknown[] => {
  if (!Array.isArray(v)) throw new Bad(`${p}: expected list`);
  return v;
};
const int = (v: unknown, p: string): number => {
  if (typeof v !== "number" || !Number.isInteger(v)) throw new Bad(`${p}: expected integer`);
  return v;
};
const num = (v: unknown, p: string): number => {
  if (typeof v !== "number" || !Number.isFinite(v)) throw new Bad(`${p}: expected number`);
  return v;
};
const str = (v: unknown, p: string): string => {
  if (typeof v !== "string") throw new Bad(`${p}: expected string`);
  return v;
};
const bool = (v: unknown, p: string): boolean => {
  if (typeof v !== "boolean") throw new Bad(`${p}: expected boolean`);
  return v;
};
const oneOf = <T extends string>(v: unknown, allowed: readonly T[], p: string): T => {
  const s = str(v, p);
  if (!(allowed as readonly string[]).includes(s)) throw new Bad(`${p}: unexpected value ${s}`);
  return s as T;
};
const nullable = <T>(v: unknown, f: (x: unknown) => T): T | null => (v === null || v === undefined ? null : f(v));

function flag(v: unknown, p: string): Flag {
  const f = obj(v, p);
  return {
    id: str(f.id, `${p}.id`),
    turn: int(f.turn, `${p}.turn`),
    segment: int(f.segment, `${p}.segment`),
    t: num(f.t, `${p}.t`),
    rule: oneOf(f.rule, RULE_IDS, `${p}.rule`),
    quote: str(f.quote, `${p}.quote`),
    layer: oneOf(f.layer, LAYERS, `${p}.layer`),
    status: oneOf(f.status, FLAG_STATUSES, `${p}.status`),
  };
}

function card(v: unknown): Scorecard {
  const c = obj(v, "scorecard");
  const sections = arr(c.sections, "scorecard.sections").map((x, i) => {
    const s = obj(x, `scorecard.sections[${i}]`);
    return {
      name: oneOf(s.name, SECTIONS, `scorecard.sections[${i}].name`),
      earned: int(s.earned, `scorecard.sections[${i}].earned`),
      possible: int(s.possible, `scorecard.sections[${i}].possible`),
    };
  });
  const checks = arr(c.checks, "scorecard.checks").map((x, i) => {
    const p = `scorecard.checks[${i}]`;
    const k = obj(x, p);
    return {
      id: str(k.id, `${p}.id`),
      label: str(k.label, `${p}.label`),
      section: oneOf(k.section, SECTIONS, `${p}.section`),
      points: int(k.points, `${p}.points`),
      earned: int(k.earned, `${p}.earned`),
      status: oneOf(k.status, STATUSES, `${p}.status`),
      evidence: arr(k.evidence, `${p}.evidence`).map((y, j) => {
        const q = `${p}.evidence[${j}]`;
        const e = obj(y, q);
        return {
          turn: int(e.turn, `${q}.turn`),
          quote: str(e.quote, `${q}.quote`),
          verified: bool(e.verified, `${q}.verified`),
          line: nullable(e.line, (z) => str(z, `${q}.line`)),
          t: nullable(e.t, (z) => num(z, `${q}.t`)),
        };
      }),
    };
  });
  const comp = obj(c.compliance, "scorecard.compliance");
  const violation = (y: unknown, q: string) => {
    const v2 = obj(y, q);
    return { rule: oneOf(v2.rule, RULE_IDS, `${q}.rule`) as RuleId, turn: int(v2.turn, `${q}.turn`), quote: str(v2.quote, `${q}.quote`) };
  };
  const compliance = {
    earned: int(comp.earned, "scorecard.compliance.earned"),
    possible: int(comp.possible, "scorecard.compliance.possible"),
    deductions: arr(comp.deductions, "scorecard.compliance.deductions").map((y, i) => {
      const q = `scorecard.compliance.deductions[${i}]`;
      const d = obj(y, q);
      return {
        rule: oneOf(d.rule, RULE_IDS, `${q}.rule`),
        points: int(d.points, `${q}.points`),
        source: str(d.source, `${q}.source`),
        occurrences: arr(d.occurrences, `${q}.occurrences`).map((z, j) => {
          const r = `${q}.occurrences[${j}]`;
          const o = obj(z, r);
          return {
            turn: int(o.turn, `${r}.turn`),
            quote: str(o.quote, `${r}.quote`),
            source: str(o.source, `${r}.source`),
            overrides_withdrawn_flag: o.overrides_withdrawn_flag === undefined ? false : bool(o.overrides_withdrawn_flag, `${r}.overrides_withdrawn_flag`),
          };
        }),
      };
    }),
    unverified: arr(comp.unverified, "scorecard.compliance.unverified").map((y, i) => violation(y, `scorecard.compliance.unverified[${i}]`)),
  };
  const flags = arr(c.flags, "scorecard.flags").map((y, i) => flag(y, `scorecard.flags[${i}]`));
  const worst_line = nullable(c.worst_line, (y) => {
    const w = obj(y, "scorecard.worst_line");
    return {
      turn: int(w.turn, "scorecard.worst_line.turn"),
      quote: str(w.quote, "scorecard.worst_line.quote"),
      quote_verified: bool(w.quote_verified, "scorecard.worst_line.quote_verified"),
      rewrite: str(w.rewrite, "scorecard.worst_line.rewrite"),
      rewrite_ok: bool(w.rewrite_ok, "scorecard.worst_line.rewrite_ok"),
    };
  });
  const names = sections.map((s) => s.name);
  if (names.length !== 4 || new Set(names).size !== 4) {
    throw new Bad("scorecard.sections: expected clarity, discovery_empathy, outcome, compliance once each");
  }
  const total = int(c.total, "scorecard.total");
  const sum = sections.reduce((n, s) => n + s.earned, 0);
  if (total !== sum) throw new Bad(`scorecard.total: ${total} is not the sum of sections (${sum})`);
  return { total, sections, checks, compliance, flags, worst_line };
}

export function decodeScorecard(v: unknown): DecodedScorecard {
  try {
    return { ok: true, card: card(v) };
  } catch (e) {
    if (e instanceof Bad) return { ok: false, error: e.message };
    throw e;
  }
}
