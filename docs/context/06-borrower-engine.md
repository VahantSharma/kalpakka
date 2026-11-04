# 06 · Borrower engine

The heart of KalPakka and its most original part. Rule: **code is the director, Evon is the actor.**
Code decides facts, feelings (as numbers), secrets, traps and outcomes; Evon turns them into
believable speech. LLMs are trained to be helpful and open, so a plain persona prompt confesses on
the second polite question. The engine stops that.

## Persona file (`content/personas/<id>.yaml`)

```yaml
id: ramesh
version: 1
display_name: "Ramesh bhaiya"
difficulty_default: medium
profile: {age: 41, occupation: "auto-rickshaw driver", city: "Delhi"}
speech:
  stt_language_code: "hi-IN,en-IN"
  keyterms: ["Ramesh", "SwiftFin", "EMI", "NACH", "CIBIL", "bounce charge", "part payment"]
  tts_voice: "Devansh"          # confirm by audition (manual step)
  tts_language: "hi-IN"         # spike: hi-IN vs hi-en vs auto
  style: "Delhi Hinglish; Hindi in Devanagari, English words in Latin"
loan:
  lender: "SwiftFin"            # fictional
  product: "two-wheeler loan"
  emi: 2500
  overdue_amount: 12500
  dpd: 23
  past_promises: 2              # shown in the briefing
hidden:
  truth: "auto was off the road 3 weeks for engine repair; earnings stopped"
  can_pay_now: 4000
  can_pay_rest_by: "2026-11-20"
  reveal_trust: 60
excuses: ["salary saat tareekh ko", "UPI fail ho gaya", "main hospital mein hoon", "kal pakka"]
temperament: {anger_sensitivity: 1.2, trust_gain: 0.8, starting_mood: 55, starting_trust: 25}
traps_allowed: ["bereavement", "third_party_wife", "late_clock"]
lines:
  pickup: "Haan, hello? Kaun bol raha hai?"
  silence: "Hello? Sun rahe ho?"
  hangup_angry: "Mujhe aur baat nahi karni. <sigh>"
```

`personas` exposes two views: the **public card** (for `GET /personas` and the briefing) and the
**full persona** (backend only). A test must prove `hidden` never appears in the public card.

### The three launch personas

| Borrower | Language | Voice candidate | Surface excuse | Hidden truth | Right move |
|----------|----------|----------------|----------------|--------------|------------|
| Ramesh bhaiya, auto driver, Delhi, owes ₹12,500 at 23 DPD | Hinglish | Devansh | "Salary 7 tareekh ko aayegi, kal pakka" | Auto off the road 3 weeks; can pay ₹4,000 now, rest by Nov 20 | Firm, polite, get a dated part-payment |
| Lakshmi akka, saree shop, Chennai | Tanglish | Lakshmi or Kalpana | "Festival-ku shop close, next week pay panren" | A disputed bounce charge she complained about (pending grievance) | Log the grievance; pause recovery until resolved |
| Debu da, IT employee, Kolkata | Benglish | Shubham | "Next week-e pay kore debo, promise" | Laid off last month | Spot hardship; offer restructuring options |

## State (owned by code, updated every turn)

| Variable | Range | Meaning |
|----------|-------|---------|
| `mood` | 0–100 | anger/defensiveness; 0 calm, 100 about to hang up |
| `trust` | 0–100 | how fair the borrower thinks this agent is |
| `revealed` | bool | has the hidden truth come out |
| `traps` | list of (name, turn) | which trap fires on which turn |
| `trap_active_until` | turn | window in which a trap's rule applies |
| `promise` | amount, date, valid | latest commitment and whether it is believable |
| `silence_strikes` | 0–2 | consecutive silent agent turns |
| `interruptions` | count | times the agent cut the borrower off |
| `sim_clock` | time | simulated time of day for the call |
| `used_excuses` | list | so the borrower does not repeat itself |
| `call_over` | bool, reason | whether and why the borrower ends the call |

Derived labels: mood 0–30 calm · 31–60 irritated · 61–85 angry · 86–100 furious.

## Per-turn loop

1. Agent text arrives (from Prisma, or from the Arena bot).
2. Tripwire state rules + lexicon run on it (flags).
3. Code builds the **turn brief** from state.
4. Evon streams: spoken reply, then `###STATE###`, then JSON.
5. Chunker sends sentences to Timbre as they complete.
6. When the reply ends, code parses the JSON suggestion and applies the update rules.
7. New state saved with the turn.

## Evon output contract

```
<spoken reply, 1–3 short sentences, numbers as spoken words, ≤ 2 official audio tags>
###STATE###
{"mood_delta": int, "trust_delta": int, "promise": null | {"amount": int, "date": "YYYY-MM-DD"},
 "wants_to_hang_up": bool, "agent_was_respectful": bool}
```

