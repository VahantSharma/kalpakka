# KalPakka — the borrower, not the agent

**An AI borrower that loan-recovery agents practise on, by voice. Built solo by Vahant Sharma (Bennett University) on Gnani AI's Prisma, Evon v3.3 and Timbre.**
🎬 [2-min demo](https://drive.google.com/file/d/198SIV1LC1_gVktGotXmKzbbPKEVJulv0/view?usp=sharing) · 💼 [LinkedIn post](https://lnkd.in/p/ewJqSpsz) · 📖 [Full README](README.md)

### Problem
From **1 January 2027**, RBI's revised directions on loan recovery take effect: recovery agents must be trained and certified, coercive practices are prohibited, contact hours are limited, and the rules extend to technology-based recovery tools. Yet agents still learn on real, stressed borrowers, where every mistake hurts a person and risks a violation. New collection bots have the same problem: nowhere safe to practise.

### Solution
Every other entry builds the agent. **KalPakka builds the borrower.** You call **Ramesh bhaiya**, a 41-year-old Delhi auto driver, ₹12,500 overdue, in Hinglish. He dodges, gets angry, sets traps (his wife picks up, his father has just died, a late-hour call) and tells the truth only if you earn his trust. Every RBI rule you break is **flagged live**. After the call, a **scorecard** (Clarity · Discovery & empathy · Outcome · Compliance) quotes your exact words for every mark.

### How it uses Gnani
- **Prisma**: streaming Hinglish speech-to-text (commit → transcript p50 **0.34 s**).
- **Evon v3.3**: plays the borrower (thinking off, first token p50 **~0.54 s**), plans each call once, judges the call afterwards (thinking on), and confirms tripwire flags. Evon has no hosted API, so it's **self-hosted**: 63.5 GB → GGUF Q8_0 (31.5 GB) on one L40S on Modal, scale-to-zero. **Infra spend: ₹0.**
- **Timbre**: Ramesh's voice (Harshad), with sighs, pauses and per-sentence pacing (first audio p50 **0.23 s**).

### What's different
**Code directs, Evon acts.** A deterministic state engine owns mood, trust, the reveal gate, traps, promise validity and the score; Evon only performs the line. A three-layer RBI tripwire (lexicon → state rules → Evon check) runs live. Judge quotes are verified against the transcript in code before they count.

### Measured
Reply format **14/30 → 30/30** with a GBNF grammar · judge JSON **9/10 → 10/10** · tripwire lexicon precision **12/12** on 50 labelled lines · two launch-blocking API bugs caught by spikes before any real call.

### Next: Arena
The same borrowers become a **test bench for collection bots**: Arena (text mode built) puts a bot on hundreds of calls with traps, moods and three languages, every call checked by the tripwire, so bots can be proven compliant before 1 January 2027.

**Honest:** not perfect yet. Ramesh's Hindi still slips, one persona is fully tuned, and the Evon model is quantized. It improved a lot in ten days, and with guidance it can be great.
