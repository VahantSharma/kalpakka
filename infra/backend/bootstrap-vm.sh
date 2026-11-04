#!/usr/bin/env bash
# One-time setup of the backend VM (Ubuntu 24.04 LTS, AWS ap-south-1). Run ON the VM as `ubuntu`:
#   scp infra/backend/bootstrap-vm.sh infra/backend/backend.env.example ubuntu@<ip>:~ && ssh ubuntu@<ip> 'bash bootstrap-vm.sh'
# AWS security group (set in the console, not here): inbound 22/tcp from your IP only,
# 80/tcp and 443/tcp from anywhere (certificates + users), 443/udp (HTTP/3, optional).
set -euo pipefail

sudo apt-get update -y
sudo apt-get install -y docker.io docker-compose-v2 curl
# BuildKit honours Dockerfile.dockerignore; package availability on 24.04 is UNVERIFIED, so optional.
sudo apt-get install -y docker-buildx || echo "bootstrap: docker-buildx not available; builds still work"
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

# Secrets file: readable by this user only (compose reads env_file as the invoking user).
sudo install -d -m 755 /etc/kalpakka
if [ ! -f /etc/kalpakka/backend.env ]; then
  sudo install -m 600 -o "$USER" -g "$USER" ~/backend.env.example /etc/kalpakka/backend.env
  echo "bootstrap: fill in /etc/kalpakka/backend.env now:  nano /etc/kalpakka/backend.env"
fi

# Swap: Python deps build fine in 2 GB, but a little swap stops a rare OOM during docker build.
if ! swapon --show | grep -q .; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "bootstrap: done. Log out and back in once (docker group), then run deploy.sh from the laptop."
