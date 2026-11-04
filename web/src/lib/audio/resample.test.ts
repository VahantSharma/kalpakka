import { describe, expect, it } from "vitest";
import { Downsampler, floatToInt16 } from "./resample";

/** One second of a sine wave in 128-sample render quanta, as an AudioWorklet delivers it. */
function sineBlocks(rate: number, hz: number, seconds = 1, amp = 0.5): Float32Array[] {
  const total = Math.round(rate * seconds);
  const blocks: Float32Array[] = [];
  for (let start = 0; start < total; start += 128) {
    const n = Math.min(128, total - start);
    const b = new Float32Array(n);
    for (let i = 0; i < n; i++) b[i] = amp * Math.sin((2 * Math.PI * hz * (start + i)) / rate);
    blocks.push(b);
  }
  return blocks;
}

function concat(parts: Int16Array[]): Int16Array {
  const out = new Int16Array(parts.reduce((s, p) => s + p.length, 0));
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}

function zeroCrossings(x: Int16Array): number {
  let c = 0;
  for (let i = 1; i < x.length; i++) if ((x[i - 1]! < 0) !== (x[i]! < 0)) c++;
  return c;
}

describe("floatToInt16", () => {
  it("maps full scale to the int16 limits and clamps beyond them", () => {
    expect(Array.from(floatToInt16(new Float32Array([1, -1, 1.5, -1.5, 0])))).toEqual([
      32767, -32768, 32767, -32768, 0,
    ]);
  });
});

describe("Downsampler", () => {
  it("turns one second at 48 kHz into 16,000 samples", () => {
    const d = new Downsampler(48000);
    const out = concat(sineBlocks(48000, 440).map((b) => d.process(b)));
    expect(out.length).toBe(16000);
  });

  it("turns one second at 44.1 kHz into 16,000 samples, within one", () => {
    const d = new Downsampler(44100);
    const out = concat(sineBlocks(44100, 440).map((b) => d.process(b)));
    expect(Math.abs(out.length - 16000)).toBeLessThanOrEqual(1);
  });

  it("keeps the pitch: a 1 kHz tone at 48 kHz is still 1 kHz at 16 kHz", () => {
    const d = new Downsampler(48000);
    // 1,003 Hz so zeros never land exactly on an output sample (sign of 0 is ambiguous)
    const out = concat(sineBlocks(48000, 1003).map((b) => d.process(b))).subarray(160);
    const hz = zeroCrossings(out) / 2 / (out.length / 16000);
    expect(Math.abs(hz - 1003)).toBeLessThan(10);
  });

  it("keeps the level of speech-band audio (300 Hz sine, amplitude 0.5)", () => {
    const d = new Downsampler(48000);
    const out = concat(sineBlocks(48000, 300).map((b) => d.process(b)));
    const peak = Math.max(...Array.from(out.subarray(1000)).map(Math.abs));
    expect(peak).toBeGreaterThan(0.45 * 32767);
    expect(peak).toBeLessThan(0.52 * 32767);
  });

  it("attenuates a 7 kHz tone that would alias at 16 kHz", () => {
    const d = new Downsampler(48000);
    const out = concat(sineBlocks(48000, 7000).map((b) => d.process(b)));
    const peak = Math.max(...Array.from(out.subarray(1000)).map(Math.abs));
    expect(peak).toBeLessThan(0.5 * 0.5 * 32767); // at least -6 dB below the input
  });

  it("gives the same samples whether fed in 128-sample blocks or all at once", () => {
    const blocks = sineBlocks(44100, 523, 0.25);
    const whole = new Float32Array(blocks.reduce((s, b) => s + b.length, 0));
    let o = 0;
    for (const b of blocks) {
      whole.set(b, o);
      o += b.length;
    }
    const stream = new Downsampler(44100);
    const streamed = concat(blocks.map((b) => stream.process(b)));
    const once = new Downsampler(44100).process(whole);
    expect(Array.from(streamed)).toEqual(Array.from(once));
  });

  it("passes 16 kHz input straight through", () => {
    const d = new Downsampler(16000);
    expect(Array.from(d.process(new Float32Array([0, 0.5, -0.5])))).toEqual([0, 16384, -16384]);
  });

  it("refuses to upsample", () => {
    expect(() => new Downsampler(8000)).toThrow(RangeError);
  });

  it("starts fresh after reset", () => {
    const d = new Downsampler(48000);
    d.process(new Float32Array(100).fill(0.9));
    d.reset();
    expect(Array.from(d.process(new Float32Array(6)))).toEqual([0, 0]);
  });
});
