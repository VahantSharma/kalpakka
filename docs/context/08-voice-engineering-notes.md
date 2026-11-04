# 08 · Voice engineering notes

What a senior voice engineer keeps in their head while building the live loop. Numbers marked
"target" are goals to measure, not facts.

## Audio math you will use constantly

- PCM16 mono: 2 bytes per sample. At 16 kHz: 32,000 bytes/s. A 1,024-byte Prisma frame = 512
  samples = **32 ms**. One second of mic audio = 31.25 frames.
- Timbre output at 24 kHz PCM16: 48,000 bytes/s. A sentence of ~2.5 s ≈ 120 KB raw (~160 KB base64).
- Phone line: 8 kHz µ-law, 1 byte per sample. Decode to PCM16 before sending to Prisma at 8 kHz.
- Browsers capture at 44.1 or 48 kHz float32. Downsample in the AudioWorklet (low-pass, then
  decimate; a linear-interpolation resampler is acceptable for speech at 16 kHz), convert to int16
  little-endian, buffer, and emit exact 1,024-byte frames.
- Endianness: Python `numpy.int16` and JS `Int16Array` are little-endian on all target platforms.
  Never build int16 from bytes by hand without stating byte order.

## Pacing frames to Prisma

Prisma expects real-time pace. Use a monotonic clock and a deadline per frame
(`next_deadline += 0.032`), not `sleep(0.032)` per frame (drift accumulates). When the browser
sends bursts (network jitter), queue and pace them out. On `ptt_end`, flush the remainder padded
to 1,024 bytes with zeros, then send `{"type":"commit"}`.

## Latency budget (targets, button release → first borrower audio)

| Stage | Target | Main lever |
|-------|--------|-----------|
| commit → final transcript | 0.2–0.5 s | push-to-talk + commit (no waiting for VAD silence) |
| brief build | < 5 ms | pure Python, no I/O |
| Evon first token | 0.15–0.4 s | thinking off, warm GPU, prefix cache, short prompt tail |
| first sentence complete | +0.1–0.3 s | persona opens with a short reaction ("Arre sir…") |
| Timbre first audio | 0.15–0.3 s | socket already open; one sentence per request |
| network + browser scheduling | 0.05–0.15 s | Mumbai region; play the first chunk immediately |
| **Total** | **≈ 1.2 s p50, < 2.5 s p95** | |

Log ten timestamps per turn: `ptt_end`, `commit_sent`, `transcript_final`, `brief_built`,
`llm_request`, `llm_first_token`, `first_sentence_ready`, `tts_request`, `tts_first_audio`,
`browser_first_play` (reported by the browser). Percentiles per stage at `GET /metrics/latency`.

## Sentence chunking

Cut on `।` (danda), `.`, `?`, `!`, `…`, and newline, but not inside numbers ("₹12.500", "2.5"),
abbreviations ("Mr.", "Dr.", "Rs."), or tags. Merge fragments shorter than ~12 characters with the
next sentence (tiny TTS requests sound choppy). Flush whatever remains when the stream ends or the
`###STATE###` delimiter arrives. Police tags: keep only the official 13 and only those allowed this
turn; at most two per turn; strip unknown `<...>` tokens; never let a tag be the whole sentence.

## Barge-in done right

The browser reacts, the server cleans up. Local stop takes ~0 ms; waiting for the server would add
50–150 ms of the AI talking over the trainee. Server steps in order: cancel the reply task (closes the
vLLM stream), clear the sentence queue, reset the Timbre socket (drop half-finished audio), bump
`min_valid_turn`, trim history to `played` sentences, apply only the interruption mood bump.
Any audio chunk tagged with an old turn number is dropped on both sides.

## Browser audio pitfalls

- AudioContext must be created or resumed inside a user gesture ("Start call"), or playback is silent.
- iOS Safari often ignores the requested sample rate and may run at 48 kHz; resample on both
  capture and playback; test on a real iPhone early.
- Echo: push-to-talk avoids most echo problems; still request `echoCancellation`, `noiseSuppression`,
  `autoGainControl` in `getUserMedia` and tell players to use headphones.
- Schedule playback on `ctx.currentTime` with a running playhead; keep ~50 ms lead; track every
  `AudioBufferSourceNode` so barge-in can stop them all.
- Bluetooth headsets switch to low-quality mic profiles; expect worse STT.
- The mic needs HTTPS (localhost is the exception).

## Streaming the LLM

Use the OpenAI-compatible client with `stream=True` against vLLM. Parse deltas into the chunker
until the delimiter, then buffer the JSON. Handle: the delimiter split across deltas, the model
emitting thinking tokens despite `enable_thinking: false` (strip `<think>…</think>` defensively and
log it), early stop, and timeouts (no first token in 2 s → filler line path).

## Concurrency inside one call

Tasks per call: receive-from-browser, send-to-Prisma (pacer), receive-from-Prisma, reply
(LLM → chunker → TTS), send-to-browser, silence timer, background tripwire checks. Use
`asyncio.Queue` between them and one `TaskGroup` owned by the `CallSession`. Admission uses an
`asyncio.Semaphore(MAX_CONCURRENT_CALLS)` plus a FIFO queue that reports positions.

## Debugging cookbook

| Symptom | Usual cause |
|---------|-------------|
| Prisma returns nothing | frames not 1,024 bytes, wrong sample-rate header, float32 sent instead of int16, no `commit` |
| Chipmunk or slow-motion audio | sample-rate mismatch between Timbre output and playback context |
| Clicks between chunks | scheduling with timers instead of the audio clock; odd-length byte buffers |
| Borrower answers a question the trainee never heard finish | history not trimmed after barge-in |
| First call after GPU start takes 20 s | cold start; warm up before live hours |
| Socket dies after ~60 s silence | proxy idle timeout; raise Caddy timeouts, send pings |
| Same tag on every sentence | tag scope not limited per request, or prompt over-encourages tags |
