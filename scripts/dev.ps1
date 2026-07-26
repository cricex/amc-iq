<#
.SYNOPSIS
  Launch the AMC IQ proof-of-concept locally (FastAPI backend + React UI) in deterministic mock mode.
.DESCRIPTION
  Starts the backend on http://localhost:8000 and the Vite dev server on http://localhost:5173.
  No cloud or auth required, everything runs against the synthetic data/ package.
  Press Ctrl+C to stop the frontend; the backend runs in a separate window.
#>
[CmdletBinding()]
param(
    [int]$ApiPort = 8000,
    [int]$WebPort = 5173
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venvPy = Join-Path $root ".venv\Scripts\python.exe"

$safeEnvironment = [ordered]@{
    APP_ENVIRONMENT = "development"
    USE_LIVE_FOUNDRY = "false"
    USE_LIVE_FABRIC = "false"
    USE_LIVE_WORK = "false"
    USE_LIVE_WEB = "false"
    USE_LIVE_AGENT = "false"
    USE_MULTI_AGENT = "false"
    USE_LIVE_SPECIALISTS = "false"
    CORS_ORIGINS = "http://localhost:$WebPort"
    DATA_DIR = (Join-Path $root "data")
    VITE_API_BASE_URL = "http://localhost:$ApiPort"
    VITE_ENTRA_TENANT_ID = ""
    VITE_ENTRA_CLIENT_ID = ""
    VITE_API_SCOPE = ""
    VITE_REDIRECT_URI = "http://localhost:$WebPort"
}
$previousEnvironment = @{}
foreach ($name in $safeEnvironment.Keys) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    [Environment]::SetEnvironmentVariable($name, $safeEnvironment[$name], "Process")
}

try {
    if (-not (Test-Path $venvPy)) {
        Write-Host "Creating Python venv..." -ForegroundColor Cyan
        python -m venv (Join-Path $root ".venv")
    }

    Write-Host "Installing backend dependencies..." -ForegroundColor Cyan
    & $venvPy -m pip install -q -r (Join-Path $root "services\api\requirements.lock.txt")

    Write-Host "Starting backend on http://localhost:$ApiPort in forced mock mode..." -ForegroundColor Green
    Start-Process -FilePath $venvPy `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "$ApiPort" `
        -WorkingDirectory (Join-Path $root "services\api")

    Start-Sleep -Seconds 3
    try {
        $health = (Invoke-WebRequest -Uri "http://localhost:$ApiPort/healthz" -TimeoutSec 8).Content
        Write-Host "Backend healthy: $health" -ForegroundColor Green
    } catch {
        Write-Warning "Backend health check failed (it may still be starting): $_"
    }

    $web = Join-Path $root "apps\web"
    if (-not (Test-Path (Join-Path $web "node_modules"))) {
        Write-Host "Installing frontend dependencies..." -ForegroundColor Cyan
        Push-Location $web
        try {
            npm ci
        } finally {
            Pop-Location
        }
    }

    Write-Host "Starting frontend on http://localhost:$WebPort without Entra configuration..." -ForegroundColor Green
    Push-Location $web
    try {
        npm run dev -- --port $WebPort
    } finally {
        Pop-Location
    }
} finally {
    foreach ($name in $safeEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], "Process")
    }
}
