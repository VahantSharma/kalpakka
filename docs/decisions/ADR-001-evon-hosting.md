# ADR-001 · Evon hosting: BF16 reference on Modal, our own Q8_0 GGUF live on one RunPod A6000

**Date:** 2026-11-05 · **Status:** accepted (decision by Vahant, 01:35 IST); the quant gate in P0.5
still has to pass before the live path is used · **Supersedes:** D6 (vLLM) for live turns only; the
reference and the judge stay on vLLM

## Context

- Evon v3.3 (`gnani/gnani-evon-v3.3-30B-A3B`) is the eligibility model: it must do real work.
  It is **not gated** (STATUS "doc corrections"; `03` still says "access by request").
- Model card, read 2026-11-05 via the published card and `config.json`: architecture
  `NemotronHForCausalLM` (Mamba2–Transformer hybrid MoE, 30B total, ~3.5B active), BF16, 128K
  context. Runtime: vLLM ≥ 0.12.0 with `--trust-remote-code --reasoning-parser nano_v3
  --tool-call-parser qwen3_coder --enable-auto-tool-choice`. Thinking off per request with
  `"chat_template_kwargs": {"enable_thinking": false}`. Listed hardware: H100 80GB, H200, A100.
- The BF16 weights are about 63.5 GB, so they need an 80 GB card. An always-on 80 GB card for the
  whole live window does not fit the budget.
- Budget: **≤ ₹1,500 in total**, with a RunPod prepaid cap of **$17**.
- llama.cpp supports this architecture. `conversion/nemotron.py` in ggml-org/llama.cpp master
  (read 2026-11-05) registers `NemotronHForCausalLM` including the MoE path, and
  `convert_hf_to_gguf.py` offers `--outtype bf16|q8_0`. So we can build our own GGUF. Nobody needs
  to publish one for us.

## Options considered

1. **Substitute model for live turns (Qwen, Llama, Sarvam, …)**, rejected. Eligibility requires
   Evon to do real work, and the whole pitch is "Gnani's stack end to end". A different live model
   would also make every persona, trap and judge measurement non-transferable.
2. **Kaggle / Colab free GPUs**, rejected. T4/P100 (16 GB) and L4 (24 GB) cannot hold BF16 or Q8
   (~34 GB). Notebook sessions also give no stable public HTTPS endpoint for strangers on launch
   day, and they time out.
3. **Always-on A100 80 GB (RunPod or Modal) for the live window**, rejected on cost. Modal A100-80GB
   is $0.000694/s ≈ $2.50/h (modal.com/pricing, read 2026-11-05). Mon 11:00–23:00 alone would cost
   about $30, before staging, testing or the Tue interview window.
4. **FP8 on vLLM on a 48 GB card**: the first fallback (below). It is not the primary because no
   FP8 build is published (`03`: UNVERIFIED). On-the-fly `--quantization fp8` on an Ampere A6000
   uses weight-only FP8 kernels whose speed with this hybrid MoE is unknown.
5. **Chosen: BF16 reference on Modal plus our own Q8_0 GGUF on a RunPod RTX A6000 48 GB**, behind
   a measured quant gate.

## Decision

- **Reference (truth for every quality comparison, and for calibrating the judge):** the official
  BF16 weights on vLLM ≥ 0.12.0, with exactly the model-card flags plus prefix caching and an API
  key, on Modal 1× A100-80GB. Containers scale to zero, and the runs are paid from Modal's $30/month
  free credit. A **CPU** function downloads the weights into a Modal Volume, so no GPU time is spent
  downloading.
- **Live (announced hours only):** an Evon **GGUF Q8_0 that we make ourselves**.
  `convert_hf_to_gguf.py --outtype bf16`, then `llama-quantize … Q8_0` (plus Q4_K_M as data only),
  runs in a Modal CPU function. The files are sha256-summed and uploaded to Vahant's **private** HF
  repo (`HF_GGUF_REPO`). The live server is `llama-server` on one RunPod RTX A6000 48 GB (quoted
  at $0.59/h; confirm in the console), started from a template with the GGUF on a network volume.
- **Fallbacks, in order:** (1) FP8 on the same A6000 via vLLM (`--quantization fp8`); (2) BF16 on a
  RunPod A100 80 GB for **Mon 11:00–23:00 only**, with every other window closed.
- Code impact: none in `backend/`. `kalpakka.llm` is already provider-agnostic. vLLM and
  llama-server both accept `chat_template_kwargs` per request (llama.cpp `tools/server/README.md`,
  read 2026-11-05), so `ChatTemplateThinkingAdapter` is expected to work for both. P0.5 measures
  this; it is not assumed.

