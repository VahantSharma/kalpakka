import { LIVE_HOURS, RECORDED_CALL_URL } from "@/lib/env";
import styles from "./frontdoor.module.css";

/** NFR-6: outside live hours the GPU is off; say so plainly and play a recorded call instead. */
export function OutsideHours() {
  return (
    <div className={styles.stack}>
      <header className={styles.hero}>
        <h1>Ramesh is off the line right now</h1>
        <p>
          KalPakka runs live calls only during announced hours, because the borrower&apos;s mind runs on a rented GPU.
          {LIVE_HOURS ? ` Live hours: ${LIVE_HOURS}.` : " Live hours are announced on the launch post."}
        </p>
      </header>
      <section className={styles.panel} aria-labelledby="rec-h">
        <h2 id="rec-h">Hear a recorded call</h2>
        {RECORDED_CALL_URL ? (
          <>
            <p className={styles.muted}>
              A real practice call with Ramesh, an AI borrower, recorded during live hours. Watch how he reacts to how he
              is spoken to.
            </p>
            <audio controls preload="none" src={RECORDED_CALL_URL} style={{ width: "100%", marginTop: 8 }}>
              Your browser cannot play this recording.
            </audio>
          </>
        ) : (
          <p className={styles.muted}>The recorded call will be posted here before launch.</p>
        )}
      </section>
      <p className={styles.footer}>Ramesh is an AI borrower with synthetic details: not a real person or loan.</p>
    </div>
  );
}
