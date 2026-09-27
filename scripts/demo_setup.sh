#!/usr/bin/env bash
# One-time setup on a new machine (Ubuntu/Debian, macOS, or Windows via WSL2), run from the unpacked
# demo folder:  ./scripts/demo_setup.sh
# Installs uv + Python 3.12, Node 20 (via nvm) and the project dependencies; builds the database and
# OCR images; restores the database dump and the data bundle; creates .env. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m!! %s\033[0m\n' "$*" >&2; exit 1; }
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

say "Checking prerequisites"
command -v curl >/dev/null || die "curl is required (Ubuntu: sudo apt install -y curl)"
command -v docker >/dev/null || die "Docker is required: install Docker Desktop (macOS/Windows) or Docker Engine
   (Ubuntu: https://docs.docker.com/engine/install/ubuntu/), then re-run this script"
docker info >/dev/null 2>&1 || die "Docker is installed but not running / not accessible (start Docker Desktop, or: sudo usermod -aG docker \$USER and log in again)"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required (included in Docker Desktop / docker-compose-plugin)"

say "uv + Python 3.12"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
uv python install 3.12
uv sync

say "Node.js 20 (nvm) + web dependencies"
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 20 ]; then
  [ -s "$HOME/.nvm/nvm.sh" ] || curl -fsSo- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
  . "$HOME/.nvm/nvm.sh"; nvm install 20; nvm use 20
fi
(cd web && npm ci)
[ -f web/.env ] || cp web/.env.example web/.env

say "Headless Chromium (PDF reports)"
uv run playwright install chromium || echo "   (PDF reports need: uv run playwright install --with-deps chromium)"

say "Configuration (.env)"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "   Created .env. Add the free API keys (GEMINI/GROQ/COHERE) to use the Agent console."
fi
if command -v aws >/dev/null && ! grep -q "sso-session fai-tce" "$HOME/.aws/config" 2>/dev/null && [ -f infra/aws-config.example ]; then
  mkdir -p "$HOME/.aws"; cat infra/aws-config.example >> "$HOME/.aws/config"
  echo "   Added the team AWS SSO profiles to ~/.aws/config (log in with: make aws-login)"
fi

say "Docker images (database, OCR tools)"
docker compose build db
docker compose --profile tools build ocr-tools

say "Database"
docker compose up -d db
for _ in $(seq 1 60); do docker compose exec -T db pg_isready -U nila -d nilasaatchi >/dev/null 2>&1 && break; sleep 1; done
N=$(docker compose exec -T db psql -U nila -d nilasaatchi -Atc "select count(*) from information_schema.tables where table_name='document'")
if [ "$N" = "0" ] && [ -f bundle/nilasaatchi.dump ]; then
  echo "   Restoring the database dump (a few minutes)..."
  # roles are not part of a dump: create the agent's read-only role first (same as migration 0003)
  docker compose exec -T db psql -U nila -d nilasaatchi -qc "DO \$\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles
    WHERE rolname='agent_ro') THEN CREATE ROLE agent_ro NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; END IF; END \$\$;
    GRANT agent_ro TO nila;"
  mkdir -p data/logs
  # "already exists" messages for the PostGIS/pgvector extensions (created by init.sql) are expected
  docker compose exec -T db pg_restore -U nila -d nilasaatchi --no-owner < bundle/nilasaatchi.dump \
    2> data/logs/restore.log || echo "   (restore warnings in data/logs/restore.log)"
elif [ "$N" = "0" ]; then
  echo "   No bundle/nilasaatchi.dump found: creating an empty schema (no data)"; uv run python scripts/migrate.py
else
  echo "   Database already has data: kept"
fi
uv run python scripts/migrate.py   # applies any migration newer than the dump (idempotent)
docker compose exec -T db psql -U nila -d nilasaatchi -Atc "select 'documents: '||count(*) from document"

say "Data files (page images, satellite tables, models)"
if [ -f bundle/data.tar.gz ] && [ ! -d data/pages ]; then tar -xzf bundle/data.tar.gz -C "$ROOT"; echo "   data/ restored"
else echo "   data/ already present (or no bundle/data.tar.gz)"; fi

say "Setup complete. Start everything with:  ./scripts/demo_run.sh   (or: make demo)"
