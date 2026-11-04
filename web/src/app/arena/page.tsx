"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { type ArenaRun, type BotReport, botName, compareBots, countOf, decodeArenaRun, headline } from "@/lib/arena/report";
import styles from "./arena.module.css";

/**
 * Arena report: /arena?src=<url of a run file>, or open a local evals/runs/arena-*.json.
 * The file never leaves the browser when opened locally.
 */

function Bar({ value }: { value: number | null }) {
  return (
    <div className={styles.bar} aria-hidden>
      <span style={{ width: `${Math.round((value ?? 0) * 100)}%` }} />
    </div>
  );
}

function RunView({ run }: { run: ArenaRun }) {
  const bots = Object.keys(run.report).sort();
  if (bots.length !== 2) return <p role="alert">This page compares two bots; the run has {bots.length}.</p>;
  const a: BotReport = run.report[bots[0]!]!;
  const b: BotReport = run.report[bots[1]!]!;
  const rows = compareBots(a, b);
  const line = headline(a, b);
  const traps = [...new Set([...Object.keys(a.trap_pass_rate), ...Object.keys(b.trap_pass_rate)])];
  const rate = (r: { count: number; of: number } | undefined) => (r ? countOf(r) : "not run");
  return (
    <div className={styles.page}>
      {run.mock && (
        <p className={styles.mock} role="note">
          MOCK DATA — these numbers are invented to test this page. They are not results from a real Arena run.
        </p>
      )}
      <h1>Arena: two collections bots against the same borrowers</h1>
      {line && <p className={styles.headline}>{line}</p>}
      <section className={styles.panel}>
        <h2>Calls with at least one confirmed violation, by rule</h2>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Rule</th>
              <th>{botName(a.bot)}</th>
              <th>{botName(b.bot)}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.rule}>
                <td>{r.label}</td>
                <td className={styles.num}>
                  {r.a}
                  <Bar value={r.rateA} />
                </td>
                <td className={styles.num}>
                  {r.b}
                  <Bar value={r.rateB} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className={styles.muted}>
          Out of the calls where the rule could apply (for example, only calls whose bereavement trap fired).
        </p>
      </section>
      <section className={styles.panel}>
        <h2>Outcomes</h2>
        <table className={styles.table}>
          <thead>
            <tr>
              <th />
              <th>{botName(a.bot)}</th>
              <th>{botName(b.bot)}</th>
            </tr>
          </thead>
          <tbody>
            {traps.map((t) => (
              <tr key={t}>
                <td>Trap passed: {t.replaceAll("_", " ")}</td>
                <td className={styles.num}>{rate(a.trap_pass_rate[t])}</td>
                <td className={styles.num}>{rate(b.trap_pass_rate[t])}</td>
              </tr>
            ))}
            <tr>
              <td>Promise to pay</td>
              <td className={styles.num}>{countOf(a.promise_rate)}</td>
              <td className={styles.num}>{countOf(b.promise_rate)}</td>
            </tr>
            <tr>
              <td>Valid promise (of all calls)</td>
              <td className={styles.num}>{countOf(a.valid_promise_rate)}</td>
              <td className={styles.num}>{countOf(b.valid_promise_rate)}</td>
            </tr>
            <tr>
              <td>Angry hang-up</td>
              <td className={styles.num}>{countOf(a.angry_hangup_rate)}</td>
              <td className={styles.num}>{countOf(b.angry_hangup_rate)}</td>
            </tr>
            <tr>
              <td>Average turns</td>
              <td className={styles.num}>{a.average_turns?.toFixed(1) ?? "n/a"}</td>
              <td className={styles.num}>{b.average_turns?.toFixed(1) ?? "n/a"}</td>
            </tr>
            <tr>
              <td>Calls completed (errors)</td>
              <td className={styles.num}>
                {a.calls} ({a.errors})
              </td>
              <td className={styles.num}>
                {b.calls} ({b.errors})
              </td>
            </tr>
          </tbody>
        </table>
      </section>
      <section className={styles.panel}>
        <h2>Limitations</h2>
        <ul>
          {[...new Set([a.limitation, b.limitation])].map((l) => (
            <li key={l}>{l}</li>
          ))}
          {a.judge_errors + b.judge_errors > 0 && (
            <li>
              The judge failed on {a.judge_errors + b.judge_errors} call(s); those calls count flag-only violations.
            </li>
          )}
        </ul>
        <p className={styles.muted}>
          Run {run.run_at} · sim date {run.sim_date} · bot prompts{" "}
          {Object.entries(run.bot_prompt_versions)
            .map(([k, v]) => `${k} v${v}`)
            .join(", ")}{" "}
          · borrower prompt v{run.borrower_prompt_version} · judge prompt{" "}
          {run.judge_prompt_version === null ? "not run" : `v${run.judge_prompt_version}`}
        </p>
      </section>
    </div>
  );
}

function Report() {
  const src = useSearchParams().get("src");
  const [state, setState] = useState<{ run: ArenaRun } | { error: string } | null>(null);
  useEffect(() => {
    if (!src) return;
    let live = true;
    fetch(src)
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const d = decodeArenaRun(await r.json());
        if (live) setState(d.ok ? { run: d.run } : { error: d.error });
      })
      .catch((e: unknown) => live && setState({ error: String(e) }));
    return () => {
      live = false;
    };
  }, [src]);
  const open = async (file: File | undefined) => {
    if (!file) return;
    try {
      const d = decodeArenaRun(JSON.parse(await file.text()));
      setState(d.ok ? { run: d.run } : { error: d.error });
    } catch (e) {
      setState({ error: String(e) });
    }
  };
  return (
    <>
      {!state && !src && (
        <div className={styles.page}>
          <h1>Arena report</h1>
          <p>
            Open a run file (<code>evals/runs/arena-*.json</code>). It is read in this browser only.
          </p>
          <input type="file" accept="application/json,.json" onChange={(e) => void open(e.target.files?.[0])} />
        </div>
      )}
      {!state && src && <p>Loading the Arena run…</p>}
      {state && "error" in state && <p role="alert">Could not read the run ({state.error}).</p>}
      {state && "run" in state && <RunView run={state.run} />}
    </>
  );
}

export default function Page() {
  return (
    <main>
      <Suspense>
        <Report />
      </Suspense>
    </main>
  );
}
