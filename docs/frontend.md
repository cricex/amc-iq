# Frontend

React + Vite + TypeScript single-page application. Source: `apps/web/`.

## Technology stack

| Component | Library |
|---|---|
| Framework | React 19 |
| Build / dev server | Vite 8 |
| Language | TypeScript 6 |
| Auth (optional) | `@azure/msal-react` + `@azure/msal-browser` |
| Icons | `lucide-react` |
| Linting | `oxlint` |
| Tests | `vitest` |

## Application structure

```
apps/web/src/
├── main.tsx              # Entry point; MSAL provider when Entra vars are set
├── App.tsx               # Root component: state machine, SSE consumer, layout
├── AuthenticatedApp.tsx  # Thin wrapper supplying the MSAL access-token getter
├── authentication.ts     # AuthenticationContext + useAuthentication hook
├── authentication.ts     # MSAL instance configuration
├── types.ts              # All shared TypeScript types (IQSource, AskResult, StreamEvent, …)
├── schemas.py            # (backend only, not used in frontend)
├── questionLibrary.ts    # Pre-built question library (7 questions, 7 categories)
├── sampleResult.ts       # Static fallback AskResult used when the backend is unavailable
├── sourceMeta.ts         # IQ source labels, icons, CSS classes, retrieving text
├── sentiment.ts          # Five-tone system (green/amber/red/blue/gray), glyph mapping
├── api/
│   ├── client.ts         # ask(), askStream(), getFabricStatus(), error helpers
│   └── client.test.ts    # Vitest unit tests
└── components/
    ├── TopBar.tsx         # Page header with title and mode badge
    ├── SideNav.tsx        # Left navigation with section jump links
    ├── QuestionLibrary.tsx  # Filterable question panel
    ├── IQActivityBar.tsx  # Four-source activity grid (idle / searching / done / failed)
    ├── BuildingAssessment.tsx  # Live assessment trace (plan steps → completed)
    ├── PatientSnapshot.tsx    # Patient summary card
    ├── TrialCard.tsx          # Trial summary card
    ├── AssessmentCard.tsx     # Intent-routed assessment (criteria, blockers, narrative)
    ├── AssessmentSteps.tsx    # Trace timeline (multi-agent or standard steps)
    ├── AssessmentFailureCard.tsx  # Degraded-mode error card
    ├── StatusBanner.tsx       # Assessment verdict banner (tone-coded)
    ├── StatusChip.tsx         # Reusable status chip (ToneMeta)
    ├── CriteriaFindings.tsx   # Criteria met/uncertain/not-met cards
    ├── BlockerCards.tsx       # Uncertain + not-met blockers highlighted
    ├── BottomLineCard.tsx     # Bottom-line summary card
    ├── NextActionCard.tsx     # Drafted next-action checklist with owner metadata
    ├── CaseReviewCard.tsx     # Reviewer list (coordinator + PI sign-off)
    ├── EvidencePacket.tsx     # Citation source table, expandable snippets
    ├── FabricStatusChip.tsx   # Optional Fabric capacity state chip
    ├── ActionRail.tsx         # 40% right column: bottom line, next action, review status
    ├── ScopeCard.tsx          # Scope card for external-context intent
    └── evidencePresentation.ts  # Citation URL/title helpers
```

## State management

`App.tsx` owns all UI state with `useState` and `useRef`. No external state library is used.
Key state variables:

| State | Type | Purpose |
|---|---|---|
| `question` | `string` | Current prompt text |
| `result` | `AskResult \| null` | Completed assessment |
| `sourceMap` | `SourceMapEntry[]` | Live IQ source activity entries |
| `trace` | `TraceStep[]` | Assessment steps timeline |
| `phase` | `'idle' \| 'loading' \| 'done' \| 'error'` | Page phase |
| `streamFailure` | `boolean` | SSE unavailable → use static endpoint fallback |

## Authentication

The app uses MSAL React (`@azure/msal-react`) when the three Vite environment variables
(`VITE_ENTRA_TENANT_ID`, `VITE_ENTRA_CLIENT_ID`, `VITE_API_SCOPE`) are all non-empty.

- When Entra variables are set: `AuthenticatedApp.tsx` wraps the app in `MsalProvider` and
  supplies a delegated access-token getter via `AuthenticationContext`.
- When Entra variables are absent (local mock mode, the default): `AuthenticationContext` provides
  a no-op getter; the backend accepts anonymous requests because `APP_ENVIRONMENT=development` and
  all live flags are `false`.
