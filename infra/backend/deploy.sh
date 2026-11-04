#!/usr/bin/env bash
# Deploy the committed backend to the VM and smoke-test it. Run from the repo root, on the laptop:
#
#   VM=ubuntu@13.233.10.25 WEB_ORIGIN=https://<web>.vercel.app ACME_EMAIL=<you@mail> \
#     infra/backend/deploy.sh
#
# What it does: refuses a dirty tree (only committed code ships) → copies `git archive HEAD` to
# ~/kalpakka on the VM (previous copy kept as ~/kalpakka.prev for a quick rollback) → checks the
# VM's /etc/kalpakka/backend.env exists and allows WEB_ORIGIN → docker compose up --build →
# waits for HTTPS (the first run includes certificate issuance) → infra/backend/smoke.py.
# It never copies or prints a secret: .env files are not in git, and the VM's env file stays there.
set -euo pipefail

: "${VM:?set VM=ubuntu@<public-ip>}"
: "${WEB_ORIGIN:?set WEB_ORIGIN=https://<web>.vercel.app (must be in ALLOWED_ORIGINS)}"
: "${ACME_EMAIL:?set ACME_EMAIL for certificate notices}"

cd "$(git rev-parse --show-toplevel)"
if [ -n "$(git status --porcelain)" ]; then
  echo "deploy: working tree is dirty; commit first (only committed code is deployed)" >&2
  exit 1
fi
IP="${VM#*@}"
PUBLIC_HOST="$(python3 -c 'import sys; p=sys.argv[1].split("."); assert len(p)==4 and all(x.isdigit() and int(x)<256 for x in p); print("-".join(p)+".sslip.io")' "$IP")"
REV="$(git rev-parse --short HEAD)"
echo "deploy: $REV -> $VM as https://$PUBLIC_HOST"

ssh "$VM" 'test -f /etc/kalpakka/backend.env' \
  || { echo "deploy: /etc/kalpakka/backend.env missing on the VM; see infra/README.md" >&2; exit 1; }
case "$WEB_ORIGIN" in */) echo "deploy: WEB_ORIGIN must not end with /" >&2; exit 1 ;; esac
ssh "$VM" "grep -qE '^ALLOWED_ORIGINS=(.*,)? *${WEB_ORIGIN//./\\.} *(,|\$)' /etc/kalpakka/backend.env" \
  || { echo "deploy: ALLOWED_ORIGINS on the VM does not include $WEB_ORIGIN" >&2; exit 1; }

git archive --format=tar HEAD | ssh "$VM" "set -e
  rm -rf ~/kalpakka.new && mkdir ~/kalpakka.new && tar -x -C ~/kalpakka.new
  echo $REV > ~/kalpakka.new/REVISION
  rm -rf ~/kalpakka.prev
  if [ -d ~/kalpakka ]; then mv ~/kalpakka ~/kalpakka.prev; fi
  mv ~/kalpakka.new ~/kalpakka"

ssh "$VM" "cd ~/kalpakka/infra/backend && PUBLIC_HOST=$PUBLIC_HOST ACME_EMAIL=$ACME_EMAIL \
  docker compose up -d --build --remove-orphans && docker compose ps"

echo "deploy: waiting for https://$PUBLIC_HOST/healthz (first deploy also gets the certificate)"
for i in $(seq 1 36); do
  if curl -fsS -m 10 "https://$PUBLIC_HOST/healthz" >/dev/null 2>&1; then break; fi
  [ "$i" -eq 36 ] && { echo "deploy: not healthy after 3 min; run: ssh $VM 'cd ~/kalpakka/infra/backend && docker compose logs --tail 80'" >&2; exit 1; }
  sleep 5
done

uv run --script infra/backend/smoke.py "https://$PUBLIC_HOST" --origin "$WEB_ORIGIN"
echo "deploy: done. Web env: NEXT_PUBLIC backend = wss://$PUBLIC_HOST/call · rollback: ssh $VM 'mv ~/kalpakka ~/kalpakka.bad && mv ~/kalpakka.prev ~/kalpakka && cd ~/kalpakka/infra/backend && docker compose up -d --build'"
