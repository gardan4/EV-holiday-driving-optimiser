#!/usr/bin/env bash
#
# One-command local dev: frees ports, brings up SQL Server, creates + migrates
# the database, seeds the car catalog, then runs the API and the frontend
# together. Ctrl-C stops everything cleanly.
#
#   ./scripts/dev.sh            # full stack
#   ./scripts/dev.sh --no-web   # backend + DB only
#
# Deliberately does the boring-but-load-bearing things the naive version
# skipped: waits for SQL Server to actually accept connections (cold start
# under emulation takes ~20 s), and CREATEs the database — the compose file
# only starts the server, so a fresh volume has no evtripdb-dev.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ── PATH bootstrap ───────────────────────────────────────────────────────────
# A VS Code launched from the Dock hands its tasks the bare launchd PATH
# (/usr/bin:/bin:/usr/sbin:/sbin) — no homebrew, no mise — so `docker`, `uv`
# and `npm` all vanish. Put the usual tool locations back so this script works
# the same from a task, a login shell, or a bare `sh -c`.
for _dir in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin" "$HOME/.local/share/mise/shims"; do
  case ":$PATH:" in
    *":$_dir:"*) ;;
    *) [[ -d "$_dir" ]] && PATH="$_dir:$PATH" ;;
  esac
done
export PATH
unset _dir

# mise won't resolve tools from an untrusted config — node/npm just silently
# disappear. Trusting our own repo's mise.toml is safe and idempotent.
if command -v mise >/dev/null 2>&1 && [[ -f mise.toml ]]; then
  mise trust >/dev/null 2>&1 || true
fi

API_PORT=8100
WEB_PORT=3100
DB_CONTAINER=evtrip-db-local
DB_NAME=evtripdb-dev
SA_PASSWORD='LocalDev_Passw0rd!'
RUN_WEB=1

[[ "${1:-}" == "--no-web" ]] && RUN_WEB=0

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── Port hygiene ─────────────────────────────────────────────────────────────
# Kills dev servers (ours and any other project's) on the ports we need, plus
# the 3000/8000 defaults. Only node/python processes — never Docker, which
# proxies other projects' containers on those ports.
free_ports() {
  local pids
  pids=$(lsof -nP -iTCP:3000 -iTCP:8000 -iTCP:"$API_PORT" -iTCP:"$WEB_PORT" -sTCP:LISTEN 2>/dev/null \
    | awk 'NR>1 && ($1=="node" || $1 ~ /^[Pp]ython/ || $1=="uvicorn" || $1=="next-ser") {print $2}' \
    | sort -u) || true
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 1
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  fi
}

cleanup() {
  local code=$?
  trap - INT TERM EXIT
  echo
  bold "Shutting down…"
  for pid in "${CHILD_PIDS[@]:-}"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
  sleep 1
  # Belt and braces: uvicorn's reloader and next-server fork grandchildren that
  # can outlive their parent. Killing by port catches whatever is left.
  free_ports
  info "Database container left running (docker compose -f docker-compose.local-db.yml down to stop it)."
  exit $code
}

# ── 1. Ports ─────────────────────────────────────────────────────────────────
bold "1/5  Freeing dev ports (3000, 8000, $API_PORT, $WEB_PORT)…"
free_ports
info "done"

# ── 2. Database container ────────────────────────────────────────────────────
bold "2/5  Starting SQL Server…"
command -v docker >/dev/null 2>&1 || fail "docker not found — is Docker/OrbStack installed?"
docker info >/dev/null 2>&1 || fail "Docker isn't running — start Docker Desktop / OrbStack first."
docker compose -f docker-compose.local-db.yml up -d >/dev/null
info "container $DB_CONTAINER up (host port 14330)"

# sqlcmd moved between image versions; find whichever this image ships.
SQLCMD=""
for candidate in /opt/mssql-tools18/bin/sqlcmd /opt/mssql-tools/bin/sqlcmd; do
  if docker exec "$DB_CONTAINER" test -x "$candidate" 2>/dev/null; then SQLCMD="$candidate"; break; fi
done
[[ -n "$SQLCMD" ]] || fail "sqlcmd not found inside $DB_CONTAINER"

sql() { docker exec "$DB_CONTAINER" "$SQLCMD" -S localhost -U sa -P "$SA_PASSWORD" -C -l 3 -Q "$1"; }

# Cold start under x86 emulation is slow; poll instead of guessing a sleep.
printf '  waiting for SQL Server'
ready=0
for _ in $(seq 1 60); do
  if sql "SELECT 1" >/dev/null 2>&1; then ready=1; break; fi
  printf '.'
  sleep 2
done
echo
[[ "$ready" == "1" ]] || fail "SQL Server did not become ready in 120 s (docker compose -f docker-compose.local-db.yml logs)"
info "accepting connections"

# ── 3. Database + schema ─────────────────────────────────────────────────────
bold "3/5  Preparing database…"
# The compose file only starts the server — nothing creates the database, so a
# fresh volume needs this or db_bootstrap dies with "Cannot open database".
sql "IF DB_ID('$DB_NAME') IS NULL CREATE DATABASE [$DB_NAME]" >/dev/null
info "database $DB_NAME present"

command -v uv >/dev/null 2>&1 || fail "uv not found — install it (brew install uv) or open a shell where it's on PATH."
[[ -f .env ]] || { cp .env.example .env; info "created .env from .env.example (add your ORS/OCM keys)"; }
( cd src && uv sync --quiet )
( cd src && uv run python -m scripts.db_bootstrap ) || fail "db_bootstrap failed (see the error above)"
info "schema migrated + car catalog seeded"

# ── 4. Backend ───────────────────────────────────────────────────────────────
CHILD_PIDS=()
trap cleanup INT TERM EXIT
# Deliberately NO `set -m`: leaving the children in this script's process group
# means a terminal Ctrl-C reaches them directly (uvicorn/next shut down
# gracefully) instead of only hitting the script. cleanup() then sweeps up.

bold "4/5  Starting API on http://localhost:$API_PORT …"
( cd src && exec uv run uvicorn app.main:app --reload --port "$API_PORT" ) &
CHILD_PIDS+=($!)

for _ in $(seq 1 40); do
  curl -fsS "http://localhost:$API_PORT/health" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fsS "http://localhost:$API_PORT/health" >/dev/null 2>&1 \
  && info "API healthy" \
  || info "API still starting — check the log lines above"

# ── 5. Frontend ──────────────────────────────────────────────────────────────
if [[ "$RUN_WEB" == "1" ]]; then
  bold "5/5  Starting web on http://localhost:$WEB_PORT …"
  command -v npm >/dev/null 2>&1 || fail "npm not found — install Node 20 (mise install) first."
  [[ -d frontend/node_modules ]] || ( cd frontend && npm install )
  [[ -f frontend/.env.local ]] || cp frontend/.env.local.example frontend/.env.local
  ( cd frontend && exec npm run dev -- --port "$WEB_PORT" ) &
  CHILD_PIDS+=($!)
else
  bold "5/5  Skipping web (--no-web)"
fi

echo
bold "Running — Ctrl-C to stop everything"
info "web  http://localhost:$WEB_PORT"
info "api  http://localhost:$API_PORT/docs"
echo

wait
