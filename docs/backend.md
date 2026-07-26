# Backend

Python FastAPI backend for the AMC IQ research proof of concept. Source: `services/api/`.

## Runtime posture

The connected, live topology is the target architecture: FastAPI validates delegated tokens,
fans out to Foundry IQ, Fabric IQ, Work IQ, and Web IQ, then returns structured assessment
output. Deterministic local and mock mode is the safe fallback for offline reproducibility,
rehearsals, and CI.

## Technology stack

| Component | Library / version |
|---|---|
| Web framework | FastAPI (ASGI via uvicorn) |
| Streaming | `sse-starlette` (Server-Sent Events) |
| Settings | `pydantic-settings` (`.env` file + env vars) |
| HTTP client | `httpx` (sync, for live AI Search / Work IQ) |
| Auth (connected mode) | `PyJWT[crypto]` + JWKS validation; MSAL for OBO |
| Azure identity | `azure-identity` (`DefaultAzureCredential`) |
| Key Vault | `azure-keyvault-secrets` |
| AI Projects | `azure-ai-projects` (hosted Foundry agent, evaluator) |
| Fabric MCP | `mcp>=1.9.0` (streamable-HTTP client) |
| Data parsing | `pyyaml`, `csv` (stdlib) |
| Tests | `pytest`, `pytest-asyncio` |

## Directory structure

```
services/api/
├── app/
│   ├── main.py            # FastAPI application, routes, CORS, evidence-doc server
│   ├── config.py          # Settings, connected/local gate, validation
│   ├── orchestrator.py    # Core orchestration lifecycle
│   ├── eligibility.py     # Eligibility boundary: local evaluator + Fabric-native live path
│   ├── schemas.py         # Pydantic models: AskRequest, AskResult, all sub-types
│   ├── streaming.py       # SSE event generator (wraps orchestrator.stream)
│   ├── auth.py            # Bearer-token validation (JWKS, audience, scope)
│   ├── workiq.py          # Work IQ A2A v1.0 client (OBO + httpx)
│   ├── agent_client.py    # Live hosted Foundry agent client (Responses API)
│   ├── registry.py        # Loads data/registry/registry.yaml; find_by_id helpers
│   ├── fabric_status.py   # Read-only Fabric capacity state (ARM, optional)
│   ├── keyvault_certificate.py  # Key Vault PFX to MSAL credential, cached
│   ├── sources/
│   │   ├── base.py        # QueryContext, SourceResult, IQSource protocol
│   │   ├── factory.py     # create_sources() / create_mock_fallbacks()
│   │   ├── foundry.py     # MockFoundryIQ + LiveFoundryIQ
│   │   ├── fabric.py      # MockFabricIQ + LiveFabricIQ
│   │   ├── work.py        # MockWorkIQ + LiveWorkIQ
│   │   ├── web.py         # MockWebIQ + LiveWebIQ
│   │   └── fabric_eligibility.py  # Fabric-native eligibility
│   └── agents/
│       ├── base.py        # SpecialistContext, SpecialistResult, Finding types
│       ├── planner.py     # Selects relevant specialists for a given intent
│       ├── grounded.py    # Grounded, deterministic specialist implementations
│       ├── deepening.py   # Bounded lead-pursuit loop
│       ├── synthesizer.py # Synthesizer + critic
│       ├── team.py        # Planner to parallel dispatch to deepening to synthesis
│       ├── live.py        # Live Foundry specialist narration
│       ├── trace.py       # Converts TeamRun to Assessment Steps trace
│       └── registry.py    # Lists all available specialist names
├── tests/                 # pytest suite, local mode, no cloud required
├── requirements.txt
├── .env.example
└── Dockerfile
```

## Startup flow

1. `get_settings()` loads `Settings` from `.env` and the process environment, then calls
   `validate_runtime_configuration()`.
2. `validate_runtime_configuration()` raises `SettingsConfigurationError` if a connected or
   production process still relies on placeholder values or lacks required coordinates.
3. `Orchestrator(settings)` is instantiated at module level; it loads the registry and creates
   the IQ source list via `create_sources()`.
4. FastAPI starts and applies CORS middleware using `settings.cors_origin_list`.

## Connected/live gate

`Settings.anonymous_mock_enabled` is `True` only when `APP_ENVIRONMENT` is one of
`development`, `dev`, `local`, or `test`, and every live flag remains `false`.

In any other configuration, a valid delegated bearer token is required. `auth.py` validates
tenant, audience, signature, lifetime, and scope before the request reaches the orchestrator.

## Connected request lifecycle

1. **Resolve context**: extract patient ID and trial ID from the question text, by patient name
   or ID, and by trial ID or short title. Unknown named entities raise `ValueError`.
2. **Dispatch IQ adapters concurrently**: all four sources run in a `ThreadPoolExecutor`. Each
   adapter returns a `SourceResult` with citations, facts, summary, and timing. On failure, the
   local fallback is used and the result is marked `failed` so the source map surfaces the outage.
3. **Eligibility pipeline starts as soon as Fabric returns**: this overlaps slower sources, such
   as live Work IQ, which can take up to 60 seconds.
4. **Optional hosted agent**: when `USE_LIVE_AGENT=true`, the hosted Foundry agent runs in
   parallel with the grounded adapters. Its answer can replace the composed narrative; its tool
   call sequence can replace the default trace.
