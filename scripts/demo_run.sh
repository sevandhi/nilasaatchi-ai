#!/usr/bin/env bash
# Start the whole application: database (docker) -> API (:8000) -> web UI (:5173), then open the browser.
# Ctrl-C stops the API and the UI (the database container keeps running; `docker compose stop db` to stop it).
set -euo pipefail
cd "$(dirname "$0")/.."
LOG=data/logs; mkdir -p "$LOG"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
[ -s "$HOME/.nvm/nvm.sh" ] && . "$HOME/.nvm/nvm.sh"

say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
wait_http() {  # url, name, seconds
  for _ in $(seq 1 "$3"); do curl -sf -o /dev/null "$1" && return 0; sleep 1; done
  echo "!! $2 did not start in $3 s; see $LOG/" >&2; return 1
}
port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

say "1/3 database (PostGIS, port 5439)"
docker compose up -d db >/dev/null
for _ in $(seq 1 60); do docker compose exec -T db pg_isready -U nila -d nilasaatchi >/dev/null 2>&1 && break; sleep 1; done
docker compose exec -T db pg_isready -U nila -d nilasaatchi

PIDS=()
cleanup() { say "stopping API and UI"; for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup INT TERM EXIT

say "2/3 API (http://localhost:8000)"
if port_busy 8000; then echo "port 8000 already in use: assuming the API is already running"
else
  uv run uvicorn app.api.main:app --port 8000 > "$LOG/api.log" 2>&1 & PIDS+=($!)
fi
wait_http http://localhost:8000/health API 90

say "3/3 web UI (http://localhost:5173)"
if port_busy 5173; then echo "port 5173 already in use: assuming the UI is already running"
else
  (cd web && npm run dev -- --port 5173 --strictPort) > "$LOG/web.log" 2>&1 & PIDS+=($!)
fi
wait_http http://localhost:5173 "web UI" 90

say "NilaSaatchi AI is running:  http://localhost:5173   (API docs: http://localhost:8000/docs)"
echo "Logs: $LOG/api.log, $LOG/web.log.  Press Ctrl-C to stop."
if command -v xdg-open >/dev/null; then xdg-open http://localhost:5173 >/dev/null 2>&1 || true
elif command -v open >/dev/null; then open http://localhost:5173 || true; fi
wait
