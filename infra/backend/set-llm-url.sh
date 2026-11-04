#!/usr/bin/env bash
# Point the deployed backend at a new Evon URL (each RunPod start is a new pod, so a new URL).
#   VM=ubuntu@13.233.10.25 infra/backend/set-llm-url.sh https://<pod-id>-8080.proxy.runpod.net/v1
# Rewrites only the LLM_BASE_URL line of /etc/kalpakka/backend.env on the VM, then recreates the
# backend container (a few seconds; do it before live hours open, never during a call).
set -euo pipefail
: "${VM:?set VM=ubuntu@<public-ip>}"
URL="${1:?usage: set-llm-url.sh https://<pod-id>-8080.proxy.runpod.net/v1}"
[[ "$URL" =~ ^https://[a-z0-9]+-8080\.proxy\.runpod\.net/v1$ ]] \
  || { echo "set-llm-url: unexpected URL shape: $URL" >&2; exit 2; }
ssh "$VM" "set -e
  sed -i 's#^LLM_BASE_URL=.*#LLM_BASE_URL=$URL#' /etc/kalpakka/backend.env
  grep -c '^LLM_BASE_URL=$URL\$' /etc/kalpakka/backend.env >/dev/null
  cd ~/kalpakka/infra/backend && docker compose up -d --no-deps --force-recreate backend"
echo "set-llm-url: backend now uses $URL; check: curl -s https://\$(echo ${VM#*@} | tr . -).sslip.io/healthz"
