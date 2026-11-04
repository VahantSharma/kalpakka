import { describe, expect, it } from "vitest";
import { BACKEND_HTTP, BACKEND_WS } from "./env";

describe("backend urls", () => {
  it("fall back to the local mock backend when the env vars are unset", () => {
    expect(BACKEND_WS).toBe("ws://localhost:8787/call");
    expect(BACKEND_HTTP).toBe("http://localhost:8787");
  });
});
