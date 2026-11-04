import { describe, expect, it } from "vitest";
import { CaptureCore } from "./capture-core";

const quantum = (v = 0.25) => new Float32Array(128).fill(v);

describe("CaptureCore (the worklet's logic)", () => {
  it("sends nothing while talk is not held", () => {
    const core = new CaptureCore(16000);
    for (let i = 0; i < 10; i++) expect(core.process(quantum())).toEqual([]);
  });

  it("frames mic audio after start, with nothing from before the press", () => {
    const core = new CaptureCore(16000);
    core.process(quantum(0.9)); // before the press: must not leak into the first frame
    core.handle({ type: "start" });
    const out = [0, 1, 2, 3].flatMap(() => core.process(quantum(0.25)));
    expect(out).toHaveLength(1);
    expect(out[0]!.byteLength).toBe(1024);
    expect(new DataView(out[0]!).getInt16(0, true)).toBe(Math.round(0.25 * 32767));
  });

  it("flush returns the padded remainder tagged with its id, then stops sending", () => {
    const core = new CaptureCore(16000);
    core.handle({ type: "start" });
    core.process(quantum());
    const reply = core.handle({ type: "flush", id: 7 });
    expect(reply).toMatchObject({ type: "flush", id: 7 });
    expect(reply?.frame?.byteLength).toBe(1024);
    for (let i = 0; i < 8; i++) expect(core.process(quantum())).toEqual([]);
  });

  it("flush with nothing pending returns a null frame", () => {
    const core = new CaptureCore(48000);
    core.handle({ type: "start" });
    expect(core.handle({ type: "flush", id: 1 })).toEqual({ type: "flush", id: 1, frame: null });
  });
});
