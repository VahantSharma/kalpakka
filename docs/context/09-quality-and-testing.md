# 09 · Quality and testing

A voice system has two kinds of correctness: the **software** must behave predictably (tests), and
the **AI** must behave well often enough (evals with measured rates). They need different tools.

## Test pyramid

| Level | What | How | When |
|-------|------|-----|------|
| Unit | chunker, tag policing, `###STATE###` splitter, state update rules, reveal gate, promise validation, trap scheduling, tripwire patterns, score arithmetic, quote verification, frame packing, resampler | pytest, fixed inputs → expected outputs | every change (`make check`), seconds |
| Contract | our Prisma/Timbre/vLLM clients against the documented message shapes | fake local WebSocket/HTTP servers in `tests/contract/fakes/` | every change |
| Integration | a whole call with fakes and a scripted agent | recorded audio streamed at real-time pace | before each deploy (`make test-integration`) |
| AI behaviour | persona realism, trap handling, judge agreement, tripwire precision/recall, speech round trip | `evals/` sets, pass rates over N runs | after any prompt/persona/pattern change |
| End-to-end | the real thing on real devices | Vahant + a friend on Chrome desktop, Android, iPhone | daily during the build; before launch |
| Load | 5–10 simultaneous calls | script streaming recorded audio | once before launch, after big changes |

## Eval sets (in `evals/`, results stored as JSON with prompt + persona version)

- **Persona realism** — 20 scripted agent turns per persona × 10 runs. Measure: caved before the
  gate, leaked the secret, broke language style, mentioned being an AI, used a non-official tag,
  over-long reply. Target: 0 leaks, 0 character breaks, caving < 1 in 10 runs.
- **Trap handling** — per trap, one correct-behaviour script and one violating script. Violating:
  flagged by state rules **and** judge every time. Correct: never flagged.
- **Tripwire precision/recall** — 50 labelled Hinglish utterances (25 violations, 25 tricky
  non-violations). Report for lexicon alone and lexicon + LLM.
- **Judge calibration** — 15–30 practice calls with varied behaviour, labelled by Vahant **before**
  seeing the judge's answer (manual step). Percent agreement and Cohen's kappa per check.
- **Speech round trip** — 20 agent lines with amounts, dates, names, recorded by two people, through
  Prisma `transcribe` with and without keyterms. Report WER (`jiwer`) and critical errors.

Regression rule: prompts, personas and patterns are code. Any change re-runs the persona, trap and
tripwire sets before it ships. Keep each run so the week shows improvement — that is interview material.

## Coverage expectations

Unit coverage on `borrower_state`, `chunker`, `tripwire`, `judge` (scoring + verification), `brain`
(splitter + prompt builder) ≥ 90% lines. Do not chase coverage in `session` wiring; integration tests
cover it. Never write a test that cannot fail.

## Definition of done (hackathon build)

- [ ] All unit and contract tests pass (`make check`).
- [ ] Three personas complete calls end to end on Chrome desktop and Android.
- [ ] Median turn latency measured and published.
- [ ] Trap tests: every violating script flagged, no correct script flagged.
- [ ] Judge agreement measured on ≥ 15 hand-labelled calls.
- [ ] Arena report for Bot A vs Bot B generated.
- [ ] README lists architecture, numbers, and honest limitations.

## Definition of done (any sub-phase)

Exit criteria met with pasted evidence · `make check` green · reviewer findings fixed or deferred
with reason · sub-phase log complete · STATUS updated · commits made and hashes logged · no AI attribution.
