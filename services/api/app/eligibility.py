"""Eligibility boundary: deterministic local mock mode or Fabric-native live evaluation.

This module owns the neutral domain types (``CriterionResult`` and the status vocabulary) and the one
public entry point, ``evaluate_eligibility``, that every caller (the orchestrator, the specialist
team, the cohort endpoint) uses to get per-criterion verdicts.

Outside the narrowly gated local mock mode, evaluation runs entirely on Fabric-native services
(Fabric Data Agent fact retrieval + the Foundry eligibility-evaluator agent, in
``app.sources.fabric_eligibility``). Live failures raise rather than silently substituting local
logic. Local mock mode traverses the selected trial's synthetic criteria against the selected
patient's synthetic facts to produce a stable offline verdict for the public walkthrough.

Synthetic only. No PHI. Not clinical decision support.
"""
from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings

logger = logging.getLogger("amciq.eligibility")

MET = "met"
UNCERTAIN = "uncertain"
NOT_MET = "not_met"
RENAL_MARGIN = 5
POSITIVE_BIOMARKER_STATUSES = frozenset(
    {"Detected", "Amplified", "Positive", "High", "MSI-High"}
)


class EligibilityUnavailable(RuntimeError):
    """Raised when the Fabric-native eligibility evaluation cannot be completed (Data Agent or the
    Foundry evaluator is unreachable/failed). There is no local fallback, so callers surface this as
    a controlled "service unavailable" rather than a generic 500."""


@dataclass(frozen=True)
class CriterionResult:
    """One evaluated trial criterion for a patient."""

    criterion: str  # criterion_id, e.g. "NCT99004324-REN"
    kind: str  # inclusion | exclusion
    category: str  # diagnosis | biomarker | performance | renal | prior_therapy | ...
    description: str
    status: str  # met | uncertain | not_met
    evidence: str


# Per-(patient, trial) cache so a single request (orchestrator + specialist team + ground truth) does
# not trigger several ~45s Fabric round trips for the same pair. The evaluation is deterministic for a
# given cohort snapshot, so caching across requests is also acceptable for the proof-of-concept.
_cache: dict[tuple[str, str, str], list[CriterionResult]] = {}


def clear_cache() -> None:
    """Drop the memoized eligibility results (used by tests)."""
    _cache.clear()


def evaluate_eligibility_mock(
    settings: Settings, patient_id: str, trial_id: str
) -> list[CriterionResult]:
    """Evaluate the selected synthetic patient against the selected trial's criteria."""
    fabric_dir = settings.DATA_DIR / "fabric"
    patient = _find_row(
        _read_csv(fabric_dir / "patient_registry.csv"),
        key="patient_id",
        value=patient_id,
    )
    criteria = [
        row
        for row in _read_csv(fabric_dir / "trial_criteria.csv")
        if row["trial_id"] == trial_id
    ]
    if not criteria:
        raise ValueError(f"Unknown trial_id: {trial_id}")

    biomarkers = _patient_rows(fabric_dir / "biomarkers.csv", patient_id)
    labs = _patient_rows(fabric_dir / "labs.csv", patient_id)
    treatments = _patient_rows(fabric_dir / "treatment_history.csv", patient_id)
    amended_criteria = {
        row["modifies_criterion_id"]
        for row in _read_csv(fabric_dir / "amendments.csv")
        if row.get("modifies_criterion_id")
    }
    results = [
        _evaluate_mock_criterion(
            patient=patient,
            criterion=criterion,
            biomarkers=biomarkers,
            labs=labs,
            treatments=treatments,
            amended_criteria=amended_criteria,
        )
        for criterion in criteria
    ]
    if patient["archetype"] == "treatment_naive_hold" and all(
        result.status == MET for result in results
    ):
        results = [
            (
                CriterionResult(
                    criterion=result.criterion,
                    kind=result.kind,
                    category=result.category,
                    description=result.description,
                    status=UNCERTAIN,
                    evidence=(
                        "Treatment-naive sequencing requires investigator confirmation before "
                        "formal screening."
                    ),
                )
                if result.category == "prior_therapy"
                else result
            )
            for result in results
        ]
    return results


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _patient_rows(path: Path, patient_id: str) -> list[dict[str, str]]:
    return [row for row in _read_csv(path) if row["patient_id"] == patient_id]


def _find_row(
    rows: list[dict[str, str]], *, key: str, value: str
) -> dict[str, str]:
    try:
        return next(row for row in rows if row[key] == value)
    except StopIteration as exc:
        raise ValueError(f"Unknown {key}: {value}") from exc


