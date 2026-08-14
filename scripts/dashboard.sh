#!/usr/bin/env bash
#
# One-command local mission control: builds the console's UI if needed, makes
# sure the database is reachable, then serves the dashboard role on :8101.
#
#   ./scripts/dashboard.sh              # build if needed, then run
#   ./scripts/dashboard.sh --build      # force a UI rebuild first
#   ./scripts/dashboard.sh --build-only # build the UI and stop
#   ./scripts/dashboard.sh --remote     # …against PRODUCTION's database
#
# Local only, by choice: the console is not deployed anywhere. It reads
# DATABASE_URL from .env, so pointing it at production is a matter of what that
# variable says — nothing here needs a second App Service.
#
# `--remote` is that, made safe and repeatable. Three things make it worth a
# flag rather than a note in a README:
#
#   * The connection string is resolved from the API App Service at run time
#     (`az webapp config appsettings list`) rather than copied into .env. A
#     second copy of a production database password on a laptop is a thing that
#     goes stale, gets committed, or gets pasted — and az login is an
#     authorisation gate that a file on disk is not. `PROD_DATABASE_URL` in
#     .env is honoured as an override for anyone without the Azure CLI.
#   * Azure SQL only admits the App Service outbound IPs, so this fails until
#     ./scripts/sql_allow_me.sh has let your machine through. The failure is
#     otherwise a wall of ODBC text, so it is caught here and named.
#   * The dashboard role does not migrate anything (see `_run_common_startup`),
#     which is what makes reading production from a checkout that is ahead of
#     the deployed schema a read rather than an accident.
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

# Where production lives. Overridable so this script does not become the only
# place the environment is named.
AZ_RESOURCE_GROUP="${AZ_RESOURCE_GROUP:-evtrip-dev-rg}"
AZ_API_APP="${AZ_API_APP:-evtrip-api-dev}"

FORCE_BUILD=0
BUILD_ONLY=0
REMOTE=0
for arg in "$@"; do
  case "$arg" in
    --build) FORCE_BUILD=1 ;;
    --build-only) FORCE_BUILD=1; BUILD_ONLY=1 ;;
    --remote) REMOTE=1 ;;
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
REMOTE_DB_URL=""
if [[ "$REMOTE" == "1" ]]; then
  bold "2/4  Production database…"

  # An override for a machine without the Azure CLI. Read with grep, never
  # `source` — the URL contains an unquoted `&`.
  REMOTE_DB_URL="$(grep -E '^PROD_DATABASE_URL=' .env | head -1 | cut -d= -f2- || true)"

  if [[ -z "$REMOTE_DB_URL" ]]; then
    command -v az >/dev/null 2>&1 || fail "az not found — brew install azure-cli then 'az login',
    or put PROD_DATABASE_URL=… in .env if you'd rather not use the CLI."
    az account show >/dev/null 2>&1 || fail "not signed in to Azure — run 'az login' first."
    info "resolving the connection string from ${AZ_API_APP}…"
    REMOTE_DB_URL="$(az webapp config appsettings list \
      -g "$AZ_RESOURCE_GROUP" -n "$AZ_API_APP" \
      --query "[?name=='DATABASE_URL'].value | [0]" -o tsv 2>/dev/null || true)"
    [[ -n "$REMOTE_DB_URL" && "$REMOTE_DB_URL" != "None" ]] \
      || fail "no DATABASE_URL app setting on ${AZ_API_APP} in ${AZ_RESOURCE_GROUP}."
  fi

  # Everything printed about the connection is host and database only. The
  # password is in this variable and must not reach a terminal, a screenshot or
  # a CI log.
  redacted="$(printf '%s' "$REMOTE_DB_URL" | sed -E 's#//[^@]*@#//…@#')"
  info "${redacted%%\?*}"

  # Fail here, in one legible sentence, rather than as ODBC noise inside a panel
  # once the browser is already open. A blocked IP and a wrong password look
  # nothing alike in this output and identically alike in the UI.
  bold "      checking the firewall lets this machine in…"
  probe="$(cd src && DATABASE_URL="$REMOTE_DB_URL" uv run --quiet python - <<'PY' 2>&1 || true
import asyncio

from sqlalchemy import text

from app.core.database import AsyncSessionLocal

# Bounded, because the common failure does not fail. A blocked IP is dropped
# rather than refused, so the driver sits in its own connect-retry for the best
# part of a minute with nothing on screen — which reads as a hung script, and
# the one thing worse than a bad error message is no output at all.
TIMEOUT_S = 20


async def probe() -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT 1"))


async def main() -> None:
    try:
        await asyncio.wait_for(probe(), TIMEOUT_S)
        print("OK")
    except asyncio.TimeoutError:
        print("TIMEOUT")
    except Exception as exc:  # noqa: BLE001 — the message IS the output
        print(f"ERR {exc}"[:400])


asyncio.run(main())
PY
)"
  if [[ "$probe" != OK* ]]; then
    if [[ "$probe" == TIMEOUT* ]] \
       || printf '%s' "$probe" | grep -qi "not allowed to access the server\|sp_set_firewall"; then
      fail "Azure SQL is not letting this machine in (it drops a blocked IP
    rather than refusing it, so this looks like a timeout).
    Let yourself through:  ./scripts/sql_allow_me.sh
    (⇧⌘P → \"Azure SQL: allow my IP\"). Then run this again."
    fi
    fail "could not reach the production database:
    ${probe}"
  fi
  info "connected"

  # One loud line, because every number on the screen after this is real and
  # some of them are somebody's holiday.
  printf '\033[33m  ⚠  PRODUCTION data. The console only reads, but treat the screen accordingly.\033[0m\n'
else
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

if [[ "$REMOTE" == "1" ]]; then
  bold "4/4  Starting mission control on http://localhost:$DASH_PORT (PRODUCTION data)…"
else
bold "4/4  Starting mission control on http://localhost:$DASH_PORT …"
fi
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
if [[ "$REMOTE" == "1" ]]; then
  # Passed through the environment, never written anywhere. `env` keeps it off
  # this shell's exported set too, so nothing else started from here inherits a
  # production credential.
  exec env PROCESS_ROLE=dashboard DATABASE_URL="$REMOTE_DB_URL" \
    uv run uvicorn app.main:app --port "$DASH_PORT"
fi
exec env PROCESS_ROLE=dashboard uv run uvicorn app.main:app --port "$DASH_PORT"
