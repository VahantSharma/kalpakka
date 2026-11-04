import { describe, expect, it } from "vitest";
import { MOCK_PERSONAS } from "../mock/fixtures";
import { checkLive, loadPersonas } from "./personas";

const res = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

describe("checkLive", () => {
  it("reads the live flag from GET /healthz", async () => {
    const urls: string[] = [];
    const f = (live: boolean) => (url: string) => {
      urls.push(url);
      return res(200, { ok: true, live, active_calls: 0 });
    };
    expect(await checkLive("http://api.test", f(true))).toBe("open");
    expect(await checkLive("http://api.test", f(false))).toBe("closed");
    expect(urls[0]).toBe("http://api.test/healthz");
  });

  it("is unknown when health cannot be read", async () => {
    expect(await checkLive("http://api.test", () => Promise.reject(new TypeError("x")))).toBe("unknown");
    expect(await checkLive("http://api.test", () => res(500, {}))).toBe("unknown");
    expect(await checkLive("http://api.test", () => res(200, { ok: true }))).toBe("unknown");
  });
});

describe("loadPersonas", () => {
  it("returns the public cards from GET /personas", async () => {
    const urls: string[] = [];
    const r = await loadPersonas("http://api.test", (url) => {
      urls.push(String(url));
      return res(200, MOCK_PERSONAS);
    });
    expect(urls).toEqual(["http://api.test/personas"]);
    expect(r).toEqual({ kind: "ok", cards: MOCK_PERSONAS });
  });

  it("keeps only public fields even if the backend sent more", async () => {
    const leaky = [{ ...MOCK_PERSONAS[0], hidden: { truth: "auto off the road" }, temperament: {} }];
    const r = await loadPersonas("http://api.test", () => res(200, leaky));
    expect(r.kind).toBe("ok");
    if (r.kind === "ok") expect(Object.keys(r.cards[0]!)).not.toContain("hidden");
  });

  it("skips a malformed card but keeps the valid ones", async () => {
    const r = await loadPersonas("http://api.test", () => res(200, [{ id: "broken" }, MOCK_PERSONAS[1]]));
    expect(r).toEqual({ kind: "ok", cards: [MOCK_PERSONAS[1]] });
  });

  it("treats 503 as outside live hours", async () => {
    expect(await loadPersonas("http://api.test", () => res(503, { code: "outside_live_hours" }))).toEqual({ kind: "offline" });
  });

  it("reports an unreachable backend as an error, not as outside live hours", async () => {
    const r = await loadPersonas("http://api.test", () => Promise.reject(new TypeError("Failed to fetch")));
    expect(r).toEqual({ kind: "error", message: "cannot reach the KalPakka server" });
  });

  it("reports other failures as errors", async () => {
    expect(await loadPersonas("http://api.test", () => res(500, {}))).toEqual({ kind: "error", message: "GET /personas: HTTP 500" });
    expect(await loadPersonas("http://api.test", () => res(200, { not: "a list" }))).toEqual({
      kind: "error",
      message: "GET /personas: expected a list",
    });
  });
});
