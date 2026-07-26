# Fabric Lakehouse Synthetic Data Dictionary

Synthetic data only. No PHI. Identifiers align to `data/registry/registry.yaml` and the schema in
`data/ontology/ontology.yaml`. All 16 tables are produced by `data/fabric/generate.py`.

## sites.csv
Clinic locations (one row per site).
- site_id: Primary site key, e.g., SITE-01.
- display_name: Full clinic name.
- short_name: Short clinic label.

## people.csv
Care-team members (principal investigators, coordinators, navigators).
- person_id: Primary person key, e.g., PI-01, COORD-01.
- display_name: Person display name.
- role: Role description.
- site_id: Joins to sites.site_id.

## patient_registry.csv
One row per synthetic oncology patient.
- patient_id: Primary patient key, e.g., PT-1042.
- mrn: Synthetic medical record number.
- display_name: Synthetic display name.
- sex: F or M.
- age: Age in years for scenario context.
- ecog_ps: ECOG performance status.
- primary_diagnosis: Diagnosis label.
- diagnosis_icd10: ICD-10 diagnosis code.
- cancer_type: Cohort cancer-type label (e.g., NSCLC, Breast HER2+, Colorectal).
- stage: Cancer stage.
- staging_date: Date stage was recorded.
- treating_oncologist_id: Person key for treating oncologist; joins to people.person_id.
- coordinator_id: Trial coordinator key; joins to coordinator_workload.coordinator_id.
- archetype: Eligibility-story label: clear_eligible, borderline, hard_excluded, biomarker_mismatch, treatment_naive_hold.

## biomarkers.csv
One row per tested variant/marker for a patient (positive or negative).
- patient_id: Joins to patient_registry.patient_id.
- marker: Marker/variant label as tested.
- gene: Gene symbol (e.g., EGFR, ERBB2, CD274, MMR).
- category: mutation, fusion, amplification, expression, msi_status, or other.
- status: Interpreted result (Detected/Not detected, Amplified/Not amplified, High/Low, MSI-High/MSS, Positive/Negative).
- method: Assay method (e.g., NGS (tumor), FISH, IHC 22C3, IHC/PCR).
- assessed_date: Date the marker was assessed.

## labs.csv
Longitudinal laboratory results, including a CrCl_CKD-EPI trajectory (>= 2 readings) per patient.
- patient_id: Joins to patient_registry.patient_id.
- lab_date: Date the lab was collected.
- lab_type: Lab name, including CrCl_CKD-EPI, WBC, ANC, Hgb, PLT, ALT, AST, Creatinine.
- value: Numeric lab result.
- unit: Result unit.
- ref_low: Lower reference bound.
- ref_high: Upper reference bound.

## treatment_history.csv
Prior and current systemic treatment history.
- patient_id: Joins to patient_registry.patient_id.
- drug_name: Regimen or drug name (None for treatment-naive).
- drug_class: Treatment class.
- line: Treatment line number; 0 indicates treatment-naive.
- start_date: Treatment start date when applicable.
- end_date: Treatment end date when applicable.
- cycles_completed: Number of completed cycles.
- best_response: Best documented response.
- reason_stopped: Reason treatment ended or current status.

## comorbidities.csv
Coexisting conditions.
- patient_id: Joins to patient_registry.patient_id.
- condition: Condition label. Reduced renal function (CKD stage 3) is recorded when latest CrCl is below the normal reference floor.
- icd10: ICD-10 code.
- status: Condition status (e.g., Active).
- noted_date: Date the condition was noted.

## adverse_events.csv
CTCAE-graded adverse events tied to specific drugs.
- patient_id: Joins to patient_registry.patient_id.
- ae_id: Adverse-event key, unique per patient (e.g., AE-1042-1).
- term: Adverse-event term.
- ctcae_grade: CTCAE grade (1-5).
- related_drug: Drug name the event is attributed to; joins conceptually to treatment_history.drug_name.
- onset_date: Onset date.
- resolved_date: Resolution date when resolved; blank when ongoing.
- outcome: Outcome label (e.g., Resolved, Ongoing (managed)).

