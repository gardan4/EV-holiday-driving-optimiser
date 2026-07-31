#!/usr/bin/env bash
#
# One-time template bootstrap. Rewrites the __PROJECT_*__ placeholders across the
# repo, generates fresh secrets into .env, resets git history, and deletes itself.
#
# Usage:
#   ./init.sh [--name "My Project"] [--slug my-project] [--domain example.com] \
#             [--owner github-user] [--email noreply@example.com] [--yes]
#
# Anything not passed as a flag is prompted for (unless --yes).

set -euo pipefail

# ── Guards ───────────────────────────────────────────────────────────────────
if [[ -f ".template-initialized" ]]; then
  echo "This repo has already been initialized (.template-initialized exists). Aborting." >&2
  exit 1
fi
if ! grep -rIlq "__PROJECT_SLUG__" . --exclude-dir=.git --exclude-dir=node_modules 2>/dev/null; then
  echo "No __PROJECT_*__ placeholders found — is this really a fresh template clone? Aborting." >&2
  exit 1
fi
command -v python3 >/dev/null 2>&1 || { echo "python3 is required (for secret generation). Aborting." >&2; exit 1; }

# ── Args ─────────────────────────────────────────────────────────────────────
NAME=""; SLUG=""; DOMAIN=""; OWNER=""; EMAIL=""; ASSUME_YES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)   NAME="$2"; shift 2 ;;
    --slug)   SLUG="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --owner)  OWNER="$2"; shift 2 ;;
    --email)  EMAIL="$2"; shift 2 ;;
    --yes)    ASSUME_YES=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

prompt() {  # prompt <var> <question> <default>
  local __var="$1" __q="$2" __def="$3" __ans
  if [[ -n "${!__var}" ]]; then return; fi
  if [[ "$ASSUME_YES" == "1" ]]; then printf -v "$__var" '%s' "$__def"; return; fi
  read -r -p "$__q${__def:+ [$__def]}: " __ans || true
  printf -v "$__var" '%s' "${__ans:-$__def}"
}

# Derive a default slug from the name (lowercase, spaces/underscores → hyphens).
default_slug() { echo "$1" | tr '[:upper:]' '[:lower:]' | tr ' _' '--' | sed 's/[^a-z0-9-]//g'; }

prompt NAME  "Project display name" "My App"
[[ -z "$SLUG" ]] && SLUG="$(default_slug "$NAME")"
prompt SLUG  "Project slug (kebab-case; Azure + Clerk safe)" "$SLUG"
prompt DOMAIN "Public domain" "${SLUG}.com"
prompt OWNER  "GitHub owner (for GHCR image names)" "your-github-user"
prompt EMAIL  "No-reply / sender email" "noreply@${DOMAIN}"

# ── Validate ─────────────────────────────────────────────────────────────────
if ! [[ "$SLUG" =~ ^[a-z][a-z0-9-]{1,30}$ ]]; then
  echo "Invalid slug '$SLUG' — must match ^[a-z][a-z0-9-]{1,30}\$ (Azure resource + Clerk safe)." >&2
  exit 1
fi

echo
echo "  Name   : $NAME"
echo "  Slug   : $SLUG"
echo "  Domain : $DOMAIN"
echo "  Owner  : $OWNER"
echo "  Email  : $EMAIL"
echo
if [[ "$ASSUME_YES" != "1" ]]; then
  read -r -p "Proceed? [y/N] " ok || true
  [[ "${ok:-}" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }
fi

# ── Token rewrite (all text files; skip binaries, node_modules, this script, .env) ──
# bash 3.2 compatible (macOS default) — no mapfile; NUL-delimited find loop.
export NAME SLUG DOMAIN OWNER EMAIL
while IFS= read -r -d '' f; do
  case "$f" in
    ./init.sh|./.env|*.png|*.ico|*.jpg|*.jpeg|*.svg|*.woff|*.woff2|*.lock|*/uv.lock|*/package-lock.json) continue ;;
  esac
  perl -pi -e '
    s/__PROJECT_NAME__/$ENV{NAME}/g;
    s/__PROJECT_SLUG__/$ENV{SLUG}/g;
    s/__PROJECT_DOMAIN__/$ENV{DOMAIN}/g;
    s/__GITHUB_OWNER__/$ENV{OWNER}/g;
    s/__NOREPLY_EMAIL__/$ENV{EMAIL}/g;
  ' "$f"
done < <(find . -type f -not -path './.git/*' -not -path '*/node_modules/*' -not -path '*/.next/*' -not -path '*/.venv/*' -print0)

# ── .env with generated secrets ──────────────────────────────────────────────
if [[ ! -f .env ]]; then
  NAME="$NAME" SLUG="$SLUG" DOMAIN="$DOMAIN" OWNER="$OWNER" EMAIL="$EMAIL" \
    perl -pe 's/__PROJECT_NAME__/$ENV{NAME}/g; s/__PROJECT_SLUG__/$ENV{SLUG}/g; s/__PROJECT_DOMAIN__/$ENV{DOMAIN}/g; s/__GITHUB_OWNER__/$ENV{OWNER}/g; s/__NOREPLY_EMAIL__/$ENV{EMAIL}/g;' \
    .env.example > .env
  SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  ENCRYPTION_KEY="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' 2>/dev/null || python3 -c 'import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
  SECRET_KEY="$SECRET_KEY" ENCRYPTION_KEY="$ENCRYPTION_KEY" perl -pi -e '
    s/^SECRET_KEY=.*$/"SECRET_KEY=".$ENV{SECRET_KEY}/e;
    s/^ENCRYPTION_KEY=.*$/"ENCRYPTION_KEY=".$ENV{ENCRYPTION_KEY}/e;
  ' .env
  echo "Wrote .env with freshly generated SECRET_KEY + ENCRYPTION_KEY."
fi

# ── Reset git history ────────────────────────────────────────────────────────
rm -rf .git
git init -q -b main
git add -A
git commit -q -m "chore: initialize $NAME from template"

# ── Self-destruct ────────────────────────────────────────────────────────────
touch .template-initialized
git rm -q --cached init.sh >/dev/null 2>&1 || true
rm -f init.sh

cat <<EOF

Done. $NAME is initialized.

Next steps:
  1. Create a Clerk application and fill CLERK_* in .env + NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
     in frontend/.env.local (see .env.example).
  2. docker compose -f docker-compose.local-db.yml up -d      # local SQL Server
  3. cd src && uv sync && uv run python -m scripts.db_bootstrap && uv run uvicorn app.main:app --reload
  4. cd frontend && npm install && npm run dev

(init.sh has removed itself. The initial commit does not include it.)
EOF
