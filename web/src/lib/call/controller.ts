/**
 * One call, browser side. Owns the protocol conversation and the barge-in order; knows nothing
 * about DOM, WebSocket or Web Audio (those are injected), so every rule here is unit-tested.
 *
 * Barge-in (docs/context/05, 08): on talk press the player is silenced locally FIRST, audio of
 * the interrupted turn and earlier is dropped from then on, and only then is `ptt_start` sent.
 */

import { decodeServer, encodeClient } from "../protocol/codec";
import type { ClientMessage, Difficulty, Flag, PublicCard, Role, ServerMessage } from "../protocol/types";

export interface Transport {
  send(data: string | ArrayBuffer): void;
  close(): void;
  onMessage: (data: string | ArrayBuffer) => void;
  onClose: (reason: string) => void;
}

export interface Player {
  onPlayed: (turn: number, sentence: number) => void;
  begin(turn: number, sentence: number, rate: number): void;
  push(chunk: ArrayBuffer): void;
  end(turn: number, sentence: number): void;
  stopAll(): void;
  dropTurnsBelow(turn: number): void;
}

export interface Capture {
  onFrame: (frame: ArrayBuffer) => void;
  /** Reset resampler and framer state at the start of a talk press. */
  start(): void;
  /** The zero-padded remainder after the last full frame, or null. */
  flush(): Promise<ArrayBuffer | null>;
}

export type Phase = "idle" | "connecting" | "queued" | "in_call" | "ended" | "scored";

export interface TranscriptLine {
  role: Role;
  text: string;
  t: number;
}

export interface CallState {
  phase: Phase;
  queue: { position: number; eta_s: number } | null;
  briefing: { call_id: string; card: PublicCard; sim_clock: string } | null;
  transcript: TranscriptLine[];
  flags: Flag[];
  mood: string | null;
  talking: boolean;
  borrowerSpeaking: boolean;
  endReason: string | null;
  scorecard: unknown;
  scoreUnavailable: boolean; // the socket closed after the call with no scorecard
  error: { code: string; message: string } | null;
}

const initialState = (): CallState => ({
  phase: "idle",
  queue: null,
  briefing: null,
  transcript: [],
  flags: [],
  mood: null,
  talking: false,
  borrowerSpeaking: false,
  endReason: null,
  scorecard: null,
  scoreUnavailable: false,
  error: null,
});

/** Add a flag, or replace the one with the same id (status changes arrive as flag_update). */
export function upsertFlag(flags: Flag[], flag: Flag): Flag[] {
  const i = flags.findIndex((f) => f.id === flag.id);
  if (i === -1) return [...flags, flag];
  const next = flags.slice();
  next[i] = flag;
  return next;
}

export class CallController {
  state: CallState = initialState();
  private listeners = new Set<() => void>();
  private lastAudioTurn = -1;
  private lastSentence: [number, number] | null = null;
  private streaming = false; // mic frames go up only while talk is held (and its flush)
  private releasing = false; // between release and ptt_end being sent
  private pendingPress = false; // pressed again while releasing: start right after ptt_end
  private hungUp = false;

