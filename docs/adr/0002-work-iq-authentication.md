# ADR 0002: Delegated Work IQ authentication boundary

## Decision

For this proof of concept, AMC IQ uses two single-tenant Microsoft Entra applications:

- `amciq-web-client` is the React/Vite SPA public client. MSAL React acquires the delegated AMC IQ
  API scope and direct cross-origin `fetch` requests attach the access token to both JSON and POST
  SSE calls.
- `amciq-workiq-client` is the confidential FastAPI client and API. The API validates the token
  tenant, exact audience, signature, lifetime, and delegated scope before accepting assessment or
  cohort requests. For each assessment it performs a stateless OBO exchange for Work IQ and sends
  an A2A v1.0 `SendMessage` request to `https://workiq.svc.cloud.microsoft/a2a/`.

In production the backend uses its user-assigned managed identity and `SecretClient` to retrieve the
base64 PFX secret backing the exportable `amciq-workiq-obo` certificate in `amciq-core-kv`. The PFX
is converted to the PEM key and certificate shape required by MSAL and cached briefly in process.
The default five-minute refresh interval picks up Key Vault certificate rotation without requiring an
application restart. A transient refresh failure serves the last-known-good certificate and applies a
short retry backoff, preventing concurrent assessments from overwhelming Key Vault. The OBO token
exchange remains stateless per assessment. Client IDs, audiences, scopes, vault URI, and certificate
name are environment inputs. No generated IDs, credentials, or client-secret fallback are committed.

## Least-privilege route policy

| Route | Authentication | Rationale |
|---|---|---|
| `POST /api/ask` | Required | Runs an assessment and can invoke delegated Work IQ. |
| `POST /api/ask/stream` | Required | Same data and delegated operations as the JSON assessment. |
| `GET /api/cohort/.../eligibility` | Required | Exposes patient and trial cohort assessment data. |
| `GET /healthz` | Public | Contains only process health and mock or live mode; required for platform probes. |
| `GET /api/fabric/status` | Public | Returns only capacity state, name, and a portal link; it never returns tenant data or credentials. |
| `GET /api/evidence/doc` | Public, allowlisted synthetic files only | Citation links open in a browser without token propagation. The handler permits only synthetic `data/foundry_docs` and `data/work` files and rejects traversal. |

Delegated bearer tokens are used instead of cookies, so CORS permits only configured origins, `GET` and `POST`,
plus the `Accept`, `Authorization`, and `Content-Type` headers. Credentialed cross-origin cookies are disabled.

## Consequences

- An assessment cannot run until the SPA and backend application registrations, delegated consent,
  and Key Vault certificate secret exist.
- Health probes and synthetic citation links remain usable without authentication.
- Work IQ failures are typed and degrade through the existing per-source fallback while remaining
  visible as a failed Work IQ source.
