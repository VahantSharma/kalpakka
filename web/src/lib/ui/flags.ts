import type { Flag, RuleId } from "../protocol/types";

/** Plain-words names for the rule catalogue (content/rules/catalogue.yaml meanings, shortened). */
export const RULE_LABELS: Record<RuleId, string> = {
  threat: "Threat or intimidation",
  abuse: "Abusive or humiliating language",
  third_party_disclosure: "Disclosed the debt to a third party",
  bereavement_pressure: "Recovery talk during bereavement",
  late_clock: "Recovery call after 7 PM",
  misleading: "Misleading statement about consequences",
  repeated_pressure: "Pressed for money right after a hardship",
  grievance_ignored: "Demanded payment while a grievance is pending",
};

export type FlagTone = "amber" | "red" | "faded";

export interface FlagView {
  tone: FlagTone;
  statusLabel: Flag["status"];
  ruleLabel: string;
  quote: string;
  at: string;
  note: string | null;
}

const TONES: Record<Flag["status"], FlagTone> = { possible: "amber", confirmed: "red", withdrawn: "faded" };

export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** "2026-11-09T18:40" -> "6:40 PM": the call's simulated time of day (late_clock rule at 7 PM). */
export function formatSimClock(iso: string): string {
  const m = /T(\d{2}):(\d{2})/.exec(iso);
  if (!m) return iso;
  const h = Number(m[1]);
  return `${h % 12 === 0 ? 12 : h % 12}:${m[2]} ${h < 12 ? "AM" : "PM"}`;
}

export function flagView(flag: Flag): FlagView {
  return {
    tone: TONES[flag.status],
    statusLabel: flag.status,
    ruleLabel: RULE_LABELS[flag.rule],
    quote: `“${flag.quote}”`,
    at: formatClock(flag.t),
    note: flag.status === "withdrawn" ? "on reflection, this was fine" : null,
  };
}

/** call_ended reasons (EndReason in backend session.py) in plain words. */
export function endReasonText(reason: string, name: string): string {
  const text: Record<string, string> = {
    agent_hangup: "You hung up.",
    disconnected: "The connection dropped.",
    bad_start: "The call could not start.",
    angry: `${name} hung up, angry.`,
    silence: `${name} hung up after a long silence.`,
    closed_after_promise: `${name} made a promise and ended the call.`,
    bereavement_pressure: `${name} hung up: you pressed for money during a bereavement.`,
    time_cap: "The call reached its time limit.",
    llm_unavailable: `${name}'s mind (the language model) is not responding. Please try again later.`,
    tts_unavailable: "The voice service is not responding. Please try again later.",
    stt_unavailable: "The speech-to-text service is not responding. Please try again later.",
    error: "Something went wrong on our side.",
  };
  return text[reason] ?? `Call ended (${reason}).`;
}
