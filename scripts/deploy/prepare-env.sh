#!/usr/bin/env bash
# Prepare .env for deployment. Run from repo root: bash scripts/deploy/prepare-env.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [[ ! -f .env.example ]]; then
  echo ".env.example not found" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
else
  echo ".env exists — will fill HOST_SYNC_SECRET / HEALTH_PORT if empty"
fi

if grep -q '^HOST_SYNC_SECRET=REPLACE_ME' .env 2>/dev/null; then
  if command -v openssl >/dev/null 2>&1; then
    SECRET="$(openssl rand -hex 32)"
  else
    SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  fi
  sed -i.bak "s/^HOST_SYNC_SECRET=REPLACE_ME/HOST_SYNC_SECRET=$SECRET/" .env
  rm -f .env.bak
  echo "Generated HOST_SYNC_SECRET"
fi

if ! grep -q '^HEALTH_PORT=' .env; then
  echo 'HEALTH_PORT=8080' >> .env
  echo "Added HEALTH_PORT=8080"
elif grep -q '^HEALTH_PORT=$' .env; then
  sed -i.bak 's/^HEALTH_PORT=$/HEALTH_PORT=8080/' .env
  rm -f .env.bak
  echo "Set HEALTH_PORT=8080"
fi

echo ""
echo "Next: edit .env (tokens, ADMIN_IDS, CHANNEL_ID)"
echo "Docker: docker compose up -d"
echo "Health: curl -s http://127.0.0.1:8080/healthz"
