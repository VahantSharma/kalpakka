# KalPakka

**An AI borrower that loan-recovery agents and collections voice bots practise on.**

You play the recovery agent. Ramesh bhaiya — an auto driver in Delhi who owes ₹12,500 — answers in
Hinglish, sighs, coughs, loses his temper, and hides what really happened. Cross an RBI line (threats,
telling his wife about the loan, pushing after a bereavement, calling after 7 PM) and it is flagged
live. Every call ends in a scorecard where each mark quotes the transcript.

From **January 1, 2027**, RBI requires recovery agents to be trained and certified before they contact
borrowers. KalPakka is a safe place to practise, and a test harness for collections bots (Arena).

Built on Gnani AI's models: **Prisma v3** (speech-to-text), **Evon v3.3** (self-hosted LLM: the borrower
and the judge), **Timbre v3** (text-to-speech with audio tags).

> Status: in development for the Great Indian AI Internship Challenge (Nov 2026).
> Current progress: [`docs/STATUS.md`](docs/STATUS.md). Plan: [`docs/plan/roadmap.md`](docs/plan/roadmap.md).

## Modes

- **Train** — a person vs the AI borrower, by voice, with live flags and an evidence-backed scorecard.
- **Arena** — a collections-bot prompt vs the borrower across personas, traps and seeds; a violation report.
- **Audit** — the same rubric applied to a recorded call (planned).

## Documentation

- [`docs/context/`](docs/context/00-reading-order.md) — domain, Gnani stack, product spec, architecture,
  borrower engine, tripwire and judge, voice engineering notes, testing, decisions.
- [`docs/phases/`](docs/phases/README.md) — build log for every sub-phase with evidence.
- [`docs/checkpoints/`](docs/checkpoints/CHECKPOINTS.md) — verified milestones.

## Running locally

Requirements: [uv](https://docs.astral.sh/uv/), GNU make, bash (Git Bash on Windows).

```bash
cd backend && uv sync          # installs Python 3.12 (from .python-version) and dependencies
cd .. && make check            # ruff + mypy --strict + unit + contract tests
make check-fast                # ruff + unit tests only; offline, about 2 s
```

```bash
make test-integration          # a whole call through the app against fake Prisma/Timbre/LLM servers
make dev                       # backend on http://127.0.0.1:8000 (needs the variables below)
```

Configuration comes from environment variables (or the repo-root `.env`, never committed):
`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` (any OpenAI-compatible server hosting Evon v3.3) and
`GNANI_API_KEY`. Optional: `SIM_DATE` (the story's "today", default 2026-10-28),
`MAX_CONCURRENT_CALLS`, `MAX_QUEUE`, `CALLS_PER_DAY`, `CALLS_PER_IP_PER_DAY`, `LIVE_HOURS`
(IST, e.g. `10:45-22:00`), `LIVE_ALWAYS_OPEN`, `ALLOWED_ORIGINS`.

## How a call works (backend)

One WebSocket per call (`/call`). The browser sends 16 kHz PCM while talk is held. The backend
streams it to **Prisma** in 1,024-byte frames at real-time pace and commits on release. It builds
the turn brief from the borrower's state in code: mood, trust, reveal gate, traps. **Evon** streams
the reply, and it is cut into sentences. **Timbre** speaks each sentence while the next one is still
being written. Barge-in stops the reply, and history keeps only the sentences the trainee heard.
Every turn records ten timestamps; `GET /metrics/latency` reports p50/p95 per stage.

Honest status: the voice loop is built and tested against **fakes** that follow Gnani's documented
protocol. No measured latency from the real services exists yet. Every Gnani fact the docs leave
open is a single constant marked `UNVERIFIED` until the spikes run (see the phase logs).

## Data and privacy

Synthetic data only (fictional lender SwiftFin, invented borrowers). Audio is streamed, never stored.

## Author

Vahant Sharma — [GitHub](https://github.com/VahantSharma) · [LinkedIn](https://linkedin.com/in/vahant-sharma)
