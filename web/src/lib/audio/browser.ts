/**
 * Web Audio wiring (browser only, not unit-tested: verified in Chromium, see CLOUD-RUN-B.md).
 * Everything here must be called from inside the "Start call" / mic-check click so the
 * AudioContext is allowed to run (autoplay rule).
 */

import type { Capture } from "../call/controller";
import type { FlushReply } from "./capture-core";
import type { AudioBufferLike, PlaybackContext, SourceLike } from "./scheduler";

export const WORKLET_URL = "/capture-worklet.js";
const FLUSH_TIMEOUT_MS = 300;

export interface CallAudio {
  ctx: AudioContext;
  stream: MediaStream;
}

/** Create (inside a click) the AudioContext and open the mic; reuses what is passed in. */
export async function startAudio(existing?: CallAudio | null): Promise<CallAudio> {
  const ctx = existing?.ctx ?? new AudioContext({ latencyHint: "interactive" });
  if (ctx.state === "suspended") await ctx.resume();
  const stream = existing?.stream ?? (await openMic());
  return { ctx, stream };
}

const workletLoaded = new WeakSet<BaseAudioContext>();

export async function openMic(): Promise<MediaStream> {
  return navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
}

/** AudioContext -> the scheduler's minimal interface. */
export function playbackContext(ctx: AudioContext): PlaybackContext {
  return {
    get currentTime() {
      return ctx.currentTime;
    },
    destination: ctx.destination,
    createBuffer: (ch, len, rate): AudioBufferLike => ctx.createBuffer(ch, len, rate),
    createBufferSource: (): SourceLike => {
      const node = ctx.createBufferSource();
      const src: SourceLike = {
        get buffer() {
          return node.buffer;
        },
        set buffer(b) {
          node.buffer = b as AudioBuffer | null;
        },
        onended: null,
        connect: (dest) => node.connect(dest as AudioNode),
        start: (when) => node.start(when),
        stop: () => node.stop(),
      };
      node.onended = () => src.onended?.();
      return src;
    },
  };
}

/** Mic -> AudioWorklet (resample + frame) -> Capture.onFrame. */
export class WorkletCapture implements Capture {
  onFrame: (frame: ArrayBuffer) => void = () => {};
  private pendingFlush: { id: number; resolve: (f: ArrayBuffer | null) => void } | null = null;
  private flushId = 0;
  private constructor(
    private readonly node: AudioWorkletNode,
    private readonly source: MediaStreamAudioSourceNode,
    private readonly sink: GainNode,
  ) {
    node.port.onmessage = (e: MessageEvent<{ type: "frame"; frame: ArrayBuffer } | FlushReply>) => {
      const m = e.data;
      if (m.type === "frame") this.onFrame(m.frame);
      else if (this.pendingFlush && m.id === this.pendingFlush.id) {
        this.pendingFlush.resolve(m.frame);
        this.pendingFlush = null;
      } // a late reply to a timed-out flush is ignored
    };
  }

  static async create(ctx: AudioContext, stream: MediaStream): Promise<WorkletCapture> {
    if (!workletLoaded.has(ctx)) {
      await ctx.audioWorklet.addModule(WORKLET_URL); // once per context: a retry reuses it
      workletLoaded.add(ctx);
    }
    const node = new AudioWorkletNode(ctx, "kalpakka-capture", { numberOfInputs: 1, numberOfOutputs: 1 });
    const source = ctx.createMediaStreamSource(stream);
    const sink = ctx.createGain();
    sink.gain.value = 0; // keeps the node pulled by the graph without playing the mic back
    source.connect(node);
    node.connect(sink);
    sink.connect(ctx.destination);
    return new WorkletCapture(node, source, sink);
  }

  start(): void {
    this.node.port.postMessage({ type: "start" });
  }

  flush(): Promise<ArrayBuffer | null> {
    const id = ++this.flushId;
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        if (this.pendingFlush?.id === id) this.pendingFlush = null;
        resolve(null);
      }, FLUSH_TIMEOUT_MS);
      this.pendingFlush = {
        id,
        resolve: (f) => {
          clearTimeout(timer);
          resolve(f);
        },
      };
      this.node.port.postMessage({ type: "flush", id });
    });
  }

  close(): void {
    this.node.port.onmessage = null;
    this.source.disconnect();
    this.node.disconnect();
    this.sink.disconnect();
  }
}

/** Stop the mic (browser indicator off) and close the context. */
export function releaseAudio(a: { ctx: AudioContext; stream: MediaStream }): void {
  for (const t of a.stream.getTracks()) t.stop();
  if (a.ctx.state !== "closed") void a.ctx.close();
}

/** Mic level 0..1 for the mic check meter (RMS of the analyser's time domain). */
export function micLevelMeter(ctx: AudioContext, stream: MediaStream): { read(): number; close(): void } {
  const source = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);
  const buf = new Float32Array(analyser.fftSize);
  return {
    read() {
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (const v of buf) sum += v * v;
      return Math.min(1, Math.sqrt(sum / buf.length) * 4);
    },
    close() {
      source.disconnect();
    },
  };
}
