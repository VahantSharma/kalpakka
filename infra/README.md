# infra · how KalPakka runs in public

Everything that puts KalPakka on the internet. The decision behind it is
[ADR-001](../docs/decisions/ADR-001-evon-hosting.md). Every number here is a **target** until a
run in the logs measures it.

```
browser (Vercel) ──wss/https──▶ Caddy (sslip.io TLS) ──▶ backend (Docker, AWS ap-south-1 VM)
                                                           ├──▶ Gnani Prisma v3 / Timbre v3
                                                           └──https──▶ Evon Q8_0 · llama-server · RunPod A6000 (live hours only)
```

| Path | What |
|------|------|
| `backend/Dockerfile`, `compose.yaml`, `Caddyfile` | backend image + Caddy on one VM; automatic TLS on `<ip-with-dashes>.sslip.io` |
| `backend/bootstrap-vm.sh` | one-time VM setup (Docker, secrets file, swap) |
| `backend/deploy.sh` | ship the committed HEAD to the VM, build, wait for HTTPS, smoke-test |
| `backend/set-llm-url.sh` | point the backend at a new RunPod pod URL |
| `backend/smoke.py` | `/healthz` + one WebSocket handshake with the right and a foreign Origin |
| `gpu/runpod.py` | live-hours GPU: `start` / `status` / `warmup` / `stop`, with a balance guard and auto-terminate |
| `gpu/llama-server.flags` | the one list of llama-server flags (RunPod and the Modal spike) — confirm after P0.5 |
| `gpu/warmup.py` | 3 real Ramesh turns through the backend's prompt code |
| `vllm/launch.sh` | vLLM fallbacks (FP8 on the A6000; BF16 on an A100 80 GB) |
| `../scripts/spikes/evon_modal/` | P0.5 spike on Modal (runbook: `docs/phases/P0-foundation/P0.5-evon-spike.md`) |
| `../evals/quant_gate/` | BF16 vs quant gates + blind A/B |

Tests for the pure helpers (budget, spike maths, gate, RunPod money rules, smoke protocol):
`uv run --script scripts/spikes/run_tests.py`.

## Environment variables (names only; values never committed or printed)

| Variable | Used by | Notes |
|----------|---------|-------|
| `EVON_API_KEY` | spike scripts, `runpod.py`, `warmup.py` | the key Evon's server requires; same value as the backend's `LLM_API_KEY` |
| `HF_TOKEN` | `convert.py` (write, via Modal secret `hf`), `runpod.py` (read, passed to the pod) | use a read-only token for the pod |
| `HF_GGUF_REPO` | `convert.py`, `runpod.py` | e.g. `<user>/evon-v3.3-gguf` (private) |
| `SPIKE_BUDGET_USD` | Modal spike scripts | default 10 |
| `CONVERT_TRANSFORMERS_PIN` | `convert.py` | only if the converter cannot load Evon's tokenizer |
| `EVON_QUANT` | `serve_q8.py`; `vllm/launch.sh` (`bf16`/`fp8`) | |
| `RUNPOD_API_KEY` | `runpod.py` | |
| `RUNPOD_TEMPLATE_ID`, `RUNPOD_NETWORK_VOLUME_ID` | `runpod.py` | from the one-time setup below |
| `RUNPOD_GPU_TYPE`, `RUNPOD_RATE_USD_H`, `RUNPOD_PREPAID_USD` | `runpod.py` | defaults `NVIDIA RTX A6000`, `0.59`, `17` |
| `VM`, `WEB_ORIGIN`, `ACME_EMAIL` | `deploy.sh`, `set-llm-url.sh` | `VM=ubuntu@<public-ip>` |
| backend: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `GNANI_API_KEY`, `ALLOWED_ORIGINS`, `LIVE_HOURS`, caps | `/etc/kalpakka/backend.env` on the VM | template: `backend/backend.env.example` |

Modal secrets (created once): `hf` → `HF_TOKEN`; `evon-api` → `EVON_API_KEY`.

## Accounts and one-time setup

1. **Modal** (free $30/month credit): `uvx modal@1.6.2 setup`, then the two secrets above. Used only
   for P0.5.
2. **Hugging Face**: one WRITE token (Modal secret, for the private GGUF upload) and one READ token
   (for the RunPod pod). Whether the private-storage quota holds ~52 GB is UNVERIFIED (P0.5 step 2).
