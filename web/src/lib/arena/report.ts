/**
 * Arena run file (evals/arena/run_arena.py on claude/logic): `report` maps a bot ("A", "B") to
 * `arena.BotReport`. Mirrors those field names exactly; counts always with percentages (07).
 */

import { RULE_IDS, type RuleId } from "../protocol/types";
import { RULE_LABELS } from "../ui/flags";

export interface Rate {
  count: number;
  of: number;
  text?: string; // arena.Rate.text, a computed field the backend includes
}

export interface BotReport {
  bot: string;
  calls: number;
  errors: number;
  judge_errors: number;
  violation_rate: Partial<Record<RuleId, Rate>>;
  trap_pass_rate: Record<string, Rate>;
  trap_not_reached: Record<string, number>;
  promise_rate: Rate;
  valid_promise_rate: Rate;
  angry_hangup_rate: Rate;
  average_turns: number | null;
  stops: Record<string, number>;
  limitation: string;
}

export interface ArenaRun {
  mock?: boolean; // UI fixtures only: the page shows a banner
  run_at: string;
  finished_at: string;
  sim_date: string;
  bot_prompt_versions: Record<string, number>;
  judge_prompt_version: number | null;
  borrower_prompt_version: number;
  persona_versions: Record<string, number>;
  report: Record<string, BotReport>;
}

/** The two prompts in 07. */
export const BOT_NAMES: Record<string, string> = { A: "Bot A (target-driven)", B: "Bot B (compliant)" };
export const botName = (id: string) => BOT_NAMES[id] ?? `Bot ${id}`;

/** Same wording and rounding (half up) as arena.Rate.text. */
export function countOf(r: Rate): string {
  if (r.text) return r.text;
  if (r.of === 0) return `${r.count} of 0 (n/a)`;
  return `${r.count} of ${r.of} (${Math.floor((100 * r.count) / r.of + 0.5)}%)`;
}

class Bad extends Error {}
type Obj = Record<string, unknown>;
const o = (v: unknown, p: string): Obj => {
  if (typeof v !== "object" || v === null || Array.isArray(v)) throw new Bad(`${p}: expected object`);
  return v as Obj;
};
const count = (v: unknown, p: string): number => {
  if (typeof v !== "number" || !Number.isInteger(v) || v < 0) throw new Bad(`${p}: expected a count`);
  return v;
};
const num = (v: unknown, p: string): number => {
  if (typeof v !== "number" || !Number.isFinite(v)) throw new Bad(`${p}: expected number`);
  return v;
};
const s = (v: unknown, p: string): string => {
  if (typeof v !== "string") throw new Bad(`${p}: expected string`);
  return v;
};
function rate(v: unknown, p: string): Rate {
  const r = o(v, p);
  const out: Rate = { count: count(r.count, `${p}.count`), of: count(r.of, `${p}.of`) };
  if (out.count > out.of) throw new Bad(`${p}: ${out.count} is more than ${out.of}`);
  if (typeof r.text === "string") out.text = r.text;
  return out;
}
const mapOf = <T>(v: unknown, p: string, f: (x: unknown, q: string) => T): Record<string, T> =>
  Object.fromEntries(Object.entries(o(v, p)).map(([k, x]) => [k, f(x, `${p}.${k}`)]));

function bot(v: unknown, p: string): BotReport {
  const b = o(v, p);
  const vr = mapOf(b.violation_rate, `${p}.violation_rate`, rate);
  for (const k of Object.keys(vr)) {
    if (!(RULE_IDS as readonly string[]).includes(k)) throw new Bad(`${p}.violation_rate: unknown rule ${k}`);
  }
  return {
    bot: s(b.bot, `${p}.bot`),
    calls: count(b.calls, `${p}.calls`),
    errors: count(b.errors, `${p}.errors`),
    judge_errors: count(b.judge_errors, `${p}.judge_errors`),
    violation_rate: vr as Partial<Record<RuleId, Rate>>,
    trap_pass_rate: mapOf(b.trap_pass_rate, `${p}.trap_pass_rate`, rate),
    trap_not_reached: mapOf(b.trap_not_reached, `${p}.trap_not_reached`, count),
    promise_rate: rate(b.promise_rate, `${p}.promise_rate`),
    valid_promise_rate: rate(b.valid_promise_rate, `${p}.valid_promise_rate`),
    angry_hangup_rate: rate(b.angry_hangup_rate, `${p}.angry_hangup_rate`),
    average_turns: b.average_turns === null ? null : num(b.average_turns, `${p}.average_turns`),
    stops: mapOf(b.stops, `${p}.stops`, count),
    limitation: s(b.limitation, `${p}.limitation`),
  };
}

export function decodeArenaRun(v: unknown): { ok: true; run: ArenaRun } | { ok: false; error: string } {
  try {
    const r = o(v, "run");
    const nullableInt = (x: unknown, p: string) => (x === null ? null : count(x, p));
    return {
      ok: true,
      run: {
        mock: r.mock === true,
        run_at: s(r.run_at, "run.run_at"),
        finished_at: s(r.finished_at, "run.finished_at"),
        sim_date: s(r.sim_date, "run.sim_date"),
        bot_prompt_versions: mapOf(r.bot_prompt_versions, "run.bot_prompt_versions", count),
        judge_prompt_version: nullableInt(r.judge_prompt_version, "run.judge_prompt_version"),
        borrower_prompt_version: count(r.borrower_prompt_version, "run.borrower_prompt_version"),
        persona_versions: mapOf(r.persona_versions, "run.persona_versions", count),
        report: mapOf(r.report, "run.report", bot),
      },
    };
  } catch (e) {
    if (e instanceof Bad) return { ok: false, error: e.message };
    throw e;
  }
}

export interface CompareRow {
  rule: RuleId;
  label: string;
  a: string;
  b: string;
  rateA: number | null;
  rateB: number | null;
}

const frac = (r: Rate | undefined) => (r && r.of > 0 ? r.count / r.of : null);

export function compareBots(a: BotReport, b: BotReport): CompareRow[] {
  return (Object.keys(a.violation_rate) as RuleId[]).map((rule) => {
    const ra = a.violation_rate[rule]!;
    const rb = b.violation_rate[rule];
    return {
      rule,
      label: RULE_LABELS[rule],
      a: countOf(ra),
      b: rb ? countOf(rb) : "not run",
      rateA: frac(ra),
      rateB: frac(rb),
    };
  });
}

/** The launch-post sentence: the rule where the two bots differ most, in counts. */
export function headline(a: BotReport, b: BotReport): string | null {
  let best: { gap: number; rule: RuleId } | null = null;
  for (const row of compareBots(a, b)) {
    if (row.rateA === null || row.rateB === null) continue;
    const gap = Math.abs(row.rateA - row.rateB);
    if (gap > 1e-9 && (!best || gap > best.gap + 1e-9)) best = { gap, rule: row.rule };
  }
  if (!best) return null;
  const ra = a.violation_rate[best.rule]!;
  const rb = b.violation_rate[best.rule]!;
  const label = RULE_LABELS[best.rule];
  const what = label.charAt(0).toLowerCase() + label.slice(1);
  return `${botName(a.bot)}: ${what} in ${ra.count} of ${ra.of} calls; ${botName(b.bot)}: ${rb.count} of ${rb.of}.`;
}
