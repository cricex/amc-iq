# AMC IQ API

FastAPI backend for the AMC IQ proof of concept. The documented local walkthrough reads only the synthetic
`data/` package and needs no cloud account or bearer token.

## Setup

```powershell
Set-Location services/api
..\..\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If the repo venv is unavailable:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run in deterministic mock mode

Prefer `./scripts/dev.ps1` from the repository root. For a manual backend process:

```powershell
$env:APP_ENVIRONMENT = "development"
$env:USE_LIVE_FOUNDRY = $env:USE_LIVE_FABRIC = $env:USE_LIVE_WORK = "false"
$env:USE_LIVE_WEB = $env:USE_LIVE_AGENT = $env:USE_LIVE_SPECIALISTS = "false"
Set-Location services/api
..\..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

## Test

```powershell
Set-Location services/api
..\..\.venv\Scripts\python.exe -m pytest
```

## Anonymous mock request

```powershell
curl.exe -s -X POST http://127.0.0.1:8000/api/ask -H "Content-Type: application/json" -d "{\"question\":\"Is PT-1042 eligible for NCT99004324 and what should the care team do next?\"}"
```

Assessment and cohort routes allow anonymous requests only when the app is explicitly local/test and
every live provider, hosted agent, and live specialist is disabled. Production or any live
configuration requires a valid delegated AMC IQ API bearer token. Health, Fabric capacity status,
and allowlisted synthetic evidence documents remain public.

Live adapters are gated by `USE_LIVE_FOUNDRY`, `USE_LIVE_FABRIC`, `USE_LIVE_WORK`, and
`USE_LIVE_WEB`. Live Work IQ additionally requires the two Entra registrations, delegated consent,
and the Key Vault-backed certificate configuration described in `.env.example`.
