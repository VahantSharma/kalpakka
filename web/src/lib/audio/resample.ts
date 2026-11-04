/**
 * Mic capture resampling: browser rate (44.1 / 48 kHz float32) -> 16 kHz int16 for Prisma.
 *
 * Low-pass first (causal Hann-windowed sinc FIR, cutoff 0.4 x 16 kHz = 6.4 kHz), then linear
 * interpolation at the output instants (docs/context/08: "low-pass, then decimate"). State is
 * carried across calls so 128-sample render quanta give exactly the same samples as one big
 * buffer. Pure TypeScript with no DOM types: it runs inside the AudioWorklet and under vitest.
 */

export const TARGET_RATE = 16000;

/** float32 [-1, 1] -> int16, clamped; negative full scale is -32768, positive 32767. */
export function floatToInt16(x: Float32Array): Int16Array {
  const out = new Int16Array(x.length);
  for (let i = 0; i < x.length; i++) {
    const s = Math.max(-1, Math.min(1, x[i]!));
    out[i] = Math.round(s < 0 ? s * 32768 : s * 32767);
  }
  return out;
}

function lowPassTaps(inRate: number, cutoffHz: number, count: number): Float32Array {
  const taps = new Float32Array(count);
  const fc = cutoffHz / inRate; // cycles per input sample
  const mid = (count - 1) / 2;
  let sum = 0;
  for (let k = 0; k < count; k++) {
    const n = k - mid;
    const sinc = n === 0 ? 2 * fc : Math.sin(2 * Math.PI * fc * n) / (Math.PI * n);
    const hann = 0.5 - 0.5 * Math.cos((2 * Math.PI * k) / (count - 1));
    taps[k] = sinc * hann;
    sum += taps[k]!;
  }
  for (let k = 0; k < count; k++) taps[k] = taps[k]! / sum; // unity gain at DC
  return taps;
}

export class Downsampler {
  private readonly ratio: number;
  private readonly taps: Float32Array;
  private history = new Float32Array(0); // input samples kept for the next call
  private historyStart = 0; // stream index of history[0]
  private produced = 0; // output samples emitted so far

  constructor(
    private readonly inRate: number,
    private readonly outRate: number = TARGET_RATE,
  ) {
    if (!(inRate >= outRate)) throw new RangeError(`cannot upsample ${inRate} Hz to ${outRate} Hz`);
    this.ratio = inRate / outRate;
    const count = (Math.round(this.ratio * 21) | 1) as number; // odd; 63 taps at 48 kHz
    this.taps = lowPassTaps(inRate, 0.4 * outRate, count);
    this.reset();
  }

  reset(): void {
    // causal filter: N-1 zeros of history so the first samples have a full window
    this.history = new Float32Array(this.taps.length - 1);
    this.historyStart = -(this.taps.length - 1);
    this.produced = 0;
  }

  process(block: Float32Array): Int16Array {
    if (this.ratio === 1) return floatToInt16(block);

    const data = new Float32Array(this.history.length + block.length);
    data.set(this.history);
    data.set(block, this.history.length);
    const start = this.historyStart;
    const lastIndex = start + data.length - 1; // last stream index available
    const taps = this.taps;
    const n = taps.length;

    // y(j) = sum_k taps[k] * x[j - k]
    const filtered = (j: number): number => {
      let acc = 0;
      const base = j - start;
      for (let k = 0; k < n; k++) acc += taps[k]! * data[base - k]!;
      return acc;
    };

    const out: number[] = [];
    for (;;) {
      const t = this.produced * this.ratio; // recomputed each time: no drift
      const i0 = Math.floor(t);
      if (i0 + 1 > lastIndex) break;
      const y0 = filtered(i0);
      const y1 = filtered(i0 + 1);
      out.push(y0 + (y1 - y0) * (t - i0));
      this.produced++;
    }

    // keep what the next output still needs: n-1 samples before floor(next t)
    const keepFrom = Math.floor(this.produced * this.ratio) - (n - 1);
    const drop = Math.max(0, keepFrom - start);
    this.history = data.slice(drop);
    this.historyStart = start + drop;

    return floatToInt16(Float32Array.from(out));
  }
}