  constructor(
    private readonly transport: Transport,
    private readonly player: Player,
    private readonly capture: Capture,
  ) {
    transport.onMessage = (data) => this.receive(data);
    transport.onClose = (reason) => this.closed(reason);
    player.onPlayed = (turn, sentence) => this.played(turn, sentence);
    capture.onFrame = (frame) => {
      if (this.streaming) this.transport.send(frame);
    };
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  start(persona: string, difficulty: Difficulty, anonId: string): void {
    this.send({ type: "start_call", persona, difficulty, anon_id: anonId });
    this.set({ phase: "connecting" });
  }

  pttStart(): void {
    if (this.hungUp || this.state.talking) return;
    if (this.releasing) {
      this.pendingPress = true;
      return;
    }
    this.player.stopAll(); // 1. silence locally, ~0 ms
    this.dropSeenTurns(); // 2. stale audio
    this.capture.start();
    this.streaming = true;
    this.send({ type: "ptt_start" }); // 3. then tell the backend
    this.set({ talking: true, borrowerSpeaking: false });
  }

  async pttEnd(): Promise<void> {
    if (this.releasing) {
      this.pendingPress = false;
      return;
    }
    if (!this.state.talking) return;
    this.releasing = true;
    this.set({ talking: false });
    try {
      const last = await this.capture.flush();
      if (last && this.streaming) this.transport.send(last);
    } finally {
      this.streaming = false;
      this.releasing = false;
    }
    if (this.hungUp) return;
    this.send({ type: "ptt_end" });
    if (this.pendingPress) {
      this.pendingPress = false;
      this.pttStart();
    }
  }

  hangUp(): void {
    if (this.hungUp) return;
    this.hungUp = true;
    this.pendingPress = false;
    this.player.stopAll();
    this.streaming = false;
    this.send({ type: "end_call" });
    this.set({ talking: false, borrowerSpeaking: false });
  }

  /** Leaving the page: end a live call, silence audio, close the socket. */
  dispose(): void {
    const live = this.state.phase === "connecting" || this.state.phase === "queued" || this.state.phase === "in_call";
    if (live) this.hangUp();
    else this.player.stopAll();
    this.transport.close();
  }

  /** Audio for any turn seen so far is stale from now on (the socket is ordered). */
  private dropSeenTurns(): void {
    if (this.lastAudioTurn >= 0) this.player.dropTurnsBelow(this.lastAudioTurn + 1);
  }

  /** While the trainee holds talk (or the release is flushing), borrower audio must not play. */
  private get muted(): boolean {
    return this.state.talking || this.releasing || this.hungUp;
  }

  private send(msg: ClientMessage): void {
    this.transport.send(encodeClient(msg));
  }

  private played(turn: number, sentence: number): void {
    this.send({ type: "played", turn, sentence });
    const l = this.lastSentence;
    if (l && l[0] === turn && l[1] === sentence) this.set({ borrowerSpeaking: false });
  }

  private receive(data: string | ArrayBuffer): void {
    if (typeof data !== "string") {
      if (!this.muted) this.player.push(data);
      return;
    }
    const r = decodeServer(data);
    if (!r.ok) {
      this.set({ error: { code: "protocol", message: r.error } });
      return;
    }
    this.handle(r.msg);
  }

  private handle(msg: ServerMessage): void {
    switch (msg.type) {
      case "busy":
        this.set({ phase: "queued", queue: { position: msg.position, eta_s: msg.eta_s } });
        return;
      case "briefing":
        this.set({ phase: "in_call", queue: null, briefing: { call_id: msg.call_id, card: msg.facts, sim_clock: msg.sim_clock } });
        return;
      case "audio_start":
        this.lastAudioTurn = Math.max(this.lastAudioTurn, msg.turn);
        if (this.muted) {
          this.dropSeenTurns(); // a reply already in flight when talk was pressed
          return;
        }
        this.lastSentence = [msg.turn, msg.sentence];
        this.player.begin(msg.turn, msg.sentence, msg.rate);
        this.set({ borrowerSpeaking: true });
        return;
      case "audio_end":
        if (!this.muted) this.player.end(msg.turn, msg.sentence);
        return;
      case "stop_audio":
        this.player.stopAll();
        this.dropSeenTurns();
        this.set({ borrowerSpeaking: false });
        return;
      case "transcript":
        this.set({ transcript: [...this.state.transcript, { role: msg.role, text: msg.text, t: msg.t }] });
        return;
      case "flag":
      case "flag_update":
        this.set({ flags: upsertFlag(this.state.flags, msg.flag) });
        return;
      case "mood":
        this.set({ mood: msg.label });
        return;
      case "call_ended":
        this.player.stopAll();
        this.streaming = false;
        this.set({ phase: "ended", endReason: msg.reason, talking: false, borrowerSpeaking: false });
        return;
      case "scorecard":
        this.set({ phase: "scored", scorecard: msg.scorecard });
        this.transport.close();
        return;
      case "error":
        this.set({ error: { code: msg.code, message: msg.message } });
        return;
    }
  }

  private closed(reason: string): void {
    if (this.state.phase === "scored") return;
    if (this.state.phase === "ended") {
      this.set({ scoreUnavailable: true });
      return;
    }
    this.player.stopAll();
    this.streaming = false;
    this.set({ phase: "ended", endReason: reason, talking: false, borrowerSpeaking: false });
  }

  private set(patch: Partial<CallState>): void {
    this.state = { ...this.state, ...patch };
    for (const fn of this.listeners) fn();
  }
}
