"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { type CallAudio, micLevelMeter, releaseAudio, startAudio } from "@/lib/audio/browser";
import { BACKEND_HTTP } from "@/lib/env";
import { DIFFICULTY_INFO, RULES_REMINDER, briefingFacts, formatRupees } from "@/lib/frontdoor/briefing";
import { flow, initialFlow } from "@/lib/frontdoor/flow";
import { checkLive, loadPersonas } from "@/lib/frontdoor/personas";
import type { Difficulty, PublicCard } from "@/lib/protocol/types";
import { decodeScorecard } from "@/lib/scorecard/decode";
import { CallScreen } from "./CallScreen";
import { OutsideHours } from "./OutsideHours";
import { ScorecardView } from "./ScorecardView";
import { SharePreview } from "./SharePreview";
import styles from "./frontdoor.module.css";

export function FrontDoor() {
  const [s, dispatch] = useReducer(flow, initialFlow);
  const [audio, setAudio] = useState<CallAudio | null>(null);
  const audioRef = useRef<CallAudio | null>(null); // for unmount cleanup only
  const [scoreError, setScoreError] = useState<string | null>(null);
  const [callOver, setCallOver] = useState(false);

  /** Stop the mic (indicator off) whenever the trainee is not about to talk. */
  const releaseMic = useCallback(() => {
    if (audioRef.current) releaseAudio(audioRef.current);
    audioRef.current = null;
    setAudio(null);
  }, []);
  const opened = useCallback((a: CallAudio) => {
    audioRef.current = a;
    setAudio(a);
  }, []);
  const go = useCallback(
    (type: "back" | "retry") => {
      setScoreError(null);
      setCallOver(false);
      if (type === "back") releaseMic();
      dispatch({ type });
    },
    [releaseMic],
  );

  useEffect(() => {
    let live = true;
    void Promise.all([checkLive(BACKEND_HTTP), loadPersonas(BACKEND_HTTP)]).then(([hours, result]) => {
      if (!live) return;
      dispatch({ type: "loaded", result: hours === "closed" ? { kind: "offline" } : result });
    });
    return () => {
      live = false;
    };
  }, []);

  useEffect(
    () => () => {
      if (audioRef.current) releaseAudio(audioRef.current);
    },
    [],
  );

  const onScored = useCallback((raw: unknown) => {
    const r = decodeScorecard(raw);
    releaseMic(); // the call is over; a retry opens the mic again from its own click
    if (r.ok) dispatch({ type: "scored", scorecard: r.card });
    else setScoreError(r.error);
  }, [releaseMic]);
  const onOffline = useCallback(() => {
    releaseMic();
    dispatch({ type: "offline" });
  }, [releaseMic]);
  const onEnded = useCallback(() => setCallOver(true), []);

  switch (s.step) {
    case "loading":
      return <p className={styles.muted}>Loading borrowers…</p>;
    case "offline":
      return <OutsideHours />;
    case "error":
      return (
        <p className={styles.error} role="alert">
          Something went wrong loading the borrowers ({s.message}). Please try again in a minute.
        </p>
      );
    case "pick":
      return <Picker cards={s.cards} onPick={(id) => dispatch({ type: "pick", id })} />;
    case "brief":
      return (
        <Briefing
          card={s.persona!}
          difficulty={s.difficulty}
          onDifficulty={(difficulty) => dispatch({ type: "difficulty", difficulty })}
          onNext={() => dispatch({ type: "to_mic" })}
          onBack={() => go("back")}
        />
      );
    case "mic":
      return (
        <MicCheck audio={audio} onOpened={opened} onReady={() => dispatch({ type: "mic_ok" })} onBack={() => go("back")} />
      );
    case "call":
      return (
        <div className={styles.stack}>
          <CallScreen
            key={s.attempt}
            persona={s.persona!}
            difficulty={s.difficulty}
            audio={audio}
            autoStart
            onOffline={onOffline}
            onScored={onScored}
            onEnded={onEnded}
          />
          {scoreError && (
            <p className={styles.error} role="alert">
              The scorecard could not be read ({scoreError}).
            </p>
          )}
          {(callOver || scoreError) && (
            <div className={styles.row}>
              <button className={styles.primary} onClick={() => go("retry")}>
                Call again
              </button>
              <button className={styles.secondary} onClick={() => go("back")}>
                Back to borrowers
              </button>
            </div>
          )}
        </div>
      );
    case "score":
      return (
        <div className={styles.stack}>
          <ScorecardView card={s.scorecard!} borrower={s.persona!.display_name} />
          <SharePreview card={s.scorecard!} borrower={s.persona!.display_name} />
          <div className={styles.row}>
            <button className={styles.primary} onClick={() => go("retry")}>
              Call {s.persona!.display_name} again{s.difficulty === "easy" ? "" : " (new traps)"}
            </button>
            <button className={styles.secondary} onClick={() => go("back")}>
              Pick another borrower
            </button>
          </div>
        </div>
      );
  }
}

