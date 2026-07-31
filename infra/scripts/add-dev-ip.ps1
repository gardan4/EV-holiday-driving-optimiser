# Add your current IP to Azure SQL Server firewall for local development
# Usage: .\add-dev-ip.ps1                       # defaults to dev SQL server
#        .\add-dev-ip.ps1 -Env prod             # target prod SQL server

param(
    [string]$ResourceGroup = "evtrip-rg",
    [ValidateSet("dev", "prod")]
    [string]$Env = "dev",
    [string]$SqlServerName = ""
)

if ([string]::IsNullOrEmpty($SqlServerName)) {
    $SqlServerName = "evtrip-sql-$Env"
}

# Get current public IP
$ip = (Invoke-RestMethod -Uri "https://api.ipify.org?format=text").Trim()
Write-Host "Your current public IP: $ip" -ForegroundColor Cyan

# Add firewall rule
Write-Host "Adding firewall rule to Azure SQL Server..." -ForegroundColor Yellow
az sql server firewall-rule create `
    --resource-group $ResourceGroup `
    --server $SqlServerName `
    --name "DevMachine-$(hostname)" `
    --start-ip-address $ip `
    --end-ip-address $ip

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nFirewall rule added successfully!" -ForegroundColor Green
    Write-Host "`nNext steps:" -ForegroundColor Cyan
    Write-Host "1. Point DATABASE_URL in your .env at the dev database, e.g.:"
    Write-Host "   DATABASE_URL=mssql+aioodbc://evtripadmin:<password>@evtrip-sql-$Env.database.windows.net:1433/evtripdb-$Env?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no"
    Write-Host ""
    Write-Host "2. Run the backend:"
    Write-Host "   cd src && uv run uvicorn app.main:app --reload"
} else {
    Write-Host "Failed to add firewall rule. Make sure you're logged in: az login" -ForegroundColor Red
}
