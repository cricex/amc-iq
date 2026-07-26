# Security

This document describes the threat model, authentication boundaries, and security assumptions for
the AMC IQ research proof of concept. For vulnerability reporting, see [`SECURITY.md`](../SECURITY.md).

## Scope

AMC IQ is a synthetic-data proof of concept. It does not store, process, or transmit real patient
data, PHI, or production clinical information. Security hardening is appropriate to a public proof
of concept that may be deployed in an operator-controlled Azure subscription.

## Authentication boundaries

### Backend (FastAPI)

All assessment and cohort endpoints require a delegated bearer token in connected mode.

| Route | Authentication | Notes |
|---|---|---|
| `POST /api/ask` | Required | Assessment, can invoke Work IQ OBO |
| `POST /api/ask/stream` | Required | Same data and delegated operations |
| `GET /api/cohort/.../eligibility` | Required | Cohort assessment data |
| `GET /healthz` | Public | Returns only process health and mock or live flag |
| `GET /api/fabric/status` | Public | Returns only capacity state, name, portal link, no tenant data |
| `GET /api/evidence/doc` | Public, allowlisted | Synthetic files only, traversal-guarded |

Token validation (`auth.py`) checks issuer, audience, signature, lifetime, and delegated scope.

**Local mode exception:** when `APP_ENVIRONMENT` is `development`, `dev`, `local`, or `test`, and
all live flags are `false`, assessment routes accept anonymous requests. `scripts/dev.ps1` always
forces this state.

### Work IQ OBO chain

```
User browser token  ->  FastAPI  ->  MSAL OBO  ->  Work IQ access token
                             │
                        Key Vault PFX
                        (production)
```

The OBO exchange is stateless per request. In production, the confidential client credential is a
PFX certificate stored as a Key Vault secret; `keyvault_certificate.py` loads and caches it for
five minutes through the API's user-assigned managed identity. No client secret is used or committed.

In non-production connected mode, the PFX can be provided through `WORK_IQ_CLIENT_CERTIFICATE`
(base64). This is a server-side secret and must never appear in a `VITE_*` variable or frontend bundle.

### Frontend (SPA)

The React SPA is a public client (`amciq-web-client`). MSAL React acquires delegated tokens
silently, then interactively. Tokens are sent as `Authorization: Bearer` headers; cookies are not
used. CORS is restricted to configured origins, default `http://localhost:5173`.

## Least-privilege expectations

| Role / identity | Minimum permission |
|---|---|
| API managed identity | `Search Index Data Reader` on the AI Search resource |
| API managed identity | Fabric workspace member, Data Agent access |
| API managed identity | Key Vault `Secret User` for the Work IQ certificate |
| API managed identity | `Reader` on the Fabric capacity resource group |
| Delegated user | AMC IQ API delegated scope (`access_as_user`) |
| Delegated user, Work IQ path | Work IQ delegated scope through OBO |

The API must not hold subscription Owner, Contributor, or any other write role.

## Evidence doc endpoint

`GET /api/evidence/doc?path=<dir>/<file>` is intentionally public so citation links open in a
browser without token propagation. Safety controls:
- `parts[0]` must be in `_ALLOWED_DOC_DIRS` (`foundry_docs`, `work`), a hardcoded allowlist.
- `parts[1]` must match `[A-Za-z0-9._-]+\.(md|json)$`, with no directory separators or traversal sequences.
- The resolved absolute path must stay under the whitelisted base directory.
- Only tracked synthetic documents are served from those directories.

## Prompt injection

The hosted agent instructions (`agent/instructions.md`) require grounding every substantive claim in
tool results. Risk: a crafted question could attempt to override agent behavior or extract
system-prompt content.

Mitigations in place:
- The hosted Foundry agent enforces non-negotiable behavior rules in its instructions.
- The grounded default path never calls a live LLM; composition is deterministic.
- The orchestrator resolves patient and trial names against the known registry before any tool call.
- The `AskResult` schema is strongly typed; the frontend renders structured fields, not free-text Markdown.

Residual risk: live hosted-agent mode, `USE_LIVE_AGENT=true`, passes the user question to the
Foundry agent. A sufficiently crafted question could still elicit off-topic content. The repository
is not intended for untrusted production traffic.

## Retrieval poisoning

Foundry IQ and the Web IQ stand-in are populated by the operator from controlled sources, the
`data/foundry_docs/` synthetic documents and a Bing-backed web knowledge base. Risk: malicious
documents injected into a knowledge base could influence retrieved context.

Mitigations:
- KB content is operator-managed; `provision_foundry_iq.ps1` uploads only tracked synthetic files.
- The AI Search KB is not publicly writable.
- Retrieved snippets map into strongly typed `Evidence` fields; the frontend does not render raw KB
  output as executable content.

## Citation trust boundaries

- Citation URLs in local mode point to `/api/evidence/doc`, which serves only allowlisted files.
- Citation URLs from live Foundry IQ are rewritten to the same endpoint, so users see the same
  synthetic content instead of firewalled blob URLs.
- External citation URLs from Work IQ attributions are validated to `http` or `https` with a hostname.
- Web IQ citations may point to real external URLs when live; the UI does not trust or execute them.

## External web content risks

When `USE_LIVE_WEB=true`, the Web IQ adapter retrieves external content through the Bing-backed
knowledge base. That content is not operator-vetted beyond what Bing indexes. It is used only for
informational background and does not determine eligibility verdicts, those remain grounded in the
Fabric path.

## Secret storage

- No credentials, keys, connection strings, or PFX material are committed to this repository.
- `.env` is gitignored; `.env.example` contains only nonfunctional placeholders or safe local values.
- Key Vault is the authoritative credential store for production Work IQ OBO.
- `DefaultAzureCredential` is used for Azure service authentication; the application image carries
  no static secrets.

## Data residency and retention

This repository does not write user queries, assessment results, or other runtime data to its own
persistent storage. State is in-memory for the duration of a request. Operator-owned Azure AI Search,
Fabric, and Log Analytics resources may log request metadata according to their default policies.

## Network assumptions

- The API is designed for HTTPS in production, Azure Container Apps enforces it.
- CORS restricts cross-origin requests to the configured frontend origin.
- The current IaC keeps Key Vault public network access enabled because the Container Apps environment
  is not VNet-integrated. Data-plane access still requires the API's managed identity and least-privilege
  Key Vault RBAC. Production deployments should use VNet integration and a private endpoint.
- Fabric Data Agent MCP and Work IQ A2A endpoints are public HTTPS; authentication is token-based,
  not network-bound.
- AI Search endpoints are public HTTPS; operators should consider restricting public network access
  for production.

## Dependency supply chain

Backend and tooling dependencies are pinned in `requirements.lock.txt` files. Frontend dependencies
are pinned in `package-lock.json`. CI runs gitleaks, `pip-audit`, `npm audit`, Python type checking,
tests, linting, and builds.
