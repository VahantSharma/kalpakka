# 05 · System architecture

One idea: **a single backend conducts every call.** The browser is a microphone, a speaker and a
screen. Gnani's APIs are the ears and the voice. Evon on a GPU is the imagination. The backend owns
the truth: borrower state, conversation, timings, flags, scores.

```
Browser (Next.js on Vercel)
  │  one WebSocket per call: binary PCM up/down + JSON events
  ▼
Backend (FastAPI + Uvicorn, Docker, VM in Mumbai, Caddy for HTTPS)
  admission → session(CallSession) ─┬─ gnani_stt ──wss──▶ Prisma v3
                                    ├─ gnani_tts ──wss──▶ Timbre v3
                                    ├─ brain ─────https─▶ Evon v3.3 on vLLM (rented 80 GB GPU)
                                    ├─ borrower_state, personas, chunker
                                    ├─ tripwire (lexicon · LLM · state rules)
                                    ├─ judge (post-call) ─https─▶ Evon (thinking on)
                                    ├─ metrics
                                    └─ db (SQLite)
  arena: runs bot-vs-borrower calls in text mode using the same brain/state/tripwire/judge
```

## Modules (backend/src/kalpakka/)

| Module | Job | Depends on |
|--------|-----|-----------|
| `config` | `Settings` from env (pydantic-settings) | — |
| `admission` | may a call start? queue, caps, live hours | config |
| `session` | one `CallSession` per call: state machine, owns all tasks, barge-in | everything below |
| `gnani_stt` | Prisma stream: headers, 1,024-byte paced frames, commit, transcripts | config |
| `gnani_tts` | Timbre socket: sentence → audio chunks, reset after interrupt; REST pre-render | config |
| `personas` | load + validate persona YAML (public card vs hidden fields) | — |
| `borrower_state` | state variables, update rules, reveal gate, promise validity, trap schedule, turn brief | personas |
| `brain` | prompt builder, stream Evon, split speech from `###STATE###` JSON | borrower_state |
| `chunker` | streamed text → speakable sentences; tag policing | — |
| `tripwire` | lexicon (YAML regex), LLM check, state rules → flags | borrower_state, rules |
| `judge` | post-call checks with quotes → quote verification → points in code | tripwire, borrower_state |
| `arena` | bot prompt vs borrower, run matrix, report | brain, borrower_state, tripwire, judge |
| `metrics` | per-stage timestamps, percentiles, cost counters | — |
| `db` | SQLite access (async-safe) | — |

`brain`, `borrower_state`, `chunker`, `tripwire`, `judge` are **text-in, text-out** and know nothing
about audio or sockets — that is what lets Arena run without audio and makes them unit-testable.

## Principles (and the failure each prevents)

1. **Only the backend has secrets** → keys and the hidden truth never reach a browser.
2. **Text is the contract between stages** → every hop is loggable, checkable, scoreable.
3. **State lives in code; the LLM acts** → no forgotten facts, no early caving, no leaked secret.
4. **Everything streams, nothing blocks** → one slow call cannot freeze the server.
5. **Data, not code, for content** → tuning a scenario is not a code change.
6. **One engine, three modes** → one definition of "good".

## CallSession state machine

`connecting → borrower_speaking(pickup) → agent_turn ⇄ agent_speaking → thinking → borrower_speaking
→ agent_turn … → ending → judging → done`. Any state → `ending` on hang-up (either side), time cap,
socket drop, or fatal error. Barge-in: `borrower_speaking --ptt_start--> agent_speaking`.

One turn (targets): ptt_end (t=0) → `commit` → final transcript (0.2–0.5 s) → build brief (< 5 ms)
→ Evon first token (0.15–0.4 s) → first sentence cut (+0.1–0.3 s) → Timbre first audio (0.15–0.3 s)
→ browser plays (≈ 1.2 s total). Sentences 2..n synthesise while 1 plays. The `###STATE###` update
arrives last and is applied when the reply finishes.

Barge-in: browser stops playback **locally first**, sends `ptt_start`; backend cancels the reply
task, empties the sentence queue, resets the Timbre socket, bumps the minimum valid turn number
(stale audio dropped), trims the borrower's last turn to the sentences the browser reported
`played`, discards that turn's state update except a small "interrupted" mood bump.

## Browser ↔ backend protocol (WebSocket `/call`)

Binary frames: 16-bit PCM. Up: mic at 16 kHz in 1,024-byte frames. Down: borrower audio at the
rate announced in `audio_start`. Text frames are JSON with a `type`:

