# KalPakka 📞 — *"Kal pakka, sir."*

**An AI borrower that loan-recovery agents practise on, by voice.**
Threaten him and the RBI rule you broke lights up, live. Earn his trust and he tells you the truth.
Built solo on **Gnani AI's Prisma, Evon v3.3 and Timbre** for the Great Indian AI Internship Challenge.

**📄 [1-page pitch](PITCH.md) · 🎬 [2-min demo](https://drive.google.com/file/d/198SIV1LC1_gVktGotXmKzbbPKEVJulv0/view?usp=sharing) · 💼 [LinkedIn post](https://lnkd.in/p/ewJqSpsz)**

> "Kal pakka" means *"tomorrow, for sure"*: the line every recovery agent in India hears a hundred times a day.

---

## 1. Why this exists

**The deadline.** RBI has issued revised directions on the conduct of regulated entities in loan recovery and the engagement of recovery agents, effective **1 January 2027** ([RBI press release, 6 Aug 2026](https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=63305)). Recovery agents must be trained and certified, coercive recovery practices are prohibited, contact hours are limited, and the rules extend to technology-based recovery tools.

**The gap.** Agents still learn the hardest part of the job, the angry, ashamed, lying or grieving borrower, on real people. Every mistake hurts someone and risks a violation. New collection voice bots have the same problem: there is nowhere safe to practise before going live.

**The flip (the jugaad).** Almost everyone uses Gnani's models to build an *agent*. KalPakka uses the same stack to build the **borrower**: a realistic, difficult customer that humans, and later bots, can practise against.

---

## 2. What it does

### Train mode (voice)
You call **Ramesh bhaiya**: 41, auto-rickshaw driver in Delhi, ₹12,500 overdue, 23 days past due, speaks Hinglish.

| Ramesh will… | How |
|---|---|
| Dodge | excuses like "UPI fail ho gaya", "salary late", "hospital mein hoon" |
| Set traps | his wife picks up · his father has just died · a late-hour call |
| Feel things | mood and trust shift every turn: irritated → angry → calm |
| Bargain | vague promises you have to pin down to an amount and a date |
| Open up | only if you earn trust: his auto's engine broke 3 weeks ago and it's still at the mistri's garage |

**Live RBI tripwire.** Threats, disclosing the debt to a third party, pressure during bereavement and late-hour calls are flagged on screen the moment they happen (*possible → confirmed / withdrawn*).

**Scorecard.** After the call: **Clarity 10 · Discovery & empathy 20 · Outcome 30 · Compliance 40**. Every mark quotes your exact words, the turn and the timestamp.

### Personas
- **Ramesh bhaiya**, Hinglish: fully tuned voice and behaviour
- **Lakshmi akka**, Tanglish, and **Debu da**, Benglish: in the app, using stock voices

### Arena (text mode built, not yet run at scale)
A pushy bot and a compliant bot each call the borrower; violation rates are reported as "x of y". This is the seed of a test bench for collection bots.

---

## 3. Architecture — *code directs, Evon acts*

```
Browser mic (AudioWorklet, 16 kHz, 1,024-byte frames, push-to-talk, barge-in)
   │  WebSocket
   ▼
FastAPI CallSession
   ├─► Gnani Prisma (streaming STT, hi-IN) ───────────── 0.34 s
   ├─► State engine: mood · trust · reveal gate · traps · promise check · score
   │     + RBI tripwire: lexicon (Roman/Devanagari/Tamil/Bengali) → state rules → Evon check
   ├─► Evon v3.3 Q8 (self-hosted, grammar-locked: speech → ###STATE### → JSON) ── 0.54 s
   ├─► Sentence chunker + human cadence (pace, breaths, reaction-first)
   └─► Gnani Timbre (Harshad, audio tags) → phone-line filter → speaker ── 0.23 s

After the call: Evon judge (thinking ON, JSON schema) → quotes verified in code
(fuzzy, negation-aware) → score summed in code → scorecard. SQLite stores calls.
```

**Why "code directs, Evon acts":** the language model is great at sounding human and bad at keeping rules. So the deterministic engine decides *what happens* (mood, trust, when the truth may be told, which trap fires, whether a promise is valid, the score), and Evon decides *how it sounds*. A guard checks each line before it's spoken.

**Stack:** Python 3.12, FastAPI, asyncio, pydantic v2, SQLite · Next.js + TypeScript · llama.cpp server on Modal · Gnani Prisma and Timbre APIs.

---

## 4. How it uses Gnani AI

| Model | Job in KalPakka |
|---|---|
| **Prisma** | Streams the agent's Hinglish to text in real time; amounts come back as digits, Hindi in Devanagari |
| **Evon v3.3** | Speaks as the borrower (thinking off) · plans each call once (thinking on) · judges the call (thinking on) · confirms tripwire flags · plays the Arena bots |
| **Timbre** | Gives Ramesh his voice: Harshad, Roman Hinglish, audio tags (`<sigh>`, `<pauses>`, `<shouting>`) |

No substitute model anywhere: every borrower line and every judgement is real Evon.

---

## 5. Engineering decisions

1. **Evon has no hosted API, so I self-hosted it.** Gnani's API serves Prisma and Timbre, not Evon. Evon is a 30B hybrid Mamba-Transformer MoE (~3B active), 63.5 GB in BF16. The official setup wants an 80 GB GPU (~$1.2–1.8/h), and my laptop has under 32 GB of RAM. I converted it myself to **GGUF Q8_0 (31.5 GB)** with llama.cpp and serve it with llama-server on **one L40S on Modal**, scale-to-zero and API-key protected. Download + conversion: **$0.32**. Infra spend: **₹0** (inside the free monthly credit).
2. **A grammar instead of hoping.** The quantized model broke the reply format (speech → `###STATE###` → JSON) on 16 of 30 turns. A **GBNF grammar sent with every borrower turn** makes any other shape impossible: **30/30**.
3. **Thinking where it pays.** Per-turn thinking is off (`chat_template_kwargs`): silence kills a phone call. With thinking on, the first word took **5.6 s**. So Evon thinks **once per call** to plan the borrower's day (25 plans: mood, morning, which excuses to try), and again **after** the call to judge it, where nobody is waiting.
4. **Spikes against the real APIs before building on them.** They caught two launch-killers: Prisma accepts exactly one `language_code` (our `hi-IN,en-IN` would have closed every call at connect with 1008), and Timbre ends each request with two messages (our client read one, so every second sentence would have been silent). Also: Timbre speed is valid only from 0.85 to 1.15.
5. **Making a TTS voice sound human.** Blind-tested six male voices → Harshad. Per-sentence speed (bursts 1.08, normal 1.05, emotional 0.92, ±3% seeded jitter), 150–450 ms breaths, a 1–2 word reaction sent first so audio starts sooner, and a 300–3400 Hz phone-line filter.
6. **Test-first, reviewed.** Independent reviews found real bugs: a judge answering "yes" to everything with the quote "haan" scored 80/100, and fuzzy matching accepted "payment nahi kar dunga" as "payment kar dunga". Each was fixed with a regression test.

---

## 6. Measured results

| What | Result |
|---|---|
| Prisma commit → transcript | p50 **0.344 s** (max 0.385), Hinglish correct |
| Timbre first audio | p50 **0.232 s** (10 requests) |
| Evon Q8 on L40S, time to first token | p50 **~0.50–0.54 s** warm · cold start 31–106 s · prefix cache ~2× |
| Evon throughput, 8 concurrent | 94–101 tokens/s total |
| Reply format | **14/30 → 30/30** with the grammar |
| Judge JSON | **9/10 → 10/10** · judge median ~72 s |
| Tripwire lexicon, 50 labelled Hinglish lines | precision **12/12**, recall 12/25 *(self-labelled, upper bound)* |
| State-rule trap eval | 80/80 violating runs flagged, 0/80 false flags *(upper bound)* |
| Tests | ~1,640 unit · ~190 contract · integration · web |
| Cost | Evon download + conversion $0.32 · infra ₹0 within free credit |

**Not yet measured:** end-to-end voice latency p50/p90 at scale, BF16 vs Q8 quality, judge agreement with human labels, real Tamil and Bengali audio, Arena at full scale.

---

## 7. The journey

1. **Research and plan (Nov 1–2).** Studied Gnani's business and the challenge. Chose the borrower flip and the RBI 2027 timing. Wrote a phased plan: spikes → text borrower → voice loop → compliance scoring → personas and Arena → deploy.
2. **Pure logic first, test-first (Nov 2–5).** Persona schema, state engine, sentence chunker (danda, abbreviations, tags), LLM client, `###STATE###` splitter, lexicon tripwire, judge scoring with quote verification. Then the voice loop, web app, Arena runner and tripwire layers, all built against fakes that follow Gnani's documented protocols.
3. **Budget wall #1: Evon has no API.** A 63.5 GB model, an 80 GB GPU in the official setup, and a laptop with under 32 GB of RAM. I refused substitute models and set a ₹1,500 cap.
4. **Budget wall #2: payments.** Indian cards were declined by the GPU provider until online use was enabled, so I ran the download and conversion on the free tier meanwhile.
5. **The hosting answer.** Downloaded Evon into Modal storage (5 min, $0.03), converted it to Q8, and served it on one L40S that scales to zero.
6. **Reality check.** The spikes against the real APIs caught the two launch-killing bugs above.
7. **First contact with Evon.** Fast but unruly: format 14/30, judge 9/10, copied excuse lines, Devanagari slips, a feminine verb, once "the engine is fixed" (a spoiler). Fixes: the grammar (30/30), schema + budget + timeout for the judge (10/10), Roman-only output for Ramesh, masculine-form checks, a truth guard, repeat blocking, excuse ids instead of quotable sentences.
8. **Making him human.** A voice bible for Ramesh (how a Delhi auto driver really talks), the voice blind test, pacing, breaths and the phone-line filter.
9. **Regression and recovery on the last day.** The humanism features made replies slower and repetitive. Fixed by keeping the prompt stable per call, blocking 5-word repeats, and retuning trust so a respectful agent earns the reveal.
10. **Launch.** Real recorded calls, a 60 s social cut and a 2-minute demo.

---

## 8. Honest limits
Evon runs quantized (Q8; the BF16 comparison wasn't run) · one fully tuned persona · the TTS voice is still slightly synthetic and Ramesh's Hindi sometimes slips · eval sets are small and self-written · the judge isn't yet calibrated against human labels · one concurrent call per GPU · the live demo runs only in announced windows (the GPU scales to zero otherwise).

**Not perfect, much improved in ten days, and with guidance it can be great.**

## 9. What's next
- **Arena against real collection bots**: prove a bot is compliant before 1 January 2027
- BF16 vs Q8 A/B, and end-to-end latency at scale
- Judge calibration on hand-labelled calls
- Consented voice cloning for realism, real Tamil and Bengali audio
- An audit mode for recorded calls
- More personas: gig worker, small shop owner, salaried defaulter

---

## 10. Run it locally

**Requirements:** Python 3.12 + [uv](https://github.com/astral-sh/uv), Node 20+, a Gnani API key, and a Modal account for Evon.

```bash
# 1. Evon on Modal (needs EVON_API_KEY set as the Modal secret "evon-api")
export MODAL_IMAGE_BUILDER_VERSION=2025.06
uv run --script scripts/spikes/evon_modal/serve_q8.py --minutes 60   # prints the base URL

# 2. Backend
cd backend
cp ../.env.example ../.env    # fill in GNANI_API_KEY, LLM_BASE_URL, LLM_API_KEY, LLM_MODEL=evon
LIVE_ALWAYS_OPEN=true uv run --frozen uvicorn kalpakka.server:create_production_app --factory --host 127.0.0.1 --port 8000

# 3. Web
cd web
NEXT_PUBLIC_BACKEND_WS=ws://localhost:8000/call NEXT_PUBLIC_BACKEND_HTTP=http://localhost:8000 npx next dev
# open http://localhost:3000

# Text-only chat with Ramesh
cd backend && uv run python -m kalpakka.cli talk ramesh
```

## 11. Synthetic data
SwiftFin is fictional. Every borrower, loan and number is synthetic; no real people, lenders or loans are used, and **no audio is stored**.

---
*Built solo by **Vahant Sharma**, Bennett University · [LinkedIn](https://linkedin.com/in/vahant-sharma) · [GitHub](https://github.com/VahantSharma)*
