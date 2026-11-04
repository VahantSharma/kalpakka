#!/usr/bin/env bash
# vLLM launch for Evon v3.3 on a rented GPU (fallbacks in ADR-001). Same flags as the Modal BF16
# reference (scripts/spikes/evon_modal/serve_ref.py): the model-card flags, unchanged, plus prefix
# caching, an API key and a served name. Status: TARGET — confirm after P0.5.
#
#   EVON_API_KEY=... MODEL_DIR=/workspace/hf/gnani-evon-v3.3-30B-A3B ./launch.sh          # BF16, 80 GB
#   EVON_API_KEY=... MODEL_DIR=... EVON_QUANT=fp8 MAX_MODEL_LEN=16384 ./launch.sh         # FP8 on 48 GB
#
# Image: vllm/vllm-openai:v0.31.0 (model card: vLLM >= 0.12.0). The key goes through the
# VLLM_API_KEY environment variable, never the command line (it would show in `ps` and logs).
set -euo pipefail

: "${EVON_API_KEY:?set EVON_API_KEY}"
MODEL_DIR="${MODEL_DIR:-/workspace/hf/gnani-evon-v3.3-30B-A3B}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"
PORT="${PORT:-8000}"

args=(
  serve "$MODEL_DIR"
  --served-model-name evon
  --host 0.0.0.0 --port "$PORT"
  --trust-remote-code
  --enable-auto-tool-choice --tool-call-parser qwen3_coder
  --reasoning-parser nano_v3
  --enable-prefix-caching
  --max-model-len "$MAX_MODEL_LEN"
)
case "${EVON_QUANT:-bf16}" in
  bf16) ;;
  fp8) args+=(--quantization fp8) ;;  # on-the-fly FP8 weights; Ampere (A6000) uses weight-only kernels
  *) echo "EVON_QUANT must be bf16 or fp8" >&2; exit 2 ;;
esac

export VLLM_API_KEY="$EVON_API_KEY"
exec vllm "${args[@]}"
