# KalPakka: the AI borrower that gets loan-recovery agents ready for 1 January 2027

**Built solo in 7 days by Vahant Sharma (Bennett University) on Gnani AI's Prisma, Evon v3.3 and Timbre.**

🎬 [2-min demo](https://drive.google.com/file/d/198SIV1LC1_gVktGotXmKzbbPKEVJulv0/view?usp=sharing) · 💼 [LinkedIn post](https://lnkd.in/p/ewJqSpsz) · 📖 [Full README](https://github.com/VahantSharma/kalpakka/blob/main/README.md)

## The problem
From **1 January 2027**, RBI's revised loan-recovery directions take effect: recovery agents must be trained and certified, coercive practices are prohibited, contact hours are limited, and the rules extend to **technology-based recovery tools**. Today agents still learn on real, stressed borrowers, where one wrong line (a threat, a late-night call, pushing someone who is grieving) hurts a person and is now a violation. New collection bots have the same gap: nowhere safe to practise.

## The idea: everyone builds the agent. I built the borrower.
You call **Ramesh bhaiya**, a 41-year-old Delhi auto driver, ₹12,500 overdue, in Hinglish. He dodges, gets angry, bargains, and sets traps: his wife picks up, his father has just died, the call comes late at night. He tells the truth only if you earn his trust. Every RBI rule you break is flagged **live**. Afterwards a scorecard (Clarity · Discovery & empathy · Outcome · Compliance) **quotes your exact words** for every mark.

## Gnani models, all three doing real work
- **Prisma**: streaming Hinglish speech-to-text (commit to transcript p50 0.34 s).
- **Evon v3.3**: plays the borrower (thinking off, first token p50 ~0.54 s), plans each call, judges it afterwards (thinking on) and confirms tripwire flags.
- **Timbre**: Ramesh's voice (Harshad), with sighs, pauses and per-sentence pacing (first audio p50 0.23 s).

## The Jugaad
Evon has no hosted API and I had no budget. So I **self-hosted it**: 63.5 GB BF16 converted to **GGUF Q8_0 (31.5 GB)** with llama.cpp and served on **one L40S on Modal, scale-to-zero**. **₹0 out of pocket so far.** Evon first broke its reply format 16 times in 30; a **GBNF grammar** made any other shape impossible: **14/30 to 30/30**.

## What makes it different
**Code directs, Evon acts.** A deterministic state engine owns mood, trust, the reveal gate, traps, promise validity and the score. Evon only performs the line, so he never caves early, forgets a fact or leaks his secret. A three-layer **RBI tripwire** (lexicon, state rules, Evon check) runs live, and judge quotes are verified against the transcript in code before they count.

## Measured, not claimed
Reply format 14/30 to 30/30 · judge JSON 9/10 to 10/10 · tripwire lexicon precision 12/12 on 50 labelled lines · two launch-blocking API bugs (Prisma, Timbre) caught by spikes before any real call.

## Honest status
Not perfect yet: Ramesh's Hindi still slips, one persona is fully tuned, it runs from my laptop, and the model is quantized. But I learned this stack from scratch and went from idea to a working voice simulator with a live compliance tripwire in 7 days. With guidance from the people who build these models, it can become a real product.

## Next: Arena
The same borrowers become a test bench for collection bots: Arena (text mode built) runs a bot through hundreds of calls with traps, moods and three languages, every call checked by the tripwire, so bots are proven compliant **before** 1 January 2027.
