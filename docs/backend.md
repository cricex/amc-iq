# Backend

Python FastAPI backend for the AMC IQ demo. Source: `services/api/`.

## Technology stack

| Component | Library / version |
|---|---|
| Web framework | FastAPI (ASGI via uvicorn) |
| Streaming | `sse-starlette` (Server-Sent Events) |
| Settings | `pydantic-settings` (`.env` file + env vars) |
| HTTP client | `httpx` (sync, for live AI Search / Work IQ) |
| Auth (live) | `PyJWT[crypto]` + JWKS validation; MSAL for OBO |
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
│   ├── config.py          # Settings (pydantic-settings), mock/live gate, validation
│   ├── orchestrator.py    # Core orchestration lifecycle (see below)
│   ├── eligibility.py     # Eligibility boundary: mock evaluator + Fabric-native live path
│   ├── schemas.py         # Pydantic models: AskRequest, AskResult, all sub-types
│   ├── streaming.py       # SSE event generator (wraps orchestrator.stream)
│   ├── auth.py            # Bearer-token validation (JWKS, audience, scope)
│   ├── workiq.py          # Work IQ A2A v1.0 client (OBO + httpx)
│   ├── agent_client.py    # Live hosted Foundry agent client (Responses API)
│   ├── registry.py        # Loads data/registry/registry.yaml; find_by_id helpers
│   ├── fabric_status.py   # Read-only Fabric capacity state (ARM, optional)
│   ├── keyvault_certificate.py  # Key Vault PFX → MSAL credential (cached)
│   ├── sources/
│   │   ├── base.py        # QueryContext, SourceResult, IQSource protocol
│   │   ├── factory.py     # create_sources() / create_mock_fallbacks()
│   │   ├── foundry.py     # MockFoundryIQ + LiveFoundryIQ (AI Search KB)
│   │   ├── fabric.py      # MockFabricIQ + LiveFabricIQ (Fabric Data Agent MCP)
│   │   ├── work.py        # MockWorkIQ + LiveWorkIQ (Work IQ A2A)
│   │   ├── web.py         # MockWebIQ + LiveWebIQ (Bing-backed AI Search KB)
│   │   └── fabric_eligibility.py  # Fabric-native eligibility (Data Agent + evaluator agent)
│   └── agents/
│       ├── base.py        # SpecialistContext, SpecialistResult, Finding types
│       ├── planner.py     # Selects relevant specialists for a given intent
│       ├── grounded.py    # Grounded (deterministic) specialist implementations
│       ├── deepening.py   # Bounded lead-pursuit loop
│       ├── synthesizer.py # Synthesizer + Critic (ground truth wins on disagreement)
│       ├── team.py        # Team engine: planner → parallel dispatch → deepening → synthesis
│       ├── live.py        # Live Foundry agent narration for specialist roles
│       ├── trace.py       # Converts TeamRun to Assessment Steps trace
│       └── registry.py    # Lists all available specialist names
├── tests/                 # pytest suite (mock mode, no cloud required)
├── requirements.txt
├── .env.example
└── Dockerfile
```

## Startup flow

1. `get_settings()` loads `Settings` from `.env` and env vars; calls `validate_runtime_configuration()`.
2. `validate_runtime_configuration()` raises `SettingsConfigurationError` if live/production flags
   are set but required coordinates are missing or still hold public placeholder values.
3. `Orchestrator(settings)` is instantiated at module level; it loads the registry and creates
   the IQ source list via `create_sources()`.
4. FastAPI starts and CORS middleware is applied using `settings.cors_origin_list`.

## Mock / live gate

`Settings.anonymous_mock_enabled` is `True` only when `APP_ENVIRONMENT ∈ {development, dev, local, test}`
**and** all six live flags are `false`. In mock mode, `/api/ask` and `/api/cohort/...` accept
anonymous requests. In any other configuration, a valid delegated bearer token is required
(`auth.py` validates tenant, audience, signature, lifetime, and scope).

## API endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/healthz` | Public | Process health; returns `{"status":"ok","mode":"mock|live"}` |
| `GET` | `/api/fabric/status` | Public | Read-only Fabric capacity state (Active/Paused/Unknown) |
| `GET` | `/api/evidence/doc?path=<dir>/<file>` | Public, allowlisted | Serves a synthetic source document as HTML |
| `POST` | `/api/ask` | Required (mock: anonymous) | Synchronous `AskResult` JSON |
| `POST` | `/api/ask/stream` | Required (mock: anonymous) | SSE stream of ordered events |
| `GET` | `/api/cohort/patients/{id}/trials/{id}/eligibility` | Required (mock: anonymous) | Per-criterion verdicts |

Full request/response shapes and SSE event sequence are documented in
[`docs/api-contract.md`](api-contract.md).

## Orchestration lifecycle

The orchestrator (`orchestrator.py`) follows this lifecycle for every assessment:

1. **Resolve context** — extract patient ID and trial ID from the question text (by patient name or
   ID, by trial ID or short title). Raises `ValueError` if a named entity is unknown.
2. **Dispatch IQ adapters concurrently** — all four sources run in a `ThreadPoolExecutor`. Each
   adapter returns a `SourceResult` with citations, facts, summary, and timing. On failure, the
   mock fallback is used and the result is marked `failed` so the source map shows the outage.
3. **Eligibility pipeline starts as soon as Fabric returns** — overlaps with slower sources
   (live Work IQ can take up to 60 s). Eligibility evaluation runs in a separate thread.
