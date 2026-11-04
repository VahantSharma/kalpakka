/**
 * Mock KalPakka backend for UI work: speaks the browser <-> backend protocol of
 * docs/context/05-architecture.md with a scripted, clearly fake borrower (tones, not speech).
 * No Gnani, no LLM, no scoring: the scorecard is the synthetic fixture.
 *
 *   node mock/server.ts             # :8787
 *   MOCK_BUSY=2 node mock/server.ts # queue position 2 -> 1 -> call
 *   MOCK_OFFLINE=1 ...              # outside live hours: /personas 503, start_call -> error
 *
 * Checks the client on the way: every binary frame must be exactly 1,024 bytes, and binary may
 * arrive only between ptt_start and ptt_end (FR-3). Violations come back as `error` messages.
 */

import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { WebSocketServer, type WebSocket } from "ws";
import { MOCK_PERSONAS, MOCK_SCORECARD, MOCK_TRANSCRIPT } from "../src/lib/mock/fixtures.ts";
import { MOCK_ARENA_RUN } from "../src/lib/mock/arena.ts";

const PORT = Number(process.env.MOCK_PORT ?? 8787);
const BUSY = Number(process.env.MOCK_BUSY ?? 0);
const OFFLINE = process.env.MOCK_OFFLINE === "1";
const RATE = 24000;
const CHUNK_MS = 100;
const CALL_CAP_S = 300;

const borrowerLines = MOCK_TRANSCRIPT.filter((l) => l.role === "borrower").map((l) => l.text);
const replies: string[][] = borrowerLines.slice(1).map((text) => splitSentences(text));
const moods = ["irritated", "irritated", "calmer", "calmer"];

function splitSentences(text: string): string[] {
  return text.match(/[^.?!।]+[.?!।]?/g)?.map((s) => s.trim()).filter(Boolean) ?? [text];
}

/** A voice-like hum: 140 Hz fundamental + harmonics, syllable-rate amplitude wobble. */
function synth(text: string): Buffer {
  const seconds = Math.min(3.5, Math.max(0.8, text.length * 0.06));
  const n = Math.round(seconds * RATE);
  const buf = Buffer.alloc(n * 2);
  for (let i = 0; i < n; i++) {
    const t = i / RATE;
    const env = Math.min(1, t * 20, (seconds - t) * 20) * (0.55 + 0.45 * Math.sin(2 * Math.PI * 4.5 * t));
    const v = 0.18 * env * (Math.sin(2 * Math.PI * 140 * t) + 0.5 * Math.sin(2 * Math.PI * 280 * t) + 0.25 * Math.sin(2 * Math.PI * 420 * t));
    buf.writeInt16LE(Math.round(Math.max(-1, Math.min(1, v)) * 32767), i * 2);
  }
  return buf;
}

function cors(res: ServerResponse): void {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Content-Type", "application/json");
}

const http = createServer((req: IncomingMessage, res: ServerResponse) => {
  cors(res);
  const url = req.url ?? "/";
  if (url === "/healthz") return void res.end(JSON.stringify({ ok: true, live: !OFFLINE, mock: true }));
  if (url === "/personas") {
    if (OFFLINE) {
      res.statusCode = 503;
      return void res.end(JSON.stringify({ code: "outside_live_hours" }));
    }
    return void res.end(JSON.stringify(MOCK_PERSONAS));
  }
  if (/^\/calls\/[^/]+\/scorecard$/.test(url)) return void res.end(JSON.stringify(MOCK_SCORECARD));
  if (url === "/arena/runs/mock") return void res.end(JSON.stringify(MOCK_ARENA_RUN));
  res.statusCode = 404;
  res.end(JSON.stringify({ code: "not_found" }));
});

const wss = new WebSocketServer({ server: http, path: "/call" });

