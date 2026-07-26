# ADR 0001: Proof-of-concept architecture, hosted Foundry Agent with four IQ tools

- Status: Accepted
- Date: 2026-07-06
- Context: public product requirements and Microsoft platform documentation

## Context

We are building a research-focused proof of concept for what Microsoft's IQ intelligence layer
could look like inside an academic medical center precision-oncology trial-readiness workflow.
It must reason across institutional knowledge, structured clinical and operational data,
care-team workflow, and external evidence, while still showing the pieces through per-source
retrieval traces and citations.

Priorities: operator-reviewed robustness and clarity over enterprise completeness; all live
resources are operator-provided.

Current-state research (Build 2026, July 2026) established:

- The **Foundry IQ role** is backed by Azure AI Search agentic-retrieval knowledge bases, a GA
  capability surfaced to agents here through the `2026-05-01-preview` API and an MCP
  `knowledge_base_retrieve` tool.
- The **Fabric IQ role** is backed by the GA Fabric Data Agent over a Lakehouse and connects to
  Foundry through delegated or OBO identity only, with no service-principal path.
- The **Work IQ role** is backed by the Work IQ A2A v1.0 API over M365, treated as GA based on the
  current implementation research. It is delegated only and requires a seeded tenant.
- The **Web IQ role** is not a native Web IQ integration. Native Web IQ is limited access and not
  GA, so this proof of concept uses a Bing-backed knowledge source plus cached snapshots.

## Decision

1. **Orchestration = a single hosted Foundry Agent** (Foundry Agent Service, `azd ai agent`,
   Microsoft Agent Framework), with **four tools, one per IQ layer**. This maps each tool call to
   the UI's IQ source map and trace panel with minimal glue.
2. **Frontend** React + Vite + TypeScript; **backend** Python FastAPI acting as invoke and
   trace/source-map proxy (SSE), plus the response composer (eligibility, evidence, missing data,
   next action, owner, citations).
3. **Use operator-provided resources** and prefix newly created artifacts with `amciq` (see
   `docs/naming-conventions.md`).
4. **Scenario-first vertical slice**: Foundry IQ knowledge base -> agent -> cited answer, before
   adding Fabric, Work, and Web sources.
5. **Determinism via feature flags** (`USE_LIVE_WEB`, `USE_LIVE_WORK`, `USE_LIVE_FABRIC`) with
   deterministic mocks and caches so operator reviews never depend on a flaky live call.
6. **Web IQ is represented by Bing Web Search or a web knowledge source plus cached snapshots**,
   labelled "Web IQ" in the UI per the scenario; do not block on Web IQ limited-access enrollment.
7. **One canonical synthetic-entity registry** (`data/registry/registry.yaml`) feeds every
   generator so IDs and names stay identical across all layers and the UI.

## Consequences

- **Positive**: clean one-agent mental model; strong per-source trace and citations; reuse of the
  existing environment; resilient proof of concept via flags and mocks; consistent, reviewable artifacts.
- **Trade-offs / risks**:
  - Fabric and Work IQ require delegated or OBO identity plus seeded environments; mocks cover the gap.
  - Per-user ACL enforcement is limited through the hosted-agent MCP channel, acceptable for this
    synthetic proof of concept but not relied upon as a production boundary.
  - Some Foundry IQ features (answer synthesis, reasoning effort) remain on `2026-05-01-preview`.
  - Web IQ is simulated; the repository is explicit that it is a Bing and web-knowledge-source stand-in.

## Alternatives considered

- **Agent Framework orchestrated fully in-process in FastAPI**: more UI control, but it drops the
  hosted Foundry Agent story this proof of concept is meant to examine. Rejected.
- **All four sources live from day one**: highest fidelity, but fragile for a scripted executive or
  operator review. Rejected in favor of flags plus deterministic fallbacks.
