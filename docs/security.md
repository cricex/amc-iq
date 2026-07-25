# Security

This document describes the threat model, authentication boundaries, and security assumptions for
the AMC IQ demo. For vulnerability reporting, see [`SECURITY.md`](../SECURITY.md).

## Scope

AMC IQ is a synthetic-data demonstration. It does not store, process, or transmit real patient
data, PHI, or production clinical information. Security hardening is appropriate to a public demo
that may be deployed in an operator-controlled Azure subscription.

## Authentication boundaries

### Backend (FastAPI)

All assessment and cohort endpoints require a **delegated bearer token** in non-mock mode:

| Route | Authentication | Notes |
|---|---|---|
| `POST /api/ask` | Required | Assessment; can invoke Work IQ OBO |
| `POST /api/ask/stream` | Required | Same data and delegated operations |
| `GET /api/cohort/.../eligibility` | Required | Cohort assessment data |
| `GET /healthz` | Public | Only returns process health and mock/live flag |
| `GET /api/fabric/status` | Public | Returns only capacity state, name, portal link — no tenant data |
| `GET /api/evidence/doc` | Public, allowlisted | Synthetic files only; traversal-guarded |

Token validation (`auth.py`) checks: issuer (Entra tenant), audience (exact `API_AUDIENCE` URI),
signature (JWKS), lifetime, and delegated scope (`API_REQUIRED_SCOPE`).

**Mock mode exception:** when `APP_ENVIRONMENT ∈ {development, dev, local, test}` and all live
flags are `false`, assessment routes accept anonymous requests. This state is enforced by
`Settings.anonymous_mock_enabled`; `scripts/dev.ps1` always forces it.

### Work IQ OBO chain

```
User browser token  ──► FastAPI ──► MSAL OBO ──► Work IQ access token
                              │
                         Key Vault PFX
                         (production)
```

The OBO exchange is stateless per request. In production, the confidential client credential is a
PFX certificate stored as a Key Vault secret; `keyvault_certificate.py` loads and caches it
(5-minute TTL) using the API's user-assigned managed identity. No client secret is used or committed.

In non-production environments, the PFX can be provided via `WORK_IQ_CLIENT_CERTIFICATE` (base64).
This variable is a server-side secret and must never appear in a `VITE_*` variable or frontend bundle.

### Frontend (SPA)

The React SPA is a public client (`amciq-web-client`). MSAL React acquires delegated tokens
silently then interactively. Tokens are sent as `Authorization: Bearer` headers; cookies are not used.
CORS is restricted to configured origins (`CORS_ORIGINS`; default `http://localhost:5173`).
Credentialed cross-origin cookies are disabled.

## Least-privilege expectations

| Role / identity | Minimum permission |
|---|---|
| API managed identity | `Search Index Data Reader` on the AI Search resource |
| API managed identity | Fabric workspace member (Data Agent access) |
| API managed identity | Key Vault `Secret User` (for Work IQ certificate) |
| API managed identity | `Reader` on the Fabric capacity resource group (status chip) |
| Delegated user | AMC IQ API delegated scope (`access_as_user`) |
| Delegated user (Work IQ) | Work IQ delegated scope (via OBO) |

The API must not hold subscription Owner, Contributor, or any write role.

## Evidence doc endpoint

`GET /api/evidence/doc?path=<dir>/<file>` is intentionally public so citation links open in a
browser without token propagation. Safety controls:
- `parts[0]` must be in `_ALLOWED_DOC_DIRS` (`foundry_docs`, `work`) — hardcoded allowlist.
- `parts[1]` must match `[A-Za-z0-9._-]+\.(md|json)$` — no directory separators or traversal sequences.
- The resolved absolute path must be a child of the whitelisted base directory.
- Files larger than the directory listing are not served; only tracked synthetic documents exist there.

## Prompt injection

The demo agent instructions (`agent/instructions.md`) require grounding every claim in tool results.
Risk: a crafted question could attempt to override agent behavior or extract system-prompt content.

Mitigations in place:
- The hosted Foundry agent enforces the non-negotiable behavior rules in its instructions.
- The grounded (default) path never calls a live LLM; composition is entirely deterministic.
- The orchestrator resolves patient/trial from the question text against the known registry before
  any tool call; unknown entity names raise `ValueError` (HTTP 404).
- The `AskResult` schema is strongly typed; the frontend renders structured fields, not free-text Markdown.

Residual risk: live hosted-agent mode (`USE_LIVE_AGENT=true`) passes the user question to the
Foundry agent. A sufficiently crafted question could elicit off-topic content from the model.
The demo is not intended for untrusted user input in production.

## Retrieval poisoning

The Foundry IQ and Web IQ knowledge bases are populated by the operator from controlled sources
(the `data/foundry_docs/` synthetic documents and a Bing-backed web KB). Risk: malicious documents
injected into the knowledge base could influence retrieved context.

Mitigations:
- KB content is managed by the operator; `provision_foundry_iq.ps1` uploads only the tracked
  `data/foundry_docs/` files.
- The AI Search KB is not publicly writable.
- Retrieved snippets are mapped into strongly typed `Evidence` fields; the frontend does not
  render raw KB output as executable content.

## Citation trust boundaries

- Citation URLs in mock mode point to `/api/evidence/doc` which serves only allowlisted files.
- Citation URLs from live Foundry IQ (blob URLs) are firewalled; the live adapter rewrites them to
  the same `/api/evidence/doc` endpoint, so users see the same synthetic content.
- External citation URLs from Work IQ attributions are validated to `http`/`https` with a hostname
  (`_validated_web_url` in `workiq.py`); relative and non-HTTP URLs are rejected.
- Web IQ citations may point to real external URLs when live; the UI does not trust or execute them.

## External web content risks

When `USE_LIVE_WEB=true`, the Web IQ adapter retrieves external content via the Bing-backed
knowledge base. This content is not vetted by the operator beyond what Bing indexes. It is used
for informational background only and is not used to determine eligibility verdicts (those come
from the grounded Fabric adapter).

## Secret storage

- No credentials, keys, connection strings, or PFX material are committed to this repository.
- `.env` is gitignored; `.env.example` contains only nonfunctional placeholders.
- The Key Vault is the authoritative credential store for Work IQ OBO.
- `DefaultAzureCredential` is used for all Azure service authentication; no static secrets are
  required in the application image.

## Data residency and retention

This demo does not write user queries, assessment results, or any data to persistent storage.
All state is in-memory for the duration of a request. The operator's Azure AI Search, Fabric
Lakehouse, and Log Analytics resources may log request metadata according to their default policies;
review and restrict those policies for any non-demo deployment.

## Network assumptions

- The API is designed for HTTPS in production (Azure Container Apps enforces it).
- CORS restricts cross-origin requests to the configured frontend origin.
- The demo IaC leaves Key Vault public network access enabled because its Container Apps environment
  is not VNet-integrated. Data-plane access still requires the API's user-assigned managed identity
  and least-privilege Key Vault RBAC. Production deployments should use VNet integration and a
  private endpoint instead.
- The Fabric Data Agent MCP and Work IQ A2A endpoints are public HTTPS; authentication is via
  bearer tokens, not network-level controls.
- AI Search KB endpoints are public HTTPS; the operator should consider restricting public network
  access to the Search resource for production.

## Dependency supply chain

Backend and tooling dependencies are pinned in `requirements.lock.txt` files. Frontend dependencies
are pinned in `package-lock.json`. CI runs gitleaks, `pip-audit`, `npm audit`, Python type checking,
tests, linting, and builds.