def _evaluate_mock_criterion(
    *,
    patient: dict[str, str],
    criterion: dict[str, str],
    biomarkers: list[dict[str, str]],
    labs: list[dict[str, str]],
    treatments: list[dict[str, str]],
    amended_criteria: set[str],
) -> CriterionResult:
    references = criterion["references_entity"]
    param = criterion["param"]
    required = criterion["value"]

    if references == "Patient" and param == "cancer_type":
        actual = patient["cancer_type"]
        compatible = _cancer_type_compatible(actual, required)
        status = MET if compatible else NOT_MET
        evidence = (
            f"Cancer type {actual} matches {required}."
            if compatible
            else f"Cancer type {actual} does not match required {required}."
        )
    elif references == "Patient" and param == "ecog_ps":
        actual = int(patient["ecog_ps"])
        threshold = int(required)
        status = MET if actual <= threshold else NOT_MET
        evidence = (
            f"ECOG {actual} is within the <= {threshold} limit."
            if status == MET
            else f"ECOG {actual} exceeds the <= {threshold} limit."
        )
    elif references == "Biomarker":
        status, evidence = _evaluate_biomarker(required, biomarkers)
    elif references == "Lab" and param == "CrCl_CKD-EPI":
        status, evidence = _evaluate_renal(int(required), labs)
    elif references == "Treatment" and param == "drug_class":
        status, evidence = _evaluate_prior_therapy(
            criterion=criterion,
            required=required,
            treatments=treatments,
            amended_criteria=amended_criteria,
        )
    else:
        status = UNCERTAIN
        evidence = f"Criterion {criterion['criterion_id']} could not be auto-evaluated."

    return CriterionResult(
        criterion=criterion["criterion_id"],
        kind=criterion["kind"],
        category=criterion["category"],
        description=criterion["description"],
        status=status,
        evidence=evidence,
    )


def _cancer_type_compatible(patient_type: str, required_type: str) -> bool:
    if required_type == "Solid tumor" or patient_type == required_type:
        return True
    if patient_type.startswith("NSCLC") and required_type.startswith("NSCLC"):
        return True
    return patient_type.startswith("Breast") and required_type.startswith("Breast")


def _evaluate_biomarker(
    required: str, biomarkers: list[dict[str, str]]
) -> tuple[str, str]:
    if not biomarkers:
        return UNCERTAIN, f"{required} status unknown: no molecular report is available."
    core = required.split("(")[0].strip().casefold()
    pattern = re.compile(rf"\b{re.escape(core)}\b")
    present = any(
        pattern.search(row["marker"].casefold())
        and row["status"] in POSITIVE_BIOMARKER_STATUSES
        for row in biomarkers
    )
    if present:
        return MET, f"{required} is documented as present."
    listed = ", ".join(row["marker"] for row in biomarkers)
    return NOT_MET, f"{required} is not present ({listed})."


def _evaluate_renal(
    threshold: int, labs: list[dict[str, str]]
) -> tuple[str, str]:
    series = sorted(
        (
            (row["lab_date"], float(row["value"]))
            for row in labs
            if row["lab_type"] == "CrCl_CKD-EPI"
        ),
        key=lambda item: item[0],
    )
    if not series:
        return UNCERTAIN, "No CrCl_CKD-EPI result is available."
    latest_date, latest = series[-1]
    prior = series[-2][1] if len(series) > 1 else None
    prior_text = f"; prior {prior:g}" if prior is not None else ""
    if latest >= threshold:
        return (
            MET,
            f"Latest CrCl {latest:g} mL/min on {latest_date} is at or above "
            f"{threshold}{prior_text}.",
        )
    if latest >= threshold - RENAL_MARGIN or (prior is not None and prior >= threshold):
        return (
            UNCERTAIN,
            f"Latest CrCl {latest:g} mL/min on {latest_date} is just below "
            f"{threshold}{prior_text}; repeat testing and investigator confirmation are needed.",
        )
    return (
        NOT_MET,
        f"Latest CrCl {latest:g} mL/min on {latest_date} is below {threshold}{prior_text}.",
    )


def _evaluate_prior_therapy(
    *,
    criterion: dict[str, str],
    required: str,
    treatments: list[dict[str, str]],
    amended_criteria: set[str],
) -> tuple[str, str]:
    matches = [row for row in treatments if row["drug_class"] == required]
    drugs = ", ".join(f"{row['drug_name']} (line {row['line']})" for row in matches)
    if criterion["kind"] == "inclusion":
        if matches:
            return MET, f"Required prior {required} is documented ({drugs})."
        return NOT_MET, f"No prior {required} is documented."
    if not matches:
        return MET, f"No prior {required} is documented; exclusion is not triggered."
    if criterion["criterion_id"] in amended_criteria:
        return (
            UNCERTAIN,
            f"Prior {required} is documented ({drugs}), but an amendment requires "
            "investigator confirmation.",
        )
    return NOT_MET, f"Prior {required} is documented ({drugs}); exclusion is triggered."


def evaluate_eligibility(
    settings: Settings, patient_id: str, trial_id: str, *, use_cache: bool = True
) -> list[CriterionResult]:
    """Evaluate criteria through the strict local-mock/live boundary.

    Live/production failures never fall back to the deterministic evaluator."""
    backend = "mock" if settings.anonymous_mock_enabled else "fabric"
    key = (backend, patient_id, trial_id)
    if use_cache and key in _cache:
        return _cache[key]

    if settings.anonymous_mock_enabled:
        results = evaluate_eligibility_mock(settings, patient_id, trial_id)
    else:
        # Imported lazily so this module has no import-time dependency on the Fabric SDK stack (and
        # to keep the type-only import in ``fabric_eligibility`` free of a circular import).
        from app.sources.fabric_eligibility import evaluate_eligibility_fabric

        try:
            results = evaluate_eligibility_fabric(settings, patient_id, trial_id)
        except Exception as exc:  # noqa: BLE001 - normalize every Fabric-side failure to one controlled type
            raise EligibilityUnavailable(
                f"Fabric eligibility evaluation failed for {patient_id}/{trial_id}: {exc}"
            ) from exc
    if use_cache:
        _cache[key] = results
    logger.info(
        "Eligibility for %s/%s: %d criteria (%s)",
        patient_id,
        trial_id,
        len(results),
        backend,
    )
    return results
