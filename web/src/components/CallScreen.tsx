"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { anonId } from "@/lib/anon";
import { type CallAudio, WorkletCapture, playbackContext, releaseAudio, startAudio } from "@/lib/audio/browser";
import { PlaybackScheduler } from "@/lib/audio/scheduler";
import { CallController, type CallState } from "@/lib/call/controller";
import { WebSocketTransport } from "@/lib/call/ws-transport";
import { BACKEND_WS } from "@/lib/env";
import type { Difficulty, PublicCard } from "@/lib/protocol/types";
import { endReasonText, formatClock, formatSimClock } from "@/lib/ui/flags";
import { FlagStrip } from "./FlagStrip";
import styles from "./call.module.css";

interface Props {
  persona: PublicCard | { id: string; display_name: string };
  difficulty: Difficulty;
  /** Opened by the mic check (inside its click). Owned by the caller: not released here. */
  audio?: CallAudio | null;
  /** Start as soon as mounted (only valid with `audio`, whose context is already running). */
  autoStart?: boolean;
  onOffline?: () => void;
  onScored?: (scorecard: unknown) => void;
  onEnded?: (state: CallState) => void;
}

const isEditable = (el: EventTarget | null) =>
  el instanceof HTMLElement && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));

export function CallScreen({ persona, difficulty, audio, autoStart, onOffline, onScored, onEnded }: Props) {
  const [controller, setController] = useState<CallController | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const resources = useRef<{
    audio: CallAudio | null;
    owned: boolean; // opened here (no `audio` prop): released on unmount
    capture: WorkletCapture | null;
    controller: CallController | null;
  }>({ audio: null, owned: false, capture: null, controller: null });
  const autoStarted = useRef(false);
  const wake = useCallback(() => resumeContext(resources.current.audio?.ctx), []);
  const unmounted = useRef(false);

  const start = useCallback(async () => {
    setStarting(true);
    setStartError(null);
    const r = resources.current;
    try {
      if (!r.audio) {
        r.owned = !audio;
        r.audio = await startAudio(audio);
      }
      r.capture ??= await WorkletCapture.create(r.audio.ctx, r.audio.stream);
      if (unmounted.current) {
        // left the page while the mic or worklet was starting: undo, do not dial
        r.capture.close();
        if (r.owned) releaseAudio(r.audio);
        r.capture = r.audio = null;
        return;
      }
      const player = new PlaybackScheduler(playbackContext(r.audio.ctx));
      const c = new CallController(new WebSocketTransport(BACKEND_WS), player, r.capture);
      r.controller = c;
      c.start(persona.id, difficulty, anonId());
      setController(c);
    } catch (e) {
      setStartError(e instanceof Error ? `${e.name}: ${e.message}` : String(e));
    } finally {
      setStarting(false);
    }
  }, [audio, persona.id, difficulty]);

  // leaving the page ends the call, stops the mic and closes the audio context
  useEffect(() => {
    const r = resources.current;
    unmounted.current = false; // StrictMode re-mount
    return () => {
      unmounted.current = true;
      r.controller?.dispose();
      r.capture?.close();
      if (r.audio && r.owned) releaseAudio(r.audio);
      r.controller = r.capture = r.audio = null;
    };
  }, []);

  useEffect(() => {
    if (autoStart && audio && !autoStarted.current) {
      autoStarted.current = true;
      void start();
    }
  }, [autoStart, audio, start]);

  if (!controller) {
    return (
      <section className={styles.screen}>
        <Disclosure name={persona.display_name} />
        <button className={styles.primary} onClick={start} disabled={starting}>
          {starting ? "Connecting…" : `Start call with ${persona.display_name}`}
        </button>
        {startError && (
          <p className={styles.error} role="alert">
            Could not start the call ({startError}). Check microphone permission and try again.
          </p>
        )}
      </section>
    );
  }
  return (
    <LiveCall
      controller={controller}
      name={persona.display_name}
      wake={wake}
      onOffline={onOffline}
      onScored={onScored}
      onEnded={onEnded}
    />
  );
}

function Disclosure({ name }: { name: string }) {
  return (
    <p className={styles.disclosure}>
      <strong>{name} is an AI borrower.</strong> A synthetic character for practice: not a real person, not a real
      loan. Your speech is transcribed live; KalPakka does not store your audio.
    </p>
  );
}

function useCallState(c: CallController): CallState {
  return useSyncExternalStore(
    (fn) => c.subscribe(fn),
    () => c.state,
    () => c.state,
  );
}

/** iOS Safari can leave the context "interrupted" (phone call, route change); a talk press is a gesture. */
function resumeContext(ctx: AudioContext | undefined): void {
  if (ctx && ctx.state !== "running" && ctx.state !== "closed") void ctx.resume();
}

