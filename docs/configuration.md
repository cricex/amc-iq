# Configuration

All configuration is provided through environment variables, loaded by `pydantic-settings` from
`services/api/.env` (local override) and the process environment. Copy
`services/api/.env.example` as your starting point; never commit `.env`.

## Mock / live gate

The backend has a strict safety gate (`config.py:Settings.anonymous_mock_enabled`):

```
anonymous_mock_enabled = True   when:
  APP_ENVIRONMENT ∈ {development, dev, local, test}
  AND USE_LIVE_FOUNDRY  = false
  AND USE_LIVE_FABRIC   = false
  AND USE_LIVE_WORK     = false
  AND USE_LIVE_WEB      = false
  AND USE_LIVE_AGENT    = false
  AND USE_LIVE_SPECIALISTS = false
```

In mock mode: assessment routes are anonymous; all four IQ adapters use local synthetic data;
eligibility uses the deterministic mock evaluator. **`scripts/dev.ps1` always sets this state.**

In any other configuration: a valid delegated bearer token is required on all assessment and
cohort routes; `validate_runtime_configuration()` raises `SettingsConfigurationError` on startup
if required live coordinates are missing or still hold public placeholder values.

## Flag routing matrix

| Flag | Required coordinates when `true` | Notes |
|---|---|---|
| `USE_LIVE_FOUNDRY` | `SEARCH_ENDPOINT`, `FOUNDRY_KB_NAME` | Also needs `AZURE_TENANT_ID`, `API_AUDIENCE`, `API_REQUIRED_SCOPE`, `FABRIC_WORKSPACE_ID`, `FABRIC_DATA_AGENT_ID`, `PROJECT_ENDPOINT`, `ELIGIBILITY_EVALUATOR_AGENT` (live production always) |
| `USE_LIVE_FABRIC` | *(same core live set)* | Fabric capacity must be Active |
| `USE_LIVE_WORK` | `WORK_IQ_CLIENT_ID`, `WORK_IQ_ENDPOINT`, `WORK_IQ_SCOPE`; in production also `AZURE_CLIENT_ID`, `WORK_IQ_KEY_VAULT_URL`, `WORK_IQ_CLIENT_CERTIFICATE_SECRET_NAME`; in dev also `WORK_IQ_CLIENT_CERTIFICATE`, `WORK_IQ_CLIENT_CERTIFICATE_THUMBPRINT` | Requires authenticated user (OBO); seeded M365 tenant |
| `USE_LIVE_WEB` | `SEARCH_ENDPOINT`, `WEB_KB_NAME` | |
| `USE_LIVE_AGENT` | `PROJECT_ENDPOINT`, `AGENT_NAME` | |
| `USE_LIVE_SPECIALISTS` | `PROJECT_ENDPOINT`; individual `SPECIALIST_*_AGENT` names for each role in `LIVE_SPECIALIST_ROLES` | Requires `USE_MULTI_AGENT=true` |

## Variable reference

### Environment and safety

| Variable | Default | Required | Notes |
|---|---|---|---|
| `APP_ENVIRONMENT` | `production` | Yes | `development`/`dev`/`local`/`test` enables mock gate |
| `CORS_ORIGINS` | `http://localhost:5173` | No | Comma-separated origins |

### IQ provider flags (all default `false`)

| Variable | Description |
|---|---|
| `USE_LIVE_FOUNDRY` | Use Azure AI Search KB instead of local markdown |
| `USE_LIVE_FABRIC` | Use Fabric Data Agent MCP instead of local CSVs |
| `USE_LIVE_WORK` | Use Work IQ A2A API instead of local JSON fixtures |
| `USE_LIVE_WEB` | Use Bing-backed AI Search KB instead of local JSON fixtures |
| `USE_LIVE_AGENT` | Use hosted Foundry agent for answer + trace |
| `USE_LIVE_SPECIALISTS` | Use live Foundry agents to narrate specialist roles |
| `USE_MULTI_AGENT` | Enable grounded specialist team (drives Assessment Steps) |
| `AGENT_MAX_DEPTH` | `1` | Deepening rounds |
| `AGENT_MAX_LEADS` | `2` | Max leads per round |

### Authentication (live/production)

| Variable | Description |
|---|---|
| `AZURE_TENANT_ID` | Entra tenant GUID for token validation |
| `API_AUDIENCE` | Accepted audience URI (`api://your-api-client-id`) |
| `API_REQUIRED_SCOPE` | Delegated scope claim (`access_as_user`) |
| `AZURE_CLIENT_ID` | User-assigned managed identity client ID (production) |

