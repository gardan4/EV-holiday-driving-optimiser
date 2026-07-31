# CLAUDE.md

Guidance for Claude Code (and any AI agent) working in this repository.

> **Keep this file and `AGENTS.md` in sync** — they are twins (one for Claude, one
> for Codex). When you change one, mirror the change in the other.

<!-- FILL IN: Product Overview -->
<!-- What is this product? Who uses it, and what does it do? Replace this block
     once you've built something on top of the skeleton. -->

## What this is

A full-stack starter: **FastAPI** (async SQLAlchemy on Azure SQL / MSSQL) +
**Next.js 16** (App Router, React 19) with **Clerk** authentication, deployed as
containers to **Azure App Service** via **Bicep** + GitHub Actions. It ships one
working vertical slice you extend:

```
User ──owns──▶ Business  (strict tenant boundary)
                   └──has──▶ Item   (example child resource)
```

## Architecture

### Backend (`src/`) — FastAPI + Python 3.12 (uv)

- **Entry point** `src/app/main.py` — process-role-aware lifespan (`PROCESS_ROLE`:
  `web`/`all` mount the API, `worker`/`all` run background loops). The skeleton
  ships **no** loops; `_start_background_loops()` is an empty, documented seam.
- **Config** `src/app/core/config.py` — `pydantic-settings`; refuses to boot with
  missing/placeholder `SECRET_KEY`/`ENCRYPTION_KEY`. In production it also refuses
  to start without `CLERK_ISSUER` + `CLERK_AUTHORIZED_PARTIES`.
- **Database** `src/app/core/database.py` — async (`aioodbc`) + sync (`pyodbc`)
  engines, auto-detects the ODBC driver, `GUID` type for MSSQL UUIDs. Do not edit
  the pool logic casually — the NullPool-on-macOS note there is load-bearing.
- **Auth** `src/app/core/clerk_auth.py` + `src/app/api/auth.py` — verifies the
  Clerk session JWT (RS256, networkless) and JIT-provisions a local `User` keyed
  by `clerk_user_id`. `GET /api/auth/me`, `POST /api/auth/onboarded`,
  `DELETE /api/auth/me` (GDPR erasure).
- **Tenancy + permissions** `src/app/api/deps.py` — `require_business_member`,
  `require_business_owner`, `require_item`, and `audit()`. Copy `require_item` for
  new nested resources.
- **Routers** `src/app/api/` — `businesses.py` (CRUD + members),
  `items.py` (the reference nested CRUD), `webhooks.py` (Clerk `user.deleted`).
- **Models** `src/app/models/__init__.py` — `User`, `Business`,
  `BusinessMembership`, `Item`, `UserAuditLog`, and the `GUID` type.
- **Migrations** `src/alembic/` — one baseline `0001_initial`. `db_bootstrap.py`
  runs `create_all` + stamp on a fresh DB, else `alembic upgrade head`.

### Frontend (`frontend/`) — Next.js 16, React 19, Tailwind 4

- `app/` — `page.tsx` (public landing), `login/` + `register/` (Clerk),
  `dashboard/` (businesses list), `businesses/[id]/` (Items + Members tabs).
- `proxy.ts` — Clerk middleware + CSP + security headers (single source of truth).
- `lib/api.ts` — auth token plumbing; `lib/client.ts` — typed API client.
- `app/_components/ui/` — Button, Card, Input, Badge primitives.

### Infra (`infra/`) + CI (`.github/workflows/`)

Bicep provisions an App Service Plan, api + web App Services (GHCR containers),
Azure SQL, and Log Analytics. Two workflows: `deploy-dev.yml` (push to `develop`)
and `deploy.yml` (push to `main`). See `docs/DEPLOYMENT.md`.

<!-- FILL IN: Domain Model -->
<!-- Describe your real entities and their relationships as you add them. -->

<!-- FILL IN: Key design decisions -->
<!-- Record the non-obvious choices future-you (and agents) should not re-litigate. -->

## Common commands

```bash
# Backend
cd src && uv sync
uv run uvicorn app.main:app --reload

# Migrations
cd src && uv run alembic upgrade head
cd src && uv run alembic revision --autogenerate -m "describe change"

# Frontend
cd frontend && npm install && npm run dev
cd frontend && npx tsc --noEmit && npx next build   # one-shot compile check

# Local SQL Server (for local dev)
docker compose -f docker-compose.local-db.yml up -d
```

## Adding a resource

Copy the `Item` slice: model in `models/__init__.py` → `api/<resource>.py` router
(mounted under `/api/businesses/{business_id}/...`) → a migration → `lib/client.ts`
block → a UI tab. Full walkthrough: `docs/ADDING_A_RESOURCE.md`.

## Working norms (load-bearing)

- **MSSQL boolean filters**: `.is_(True)` / `.is_(False)` compile to `IS 1` / `IS 0`
  — a T-SQL **syntax error**. Use `Column == True` / `== False  # noqa: E712`, or a
  string status column (as `Item.status` does).
- **Scripts that mutate the DB must guard against prod**: refuse to run unless the
  DB name contains `-dev`. (Pattern to add when you write seed/cleanup scripts.)
- **`npx tsc` only works from `frontend/`.** `npm run dev` is long-running — use
  `npx next build` for a one-shot compile check.
- **Before `git add`-ing a file, `git diff` it** — stage only what you touched;
  never `git add -A` blindly.
- **Commits**: lowercase `<type>(<scope>): <imperative summary>` — `feat`, `fix`,
  `chore`, `docs`, `style`, `test`. Bodies explain the *why*.
- `develop` is the integration branch; merging to `main` ships to prod.

## Environment

Copy `.env.example` → `.env` (init.sh does this + generates the secrets). The app
will not render without Clerk keys — create a Clerk application first and fill the
`CLERK_*` vars + the frontend `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`.
