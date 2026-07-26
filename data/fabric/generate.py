"""Deterministic Fabric Lakehouse table generator for the AMC IQ proof-of-concept.

This script reads the two portable contracts:

  * ``data/registry/registry.yaml`` (single source of truth for identifiers and hero facts), and
  * ``data/ontology/ontology.yaml`` (the entity/relationship schema),

and emits one CSV per ontology entity under ``data/fabric/`` with full referential integrity. Every
foreign key (patient_id, trial_id, coordinator_id, site_id, criterion_id) resolves to a defined row.

Design principles (OSS, reproducible):
  * Deterministic: all synthetic variation is drawn from a fixed master seed combined with each
    entity key, so re-running produces byte-identical output regardless of process hash seeding.
  * Idempotent: running the generator twice yields the same files.
  * Hero-preserving: the hero patient (PT-1042) and the pinned rows of the four original patients and
    four original trials are written from literal, verified values. The generator asserts the hero's
    CrCl trajectory (55 on 2026-05-20, then 48 on 2026-06-18) and the hero trial's renal threshold
    (CrCl >= 50) before writing, so the borderline eligibility story can never drift.

Synthetic only. No PHI. Not clinical decision support. Clinical phrasing uses "renal function" and
"CrCl", never "renal failure".

Usage:
    python data/fabric/generate.py
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

SEED = "amc-iq-fabric-2026"

FABRIC_DIR = Path(__file__).resolve().parent
DATA_DIR = FABRIC_DIR.parent
REGISTRY_PATH = DATA_DIR / "registry" / "registry.yaml"
ONTOLOGY_PATH = DATA_DIR / "ontology" / "ontology.yaml"

# ---------------------------------------------------------------------------
# Pinned rows: exact current values for the four original patients and trials.
# These MUST NOT change so the hero eligibility thread stays byte-stable.
# ---------------------------------------------------------------------------
PINNED_DIAGNOSIS = {
    "PT-1042": "Metastatic NSCLC adenocarcinoma",
    "PT-1043": "Metastatic NSCLC adenocarcinoma",
    "PT-1044": "Extensive-stage SCLC",
    "PT-1045": "Metastatic NSCLC adenocarcinoma",
}

# Full pinned lab sets for the original patients (order preserved).
PINNED_LABS: dict[str, list[tuple[str, str, str, str, str, str]]] = {
    # (lab_date, lab_type, value, unit, ref_low, ref_high)
    "PT-1042": [
        ("2026-05-20", "CrCl_CKD-EPI", "55", "mL/min", "60", "120"),
        ("2026-05-20", "Creatinine", "1.08", "mg/dL", "0.5", "1.1"),
        ("2026-06-18", "CrCl_CKD-EPI", "48", "mL/min", "60", "120"),
        ("2026-06-18", "WBC", "5.7", "10^3/uL", "4", "11"),
        ("2026-06-18", "ANC", "3.2", "10^3/uL", "1.5", "8"),
        ("2026-06-18", "Hgb", "10.8", "g/dL", "12", "16"),
        ("2026-06-18", "PLT", "211", "10^3/uL", "150", "400"),
        ("2026-06-18", "ALT", "29", "U/L", "7", "56"),
        ("2026-06-18", "AST", "31", "U/L", "10", "40"),
        ("2026-06-18", "Creatinine", "1.23", "mg/dL", "0.5", "1.1"),
    ],
    "PT-1043": [
        ("2026-06-20", "CrCl_CKD-EPI", "72", "mL/min", "60", "120"),
        ("2026-06-20", "WBC", "6.8", "10^3/uL", "4", "11"),
        ("2026-06-20", "ANC", "4.1", "10^3/uL", "1.5", "8"),
        ("2026-06-20", "Hgb", "11.4", "g/dL", "13.5", "17.5"),
        ("2026-06-20", "PLT", "245", "10^3/uL", "150", "400"),
        ("2026-06-20", "ALT", "34", "U/L", "7", "56"),
        ("2026-06-20", "AST", "28", "U/L", "10", "40"),
        ("2026-06-20", "Creatinine", "1.01", "mg/dL", "0.7", "1.3"),
    ],
    "PT-1044": [
        ("2026-06-22", "CrCl_CKD-EPI", "88", "mL/min", "60", "120"),
        ("2026-06-22", "WBC", "4.9", "10^3/uL", "4", "11"),
        ("2026-06-22", "ANC", "2.6", "10^3/uL", "1.5", "8"),
        ("2026-06-22", "Hgb", "9.9", "g/dL", "12", "16"),
        ("2026-06-22", "PLT", "178", "10^3/uL", "150", "400"),
        ("2026-06-22", "ALT", "22", "U/L", "7", "56"),
        ("2026-06-22", "AST", "25", "U/L", "10", "40"),
        ("2026-06-22", "Creatinine", "0.76", "mg/dL", "0.5", "1.1"),
    ],
    "PT-1045": [
        ("2026-06-25", "CrCl_CKD-EPI", "95", "mL/min", "60", "120"),
        ("2026-06-25", "WBC", "7.2", "10^3/uL", "4", "11"),
        ("2026-06-25", "ANC", "4.8", "10^3/uL", "1.5", "8"),
        ("2026-06-25", "Hgb", "13.7", "g/dL", "13.5", "17.5"),
        ("2026-06-25", "PLT", "289", "10^3/uL", "150", "400"),
        ("2026-06-25", "ALT", "19", "U/L", "7", "56"),
        ("2026-06-25", "AST", "21", "U/L", "10", "40"),
        ("2026-06-25", "Creatinine", "0.88", "mg/dL", "0.7", "1.3"),
    ],
}

# Prior CrCl readings appended to the original distractors so every patient has a >= 2 point
# CrCl_CKD-EPI trajectory. These are additive rows (earlier dates) and never alter existing values.
PINNED_PRIOR_CRCL: dict[str, tuple[str, str]] = {
    # patient_id: (prior_date, prior_value)
    "PT-1043": ("2026-05-22", "68"),
    "PT-1044": ("2026-05-25", "84"),
    "PT-1045": ("2026-05-28", "92"),
}

PINNED_TREATMENTS: dict[str, list[tuple[str, ...]]] = {
    # (drug_name, drug_class, line, start_date, end_date, cycles_completed, best_response, reason_stopped)
    "PT-1042": [
        ("Carboplatin + Pemetrexed", "Platinum doublet", "1", "2026-04-22", "2026-06-12", "3",
         "Partial response", "Completed planned induction; evaluating trial maintenance option"),
    ],
    "PT-1043": [
        ("Pembrolizumab", "PD-1 inhibitor", "1", "2026-03-24", "", "4", "Stable disease", "Ongoing therapy"),
    ],
    "PT-1044": [
        ("Carboplatin + Etoposide", "Platinum doublet", "1", "2026-05-08", "2026-06-19", "2",
         "Partial response", "Transitioning to maintenance evaluation"),
    ],
    "PT-1045": [
        ("None", "Treatment-naive", "0", "", "", "0", "Not applicable", "New diagnosis; treatment not yet started"),
    ],
}

PINNED_ENROLLMENT: dict[str, tuple[str, ...]] = {
    # patient_id: (trial_id, status, screening_date, coordinator_id, notes)
    "PT-1042": ("NCT99004324", "Pre-screening", "2026-07-06", "COORD-01",
                "EGFR exon 20 insertion documented; renal threshold pending repeat CrCl because latest "
                "CKD-EPI CrCl is 48 mL/min against 50 mL/min minimum."),
    "PT-1043": ("NCT99004325", "Screening", "2026-07-02", "COORD-01",
                "KRAS G12C documented; ECOG 2 allowed; baseline labs adequate."),
    "PT-1044": ("NCT99004401", "Not enrolled", "2026-06-28", "COORD-01",
                "Trial active not recruiting; patient tracked for future SCLC maintenance options."),
    "PT-1045": ("NCT99004326", "Pre-screening", "2026-07-05", "COORD-01",
                "ALK fusion documented; treatment-naive status requires PI review against protocol "
                "line-of-therapy requirements."),
}

PINNED_TRIALS: dict[str, dict[str, str]] = {
    # Preserved trials.csv values (short_title / condition / status / biomarker_required) for the
    # four original trials. cancer_type / phase / latest_protocol are added from the registry.
    "NCT99004324": {"short_title": "EGFR exon 20 NSCLC",
                    "condition": "Metastatic NSCLC with EGFR exon 20 insertion", "status": "Recruiting",
                    "ecog_max": "1", "crcl_min": "50", "biomarker_required": "EGFR exon 20 insertion"},
    "NCT99004325": {"short_title": "KRAS G12C NSCLC", "condition": "Metastatic NSCLC with KRAS G12C",
                    "status": "Recruiting", "ecog_max": "2", "crcl_min": "45", "biomarker_required": "KRAS G12C"},
    "NCT99004326": {"short_title": "ALK+ NSCLC", "condition": "Metastatic NSCLC with ALK fusion",
                    "status": "Recruiting", "ecog_max": "2", "crcl_min": "45", "biomarker_required": "ALK fusion"},
    "NCT99004401": {"short_title": "ES-SCLC maintenance", "condition": "Extensive-stage SCLC",
                    "status": "Active not recruiting", "ecog_max": "1", "crcl_min": "40", "biomarker_required": ""},
}

PINNED_WORKLOAD: dict[str, tuple[str, str, str, str]] = {
    # coordinator_id: (display_name, open_screenings, pending_tasks, capacity_this_week)
    "COORD-01": ("Dana Whitfield", "7", "12", "10"),
}

PINNED_SLOTS: list[tuple[str, ...]] = [
    # (slot_id, site_id, slot_type, date, time, available, assigned_patient_id)
    ("SLOT-20260708-0900-SCR", "SITE-01", "Screening", "2026-07-08", "09:00", "true", ""),
    ("SLOT-20260708-1030-SCR", "SITE-01", "Screening", "2026-07-08", "10:30", "false", "PT-1043"),
    ("SLOT-20260709-1300-SCR", "SITE-01", "Screening", "2026-07-09", "13:00", "true", ""),
    ("SLOT-20260709-1430-LAB", "SITE-01", "Lab repeat", "2026-07-09", "14:30", "true", ""),
    ("SLOT-20260710-1000-PI", "SITE-01", "PI consult", "2026-07-10", "10:00", "false", "PT-1042"),
    ("SLOT-20260713-0900-SCR", "SITE-01", "Screening", "2026-07-13", "09:00", "true", ""),
]

ORIGINAL_PATIENTS = frozenset(PINNED_LABS)
ORIGINAL_TRIALS = frozenset(PINNED_TRIALS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _rng(*parts: Any) -> random.Random:
    """A deterministic RNG seeded from the master seed and the given key parts."""
    return random.Random(":".join([SEED, *[str(p) for p in parts]]))


def _pnum(patient_id: str) -> int:
    return int(patient_id.split("-")[1])


def _iso(d: date) -> str:
    return d.isoformat()


def _parse(d: str) -> date:
    return date.fromisoformat(d)


def entity_columns(ontology: dict[str, Any]) -> dict[str, list[str]]:
    """Map each entity table name to its ordered column list (key columns + attributes, deduped)."""
    columns: dict[str, list[str]] = {}
    for entity in ontology["entities"].values():
        table = entity["table"]
        key = entity["key"]
        key_cols = key if isinstance(key, list) else [key]
        cols = list(key_cols)
        for attr in entity.get("attributes", []):
            if attr not in cols:
                cols.append(attr)
        columns[table] = cols
    return columns


def write_table(
    table: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    output_dir: Path = FABRIC_DIR,
) -> None:
    path = output_dir / table
    output_dir.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([str(row.get(col, "")) for col in columns])


# ---------------------------------------------------------------------------
# Biomarker interpretation
# ---------------------------------------------------------------------------
GENE_RULES: list[tuple[str, str, str, str, str]] = [
    # (keyword, gene, category, method, negated_keywords are handled separately)
    ("egfr", "EGFR", "mutation", "NGS (tumor)", ""),
    ("alk", "ALK", "fusion", "NGS (tumor)", ""),
    ("ros1", "ROS1", "fusion", "NGS (tumor)", ""),
    ("ret", "RET", "fusion", "NGS (tumor)", ""),
    ("met", "MET", "mutation", "NGS (tumor)", ""),
    ("kras", "KRAS", "mutation", "NGS (tumor)", ""),
    ("braf", "BRAF", "mutation", "NGS (tumor)", ""),
    ("nras", "NRAS", "mutation", "NGS (tumor)", ""),
    ("her2", "ERBB2", "amplification", "FISH", ""),
    ("brca1", "BRCA1", "mutation", "NGS (germline)", ""),
    ("brca2", "BRCA2", "mutation", "NGS (germline)", ""),
    ("pd-l1", "CD274", "expression", "IHC 22C3", ""),
    ("msi-h", "MMR", "msi_status", "IHC/PCR", ""),
    ("mss", "MMR", "msi_status", "IHC/PCR", ""),
    ("er positive", "ESR1", "expression", "IHC", ""),
    ("er negative", "ESR1", "expression", "IHC", ""),
]

NEGATIVE_TOKENS = ("negative", "wild-type", "wild type", "low", "mss")


def interpret_marker(marker: str) -> tuple[str, str, str, str]:
    """Return (gene, category, status, method) for a registry biomarker string."""
    low = marker.lower()
    gene, category, method = "Unknown", "other", "NGS (tumor)"
    for keyword, g, cat, meth, _ in GENE_RULES:
        if keyword in low:
            gene, category, method = g, cat, meth
            break
    negative = any(tok in low for tok in NEGATIVE_TOKENS)
    if category == "msi_status":
        status = "MSI-High" if "msi-h" in low else "MSS"
    elif category == "expression" and gene == "CD274":
        status = "Low" if "low" in low else "High"
    elif category == "expression" and gene == "ESR1":
        status = "Negative" if negative else "Positive"
    elif category == "amplification":
        status = "Not amplified" if negative else "Amplified"
    else:
        status = "Not detected" if negative else "Detected"
    return gene, category, status, method


POSITIVE_STATUSES = frozenset({"Detected", "Amplified", "Positive", "High", "MSI-High"})


# ---------------------------------------------------------------------------
# Table builders
# ---------------------------------------------------------------------------
def build_sites(registry: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"site_id": s["id"], "display_name": s["display"], "short_name": s["short"]}
        for s in registry["sites"]
    ]


def build_people(registry: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"person_id": p["id"], "display_name": p["display"], "role": p["role"], "site_id": p["site"]}
        for p in registry["people"]
    ]


def build_patient_registry(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for p in registry["patients"]:
        pid = p["id"]
        primary = PINNED_DIAGNOSIS.get(pid, p["diagnosis"])
        rows.append({
            "patient_id": pid,
            "mrn": p["mrn"],
            "display_name": p["display"],
            "sex": p["sex"],
            "age": p["age"],
            "ecog_ps": p["ecog"],
            "primary_diagnosis": primary,
            "diagnosis_icd10": p["diagnosis_icd10"],
            "cancer_type": p["cancer_type"],
            "stage": p["stage"],
            "staging_date": p["staging_date"],
            "treating_oncologist_id": p["treating_oncologist"],
            "coordinator_id": p["coordinator"],
            "archetype": p["archetype"],
        })
    return rows


def build_biomarkers(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for p in registry["patients"]:
        pid = p["id"]
        assessed = p["staging_date"]
        for marker in p.get("biomarkers", []) or []:
            gene, category, status, method = interpret_marker(marker)
            rows.append({
                "patient_id": pid,
                "marker": marker,
                "gene": gene,
                "category": category,
                "status": status,
                "method": method,
                "assessed_date": assessed,
            })
    return rows


def _synth_labs(p: dict[str, Any]) -> list[tuple[str, str, str, str, str, str]]:
    """Deterministic non-CrCl labs on the latest CrCl date for a generated patient."""
    pid = p["id"]
    rng = _rng("labs", pid)
    male = p["sex"] == "M"
    latest_date = p["key_labs"]["crcl_date"]
    had_chemo = bool(p.get("prior_therapy"))
    hgb_low, hgb_high = ("13.5", "17.5") if male else ("12", "16")
    crea_low, crea_high = ("0.7", "1.3") if male else ("0.5", "1.1")
    # Anemia bias when the patient received cytotoxic therapy.
    hgb = round(rng.uniform(9.5, 11.5) if had_chemo else rng.uniform(12.5, 15.5), 1)
    labs = [
        ("WBC", round(rng.uniform(4.2, 9.5), 1), "10^3/uL", "4", "11"),
        ("ANC", round(rng.uniform(1.8, 6.5), 1), "10^3/uL", "1.5", "8"),
        ("Hgb", hgb, "g/dL", hgb_low, hgb_high),
        ("PLT", int(rng.uniform(160, 360)), "10^3/uL", "150", "400"),
        ("ALT", int(rng.uniform(12, 48)), "U/L", "7", "56"),
        ("AST", int(rng.uniform(14, 38)), "U/L", "10", "40"),
        ("Creatinine", round(rng.uniform(0.8, 1.4), 2), "mg/dL", crea_low, crea_high),
    ]
    return [(latest_date, name, str(value), unit, lo, hi) for name, value, unit, lo, hi in labs]


def build_labs(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def emit(pid: str, lab_date: str, lab_type: str, value: str, unit: str, lo: str, hi: str) -> None:
        rows.append({"patient_id": pid, "lab_date": lab_date, "lab_type": lab_type, "value": value,
                     "unit": unit, "ref_low": lo, "ref_high": hi})

    for p in registry["patients"]:
        pid = p["id"]
        if pid in PINNED_LABS:
            for lab_date, lab_type, value, unit, lo, hi in PINNED_LABS[pid]:
                emit(pid, lab_date, lab_type, value, unit, lo, hi)
            if pid in PINNED_PRIOR_CRCL:
                prior_date, prior_value = PINNED_PRIOR_CRCL[pid]
                emit(pid, prior_date, "CrCl_CKD-EPI", prior_value, "mL/min", "60", "120")
            continue
        # Generated patient: prior + latest CrCl trajectory, then the metabolic panel.
        kl = p["key_labs"]
        emit(pid, kl["crcl_prior_date"], "CrCl_CKD-EPI", str(kl["crcl_prior"]), "mL/min", "60", "120")
        emit(pid, kl["crcl_date"], "CrCl_CKD-EPI", str(kl["crcl_ckd_epi"]), "mL/min", "60", "120")
        for lab_date, lab_type, value, unit, lo, hi in _synth_labs(p):
            emit(pid, lab_date, lab_type, value, unit, lo, hi)
    # Stable ordering: by patient, then date, keeping CrCl-first within a date via insertion order.
    return rows


def _reason_stopped(therapy: dict[str, Any]) -> str:
    if not therapy.get("end_date"):
        return "Ongoing therapy"
    response = therapy.get("best_response", "")
    if response == "Progressive disease":
        return "Discontinued for disease progression"
    return "Completed planned course; evaluating next-line options"


def build_treatment_history(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for p in registry["patients"]:
        pid = p["id"]
        if pid in PINNED_TREATMENTS:
            for (drug, cls, line, start, end, cycles, response, reason) in PINNED_TREATMENTS[pid]:
                rows.append({"patient_id": pid, "drug_name": drug, "drug_class": cls, "line": line,
                             "start_date": start, "end_date": end, "cycles_completed": cycles,
                             "best_response": response, "reason_stopped": reason})
            continue
        therapies = p.get("prior_therapy") or []
        if not therapies:
            rows.append({"patient_id": pid, "drug_name": "None", "drug_class": "Treatment-naive",
                         "line": "0", "start_date": "", "end_date": "", "cycles_completed": "0",
                         "best_response": "Not applicable",
                         "reason_stopped": "New diagnosis; treatment not yet started"})
            continue
        rng = _rng("cycles", pid)
        for therapy in therapies:
            cycles = "0" if not therapy.get("start_date") else str(rng.randint(2, 6))
            rows.append({
                "patient_id": pid,
                "drug_name": therapy["drug"],
                "drug_class": therapy["class"],
                "line": str(therapy["line"]),
                "start_date": therapy.get("start_date", ""),
                "end_date": therapy.get("end_date", ""),
                "cycles_completed": cycles,
                "best_response": therapy.get("best_response", ""),
                "reason_stopped": _reason_stopped(therapy),
            })
    return rows


COMORBIDITY_POOL = [
    ("Essential hypertension", "I10"),
    ("Type 2 diabetes mellitus", "E11.9"),
    ("Chronic obstructive pulmonary disease", "J44.9"),
    ("Hypothyroidism", "E03.9"),
    ("Gastroesophageal reflux disease", "K21.9"),
    ("Osteoarthritis", "M19.90"),
]


def build_comorbidities(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for p in registry["patients"]:
        pid = p["id"]
        rng = _rng("comorbid", pid)
        noted = _iso(_parse(p["staging_date"]) - timedelta(days=rng.randint(30, 400)))
        seen: set[str] = set()
        # Reduced renal function comorbidity when latest CrCl is below the normal reference floor.
        if int(p["key_labs"]["crcl_ckd_epi"]) < 60:
            rows.append({"patient_id": pid, "condition": "Chronic kidney disease, stage 3 (reduced renal function)",
                         "icd10": "N18.30", "status": "Active", "noted_date": noted})
            seen.add("N18.30")
        count = rng.randint(0, 2)
        for condition, icd10 in rng.sample(COMORBIDITY_POOL, k=count):
            if icd10 in seen:
                continue
            seen.add(icd10)
            rows.append({"patient_id": pid, "condition": condition, "icd10": icd10, "status": "Active",
                         "noted_date": _iso(_parse(p["staging_date"]) - timedelta(days=rng.randint(30, 800)))})
    return rows


AE_RULES: dict[str, list[tuple[str, str]]] = {
    "Platinum doublet": [("Anemia", "2"), ("Fatigue", "1"), ("Nausea", "1")],
    "Multi-agent chemotherapy": [("Anemia", "2"), ("Peripheral neuropathy", "1"), ("Diarrhea", "1")],
    "Fluoropyrimidine + platinum": [("Neutropenia", "2"), ("Peripheral neuropathy", "1")],
    "Fluoropyrimidine + irinotecan": [("Diarrhea", "2"), ("Neutropenia", "1")],
    "Anthracycline regimen": [("Neutropenia", "2"), ("Fatigue", "1")],
    "PD-1 inhibitor": [("Hypothyroidism", "1"), ("Immune-related rash", "1")],
    "Checkpoint inhibitor combination": [("Colitis (immune-related)", "2"), ("Hypothyroidism", "1")],
    "Anti-HER2 therapy": [("Left ventricular dysfunction", "1"), ("Fatigue", "1")],
}


def build_adverse_events(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for p in registry["patients"]:
        pid = p["id"]
        therapies = PINNED_TREATMENTS.get(pid)
        source = None
        if therapies:
            drug, cls, _line, start = therapies[0][0], therapies[0][1], therapies[0][2], therapies[0][3]
            source = (drug, cls, start)
        else:
            first = (p.get("prior_therapy") or [None])[0]
            if first:
                source = (first["drug"], first["class"], first.get("start_date", ""))
        if not source or not source[2]:
            continue
        drug, cls, start = source
        catalog = AE_RULES.get(cls, [("Fatigue", "1")])
        rng = _rng("ae", pid)
        picks = catalog[: rng.randint(1, len(catalog))]
        for n, (term, grade) in enumerate(picks, start=1):
            onset = _iso(_parse(start) + timedelta(days=rng.randint(10, 35)))
            if grade == "1":
                resolved = _iso(_parse(onset) + timedelta(days=rng.randint(7, 28)))
                outcome = "Resolved"
            else:
                resolved = ""
                outcome = "Ongoing (managed)"
            rows.append({"patient_id": pid, "ae_id": f"AE-{_pnum(pid)}-{n}", "term": term,
                         "ctcae_grade": grade, "related_drug": drug, "onset_date": onset,
                         "resolved_date": resolved, "outcome": outcome})
    return rows


RESPONSE_FACTOR = {
    "Complete response": 0.0,
    "Partial response": 0.6,
    "Stable disease": 0.9,
    "Progressive disease": 1.25,
}


def build_recist(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    method = "CT chest/abdomen/pelvis (RECIST 1.1)"
    for p in registry["patients"]:
        pid = p["id"]
        rng = _rng("recist", pid)
        baseline_sum = int(rng.uniform(52, 92))
        baseline_date = _iso(_parse(p["staging_date"]) + timedelta(days=rng.randint(7, 21)))
        rows.append({"patient_id": pid, "assessment_date": baseline_date, "timepoint": "Baseline",
                     "overall_response": "Baseline", "target_lesion_sum_mm": str(baseline_sum),
                     "method": method})
        therapies = PINNED_TREATMENTS.get(pid) or p.get("prior_therapy") or []
        if not therapies:
            continue
        # Use the best response of the first line to shape the on-treatment follow-up.
        if pid in PINNED_TREATMENTS:
            response = PINNED_TREATMENTS[pid][0][6]
        else:
            response = therapies[0].get("best_response", "Stable disease")
        factor = RESPONSE_FACTOR.get(response, 0.9)
        follow_sum = int(round(baseline_sum * factor))
        follow_date = _iso(_parse(baseline_date) + timedelta(days=rng.randint(56, 84)))
        overall = response if response in RESPONSE_FACTOR else "Stable disease"
        rows.append({"patient_id": pid, "assessment_date": follow_date, "timepoint": "Follow-up",
                     "overall_response": overall, "target_lesion_sum_mm": str(follow_sum),
                     "method": method})
    return rows


def build_trials(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for t in registry["trials"]:
        tid = t["id"]
        pinned = PINNED_TRIALS.get(tid, {})
        rows.append({
            "trial_id": tid,
            "short_title": pinned.get("short_title", t["short"]),
            "condition": pinned.get("condition", t["condition"]),
            "cancer_type": t["cancer_type"],
            "phase": t["phase"],
            "status": pinned.get("status", t["status"]),
            "site_id": t["site"],
            "ecog_max": pinned.get("ecog_max", str(t["ecog_max"])),
            "crcl_min": pinned.get("crcl_min", str(t["crcl_min"])),
            "biomarker_required": pinned.get("biomarker_required", t.get("biomarker_required", "")),
            "latest_protocol": t["latest_protocol"],
        })
    return rows


def build_trial_criteria(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for t in registry["trials"]:
        tid = t["id"]
        excl = t.get("prior_therapy_exclusion") or {}
        criteria = [
            ("DX", "inclusion", "diagnosis", f"Histologically confirmed {t['condition']}.",
             "Patient", "cancer_type", "matches", t["cancer_type"]),
        ]
        if t.get("biomarker_required"):
            criteria.append(("BIO", "inclusion", "biomarker", f"Documented {t['biomarker_required']}.",
                             "Biomarker", "marker", "present", t["biomarker_required"]))
        criteria.append(("PS", "inclusion", "performance",
                         f"ECOG performance status 0 to {t['ecog_max']}.",
                         "Patient", "ecog_ps", "<=", str(t["ecog_max"])))
        criteria.append(("REN", "inclusion", "renal",
                         f"Creatinine clearance >= {t['crcl_min']} mL/min (CKD-EPI).",
                         "Lab", "CrCl_CKD-EPI", ">=", str(t["crcl_min"])))
        if excl:
            criteria.append(("RX", "exclusion", "prior_therapy", excl["description"] + ".",
                             "Treatment", "drug_class", "excludes", excl["drug_class"]))
        for suffix, kind, category, description, ref, param, comparator, value in criteria:
            rows.append({"trial_id": tid, "criterion_id": f"{tid}-{suffix}", "kind": kind,
                         "category": category, "description": description, "references_entity": ref,
                         "param": param, "comparator": comparator, "value": value})
    return rows


def build_amendments(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for t in registry["trials"]:
        tid = t["id"]
        for amd in t.get("amendments", []) or []:
            rows.append({
                "trial_id": tid,
                "amendment_id": amd["id"],
                "label": amd["label"],
                "effective_date": amd["effective_date"],
                "modifies_criterion_id": f"{tid}-{amd['modifies']}",
                "summary": amd["summary"],
            })
    return rows


def _cancer_type_compatible(patient_ct: str, trial_ct: str) -> bool:
    if trial_ct == "Solid tumor":
        return True
    if patient_ct == trial_ct:
        return True
    if patient_ct.startswith("NSCLC") and trial_ct.startswith("NSCLC"):
        return True
    if patient_ct.startswith("Breast") and trial_ct.startswith("Breast"):
        return True
    return False


def _marker_matches(required: str, markers: list[str]) -> bool:
    core = required.split("(")[0].strip().lower()
    for marker in markers:
        low = marker.lower()
        if core and (core in low):
            _g, _c, status, _m = interpret_marker(marker)
            if status in POSITIVE_STATUSES:
                return True
    return False


def match_trial(patient: dict[str, Any], trials: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, bool]:
    """Return (best_trial, fully_matched). Full match = compatible cancer type and required biomarker."""
    markers = patient.get("biomarkers", []) or []
    ct = patient["cancer_type"]
    ct_only: dict[str, Any] | None = None
    for t in trials:
        ct_ok = _cancer_type_compatible(ct, t["cancer_type"])
        bio_req = t.get("biomarker_required") or ""
        bio_ok = (not bio_req) or _marker_matches(bio_req, markers)
        if ct_ok and bio_ok:
            return t, True
        if ct_ok and ct_only is None:
            ct_only = t
    if ct_only is not None:
        return ct_only, False
    pan = next((t for t in trials if t["cancer_type"] == "Solid tumor"), None)
    return pan, False


def build_enrollment(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    trials = registry["trials"]
    base_date = _parse("2026-07-01")
    for idx, p in enumerate(registry["patients"]):
        pid = p["id"]
        if pid in PINNED_ENROLLMENT:
            trial_id, status, screening_date, coordinator_id, notes = PINNED_ENROLLMENT[pid]
            rows.append({"patient_id": pid, "trial_id": trial_id, "status": status,
                         "screening_date": screening_date, "coordinator_id": coordinator_id,
                         "notes": notes})
            continue
        trial, matched = match_trial(p, trials)
        if trial is None:
            continue
        trial_recruiting = "not recruiting" not in trial["status"].lower()
        archetype = p["archetype"]
        if not matched or not trial_recruiting or archetype == "hard_excluded":
            status = "Not enrolled"
        elif archetype in ("borderline", "treatment_naive_hold"):
            status = "Pre-screening"
        else:
            status = "Screening"
        screening_date = _iso(base_date + timedelta(days=(idx % 12)))
        notes = _enrollment_note(p, trial, matched, status)
        rows.append({"patient_id": pid, "trial_id": trial["id"], "status": status,
                     "screening_date": screening_date, "coordinator_id": p["coordinator"], "notes": notes})
    return rows


def _enrollment_note(patient: dict[str, Any], trial: dict[str, Any], matched: bool, status: str) -> str:
    markers = ", ".join(patient.get("biomarkers", []) or []) or "no actionable biomarker"
    if status == "Not enrolled" and not matched:
        return f"Biomarker/cancer-type profile ({markers}) does not match {trial['id']} requirements; tracked for future options."
    if status == "Not enrolled":
        return f"{trial['id']} closed to new enrollment or performance status outside protocol; tracked for future options."
    if patient["archetype"] == "treatment_naive_hold":
        return f"{markers} documented; treatment-naive status requires PI review against protocol line-of-therapy requirements."
    if int(patient["key_labs"]["crcl_ckd_epi"]) < int(str(trial["crcl_min"])):
        return f"{markers} documented; renal function pending repeat CrCl before {trial['id']} screening can advance."
    return f"{markers} documented; baseline criteria adequate for {trial['id']} screening."


def build_consent(registry: dict[str, Any], enrollment: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    status_map = {"Screening": "Signed", "Pre-screening": "In progress", "Not enrolled": "Not started"}
    for e in enrollment:
        consent_status = status_map.get(e["status"], "Not started")
        consent_date = e["screening_date"] if consent_status == "Signed" else ""
        rows.append({"patient_id": e["patient_id"], "trial_id": e["trial_id"],
                     "consent_status": consent_status, "consent_version": "ICF v2.0",
                     "consent_date": consent_date})
    return rows


def build_coordinator_workload(registry: dict[str, Any], enrollment: list[dict[str, Any]]) -> list[dict[str, Any]]:
    people = {p["id"]: p for p in registry["people"]}
    coordinators = [p["id"] for p in registry["people"] if "coordinator" in p["role"].lower()]
    open_counts: dict[str, int] = {c: 0 for c in coordinators}
    for e in enrollment:
        if e["status"] in ("Pre-screening", "Screening"):
            open_counts[e["coordinator_id"]] = open_counts.get(e["coordinator_id"], 0) + 1
    rows = []
    for cid in coordinators:
        if cid in PINNED_WORKLOAD:
            display, open_s, pending, capacity = PINNED_WORKLOAD[cid]
            rows.append({"coordinator_id": cid, "display_name": display, "open_screenings": open_s,
                         "pending_tasks": pending, "capacity_this_week": capacity})
            continue
        rng = _rng("workload", cid)
        open_s = open_counts.get(cid, 0)
        rows.append({"coordinator_id": cid, "display_name": people[cid]["display"],
                     "open_screenings": str(open_s), "pending_tasks": str(open_s + rng.randint(2, 6)),
                     "capacity_this_week": str(rng.randint(8, 12))})
    return rows


def build_scheduling_slots(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [dict(zip(
        ["slot_id", "site_id", "slot_type", "date", "time", "available", "assigned_patient_id"], slot))
        for slot in PINNED_SLOTS]
    # Additional open slots for the two non-hero sites (never SITE-01, to keep hero slot selection stable).
    extra_sites = [s["id"] for s in registry["sites"] if s["id"] != "SITE-01"]
    slot_types = ["Screening", "Lab repeat", "PI consult"]
    for site in extra_sites:
        rng = _rng("slots", site)
        for day in range(3):
            slot_date = _parse("2026-07-08") + timedelta(days=day * 2)
            slot_type = slot_types[day % len(slot_types)]
            hour = 9 + rng.randint(0, 6)
            time = f"{hour:02d}:00"
            code = {"Screening": "SCR", "Lab repeat": "LAB", "PI consult": "PI"}[slot_type]
            slot_id = f"SLOT-{slot_date.strftime('%Y%m%d')}-{time.replace(':', '')}-{site[-2:]}{code}"
            rows.append({"slot_id": slot_id, "site_id": site, "slot_type": slot_type,
                         "date": _iso(slot_date), "time": time, "available": "true",
                         "assigned_patient_id": ""})
    return rows


# ---------------------------------------------------------------------------
# Verification of pinned hero facts
# ---------------------------------------------------------------------------
def verify_hero(tables: dict[str, list[dict[str, Any]]]) -> None:
    labs = [r for r in tables["labs.csv"] if r["patient_id"] == "PT-1042" and r["lab_type"] == "CrCl_CKD-EPI"]
    series = sorted(((r["lab_date"], r["value"]) for r in labs), key=lambda x: x[0])
    assert series == [("2026-05-20", "55"), ("2026-06-18", "48")], f"Hero CrCl series drifted: {series}"

    trial = next(r for r in tables["trials.csv"] if r["trial_id"] == "NCT99004324")
    assert trial["crcl_min"] == "50", f"Hero trial crcl_min drifted: {trial['crcl_min']}"
    assert trial["biomarker_required"] == "EGFR exon 20 insertion"
    assert trial["short_title"] == "EGFR exon 20 NSCLC"

    ren = next(r for r in tables["trial_criteria.csv"]
               if r["trial_id"] == "NCT99004324" and r["criterion_id"] == "NCT99004324-REN")
    assert ren["comparator"] == ">=" and ren["value"] == "50", "Hero renal criterion drifted"

    amd = next(r for r in tables["amendments.csv"]
               if r["trial_id"] == "NCT99004324" and r["amendment_id"] == "AMD-2")
    assert amd["modifies_criterion_id"] == "NCT99004324-RX", "Hero Amendment 2 target drifted"

    enroll = next(r for r in tables["trial_enrollment.csv"] if r["patient_id"] == "PT-1042")
    assert enroll["trial_id"] == "NCT99004324" and enroll["status"] == "Pre-screening"


def check_referential_integrity(tables: dict[str, list[dict[str, Any]]]) -> None:
    patient_ids = {r["patient_id"] for r in tables["patient_registry.csv"]}
    trial_ids = {r["trial_id"] for r in tables["trials.csv"]}
    site_ids = {r["site_id"] for r in tables["sites.csv"]}
    person_ids = {r["person_id"] for r in tables["people.csv"]}
    coordinator_ids = {r["coordinator_id"] for r in tables["coordinator_workload.csv"]}
    criterion_ids = {r["criterion_id"] for r in tables["trial_criteria.csv"]}

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise AssertionError(message)

    for table in ("labs.csv", "biomarkers.csv", "treatment_history.csv", "comorbidities.csv",
                  "adverse_events.csv", "recist_assessments.csv", "consent.csv", "trial_enrollment.csv"):
        for r in tables[table]:
            require(r["patient_id"] in patient_ids, f"{table}: dangling patient_id {r['patient_id']}")
    for r in tables["patient_registry.csv"]:
        require(r["treating_oncologist_id"] in person_ids, f"patient_registry: bad oncologist {r['treating_oncologist_id']}")
        require(r["coordinator_id"] in coordinator_ids, f"patient_registry: bad coordinator {r['coordinator_id']}")
    for r in tables["people.csv"]:
        require(r["site_id"] in site_ids, f"people: bad site {r['site_id']}")
    for r in tables["trials.csv"]:
        require(r["site_id"] in site_ids, f"trials: bad site {r['site_id']}")
    for r in tables["trial_criteria.csv"] + tables["amendments.csv"]:
        require(r["trial_id"] in trial_ids, f"criteria/amendments: dangling trial {r['trial_id']}")
    for r in tables["amendments.csv"]:
        require(r["modifies_criterion_id"] in criterion_ids,
                f"amendments: dangling criterion {r['modifies_criterion_id']}")
    for r in tables["trial_enrollment.csv"] + tables["consent.csv"]:
        require(r["trial_id"] in trial_ids, f"enrollment/consent: dangling trial {r['trial_id']}")
    for r in tables["scheduling_slots.csv"]:
        require(r["site_id"] in site_ids, f"slots: bad site {r['site_id']}")
        if r["assigned_patient_id"]:
            require(r["assigned_patient_id"] in patient_ids, f"slots: bad patient {r['assigned_patient_id']}")
    # Every trial must have at least one criterion.
    trials_with_criteria = {r["trial_id"] for r in tables["trial_criteria.csv"]}
    for tid in trial_ids:
        require(tid in trials_with_criteria, f"trial {tid} has no criteria")


def generate(output_dir: Path = FABRIC_DIR) -> dict[str, list[dict[str, Any]]]:
    with REGISTRY_PATH.open(encoding="utf-8") as handle:
        registry = yaml.safe_load(handle)
    with ONTOLOGY_PATH.open(encoding="utf-8") as handle:
        ontology = yaml.safe_load(handle)

    enrollment = build_enrollment(registry)
    tables: dict[str, list[dict[str, Any]]] = {
        "sites.csv": build_sites(registry),
        "people.csv": build_people(registry),
        "patient_registry.csv": build_patient_registry(registry),
        "biomarkers.csv": build_biomarkers(registry),
        "labs.csv": build_labs(registry),
        "treatment_history.csv": build_treatment_history(registry),
        "comorbidities.csv": build_comorbidities(registry),
        "adverse_events.csv": build_adverse_events(registry),
        "recist_assessments.csv": build_recist(registry),
        "consent.csv": build_consent(registry, enrollment),
        "trials.csv": build_trials(registry),
        "trial_criteria.csv": build_trial_criteria(registry),
        "amendments.csv": build_amendments(registry),
        "trial_enrollment.csv": enrollment,
        "coordinator_workload.csv": build_coordinator_workload(registry, enrollment),
        "scheduling_slots.csv": build_scheduling_slots(registry),
    }

    verify_hero(tables)
    check_referential_integrity(tables)

    columns = entity_columns(ontology)
    for table, rows in tables.items():
        write_table(table, columns[table], rows, output_dir)
    return tables


def main() -> None:
    tables = generate()
    print(f"Generated {len(tables)} Fabric tables under {FABRIC_DIR}:")
    for table, rows in tables.items():
        print(f"  {table:<28} {len(rows):>4} rows")
    print("Hero pinned facts verified; referential integrity OK.")


if __name__ == "__main__":
    main()
