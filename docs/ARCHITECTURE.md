# Architecture

> Keep this in sync with the code. When docs and the file tree disagree, trust
> the tree.

## Layers

```
Browser
  │  Clerk session JWT (Authorization: Bearer <token>)
  ▼
Next.js frontend (frontend/)            proxy.ts: Clerk middleware + CSP
  │  fetchWithAuth → NEXT_PUBLIC_API_URL
  ▼
FastAPI backend (src/app/)
  ├─ core/clerk_auth.py   verify RS256 session token (networkless)
  ├─ api/auth.py          JIT-provision local User (clerk_user_id)
  ├─ api/deps.py          require_business_member / _owner / _item + audit()
  ├─ api/businesses.py    tenant CRUD + membership
  ├─ api/items.py         example nested resource (copy me)
  └─ core/database.py     async (aioodbc) + sync (pyodbc) engines
  ▼
Azure SQL (MSSQL)         schema via Alembic / create_all
```

## Request flow (an authenticated API call)

1. The frontend calls `fetchWithAuth("/api/...")` (`lib/api.ts`), which attaches
   the live Clerk session JWT as a bearer token.
2. `get_current_user` (`api/auth.py`) verifies the token against the Clerk PEM
   public key (RS256, no network round-trip) and loads/provisions the local
   `User` row keyed by `clerk_user_id`.
3. A route dependency (`require_business_member` / `require_item`) resolves the
   tenant and 404s archived/foreign rows — this is the tenancy boundary.
4. The handler reads/writes via the async SQLAlchemy session (`get_db`), records
   an audit row via `audit()`, and commits.

## Tenancy model

`Business` is the strict tenant boundary — zero cross-business sharing. A `User`
can own many businesses. `BusinessMembership.role` is `owner` (full access; the
founder is always an implicit owner) or `member`. Child resources (`Item`, and
anything you add) are always resolved *under* a business, so a query can never
leak across tenants.

## Process-role seam

`PROCESS_ROLE` (`web` / `worker` / `all`) selects which half of the app a process
runs. The skeleton has no background loops, so `_start_background_loops()` in
`main.py` returns `[]`. To add async work: implement a loop, return its task from
that function, and run a dedicated process with `PROCESS_ROLE=worker` (and add a
worker App Service to the Bicep). `web`/`all` mount the API; `worker`/`all` run
the loops.

## Data & migrations

Models live in `src/app/models/__init__.py` (all UUID ids use the `GUID`
CHAR(36) type for MSSQL). The single baseline migration is
`src/alembic/versions/0001_initial.py`. On a fresh DB, `scripts/db_bootstrap.py`
runs `create_all` + stamps the baseline; on an established DB it runs
`alembic upgrade head`. After changing models, generate an incremental migration:
`cd src && uv run alembic revision --autogenerate -m "..."`.
