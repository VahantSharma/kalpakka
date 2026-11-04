/**
 * Plays streamed borrower PCM (int16 LE at the rate in `audio_start`) gaplessly on the
 * AudioContext clock: a running playhead, ~50 ms lead, never timers (docs/context/08).
 * Tracks every source so barge-in can stop them all, and reports a sentence as played only when
 * its `audio_end` has arrived and every chunk of it has finished playing.
 */

export const LEAD_S = 0.05;

export interface AudioBufferLike {
  readonly length: number;
  readonly duration: number;
  getChannelData(channel: number): Float32Array;
}

export interface SourceLike {
  buffer: AudioBufferLike | null;
  onended: (() => void) | null;
  connect(dest: unknown): unknown;
  start(when: number): void;
  stop(): void;
}

export interface PlaybackContext {
  readonly currentTime: number;
  readonly destination: unknown;
  createBuffer(channels: number, length: number, sampleRate: number): AudioBufferLike;
  createBufferSource(): SourceLike;
}

interface Sentence {
  turn: number;
  sentence: number;
  rate: number;
  live: number; // sources scheduled and not yet ended
  ended: boolean; // audio_end received
  reported: boolean;
}

export type PlayedCallback = (turn: number, sentence: number) => void;

export class PlaybackScheduler {
  private playhead = 0;
  private current: Sentence | null = null;
  private sentences = new Map<string, Sentence>();
  private sources = new Set<SourceLike>();
  private minTurn = 0;
  private carry: number | null = null; // odd trailing byte from the previous chunk

  constructor(
    private readonly ctx: PlaybackContext,
    public onPlayed: PlayedCallback = () => {},
  ) {}

  /** Number of sources scheduled and not yet finished or stopped. */
  get active(): number {
    return this.sources.size;
  }

  dropTurnsBelow(turn: number): void {
    this.minTurn = Math.max(this.minTurn, turn);
    if (this.current && this.current.turn < this.minTurn) this.current = null;
  }

  begin(turn: number, sentence: number, rate: number): void {
    this.carry = null;
    if (turn < this.minTurn) {
      this.current = null;
      return;
    }
    const s: Sentence = { turn, sentence, rate, live: 0, ended: false, reported: false };
    this.sentences.set(key(turn, sentence), s);
    this.current = s;
  }

  push(chunk: ArrayBuffer): void {
    const s = this.current;
    if (!s) return; // no audio_start, or a stale turn: drop
    const samples = this.decode(new Uint8Array(chunk));
    if (samples.length === 0) return;

    const buffer = this.ctx.createBuffer(1, samples.length, s.rate);
    buffer.getChannelData(0).set(samples);
    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.ctx.destination);

    const when = Math.max(this.playhead, this.ctx.currentTime + LEAD_S);
    this.playhead = when + buffer.duration;
    s.live++;
    this.sources.add(src);
    src.onended = () => {
      if (!this.sources.delete(src)) return; // stopped by stopAll: not played
      s.live--;
      this.maybeReport(s);
    };
    src.start(when);
  }

  end(turn: number, sentence: number): void {
    const s = this.sentences.get(key(turn, sentence));
    if (!s) return;
    s.ended = true;
    if (this.current === s) this.current = null;
    this.maybeReport(s);
  }

  /** Barge-in or hang-up: silence everything now. Unfinished sentences are never reported. */
  stopAll(): void {
    const all = [...this.sources];
    this.sources.clear();
    for (const src of all) {
      try {
        src.stop();
      } catch {
        // a source that already ended throws on some browsers; it is silent either way
      }
    }
    this.sentences.clear();
    this.current = null;
    this.carry = null;
    this.playhead = 0;
  }

  private maybeReport(s: Sentence): void {
    if (s.ended && s.live === 0 && !s.reported) {
      s.reported = true;
      this.sentences.delete(key(s.turn, s.sentence));
      this.onPlayed(s.turn, s.sentence);
    }
  }

  private decode(bytes: Uint8Array): Float32Array {
    let src = bytes;
    if (this.carry !== null) {
      src = new Uint8Array(bytes.length + 1);
      src[0] = this.carry;
      src.set(bytes, 1);
      this.carry = null;
    }
    const n = src.length >> 1;
    if (src.length % 2 === 1) this.carry = src[src.length - 1]!;
    const view = new DataView(src.buffer, src.byteOffset, n * 2);
    const out = new Float32Array(n);
    for (let i = 0; i < n; i++) out[i] = view.getInt16(i * 2, true) / 32768;
    return out;
  }
}

function key(turn: number, sentence: number): string {
  return `${turn}:${sentence}`;
}
