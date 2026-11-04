import {
  FLAG_STATUSES,
  LAYERS,
  RULE_IDS,
  type ClientMessage,
  type Flag,
  type PublicCard,
  type ServerMessage,
} from "./types";

export type Decoded = { ok: true; msg: ServerMessage } | { ok: false; error: string };

export function encodeClient(msg: ClientMessage): string {
  return JSON.stringify(msg);
}

class DecodeError extends Error {}

type Obj = Record<string, unknown>;

function isObj(v: unknown): v is Obj {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function obj(o: Obj, key: string, path: string): Obj {
  const v = o[key];
  if (!isObj(v)) throw new DecodeError(`${path}.${key}: expected object`);
  return v;
}

function int(o: Obj, key: string, path: string): number {
  const v = o[key];
  if (typeof v !== "number" || !Number.isInteger(v)) throw new DecodeError(`${path}.${key}: expected integer`);
  return v;
}

function num(o: Obj, key: string, path: string): number {
  const v = o[key];
  if (typeof v !== "number" || !Number.isFinite(v)) throw new DecodeError(`${path}.${key}: expected number`);
  return v;
}

function str(o: Obj, key: string, path: string): string {
  const v = o[key];
  if (typeof v !== "string") throw new DecodeError(`${path}.${key}: expected string`);
  return v;
}

function oneOf<T extends string>(o: Obj, key: string, path: string, allowed: readonly T[]): T {
  const v = str(o, key, path);
  if (!(allowed as readonly string[]).includes(v)) {
    throw new DecodeError(`${path}.${key}: expected one of ${allowed.join(", ")}`);
  }
  return v as T;
}

function flag(o: Obj, path: string): Flag {
  const f = obj(o, "flag", path);
  const p = `${path}.flag`;
  return {
    id: str(f, "id", p),
    turn: int(f, "turn", p),
    segment: int(f, "segment", p),
    t: num(f, "t", p),
    rule: oneOf(f, "rule", p, RULE_IDS),
    quote: str(f, "quote", p),
    layer: oneOf(f, "layer", p, LAYERS),
    status: oneOf(f, "status", p, FLAG_STATUSES),
  };
}

export function decodePublicCard(c: Obj, p: string): PublicCard {
  const profile = obj(c, "profile", p);
  const loan = obj(c, "loan", p);
  return {
    id: str(c, "id", p),
    version: int(c, "version", p),
    display_name: str(c, "display_name", p),
    language: str(c, "language", p),
    difficulty_default: oneOf(c, "difficulty_default", p, ["easy", "medium", "hard"] as const),
    profile: {
      age: int(profile, "age", `${p}.profile`),
      occupation: str(profile, "occupation", `${p}.profile`),
      city: str(profile, "city", `${p}.profile`),
    },
    loan: {
      lender: str(loan, "lender", `${p}.loan`),
      product: str(loan, "product", `${p}.loan`),
      emi: int(loan, "emi", `${p}.loan`),
      overdue_amount: int(loan, "overdue_amount", `${p}.loan`),
      dpd: int(loan, "dpd", `${p}.loan`),
      past_promises: int(loan, "past_promises", `${p}.loan`),
    },
  };
}

function decodeObject(o: Obj): ServerMessage {
  const type = o.type;
  if (typeof type !== "string") throw new DecodeError("type: expected string");
  const p = type;
  switch (type) {
    case "busy":
      return { type, position: int(o, "position", p), eta_s: num(o, "eta_s", p) };
    case "briefing":
      return {
        type,
        call_id: str(o, "call_id", p),
        facts: decodePublicCard(obj(o, "facts", p), `${p}.facts`),
        sim_clock: str(o, "sim_clock", p),
      };
    case "audio_start":
      return { type, turn: int(o, "turn", p), sentence: int(o, "sentence", p), rate: int(o, "rate", p) };
    case "audio_end": {
      const msg: ServerMessage = { type, turn: int(o, "turn", p), sentence: int(o, "sentence", p) };
      if (o.rate !== undefined) msg.rate = int(o, "rate", p);
      return msg;
    }
    case "stop_audio":
      return { type };
    case "transcript":
      return {
        type,
        role: oneOf(o, "role", p, ["agent", "borrower"] as const),
        text: str(o, "text", p),
        t: num(o, "t", p),
      };
    case "flag":
    case "flag_update":
      return { type, flag: flag(o, p) };
    case "mood":
      return { type, label: str(o, "label", p) };
    case "call_ended":
      return { type, reason: str(o, "reason", p) };
    case "scorecard":
      if (!isObj(o.scorecard)) throw new DecodeError("scorecard.scorecard: expected object");
      return { type, scorecard: o.scorecard };
    case "error":
      return { type, code: str(o, "code", p), message: str(o, "message", p) };
    default:
      throw new DecodeError(`unknown type: ${type}`);
  }
}

/** Decode one JSON text frame from the backend. Never throws. */
export function decodeServer(text: string): Decoded {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return { ok: false, error: "not JSON" };
  }
  if (!isObj(raw)) return { ok: false, error: "expected a JSON object" };
  try {
    return { ok: true, msg: decodeObject(raw) };
  } catch (e) {
    if (e instanceof DecodeError) return { ok: false, error: e.message };
    throw e;
  }
}
