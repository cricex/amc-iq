<#
.SYNOPSIS
  Deploy the AMC IQ web UI to the Static Web App (reliable, no local Docker, no Oryx rebuild).
.DESCRIPTION
  `azd deploy web` (and `swa deploy`) try to run an Oryx build over the already-built `apps/web/dist`
  folder, which fails in some environments ("deployment binary exited with code 1" during
  "Preparing deployment"). This script builds the UI locally with npm and uploads the prebuilt `dist`
  directly with StaticSitesClient using `--skipAppBuild true`, which deploys reliably.

  Prereqs: `az login` (Contributor on the Static Web App), Node/npm. Run from anywhere.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$Subscription,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$StaticWebAppName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$ApiContainerApp,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$ResourceGroup,
  [string]$ApiBaseUrl       = "",
  [string]$TenantId         = "",
  [string]$WebClientId      = "",
  [string]$ApiScope         = "",
  [switch]$SkipBuild
)
$ErrorActionPreference = "Stop"
az account set --subscription $Subscription | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "Failed to select Azure subscription $Subscription."
}
$repo = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$web  = Join-Path $repo "apps\web"
$infra = Join-Path $repo "infra"

# The UI calls the Container App API directly (VITE_API_BASE_URL) so SSE streams live; the SWA
# managed proxy buffers Server-Sent Events. Derive the API URL from the Container App if not given.
if (-not $ApiBaseUrl) {
  $fqdn = az containerapp show -n $ApiContainerApp -g $ResourceGroup --query "properties.configuration.ingress.fqdn" -o tsv
  if ($LASTEXITCODE -ne 0) { throw "Could not resolve the API Container App endpoint." }
  if ($fqdn) { $ApiBaseUrl = "https://$fqdn" }
}
if (-not $ApiBaseUrl) { throw "VITE_API_BASE_URL could not be resolved." }
Write-Host "VITE_API_BASE_URL = $ApiBaseUrl" -ForegroundColor Cyan

if (-not $SkipBuild) {
  $azdValues = @{}
  if (-not $TenantId -or -not $WebClientId -or -not $ApiScope) {
    Push-Location $infra
    try {
      $rawAzdValues = azd env get-values
      if ($LASTEXITCODE -ne 0) {
        throw "Could not read the azd environment."
      }
      $rawAzdValues | ForEach-Object {
        if ($_ -match '^([^=]+)="?(.*?)"?$') {
          $azdValues[$matches[1]] = $matches[2].Trim('"')
        }
      }
    } finally {
      Pop-Location
    }
  }

  if (-not $TenantId) { $TenantId = $azdValues["AZURE_TENANT_ID"] }
  if (-not $WebClientId) { $WebClientId = $azdValues["WEB_CLIENT_ID"] }
  if (-not $ApiScope) { $ApiScope = $azdValues["WEB_API_SCOPE"] }

  $missing = @(
    if (-not $TenantId) { "AZURE_TENANT_ID" }
    if (-not $WebClientId) { "WEB_CLIENT_ID" }
    if (-not $ApiScope) { "WEB_API_SCOPE" }
  )
  if ($missing) {
    throw "Missing web authentication values in the azd environment: $($missing -join ', ')."
  }

  Write-Host "Building UI (npm ci + build)..." -ForegroundColor Cyan
  Push-Location $web
  $env:VITE_API_BASE_URL = $ApiBaseUrl
  $env:VITE_ENTRA_TENANT_ID = $TenantId
  $env:VITE_ENTRA_CLIENT_ID = $WebClientId
  $env:VITE_API_SCOPE = $ApiScope
  npm ci
  if ($LASTEXITCODE -ne 0) {
    Pop-Location
    throw "npm ci failed."
  }
  npm run build
  if ($LASTEXITCODE -ne 0) {
    Pop-Location
    throw "npm run build failed."
  }
  Pop-Location
}

$token = az staticwebapp secrets list --name $StaticWebAppName --resource-group $ResourceGroup --query "properties.apiKey" -o tsv
if ($LASTEXITCODE -ne 0) { throw "Could not retrieve the deployment token for $StaticWebAppName." }
if (-not $token) { throw "Could not get deployment token for $StaticWebAppName." }

# Use the StaticSitesClient the swa CLI caches (download it once via a no-op swa deploy if missing).
$bin = (Get-ChildItem "$env:USERPROFILE\.swa\deploy\*\StaticSitesClient.exe" -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
if (-not $bin) {
  Write-Host "Fetching StaticSitesClient via swa CLI..." -ForegroundColor Cyan
  npx --yes @azure/static-web-apps-cli@latest deploy "$web\dist" --deployment-token $token --env production 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not download StaticSitesClient." }
  $bin = (Get-ChildItem "$env:USERPROFILE\.swa\deploy\*\StaticSitesClient.exe" | Select-Object -First 1).FullName
}
if (-not $bin) { throw "StaticSitesClient is unavailable." }

Write-Host "Uploading dist to $StaticWebAppName (skip Oryx build)..." -ForegroundColor Cyan
& $bin upload --app "$web\dist" --outputLocation "$web\dist" --apiToken $token --skipAppBuild true --skipApiBuild true --verbose
if ($LASTEXITCODE -ne 0) { throw "Static Web App upload failed." }
$hostname = az staticwebapp show -n $StaticWebAppName -g $ResourceGroup --query defaultHostname -o tsv
if ($LASTEXITCODE -ne 0 -or -not $hostname) { throw "Could not resolve the deployed site hostname." }
Write-Host "Done. Site: https://$hostname" -ForegroundColor Green
