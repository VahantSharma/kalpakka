"use client";

import { useState } from "react";
import { shareCard } from "@/lib/scorecard/share";
import type { Scorecard } from "@/lib/scorecard/types";
import styles from "./share.module.css";

/** FR-12 preview: what the share image says. The PNG itself is GET /share/{id}.png (backend). */
export function SharePreview({ card, borrower }: { card: Scorecard; borrower: string }) {
  const c = shareCard(card, borrower);
  const [copied, setCopied] = useState(false);
  const share = async () => {
    const url = typeof window === "undefined" ? "" : window.location.origin;
    const text = `${c.text}. Try it: ${url}`;
    try {
      if (navigator.share) await navigator.share({ title: "KalPakka", text });
      else {
        await navigator.clipboard.writeText(text);
        setCopied(true);
      }
    } catch {
      // the share sheet was dismissed: nothing to do
    }
  };
  return (
    <section aria-label="Share your score" className={styles.wrap}>
      <div className={styles.card}>
        <span className={styles.brand}>KalPakka</span>
        <span className={styles.score}>{c.score}</span>
        <span className={styles.title}>{c.title}</span>
        <ul className={styles.facts}>
          {c.facts.map((f) => (
            <li key={f}>{f}</li>
          ))}
        </ul>
        <span className={styles.ai}>AI borrower · synthetic data · practice only</span>
      </div>
      <button className={styles.btn} onClick={share}>
        {copied ? "Copied" : "Share my score"}
      </button>
    </section>
  );
}
