/**
 * Browser <-> backend protocol, exactly the table in docs/context/05-architecture.md.
 * Field names match backend/src/kalpakka/protocol.py on claude/build: `briefing` carries
 * `call_id`, `facts` (the PublicCard) and `sim_clock` (ISO "YYYY-MM-DDTHH:MM"); `flag` and
 * `flag_update` carry `flag`; `scorecard` carries `scorecard`.
 */

export type Difficulty = "easy" | "medium" | "hard";
export type Role = "agent" | "borrower";

// --- mirrors backend/src/kalpakka/tripwire.py ---
export const RULE_IDS = [
  "threat",
  "abuse",
  "third_party_disclosure",
  "bereavement_pressure",
  "late_clock",
  "misleading",
  "repeated_pressure",
  "grievance_ignored",
] as const;
export type RuleId = (typeof RULE_IDS)[number];
export const LAYERS = ["lexicon", "llm", "state"] as const;
export type Layer = (typeof LAYERS)[number];
export const FLAG_STATUSES = ["possible", "confirmed", "withdrawn"] as const;
export type FlagStatus = (typeof FLAG_STATUSES)[number];

export interface Flag {
  id: string;
  turn: number;
  segment: number;
  t: number;
  rule: RuleId;
  quote: string;
  layer: Layer;
  status: FlagStatus;
}

// --- mirrors PublicCard in backend/src/kalpakka/personas.py (never hidden fields) ---
export interface PublicCard {
  id: string;
  version: number;
  display_name: string;
  language: string;
  difficulty_default: Difficulty;
  profile: { age: number; occupation: string; city: string };
  loan: {
    lender: string;
    product: string;
    emi: number;
    overdue_amount: number;
    dpd: number;
    past_promises: number;
  };
}

// --- client -> server ---
export type ClientMessage =
  | { type: "start_call"; persona: string; difficulty: Difficulty; anon_id: string }
  | { type: "ptt_start" }
  | { type: "ptt_end" }
  | { type: "played"; turn: number; sentence: number }
  | { type: "end_call" };

// --- server -> client ---
export type ServerMessage =
  | { type: "busy"; position: number; eta_s: number }
  | { type: "briefing"; call_id: string; facts: PublicCard; sim_clock: string }
  | { type: "audio_start"; turn: number; sentence: number; rate: number }
  | { type: "audio_end"; turn: number; sentence: number; rate?: number }
  | { type: "stop_audio" }
  | { type: "transcript"; role: Role; text: string; t: number }
  | { type: "flag"; flag: Flag }
  | { type: "flag_update"; flag: Flag }
  | { type: "mood"; label: string }
  | { type: "call_ended"; reason: string }
  | { type: "scorecard"; scorecard: unknown }
  | { type: "error"; code: string; message: string };

export type ServerType = ServerMessage["type"];
