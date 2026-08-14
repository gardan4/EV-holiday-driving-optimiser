# Deployment

**One environment.** This is a PoC: `main` is the only shipping branch and `dev`
is the only Azure environment.

| Branch | Env   | Workflow                       |
|--------|-------|--------------------------------|
| `main` | `dev` | `.github/workflows/deploy.yml` |

The pipeline: **paths-filter** (`src/**` vs `frontend/**`) → **test** (backend
smoke-import + `pytest`; frontend `tsc` + `next build`) → **build** container
images → **push to GHCR** → **`az webapp config container set`** +
**`az webapp restart`**.

Everything from "push to GHCR" onward is **skipped while the `AZURE_*` secrets
are unset**, so the pipeline is green (tests + builds still run and still gate
merges) until you actually provision Azure. Add the secrets and the same
pipeline starts deploying — no workflow edit needed.

## Azure resources (Bicep — `infra/main.bicep`)

Provisioned as `evtrip-{kind}-dev`:

| Resource            | Name                              |
|---------------------|-----------------------------------|
| Log Analytics       | `evtrip-logs-dev`     |
| App Service Plan    | `evtrip-plan-dev`     |
| API App Service     | `evtrip-api-dev`      |
| Web App Service     | `evtrip-web-dev`      |
| SQL Server          | `evtrip-sql-dev`      |
| SQL Database        | `evtripdb-dev`        |

Container images: `ghcr.io/gardan4/evtrip-api` and
`-web`. There is **no worker tier** (the skeleton ships no background loops) —
add one to the Bicep + CI if you introduce `PROCESS_ROLE=worker` work.

## First-time setup

1. **Resource group** — `az group create -n <rg> -l <location>`.
2. **GitHub OIDC** — create an Entra app registration with a federated credential
   for this repo, and grant it Contributor on the resource group. Put its ids in
   repo secrets (below). See the Azure "Configure a federated identity credential"
   docs.
3. **Repo secrets** (Settings → Secrets and variables → Actions):
   `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
   `RESOURCE_GROUP`, `GHCR_TOKEN` (a PAT with `write:packages`).
   Until `AZURE_CLIENT_ID` exists the pipeline runs tests/builds and skips the
   deploy steps.

   For the **infra** job, additionally: `SQL_ADMIN_PASSWORD`, `APP_SECRET_KEY`,
   `APP_ENCRYPTION_KEY`, `ORS_API_KEY`, `OCM_API_KEY`. These must match what the
   running app already has — the Bicep template writes them into App Service
   settings, and ARM treats an omitted `@secure()` param as an empty string, so
   a deploy missing one *succeeds* and blanks the setting. That has happened
   here (empty ORS/OCM keys reached production), which is why the job refuses to
   run rather than deploying a partial set.
4. **Provision infra.** After the secrets above exist, CI owns this: any push
   touching `infra/**` runs `what-if` (the diff goes to the job log) and then
   applies the template. A `workflow_dispatch` run applies it too.

   The first provision still has to be by hand, because the OIDC app needs a
   resource group to be Contributor on before it can deploy into one:

   ```bash
   az deployment group create \
     -g <rg> -f infra/main.bicep -p infra/main.parameters.json \
     -p environmentName=dev sqlAdminPassword=<pw> \
        secretKey=<32-byte> encryptionKey=<fernet> \
        orsApiKey=<openrouteservice-key> ocmApiKey=<openchargemap-key> \
        githubOwner=gardan4 ghcrToken=<pat>
   ```

   Secrets are passed as `@secure()` params and land in App Service settings
   (encrypted at rest) — there is no Key Vault in the default template.

5. **Push** to `main` → CI tests, builds, pushes, and deploys.

## Local build sanity

```bash
docker build -t evtrip-api ./src
docker build -t evtrip-web ./frontend
```

## Reading the deployed database from the admin console

The console (`./scripts/dashboard.sh`, VS Code: "Mission control") runs on your
machine and reads whatever `DATABASE_URL` it is given. `--prod` gives it the
deployed one, on port 8102 so a local console and a live one can sit open at
once, and the filter bar carries the host it actually connected to.

It is read-only by role rather than by promise: `PROCESS_ROLE=dashboard` mounts
the admin router and the built SPA and none of the public API routers, every
route on it is a SELECT, and `db_bootstrap` is never run from that script. Use a
read-only login anyway — the credential is the part that still holds when
somebody changes the role.

Three steps, and only the first is in this repository:

1. **A read-only login**, created once against the deployed database as admin:

   ```sql
   CREATE USER dashboard_ro WITH PASSWORD = '…';
   ALTER ROLE db_datareader ADD MEMBER dashboard_ro;
   -- deliberately no db_datawriter and no db_ddladmin
   ```

2. **`DATABASE_URL_PROD` in `.env`**, in the same shape as `DATABASE_URL` but
   with that login and the Azure host. It is read by the dashboard script and
   nothing else, and passed to that one process rather than exported.

3. **A SQL firewall rule for your address.** Azure SQL admits the App Service
   outbound set and nothing else, so without one the login simply times out:

   ```bash
   curl -s https://api.ipify.org        # the address to allow
   az deployment group create \
     --resource-group "$RG" \
     --template-file infra/main.bicep \
     --parameters infra/main.parameters.json \
     --parameters devAllowedIps='<that address>' …
   ```

   `devAllowedIps` is empty by default and CI never passes it, so deploying
   opens nothing on its own. Take yours out when you are done: a home address is
   not static, and a stale rule is a door left open for whoever holds it next.
   Adding the rule in the portal instead would work and is what this parameter
   exists to prevent — deployments are incremental, so a hand-made rule survives
   invisibly and nobody reading `infra/main.bicep` would know the database had a
   door in it.

`usage_report --remote` and `usage_dashboard --remote` need none of this: they
read the API's token-gated stats route, so they work from anywhere and remain
the right tool for a number you just want to look at.

## Notes

- SQL location follows the deployment `location` (default: the resource group's).
- The backend container's startup runs `scripts/db_bootstrap.py`: `create_all` +
  stamp on a fresh DB, else `alembic upgrade head`.
- **An infra deploy never moves deployed code.** The template has to set some
  image tag, and any fixed default goes stale the moment CI ships a new SHA —
  the original default was `latest`, which CI has never pushed, so applying
  infra would have pointed both apps at an image that does not exist. The infra
  job now reads the running tag off the live apps and passes it back in.
- **App Service Plan sizing** lives in `appSkuName`/`appSkuTier`
  (`infra/main.parameters.json`). `P0v3` is the cheapest tier that can
  autoscale; `B1` is the cheapest overall and cannot autoscale at all. The
  autoscale resource is conditional on the tier, so switching back to Basic is a
  parameter change, not an edit.
