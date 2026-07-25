# Data model

The demo's structured layer is a deterministic, reproducible synthetic cohort stored as Fabric
Lakehouse Delta tables (CSV source under `data/fabric/`). The schema is defined by the ontology
in `data/ontology/ontology.yaml`; the generator in `data/fabric/generate.py` writes all 16 tables
from that ontology and from `data/registry/registry.yaml`.

## Entity graph

```
Site ◄──────────── Patient ──────────► Trial
                      │                   │
           ┌──────────┼──────────┐        ├─ TrialCriteria ──► Amendment
           ▼          ▼          ▼        └─ TrialEnrollment
       Biomarker     Lab     Treatment
           │          │          │
     Comorbidity  AdverseEvent  RECISTAssessment
           │
        Consent ──────────────────────────────────► Trial
           │
    CoordinatorWorkload (Person)
    SchedulingSlot
```

## Tables and keys

| Table | Entity | Primary key | Feeds |
|---|---|---|---|
| `sites.csv` | Site | `site_id` | Clinic locations, scheduling slots |
| `people.csv` | Person | `person_id` | PIs, coordinators, navigators |
| `patient_registry.csv` | Patient | `patient_id` | Demographics, ECOG, archetype, coordinator/PI |
| `biomarkers.csv` | Biomarker | `patient_id, marker` | Tested variants (EGFR exon 20, ALK, KRAS, …) |
| `labs.csv` | Lab | `patient_id, lab_date, lab_type` | Longitudinal labs; CrCl_CKD-EPI trajectory |
| `treatment_history.csv` | Treatment | `patient_id, line, drug_name` | Prior/current therapy lines |
| `comorbidities.csv` | Comorbidity | `patient_id, condition` | Coexisting conditions |
| `adverse_events.csv` | AdverseEvent | `patient_id, ae_id` | CTCAE-graded events |
| `recist_assessments.csv` | RECISTAssessment | `patient_id, assessment_date` | RECIST 1.1 timepoints |
| `consent.csv` | Consent | `patient_id, trial_id` | Informed-consent state |
| `trials.csv` | Trial | `trial_id` | Trial catalog, CrCl threshold, biomarker requirement |
| `trial_criteria.csv` | Criterion | `trial_id, criterion_id` | Machine-checkable eligibility criteria |
| `amendments.csv` | Amendment | `trial_id, amendment_id` | Protocol amendments |
| `trial_enrollment.csv` | Enrollment | `patient_id, trial_id` | Screening workflow edge |
| `coordinator_workload.csv` | CoordinatorWorkload | `coordinator_id` | Capacity snapshot |
| `scheduling_slots.csv` | Slot | `slot_id` | Availability by site, type, and patient |

Full column-level schema is in [`data/fabric/data_dictionary.md`](../data/fabric/data_dictionary.md).

## Criterion contract (`trial_criteria.csv`)

Each criterion carries:

```
criterion_id | trial_id | kind (inclusion|exclusion) | category | description
references_entity | param | comparator | value
```

The `references_entity / param / comparator / value` quadruple is the ontology edge: eligibility
is a traversal from `Criterion` to the patient's concrete fact node, not an ad hoc SQL join.

| `references_entity` | `param` | Example |
|---|---|---|
| `Patient` | `cancer_type` | `NSCLC Adenocarcinoma` |
| `Patient` | `ecog_ps` | `<= 1` |
| `Biomarker` | *(marker name)* | `EGFR exon 20 insertion` |
| `Lab` | `CrCl_CKD-EPI` | `>= 50` |
| `Treatment` | `drug_class` | `Platinum` (exclusion) |

## Amendments contract (`amendments.csv`)

Amendments carry `modifies_criterion_id`, which points to a `trial_criteria.criterion_id`. The
mock eligibility evaluator (`eligibility.py`) uses this to convert an exclusion from `not_met` to
`uncertain` when an amendment requires investigator confirmation instead of an automatic exclusion.

**Hero mechanism:** `AMD-2` on trial `NCT99004324` modifies criterion `NCT99004324-RX` (prior
platinum exclusion). The patient (`PT-1042`) has prior Carboplatin (line 1), so without the
amendment the criterion is `not_met`. With the amendment it becomes `uncertain`, requiring PI
confirmation.

## Patient archetypes

The 25 synthetic patients are assigned one of five archetypes that drive eligibility variation:

| Archetype | Behaviour |
|---|---|
| `clear_eligible` | All criteria met; advances to screening normally |
| `borderline` | One or more criteria `uncertain` (e.g. CrCl near threshold) |
| `hard_excluded` | One or more criteria `not_met` (e.g. wrong biomarker or prior therapy) |
| `biomarker_mismatch` | Required biomarker absent or different variant |
| `treatment_naive_hold` | All criteria otherwise met, but prior-therapy sequencing needs PI confirmation |

## Referential integrity

`data/fabric/generate.py` asserts and `tools/validate_consistency.py` re-checks:

- All `patient_id` foreign keys in every non-registry table resolve to `patient_registry`.
- All `trial_id` foreign keys resolve to `trials`.
- All `treating_oncologist_id` / `coordinator_id` resolve to `people` / `coordinator_workload`.
- All `site_id` resolve to `sites`.
- Every `amendments.modifies_criterion_id` resolves to `trial_criteria`.
- Every trial has at least one criterion.
- Populated `scheduling_slots.assigned_patient_id` resolves to `patient_registry`.

## Hero thread (pinned, never drifts)

The generator asserts these values before writing:

| Fact | Value |
|---|---|
| Patient | `PT-1042` / *Alex Morgan* |
| Diagnosis | Metastatic NSCLC, EGFR exon 20 insertion, ECOG 1 |
| CrCl trajectory | 55 mL/min (2026-05-20) → 48 mL/min (2026-06-18) |
| Trial | `NCT99004324`, CrCl threshold ≥ 50 mL/min |
| Amendment | `AMD-2` modifies platinum exclusion to PI-confirmation required |
| Expected verdict | `likely_eligible_pending` |

## Regenerate and validate

```powershell
# Regenerate all 16 tables (deterministic, byte-identical output)
.\.venv\Scripts\python data/fabric/generate.py

# Validate referential integrity and hero rows
.\.venv\Scripts\python tools/validate_consistency.py
```

## Cohort exploration API

```
GET /api/cohort/patients/{patient_id}/trials/{trial_id}/eligibility
```

Returns a list of `CriterionResult` objects (`criterion`, `kind`, `category`, `description`,
`status`, `evidence`) for any (patient, trial) pair in the cohort. Evaluated via Fabric-native
services in live mode; via the deterministic mock evaluator offline.
