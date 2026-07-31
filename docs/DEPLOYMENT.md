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
4. **Provision infra** (manual — the workflows deploy app *images*, not infra):

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
- If you want CI to own infra too, add an `az deployment group create` job gated
  on `infra/**` changes.