| Dir | type | fields | when |
|-----|------|--------|------|
| → | `start_call` | persona, difficulty, anon_id | first message |
| → | `ptt_start` / `ptt_end` | — | talk pressed / released |
| → | `played` | turn, sentence | a borrower sentence finished playing |
| → | `end_call` | — | trainee hangs up |
| ← | `busy` | position, eta_s | queue full |
| ← | `briefing` | visible facts, sim_clock | call accepted |
| ← | `audio_start` / `audio_end` | turn, sentence, rate | around each borrower sentence |
| ← | `stop_audio` | — | barge-in or hang-up |
| ← | `transcript` | role, text, t | each agent segment and borrower sentence |
| ← | `flag` / `flag_update` | flag object | tripwire result / status change |
| ← | `mood` | label only | optional UI hint, never numbers |
| ← | `call_ended` | reason | call over |
| ← | `scorecard` | verified scorecard | after judging |
| ← | `error` | code, message | recoverable problems |

REST: `GET /personas` (no hidden fields) · `GET /calls/{id}/scorecard` · `GET /share/{id}.png` ·
`GET /leaderboard` · `POST /arena/runs` · `GET /arena/runs/{id}` · `GET /metrics/latency` ·
`DELETE /me` · `GET /healthz` (backend, GPU, Gnani reachability).

## Data model (SQLite, no audio stored)

```sql
CREATE TABLE players (anon_id TEXT PRIMARY KEY, nickname TEXT, created_at TEXT,
  clone_consent INTEGER DEFAULT 0, voice_embedding TEXT);
CREATE TABLE calls (id TEXT PRIMARY KEY, anon_id TEXT, mode TEXT,        -- train|arena|audit
  persona_id TEXT, persona_version TEXT, prompt_version TEXT, difficulty TEXT,
  seed INTEGER, traps TEXT, started_at TEXT, ended_at TEXT, end_reason TEXT,
  score_total INTEGER, promise_amount INTEGER, promise_date TEXT, promise_valid INTEGER);
CREATE TABLE turns (call_id TEXT, idx INTEGER, role TEXT, text TEXT,     -- borrower: text actually heard
  t_start REAL, t_end REAL, interrupted INTEGER, latency TEXT, state_after TEXT,
  PRIMARY KEY (call_id, idx));
CREATE TABLE flags (id TEXT PRIMARY KEY, call_id TEXT, turn_idx INTEGER, segment_idx INTEGER,
  t REAL, rule TEXT, quote TEXT, layer TEXT, status TEXT);              -- possible|confirmed|withdrawn
CREATE TABLE scorecards (call_id TEXT PRIMARY KEY, card TEXT, judge_version TEXT);
CREATE TABLE arena_runs (id TEXT PRIMARY KEY, bot_prompt TEXT, started_at TEXT,
  finished_at TEXT, summary TEXT);
```

## Failure handling

| Failure | Detect | Response |
|---------|--------|----------|
| Prisma stream drops | close / `error` | reconnect, resend last ~1 s of buffered audio |
| Prisma rejects config (1008) | first message `error` | fail call start clearly; it is a bug, do not retry |
| Timbre error mid-sentence | `error` / timeout | retry once → backup voice → pre-rendered "line kharab hai", end call |
| Evon slow/down | no first token in 2 s | one in-character filler line while retrying; then end politely, mark GPU unhealthy |
| 429 | status | backoff with jitter, admit fewer calls; never retry other 4xx |
| 402 credits | status | stop admitting, show "paused", alert operator |
| Browser disconnects | WS close | cancel tasks, close Gnani sockets, still judge and save |

## Security and privacy

Keys only in backend env; built JS checked for leaks before deploy. WS checks `Origin`. vLLM needs
an API key over HTTPS. Hidden state never leaves the backend. Trainee speech is untrusted LLM input:
it can change the conversation, never the rules. Per-IP limits and call caps. Audio streamed, never
stored. Anonymous browser ID + optional nickname only. `DELETE /me` removes everything.

## Deployment

| Piece | Host | Why |
|-------|------|-----|
| Web | Vercel | fast static hosting, HTTPS for the mic |
| Backend | Docker on a small VM in AWS Mumbai (or Fly/Railway India), Caddy for TLS | long-lived WebSockets, near users and Gnani |
| Evon | rented 80 GB GPU with vLLM, persistent disk for weights | needs ~60 GB GPU memory |
| DB | SQLite on the backend VM, daily backup | one server, small data |

Environments: **local** (fakes for Gnani, optional real GPU) · **staging** (real URL, `?staging`) ·
**live** (announced hours; GPU up 15 min before, warmed with one request, health-checked; stopped after).
Deployment traps: mic needs HTTPS; proxies kill idle sockets (raise Caddy timeouts, keep pings,
`x-idle-timeout-sec: 300`); HTTPS pages can only open `wss://`; first GPU call is slow (warm-up);
measure RTT from the chosen region to `api.vachana.ai` before committing.