Speech first so the borrower starts talking sooner and a broken JSON never breaks the spoken turn.
If the delimiter or JSON is missing or invalid: speak what came, treat the suggestion as all-zero,
log a `state_parse_error` metric. The splitter must handle the delimiter arriving split across stream
chunks. Thinking off; `max_tokens` 150–180; persona prompt identical every turn (prefix cache).

## Update rules

1. **Evon's suggestion:** clamp each delta to ±20, multiply by temperament (`anger_sensitivity` on
   positive mood deltas, `trust_gain` on positive trust deltas).
2. **Hard rules (override the model):** a confirmed threat or abuse flag → mood +25, trust −20.
   Interruption → mood +5. Silent agent turn → silence strike. Respectful specific turn (correct
   amount named, open question asked, problem acknowledged) → trust +5 to +10.
3. Clamp to 0–100. Mood drifts 5 points toward `starting_mood` each turn. Recompute labels.

## Reveal gate

Truth may come out only when `trust ≥ reveal_trust` (Easy 40, Medium 60, Hard 70) — and on Hard
only after the agent has asked an open question about the borrower's situation. Below the gate the
brief says: *"You may NOT reveal your real situation yet; use an excuse you have not used."* At the
gate: *"If the caller asks about your situation, you may tell the truth now."* The reveal turn
allows `<sniffles>`, `<slow>`, `<pauses>` and speed 0.92. Code records the turn the gate opened so
the judge knows when the truth became discoverable.

## Traps

Drawn per call from `traps_allowed` with a seeded RNG; each scheduled on a random turn 3–6.

| Trap | Borrower does | Correct agent behaviour | Violation if |
|------|---------------|------------------------|--------------|
| `bereavement` | "Papa ka kal dehant ho gaya…" | condolence; offer to call back; no payment talk | payment, amount or date mentioned in the next two turns |
| `third_party_wife` | next turn answered by the wife: "Main unki wife bol rahi hoon, woh bahar gaye hain" | say only that you will call back | loan, amount, lender or overdue status mentioned |
| `late_clock` | briefing clock reads 7:20 PM | do not proceed with recovery; offer to call in hours | agent proceeds with recovery talk |
| `disputed_charge` (Lakshmi) | "Maine bounce charge ke against complaint daali hai" | log the grievance; pause recovery | agent keeps demanding payment |

The trap fires through the brief ("THIS TURN: tell the caller, in your own words, that your father
passed away yesterday"), so wording varies. `trap_active_until` tells tripwire and judge when the
rule applies.

## Promises

A promise from Evon's JSON is **valid** only if: amount ≤ `can_pay_now` (or a plan matching
`can_pay_rest_by`); the date is real and inside the plan window; trust is above a minimum (an
untrusting borrower says "haan haan, kal pakka" just to end the call). Invalid promises are still
spoken (realistic) but stored `valid: false`; the next brief tells the borrower the commitment was
not serious. The scorecard rewards realistic promises and calls out accepted "kal pakka" promises.

## Hang-up conditions

Mood ≥ 95 two turns in a row · two silence strikes · after a valid promise and a closing exchange ·
bereavement trap and the agent pushes twice · time cap.

## Mood shapes the voice

| Band | Allowed tags | Speed |
|------|-------------|-------|
| calm 0–30 | `<pauses>`, occasionally `<laugh>` | 1.00 |
| irritated 31–60 | `<sigh>`, `<clears throat>`, `<exhales>` | 1.00 |
| angry 61–85 | `<sigh>`, `<shouting>` | 1.08 |
| furious 86–100 | `<shouting>` | 1.12 |
| evasive excuse | `<coughs>` (sick excuse), nervous `<giggle>` | 1.00 |
| truth revealed | `<sniffles>`, `<slow>`, `<pauses>` | 0.92 |

At most two tags per turn; the chunker strips any tag not allowed this turn.

## Turn brief (rebuilt every turn)

```
TURN BRIEF (turn 5)
Mood: 72/100 (angry). Trust: 30/100.
You may NOT reveal your real situation yet. Use an excuse you have not used.
Allowed audio tags this turn: <sigh>, <shouting>. Use at most two.
THIS TURN: tell the caller, in your own words, that your father passed away yesterday.
Keep it to 1-3 short sentences. Numbers as spoken words.
```

Prompt order for prefix caching: [fixed system prompt: role, never say you are an AI, language
style, output contract] → [persona block, fixed per call] → [conversation history] → [turn brief as
the last user-side message]. Only the tail changes.

## Behaviour the persona must never show

Mention being an AI or a model · confess before the gate · forget the amount or a prior promise ·
switch out of its language style · use a tag outside the official 13 · speak more than ~3 sentences.
These are measured by the persona realism eval (see `09-quality-and-testing.md`).

## Reproducibility

Every call stores seed, persona version and prompt version. Replaying a seed reproduces the trap
schedule and excuse choices; Arena results are comparable across prompt versions.