function Picker({ cards, onPick }: { cards: PublicCard[]; onPick: (id: string) => void }) {
  return (
    <div className={styles.stack}>
      <header className={styles.hero}>
        <h1>Can you recover the money without breaking RBI&apos;s rules?</h1>
        <p>
          Practise a loan-recovery call with an AI borrower who speaks like a real one. Every rule you cross is flagged
          live, and your scorecard quotes your own words.
        </p>
      </header>
      <ul className={styles.cards} aria-label="Pick a borrower">
        {cards.map((c) => (
          <li key={c.id}>
            <button className={styles.card} onClick={() => onPick(c.id)}>
              <span className={styles.cardName}>{c.display_name}</span>
              <span className={styles.badges}>
                <span className={styles.badge}>{c.language}</span>
                <span className={styles.badge}>{DIFFICULTY_INFO[c.difficulty_default].label}</span>
              </span>
              <span className={styles.cardMeta}>
                {c.profile.age}, {c.profile.occupation}, {c.profile.city}
              </span>
              <span className={styles.cardDue}>
                {formatRupees(c.loan.overdue_amount)} overdue · {c.loan.dpd} DPD
              </span>
            </button>
          </li>
        ))}
      </ul>
      <p className={styles.footer}>
        Every borrower is an AI character with synthetic details: no real people, lenders or loans. Built on Gnani&apos;s
        Prisma (speech-to-text), Timbre (voice) and Evon (the borrower&apos;s mind).
      </p>
    </div>
  );
}

function Briefing({
  card,
  difficulty,
  onDifficulty,
  onNext,
  onBack,
}: {
  card: PublicCard;
  difficulty: Difficulty;
  onDifficulty: (d: Difficulty) => void;
  onNext: () => void;
  onBack: () => void;
}) {
  return (
    <div className={styles.stack}>
      <section className={styles.panel} aria-labelledby="brief-h">
        <h2 id="brief-h">Your next call</h2>
        <dl className={styles.facts}>
          {briefingFacts(card).map(([k, v]) => (
            <div key={k} style={{ display: "contents" }}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
        <p className={styles.muted} style={{ marginTop: 10 }}>
          The simulated time of day is shown when the call connects. You are the recovery agent; {card.display_name} is
          an AI borrower.
        </p>
      </section>
      <section className={styles.panel} aria-labelledby="rules-h">
        <h2 id="rules-h">Remember</h2>
        <ul className={styles.rules}>
          {RULES_REMINDER.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      </section>
      <fieldset className={styles.levels}>
        <legend>Difficulty</legend>
        {(Object.keys(DIFFICULTY_INFO) as Difficulty[]).map((d) => (
          <label key={d} className={`${styles.level} ${d === difficulty ? styles.levelOn : ""}`}>
            <input type="radio" name="difficulty" value={d} checked={d === difficulty} onChange={() => onDifficulty(d)} />
            <strong>{DIFFICULTY_INFO[d].label}</strong>
            <span>{DIFFICULTY_INFO[d].summary}</span>
          </label>
        ))}
      </fieldset>
      <div className={styles.row}>
        <button className={styles.primary} onClick={onNext}>
          Next: check my microphone
        </button>
        <button className={styles.secondary} onClick={onBack}>
          Back
        </button>
      </div>
    </div>
  );
}

function MicCheck({
  audio,
  onOpened,
  onReady,
  onBack,
}: {
  audio: CallAudio | null; // owned by FrontDoor so it is released on Back, offline or unmount
  onOpened: (a: CallAudio) => void;
  onReady: () => void;
  onBack: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [level, setLevel] = useState(0);
  const [heard, setHeard] = useState(false);

  useEffect(() => {
    if (!audio) return;
    const meter = micLevelMeter(audio.ctx, audio.stream);
    let raf = 0;
    const tick = () => {
      const v = meter.read();
      setLevel(v);
      if (v > 0.08) setHeard(true);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      meter.close();
    };
  }, [audio]);

  const allow = async () => {
    setError(null);
    try {
      onOpened(await startAudio(audio)); // inside the click: the context may start
    } catch (e) {
      const name = e instanceof Error ? e.name : "";
      setError(
        name === "NotAllowedError"
          ? "Microphone permission was blocked. Allow it in the address bar (lock icon), then try again."
          : name === "NotFoundError"
            ? "No microphone was found. Plug in a headset or use a phone."
            : `The microphone could not start (${String(e)}).`,
      );
    }
  };

  return (
    <div className={styles.stack}>
      <section className={styles.panel} aria-labelledby="mic-h">
        <h2 id="mic-h">Microphone check</h2>
        <p className={styles.muted}>
          Headphones help: they stop the borrower&apos;s voice leaking into your mic. Your audio is streamed for
          transcription only while you hold the talk button. KalPakka does not store it.
        </p>
        {!audio ? (
          <div className={styles.row} style={{ marginTop: 12 }}>
            <button className={styles.primary} onClick={allow}>
              Allow microphone
            </button>
          </div>
        ) : (
          <div className={styles.stack} style={{ marginTop: 12 }}>
            <p>Say &quot;Namaste, main SwiftFin se bol raha hoon&quot;. The bar should move.</p>
            <div
              className={styles.meter}
              role="meter"
              aria-label="Microphone level"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(level * 100)}
            >
              <div className={styles.meterFill} style={{ width: `${Math.round(level * 100)}%` }} />
            </div>
            <p className={styles.muted}>{heard ? "We can hear you." : "Waiting for your voice…"}</p>
          </div>
        )}
        {error && (
          <p className={styles.error} role="alert" style={{ marginTop: 12 }}>
            {error}
          </p>
        )}
      </section>
      <div className={styles.row}>
        <button className={styles.primary} disabled={!audio} onClick={onReady}>
          Start the call
        </button>
        <button className={styles.secondary} onClick={onBack}>
          Back
        </button>
      </div>
    </div>
  );
}
