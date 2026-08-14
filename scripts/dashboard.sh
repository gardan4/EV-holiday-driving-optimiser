#!/usr/bin/env bash
#
# One-command local mission control: builds the console's UI if needed, makes
# sure the database is reachable, then serves the dashboard role on :8101.
#
#   ./scripts/dashboard.sh              # build if needed, then run
#   ./scripts/dashboard.sh --build      # force a UI rebuild first
#   ./scripts/dashboard.sh --build-only # build the UI and stop
#   ./scripts/dashboard.sh --prod       # …against the DEPLOYED database
#
# Still not deployed anywhere: the console runs on your machine either way, and
# `--prod` changes exactly one thing — the connection string it hands the
# process. That is safe to offer because of what the role is, not because of
# what this script does: PROCESS_ROLE=dashboard mounts the admin router and the
# built SPA and nothing else, every route on it is a SELECT, and db_bootstrap is
# ./scripts/dev.sh's job and is never run from here. Nothing this script starts
# can write to the database it connects to.
#
# Three things make `--prod` work, and the third is not in this repository:
#
#   1. DATABASE_URL_PROD in .env — the deployed connection string. Use a
#      READ-ONLY login, not the SQL admin (.env.example has the T-SQL).
#   2. This script, which passes that URL to this process alone. It is never
#      exported, so nothing else in the shell inherits it.
#   3. A SQL firewall rule for the IP you are sitting behind. Azure SQL admits
#      the App Service outbound set and nothing else, so without one the login
#      times out. `infra/main.bicep` has `devAllowedIps` for that — see
#      docs/DEPLOYMENT.md.
#
# Prod runs on 8102 rather than 8101, so a local console and a live one can sit
# open together and the URL says which is which. The console says so too: the
# filter bar carries the database it actually connected to, read off the
# connection rather than off this flag.
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
PROD_PORT=8102
DB_CONTAINER=evtrip-db-local
DB_NAME=evtripdb-dev
SA_PASSWORD='LocalDev_Passw0rd!'
STATIC_DIR="src/dashboard_static"

FORCE_BUILD=0
BUILD_ONLY=0
PROD=0
for arg in "$@"; do
  case "$arg" in
    --build) FORCE_BUILD=1 ;;
    --build-only) FORCE_BUILD=1; BUILD_ONLY=1 ;;
    --prod) PROD=1 ;;
    *) printf 'Unknown option: %s\n' "$arg" >&2; exit 2 ;;
  esac
done
# An `if`, not `[[ … ]] && DASH_PORT=…`: an AND-list whose test fails returns 1,
# and under `set -e` that is only survivable because bash exempts every element
# of such a list but the last. It works, and it is one refactor away from
# killing the script on the ordinary local path.
if [[ "$PROD" == "1" ]]; then
  DASH_PORT="$PROD_PORT"
fi

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

# The deployed connection string, for --prod only. Read the same way and for the
# same reason: DATABASE_URL_PROD contains an unquoted `&` too.
PROD_URL=""
if [[ "$PROD" == "1" ]]; then
  PROD_URL="$(grep -E '^DATABASE_URL_PROD=' .env | head -1 | cut -d= -f2- || true)"
  [[ -n "$PROD_URL" ]] || fail "DATABASE_URL_PROD is not set in .env, so there is nothing to connect to.
    It is the deployed database's URL, in the same shape as DATABASE_URL:
      mssql+aioodbc://USER:PASSWORD@SERVER.database.windows.net:1433/evtripdb-dev?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no
    Use a READ-ONLY login rather than the SQL admin — .env.example has the
    three lines of T-SQL that create one. Your IP also needs a SQL firewall
    rule (infra devAllowedIps; see docs/DEPLOYMENT.md), or the login will
    simply time out."
  case "$PROD_URL" in
    *localhost*|*127.0.0.1*)
      fail "DATABASE_URL_PROD points at localhost, which is the local database DATABASE_URL already covers.
    Drop --prod, or fix the value in .env." ;;
  esac
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

if [[ "$PROD" == "1" ]]; then
  # No Docker, no container, no schema check: the deployed database is already
  # migrated by the API's own boot, and nothing here may touch its shape.
  #
  # The login is attempted BEFORE the server starts. Without this the firewall
  # case — by far the likeliest failure — looks like a console that came up
  # fine and then answered every panel with a 500, which reads as a bug in the
  # dashboard rather than as a missing firewall rule.
  command -v uv >/dev/null 2>&1 || fail "uv not found — install it (brew install uv)."
  ( cd src && uv sync --quiet )
  info "connecting to the deployed database (read-only role)…"
  if ! ( cd src && env DATABASE_URL="$PROD_URL" uv run python -m scripts.db_ping ); then
    fail "could not reach the deployed database.
    The usual cause is the SQL firewall: Azure SQL admits the App Service
    outbound addresses and nothing else, so your machine needs its own rule.
      curl -s https://api.ipify.org                     # the IP to allow
      az deployment group create … devAllowedIps='<ip>' # see docs/DEPLOYMENT.md
    A login failure instead means the credentials in DATABASE_URL_PROD are
    wrong — check them against the read-only login you created, and a driver
    error means msodbcsql18 is missing rather than anything being unreachable.
    The line above says which of the three it was."
  fi
else
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
fi

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

if [[ "$PROD" == "1" ]]; then
  printf '\033[33m  ── the DEPLOYED database. Real trips, real people. ──\033[0m\n'
  info "read-only: this role serves the admin router and nothing that writes"
fi

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
# DATABASE_URL is passed to THIS process only — never exported, so nothing else
# in the shell, and no later command in this terminal, inherits a live
# connection string. An environment variable beats the .env file in
# pydantic-settings, which is what makes the override work without editing .env.
if [[ "$PROD" == "1" ]]; then
  exec env PROCESS_ROLE=dashboard DATABASE_URL="$PROD_URL" \
    uv run uvicorn app.main:app --port "$DASH_PORT"
fi
exec env PROCESS_ROLE=dashboard uv run uvicorn app.main:app --port "$DASH_PORT"
