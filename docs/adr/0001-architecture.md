# ADR 0001: Demo architecture: hosted Foundry Agent with four IQ tools

- Status: Accepted
- Date: 2026-07-06
- Context: public product requirements and Microsoft platform documentation

## Context

We are building a sandbox, executive-ready demo of Microsoft's IQ intelligence layer for an academic
medical center precision-oncology trial-eligibility scenario. It must reason across institutional
knowledge, structured clinical/operational data, care-team workflow, and external evidence, and
**show the pieces** (a transparent per-source retrieval trace and citations). Priorities: demo
robustness and clarity over enterprise completeness; all live resources are operator-provided.

Current-state research (Build 2026, July 2026) established:

- **Foundry IQ** = GA Azure AI Search agentic-retrieval **knowledge bases** surfaced to agents as an
  MCP `knowledge_base_retrieve` tool.
- **Fabric IQ** = Fabric **Data Agent** (GA) over a Lakehouse; connected to Foundry as a tool via
  **OBO/delegated identity only** (no service principal).
- **Work IQ** = GA developer-callable API over M365, **delegated-only**, needs a seeded tenant.
- **Web IQ** = **limited access, not GA** → substitute with Bing Web Search / a Foundry IQ web
  knowledge source, plus cached snapshots for determinism.

## Decision

1. **Orchestration = a single hosted Foundry Agent** (Foundry Agent Service, `azd ai agent`,
   Microsoft Agent Framework), with **four tools, one per IQ layer**. This maps each tool call to the
   UI's IQ source map + agent trace panel with minimal glue.
2. **Frontend** React + Vite + TypeScript; **backend** Python FastAPI acting as invoke + trace/source-map
   proxy (SSE), plus the response composer (eligibility, evidence, missing data, next action, owner, citations).
3. **Use operator-provided resources** and prefix newly created artifacts with `amciq` (see
   `docs/naming-conventions.md`).
4. **Scenario-first vertical slice**: Foundry IQ knowledge base → agent → cited answer, before adding
   Fabric/Work/Web sources.
5. **Determinism via feature flags** (`USE_LIVE_WEB`, `USE_LIVE_WORK`, `USE_LIVE_FABRIC`) with
   deterministic mocks/caches so demo day never depends on a flaky live call.
6. **Web IQ is represented by Bing Web Search / a web knowledge source + cached snapshots**, labelled
   "Web IQ" in the UI per the scenario; we do not block on Web IQ limited-access enrollment.
7. **One canonical demo-entity registry** (`data/registry/registry.yaml`) feeds every generator so IDs
   and names are identical across all layers and the UI.

## Consequences

- **Positive**: clean one-agent mental model; strong per-source trace/citations; reuses existing
  environment; resilient demo via flags/mocks; consistent, showable artifacts.
- **Trade-offs / risks**:
  - Fabric and Work IQ require delegated/OBO identity and seeded environments; mocks cover the gap.
  - Per-user ACL enforcement is limited through the hosted-agent MCP channel, acceptable for a
    synthetic-data demo (documented, not relied upon).
  - Some Foundry IQ features (answer synthesis, reasoning effort) are `2026-05-01-preview`.
  - Web IQ is simulated; we are transparent that it is a Bing/web-KS stand-in.

## Alternatives considered

- **Agent Framework orchestrated fully in-process in FastAPI** (no hosted agent), more UI control but
  drops the "hosted Foundry Agent" story the demo is meant to showcase. Rejected.
- **All four sources live from day one**, highest fidelity but fragile for a scripted exec demo.
  Rejected in favor of flags + deterministic fallbacks.
