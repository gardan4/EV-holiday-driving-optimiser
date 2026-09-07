# Azure production account migration

The destination subscription is `cda65360-878b-48f2-a774-6eb4782a95aa`, in tenant
`ecc5c9bf-7f28-430d-966c-bd02f9cf2f72`, owned by the marc-meijers account.
The source `evtrip-dev-rg` is the current **live** environment despite its name.
Do not remove source resources until the final data and DNS cutover is verified.

## Destination

- Group: `evtrip-prod-rg`.
- Apps: `evtrip-api-prod` and `evtrip-web-prod`, North Europe, shared P0v3 plan.
- SQL: `evtrip-sql-prod-se.database.windows.net`, database `evtripdb-prod`,
  Sweden Central, Standard S0. Existing database engine and credentials retained.
- Public domains remain `evtrip.dev` and `api.evtrip.dev`.
- GitHub environment: `azure-production`, with a resource-group-scoped managed
  identity and OIDC federation; no Azure client secret.

## Staging

Restore a transactionally consistent SQL copy into the destination before
dispatching `deploy.yml` with `migration_stage=true`. The infrastructure helper
requires the restored database to be online. It deploys the current released
images, limits incoming traffic to the runner and `MIGRATION_OPERATOR_IPS`, and
sets `PROCESS_ROLE=web` so staging does not start background maintenance loops.
GitHub builds and normal releases are skipped for this migration dispatch.

`MIGRATION_COMPLETE=false` blocks normal deployments. Keep that guard until the
write freeze, final database synchronization, source API shutdown and DNS
cutover have completed. The initial copy is a staging snapshot, not a continuous
replica, so do not switch traffic to it without a final synchronization.

## Cutover and ongoing releases

1. Verify restored schema, row counts, API reads and existing public share links.
2. Freeze source writes and stop both source/target APIs before the final copy.
   Verify the fresh copy and prepare custom-domain ownership and TLS bindings.
3. Set `MIGRATION_COMPLETE=true`, run the production infrastructure deployment,
   switch Cloudflare DNS, and verify both public domains against the new apps.
   Exactly one API deployment may run background jobs against the current DB.
4. Merge the migration branch into `main`; the same official workflow then
   builds and deploys future changes into this destination environment.
5. Remove the source Azure deployment credentials and retire old resources only
   after the rollback checkpoint has been recorded. Remove temporary SQL and
   App Service migration IP allowances.

Infrastructure updates use `infra/deploy.py`, which reads current image tags
before applying Bicep. Required secrets are validated, passed through a private
temporary parameters file, and removed on exit. Bicep and Actions validation
must pass before changing the deployment workflow.
