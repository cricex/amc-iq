# Canonical Scenario Entity Registry

`registry.yaml` is the **single source of truth** for every synthetic identifier in this scenario:
patient IDs/MRNs, trial NCT IDs, people (PI, coordinator, navigator), sites, key dates, and the
scenario-critical lab values (e.g. the hero patient's CrCl 48 vs the trial's ≥50 threshold).

## Why this exists
The scenario must feel like one coherent institution. The same hero patient (`PT-1042` / *Alex Morgan*),
the same hero trial (`NCT99004324`), and the same coordinator (`COORD-01` / *Dana Whitfield*) must
appear **identically** across:

- **Foundry IQ**, protocol, IRB/consent, notes, pathology, genomics documents
- **Fabric IQ**, Lakehouse Delta tables (registry, labs, treatments, trials, scheduling)
- **Work IQ**, tumor board summary, coordinator tasks, referral queue, PI availability
- **Web IQ**, cached external trial-registry / guideline snapshots
- **UI**, patient card, evidence table, next-action panel

## Rule
**Never hand-type an ID or name.** All generators, document templates, table loaders, M365 seed
scripts, and UI fixtures import from `registry.yaml`. Change an entity once here and it propagates
everywhere.

## Conventions
- Trial IDs use the synthetic range `NCT99xxxxxx`. All current IDs returned HTTP 404 from the
  ClinicalTrials.gov v2 study endpoint on 2026-07-25; recheck immediately before publication rather
  than assuming the range can never collide.
- `meta.scenario_today` fixes "now" (`2026-07-06`) so time-relative content is deterministic.
- `expected_outcome` encodes the target scenario answer, used to align the talk-track and (later)
  automated evaluation.
