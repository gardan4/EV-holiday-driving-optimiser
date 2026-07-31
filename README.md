# EV Trip Optimizer

A production-shaped full-stack starter:

- **Backend** — FastAPI (Python 3.12, `uv`), async SQLAlchemy on **Azure SQL /
  MSSQL**, Alembic migrations, `pydantic-settings`, rate limiting, structured
  JSON logging.
- **Frontend** — Next.js 16 (App Router, React 19), **Clerk** authentication,
  Tailwind CSS 4.
- **Infra** — Azure Bicep (App Service Plan, api + web App Services, Azure SQL,
  Log Analytics), GitHub Actions CI/CD to GHCR + Azure App Service.
- **Local dev** — Docker Compose (backend container + local SQL Server 2022).

It ships one working vertical slice — `User → Business (tenant) → Item` with
owner/member roles and audit logging — so a new project boots, authenticates,
and serves a real multi-tenant resource on day one. Build your product by copying
that slice (see [docs/ADDING_A_RESOURCE.md](docs/ADDING_A_RESOURCE.md)).

> **This is a template.** If you just cloned it, run **`./init.sh`** first — it
> rebrands the placeholders (`EV Trip Optimizer`, `evtrip`, …), generates
> fresh secrets, resets git history, and deletes itself.

## Quick start

```bash
# 0. (once) rebrand + generate secrets + reset git
./init.sh

# 1. Create a Clerk application (https://clerk.com) and fill the CLERK_* vars in
#    .env, plus NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY in frontend/.env.local.
#    In Clerk → Sessions → Customize session token, add the email/name claims
#    (see .env.example). The app will not render without Clerk keys.

# 2. Start a local SQL Server
docker compose -f docker-compose.local-db.yml up -d

# 3. Backend
cd src
uv sync
uv run python -m scripts.db_bootstrap     # create schema on the fresh DB
uv run uvicorn app.main:app --reload      # http://localhost:8000  (GET /health)

# 4. Frontend
cd ../frontend
npm install
npm run dev                                # http://localhost:3000
```

Toolchain versions are pinned in `mise.toml` (Python 3.12, Node 20) — run
`mise install` if you use [mise](https://mise.jdx.dev).

## Layout

```
src/          FastAPI backend (app/, alembic/, scripts/, Dockerfile, pyproject.toml)
frontend/     Next.js frontend (app/, lib/, proxy.ts, Dockerfile, package.json)
infra/        Azure Bicep + deploy scripts
.github/      CI/CD workflows (deploy-dev.yml → develop, deploy.yml → main)
docs/         ARCHITECTURE, DEPLOYMENT, ADDING_A_RESOURCE
CLAUDE.md     Working map of the codebase for AI agents (twin: AGENTS.md)
```

## Tests

```bash
cd src && uv run pytest        # add tests under src/tests or a tests/ dir
cd frontend && npx tsc --noEmit && npx next build
```

## Deployment

Two branches → two Azure environments: push `develop` → **dev**, `main` → **prod**.
Each pipeline: paths-filter → test → build container → push to GHCR →
`az webapp config container set` → restart. See
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## License

Apache-2.0 — see [LICENSE](LICENSE).
