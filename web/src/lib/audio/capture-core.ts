/**
 * The capture worklet's logic, outside the AudioWorkletGlobalScope so it can be unit-tested.
 * Frames go out only between "start" and "flush" (FR-3); "start" resets the resampler and the
 * framer, so nothing captured before the press reaches the backend. Flush replies carry the
 * request id so a late reply can never be taken for the next release's flush.
 */

import { Framer } from "./framer";
import { Downsampler } from "./resample";

export type CaptureCommand = { type: "start" } | { type: "flush"; id: number };
export type FlushReply = { type: "flush"; id: number; frame: ArrayBuffer | null };

export class CaptureCore {
  private readonly ds: Downsampler;
  private readonly framer = new Framer();
  private active = false;

  constructor(contextRate: number) {
    this.ds = new Downsampler(contextRate);
  }

  handle(cmd: CaptureCommand): FlushReply | null {
    if (cmd.type === "start") {
      this.ds.reset();
      this.framer.reset();
      this.active = true;
      return null;
    }
    const frame = this.active ? this.framer.flush() : null;
    this.active = false;
    return { type: "flush", id: cmd.id, frame };
  }

  process(mono: Float32Array): ArrayBuffer[] {
    if (!this.active || mono.length === 0) return [];
    return this.framer.push(this.ds.process(mono));
  }
}
