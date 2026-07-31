# Deployment

Two branches → two Azure environments:

| Branch    | Env    | Workflow                          |
|-----------|--------|-----------------------------------|
| `develop` | `dev`  | `.github/workflows/deploy-dev.yml`|
| `main`    | `prod` | `.github/workflows/deploy.yml`    |

Each pipeline: **paths-filter** (`src/**` vs `frontend/**`) → **test** (backend
smoke-import; frontend `tsc` + `next build`) → **build & push** container images
to GHCR → **`az webapp config container set`** + **`az webapp restart`**.

## Azure resources (Bicep — `infra/main.bicep`)

Provisioned per environment (`{env}` = `dev` | `prod`), named
`evtrip-{kind}-{env}`:

| Resource            | Name                              |
|---------------------|-----------------------------------|
| Log Analytics       | `evtrip-logs-{env}`     |
| App Service Plan    | `evtrip-plan-{env}`     |
| API App Service     | `evtrip-api-{env}`      |
| Web App Service     | `evtrip-web-{env}`      |
| SQL Server          | `evtrip-sql-{env}`      |
| SQL Database        | `evtripdb-{env}`        |

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
   `RESOURCE_GROUP`, `GHCR_TOKEN` (a PAT with `write:packages`), and
   `CLERK_PUBLISHABLE_KEY`.
4. **Provision infra** (manual — the workflows deploy app *images*, not infra):

   ```bash
   az deployment group create \
     -g <rg> -f infra/main.bicep -p infra/main.parameters.json \
     -p environmentName=dev sqlAdminPassword=<pw> \
        secretKey=<32-byte> encryptionKey=<fernet> \
        clerkJwtKey='<pem>' clerkIssuer='https://clerk.evtrip.app' \
        clerkAuthorizedParties='https://evtrip.app' \
        clerkSecretKey=<sk> clerkPublishableKey=<pk> \
        githubOwner=gardan4 ghcrToken=<pat>
   ```

   Secrets are passed as `@secure()` params and land in App Service settings
   (encrypted at rest) — there is no Key Vault in the default template.

5. **Push** to `develop` (or `main`) → CI builds, pushes, and deploys.

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