### Azure AI Search / Foundry IQ / Web IQ

| Variable | Description |
|---|---|
| `SEARCH_ENDPOINT` | `https://<service>.search.windows.net` |
| `FOUNDRY_KB_NAME` | Knowledge base name for Foundry IQ |
| `WEB_KB_NAME` | Knowledge base name for Web IQ (Bing-backed) |
| `SEARCH_API_VERSION` | `2026-05-01-preview` |
| `PROJECT_ENDPOINT` | Foundry project HTTPS endpoint |
| `AGENT_NAME` | Hosted agent resource name |
| `ELIGIBILITY_EVALUATOR_AGENT` | Foundry evaluator agent name (Fabric-native live path) |
| `SPECIALIST_*_AGENT` | Six specialist agent names (eligibility, renal, genomics, protocol, workflow, evidence) |
| `LIVE_SPECIALIST_ROLES` | Comma-separated role names to narrate live |
| `SPECIALIST_TIMEOUT` | Seconds per specialist live agent call (default `90`) |
| `MODEL_DEPLOYMENT` | Model deployment name |

### Fabric IQ

| Variable | Description |
|---|---|
| `FABRIC_WORKSPACE_ID` | Fabric workspace GUID |
| `FABRIC_DATA_AGENT_ID` | Fabric Data Agent item GUID |
| `FABRIC_LAKEHOUSE_ID` | Lakehouse item GUID |
| `FABRIC_ONTOLOGY_ID` | Fabric Ontology item GUID (optional; for portal-visible ontology) |
| `FABRIC_ONTOLOGY_NAME` | Ontology item name |
| `FABRIC_API_SCOPE` | `https://api.fabric.microsoft.com/.default` (no override needed) |
| `AZURE_SUBSCRIPTION_ID` | Subscription GUID (for optional capacity status chip) |
| `FABRIC_CAPACITY_RG` | Resource group of the F64 capacity |
| `FABRIC_CAPACITY_NAME` | Capacity resource name |

### Work IQ

| Variable | Description |
|---|---|
| `WORK_IQ_CLIENT_ID` | Confidential client app ID (`amciq-workiq-client`) |
| `WORK_IQ_ENDPOINT` | `https://workiq.svc.cloud.microsoft/a2a/` |
| `WORK_IQ_SCOPE` | `api://workiq.svc.cloud.microsoft/.default` |
| `WORK_IQ_KEY_VAULT_URL` | Key Vault URI for OBO certificate (production) |
| `WORK_IQ_CLIENT_CERTIFICATE_SECRET_NAME` | Secret name in Key Vault |
| `WORK_IQ_CLIENT_CERTIFICATE` | Base64 PFX (non-production dev only; never commit) |
| `WORK_IQ_CLIENT_CERTIFICATE_THUMBPRINT` | Certificate thumbprint (non-production dev) |
| `WORK_IQ_TIMEOUT_SECONDS` | Default `60` |
| `WORK_IQ_CERTIFICATE_CACHE_SECONDS` | Default `300` |
| `WORK_IQ_TIMEZONE` | IANA timezone string (default `America/Chicago`) |
| `WORK_IQ_TIMEZONE_OFFSET_MINUTES` | UTC offset in minutes (default `-300`) |

### Frontend (Vite, prefix `VITE_`)

Set in `apps/web/.env` or `apps/web/.env.local`. Never use `VITE_` prefix for server-side secrets.

| Variable | Description |
|---|---|
| `VITE_API_BASE_URL` | API origin (default empty → same origin or Vite proxy) |
| `VITE_ENTRA_TENANT_ID` | Entra tenant GUID (empty = mock/unauthenticated mode) |
| `VITE_ENTRA_CLIENT_ID` | SPA client app ID (`amciq-web-client`) |
| `VITE_API_SCOPE` | Delegated API scope URI |
| `VITE_REDIRECT_URI` | MSAL redirect URI (default `http://localhost:5173`) |

## Startup validation

`Settings.validate_runtime_configuration()` runs at module load. It fails fast with a clear list of
missing variables when:
- Any live flag is `true` but the required coordinates are still placeholder values.
- The environment is not an explicitly local/test environment but lacks auth configuration.

Placeholder detection: a value is considered a placeholder when it contains `your-`, starts with
`<`, contains `${`, ends with `.example.invalid`, or is the all-zero UUID.
