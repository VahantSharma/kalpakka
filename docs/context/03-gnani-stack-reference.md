# 03 · Gnani stack reference

Exact protocol facts for the three Gnani models and Evon serving. Each fact has a status:

- **DOC** — stated in official documentation read during research (Nov 1–2 2026). Safe to design
  against, but the P0 spikes must confirm it before app code depends on it.
- **MEASURED** — confirmed by one of our spikes; link the spike report.
- **UNVERIFIED** — inferred, ambiguous, or the docs disagree. A spike must settle it first.

When a spike changes a fact, edit its row here in the same sub-phase and link the report.
Primary sources: <https://docs.gnani.ai/llms.txt> (index), <https://docs.gnani.ai/api/STT/limits.md>,
<https://docs.gnani.ai/api/TTS/tts-audio-tags.md>, <https://docs.gnani.ai/api/VC/voice-clone-embeddings.md>,
<https://docs.gnani.ai/api/VC/vc-inference.md>, <https://huggingface.co/gnani/gnani-evon-v3.3-30B-A3B>.

## Account and credits

| Fact | Status |
|------|--------|
| Challenge accounts get 5,000 programme credits on app.gnani.ai/voice | DOC |
| What one credit buys (per second of STT? per character of TTS?) | UNVERIFIED — measure before/after a 3-minute call (spike 04) |
| Some accounts are limited to **one active request** at a time | DOC (limits page) |
| Whether our account can hold a Prisma stream open **and** run a Timbre request at the same time | UNVERIFIED — **the single biggest risk**; spike 03 on Day 1. If 429, ask on Discord before writing more code |

## Prisma v3.0 — speech-to-text (realtime)

| Fact | Value | Status |
|------|-------|--------|
| Endpoint | `wss://api.vachana.ai/stt/v3/stream` | DOC |
| Auth header | `x-api-key-id: <key>` | DOC |
| Model header | `model: prisma-v3.0` | DOC |
| Language header | `language_code: hi-IN,en-IN` (persona-specific list), or `indic-auto` for auto language ID | DOC |
| Output format header | `format: transcribe` (live default: amounts/dates as digits). Others: `verbatim`, `codemix`, `transliterate` | DOC |
| Keyterms header | `keyterms` (up to 200): EMI, NACH, PTP, CIBIL, foreclosure, bounce charge, SwiftFin, persona name | DOC (exact separator UNVERIFIED) |
| Sample rate header | `x-sample-rate: 16000` (8000 for the phone-line Arena loop) | DOC |
| VAD headers | `x-vad-threshold`, `x-min-silence-ms` (we use 600), `x-min-speech-ms` — fixed at connection time | DOC |
| Idle timeout header | `x-idle-timeout-sec` (raise to 300) | DOC |
| Request id header | `x-api-request-id` (send our `call_id`) | DOC |
| Audio frames | binary, **exactly 1,024 bytes** = 512 little-endian int16 samples, sent at real-time pace (32 ms per frame at 16 kHz) | DOC |
| Force segment end | send text `{"type":"commit"}` (on push-to-talk release) | DOC |
| Server messages | `connected` (echoes config), `speech_start`, `processing`, `transcript` (`text`, `detected_language_code`, `latency`, `segment_index`), `error` | DOC |
| Partials | **none**: one final transcript per speech segment | DOC |
| Max session | 15 minutes | DOC |
| Bad config | socket closes with 1008 / first message `error` | DOC |
| `speaker_names` and `topic` on the realtime stream with v3 | docs disagree | UNVERIFIED (spike 02) |
| Latency from segment end to `transcript` | ~0.3–0.6 s | target, UNVERIFIED |
| Batch / REST transcription (for Audit mode) | exists | DOC; endpoint details not yet read |

Languages: nine Indian languages plus English (10), code-mixing supported, trained on telephone
audio, native 8 kHz support.

## Timbre v3.0 — text-to-speech (realtime)