5. **Assemble response**: `_assemble()` merges all source facts, criterion verdicts, the registry,
   and an intent-classified composition into the full `AskResult`.
6. **Structured output, never raw model Markdown**: the answer field is composed deterministically
   from structured facts. Even when the hosted agent is enabled, citations and evidence come from
   grounded adapters.

### SSE event sequence (streaming)

```
plan           -> assessment step labels
source_query   x 4  -> IQ layer starts
source_result  x 4  -> IQ layer completes, with summary, citations, duration
agent_activity x N  -> optional live agent tool-call trace events
token          -> composed answer text
final          -> complete AskResult payload
```

## Deterministic local and mock fallback

In local mode, `/api/ask` and `/api/cohort/...` accept anonymous requests, every IQ adapter uses
synthetic data from `data/`, and eligibility stays fully offline. `scripts/dev.ps1` always forces
this state.

## API endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/healthz` | Public | Process health, returns `{"status":"ok","mode":"mock|live"}` |
| `GET` | `/api/fabric/status` | Public | Read-only Fabric capacity state |
| `GET` | `/api/evidence/doc?path=<dir>/<file>` | Public, allowlisted | Serves a synthetic source document as HTML |
| `POST` | `/api/ask` | Required, local mode is anonymous | Synchronous `AskResult` JSON |
| `POST` | `/api/ask/stream` | Required, local mode is anonymous | SSE stream of ordered events |
| `GET` | `/api/cohort/patients/{id}/trials/{id}/eligibility` | Required, local mode is anonymous | Per-criterion verdicts |

Full request and response shapes are documented in [`docs/api-contract.md`](api-contract.md).

## Eligibility boundary (`eligibility.py`)

- **Local mode**: `evaluate_eligibility_mock()` traverses `data/fabric/trial_criteria.csv`
  against patient labs, biomarkers, treatments, and demographics. All logic is deterministic.
- **Connected mode**: `evaluate_eligibility_fabric()` in `sources/fabric_eligibility.py` uses
  the Fabric Data Agent for fact retrieval and the Foundry eligibility-evaluator agent for
  verdict reasoning. Live failures raise `EligibilityUnavailable` (HTTP 503); there is no silent
  fallback to local logic in production.

The renal margin constant (`RENAL_MARGIN = 5 mL/min`) creates the `uncertain` band around the
threshold, which produces the intended ambiguity in the hero scenario, CrCl 48 versus a threshold
of 50.

## Partial failure handling

If any IQ adapter throws, `_run_source()` catches the exception, runs the matching local fallback,
marks the result `failed`, and continues. The final `AskResult.unavailableSources` list names
failed layers so the UI can surface degraded state.

## Provider implementations

### Foundry IQ (`sources/foundry.py`)

- **Local**: reads all `.md` files from `data/foundry_docs/`, scores by keyword frequency and
  patient or trial ID match, and extracts EGFR biomarker status plus CrCl threshold via regex.
- **Connected**: `POST {SEARCH_ENDPOINT}/knowledgebases/{FOUNDRY_KB_NAME}/retrieve?api-version=2026-05-01-preview`
  with a structured question. Maps returned `references[].blobUrl` to local `/api/evidence/doc`
  links because blob URLs are firewalled.

### Fabric IQ (`sources/fabric.py`)

- **Local**: reads six CSV tables from `data/fabric/` and builds a snippet covering patient
  demographics, CrCl trend, trial threshold, prior therapy, and open scheduling slots.
- **Connected**: calls the Fabric Data Agent MCP endpoint with a structured NL2SQL question,
  parses CrCl readings and dates from the answer, and uses seeded CSV data as scaffolding for
  non-CrCl facts. This path is delegated only.

### Work IQ (`sources/work.py`, `workiq.py`)

- **Local**: reads JSON fixtures and markdown files from `data/work/`.
- **Connected**: sends an A2A v1.0 JSON-RPC `SendMessage` request to `WORK_IQ_ENDPOINT` using
  MSAL `ConfidentialClientApplication.acquire_token_on_behalf_of()`. In production, the OBO
  certificate is loaded from Azure Key Vault and cached briefly in process. No task creation
  occurs; only a question is sent to Work IQ.

### Web IQ (`sources/web.py`)

- **Local**: reads `data/web/manifest.json`, locates the fixture for `context.trial_id`, and
  requires `synthetic: true` on both the manifest entry and the JSON file.
- **Connected**: calls `POST {SEARCH_ENDPOINT}/knowledgebases/{WEB_KB_NAME}/retrieve`, using the
  same pattern as Foundry IQ. This is a Bing-backed stand-in for the limited-access Web IQ
  product, not a native Web IQ integration.

## Testing

```powershell
cd services/api
..\..\.venv\Scripts\python -m pytest
..\..\.venv\Scripts\python -m pytest -q
```

Tests cover orchestrator assembly, eligibility evaluation, Work IQ client behavior, SSE event
order, and provisioning scripts. All tests run offline.

## Known limitations

See [`docs/limitations.md`](limitations.md) for the consolidated list. Key backend items:
- Live Fabric and Work IQ require delegated identity, there is no service-principal path.
- `USE_LIVE_SPECIALISTS` plus multiple specialist agents plus one F64 Data Agent can time out.
- AI Search API version `2026-05-01-preview` may change.
- No retry logic exists on AI Search KB retrieve calls, only one attempt with a 90-second timeout.
