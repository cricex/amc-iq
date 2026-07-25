# Ontology & multi-agent architecture

How the AMC IQ demo turns a synthetic clinical cohort into a relationship-aware, multi-agent
trial-readiness assessment. Everything here is **synthetic, no PHI, not clinical decision support**.

This document covers three layers that build on each other:

1. the synthetic **data cohort** (Fabric Lakehouse tables),
2. the **ontology** (typed entities + relationships) and its in-app traversal layer, and
3. the **multi-agent** specialist team (planner, deepening, synthesizer/critic).

---

## 1. Synthetic data cohort

The demo ships a deterministic, reproducible cohort under `data/fabric/` (16 entity tables), driven by
the canonical registry `data/registry/registry.yaml` (the single source of truth for IDs and the hero
facts).

- **25 patients** (`PT-1042`..`PT-1066`) spanning multiple cancer types and biomarkers (EGFR exon 20,
  ALK, KRAS G12C, BRAF V600E, HER2, BRCA, MSI-H, and more) and five eligibility **archetypes**:
  `clear_eligible`, `borderline`, `hard_excluded`, `biomarker_mismatch`, `treatment_naive_hold`.
- **10 trials** (`NCT99004324`..) with structured, machine-checkable criteria.
- Longitudinal **labs** (including a CrCl_CKD-EPI trajectory), **treatment_history**, **biomarkers**,
  **comorbidities**, **adverse_events**, **recist_assessments**, **consent**, **trial_criteria**,
  **amendments**, **trial_enrollment**, **scheduling_slots**, **coordinator_workload**, plus
  **sites**/**people**.

The **hero thread** is pinned and never changes: `PT-1042` (Alex Morgan), metastatic NSCLC, EGFR exon
20 insertion, ECOG 1, CrCl 55 (2026-05-20) then 48 (2026-06-18); trial `NCT99004324` requires
CrCl >= 50 with an ambiguous prior-platinum exclusion revised by Amendment 2.

### Regenerate and validate

```powershell
# Deterministic + idempotent: re-running produces byte-identical tables.
.\.venv\Scripts\python data/fabric/generate.py

# Referential integrity across all 16 tables + hero rows unchanged + every trial has >=1 criterion.
.\.venv\Scripts\python tools/validate_consistency.py    # exit 0 == consistent
```

To add a patient or trial, edit `data/registry/registry.yaml` (roster + seed fields), then regenerate.
See `data/fabric/README.md` and `data/fabric/data_dictionary.md` for the per-table schemas.

### Explore the cohort (read-only API)

Per-(patient, trial) eligibility is exposed as a read-only endpoint so the whole cohort is usable
beyond the hero. It is evaluated on Fabric-native services (see below):

| Endpoint | Returns |
|---|---|
| `GET /api/cohort/patients/{patient_id}/trials/{trial_id}/eligibility` | Per-criterion evaluation (met / uncertain / not_met + evidence) |

Cohort-wide **ranking** views (near-eligible patients, candidate trials) are relationship traversals
that belong on the Fabric **GraphModel**. They remain available through Fabric portal chat, but are
not exposed in AMC IQ because the published external MCP endpoint is not reliable.

---

## 2. Ontology

The ontology is the portable **semantic contract** for the domain: typed entities and typed
relationships between them. It is defined once in `data/ontology/ontology.yaml` and maps onto the
Lakehouse tables, the live **Fabric IQ Ontology** item, and the standalone
`amciq_oncology_graph` GraphModel used for GQL traversal. The Ontology's system-owned child graph
remains empty because of a Fabric preview defect, so it is not attached to the Data Agent.
The standalone graph uses canonical longitudinal identities for `Lab`
(`patient_id + lab_date + lab_type`) and `Treatment` (`patient_id + line + drug_name`) so repeated
patient facts remain separate graph nodes.

### Graph boundary

Graph relationship traversal is optional and disabled in the application by default. The supported
application eligibility path uses the Lakehouse Data Agent; no application-hosted Graph adapter is
included.

The key idea is **relationship-aware eligibility**: a `Trial` requires `Criteria`, and each
`Criterion` references a concrete fact entity (`Lab`, `Biomarker`, `Treatment`, or a patient
demographic) via `references_entity / param / comparator / value`. So "why is patient X blocked for
trial Y" is answered by walking criteria to facts, not with ad hoc SQL.

### Eligibility engine (`services/api/app/eligibility.py`)

Outside local mock mode, eligibility is **Fabric-native only**. `app/eligibility.py` is the single
boundary for criterion verdicts:

| Piece | Purpose |
|---|---|
| `evaluate_eligibility(settings, patient_id, trial_id)` | The one entry point. Returns a `CriterionResult` (status `met` / `uncertain` / `not_met` + evidence) per criterion. Cached per (patient, trial). Live failures do not fall back to mock. |
| `sources/fabric_eligibility.py` | Retrieval (Fabric Data Agent NL2SQL for criteria + patient facts) + evaluation (the `amciq-eligibility-evaluator` Foundry agent reasoning over the facts). |

Borderline renal function and amendment-modified exclusions resolve to `uncertain` (repeat draw / PI
confirmation) rather than a hard exclusion, per the evaluator's rules. This is why PT-1042 remains
a *pending* match for NCT99004324. Local mock mode evaluates the same synthetic criteria against the
bundled patient facts without cloud access.

---

## 3. Multi-agent specialist team

Instead of one agent, the assessment is produced by a **team of domain specialists** that run in
parallel and **deepen** their investigation as they discover leads, reconciled by a synthesizer/critic.
The framework lives in `services/api/app/agents/`.

### Specialists

| Specialist | Owns |
|---|---|
| Eligibility | Full criteria matching (Fabric eligibility) |
| Renal / Labs | Renal function, lab thresholds, trends, staleness |
| Genomics | Biomarker / variant interpretation |
| Protocol | Exclusion interpretation and amendment deltas |
| Workflow | Tasks, ownership, scheduling, capacity |
| Evidence | External literature / registry / guidelines |

Each is implemented twice: a deterministic **grounded** specialist whose findings come from the
Fabric eligibility verdicts (`agents/grounded.py`, the default), and a **live** adapter that narrates
the core roles over real Foundry agents. The grounded team is also the ground-truth reference the live
agents are checked against.

### Flow (`agents/team.py`)

```
planner  ->  parallel specialist turns  ->  bounded deepening loop  ->  synthesizer / critic
(select)     (agents/grounded.py)           (agents/deepening.py)       (agents/synthesizer.py)
```

1. **Planner** (`agents/planner.py`) selects specialists by question intent + keywords (deterministic,
   explainable).
2. Specialists run and each may emit **investigation Leads**.
3. **Deepening** (`agents/deepening.py`) scores leads and dispatches the top ones to the best-suited
   specialist at the next depth, within a hard `Budget` (`max_depth`, `max_leads`, `time_budget_s`).
   A specialist never re-investigates its own lead.
4. **Synthesizer + Critic** (`agents/synthesizer.py`) reconcile findings. **Ground truth (the
   Fabric eligibility verdict) always wins** on any disagreement, and the disagreement is logged as a
   conflict, never silently dropped.

Worked examples (deterministic):

- **Hero `PT-1042` / `NCT99004324`**: renal borderline + amendment-modified exclusion -> two workflow
  deepening hops (schedule a repeat CrCl, route PI confirmation). Overall: `uncertain`.
- **A biomarker-mismatch patient vs an EGFR trial**: biomarker mismatch -> genomics deepens to
  evidence for candidate trials that match the patient's actual profile. Overall: `not_met`.

### Enable it

Off by default (the single-agent path is unchanged). Turn it on in `services/api/.env`:

```
USE_MULTI_AGENT=true
AGENT_MAX_DEPTH=1
AGENT_MAX_LEADS=2
```

When on, the specialist team drives the **Assessment Steps** trace in the UI: planned team ->
per-specialist turns -> deepening hops -> Critic reconciliation -> governed assessment. The hero
assessment (`likely_eligible_pending`) is preserved.

### Extend

- **Add a specialist**: implement the `Specialist` protocol (`agents/base.py`), register it in
  `agents/registry.py` + `agents/grounded.py`, and add it to the planner's intent/keyword maps.
- **Add a lead type**: emit a `Lead` from a specialist with a `target_specialist` and `focus_question`;
  the deepening loop handles the rest within budget.

---

## Optional extensions

- Provision live Foundry specialist agents only after validating latency, cost, and failure behavior
  in the operator's environment.
- Add scoped Fabric data agents or Graph traversal only after validating the selected external tool
  boundary; the Lakehouse eligibility path remains the supported default.

The code and ADRs in this repository are the public source of truth for this design.
