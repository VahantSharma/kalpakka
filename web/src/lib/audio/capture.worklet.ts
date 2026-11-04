/**
 * AudioWorklet processor: mic (context rate, float32) -> 16 kHz int16 -> exact 1,024-byte frames,
 * only while talk is held. Bundled to public/capture-worklet.js by scripts/build-worklet.mjs;
 * all logic lives in CaptureCore (capture-core.test.ts, resample.test.ts, framer.test.ts).
 *
 * Port in:  {type: "start"} | {type: "flush", id}
 * Port out: {type: "frame", frame} | {type: "flush", id, frame: ArrayBuffer | null}
 */

import { CaptureCore, type CaptureCommand } from "./capture-core";

declare const sampleRate: number;
declare class AudioWorkletProcessor {
  readonly port: MessagePort;
}
declare function registerProcessor(name: string, ctor: new () => AudioWorkletProcessor): void;

class CaptureProcessor extends AudioWorkletProcessor {
  private readonly core = new CaptureCore(sampleRate);

  constructor() {
    super();
    this.port.onmessage = (e: MessageEvent<CaptureCommand>) => {
      const reply = this.core.handle(e.data);
      if (reply) this.port.postMessage(reply, reply.frame ? [reply.frame] : []);
    };
  }

  process(inputs: Float32Array[][]): boolean {
    const mono = inputs[0]?.[0];
    if (mono) for (const frame of this.core.process(mono)) this.port.postMessage({ type: "frame", frame }, [frame]);
    return true;
  }
}

registerProcessor("kalpakka-capture", CaptureProcessor);
