# Spike 02 · Prisma v3 realtime STT on our account

**Answer:** works; when push-to-talk is released straight after speech, commit→transcript is 0.315–0.385 s
(p50 0.344 s, n=5). Multi-locale `language_code` is **rejected** (1008).
**Confidence:** high for the measured items, low for anything below marked unverified.
**Date:** 2026-11-09 12:54 IST
**Script:** `scripts/spikes/02_prisma_stt.py` · **Time box:** 30 min (used ~12)

## Question and the decision it drives

Does Prisma v3 transcribe Hinglish collections speech from our code, how fast after commit, and do the
DOC facts that `gnani_stt.py` relies on hold? If the latency fits the turn budget we keep commit-on-release;
any header or message shape that differs becomes a client fix before the first real call (CP2).

## Method

- Input: M10 recordings `backend/tests/fixtures/audio/line{1,2,3}.wav` (mono, s16, 16 kHz; 9.11 / 5.87 /
  4.97 s). Trailing low-energy tail (frame RMS < 5 % of peak): line1 0.06 s, line2 0.90 s, line3 0.86 s.
- One socket per run; per line: 1,024-byte frames paced at 32 ms, then `{"type":"commit"}`, wait up to
  6 s for a `transcript`, 0.8 s settle. Times are `time.perf_counter()` on the client (WSL2, home network).
- Headers: `model: prisma-v3.0`, `format: transcribe`, `x-sample-rate: 16000`, `x-min-silence-ms: 600`,
  `x-idle-timeout-sec: 60`, `x-api-request-id`, `keyterms` = EMI, NACH, PTP, CIBIL, foreclosure,
  bounce charge, SwiftFin, Ramesh, Pooja.
- 5 runs × 3 lines with `language_code: hi-IN`; then single probes for `|` separator, no keyterms,
  `speaker_names`, `topic`, and 1 s of silence + commit.

## Raw results

`language_code: hi-IN,en-IN` (what the personas send today):

```
<- {"type": "error", "message": "Invalid language_code: exactly one locale is required (e.g. hi-IN).
    Received: hi-IN, en-IN. Use multi_lang_codes for parallel multi-locale transcription.", ...}
socket ended: ConnectionClosedError: received 1008 (policy violation)
```

`connected` with `language_code: hi-IN`:

```
{"type": "connected", "message": "STT service ready — VAD service connected", "request_id": "spike02-…",
 "config": {"sample_rate": 16000, "chunk_size": 512, "partials_enabled": false, "partial_interval_ms": 450,
 "partial_max_window_s": 15.0, "language_code": "hi-IN", "format": "transcribe"}, "timestamp": "…"}
```

A transcript (identical text in all 5 runs):

```
{"type": "transcript", "text": "नमस्ते रमेश जी, मैं SwiftFin से पूजा बोल रही हूँ। आपका EMI 12500 का pending है।",
 "audio_duration_ms": 8692.0, "segment_id": "spike02-…-seg-1", "segment_index": 1, "utterance_id": "u1f7c05ee",
 "is_final": true, "latency": 221, "prefetched": false, "timestamp": "…", "detected_language_code": "hi-IN"}
line2: "क्या आप 20 November तक 4000 का part payment कर सकते हैं?"
line3: "आपका NACH एक bounce हुआ था, bounce charge भी लगा है।"
```

Messages seen per line: `speech_start` (with `utterance_id`, `t_ms`) then `transcript`. No `processing`
message was seen in any run.

| Run | line1 commit→tr (s) | line2 (s) | line3 (s) | server `latency` line1/2/3 (ms) |
|-----|------|------|------|------|
| 1 | 0.385 | 0.069 | 0.044 | 221 / 168 / 139 |
| 2 | 0.344 | 0.069 | 0.041 | 216 / 163 / 135 |
| 3 | 0.352 | 0.075 | 0.050 | 236 / 173 / 147 |
| 4 | 0.315 | 0.056 | 0.051 | 208 / 159 / 140 |
| 5 | 0.332 | 0.048 | 0.021 | 214 / 162 / 130 |

All 15: p50 0.069 s, max 0.385 s, min 0.021 s. No transcript arrived before a commit in any run.

Probes (line1 unless noted):

| Probe | Result |
|-------|--------|
| keyterms joined with `\|` | accepted; transcript corrupted: "…आपका EMI NACH-PTPCIBIL का पेंडिंग है।" (amount lost, keyterms pasted in) |
| no keyterms (empty header) | accepted; "Swift Fin" instead of "SwiftFin", rest identical |
| `speaker_names: Pooja,Ramesh` (line2) | accepted silently; not echoed in `config`; transcript unchanged |
| `topic: loan_recovery` (line2) | accepted silently; not echoed in `config`; transcript unchanged |
| 1 s silence (32,000 B of zeros) + commit | `connected` only; **no message of any kind** within 6.8 s |

## Interpretation

- **Two latency regimes.** line1 ends 0.06 s after speech, so the commit is what closes the segment:
  0.315–0.385 s. line2/3 carry ~0.9 s of trailing silence, more than `x-min-silence-ms` 600, so the server
  had already closed the segment and was decoding before the commit: 0.021–0.075 s. Push-to-talk release
  right after speech is the line1 case; **plan with p50 0.344 s, max 0.385 s (n=5)**. The pooled p50 of
  0.069 s flatters the real case and should not be quoted alone. Measured on one network from WSL2.
- Output style with `format: transcribe` + `hi-IN`: **Devanagari** for Hindi words, **Roman** for English
  words and brand names (SwiftFin, EMI, NACH, November, part payment, bounce charge), **digits** for
  amounts and dates (M10 script "baarah hazaar paanch sau" → 12500, "bees November" → 20 November,
  "chaar hazaar" → 4000).
- Keyterms help (SwiftFin vs "Swift Fin") and the separator is **`,`**; `|` actively damages output.
- `speaker_names` / `topic`: accepted, no observable effect on the v3 realtime stream. Treat as no-ops.
- line3 has "NACH **एक** bounce"; the script line has no "ek". Whether it was spoken is for Vahant to hear.

## Decision

1. **Fix before the first real call (blocker):** personas send `"hi-IN,en-IN"`, `"ta-IN,en-IN"`,
   `"bn-IN,en-IN"`; Prisma rejects all of these with 1008, so every real call would fail at connect.
   Use a single locale per persona (`hi-IN`, `ta-IN`, `bn-IN`); English words already come through in Roman
   with `hi-IN`. `multi_lang_codes` exists per the error text; not tried — unverified. `ta-IN` and `bn-IN`
   with Tanglish/Benglish speech: unverified (no recordings).
2. `connected` echo is nested under `config` and has no `model`; `gnani_stt.py` reads top-level fields,
   so its echo check never compares anything. Read `config.language_code` / `config.format`.
3. Commit with no open segment gets no answer: keep the client-side grace timeout (`NO_SPEECH_GRACE_S`).
4. Keep `,` for keyterms. Keep transcript fields top-level; error text is in `message`.
5. Update `fake_prisma.py` to mirror the real shapes (nested `config` echo, no `processing`, 1008 on
   multi-locale) in the same fix step, per the Gnani client rules.