function LiveCall({
  controller: c,
  wake,
  name,
  onOffline,
  onScored,
  onEnded,
}: {
  controller: CallController;
  name: string;
  wake: () => void;
  onOffline?: () => void;
  onScored?: (scorecard: unknown) => void;
  onEnded?: (state: CallState) => void;
}) {
  const s = useCallState(c);
  const [elapsed, setElapsed] = useState(0);
  const transcriptEnd = useRef<HTMLLIElement>(null);
  const inCall = s.phase === "in_call";

  useEffect(() => {
    if (!inCall) return;
    const t0 = Date.now();
    const id = setInterval(() => setElapsed((Date.now() - t0) / 1000), 500); // display only
    return () => clearInterval(id);
  }, [inCall]);

  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ block: "nearest" });
  }, [s.transcript.length]);

  const reported = useRef<{ ended: boolean; scored: boolean }>({ ended: false, scored: false });
  useEffect(() => {
    if (s.error?.code === "outside_live_hours") onOffline?.();
  }, [s.error, onOffline]);
  useEffect(() => {
    if (s.phase === "ended" && !reported.current.ended) {
      reported.current.ended = true;
      onEnded?.(s);
    }
    if (s.phase === "scored" && !reported.current.scored) {
      reported.current.scored = true;
      onScored?.(s.scorecard);
    }
  }, [s, onScored, onEnded]);

  // spacebar = hold to talk (auto-repeat ignored; released on blur so it never sticks)
  useEffect(() => {
    if (!inCall) return;
    const down = (e: KeyboardEvent) => {
      if (e.code !== "Space" || isEditable(e.target)) return;
      e.preventDefault();
      if (!e.repeat) {
        wake();
        c.pttStart();
      }
    };
    const up = (e: KeyboardEvent) => {
      if (e.code !== "Space" || isEditable(e.target)) return;
      e.preventDefault();
      void c.pttEnd();
    };
    const blur = () => void c.pttEnd();
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
    };
  }, [c, inCall, wake]);

  return (
    <section className={styles.screen}>
      <Disclosure name={name} />
      <header className={styles.header}>
        <div className={styles.who}>
          <strong>{name}</strong>
          <span>
            {s.briefing ? `${s.briefing.card.loan.lender} · ${s.briefing.card.loan.product} · simulated time ${formatSimClock(s.briefing.sim_clock)}` : ""}
          </span>
        </div>
        <div className={styles.meta}>
          {s.mood && <span className={styles.pill}>mood: {s.mood}</span>}
          {s.borrowerSpeaking && <span className={`${styles.pill} ${styles.speaking}`}>speaking…</span>}
          <span className={styles.pill} aria-label="call time">
            {formatClock(elapsed)}
          </span>
        </div>
      </header>

      {s.phase === "connecting" && <p className={styles.hint}>Connecting…</p>}
      {s.phase === "queued" && s.queue && (
        <p className={styles.disclosure} role="status">
          All lines are busy. You are <strong>number {s.queue.position}</strong> in the queue (about{" "}
          {Math.max(1, Math.round(s.queue.eta_s / 60))} min).
        </p>
      )}

      <ol className={styles.transcript} aria-live="polite" aria-label="Live transcript">
        {s.transcript.length === 0 && <li className={styles.empty}>The transcript of both sides appears here.</li>}
        {s.transcript.map((l, i) => (
          <li key={i} className={`${styles.line} ${l.role === "agent" ? styles.agent : styles.borrower}`}>
            <small>
              {l.role === "agent" ? "You" : name} · {formatClock(l.t)}
            </small>
            {l.text}
          </li>
        ))}
        <li ref={transcriptEnd} aria-hidden />
      </ol>

      <FlagStrip flags={s.flags} />

      {s.error && (
        <p className={styles.error} role="alert">
          {s.error.message} ({s.error.code})
        </p>
      )}

      {s.phase === "ended" && (
        <p className={styles.hint} role="status">
          {endReasonText(s.endReason ?? "", name)} {!s.briefing ? "" : s.scoreUnavailable ? "No scorecard is available for this call." : "Scoring your call…"}
        </p>
      )}

      {(inCall || s.phase === "connecting" || s.phase === "queued") && (
        <>
          <div className={styles.controls}>
            <button
              className={`${styles.talk} ${s.talking ? styles.talking : ""}`}
              disabled={!inCall}
              aria-pressed={s.talking}
              onPointerDown={(e) => {
                e.currentTarget.setPointerCapture(e.pointerId);
                wake();
                c.pttStart();
              }}
              onPointerUp={() => void c.pttEnd()}
              onPointerCancel={() => void c.pttEnd()}
              onContextMenu={(e) => e.preventDefault()}
            >
              {s.talking ? "Listening… release to send" : "Hold to talk"}
            </button>
            <button className={styles.hangup} onClick={() => c.hangUp()}>
              Hang up
            </button>
          </div>
          <p className={styles.hint}>Hold the button or the spacebar while you speak. Press it to interrupt.</p>
        </>
      )}
    </section>
  );
}
