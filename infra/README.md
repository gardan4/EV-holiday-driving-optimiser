# Infrastructure

Azure infrastructure for EV Trip Optimizer, defined in Bicep and deployed by
GitHub Actions. Entry point: [`main.bicep`](main.bicep).

## What deploys

Everything lives in one resource group (`evtrip-rg`), namespaced per
environment (`dev` | `prod`) via the `environmentName` parameter:

| Resource | Name |
|---|---|
| Log Analytics workspace | `evtrip-logs-{env}` |
| App Service Plan (Linux, B1) | `evtrip-plan-{env}` |
| API App Service (FastAPI) | `evtrip-api-{env}` |
| Web App Service (Next.js) | `evtrip-web-{env}` |
| SQL Server | `evtrip-sql-{env}` |
| SQL Database | `evtripdb-{env}` |

Both App Services pull their image from GHCR
(`ghcr.io/gardan4/evtrip-{api,web}:{tag}`). There is **no**
worker tier — the API runs `PROCESS_ROLE=all` in a single container.

## Secrets

Secrets are passed as `@secure()` parameters and written directly into App
Service application settings (encrypted at rest) — no Key Vault. The sensitive
ones (`sqlAdminPassword`, `secretKey`, `encryptionKey`, `clerkSecretKey`,
`clerkWebhookSecret`, `ghcrToken`) come from **GitHub Actions secrets** at deploy
time; see the workflows. Non-secret defaults live in
[`main.parameters.json`](main.parameters.json).

## Deploying (CI)

| Branch | Workflow | Targets |
|---|---|---|
| `develop` | [deploy-dev.yml](../.github/workflows/deploy-dev.yml) | `*-dev`, `evtripdb-dev` |
| `main` | [deploy.yml](../.github/workflows/deploy.yml) | `*-prod`, `evtripdb-prod` |

Each workflow: paths-filter (backend/frontend) → test → build & push container
images to GHCR (tagged with the git sha) → point each App Service at the new
image (`az webapp config container set`) → `az webapp restart`. Azure auth is via
GitHub OIDC (`azure/login` federated credentials): the secrets
`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, and
`RESOURCE_GROUP` must be set on the repo.

## Manual deploy

```bash
az group create -n evtrip-rg -l westeurope   # first time only

az deployment group create \
  --resource-group evtrip-rg \
  --template-file infra/main.bicep \
  --parameters infra/main.parameters.json \
  --parameters \
    environmentName=dev \
    sqlAdminPassword="<strong-password>" \
    secretKey="<jwt-signing-key>" \
    encryptionKey="<fernet-key>" \
    clerkSecretKey="<sk_…>" \
    ghcrToken="<ghcr-pat>"
```

`sqlAdminPassword`, `secretKey`, and `encryptionKey` are required (no defaults).

## Local dev against SQL

Whitelist your IP on the dev SQL Server, then point `DATABASE_URL` at it:

```powershell
.\infra\scripts\add-dev-ip.ps1        # adds your public IP to evtrip-sql-dev
```

Or skip Azure entirely and run a local SQL Server with
[../docker-compose.local-db.yml](../docker-compose.local-db.yml).
