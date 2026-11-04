# 04 · Product specification

## Problem

Recovery agents handle the most emotionally difficult calls in lending (disputes, hardship, anger,
evasion), in many languages, under volume pressure, and mostly learn on real borrowers. From
Jan 1 2027 RBI requires them to be trained and certified before contact and prohibits threats, abuse,
third-party disclosure, misleading statements and pressure during sensitive occasions. Lenders have
no safe, realistic, multilingual way to practise with objective, evidence-based feedback; voice-AI
teams have no repeatable way to test collections bots against difficult borrowers before launch.

**How might we** let any recovery agent, or any collections bot, practise against a realistic,
emotional, code-mixed Indian borrower and get scored fairly against RBI's rules, in minutes, at scale?

## Users

| User | Example | Needs | Mode |
|------|---------|-------|------|
| **Trainee agent** (primary) | Pooja, 22, new tele-caller in Noida, Hindi + English | Safe place to fail; hear a good answer; a score to improve | Train |
| **Public player** (hackathon audience) | Clicked the LinkedIn link | A fun 3-minute challenge with a shareable score | Train |
| **Voice-bot engineer** | Gnani engineer shipping a collections bot | Find violations before a real borrower does | Arena |
| Team lead / trainer | Runs a 30-person team | Assign scenarios, see who struggles | Train dashboard (stretch) |
| Compliance officer | At the lender | One rubric for training and audits | Audit (stretch) |

## The trainee's journey

1. **Pick a borrower.** Ramesh bhaiya (Hinglish, medium), Lakshmi akka (Tanglish, hard), Debu da
   (Benglish, medium). Easy mode has no traps.
2. **Briefing** (what a real agent's screen shows): name, lender SwiftFin, product, overdue ₹12,500,
   23 DPD, "promised twice, did not pay", language, simulated time of day, a 3-line rules reminder.
   Never shows the hidden truth, mood numbers, or traps.
3. **Mic check** — permission asked here, not mid-call.
4. **The call** — phone-style screen, timer, hold-to-talk (button or spacebar), live transcript of both
   sides, a flag strip, optional coarse mood label ("irritated", "calmer"). Either side can hang up.
5. **Scorecard** — Outcome, Compliance, Discovery & empathy, Clarity; every mark tied to a quote and
   timestamp; best moment; worst line with a better version (playable, optionally in the trainee's
   own cloned voice); timeline with flags.
6. **Share and retry** — share card ("Recovered ₹4,000 from Ramesh · 0 RBI violations · beat the
   excuse: salary on the 7th"). Each retry draws different traps.

Difficulty: Easy (calm, no traps, reveal at trust ≥ reveal_trust − 20, 40 for Ramesh) · Medium
(irritated, one random trap, trust ≥ reveal_trust, 60) · Hard (angry, two traps,
trust ≥ reveal_trust + 10, 70, **and** an open question about their situation).

## User stories (acceptance in brackets)

- US-1 trainee: pick borrower + language and start in one click [borrower answers within 2 s]
- US-2 trainee: speak natural Hinglish and be understood [amounts, dates, names right in most turns]
- US-3 trainee: hear the borrower react to *how* I speak [polite turns raise trust; threats audibly raise anger]
- US-4 trainee: be warned the moment I cross a rule [flag within ~1 s of the segment closing]
- US-5 trainee: discover the real situation by asking well [truth only after enough trust]
- US-6 trainee: a scorecard where every mark has a quote [every check shows line and time]
- US-7 trainee: hear my worst line said the right way [rewrite plays, optionally in my voice]
- US-8 player: share my score as an image [share card after the call]
- US-9 bot engineer: run my bot against all personas and traps [violation rates per scenario]
- US-10 trainee: interrupt the borrower [pressing talk silences the borrower immediately]

## Functional requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-1 | Choose one of three personas (Hinglish, Tanglish, Benglish) and a difficulty | Must |
| FR-2 | Pre-call briefing with visible facts; secret state hidden | Must |
| FR-3 | Push-to-talk (button or spacebar); audio streams to Prisma only while held | Must |
| FR-4 | Borrower replies by voice through Timbre v3, streaming, with mood-driven tags | Must |
| FR-5 | Facts, mood, trust, traps, reveal gate, promise validity held in code, updated each turn | Must |
| FR-6 | Talk while borrower speaks → borrower stops at once, history corrected | Must |
| FR-7 | Lexicon tripwire flags harsh practices per segment, shown in the call UI | Must |
| FR-8 | Post-call judge scorecard; every check cites a quote verified against the transcript | Must |
| FR-9 | Worst line + better version, playable as audio | Must |
| FR-10 | Arena runs a bot prompt against all personas × traps in text mode and reports | Must |
| FR-11 | Per-stage timestamps every turn; a page shows p50/p95 | Must |
| FR-12 | Shareable score card image | Should |
| FR-13 | LLM tripwire confirms / withdraws / adds flags in the background | Should |
| FR-14 | Leaderboard by nickname | Should |
| FR-15 | Consented voice clone replay of the better line | Could |
| FR-16 | Arena audio mode through an 8 kHz µ-law phone-line loop | Could |
| FR-17 | Audit mode: score an uploaded synthetic recording via Prisma batch | Could |
| FR-18 | Hands-free VAD turn-taking and barge-in | Could |
| FR-19 | Phone mode via a telephony provider | Could |

## Non-functional requirements (all targets until measured)

| ID | Quality | Target |
|----|---------|--------|
| NFR-1 | Turn latency | p50 ≈ 1.2 s, p95 < 2.5 s, button release → first borrower audio |
| NFR-2 | Call start | borrower answers within 2 s (pre-rendered pickup line) |
| NFR-3 | Recognition | amounts and dates right in ≥ 9 of 10 test turns |
| NFR-4 | Live flag delay | lexicon flag within ~1 s of segment close |
| NFR-5 | Capacity | as many simultaneous calls as Gnani concurrency allows; the rest queue with a position |
| NFR-6 | Availability | working in announced live hours; recorded call + clear message outside them |
| NFR-7 | Cost | caps on call length, calls/day, calls/IP; credits per call measured and logged |
| NFR-8 | Privacy | no raw audio stored; anonymous ID; delete on request |
| NFR-9 | Security | keys server-side only; vLLM behind an API key; WS Origin checked |
| NFR-10 | Compatibility | Chrome desktop + Android; iPhone Safari with resampling fallback |
| NFR-11 | Accessibility | spacebar + button; transcript as captions |
| NFR-12 | Maintainability | personas, traps, patterns in data files |
| NFR-13 | Honesty | UI says the borrower is an AI; limitations in README |

## Scope

**In for the Nov 8 build freeze:** FR-1 to FR-14 (Must + Should); FR-15 and FR-16 if the latency budget allows. Three personas. Browser only.
**Out:** real borrower data, real lender names, telephony to anyone except Vahant's own verified
number, multi-tenant accounts, payments, certification claims.

## What KalPakka is not

Not a real collections tool (never calls real borrowers). Not legal advice. Not a replacement for
IIBF certification — it is practice that makes certified training stick.

## Success signals

Product: a trainee's compliance score and promise rate improve over repeated calls; Arena violation
rates clearly separate a bad bot prompt from a good one. System: p50 turn ≈ 1.2 s, no dropped calls,
judge agreement high enough to trust. Hackathon: people finish calls, share scores, comment.
