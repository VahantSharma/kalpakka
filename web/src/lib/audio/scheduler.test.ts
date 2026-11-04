import { describe, expect, it } from "vitest";
import { LEAD_S, PlaybackScheduler, type PlaybackContext } from "./scheduler";

class FakeSource {
  buffer: FakeBuffer | null = null;
  onended: (() => void) | null = null;
  startedAt: number | null = null;
  stopped = false;
  connect(): void {}
  start(when: number): void {
    this.startedAt = when;
  }
  stop(): void {
    this.stopped = true;
    // browsers fire `ended` after stop(); the scheduler must not count it as "played"
    this.onended?.();
  }
  finish(): void {
    this.onended?.();
  }
}

class FakeBuffer {
  readonly data: Float32Array;
  constructor(
    readonly length: number,
    readonly sampleRate: number,
  ) {
    this.data = new Float32Array(length);
  }
  get duration(): number {
    return this.length / this.sampleRate;
  }
  getChannelData(): Float32Array {
    return this.data;
  }
}

class FakeContext implements PlaybackContext {
  currentTime = 10;
  sources: FakeSource[] = [];
  destination = {};
  createBuffer(_ch: number, length: number, rate: number): FakeBuffer {
    return new FakeBuffer(length, rate);
  }
  createBufferSource(): FakeSource {
    const s = new FakeSource();
    this.sources.push(s);
    return s;
  }
}

/** n samples of int16 PCM at a constant value, as the backend streams it. */
function pcm(n: number, value = 1000): ArrayBuffer {
  const b = new ArrayBuffer(n * 2);
  const v = new DataView(b);
  for (let i = 0; i < n; i++) v.setInt16(i * 2, value, true);
  return b;
}

function setup() {
  const ctx = new FakeContext();
  const played: Array<[number, number]> = [];
  const s = new PlaybackScheduler(ctx, (turn, sentence) => played.push([turn, sentence]));
  return { ctx, s, played };
}

describe("PlaybackScheduler", () => {
  it("schedules the first chunk a short lead after the current audio clock", () => {
    const { ctx, s } = setup();
    s.begin(1, 0, 24000);
    s.push(pcm(2400)); // 100 ms at 24 kHz
    expect(ctx.sources[0]!.startedAt).toBeCloseTo(10 + LEAD_S, 6);
  });

  it("schedules back-to-back chunks on the playhead with no gap or overlap", () => {
    const { ctx, s } = setup();
    s.begin(1, 0, 24000);
    s.push(pcm(2400));
    s.push(pcm(4800));
    s.begin(1, 1, 24000);
    s.push(pcm(2400));
    const starts = ctx.sources.map((x) => x.startedAt!);
    expect(starts[1]! - starts[0]!).toBeCloseTo(0.1, 6);
    expect(starts[2]! - starts[1]!).toBeCloseTo(0.2, 6);
  });

  it("restarts from the clock, not the stale playhead, after a pause in the stream", () => {
    const { ctx, s } = setup();
    s.begin(1, 0, 24000);
    s.push(pcm(2400));
    ctx.currentTime = 20; // long after the first chunk finished
    s.push(pcm(2400));
    expect(ctx.sources[1]!.startedAt).toBeCloseTo(20 + LEAD_S, 6);
  });

  it("converts little-endian int16 to float in [-1, 1) at the announced rate", () => {
    const { ctx, s } = setup();
    s.begin(1, 0, 22050);
    s.push(pcm(4, -16384));
    const buf = ctx.sources[0]!.buffer!;
    expect(buf.sampleRate).toBe(22050);
    expect(Array.from(buf.data)).toEqual([-0.5, -0.5, -0.5, -0.5]);
  });

  it("carries an odd trailing byte into the next chunk instead of clicking", () => {
    const { ctx, s } = setup();
    s.begin(1, 0, 24000);
    const whole = new Uint8Array(pcm(3, 0x0102));
    s.push(whole.slice(0, 3).buffer); // 1.5 samples
    s.push(whole.slice(3).buffer);
    const total = ctx.sources.reduce((n, x) => n + x.buffer!.length, 0);
    expect(total).toBe(3);
    expect(ctx.sources.flatMap((x) => Array.from(x.buffer!.data))).toEqual([
      0x0102 / 32768,
      0x0102 / 32768,
      0x0102 / 32768,
    ]);
  });

  it("reports a sentence played only after audio_end and every chunk has finished", () => {
    const { ctx, s, played } = setup();
    s.begin(2, 0, 24000);
    s.push(pcm(2400));
    s.push(pcm(2400));
    ctx.sources[0]!.finish();
    s.end(2, 0);
    expect(played).toEqual([]);
    ctx.sources[1]!.finish();
    expect(played).toEqual([[2, 0]]);
  });

  it("reports a sentence whose chunks all finished before audio_end when audio_end arrives", () => {
    const { ctx, s, played } = setup();
    s.begin(2, 1, 24000);
    s.push(pcm(240));
    ctx.sources[0]!.finish();
    expect(played).toEqual([]);
    s.end(2, 1);
    expect(played).toEqual([[2, 1]]);
  });

  it("stopAll silences every scheduled source and reports none of them as played", () => {
    const { ctx, s, played } = setup();
    s.begin(3, 0, 24000);
    s.push(pcm(2400));
    s.end(3, 0);
    s.begin(3, 1, 24000);
    s.push(pcm(2400));
    ctx.sources[0]!.finish(); // sentence 0 fully heard
    s.stopAll();
    expect(ctx.sources[1]!.stopped).toBe(true);
    expect(played).toEqual([[3, 0]]);
    expect(s.active).toBe(0);
  });

  it("drops audio for turns below the minimum valid turn (stale after a barge-in)", () => {
    const { ctx, s, played } = setup();
    s.dropTurnsBelow(4);
    s.begin(3, 2, 24000);
    s.push(pcm(2400));
    s.end(3, 2);
    expect(ctx.sources).toHaveLength(0);
    expect(played).toEqual([]);
    s.begin(4, 0, 24000);
    s.push(pcm(2400));
    expect(ctx.sources).toHaveLength(1);
  });

  it("ignores audio that arrives with no audio_start before it", () => {
    const { ctx, s } = setup();
    s.push(pcm(2400));
    expect(ctx.sources).toHaveLength(0);
  });

  it("plays fresh audio from the clock after stopAll", () => {
    const { ctx, s } = setup();
    s.begin(1, 0, 24000);
    s.push(pcm(24000)); // 1 s queued
    s.stopAll();
    s.begin(2, 0, 24000);
    s.push(pcm(2400));
    expect(ctx.sources[1]!.startedAt).toBeCloseTo(10 + LEAD_S, 6);
  });
});
