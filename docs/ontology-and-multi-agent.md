# Ontology and multi-agent architecture

This document explains how the AMC IQ research proof of concept connects the Microsoft IQ stack
to a synthetic oncology cohort inside a research-focused academic medical center. It leads with
the connected topology because that is the target architecture. Deterministic local and mock mode
is the safe reproducibility fallback.

## 1. Connected intelligence-layer topology

In connected mode the application uses:

- **Foundry IQ** for protocol text, genomics, pathology, and institutional documents.
- **Fabric IQ** for structured clinical and operational data through the Fabric Data Agent.
- **Work IQ** for care-team workflow context through delegated OBO access.
- **Web IQ** as a Bing-backed Azure AI Search stand-in, because native Web IQ remains limited-access.
- **Optional hosted agents** for enriched narration, while grounded facts remain anchored to the
  structured Fabric eligibility path.

Eligibility verdicts stay grounded in Fabric-native retrieval and evaluation. External web context
is informational only. Fabric IQ and Work IQ both require delegated identity.

## 2. Deterministic synthetic data cohort

The repository ships a deterministic, reproducible cohort under `data/fabric/` (16 entity tables),
driven by the canonical registry `data/registry/registry.yaml`, the single source of truth for IDs
and hero facts.

- **25 patients** (`PT-1042` through `PT-1066`) spanning multiple cancer types and biomarkers,
  with five eligibility archetypes: `clear_eligible`, `borderline`, `hard_excluded`,
  `biomarker_mismatch`, and `treatment_naive_hold`.
- **10 trials** (`NCT99004324` and related IDs) with structured, machine-checkable criteria.
- Longitudinal **labs**, **treatment_history**, **biomarkers**, **comorbidities**,
  **adverse_events**, **recist_assessments**, **consent**, **trial_criteria**, **amendments**,
  **trial_enrollment**, **scheduling_slots**, **coordinator_workload**, plus **sites** and **people**.

The hero thread is pinned and never changes: `PT-1042` (Alex Morgan), metastatic NSCLC, EGFR exon
20 insertion, ECOG 1, CrCl 55 on 2026-05-20 then 48 on 2026-06-18; trial `NCT99004324` requires
CrCl >= 50 with an ambiguous prior-platinum exclusion revised by Amendment 2.

### Regenerate and validate

```powershell
.\.venv\Scripts\python data/fabric/generate.py
.\.venv\Scripts\python tools/validate_consistency.py
```

To add a patient or trial, edit `data/registry/registry.yaml`, then regenerate. See
`data/fabric/README.md` and `data/fabric/data_dictionary.md` for per-table schemas.

### Explore the cohort (read-only API)

| Endpoint | Returns |
|---|---|
| `GET /api/cohort/patients/{patient_id}/trials/{trial_id}/eligibility` | Per-criterion evaluation (`met`, `uncertain`, `not_met`) plus evidence |

Cohort-wide ranking views, such as near-eligible patients or candidate trials, belong on the
Fabric GraphModel. They remain available through Fabric portal chat, but are not exposed in this
application because the published external MCP endpoint is not reliable.

## 3. Ontology

The ontology is the portable semantic contract for the domain: typed entities and typed
relationships between them. It is defined once in `data/ontology/ontology.yaml` and maps onto the
Lakehouse tables, the live Fabric IQ Ontology item, and the standalone `amciq_oncology_graph`
GraphModel used for GQL traversal. The ontology's system-owned child graph remains empty because of
a Fabric preview defect, so it is not attached to the Data Agent.

The standalone graph uses canonical longitudinal identities for `Lab`
(`patient_id + lab_date + lab_type`) and `Treatment` (`patient_id + line + drug_name`) so repeated
patient facts remain separate graph nodes.

### Graph boundary

Graph relationship traversal is optional and disabled in the application by default. The supported
application eligibility path uses the Lakehouse Data Agent; no application-hosted Graph adapter is
included.

