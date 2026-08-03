// ============================================================================
// EV Trip Optimizer — Azure infrastructure (minimal skeleton)
// ----------------------------------------------------------------------------
// Provisions everything the two-tier app needs and nothing more:
//   • Log Analytics workspace          (central logs)
//   • App Service Plan (Linux)         (hosts both containers)
//   • Azure SQL Server + one Database  (app data)
//   • API  App Service  (FastAPI)      (container from GHCR)
//   • Web  App Service  (Next.js)      (container from GHCR)
//
// Secrets are passed as @secure() params and written straight into App Service
// application settings (encrypted at rest). No Key Vault / managed-identity
// dance — keeps the template small and the CI simple. Add Key Vault later if
// you need cross-team secret sharing.
//
// Resource naming: `evtrip-{kind}-{environmentName}` (sql db is
// `evtripdb-{environmentName}`). CI targets these exact names.
// ============================================================================

targetScope = 'resourceGroup'

// ── Core ────────────────────────────────────────────────────────────────────
@description('Environment name — becomes the suffix on every resource (e.g. dev, prod).')
@allowed([
  'dev'
  'prod'
])
param environmentName string = 'dev'

@description('Location for all resources.')
param location string = resourceGroup().location

// ── Container images (GHCR) ─────────────────────────────────────────────────
@description('GitHub owner / GHCR namespace that hosts the container images.')
param githubOwner string = 'gardan4'

@description('Tag of the API image to run (CI overrides with the git sha).')
param apiImageTag string = 'latest'

@description('Tag of the Web image to run (CI overrides with the git sha).')
param webImageTag string = 'latest'

@description('GHCR pull token (PAT with read:packages). Leave empty for public images.')
@secure()
param ghcrToken string = ''

// ── SQL ─────────────────────────────────────────────────────────────────────
@description('SQL Server administrator login.')
param sqlAdminLogin string = 'evtripadmin'

@description('SQL Server administrator password.')
@secure()
param sqlAdminPassword string

@description('SQL Database SKU name (e.g. Basic, S0, S1).')
param sqlSkuName string = 'S0'

@description('SQL Database tier (Basic | Standard | GeneralPurpose). Must match sqlSkuName.')
param sqlTier string = 'Standard'

// ── App secrets (backend) ───────────────────────────────────────────────────
@description('JWT signing key.')
@secure()
param secretKey string

@description('Fernet key used to encrypt stored user credentials.')
@secure()
param encryptionKey string

// ── External data providers (API tier only) ─────────────────────────────────
@description('OpenRouteService API key (routing + geocoding).')
@secure()
param orsApiKey string = ''

@description('OpenChargeMap API key (charger POIs).')
@secure()
param ocmApiKey string = ''

// ── Public URLs / CORS ──────────────────────────────────────────────────────
@description('Public frontend URL (used for CORS + email links). Empty ⇒ derive the *.azurewebsites.net host.')
param frontendUrl string = ''

@description('Public API URL. Empty ⇒ derive the *.azurewebsites.net host.')
param apiUrl string = ''

@description('Public site URL for the frontend (NEXT_PUBLIC_SITE_URL). Empty ⇒ same as frontendUrl.')
param siteUrl string = ''

@description('Comma-separated CORS origins accepted by the API. Empty ⇒ same as frontendUrl.')
param corsOrigins string = ''

// ============================================================================
// Variables
// ============================================================================
var prefix = 'evtrip'
var planName = '${prefix}-plan-${environmentName}'
var apiAppName = '${prefix}-api-${environmentName}'
var webAppName = '${prefix}-web-${environmentName}'
var sqlServerName = '${prefix}-sql-${environmentName}'
var sqlDatabaseName = '${prefix}db-${environmentName}'
var logAnalyticsName = '${prefix}-logs-${environmentName}'

var registry = 'ghcr.io'
var apiImage = '${registry}/${githubOwner}/${prefix}-api:${apiImageTag}'
var webImage = '${registry}/${githubOwner}/${prefix}-web:${webImageTag}'

// Default the public URLs to the App Service hostnames when not overridden.
var apiPublicUrl = empty(apiUrl) ? 'https://${apiAppName}.azurewebsites.net' : apiUrl
var webPublicUrl = empty(frontendUrl) ? 'https://${webAppName}.azurewebsites.net' : frontendUrl
var sitePublicUrl = empty(siteUrl) ? webPublicUrl : siteUrl
var effectiveCors = empty(corsOrigins) ? webPublicUrl : corsOrigins

// mssql+aioodbc DSN the FastAPI app expects (matches src/ settings + Dockerfile ODBC 18).
var databaseUrl = 'mssql+aioodbc://${sqlAdminLogin}:${sqlAdminPassword}@${sqlServer.properties.fullyQualifiedDomainName}:1433/${sqlDatabaseName}?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no'

// Shared container-registry settings. For public GHCR packages the empty
// password is harmless; set ghcrToken for private ones.
var registrySettings = [
  {
    name: 'DOCKER_REGISTRY_SERVER_URL'
    value: 'https://${registry}'
  }
  {
    name: 'DOCKER_REGISTRY_SERVER_USERNAME'
    value: githubOwner
  }
  {
    name: 'DOCKER_REGISTRY_SERVER_PASSWORD'
    value: ghcrToken
  }
  {
    name: 'WEBSITES_ENABLE_APP_SERVICE_STORAGE'
    value: 'false'
  }
]

