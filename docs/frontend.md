# Frontend

React + Vite + TypeScript single-page application for the AMC IQ research proof of concept.
In the connected topology it is the user surface for the live intelligence layer. Deterministic
local and mock mode keeps the same workflow usable offline.

## Technology stack

| Component | Library |
|---|---|
| Framework | React 19 |
| Build / dev server | Vite 8 |
| Language | TypeScript 6 |
| Auth, optional | `@azure/msal-react` + `@azure/msal-browser` |
| Icons | `lucide-react` |
| Linting | `oxlint` |
| Tests | `vitest` |

## Application structure

```
apps/web/src/
├── main.tsx                 # Entry point
├── App.tsx                  # Root component: state machine, SSE consumer, layout
├── AuthenticatedApp.tsx     # Wraps the app in MsalProvider when Entra vars are set
├── auth.tsx                 # MSAL instance configuration
├── authentication.ts        # AuthenticationContext + useAuthentication hook
├── types.ts                 # Shared TypeScript types
├── questionLibrary.ts       # Pre-built question library
├── sampleResult.ts          # Static fallback AskResult
├── sourceMeta.ts            # IQ source labels, icons, CSS classes
├── sentiment.ts             # Five-tone system and glyph mapping
├── api/
│   ├── client.ts            # ask(), askStream(), getFabricStatus(), helpers
│   └── client.test.ts       # Vitest unit tests
└── components/
    ├── ActionRail.tsx
    ├── AnswerBody.tsx
    ├── AssessmentCard.tsx
    ├── AssessmentCard.test.tsx
    ├── AssessmentFailureCard.tsx
    ├── AssessmentSteps.tsx
    ├── BlockerCards.tsx
    ├── BottomLineCard.tsx
    ├── BuildingAssessment.tsx
    ├── CaseReviewCard.tsx
    ├── CriteriaFindings.tsx
    ├── EmptyState.tsx
    ├── EvidencePacket.tsx
    ├── EvidencePacket.test.tsx
    ├── FabricStatusChip.tsx
    ├── IQActivityBar.tsx
    ├── NextActionCard.tsx
    ├── PatientSnapshot.tsx
    ├── QuestionLibrary.tsx
    ├── ScopeCard.tsx
    ├── SideNav.tsx
    ├── StatusBanner.tsx
    ├── StatusChip.tsx
    ├── TopBar.tsx
    ├── TrialCard.tsx
    └── evidencePresentation.ts
```

## State management

`App.tsx` owns the UI state with `useState` and `useRef`. No external state library is used.

| State | Type | Purpose |
|---|---|---|
| `question` | `string` | Current prompt text |
| `result` | `AskResult \| null` | Completed assessment |
| `sourceMap` | `SourceMapEntry[]` | Live IQ source activity entries |
| `trace` | `TraceStep[]` | Assessment steps timeline |
| `phase` | `'idle' \| 'loading' \| 'done' \| 'error'` | Page phase |
| `streamFailure` | `boolean` | SSE unavailable, use static endpoint fallback |

## Authentication

The app uses MSAL React when `VITE_ENTRA_TENANT_ID`, `VITE_ENTRA_CLIENT_ID`, and
`VITE_API_SCOPE` are all non-empty.

- When Entra variables are set, `AuthenticatedApp.tsx` wraps the app in `MsalProvider` and
  supplies a delegated access-token getter through `AuthenticationContext`.
- When Entra variables are absent, local mode is the default, `AuthenticationContext` provides a
  no-op getter and the backend can accept anonymous requests only when its local gate is open.
- The SPA is a public client. Tokens are acquired silently, then interactively when needed, and
  sent on API requests as delegated bearer authorization headers.
- Credentialed cross-origin cookies are disabled. CORS allows only configured origins.

## API integration (`api/client.ts`)

| Function | Description |
|---|---|
| `ask(question, patientId?, accessToken?)` | `POST /api/ask` to `AskResult` |
| `askStream(question, patientId?, accessToken?, callbacks)` | `POST /api/ask/stream` to SSE events |
| `getFabricStatus()` | `GET /api/fabric/status` |
| `shouldUseSampleFallback(result)` | Returns `true` when the result is obviously empty |
| `decideStreamFailure(events)` | Returns `true` when SSE ended without a `final` event |

`askStream` opens a `fetch` SSE connection, parses `event:` and `data:` pairs, and calls
per-event callbacks to update `App.tsx` incrementally.

## Streaming behaviour

1. On submit, `phase = 'loading'` and the source map resets to `queued`.
2. `plan` events show assessment step labels in `BuildingAssessment`.
3. `source_query` events switch IQ tiles to `searching`.
4. `source_result` events switch tiles to `complete` or `failed`, with evidence counts.
5. `agent_activity` events, optional, show live agent tool-call activity.
6. `token` events stream answer text into `AssessmentCard`.
7. `final` events render the full `AskResult` and set `phase = 'done'`.
8. On SSE failure, the app falls back to `POST /api/ask`. If that also fails, it renders
   `sampleResult.ts` with `AssessmentFailureCard` so the hero scenario remains usable offline.

## Intent routing

`AssessmentCard` maps `AskResult.intent` to structured sections through `SECTIONS_BY_INTENT`.

| Intent | Sections rendered |
|---|---|
| `eligibility` | Summary banner -> Criteria findings -> Blockers -> Narrative |
| `screening` | Summary -> Blockers -> Criteria -> Narrative |
| `protocol` | Summary -> Criteria -> Blockers -> Narrative |
| `data_gaps` | Summary -> Blockers -> Narrative |
| `workflow` | Summary -> Narrative |
| `evidence` | Summary -> Criteria -> Narrative |
| `external_context` | Summary -> Narrative, no criteria or blocker cards |

The main assessment is never rendered as unrestricted model-generated Markdown. The backend returns
structured fields, and the frontend maps them into banners, cards, tables, chips, and workflow
steps.

## Five-tone sentiment system (`sentiment.ts`)

| Tone | Color | Semantics | Key uses |
|---|---|---|---|
| `green` | Green | Met / favorable | Criterion met, eligible |
| `amber` | Amber | Needs review / conditional | Uncertain criterion, pending |
| `red` | Red | Not met / blocked | Not-met criterion, not eligible |
| `blue` | Blue | Informational / external | External context only |
| `gray` | Gray | Neutral metadata | Drafted task, not submitted |

Special exported constants: `NOT_SUBMITTED`, `TASK_DRAFTED`, and `READY_FOR_PI_REVIEW`.

## Question library

`questionLibrary.ts` defines seven curated questions across seven categories: Trial Eligibility,
Screening Check, Evidence, Workflow, Protocol, Data Gaps, and External Context. The component
renders a filterable panel; selected questions populate the prompt bar.

## Safety indicators

- Disclaimer footer on every assessment: *"Synthetic data. No PHI. Not clinical decision support."*
- `ActionRail.tsx` sidebar: *"Synthetic AMC sandbox. No PHI. Not clinical decision support.
  Requires clinician and PI review."*
- `StatusBanner` renders `likely_eligible_pending` as *"Potential match, not yet confirmed
  eligible"*, never simply *"eligible"*.
- `NextActionCard` always displays *"NOT SUBMITTED"*.
- `external_context` never renders criteria or blocker cards.

## Deterministic local fallback

`sampleResult.ts` contains a static `AskResult` for the hero scenario, `PT-1042` and
`NCT99004324`. The app uses it only after both SSE and synchronous backend calls fail.

## Build and test

```powershell
cd apps/web
npm ci
npm run dev
npm run build
npm run lint
npm test
```
