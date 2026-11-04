# 10 · Decision log (D1–D20)

Decisions made during design, before code. New decisions go in `docs/decisions/ADR-*.md`;
reversing one of these needs an ADR that says which D it supersedes and the measured reason.

| # | Decision | Chosen | Rejected | Why | Cost |
|---|----------|--------|----------|-----|------|
| D1 | Product angle | AI plays the **borrower**; humans and bots play the agent | another agent-side bot | unclaimed; Gnani's biggest vertical; one engine serves training and bot testing | domain must be learned |
| D2 | Pipeline | cascade STT → LLM → TTS | speech-to-speech | every word inspected and scored; Warp not in the challenge | higher latency; tone lost between stages |
| D3 | Turn-taking | push-to-talk | hands-free VAD | perfect endpointing, no echo, fewer credits, simple barge-in | less natural; hands-free is stretch |
| D4 | STT output | `transcribe` | `codemix`, `verbatim` | amounts and dates as digits for checks | regex must cover Devanagari + Latin |
| D5 | LLM | Evon v3.3 self-hosted | Gemini/GPT API | genuine Gnani use; Indic-native; shows infra skill | GPU cost, setup risk, cold starts |
| D6 | Serving | vLLM | Transformers, llama.cpp | batching, paged KV, OpenAI API, documented by the card | needs 80 GB GPU |
| D7 | Borrower control | state in code; LLM acts from a turn brief | one big persona prompt | no caving, leaks or forgotten facts; fair traps; grounded judge | more code |
| D8 | LLM output | speech first, then `###STATE###` JSON | JSON first | speak sooner; broken JSON never breaks the turn | custom parser |
| D9 | TTS granularity | one request per sentence | one per reply | earlier first audio; tag scope per sentence; clean interrupts | more requests; fragment merging |
| D10 | Borrower voices | stock Timbre v3 voices | cloned voices | tags documented for Timbre voices | fewer options |
| D11 | Guardrails | lexicon + LLM + state rules; judge final | one layer | instant and context-aware together | more moving parts |
| D12 | Scoring | LLM answers yes/no with quotes; code verifies and sums | LLM 1–10 score | consistent, auditable, hallucination-resistant | rubric design effort |
| D13 | Browser transport | one WebSocket per call | WebRTC | simple, enough for push-to-talk | weaker on very poor networks |
| D14 | Backend language | Python (FastAPI, asyncio) | Node.js | model/eval tooling is Python-first; one language for server, judge, Arena | discipline to stay non-blocking |
| D15 | Orchestration | hand-built | Pipecat / LiveKit Agents | full control of state, barge-in history, scoring | more code; switch if the loop is not working by Nov 3 evening |
| D16 | Storage | SQLite, no audio | Postgres; recordings | one server; privacy by design | not multi-server; no replay |
| D17 | Personas | YAML files | hard-coded | editable, versioned, no code change per tweak | schema validation needed |
| D18 | Channel | browser first | phone first | faster; no telephony cost or regulation | less like a real call |
| D19 | Cloning scope | trainee's own voice, own improved line, spoken consent | any text in any voice | removes most misuse | narrower feature |
| D20 | Availability | announced live hours with a queue | always on | bounded GPU and credit cost; scarcity can help engagement | visitors outside hours see a recording |
