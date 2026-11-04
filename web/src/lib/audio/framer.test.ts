import { describe, expect, it } from "vitest";
import { FRAME_BYTES, Framer } from "./framer";

describe("Framer", () => {
  it("emits one 1,024-byte frame for 512 samples (32 ms at 16 kHz)", () => {
    const f = new Framer();
    const frames = f.push(new Int16Array(512));
    expect(FRAME_BYTES).toBe(1024);
    expect(frames).toHaveLength(1);
    expect(frames[0]!.byteLength).toBe(1024);
  });

  it("holds back a partial frame until enough samples arrive", () => {
    const f = new Framer();
    expect(f.push(new Int16Array(700))).toHaveLength(1);
    expect(f.pending).toBe(188);
    expect(f.push(new Int16Array(323))).toHaveLength(0);
    const frames = f.push(new Int16Array(1));
    expect(frames).toHaveLength(1);
    expect(f.pending).toBe(0);
  });

  it("emits several frames from one large push, all exactly 1,024 bytes", () => {
    const f = new Framer();
    const frames = f.push(new Int16Array(512 * 3 + 10));
    expect(frames.map((x) => x.byteLength)).toEqual([1024, 1024, 1024]);
  });

  it("writes samples little-endian and keeps their order across pushes", () => {
    const f = new Framer();
    f.push(Int16Array.from({ length: 300 }, (_, i) => i));
    const [frame] = f.push(Int16Array.from({ length: 212 }, (_, i) => 300 + i));
    const bytes = new Uint8Array(frame!);
    expect([bytes[0], bytes[1]]).toEqual([0x00, 0x00]);
    expect([bytes[2], bytes[3]]).toEqual([0x01, 0x00]);
    // sample 511 = 0x01FF -> FF 01
    expect([bytes[1022], bytes[1023]]).toEqual([0xff, 0x01]);
    const negative = new Framer().push(Int16Array.from({ length: 512 }, () => -2))[0]!;
    expect(Array.from(new Uint8Array(negative).subarray(0, 2))).toEqual([0xfe, 0xff]);
  });

  it("flush pads the remainder with zeros to a full frame", () => {
    const f = new Framer();
    f.push(new Int16Array(100).fill(7));
    const last = f.flush();
    expect(last).not.toBeNull();
    const view = new DataView(last!);
    expect(last!.byteLength).toBe(1024);
    expect(view.getInt16(99 * 2, true)).toBe(7);
    expect(view.getInt16(100 * 2, true)).toBe(0);
    expect(f.pending).toBe(0);
  });

  it("flush with nothing pending returns null", () => {
    expect(new Framer().flush()).toBeNull();
  });

  it("each frame owns its buffer, so sending one never aliases the next", () => {
    const f = new Framer();
    const [a, b] = f.push(Int16Array.from({ length: 1024 }, (_, i) => (i < 512 ? 1 : 2)));
    expect(a).not.toBe(b);
    expect(new DataView(a!).getInt16(0, true)).toBe(1);
    expect(new DataView(b!).getInt16(0, true)).toBe(2);
  });

  it("reset drops the pending remainder", () => {
    const f = new Framer();
    f.push(new Int16Array(10));
    f.reset();
    expect(f.pending).toBe(0);
    expect(f.flush()).toBeNull();
  });
});