- The SPA is a public client. Tokens are acquired silently then interactively. Bearer tokens are
  attached as `Authorization: Bearer <token>` headers.
- Credentialed cross-origin cookies are disabled; CORS permits only configured origins.

## API integration (`api/client.ts`)

| Function | Description |
|---|---|
| `ask(question, patientId?, accessToken?)` | `POST /api/ask` → `AskResult` |
| `askStream(question, patientId?, accessToken?, callbacks)` | `POST /api/ask/stream` → SSE events |
| `getFabricStatus()` | `GET /api/fabric/status` |
| `shouldUseSampleFallback(result)` | Returns `true` when the result is obviously empty |
| `decideStreamFailure(events)` | Returns `true` when SSE ended without a `final` event |

`askStream` opens a `fetch` SSE connection, parses `event:` / `data:` pairs, and calls per-event
callbacks to update `App.tsx` state incrementally.

## Streaming behaviour

1. On submit: `phase = 'loading'`, source map reset to `queued`.
2. `plan` event: assessment step labels appear in `BuildingAssessment`.
3. `source_query` events (×4): IQ tiles switch to `searching`.
4. `source_result` events (×4): tiles switch to `complete` (or `failed`) with evidence count.
5. `agent_activity` events (optional): live agent tool-call ticker.
6. `token` event: answer text rendered in `AssessmentCard`.
7. `final` event: full `AskResult` rendered; `phase = 'done'`.
8. On SSE failure: falls back to `POST /api/ask` (synchronous). If that also fails, renders
   `sampleResult.ts` with `AssessmentFailureCard` to keep the hero scenario demoable offline.

## Intent routing

`AssessmentCard` maps `AskResult.intent` to a set of structured sections via `SECTIONS_BY_INTENT`:

| Intent | Sections rendered |
|---|---|
| `eligibility` | Summary banner → Criteria findings → Blockers → Narrative |
| `screening` | Summary → Blockers → Criteria → Narrative |
| `protocol` | Summary → Criteria → Blockers → Narrative |
| `data_gaps` | Summary → Blockers → Narrative |
| `workflow` | Summary → Narrative |
| `evidence` | Summary → Criteria → Narrative |
| `external_context` | Summary → Narrative (no criteria/blocker cards — never reads as an eligibility decision) |

**The main assessment is never rendered as unrestricted model-generated Markdown.** The backend
returns structured fields. The frontend maps them to: status banners, criteria cards, status chips,
open-issue cards, next-action panels, review-status chips, evidence tables, and assessment workflow
steps.

## Five-tone sentiment system (`sentiment.ts`)

| Tone | Color | Semantics | Key uses |
|---|---|---|---|
| `green` | Green | Met / favorable | Criterion met, eligible |
| `amber` | Amber | Needs review / conditional | Uncertain criterion, pending |
| `red` | Red | Not met / blocked | Not-met criterion, not eligible |
| `blue` | Blue | Informational / external | External context only |
| `gray` | Gray | Neutral metadata | Drafted task, not submitted |

Special exported constants: `NOT_SUBMITTED` (gray) for task status; `TASK_DRAFTED` (gray) for
pre-submission tasks; `READY_FOR_PI_REVIEW` (amber) for review gating.

## Question library

`questionLibrary.ts` defines 7 curated questions across 7 categories (Trial Eligibility,
Screening Check, Evidence, Workflow, Protocol, Data Gaps, External Context). `QuestionLibrary`
renders a filterable panel; selected questions populate the prompt bar.

## Safety indicators

- Disclaimer footer on every assessment: *"Synthetic data. No PHI. Not clinical decision support.
  Always follow institutional policies and clinical judgement."*
- `ActionRail.tsx` sidebar: *"Synthetic AMC sandbox. No PHI. Not clinical decision support.
  Requires clinician and PI review."*
- `StatusBanner`: `likely_eligible_pending` renders as *"Potential match, not yet confirmed
  eligible"* (never "eligible").
- `NextActionCard`: task status always displays as *"NOT SUBMITTED"* (gray chip).
- `external_context` intent never renders criteria or blocker cards.

## Demo fallback

`sampleResult.ts` contains a static `AskResult` for the hero scenario (`PT-1042` / `NCT99004324`).
The app renders it with `AssessmentFailureCard` when both SSE and the synchronous `/api/ask` are
unavailable, keeping the UI demoable without a running backend.

## Build and test

```powershell
cd apps/web
npm ci
npm run dev          # Vite dev server, http://localhost:5173
npm run build        # TypeScript + Vite production build
npm run lint         # oxlint (zero warnings required in CI)
npm test             # vitest run
```
