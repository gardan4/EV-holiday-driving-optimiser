#!/usr/bin/env bash
#
# Let THIS machine through the Azure SQL firewall, so the local console can read
# production (./scripts/dashboard.sh --remote).
#
#   ./scripts/sql_allow_me.sh            # add/update the rule for your current IP
#   ./scripts/sql_allow_me.sh --remove   # take it away again
#   ./scripts/sql_allow_me.sh --show     # what is allowed right now
#
# Why this is not a Bicep parameter. `sqlAllowedIps` in infra/main.bicep is
# resolved by the infra job FROM THE LIVE APP SERVICE immediately before
# deploying — it is the App Service's outbound set, machine-derived on purpose
# so nobody hand-maintains nineteen addresses. Putting a laptop in there would
# be wiped by the next resolve, and would make the source claim an App Service
# IP that is really somebody's kitchen table. So the developer rule lives beside
# it under its own name: Bicep deployments are incremental, so a rule the
# template does not mention is left alone rather than deleted.
#
# It is also the honest shape for the thing being modelled — a laptop IP is not
# infrastructure. It changes when you move to a café, and the fix is to run this
# again rather than to edit and redeploy a template.
#
# **This opens your database to an address on the public internet.** It is one
# IP, it is named after you, and `--remove` is instant — but it is a real change
# to a real firewall, so it is a deliberate command and not something the
# dashboard does for you on startup.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Same PATH repair as the other scripts: a Dock-launched VS Code hands its tasks
# the bare launchd PATH, so `az` is not on it.
for _dir in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin" "$HOME/.local/share/mise/shims"; do
  case ":$PATH:" in
    *":$_dir:"*) ;;
    *) [[ -d "$_dir" ]] && PATH="$_dir:$PATH" ;;
  esac
done
export PATH
unset _dir

RG="${AZ_RESOURCE_GROUP:-evtrip-dev-rg}"
SERVER="${AZ_SQL_SERVER:-evtrip-sql-dev}"
# One rule per machine, not one per person: two laptops on the same account
# would otherwise fight over a single rule and silently lock each other out.
RULE="dev-$(scutil --get LocalHostName 2>/dev/null || hostname -s)"
# Azure firewall rule names are limited and fussy about punctuation.
RULE="$(printf '%s' "$RULE" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9-' '-' | cut -c1-60)"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

command -v az >/dev/null 2>&1 || fail "az not found — brew install azure-cli, then 'az login'."
az account show >/dev/null 2>&1 || fail "not signed in to Azure — run 'az login' first."

case "${1:-}" in
  --show)
    bold "Firewall rules on ${SERVER}:"
    az sql server firewall-rule list -g "$RG" -s "$SERVER" \
      --query "[].{name:name, start:startIpAddress, end:endIpAddress}" -o table
    exit 0
    ;;
  --remove)
    bold "Removing ${RULE} from ${SERVER}…"
    az sql server firewall-rule delete -g "$RG" -s "$SERVER" -n "$RULE" >/dev/null
    info "gone — this machine can no longer reach production SQL."
    exit 0
    ;;
  "") ;;
  *) fail "Unknown option: $1 (use --remove or --show)" ;;
esac

# Ask Azure what it sees, not what the machine thinks it is: behind NAT, a VPN
# or CGNAT, the address the server will actually check is not one this host
# knows. `ipify` is the fallback for when the error text changes shape.
bold "Finding this machine's public IP…"
IP="$(curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)"
[[ "$IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || fail "could not determine your public IP (are you online?)"
info "$IP"

bold "Allowing it through ${SERVER} as ${RULE}…"
# create is an upsert here: run it again after switching networks and the rule
# moves to the new address rather than accumulating.
az sql server firewall-rule create \
  -g "$RG" -s "$SERVER" -n "$RULE" \
  --start-ip-address "$IP" --end-ip-address "$IP" >/dev/null
info "done"
echo
info "Now: ./scripts/dashboard.sh --remote   (or ⇧⌘P → Mission control: production)"
info "When you're finished: ./scripts/sql_allow_me.sh --remove"
