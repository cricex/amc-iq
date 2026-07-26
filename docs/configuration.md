# Configuration

Connected, live AMC IQ requires explicit operator-supplied coordinates for Entra, Azure AI
Search, Fabric, Work IQ, and hosted agents. The repository ships safe local defaults through
`services/api/.env.example` and `scripts/dev.ps1`, which keep every live flag off and force the
deterministic local fallback.

## Connected/live posture

In any connected configuration, the backend validates delegated bearer tokens on assessment and
cohort routes and `validate_runtime_configuration()` fails fast if required coordinates are
missing or still use public placeholder values.

## Flag routing matrix

| Flag | Required coordinates when `true` | Notes |
|---|---|---|
| `USE_LIVE_FOUNDRY` | `SEARCH_ENDPOINT`, `FOUNDRY_KB_NAME` | Also needs the core connected set: `AZURE_TENANT_ID`, `API_AUDIENCE`, `API_REQUIRED_SCOPE`, `FABRIC_WORKSPACE_ID`, `FABRIC_DATA_AGENT_ID`, `PROJECT_ENDPOINT`, `ELIGIBILITY_EVALUATOR_AGENT` |
| `USE_LIVE_FABRIC` | Same core connected set | Fabric capacity must be Active |
| `USE_LIVE_WORK` | `WORK_IQ_CLIENT_ID`, `WORK_IQ_ENDPOINT`, `WORK_IQ_SCOPE`; in production also `AZURE_CLIENT_ID`, `WORK_IQ_KEY_VAULT_URL`, `WORK_IQ_CLIENT_CERTIFICATE_SECRET_NAME`; in non-production connected mode also `WORK_IQ_CLIENT_CERTIFICATE`, `WORK_IQ_CLIENT_CERTIFICATE_THUMBPRINT` | Requires authenticated user and a seeded M365 tenant |
| `USE_LIVE_WEB` | `SEARCH_ENDPOINT`, `WEB_KB_NAME` | Web IQ is represented by a Bing-backed AI Search knowledge base |
| `USE_LIVE_AGENT` | `PROJECT_ENDPOINT`, `AGENT_NAME` | Hosted Foundry agent is optional |
| `USE_LIVE_SPECIALISTS` | `PROJECT_ENDPOINT`; individual `SPECIALIST_*_AGENT` names for each role in `LIVE_SPECIALIST_ROLES` | Requires `USE_MULTI_AGENT=true` |

## Variable reference

### Environment and safety

| Variable | Process default | Local starter value | Required | Notes |
|---|---|---|---|---|
| `APP_ENVIRONMENT` | `production` in `config.py` | `development` in `.env.example` | Yes | `development` or `dev` or `local` or `test` enables the local fallback gate |
| `CORS_ORIGINS` | `http://localhost:5173` | `http://localhost:5173` | No | Comma-separated origins |

### IQ provider flags, all default `false`

| Variable | Description |
|---|---|
| `USE_LIVE_FOUNDRY` | Use Azure AI Search KB instead of local markdown |
| `USE_LIVE_FABRIC` | Use Fabric Data Agent MCP instead of local CSVs |
| `USE_LIVE_WORK` | Use Work IQ A2A API instead of local JSON fixtures |
| `USE_LIVE_WEB` | Use Bing-backed AI Search KB instead of local JSON fixtures |
| `USE_LIVE_AGENT` | Use the hosted Foundry agent for answer and trace |
| `USE_LIVE_SPECIALISTS` | Use live Foundry agents to narrate specialist roles |
| `USE_MULTI_AGENT` | Enable the grounded specialist team |
| `AGENT_MAX_DEPTH` | Default `1`, deepening rounds |
| `AGENT_MAX_LEADS` | Default `2`, max leads per round |

### Authentication, connected mode

| Variable | Description |
|---|---|
| `AZURE_TENANT_ID` | Entra tenant GUID for token validation |
| `API_AUDIENCE` | Accepted audience URI (`api://your-api-client-id`) |
| `API_REQUIRED_SCOPE` | Delegated scope claim (`access_as_user`) |
| `AZURE_CLIENT_ID` | User-assigned managed identity client ID, production Work IQ path |

### Azure AI Search, Foundry IQ, and Web IQ