The key idea is relationship-aware eligibility: a `Trial` requires `Criteria`, and each `Criterion`
references a concrete fact entity (`Lab`, `Biomarker`, `Treatment`, or a patient demographic) via
`references_entity / param / comparator / value`. That makes "why is patient X blocked for trial Y"
a relationship walk rather than an ad hoc SQL join.

### Eligibility engine (`services/api/app/eligibility.py`)

Outside local mode, eligibility is Fabric-native only. `app/eligibility.py` is the single boundary
for criterion verdicts:

| Piece | Purpose |
|---|---|
| `evaluate_eligibility(settings, patient_id, trial_id)` | The entry point. Returns a `CriterionResult` per criterion. Cached per patient and trial. Live failures do not fall back to local mode. |
| `sources/fabric_eligibility.py` | Retrieval through the Fabric Data Agent plus evaluation through the `amciq-eligibility-evaluator` Foundry agent. |

Borderline renal function and amendment-modified exclusions resolve to `uncertain`, repeat draw or
PI confirmation, rather than hard exclusion. That is why `PT-1042` remains a pending match for
`NCT99004324`. Local mode evaluates the same synthetic criteria against bundled facts without cloud access.

## 4. Multi-agent specialist team

Instead of one agent, the assessment can be produced by a team of domain specialists that run in
parallel and deepen their investigation as they discover leads, reconciled by a synthesizer and critic.
The framework lives in `services/api/app/agents/`.

### Specialists

| Specialist | Owns |
|---|---|
| Eligibility | Full criteria matching (Fabric eligibility) |
| Renal / Labs | Renal function, lab thresholds, trends, staleness |
| Genomics | Biomarker / variant interpretation |
| Protocol | Exclusion interpretation and amendment deltas |
| Workflow | Tasks, ownership, scheduling, capacity |
| Evidence | External literature, registry, guidelines |

Each specialist is implemented twice: a deterministic grounded path whose findings come from the
Fabric eligibility verdicts, and a live narration path over real Foundry agents. The grounded team
remains the reference truth for live-agent output.

### Flow (`agents/team.py`)

```
planner  ->  parallel specialist turns  ->  bounded deepening loop  ->  synthesizer / critic
```

1. **Planner** selects specialists by question intent and keywords.
2. Specialists run and may emit investigation leads.
3. **Deepening** scores leads and dispatches the top ones to the best-suited specialist at the next
   depth, within a hard budget (`max_depth`, `max_leads`, `time_budget_s`). A specialist never
   re-investigates its own lead.
4. **Synthesizer + critic** reconcile findings. Ground truth, the Fabric eligibility verdict,
   always wins on disagreement and the conflict is logged.

Worked examples:

- **Hero `PT-1042` / `NCT99004324`**: renal borderline plus amendment-modified exclusion create two
  workflow deepening hops, schedule a repeat CrCl and route PI confirmation. Overall: `uncertain`.
- **A biomarker-mismatch patient vs. an EGFR trial**: genomics deepens to evidence for candidate
  trials that match the patient's actual profile. Overall: `not_met`.

### Enable it

Off by default. Turn it on in `services/api/.env`:

```
USE_MULTI_AGENT=true
AGENT_MAX_DEPTH=1
AGENT_MAX_LEADS=2
```

When enabled, the specialist team drives the **Assessment Steps** trace in the UI: planned team,
per-specialist turns, deepening hops, critic reconciliation, governed assessment. The hero verdict
remains `likely_eligible_pending`.

### Extend

- **Add a specialist**: implement the `Specialist` protocol (`agents/base.py`), register it in
  `agents/registry.py` and `agents/grounded.py`, then add it to the planner mappings.
- **Add a lead type**: emit a `Lead` with `target_specialist` and `focus_question`; the deepening
  loop handles the rest within budget.

## Optional extensions

- Provision live Foundry specialist agents only after validating latency, cost, and failure behavior
  in the target environment.
- Add scoped Fabric data agents or Graph traversal only after validating the external tool boundary;
  the Lakehouse eligibility path remains the supported default.

The code and ADRs in this repository are the public source of truth for this design.