3. **RunPod** (prepay **$17**, the hard cap):
   - Network volume, 50 GB, in a Secure Cloud data centre that lists RTX A6000.
   - Pod template: image `ghcr.io/ggml-org/llama.cpp:server-cuda12-b12030` (needs a CUDA ≥ 12.8 host;
     filter on that if offered), container disk 20 GB, volume mount path `/workspace`, expose HTTP
     port `8080`, no start command (`runpod.py` passes the llama-server args as `dockerArgs`).
   - Put the template id and the volume id into `RUNPOD_TEMPLATE_ID` / `RUNPOD_NETWORK_VOLUME_ID`.
   - **First start on a staging day, not Monday:** it downloads the ~32 GB Q8_0 into
     `/workspace/llama-cache` (GPU billed while it downloads). Later starts reuse it. On staging
     days always pass a short dead-man timer, e.g. `runpod.py start --until 15:00` for a test
     at 13:00. The default `--until 23:00` would let a forgotten pod bill until 23:15 (≈ $6 from 13:00).
4. **AWS** ap-south-1: one small Ubuntu 24.04 VM (e.g. t3.small; price UNVERIFIED), an Elastic IP
   so the sslip.io name never changes, security group 22/tcp from your IP, 80/tcp + 443/tcp(+udp)
   from anywhere. Then `bootstrap-vm.sh`, fill `/etc/kalpakka/backend.env`, run `deploy.sh`.
5. **Vercel**: the web app's backend URL is `wss://<ip-with-dashes>.sslip.io/call` (owned by the web
   track); `ALLOWED_ORIGINS` on the VM must contain the Vercel URL exactly.

## Live day · Monday 2026-11-09 (posts at 11:00 IST)

Every step prints what it did. Paste each `[cost]` line into the table at the end.

| IST | Do | Expect / if not |
|-----|----|-----------------|
| 10:30 | `uv run --script infra/gpu/runpod.py status` | `balance $… · ≈ N h left`. If < 13 h of balance, top up **now** (start refuses below 1 h) |
| 10:35 | `uv run --script infra/gpu/runpod.py start --until 23:00` | `pod … created at $0.xx/h` → `llama-server healthy … s` → `[warmup] 3/3 OK` → `READY · LLM_BASE_URL=…`. Auto-terminate is set for 23:15 |
|  | if `no A6000 available` | retry in 2 min; after 3 tries go to fallback 2 (`vllm/launch.sh` on an A100, Mon only, ADR-001) |
|  | if warm-up FAIL | `runpod.py warmup` once; still failing → `stop`, `start` again (new pod); paste the warm-up lines |
| 10:45 | `VM=ubuntu@<ip> infra/backend/set-llm-url.sh <LLM_BASE_URL from start>` | backend recreated in seconds |
| 10:47 | `uv run --script infra/backend/smoke.py https://<ip-dashes>.sslip.io --origin https://<web>.vercel.app` | `[smoke] PASS`: healthz 200 with the Gnani key set and fixed lines warmed; good origin 101; foreign origin refused. It counts as **one call** against the caps; run it once, from a network the phone testers do not share |
| 10:50 | phone test: Android Chrome + iPhone Safari, one full call each on the **public** link | Ramesh answers, flags show, scorecard arrives. Note turn latency from `GET /metrics/latency` |
| 11:00 | posts go live | |
| every hour | `runpod.py status` + `curl -s https://<host>/healthz` + `/metrics/latency` | balance falling at ~$0.59/h; `ok: true`; p50/p95 per stage. Write the numbers in the log |
| 22:00 | live hours end in the backend (`LIVE_HOURS=10:45-22:00`; new calls are refused with a message) | change `LIVE_HOURS` on the VM if the announced hours differ |
| 23:00 | `uv run --script infra/gpu/runpod.py stop` | `terminated … after N h at $… ≈ $…` + `[cost]` line. Missed it? The pod self-terminates at 23:15 |
| 23:05 | `runpod.py status` | `no pod running` — and the RunPod console shows no pod |

Incident notes go to `docs/launch/incidents.md` (time, symptom, action, number).

### Cost per stop (fill in from the `[cost]` / `terminated` lines)

The real number is **"spent since prepaid"** (from RunPod's balance). "This pod ≈ $" comes from
the container's uptime, which resets if llama-server restarts, so it can be too low.

| Date | Pod hours | $ this pod | Balance after | Spent since prepaid |
|------|-----------|------------|---------------|---------------------|
| | | | | |
