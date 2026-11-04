import type { Flag } from "@/lib/protocol/types";
import { flagView } from "@/lib/ui/flags";
import styles from "./call.module.css";

export function FlagStrip({ flags }: { flags: Flag[] }) {
  if (flags.length === 0) {
    return <p className={styles.flagsEmpty}>No rule flags yet. They appear here the moment a line crosses an RBI rule.</p>;
  }
  return (
    <ul className={styles.flags} aria-live="polite" aria-label="Rule flags">
      {flags.map((f) => {
        const v = flagView(f);
        return (
          <li key={f.id} className={`${styles.flag} ${styles[v.tone]}`}>
            <div className={styles.flagHead}>
              <strong>{v.ruleLabel}</strong>
              <span className={styles.flagStatus}>{v.statusLabel}</span>
              <span className={styles.flagAt}>{v.at}</span>
            </div>
            <div className={styles.flagQuote}>{v.quote}</div>
            {v.note && <div className={styles.flagNote}>{v.note}</div>}
          </li>
        );
      })}
    </ul>
  );
}