| Variable | Description |
|---|---|
| `SEARCH_ENDPOINT` | `https://<service>.search.windows.net` |
| `FOUNDRY_KB_NAME` | Knowledge base name for Foundry IQ |
| `WEB_KB_NAME` | Knowledge base name for the Bing-backed Web IQ stand-in |
| `SEARCH_API_VERSION` | `2026-05-01-preview` |
| `PROJECT_ENDPOINT` | Foundry project HTTPS endpoint |
| `AGENT_NAME` | Hosted agent resource name |
| `ELIGIBILITY_EVALUATOR_AGENT` | Foundry evaluator agent name for the Fabric-native live path |
| `SPECIALIST_*_AGENT` | Six specialist agent names |
| `LIVE_SPECIALIST_ROLES` | Comma-separated role names to narrate live |
| `SPECIALIST_TIMEOUT` | Seconds per specialist live agent call, default `90` |
| `MODEL_DEPLOYMENT` | Model deployment name |

### Fabric IQ

| Variable | Description |
|---|---|
| `FABRIC_WORKSPACE_ID` | Fabric workspace GUID |
| `FABRIC_DATA_AGENT_ID` | Fabric Data Agent item GUID |
| `FABRIC_LAKEHOUSE_ID` | Lakehouse item GUID |
| `FABRIC_ONTOLOGY_ID` | Fabric Ontology item GUID, optional for a portal-visible ontology |
| `FABRIC_ONTOLOGY_NAME` | Ontology item name |
| `FABRIC_API_SCOPE` | `https://api.fabric.microsoft.com/.default`, no override needed |
| `AZURE_SUBSCRIPTION_ID` | Subscription GUID for the optional capacity status chip |
| `FABRIC_CAPACITY_RG` | Resource group of the F64 capacity |
| `FABRIC_CAPACITY_NAME` | Capacity resource name |

### Work IQ

| Variable | Description |
|---|---|
| `WORK_IQ_CLIENT_ID` | Confidential client app ID (`amciq-workiq-client`) |
| `WORK_IQ_ENDPOINT` | `https://workiq.svc.cloud.microsoft/a2a/` |
| `WORK_IQ_SCOPE` | `api://workiq.svc.cloud.microsoft/.default` |
| `WORK_IQ_KEY_VAULT_URL` | Key Vault URI for the production OBO certificate |
| `WORK_IQ_CLIENT_CERTIFICATE_SECRET_NAME` | Secret name in Key Vault |
| `WORK_IQ_CLIENT_CERTIFICATE` | Base64 PFX for manual non-production connected runs, never commit |
| `WORK_IQ_CLIENT_CERTIFICATE_THUMBPRINT` | Certificate thumbprint for the same path |
| `WORK_IQ_TIMEOUT_SECONDS` | Default `60` |
| `WORK_IQ_CERTIFICATE_CACHE_SECONDS` | Default `300` |
| `WORK_IQ_TIMEZONE` | IANA timezone string, default `America/Chicago` |
| `WORK_IQ_TIMEZONE_OFFSET_MINUTES` | UTC offset in minutes, default `-300` |

### Frontend, Vite variables prefixed `VITE_`

Set these in `apps/web/.env` or `apps/web/.env.local`. Never use `VITE_` for server-side secrets.

| Variable | Description |
|---|---|
| `VITE_API_BASE_URL` | API origin, default empty means same origin or Vite proxy |
| `VITE_ENTRA_TENANT_ID` | Entra tenant GUID, empty means unauthenticated local fallback |
| `VITE_ENTRA_CLIENT_ID` | SPA client app ID (`amciq-web-client`) |
| `VITE_API_SCOPE` | Delegated API scope URI |
| `VITE_REDIRECT_URI` | MSAL redirect URI, default `http://localhost:5173` |

## Deterministic local and mock fallback

The backend enables anonymous deterministic requests only when:

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

In this state, assessment routes are anonymous, all four IQ adapters use local synthetic data,
and eligibility stays fully deterministic. `scripts/dev.ps1` always sets this state.

## Startup validation

`Settings.validate_runtime_configuration()` runs at module load. It fails with a clear list of
missing variables when:
- any connected or production path still relies on placeholder values,
- a live flag is `true` without its required coordinates, or
- the process is outside the explicit local/test environments and lacks delegated auth settings.

Placeholder detection treats a value as non-operator when it contains `your-`, starts with `<`,
contains `${`, ends with `.example.invalid`, or equals the all-zero UUID.