4. **Optional hosted agent** — when `USE_LIVE_AGENT=true`, the hosted Foundry agent runs
   concurrently with the grounded adapters. Its answer replaces the composed answer; its tool-call
   sequence replaces the default trace.
5. **Assemble response** — `_assemble()` merges all source facts, criterion verdicts, the registry,
   and an intent-classified compose to build the full `AskResult`.
6. **Structured output, never raw model Markdown** — the answer field is composed deterministically
   from structured facts. The hosted agent's output is used only when `USE_LIVE_AGENT=true`; even
   then, citations and evidence come from the grounded adapters.

### SSE event sequence (streaming)

```
plan           → assessment step labels
source_query   × 4  → IQ layer starts (lights up the UI activity bar)
source_result  × 4  → IQ layer completes (with summary, citation refs, duration)
agent_activity × N  → optional live agent tool-call trace events
token          → composed answer text
final          → complete AskResult payload
```

## Eligibility boundary (`eligibility.py`)

- **Mock mode**: `evaluate_eligibility_mock()` traverses `data/fabric/trial_criteria.csv` against
  the patient's labs, biomarkers, treatments, and demographics. All logic is deterministic and
  offline.
- **Live/production**: calls `evaluate_eligibility_fabric()` in `sources/fabric_eligibility.py`,
  which uses the Fabric Data Agent for fact retrieval and the Foundry eligibility-evaluator agent
  for verdict reasoning. Live failures raise `EligibilityUnavailable` (HTTP 503); there is no
  silent fallback to mock logic in production.

The renal margin constant (`RENAL_MARGIN = 5 mL/min`) creates the `uncertain` band around the
threshold, producing the demo's intentional ambiguity (CrCl 48 vs. threshold 50).

## Partial failure handling

If any IQ adapter throws, `_run_source()` catches the exception, runs the mock fallback for that
layer, marks the result `failed`, and continues. The final `AskResult.unavailableSources` list
names any failed layers so the UI can surface the degraded state.

## Provider implementations

### Foundry IQ (`sources/foundry.py`)

- **Mock**: reads all `.md` files from `data/foundry_docs/`, scores by keyword frequency and
  patient/trial ID match, extracts EGFR biomarker status and CrCl threshold via regex. Returns
  two citations: `r1` (genomics) and `r2` (protocol/amendment).
- **Live**: `POST {SEARCH_ENDPOINT}/knowledgebases/{FOUNDRY_KB_NAME}/retrieve?api-version=2026-05-01-preview`
  with a structured question. Maps returned `references[].blobUrl` to local `/api/evidence/doc`
  links (blob URLs are firewalled). Auth: `DefaultAzureCredential` → `https://search.azure.com/.default`.

### Fabric IQ (`sources/fabric.py`)

- **Mock**: reads six CSV tables from `data/fabric/`. Builds a snippet covering patient demographics,
  CrCl trend, trial threshold, prior therapy, and open scheduling slots.
- **Live**: calls the Fabric Data Agent MCP endpoint (`fabric_mcp_url`) with a structured NL2SQL
  question. Parses CrCl readings and dates from the natural-language answer. Uses `MockFabricIQ` as
  a scaffold for all non-CrCl facts (those are loaded from seed CSVs, not re-fetched each time).
  Auth: `DefaultAzureCredential` → `https://api.fabric.microsoft.com/.default`. Delegated-only;
  no service principal. The MCP call runs in an isolated thread with a fresh event loop.

### Work IQ (`sources/work.py`, `workiq.py`)

- **Mock**: reads JSON fixtures (`open_tasks.json`, `referral_queue.json`, `teams_messages.json`,
  `pi_availability.json`) and markdown files from `data/work/`.
- **Live**: sends an A2A v1.0 JSON-RPC `SendMessage` request to `WORK_IQ_ENDPOINT`. Uses MSAL
  `ConfidentialClientApplication.acquire_token_on_behalf_of()` with the user's delegated token.
  In production, the OBO client certificate is loaded from Azure Key Vault via
  `keyvault_certificate.py` (cached 5 minutes, transient refresh fallback). No task creation
  occurs; only a question is sent to Work IQ.

### Web IQ (`sources/web.py`)

- **Mock**: reads `data/web/manifest.json`, locates the fixture for `context.trial_id`, verifies
  `synthetic: true` on both the manifest entry and the JSON file (raises if not synthetic).
- **Live**: `POST {SEARCH_ENDPOINT}/knowledgebases/{WEB_KB_NAME}/retrieve` (same pattern as
  Foundry IQ). Returns external treatment-landscape context for the patient's biomarker.

## Testing

```powershell
cd services/api
..\..\.venv\Scripts\python -m pytest          # full suite (mock mode, no cloud)
..\..\.venv\Scripts\python -m pytest -q       # quiet
```

Tests cover: orchestrator assembly (mock), eligibility evaluator (all criterion categories),
Work IQ client (OBO path, protocol parsing, error cases), streaming SSE event order, and
provisioning scripts. All tests run offline.

## Known limitations

See [`docs/limitations.md`](limitations.md) for the consolidated list. Key backend items:
- Live Fabric + Work IQ require delegated identity; no service-principal path.
- `USE_LIVE_SPECIALISTS` + multiple specialist agents + one F64 Data Agent can time out.
- AI Search API version `2026-05-01-preview` may change.
- No retry logic on the AI Search KB retrieve call (single attempt with 90 s timeout).
