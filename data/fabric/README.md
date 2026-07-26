# Fabric Lakehouse tables (generated)

Synthetic, internally consistent structured data for the AMC IQ proof of concept. **Synthetic only. No PHI. Not
clinical decision support.** Every identifier traces back to `data/registry/registry.yaml`, and every
table maps one-to-one to an entity in `data/ontology/ontology.yaml`.

## Regenerate

```bash
python data/fabric/generate.py
```

The generator reads `data/registry/registry.yaml` (source of truth) and `data/ontology/ontology.yaml`
(schema), then writes all 16 CSV tables below. It is:

- **Deterministic**: all synthetic variation is drawn from a fixed master seed combined with each
  entity key (`random.Random("amc-iq-fabric-2026:...")`), so output is byte-identical on every run,
  with canonical LF line endings regardless of operating system or Python hash seeding.
- **Idempotent**: running it twice produces the same files.
- **Hero-preserving**: the four original patients (`PT-1042..PT-1045`) and four original trials
  (`NCT99004324`, `NCT99004325`, `NCT99004326`, `NCT99004401`) are written from pinned literal values.
  The generator asserts the hero CrCl trajectory (55 mL/min on 2026-05-20, then 48 mL/min on
  2026-06-18) and the hero trial renal threshold (CrCl >= 50) before writing, so the borderline
  eligibility story can never drift.

## Entities and tables

| Table | Entity | Key | Feeds |
|---|---|---|---|
| `sites.csv` | Site | `site_id` | clinic locations |
| `people.csv` | Person | `person_id` | PIs, coordinators, navigators |
| `patient_registry.csv` | Patient | `patient_id` | cohort demographics + archetype |
| `biomarkers.csv` | Biomarker | `patient_id, marker` | tested variants/markers |
| `labs.csv` | Lab | `patient_id, lab_date, lab_type` | longitudinal labs (CrCl trajectory) |
| `treatment_history.csv` | Treatment | `patient_id, line, drug_name` | prior/current therapy lines |
| `comorbidities.csv` | Comorbidity | `patient_id, condition` | coexisting conditions |
| `adverse_events.csv` | AdverseEvent | `patient_id, ae_id` | CTCAE-graded events tied to drugs |
| `recist_assessments.csv` | RECISTAssessment | `patient_id, assessment_date` | RECIST 1.1 timepoints |
| `consent.csv` | Consent | `patient_id, trial_id` | informed-consent state |
| `trials.csv` | Trial | `trial_id` | trial catalog + thresholds |
| `trial_criteria.csv` | Criterion | `trial_id, criterion_id` | machine-checkable eligibility criteria |
| `amendments.csv` | Amendment | `trial_id, amendment_id` | protocol amendments (incl. hero Amendment 2) |
| `trial_enrollment.csv` | Enrollment | `patient_id, trial_id` | screening workflow edge |
| `coordinator_workload.csv` | CoordinatorWorkload | `coordinator_id` | coordinator capacity snapshot |
| `scheduling_slots.csv` | Slot | `slot_id` | clinic scheduling availability |

Column order follows each entity's `key` plus `attributes` in `ontology.yaml`.

## Referential integrity

The generator verifies (and `tools/validate_consistency.py` re-checks) that:

- every `patient_id` in `labs`, `biomarkers`, `treatment_history`, `comorbidities`, `adverse_events`,
  `recist_assessments`, `consent`, and `trial_enrollment` resolves to a `patient_registry` row;
- every `trial_id` in `trial_criteria`, `amendments`, `trial_enrollment`, and `consent` resolves to a
  `trials` row;
- every `treating_oncologist_id` / `coordinator_id` resolves to `people` / `coordinator_workload`;
- every `site_id` resolves to `sites`;
- every `amendments.modifies_criterion_id` resolves to a `trial_criteria.criterion_id`;
- every trial has at least one criterion;
- populated `scheduling_slots.assigned_patient_id` values resolve to `patient_registry`.

## Relationship-aware eligibility

`trial_criteria` is the heart of the ontology story: each criterion carries
`references_entity / param / comparator / value`, so eligibility is a graph traversal from a
`Criterion` to the patient's concrete facts (`Lab`, `Biomarker`, `Treatment`, or `Patient`
demographic) rather than an ad hoc SQL join. The live Fabric IQ Ontology and the Fabric-native
eligibility engine (`services/api/app/eligibility.py`) both honor this contract.

For the hero: criterion `NCT99004324-REN` (`CrCl_CKD-EPI >= 50`) evaluates **uncertain** against the
patient's latest CrCl of 48 (with a prior 55 above threshold), and criterion `NCT99004324-RX`
(prior platinum exclusion) evaluates **uncertain** because `amendments.AMD-2` modifies it to a
line-of-therapy interpretation requiring PI confirmation.