wss.on("connection", (ws: WebSocket) => {
  const send = (msg: object) => ws.readyState === ws.OPEN && ws.send(JSON.stringify(msg));
  const timers = new Set<NodeJS.Timeout>();
  const later = (ms: number, fn: () => void) => {
    const t = setTimeout(() => {
      timers.delete(t);
      fn();
    }, ms);
    timers.add(t);
  };
  let streamTimers = new Set<NodeJS.Timeout>();
  const stream = (ms: number, fn: () => void) => {
    const t = setTimeout(() => {
      streamTimers.delete(t);
      fn();
    }, ms);
    streamTimers.add(t);
  };

  const started = Date.now();
  const clock = () => Math.round((Date.now() - started) / 100) / 10;
  let turn = 0; // borrower reply number (audio turn id)
  let agentTurns = 0;
  let talking = false;
  let frames = 0;
  let bytes = 0;
  let speaking: { turn: number; sentences: string[]; played: Set<number> } | null = null;
  let ended = false;

  function speak(sentences: string[]): void {
    const t = turn;
    speaking = { turn: t, sentences, played: new Set() };
    let at = 0;
    sentences.forEach((text, sentence) => {
      const pcm = synth(text);
      stream(at, () => {
        send({ type: "audio_start", turn: t, sentence, rate: RATE });
        send({ type: "transcript", role: "borrower", text, t: clock() });
      });
      const step = (RATE * 2 * CHUNK_MS) / 1000;
      for (let off = 0, k = 0; off < pcm.length; off += step, k++) {
        const chunk = pcm.subarray(off, off + step);
        stream(at + k * CHUNK_MS * 0.8, () => ws.readyState === ws.OPEN && ws.send(chunk)); // a bit ahead of real time
      }
      at += (pcm.length / 2 / RATE) * 1000 * 0.8;
      stream(at, () => send({ type: "audio_end", turn: t, sentence }));
    });
  }

  function bargeIn(): void {
    for (const t of streamTimers) clearTimeout(t);
    streamTimers = new Set();
    if (speaking) {
      const heard = [...speaking.played].sort();
      console.log(`[mock] barge-in on turn ${speaking.turn}: history trimmed to sentences [${heard.join(", ")}]`);
      send({ type: "stop_audio" });
    }
    speaking = null;
    turn++; // min valid turn bump: later audio carries a higher turn id
  }

  function agentFinished(): void {
    agentTurns++;
    const seconds = (bytes / 32000).toFixed(1);
    send({ type: "transcript", role: "agent", text: `(mock transcript) ${seconds} s of speech, ${frames} frames`, t: clock() });
    if (agentTurns === 2) {
      const flag = { id: `f-${agentTurns}-0-misleading`, turn: agentTurns, segment: 0, t: clock(), rule: "misleading", quote: "CIBIL pe asar padega", layer: "lexicon", status: "possible" };
      send({ type: "flag", flag });
      later(1500, () => send({ type: "flag_update", flag: { ...flag, layer: "llm", status: "withdrawn" } }));
    }
    if (agentTurns === 3) {
      const flag = { id: `f-${agentTurns}-0-threat`, turn: agentTurns, segment: 0, t: clock(), rule: "threat", quote: "police bhej denge ghar pe", layer: "lexicon", status: "possible" };
      send({ type: "flag", flag });
      later(1200, () => send({ type: "flag_update", flag: { ...flag, layer: "llm", status: "confirmed" } }));
    }
    const mood = moods[agentTurns - 1];
    if (mood) send({ type: "mood", label: mood });
    const reply = replies[(agentTurns - 1) % replies.length]!;
    turn++;
    stream(700, () => speak(reply)); // stand-in for STT + LLM + TTS latency; cancelled by barge-in
  }

  function endCall(reason: string): void {
    if (ended) return;
    ended = true;
    for (const t of streamTimers) clearTimeout(t);
    send({ type: "call_ended", reason });
    later(1500, () => {
      send({ type: "scorecard", scorecard: MOCK_SCORECARD });
    });
  }

  ws.on("message", (data: Buffer, isBinary: boolean) => {
    if (isBinary) {
      if (!talking) return send({ type: "error", code: "audio_outside_ptt", message: "mic audio arrived while talk was not held" });
      if (data.length !== 1024) send({ type: "error", code: "bad_frame", message: `frame was ${data.length} bytes, expected 1024` });
      frames++;
      bytes += data.length;
      return;
    }
    let msg: { type?: string; turn?: number; sentence?: number; persona?: string };
    try {
      msg = JSON.parse(data.toString("utf8"));
    } catch {
      return send({ type: "error", code: "bad_json", message: "text frame was not JSON" });
    }
    switch (msg.type) {
      case "start_call": {
        if (OFFLINE) {
          send({ type: "error", code: "outside_live_hours", message: "Ramesh is offline outside live hours" });
          return void ws.close(1000);
        }
        const card = MOCK_PERSONAS.find((p) => p.id === msg.persona) ?? MOCK_PERSONAS[0]!;
        const begin = () => {
          send({ type: "briefing", call_id: `mock-${started}`, facts: card, sim_clock: "2026-11-09T18:20" });
          later(600, () => speak([borrowerLines[0]!]));
          later(CALL_CAP_S * 1000, () => endCall("time_cap"));
        };
        if (BUSY > 0) {
          for (let p = BUSY; p >= 1; p--) later((BUSY - p) * 3000, () => send({ type: "busy", position: p, eta_s: p * 90 }));
          later(BUSY * 3000, begin);
        } else begin();
        return;
      }
      case "ptt_start":
        talking = true;
        frames = 0;
        bytes = 0;
        bargeIn();
        return;
      case "ptt_end":
        talking = false;
        agentFinished();
        return;
      case "played":
        if (speaking && msg.turn === speaking.turn && typeof msg.sentence === "number") speaking.played.add(msg.sentence);
        return;
      case "end_call":
        return endCall("agent_hangup");
      default:
        send({ type: "error", code: "unknown_type", message: `unknown type ${String(msg.type)}` });
    }
  });

  ws.on("close", () => {
    for (const t of timers) clearTimeout(t);
    for (const t of streamTimers) clearTimeout(t);
  });
});

http.listen(PORT, () => console.log(`[mock] KalPakka mock backend on http://localhost:${PORT} (ws /call)`));
