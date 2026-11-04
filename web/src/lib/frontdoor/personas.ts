import { decodePublicCard } from "../protocol/codec";
import type { PublicCard } from "../protocol/types";

export type PersonasResult =
  | { kind: "ok"; cards: PublicCard[] }
  | { kind: "offline" } // the backend says it is outside live hours (HTTP 503)
  | { kind: "error"; message: string };

type Fetch = (url: string) => Promise<Response>;

/** GET /healthz `live`: whether the backend is inside announced live hours right now. */
export async function checkLive(base: string, fetchFn: Fetch = (u) => fetch(u)): Promise<"open" | "closed" | "unknown"> {
  try {
    const res = await fetchFn(`${base}/healthz`);
    if (!res.ok) return "unknown";
    const body: unknown = await res.json();
    const live = typeof body === "object" && body !== null ? (body as { live?: unknown }).live : undefined;
    return live === true ? "open" : live === false ? "closed" : "unknown";
  } catch {
    return "unknown";
  }
}

/**
 * GET /personas. Every card is re-built field by field from the public schema, so a hidden
 * field the backend sent by mistake never reaches React state. Invalid cards are skipped.
 */
export async function loadPersonas(base: string, fetchFn: Fetch = (u) => fetch(u)): Promise<PersonasResult> {
  let res: Response;
  try {
    res = await fetchFn(`${base}/personas`);
  } catch {
    // a crash, CORS or DNS problem during live hours must not look like "come back later"
    return { kind: "error", message: "cannot reach the KalPakka server" };
  }
  if (res.status === 503) return { kind: "offline" };
  if (!res.ok) return { kind: "error", message: `GET /personas: HTTP ${res.status}` };
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    return { kind: "error", message: "GET /personas: not JSON" };
  }
  if (!Array.isArray(body)) return { kind: "error", message: "GET /personas: expected a list" };
  const cards: PublicCard[] = [];
  for (const raw of body) {
    if (typeof raw !== "object" || raw === null) continue;
    try {
      cards.push(decodePublicCard(raw as Record<string, unknown>, "persona"));
    } catch {
      // malformed card: skip it, keep the rest playable
    }
  }
  return { kind: "ok", cards };
}
