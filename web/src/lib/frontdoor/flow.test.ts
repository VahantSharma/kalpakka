import { describe, expect, it } from "vitest";
import { MOCK_PERSONAS, MOCK_SCORECARD } from "../mock/fixtures";
import { flow, initialFlow, type FlowState } from "./flow";

const [ramesh, lakshmi] = MOCK_PERSONAS as [(typeof MOCK_PERSONAS)[0], (typeof MOCK_PERSONAS)[0]];
const loaded = (): FlowState => flow(initialFlow, { type: "loaded", result: { kind: "ok", cards: MOCK_PERSONAS } });

describe("front-door flow", () => {
  it("starts loading and shows the picker once personas arrive", () => {
    expect(initialFlow.step).toBe("loading");
    const s = loaded();
    expect(s.step).toBe("pick");
    expect(s.cards).toHaveLength(3);
  });

  it("shows the outside-live-hours state when the backend is offline", () => {
    expect(flow(initialFlow, { type: "loaded", result: { kind: "offline" } }).step).toBe("offline");
  });

  it("shows an error when personas fail to load for another reason", () => {
    const s = flow(initialFlow, { type: "loaded", result: { kind: "error", message: "GET /personas: HTTP 500" } });
    expect(s).toMatchObject({ step: "error", message: "GET /personas: HTTP 500" });
  });

  it("an empty persona list is an error, not an empty picker", () => {
    expect(flow(initialFlow, { type: "loaded", result: { kind: "ok", cards: [] } }).step).toBe("error");
  });

  it("picking a borrower opens the briefing at that borrower's default difficulty", () => {
    const s = flow(loaded(), { type: "pick", id: "lakshmi" });
    expect(s).toMatchObject({ step: "brief", persona: lakshmi, difficulty: "hard" });
  });

  it("ignores a pick for an unknown id", () => {
    expect(flow(loaded(), { type: "pick", id: "nobody" }).step).toBe("pick");
  });

  it("difficulty can be changed on the briefing", () => {
    const s = flow(flow(loaded(), { type: "pick", id: "ramesh" }), { type: "difficulty", difficulty: "easy" });
    expect(s.difficulty).toBe("easy");
  });

  it("goes briefing -> mic check -> call", () => {
    let s = flow(loaded(), { type: "pick", id: "ramesh" });
    s = flow(s, { type: "to_mic" });
    expect(s.step).toBe("mic");
    s = flow(s, { type: "mic_ok" });
    expect(s).toMatchObject({ step: "call", persona: ramesh, difficulty: "medium" });
  });

  it("a mic or call step cannot be reached without a borrower", () => {
    expect(flow(loaded(), { type: "to_mic" }).step).toBe("pick");
    expect(flow(loaded(), { type: "mic_ok" }).step).toBe("pick");
  });

  it("the scorecard follows the call", () => {
    let s = flow(loaded(), { type: "pick", id: "ramesh" });
    s = flow(flow(s, { type: "to_mic" }), { type: "mic_ok" });
    s = flow(s, { type: "scored", scorecard: MOCK_SCORECARD });
    expect(s).toMatchObject({ step: "score", scorecard: MOCK_SCORECARD });
  });

  it("retry after the scorecard goes back to the call with the same borrower", () => {
    let s = flow(loaded(), { type: "pick", id: "ramesh" });
    s = flow(flow(flow(s, { type: "to_mic" }), { type: "mic_ok" }), { type: "scored", scorecard: MOCK_SCORECARD });
    s = flow(s, { type: "retry" });
    expect(s).toMatchObject({ step: "call", persona: ramesh, scorecard: null });
    expect(s.attempt).toBe(2);
  });

  it("back returns to the picker from anywhere but keeps the cards", () => {
    const s = flow(flow(loaded(), { type: "pick", id: "ramesh" }), { type: "back" });
    expect(s).toMatchObject({ step: "pick", persona: null });
    expect(s.cards).toHaveLength(3);
  });

  it("an outside-live-hours error during start switches to the offline state", () => {
    let s = flow(loaded(), { type: "pick", id: "ramesh" });
    s = flow(flow(s, { type: "to_mic" }), { type: "mic_ok" });
    expect(flow(s, { type: "offline" }).step).toBe("offline");
  });
});
