# Limitations

Explicit scope and known limitations of the AMC IQ research proof of concept.

## Research proof of concept, not production

This repository is a synthetic research exercise. It is not clinical decision support and has not
been clinically validated. All patient records, trial protocols, and clinical facts are fictional.
Do not use it for patient care, trial enrollment, regulatory submissions, or any clinical purpose.

## IQ provider limitations

| Provider | Limitation |
|---|---|
| **Web IQ** | The native Web IQ product is limited-access and not GA. This repository substitutes a Bing-backed Azure AI Search web knowledge source, labelled "Web IQ" in the scenario. It is not the same as a native Web IQ integration. |
| **Fabric IQ** | Requires delegated or OBO identity, there is no service-principal path. The Fabric capacity must be Active; a suspended capacity returns `CapacityNotActive`. |
| **Work IQ** | Requires a seeded Microsoft 365 tenant and an authenticated user for OBO. No self-service seeding path is provided here. No task creation or write operations occur; only a `SendMessage` query is sent. |
| **Foundry IQ** | Uses the `2026-05-01-preview` AI Search API version, which may change. Live blob citation URLs are firewalled; the adapter rewrites them to local synthetic documents. |
| **Hosted agent** | Optional, `USE_LIVE_AGENT=false` by default. When enabled, one blocking Responses API round trip determines total latency. The agent enriches the narrative; citations always come from grounded adapters. |

## Authentication limitations

- Live Fabric and Work IQ require a delegated user identity through OBO.
- Per-user ACL enforcement through the hosted-agent MCP channel is limited. That is acceptable for
  this synthetic proof of concept, but not a production security boundary.
- Work IQ requires an explicit certificate-based OBO application. The provisioning scripts do not
  create or seed the M365 environment.

## Task and routing limitations

- **No task is created.** The "Next Action" output is a drafted recommendation
  (`taskStatus: 'Drafted (not submitted)'`). No write to M365 To-Do, Planner, Teams, or any Work IQ
  endpoint occurs.
- **No message is sent.** The "Human Review" field identifies the responsible reviewer but does not
  send a notification, email, or Teams message.
- The routing of the care-team action remains the responsibility of the clinical team that reviews
  the output.

## Data limitations

- Synthetic trial IDs use the reserved project range `NCT99xxxxxx`. All returned HTTP 404 from
  ClinicalTrials.gov on 2026-07-25. Recheck immediately before publication because the registry can
  change.
- The cohort, 25 patients and 10 trials, is sufficient for the scenario but does not model all
  real-world eligibility edge cases.
- The Web IQ mock fixtures simulate ClinicalTrials.gov-style content; they are not real registry
  records and should not be presented as such.

## Multi-agent limitations

- `USE_LIVE_SPECIALISTS=true` with all six specialists plus one Fabric F64 Data Agent can exceed
  timeouts. The recommended connected configuration narrows live specialist roles to three,
  `LIVE_SPECIALIST_ROLES=eligibility,renal_labs,genomics`.
- Deepening is bounded by `AGENT_MAX_DEPTH` and `AGENT_MAX_LEADS` to control cost and latency;
  deep investigation may not surface every relevant fact.
- The multi-agent path is off by default, `USE_MULTI_AGENT=false`.

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
