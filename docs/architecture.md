# Architecture

AMC IQ is framed here as a research proof of concept for what a Microsoft IQ intelligence layer
could look like inside a research-focused academic medical center. The target posture is the
connected, live topology. Deterministic local and mock mode is the safe reproducibility fallback
for offline review, CI, and operator rehearsals.

## Connected topology

```
┌─────────────────────────────────────────────────────────────┐
│  React / Vite / TypeScript SPA   (apps/web)                 │
│  MSAL React (optional Entra auth)                           │
└───────────────────┬─────────────────────────────────────────┘
                    │  REST POST /api/ask
                    │  POST /api/ask/stream (Server-Sent Events)
                    ▼
┌─────────────────────────────────────────────────────────────┐
│  FastAPI backend   (services/api)                           │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Orchestrator                                         │  │
│  │  • resolves patient/trial from question text          │  │
│  │  • dispatches four IQ adapters concurrently           │  │
│  │  • starts eligibility evaluation when Fabric returns  │  │
│  │  • optionally runs multi-agent specialist team        │  │
│  │  • optionally calls live hosted Foundry agent         │  │
│  │  • assembles AskResult (structured output)            │  │
│  └──────────────────────────────────────────────────────┘  │
└──────┬────────────┬──────────────┬───────────────┬──────────┘
       ▼            ▼              ▼               ▼
  Foundry IQ    Fabric IQ      Work IQ          Web IQ
  Azure AI      Fabric Data    Work IQ A2A      Bing-backed
  Search KB     Agent (MCP)    v1.0 API         AI Search KB
  (or mock)     (or mock)      (or mock)        (or mock)
```

The orchestrator also exposes:
- `GET /api/cohort/patients/{id}/trials/{id}/eligibility`, per-criterion eligibility for any
  cohort pair, evaluated on Fabric-native services when connected.
- `GET /api/evidence/doc`, serves cited synthetic source documents from an allowlisted,
  traversal-guarded file set.
- `GET /api/fabric/status`, read-only Fabric capacity state for the UI status chip.
- `GET /healthz`, process health and the active `mock` or `live` mode.

## IQ layer assignments

| Role in this proof of concept | Backing service and status | Implementation | Opt-in flag, default `false` |
|---|---|---|---|
| **Foundry IQ role** | Azure AI Search agentic-retrieval knowledge bases; GA capability with the `2026-05-01-preview` API used here | Knowledge-base retrieval through Azure AI Search | `USE_LIVE_FOUNDRY` |
| **Fabric IQ role** | Fabric Data Agent; GA | MCP over Fabric Data Agent; OBO-only | `USE_LIVE_FABRIC` |
| **Work IQ role** | Work IQ A2A v1.0 API; GA based on the current implementation research | `workiq.svc.cloud.microsoft/a2a/`; OBO + certificate | `USE_LIVE_WORK` |
| **Web IQ role** | Native Web IQ is limited access and not GA | Bing-backed AI Search knowledge-base stand-in, see ADR 0001 | `USE_LIVE_WEB` |
| Hosted orchestration | Foundry Agent Service; GA | Responses API plus `agent_reference`; optional enrichment | `USE_LIVE_AGENT` |

## Live request flow

1. The SPA acquires a delegated Entra token when the `VITE_ENTRA_*` values are configured.
2. FastAPI validates tenant, audience, signature, lifetime, and delegated scope before running
   an assessment.
3. The orchestrator resolves patient and trial context, then dispatches Foundry IQ, Fabric IQ,
   Work IQ, and Web IQ concurrently.
4. The Fabric eligibility path starts as soon as Fabric returns, so criterion evaluation overlaps
   slower sources such as Work IQ.
5. When `USE_LIVE_AGENT=true`, the hosted Foundry agent can replace the composed narrative,
   while citations still come from grounded adapters.
6. The UI receives a structured `AskResult`, or SSE `plan`, `source_query`, `source_result`,
   `token`, and `final` events.

## Authentication and identity boundary

- In connected mode, assessment and cohort routes require a delegated bearer token.
- Fabric IQ and Work IQ are delegated or OBO only, there is no service-principal path for those
  platform calls.
- `GET /healthz`, `GET /api/fabric/status`, and allowlisted synthetic evidence documents remain
  public by design.
- Work IQ production access depends on a certificate loaded from Azure Key Vault through the API's
  user-assigned managed identity.

## Deterministic local and mock fallback

`Settings.anonymous_mock_enabled` is `True` only when:
- `APP_ENVIRONMENT` is `development`, `dev`, `local`, or `test`, and
- all six live flags (`USE_LIVE_FOUNDRY`, `USE_LIVE_FABRIC`, `USE_LIVE_WORK`, `USE_LIVE_WEB`,
  `USE_LIVE_AGENT`, `USE_LIVE_SPECIALISTS`) are `false`.

In this mode, assessment and cohort routes are anonymous, all four IQ adapters use local synthetic
data, and the eligibility evaluator is deterministic and offline. `scripts/dev.ps1` always forces
this state so the proof of concept is reproducible without cloud access.

## Multi-agent extension (opt-in)

When `USE_MULTI_AGENT=true`, after the standard IQ adapter pass a grounded specialist team
(planner, six specialists in parallel, bounded deepening, synthesizer, critic) replaces the
default assessment trace. The critic cross-checks every specialist finding against the Fabric
eligibility verdict, ground truth wins on any disagreement. See
[`docs/ontology-and-multi-agent.md`](ontology-and-multi-agent.md) for full details.

## Cloud deployment footprint

```
Azure Static Web Apps  (apps/web built output)
        │
        │  HTTPS
        ▼
Azure Container Apps  (services/api FastAPI image, built by azd)
        │
        ├─ Azure AI Search   (Foundry IQ KB + Web IQ KB stand-in)
        ├─ Microsoft Fabric  (Lakehouse + Data Agent, via MCP)
        ├─ Work IQ A2A       (workiq.svc.cloud.microsoft)
        └─ Azure Key Vault   (Work IQ OBO certificate)
```

IaC lives in `infra/main.bicep`. Existing Foundry and Search resources are referenced by
parameter, not recreated. See [`docs/deployment.md`](deployment.md) for provisioning steps.