## recist_assessments.csv
RECIST 1.1 tumor assessments (Baseline plus follow-ups).
- patient_id: Joins to patient_registry.patient_id.
- assessment_date: Assessment date.
- timepoint: Baseline or Follow-up.
- overall_response: Baseline, Complete response, Partial response, Stable disease, or Progressive disease.
- target_lesion_sum_mm: Sum of target-lesion diameters in millimeters.
- method: Imaging method and RECIST version.

## consent.csv
Informed-consent state per patient/trial.
- patient_id: Joins to patient_registry.patient_id.
- trial_id: Joins to trials.trial_id.
- consent_status: Not started, In progress, or Signed.
- consent_version: Informed-consent form version.
- consent_date: Date consent was signed; blank when not signed.

## trials.csv
Synthetic trial catalog.
- trial_id: Primary trial key, using out-of-range synthetic NCT99 identifiers.
- short_title: Short trial label.
- condition: Target condition or population.
- cancer_type: Trial cancer-type label (Solid tumor for basket studies).
- phase: Trial phase (e.g., Phase 2).
- status: Recruiting status.
- site_id: Site key; joins to sites.site_id.
- ecog_max: Maximum allowed ECOG performance status.
- crcl_min: Minimum creatinine clearance in mL/min (CKD-EPI).
- biomarker_required: Required biomarker, blank when none.
- latest_protocol: Latest protocol version label (e.g., Amendment 2).

## trial_criteria.csv
Machine-checkable eligibility criteria (one row per criterion).
- trial_id: Joins to trials.trial_id.
- criterion_id: Criterion key, e.g., NCT99004324-REN.
- kind: inclusion or exclusion.
- category: diagnosis, biomarker, performance, renal, prior_therapy, hepatic, hematologic, or consent.
- description: Human-readable criterion text.
- references_entity: The fact entity the criterion evaluates (Patient, Biomarker, Lab, Treatment).
- param: The attribute evaluated (e.g., CrCl_CKD-EPI, ecog_ps, marker, drug_class, cancer_type).
- comparator: Comparison operator or relation (>=, <=, matches, present, excludes).
- value: Threshold or target value.

## amendments.csv
Protocol amendments.
- trial_id: Joins to trials.trial_id.
- amendment_id: Amendment key within a trial (e.g., AMD-2).
- label: Amendment label.
- effective_date: Effective date.
- modifies_criterion_id: Criterion the amendment modifies; joins to trial_criteria.criterion_id.
- summary: Summary of the change.

## trial_enrollment.csv
Patient-to-trial screening and enrollment workflow.
- patient_id: Joins to patient_registry.patient_id.
- trial_id: Joins to trials.trial_id.
- status: Workflow status such as Pre-screening, Screening, or Not enrolled.
- screening_date: Screening or pre-screening date.
- coordinator_id: Owning coordinator; joins to coordinator_workload.coordinator_id.
- notes: Coordinator-facing eligibility notes.

## coordinator_workload.csv
Trial coordinator capacity snapshot.
- coordinator_id: Coordinator key; joins to patient_registry.coordinator_id and people.person_id.
- display_name: Coordinator display name.
- open_screenings: Count of active screening workflows.
- pending_tasks: Count of pending work items.
- capacity_this_week: Estimated screening task capacity for the week.

## scheduling_slots.csv
Clinic scheduling availability.
- slot_id: Primary slot key.
- site_id: Site key; joins to sites.site_id.
- slot_type: Appointment type, such as Screening, Lab repeat, or PI consult.
- date: Slot date.
- time: Slot start time.
- available: true when unassigned and open.
- assigned_patient_id: Optional patient assignment; populated values join to patient_registry.patient_id.

## Relationships
- patient_registry.patient_id is referenced by biomarkers, labs, treatment_history, comorbidities, adverse_events, recist_assessments, consent, trial_enrollment, and populated scheduling_slots.assigned_patient_id.
- trials.trial_id is referenced by trial_criteria, amendments, trial_enrollment, and consent.
- trial_criteria.criterion_id is referenced by amendments.modifies_criterion_id.
- patient_registry.treating_oncologist_id references people.person_id; patient_registry.coordinator_id references coordinator_workload.coordinator_id.
- sites.site_id is referenced by people.site_id, trials.site_id, and scheduling_slots.site_id.
