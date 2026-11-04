# Open questions

Unknowns that code must not assume. Each is closed by a spike (`/spike`) with a report in this folder.

| # | Question | Why it matters | Spike | Status |
|---|----------|----------------|-------|--------|
| Q1 | Can our account hold a Prisma stream open **and** run Timbre requests at the same time? | If not, every turn serialises; the loop may not work | P0.4 | open |
| Q2 | How many credits does a 3-minute call use? | Sets daily caps and live hours | P0.4 | open |
| Q3 | Do `speaker_names` and `topic` work on the realtime stream with Prisma v3? | Recognition of persona names; docs disagree | P0.2 | open |
| Q4 | Which Timbre `language` value sounds best for Hinglish/Tanglish/Benglish text: `hi-IN`, `hi-en`, `auto` (and Tamil/Bengali equivalents)? | Voice naturalness is the product | P0.3 | open |
| Q5 | Which voices perform each audio tag convincingly? | Borrower must not sound like a chatbot | P0.3 + manual M11 | open |
| Q6 | Does vLLM prefix caching work with Evon's hybrid Mamba–Transformer architecture? | TTFT per turn | P0.5 | open |
| Q7 | Evon TTFT with thinking off at ~1.2K-token prompts on our GPU | Latency budget | P0.5 | open |
| Q8 | Does a cloned voice handle Hinglish, and do tags work with it? | Optional replay feature | P0.6 | open |
| Q9 | Exact keyterms header format and Timbre `audio_config` keys | Request correctness | P0.2 / P0.3 | open |
| Q10 | RTT from the chosen backend region to `api.vachana.ai` | Region choice | P5.1 (earlier if cheap) | open |
