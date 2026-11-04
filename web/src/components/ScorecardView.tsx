"use client";

import { useRef, useState } from "react";
import type { Scorecard } from "@/lib/scorecard/types";
import { scorecardView, type ItemView } from "@/lib/scorecard/view";
import styles from "./scorecard.module.css";

interface Props {
  card: Scorecard;
  borrower?: string | null;
  /** Audio of the better line (Timbre). No protocol field carries it yet; absent = button disabled. */
  betterLineUrl?: string | null;
}

export function ScorecardView({ card, borrower, betterLineUrl }: Props) {
  const v = scorecardView(card);
  return (
    <article className={styles.card} aria-labelledby="score-h">
      <header className={styles.head}>
        <h1 id="score-h">{borrower ? `Your call with ${borrower}` : "Your call"}</h1>
        <p className={styles.total}>
          <strong>{v.total.earned}</strong>
          <span>/ {v.total.possible}</span>
        </p>
        <p className={styles.sub}>
          {v.violations === 0 ? "No RBI rule broken." : `${v.violations} RBI rule${v.violations > 1 ? "s" : ""} broken.`}{" "}
          Every mark below quotes the call.
        </p>
      </header>

      {v.betterLine && <BetterLine line={v.betterLine} url={betterLineUrl ?? null} />}

      {v.sections.map((s) => (
        <section key={s.name} className={styles.section} aria-labelledby={`sec-${s.name}`}>
          <h2 id={`sec-${s.name}`}>
            <span>{s.title}</span>
            <span className={styles.secScore}>
              {s.earned} / {s.possible}
            </span>
          </h2>
          {s.empty && <p className={styles.clean}>{s.empty}</p>}
          <ul className={styles.items}>
            {s.items.map((i) => (
              <Item key={i.id} item={i} deduction={s.name === "compliance"} />
            ))}
          </ul>
        </section>
      ))}

      {v.timeline.length > 0 && (
        <section className={styles.section} aria-labelledby="sec-flags">
          <h2 id="sec-flags">Flags during the call</h2>
          <ol className={styles.timeline}>
            {v.timeline.map((f) => (
              <li key={f.id} className={styles[f.tone]}>
                <span className={styles.at}>{f.at}</span>
                <span>
                  <strong>{f.ruleLabel}</strong> <em className={styles.status}>{f.statusLabel}</em>
                  <br />
                  {f.quote}
                  {f.note && <span className={styles.note}> — {f.note}</span>}
                </span>
              </li>
            ))}
          </ol>
        </section>
      )}
      <p className={styles.foot}>
        Scored by an AI judge. Every quote was checked against the transcript; marks whose quote could not be found earn
        nothing. Practice only — not a certification.
      </p>
    </article>
  );
}

function Item({ item, deduction }: { item: ItemView; deduction: boolean }) {
  const got = deduction ? "lost" : item.earned > 0 ? "got" : "missed";
  return (
    <li className={`${styles.item} ${styles[got]}`}>
      <div className={styles.itemHead}>
        <span>{item.label}</span>
        <span className={styles.pts}>{deduction ? item.statusText : `${item.earned} / ${item.points}`}</span>
      </div>
      {!deduction && item.earned === 0 && <div className={styles.why}>{item.statusText}</div>}
      {item.evidence.map((e, k) => (
        <blockquote key={k} className={`${styles.quote} ${e.verified ? "" : styles.unverified}`}>
          {e.at && <span className={styles.at}>{e.at}</span>} “{e.line ?? e.quote}”
          {!e.verified && <span className={styles.note}> (not found in the transcript)</span>}
          {e.note && <span className={styles.note}> — {e.note}</span>}
        </blockquote>
      ))}
    </li>
  );
}

function BetterLine({
  line,
  url,
}: {
  line: { said: string; better: string; playable: boolean; verified: boolean; at: string | null };
  url: string | null;
}) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const canPlay = line.playable && !!url;
  const play = () => {
    if (!url) return;
    audio.current ??= new Audio(url);
    const a = audio.current;
    if (playing) {
      a.pause();
      a.currentTime = 0;
      setPlaying(false);
      return;
    }
    a.onended = () => setPlaying(false);
    void a.play().then(
      () => setPlaying(true),
      () => setPlaying(false),
    );
  };
  return (
    <section className={styles.better} aria-labelledby="better-h">
      <h2 id="better-h">Your worst line, said better</h2>
      <p className={styles.said}>
        {line.at && <span className={styles.at}>{line.at}</span>}
        {line.verified ? `You said: “${line.said}”` : `The judge cited: “${line.said}”`}
        {!line.verified && <span className={styles.note}> (not found in the transcript)</span>}
      </p>
      <p className={styles.betterText}>Try: “{line.better}”</p>
      <button className={styles.play} onClick={play} disabled={!canPlay} aria-pressed={playing}>
        {playing ? "■ Stop" : "▶ Hear it"}
      </button>
      {!line.playable && <p className={styles.note}>The rewrite did not pass the rules check, so it is not played.</p>}
      {line.playable && !url && <p className={styles.note}>Audio for this line is not available yet.</p>}
    </section>
  );
}
