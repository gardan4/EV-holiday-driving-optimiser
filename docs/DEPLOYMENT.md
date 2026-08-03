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

## Notes

- SQL location follows the deployment `location` (default: the resource group's).
- The backend container's startup runs `scripts/db_bootstrap.py`: `create_all` +
  stamp on a fresh DB, else `alembic upgrade head`.
- **An infra deploy can roll the containers back to `:dev-latest`,** because the
  template sets the image tag it was authored with while the backend/frontend
  jobs pin the exact SHA. After an infra-only change, re-run the workflow
  (`workflow_dispatch`) to re-pin. The job prints a notice saying so.
- **App Service Plan sizing** lives in `appSkuName`/`appSkuTier`
  (`infra/main.parameters.json`). `P0v3` is the cheapest tier that can
  autoscale; `B1` is the cheapest overall and cannot autoscale at all. The
  autoscale resource is conditional on the tier, so switching back to Basic is a
  parameter change, not an edit.