| Fact | Value | Status |
|------|-------|--------|
| Endpoint | `wss://api.vachana.ai/api/v1/tts` | DOC |
| Auth header | `X-API-Key-ID: <key>` | DOC |
| Request (one JSON per sentence) | `{"text", "voice", "model": "timbre-v3.0", "language", "speed", "audio_config": {"container": "raw", "sample_rate": 24000, ...}}` | DOC (exact `audio_config` keys UNVERIFIED) |
| Server messages | `start` (`request_id`), `audio` (base64 PCM in `data.audio`), `complete`, `error` | DOC |
| Output | raw 16-bit PCM at 24 kHz; µ-law 8 kHz also available for phone mode | DOC |
| Voices | 13, each speaks all 10 languages. Candidates: **Devansh** (Ramesh), **Lakshmi** or **Kalpana** (Lakshmi akka), **Shubham** (Debu) | DOC (names); fit UNVERIFIED — Vahant auditions (manual step) |
| Audio tags (13) | `<laugh>` `<giggle>` `<sigh>` `<exhales>` `<sniffles>` `<yawns>` `<coughs>` `<clears throat>` `<whispers>` `<shouting>` `<excited>` `<slow>` `<pauses>` | DOC |
| Tag scope | from the tag's position to the end of that request → one sentence per request scopes it | DOC |
| Recommended tags per turn | at most 2 | DOC |
| First audio | sub-150 ms advertised | DOC; our number UNVERIFIED (spike 02) |
| Best `language` value for Hinglish text: `hi-IN`, `hi-en` or `auto` | — | UNVERIFIED (spike 02, Vahant listens) |
| Mid-sentence code-switch and Indian number reading | supported | DOC |
| REST endpoint for pre-rendered fixed lines | exists | DOC; path to confirm |

## Voice clone — `vachana-vc-v1` (optional replay feature only)

| Fact | Status |
|------|--------|
| `POST /api/v1/tts/voice-clone/embeddings`, multipart field `audio_file`, 5–30 s reference audio → speaker embedding (768 numbers) | DOC |
| Synthesis: pass `speaker_embedding` object with `model: vachana-vc-v1` to the TTS endpoints (REST, SSE, realtime WS) | DOC |
| Audio tags with cloned voices | NOT DOCUMENTED → treat as unsupported; borrowers use stock Timbre voices |
| Hinglish quality of a cloned voice | UNVERIFIED (spike 05) |

Consent design: spoken consent with a random code, clone only the trainee's own voice, only for text
our judge wrote, store only the embedding, delete with the player.

## Evon v3.3 — the LLM (self-hosted)

| Fact | Status |
|------|--------|
| Hugging Face repo `gnani/gnani-evon-v3.3-30B-A3B`, **not gated** (checked via the Hugging Face API on 2026-11-04), Apache 2.0 | VERIFIED (HF API, 2026-11-04) |
| 30B-parameter hybrid Mamba–Transformer MoE, ~3.5B active per token, 11 languages, 128K context | DOC |
| Not on Gnani's API → we self-host | DOC |
| BF16 weights ≈ 60 GB → one 80 GB GPU (A100 80GB / H100) | DOC (model card lists H100/A100) |
| Launch: `vllm serve gnani/gnani-evon-v3.3-30B-A3B --trust-remote-code --reasoning-parser nano_v3 --enable-prefix-caching --max-model-len 16384 --api-key $VLLM_API_KEY` | DOC for the base command and parser name per model card; **flags must be checked against the card on the day** |
| Thinking off for live turns: `extra_body={"chat_template_kwargs": {"enable_thinking": false}}` | DOC |
| Thinking on for the judge, with JSON-schema structured output | DOC (vLLM feature); with this model UNVERIFIED |
| SGLang is also documented as a server | DOC |
| Prefix caching works with the hybrid (Mamba) architecture in the installed vLLM version | UNVERIFIED (spike 06) — if not, the persona prompt is re-processed each turn; measure the cost |
| Time to first token with thinking off, ~1.2K-token prompt | target 0.15–0.4 s, UNVERIFIED (spike 06) |
| Quantised (FP8) build availability | UNVERIFIED — fallback if BF16 will not load |

Fallback if Evon cannot serve by Nov 3 morning (kill criterion a): try quantised; else run live
turns on another LLM, keep Evon for the post-call judge on a short-lived GPU, and **say so openly**.

## Out of scope for the challenge

Warp (speech-to-speech) and the Agent Builder. Pipecat / LiveKit have Gnani plugins, but we
hand-build orchestration (D15) for control of state, barge-in history and scoring.
