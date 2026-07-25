# Limitations

Explicit scope and known limitations of the AMC IQ demo.

## Simulation, not production

This is a sandbox demonstration project. It is not clinical decision support and has not been
clinically validated. All patient records, trial protocols, and clinical facts are fictional.
Do not use for patient care, trial enrollment, regulatory submissions, or any clinical purpose.

## IQ provider limitations

| Provider | Limitation |
|---|---|
| **Web IQ** | The native Web IQ product is limited-access and not GA. This demo substitutes a Bing-backed Azure AI Search web knowledge source, labelled "Web IQ" per the scenario. This is not the same as a native Web IQ integration. |
| **Fabric IQ** | Requires delegated/OBO identity (no service principal path). The Fabric capacity must be Active; a suspended capacity returns `CapacityNotActive`. |
| **Work IQ** | Requires a seeded Microsoft 365 tenant and an authenticated user for OBO. No self-service seeding path is provided in this repository. No task creation or write operations occur; only a `SendMessage` query is sent. |
| **Foundry IQ** | Uses the `2026-05-01-preview` AI Search API version, which may change. Live blob citation URLs are firewalled; the adapter rewrites them to local synthetic documents. |
| **Hosted agent** | Optional (`USE_LIVE_AGENT=false` by default). When enabled, one blocking Responses API round trip determines total latency. The agent enriches the narrative; citations always come from the grounded adapters. |

## Authentication limitations

- Live Fabric and Work IQ require a delegated user identity (OBO). Anonymous or service-identity
  access to these providers is not supported by the platform.
- The per-user ACL enforcement through the hosted-agent MCP channel is limited; acceptable for a
  synthetic-data demo but not a production security boundary.
- Work IQ requires an explicit certificate-based OBO application. The provisioning scripts do not
  create or seed the M365 environment.

## Task and routing limitations

- **No task is created.** The "Next Action" output is a drafted recommendation (`taskStatus: 'Drafted (not submitted)'`). No write to M365 To-Do, Planner, Teams, or any Work IQ endpoint occurs.
- **No message is sent.** The "Human Review" field identifies the responsible reviewer but does not send a notification, email, or Teams message.
- The routing of the care team action is the responsibility of the clinical team acting on the demo output.

## Data limitations

- Synthetic NCT trial IDs use the demo range `NCT99xxxxxx`. All returned HTTP 404 from
  ClinicalTrials.gov on 2026-07-25. **Recheck immediately before publication**; the registry can
  change.
- The cohort (25 patients, 10 trials) is sufficient for the demo scenario but does not model
  all real-world eligibility edge cases.
- The Web IQ mock fixtures simulate ClinicalTrials.gov-style content; they are not real registry
  records and should not be presented as such.

## Multi-agent limitations

- `USE_LIVE_SPECIALISTS=true` with all six specialists + one Fabric F64 Data Agent can exceed
  timeouts. The recommended configuration narrows live specialist roles to 3
  (`LIVE_SPECIALIST_ROLES=eligibility,renal_labs,genomics`).
- Deepening (lead pursuit) is bounded by `AGENT_MAX_DEPTH` and `AGENT_MAX_LEADS` to limit cost
  and latency; deep investigation may not surface all relevant facts.
- The multi-agent path is off by default (`USE_MULTI_AGENT=false`).

## CI and validation boundaries

- CI runs gitleaks, Python dependency audits, API type checking, synthetic-data validation, tests,
  Bicep compilation, frontend linting, npm audit, frontend tests, and the production build.
- TypeScript is checked as part of `npm run build` (`tsc -b && vite build`), not as a separate step.
- Live integration validation is manual and environment-gated because it requires operator-owned
  Azure, Fabric, and Microsoft 365 resources.

## Licensing

The repository source and documentation are available under the
[MIT License](../LICENSE). Dependencies, hosted services, and platform SDKs remain subject to
their respective upstream terms.
