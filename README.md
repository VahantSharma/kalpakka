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

Configuration comes from environment variables (or the repo-root `.env`, never committed):
`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` (any OpenAI-compatible server hosting Evon v3.3) and
`GNANI_API_KEY`. The web app and `make dev` arrive with the voice loop (Phase 2).

## Data and privacy

Synthetic data only (fictional lender SwiftFin, invented borrowers). Audio is streamed, never stored.

## Author

Vahant Sharma — [GitHub](https://github.com/VahantSharma) · [LinkedIn](https://linkedin.com/in/vahant-sharma)