// ============================================================================
// Log Analytics
// ============================================================================
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    // Everything else here is a fixed-price SKU, so ingestion is the only line
    // on the bill that can follow traffic. Both apps ship HTTP *and* console
    // logs, which is ~2 records per request — a flood, or a few tabs left open
    // polling the live view, is the one way this gets expensive. Capping means
    // logs stop for the day rather than the spend continuing.
    workspaceCapping: {
      dailyQuotaGb: 1
    }
  }
}

// ============================================================================
// Azure SQL Server + Database
// ============================================================================
resource sqlServer 'Microsoft.Sql/servers@2023-05-01-preview' = {
  name: sqlServerName
  location: location
  properties: {
    administratorLogin: sqlAdminLogin
    administratorLoginPassword: sqlAdminPassword
    version: '12.0'
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
  }
}

resource sqlDatabase 'Microsoft.Sql/servers/databases@2023-05-01-preview' = {
  parent: sqlServer
  name: sqlDatabaseName
  location: location
  sku: {
    name: sqlSkuName
    tier: sqlTier
  }
  properties: {
    collation: 'SQL_Latin1_General_CP1_CI_AS'
  }
}

// Allow other Azure services (the App Services) to reach SQL. The 0.0.0.0
// sentinel rule is Azure's documented "Allow Azure services" toggle.
resource sqlFirewallAzure 'Microsoft.Sql/servers/firewallRules@2023-05-01-preview' = {
  parent: sqlServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// ============================================================================
// App Service Plan (Linux, shared by both containers)
// ============================================================================
resource appServicePlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: planName
  location: location
  kind: 'linux'
  sku: {
    name: 'B1'
    tier: 'Basic'
  }
  properties: {
    reserved: true // required for Linux plans
  }
}

// ============================================================================
// API App Service (FastAPI container)
// ============================================================================
resource apiApp 'Microsoft.Web/sites@2023-01-01' = {
  name: apiAppName
  location: location
  kind: 'app,linux,container'
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'DOCKER|${apiImage}'
      alwaysOn: true
      ftpsState: 'Disabled'
      http20Enabled: true
      healthCheckPath: '/health'
      appSettings: concat(registrySettings, [
        // Sensitive config (App Service encrypts app settings at rest).
        {
          name: 'DATABASE_URL'
          value: databaseUrl
        }
        {
          name: 'SECRET_KEY'
          value: secretKey
        }
        {
          name: 'ENCRYPTION_KEY'
          value: encryptionKey
        }
        {
          name: 'ORS_API_KEY'
          value: orsApiKey
        }
        {
          name: 'OCM_API_KEY'
          value: ocmApiKey
        }
        // Non-secret config.
        {
          name: 'CORS_ORIGINS'
          value: effectiveCors
        }
        {
          name: 'FRONTEND_URL'
          value: webPublicUrl
        }
        {
          // Any deployment of this template is reachable from the open
          // internet, so it runs under production rules whatever it is called.
          // Keying this on the environment name meant the one real deployment —
          // named "dev" — served Swagger and skipped the hardened response CSP.
          name: 'ENV'
          value: 'production'
        }
        // No separate worker tier ships in this skeleton — run everything in
        // one process. (The code honours PROCESS_ROLE if you split later.)
        {
          name: 'PROCESS_ROLE'
          value: 'all'
        }
        {
          name: 'WEBSITES_PORT'
          value: '8000'
        }
      ])
    }
  }
}

// ============================================================================
// Web App Service (Next.js container)
// ============================================================================
resource webApp 'Microsoft.Web/sites@2023-01-01' = {
  name: webAppName
  location: location
  kind: 'app,linux,container'
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'DOCKER|${webImage}'
      alwaysOn: true
      ftpsState: 'Disabled'
      http20Enabled: true
      appSettings: concat(registrySettings, [
        // NEXT_PUBLIC_* are baked in at build time (build-args in CI); they are
        // repeated here so server-side rendering / middleware also see them.
        {
          name: 'NEXT_PUBLIC_API_URL'
          value: apiPublicUrl
        }
        {
          name: 'NEXT_PUBLIC_SITE_URL'
          value: sitePublicUrl
        }
        {
          name: 'WEBSITES_PORT'
          value: '3000'
        }
      ])
    }
  }
}

// ============================================================================
// Diagnostics → Log Analytics (HTTP + console logs for both apps)
// ============================================================================
resource apiDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: apiApp
  name: 'to-log-analytics'
  properties: {
    workspaceId: logAnalytics.id
    logs: [
      { category: 'AppServiceHTTPLogs', enabled: true }
      { category: 'AppServiceConsoleLogs', enabled: true }
    ]
  }
}

resource webDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: webApp
  name: 'to-log-analytics'
  properties: {
    workspaceId: logAnalytics.id
    logs: [
      { category: 'AppServiceHTTPLogs', enabled: true }
      { category: 'AppServiceConsoleLogs', enabled: true }
    ]
  }
}

// ============================================================================
// Outputs
// ============================================================================
output apiAppName string = apiApp.name
output webAppName string = webApp.name
output apiUrl string = 'https://${apiApp.properties.defaultHostName}'
output webUrl string = 'https://${webApp.properties.defaultHostName}'
output sqlServerFqdn string = sqlServer.properties.fullyQualifiedDomainName
output sqlDatabaseName string = sqlDatabaseName
