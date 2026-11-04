import type { Difficulty, PublicCard } from "../protocol/types";
import type { Scorecard } from "../scorecard/types";
import type { PersonasResult } from "./personas";

export type Step = "loading" | "pick" | "brief" | "mic" | "call" | "score" | "offline" | "error";

export interface FlowState {
  step: Step;
  cards: PublicCard[];
  persona: PublicCard | null;
  difficulty: Difficulty;
  scorecard: Scorecard | null;
  attempt: number; // increments per call; keys the call screen so a retry is a fresh call
  message: string | null;
}

export type FlowEvent =
  | { type: "loaded"; result: PersonasResult }
  | { type: "pick"; id: string }
  | { type: "difficulty"; difficulty: Difficulty }
  | { type: "to_mic" }
  | { type: "mic_ok" }
  | { type: "scored"; scorecard: Scorecard }
  | { type: "retry" }
  | { type: "back" }
  | { type: "offline" };

export const initialFlow: FlowState = {
  step: "loading",
  cards: [],
  persona: null,
  difficulty: "medium",
  scorecard: null,
  attempt: 0,
  message: null,
};

export function flow(s: FlowState, e: FlowEvent): FlowState {
  switch (e.type) {
    case "loaded":
      if (e.result.kind === "offline") return { ...s, step: "offline" };
      if (e.result.kind === "error") return { ...s, step: "error", message: e.result.message };
      if (e.result.cards.length === 0) return { ...s, step: "error", message: "No borrowers are available right now." };
      return { ...s, step: "pick", cards: e.result.cards };
    case "pick": {
      const persona = s.cards.find((c) => c.id === e.id);
      if (!persona) return s;
      return { ...s, step: "brief", persona, difficulty: persona.difficulty_default };
    }
    case "difficulty":
      return { ...s, difficulty: e.difficulty };
    case "to_mic":
      return s.persona ? { ...s, step: "mic" } : s;
    case "mic_ok":
      return s.persona ? { ...s, step: "call", attempt: s.attempt + 1, scorecard: null } : s;
    case "scored":
      return { ...s, step: "score", scorecard: e.scorecard };
    case "retry":
      return s.persona ? { ...s, step: "call", attempt: s.attempt + 1, scorecard: null } : s;
    case "back":
      return { ...s, step: s.cards.length ? "pick" : "loading", persona: null, scorecard: null };
    case "offline":
      return { ...s, step: "offline" };
  }
}
