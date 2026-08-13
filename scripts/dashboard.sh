#!/usr/bin/env bash
#
# One-command local mission control: builds the console's UI if needed, makes
# sure the database is reachable, then serves the dashboard role on :8101.
#
#   ./scripts/dashboard.sh              # build if needed, then run
#   ./scripts/dashboard.sh --build      # force a UI rebuild first
#   ./scripts/dashboard.sh --build-only # build the UI and stop
#
# Local only, by choice: the console is not deployed anywhere. It reads
# DATABASE_URL from .env, so pointing it at production is a matter of what that
# variable says — nothing here needs a second App Service.
#
# The password is STATS_TOKEN from .env. An empty one means every route 404s
# (default-deny), which is why this script refuses to start without it rather
# than letting you meet a blank page and guess why.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ── PATH bootstrap ───────────────────────────────────────────────────────────
# A VS Code launched from the Dock hands its tasks the bare launchd PATH
# (/usr/bin:/bin:/usr/sbin:/sbin) — no homebrew, no mise — so `docker`, `uv`
# and `npm` all vanish. Same repair as scripts/dev.sh, for the same reason.
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

DASH_PORT=8101
DB_CONTAINER=evtrip-db-local
DB_NAME=evtripdb-dev
SA_PASSWORD='LocalDev_Passw0rd!'
STATIC_DIR="src/dashboard_static"

FORCE_BUILD=0
BUILD_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --build) FORCE_BUILD=1 ;;
    --build-only) FORCE_BUILD=1; BUILD_ONLY=1 ;;
    *) printf 'Unknown option: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 1. The token ─────────────────────────────────────────────────────────────
# Read with grep, never `source`: DATABASE_URL contains an unquoted `&`, so
# sourcing .env aborts the shell's parse at that line and silently empties
# every variable after it.
[[ -f .env ]] || fail ".env not found — copy .env.example to .env first."
STATS_TOKEN="$(grep -E '^STATS_TOKEN=' .env | head -1 | cut -d= -f2- || true)"
if [[ -z "$STATS_TOKEN" ]]; then
  fail "STATS_TOKEN is empty in .env — the dashboard default-denies without it (every route 404s).
    Generate one:  python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
    then add it to .env as STATS_TOKEN=…  (it is also the sign-in password)."
fi

# ── 2. The UI bundle ─────────────────────────────────────────────────────────
bold "1/4  Dashboard UI…"
command -v npm >/dev/null 2>&1 || fail "npm not found — install Node 20 (mise install) first."
if [[ "$FORCE_BUILD" == "1" || ! -f "$STATIC_DIR/index.html" ]]; then
  [[ -d dashboard/node_modules ]] || ( cd dashboard && npm install )
  ( cd dashboard && npm run build ) || fail "the dashboard UI failed to build (see above)"
  info "built into $STATIC_DIR"
else
  info "already built ($STATIC_DIR) — pass --build to rebuild"
fi

if [[ "$BUILD_ONLY" == "1" ]]; then
  bold "Done."
  exit 0
fi

# ── 3. Database ──────────────────────────────────────────────────────────────
# The console only reads, and it reads through the same services the API uses,
# so it needs the schema to exist — but it must never migrate it (see the
# Dockerfile CMD note). Starting the container is enough; db_bootstrap is
# ./scripts/dev.sh's job.
bold "2/4  Database…"
command -v docker >/dev/null 2>&1 || fail "docker not found — is Docker/OrbStack installed?"
docker info >/dev/null 2>&1 || fail "Docker isn't running — start Docker Desktop / OrbStack first."

if ! docker ps --format '{{.Names}}' | grep -qx "$DB_CONTAINER"; then
  docker compose -f docker-compose.local-db.yml up -d >/dev/null
  info "started $DB_CONTAINER"
fi

SQLCMD=""
for candidate in /opt/mssql-tools18/bin/sqlcmd /opt/mssql-tools/bin/sqlcmd; do
  if docker exec "$DB_CONTAINER" test -x "$candidate" 2>/dev/null; then SQLCMD="$candidate"; break; fi
done
[[ -n "$SQLCMD" ]] || fail "sqlcmd not found inside $DB_CONTAINER"

# Cold start under x86 emulation is slow; poll instead of guessing a sleep.
printf '  waiting for SQL Server'
ready=0
for _ in $(seq 1 60); do
  if docker exec "$DB_CONTAINER" "$SQLCMD" -S localhost -U sa -P "$SA_PASSWORD" -C -l 3 \
       -Q "SELECT 1" >/dev/null 2>&1; then ready=1; break; fi
  printf '.'
  sleep 2
done
echo
[[ "$ready" == "1" ]] || fail "SQL Server did not become ready in 120 s (docker compose -f docker-compose.local-db.yml logs)"

if ! docker exec "$DB_CONTAINER" "$SQLCMD" -S localhost -U sa -P "$SA_PASSWORD" -C -l 3 \
     -Q "SET NOCOUNT ON; SELECT DB_ID('$DB_NAME')" 2>/dev/null | grep -qE '[0-9]'; then
  fail "database $DB_NAME does not exist yet — run ./scripts/dev.sh once to create and migrate it."
fi
info "$DB_NAME reachable"

# ── 4. Serve ─────────────────────────────────────────────────────────────────
# Braces are load-bearing: `$DASH_PORT…` puts the variable name straight
# against a multi-byte ellipsis, and in a UTF-8 locale bash reads those bytes
# as part of the identifier — so `set -u` kills the script with
# "DASH_PORT<junk>: unbound variable". It survives in the C locale, which is
# exactly what a bare `env -i` test gives you, so this hid from the PATH check.
bold "3/4  Freeing port ${DASH_PORT}…"
pids=$(lsof -nP -iTCP:"$DASH_PORT" -sTCP:LISTEN 2>/dev/null \
  | awk 'NR>1 && ($1 ~ /^[Pp]ython/ || $1=="uvicorn") {print $2}' | sort -u) || true
if [[ -n "$pids" ]]; then
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 1
fi
info "done"

bold "4/4  Starting mission control on http://localhost:$DASH_PORT …"
command -v uv >/dev/null 2>&1 || fail "uv not found — install it (brew install uv)."
( cd src && uv sync --quiet )

# Open the browser once the server answers, without blocking startup.
(
  for _ in $(seq 1 40); do
    curl -fsS "http://localhost:$DASH_PORT/health" >/dev/null 2>&1 && break
    sleep 0.5
  done
  command -v open >/dev/null 2>&1 && open "http://localhost:$DASH_PORT" || true
) &

echo
info "sign in with STATS_TOKEN from .env"
info "Ctrl-C to stop"
echo

cd src
exec env PROCESS_ROLE=dashboard uv run uvicorn app.main:app --port "$DASH_PORT"