## Quant gate (P0.5; `evals/quant_gate/`)

A live candidate (Q8_0, or FP8) replaces BF16 only if **every** gate passes on the same 30 scripted
turns with identical prompts. Prompt hashes per turn must match between the two runs.

| Gate | Pass if |
|------|---------|
| `###STATE###` valid rate (StateSplitter, no errors) | candidate ≥ 98% |
| Judge JSON valid rate (thinking on, `response_format` JSON schema, parses as `JudgeOutput`) | candidate ≥ 95% |
| Thinking off works | no reasoning text in any thinking-off reply |
| Script switches (a character outside Devanagari + Latin in a Ramesh reply) | candidate 0 |
| TTFT p50 and p95 (thinking off) | candidate ≤ BF16 × 1.10 (10% noise allowance, set before any run) |
| Blind A/B: Vahant's pick rate for the candidate over 20 pairs | ≥ 40% |

If any gate fails, go to the next fallback and re-run the same gate. The gate is not loosened after
seeing results.

## Cost table (targets, not measurements)

Prices: Modal from modal.com/pricing (read 2026-11-05). The RunPod A6000 price is as quoted by
Vahant. The RunPod network-volume price ($0.07/GB/month) is from memory and UNVERIFIED; confirm it
in the console. INR uses Vahant's planning figure; set `USD_INR` for the cost meter.

| Item | Basis | USD |
|------|-------|-----|
| Modal download (CPU, ~4 cores, 16 GiB, ≤ 30 min) | free credit | ≈ 0.16 |
| Modal convert + quantize + upload (CPU, 8 cores, 64 GiB, ≤ 120 min) | free credit | ≈ 1.78 |
| Modal BF16 spike (A100-80GB, ≤ 90 min hold) | free credit | ≈ 3.75 |
| Modal Q8 spike (L40S, ≤ 60 min hold) | free credit | ≈ 1.95 |
| **Modal subtotal** (hard guard `SPIKE_BUDGET_USD=10`) | inside the $30 credit | **≈ 7.6 → ₹0 cash** |
| RunPod A6000: staging and phone tests Fri–Sun, ~6 h | $0.59/h | 3.54 |
| RunPod A6000: live Mon 10:45–23:00, 12.25 h | $0.59/h | 7.23 |
| RunPod A6000: Tue window for judges/interview, ~6 h | $0.59/h | 3.54 |
| RunPod network volume 50 GB, ~1 week | $0.07/GB/mo (UNVERIFIED) | ≈ 0.82 |
| **RunPod subtotal** | prepaid, hard cap **$17** | **≈ 15.1** |
| HF private repo (~34 GB Q8 + ~18 GB Q4_K_M) | free tier; private-storage quota UNVERIFIED | 0 |

Plan total ≈ $15.1 cash ≈ **₹1,365** (Vahant's planning figure). The $17 prepaid cap is the hard
ceiling: `infra/gpu/` refuses to start a pod if the balance would not cover one more hour. At
₹88.2/$ or less, the $17 cap stays within ₹1,500. Above that rate, lower the cap.

## Consequences

- Two servers, one contract. The spike runs one script (`scripts/spikes/evon_modal/run_spike.py`)
  against both, so differences are measured, not argued.
- We own a GGUF build step (llama.cpp version pinned by commit in `convert.py`), and the file's
  sha256 is recorded so the live server can be traced to the exact weights.
- Live hours are bounded (D20). Outside them the site shows the recording.
- Interview story: "Evon BF16 is the reference. Live runs our own Q8_0 build, which had to pass six
  pre-registered gates against BF16, including a blind A/B."

## Open items the spike settles (P0.5)

1. Does `chat_template_kwargs.enable_thinking=false` turn thinking off on **llama-server** with
   this GGUF's embedded template? If not, start llama-server with `--reasoning-budget 0` and record it.
2. Does vLLM prefix caching work with the hybrid Mamba layers in the installed version (`03` spike 06)?
3. TTFT p50/p95 for a ~1.2K-token Ramesh prompt on A100 BF16 vs L40S Q8 (the A6000 is slower than
   the L40S; the live number is re-measured on the A6000 in P5.3).
4. Judge JSON validity with thinking on plus `response_format` JSON schema on both servers.
5. Cold start: Modal scale-from-zero, and RunPod pod start → llama-server healthy.
6. Whether the HF private-repo quota accepts ~52 GB on Vahant's account.

## Revisit if

Any quant gate fails for both Q8_0 and FP8 · live TTFT p95 on the A6000 > 1.0 s · RunPod balance
would drop below one hour before Mon 23:00 · Gnani publishes an official hosted Evon endpoint.
