/**
 * Packs 16 kHz int16 samples into exact 1,024-byte frames (512 samples = 32 ms), little-endian,
 * the only frame size Prisma accepts (docs/context/08). Each frame owns its ArrayBuffer so it can
 * be handed to WebSocket.send or transferred out of the AudioWorklet without aliasing.
 */

export const FRAME_BYTES = 1024;
export const FRAME_SAMPLES = FRAME_BYTES / 2;

export class Framer {
  private buf = new Int16Array(FRAME_SAMPLES);
  private fill = 0;

  get pending(): number {
    return this.fill;
  }

  push(samples: Int16Array): ArrayBuffer[] {
    const frames: ArrayBuffer[] = [];
    let i = 0;
    while (i < samples.length) {
      const take = Math.min(FRAME_SAMPLES - this.fill, samples.length - i);
      this.buf.set(samples.subarray(i, i + take), this.fill);
      this.fill += take;
      i += take;
      if (this.fill === FRAME_SAMPLES) frames.push(this.emit());
    }
    return frames;
  }

  /** On ptt_end: the remainder zero-padded to a full frame, or null if nothing is pending. */
  flush(): ArrayBuffer | null {
    if (this.fill === 0) return null;
    this.buf.fill(0, this.fill);
    return this.emit();
  }

  reset(): void {
    this.fill = 0;
  }

  private emit(): ArrayBuffer {
    const out = new ArrayBuffer(FRAME_BYTES);
    const view = new DataView(out);
    for (let s = 0; s < FRAME_SAMPLES; s++) view.setInt16(s * 2, this.buf[s]!, true);
    this.fill = 0;
    return out;
  }
}
