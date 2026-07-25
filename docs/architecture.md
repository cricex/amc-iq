# Architecture

AMC IQ is a sandbox demo of the Microsoft IQ intelligence layer applied to precision-oncology
clinical-trial readiness. The full architecture decision is captured in
[`docs/adr/0001-architecture.md`](adr/0001-architecture.md). This document summarises the runtime
shape and IQ layer assignments.

## Component overview

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
- `GET /api/cohort/patients/{id}/trials/{id}/eligibility` — per-criterion eligibility for any cohort
  pair, evaluated on Fabric-native services.
- `GET /api/evidence/doc` — serves cited synthetic source documents (allowlisted, traversal-guarded).
- `GET /api/fabric/status` — read-only Fabric capacity state for the UI status chip.
- `GET /healthz` — process health and mock/live mode.

## IQ layer assignments

| Layer | GA status | Demo implementation | Off-by-default flag |
|---|---|---|---|
| **Foundry IQ** | GA (agentic-retrieval KB) | Azure AI Search `2026-05-01-preview` | `USE_LIVE_FOUNDRY` |
| **Fabric IQ** | GA (Fabric Data Agent) | MCP over Fabric Data Agent; OBO-only | `USE_LIVE_FABRIC` |
| **Work IQ** | GA (A2A v1.0 API) | `workiq.svc.cloud.microsoft/a2a/`; OBO + certificate | `USE_LIVE_WORK` |
| **Web IQ** | **Limited access / not GA** | Bing-backed AI Search KB (stand-in; see ADR 0001) | `USE_LIVE_WEB` |
| Hosted agent | GA (Foundry Agent Service) | Responses API + `agent_reference`; optional enrichment | `USE_LIVE_AGENT` |

## Mock / live boundary

`config.py:Settings.anonymous_mock_enabled` gates the boundary. It is `True` only when:
- `APP_ENVIRONMENT` is `development`, `dev`, `local`, or `test`, **and**
- all six live flags (`USE_LIVE_FOUNDRY`, `USE_LIVE_FABRIC`, `USE_LIVE_WORK`, `USE_LIVE_WEB`,
  `USE_LIVE_AGENT`, `USE_LIVE_SPECIALISTS`) are `false`.

In mock mode, assessment and cohort routes are anonymous. In live mode they require a delegated
bearer token validated against the configured Entra tenant.

## Data flow (mock mode, default)

1. SPA sends `POST /api/ask` with the oncology question.
2. Orchestrator resolves patient (`PT-1042`) and trial (`NCT99004324`) from question text.
3. Four mock IQ adapters run concurrently in a `ThreadPoolExecutor`.
4. As soon as the Fabric mock returns, the deterministic eligibility evaluator runs concurrently.
5. The orchestrator assembles `AskResult` from all source facts, criterion verdicts, and the
   registry (single source of truth for display names/IDs).
6. For `/api/ask/stream`, intermediate SSE events (`plan`, `source_query`, `source_result`,
   `token`, `final`) are emitted as each adapter completes.

## Multi-agent extension (opt-in)

When `USE_MULTI_AGENT=true`, after the standard IQ adapter pass a grounded specialist team
(planner → 6 specialists in parallel → bounded deepening → synthesizer/critic) replaces the
default assessment trace. The Critic cross-checks every specialist finding against the Fabric
eligibility verdict (ground truth wins on any disagreement). See
[`docs/ontology-and-multi-agent.md`](ontology-and-multi-agent.md) for full details.

## Deployment topology (cloud)

```
Azure Static Web Apps  (apps/web built output)
        │
        │  HTTPS
        ▼
Azure Container Apps  (services/api FastAPI image, built by azd)
        │
        ├─ Azure AI Search   (Foundry IQ KB + Web IQ KB)
        ├─ Microsoft Fabric  (Lakehouse + Data Agent, via MCP)
        ├─ Work IQ A2A       (workiq.svc.cloud.microsoft)
        └─ Azure Key Vault   (Work IQ OBO certificate)
```

IaC is in `infra/main.bicep`. Existing Foundry and Search resources are referenced by parameter,
not recreated. See [`docs/deployment.md`](deployment.md) for provisioning steps.
