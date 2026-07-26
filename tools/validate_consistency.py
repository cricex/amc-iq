"""Consistency validator for the synthetic proof-of-concept data package.

Scans the generated content directories (data/foundry_docs, data/fabric, data/work,
data/web) for proof-of-concept identifiers and verifies every patient/trial ID that appears is
defined in the canonical registry (data/registry/registry.yaml). Also confirms the
hero patient and hero trial appear in every layer, and validates the generated Fabric
entity tables for referential integrity, unchanged hero rows, and at least one
criterion per trial.

Usage:  python tools/validate_consistency.py
Exit code 0 = consistent, 1 = problems found.
"""
from __future__ import annotations

import csv
import json
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data" / "registry"))
from data.fabric.generate import generate as generate_fabric_tables  # noqa: E402
from loader import hero_patient, hero_trial, patient_ids, trial_ids  # noqa: E402

FABRIC_DIR = ROOT / "data" / "fabric"
DATA_DIRS = {
    "foundry": ROOT / "data" / "foundry_docs",
    "fabric": ROOT / "data" / "fabric",
    "work": ROOT / "data" / "work",
    "web": ROOT / "data" / "web",
}

# PT-#### patient tokens and NCT######## trial tokens (out-of-range NCT99xxxxxx form)
PT_RE = re.compile(r"\bPT-\d{4}\b")
NCT_RE = re.compile(r"\bNCT\d{8}\b")
URL_RE = re.compile(r'https?://[^\s"<>]+')

# The hero facts the whole proof-of-concept pivots on: they must never drift in the Fabric tables.
HERO_CRCL_SERIES = [("2026-05-20", "55"), ("2026-06-18", "48")]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def validate_fabric_tables() -> list[str]:
    """Validate the generated Fabric entity tables: foreign keys resolve, hero rows are unchanged,
    every trial has at least one criterion, and there are no dangling references."""
    problems: list[str] = []
    try:
        tables = {
            name: _read_csv(FABRIC_DIR / name)
            for name in (
                "sites.csv", "people.csv", "patient_registry.csv", "biomarkers.csv", "labs.csv",
                "treatment_history.csv", "comorbidities.csv", "adverse_events.csv",
                "recist_assessments.csv", "consent.csv", "trials.csv", "trial_criteria.csv",
                "amendments.csv", "trial_enrollment.csv", "coordinator_workload.csv",
                "scheduling_slots.csv",
            )
        }
    except FileNotFoundError as exc:
        return [f"[fabric] missing generated table: {exc.filename} (run python data/fabric/generate.py)"]

    patient_ids_set = {r["patient_id"] for r in tables["patient_registry.csv"]}
    trial_ids_set = {r["trial_id"] for r in tables["trials.csv"]}
    site_ids_set = {r["site_id"] for r in tables["sites.csv"]}
    person_ids_set = {r["person_id"] for r in tables["people.csv"]}
    coordinator_ids_set = {r["coordinator_id"] for r in tables["coordinator_workload.csv"]}
    criterion_ids_set = {r["criterion_id"] for r in tables["trial_criteria.csv"]}

    # Foreign keys that must resolve.
    for table in ("labs.csv", "biomarkers.csv", "treatment_history.csv", "comorbidities.csv",
                  "adverse_events.csv", "recist_assessments.csv", "consent.csv", "trial_enrollment.csv"):
        for r in tables[table]:
            if r["patient_id"] not in patient_ids_set:
                problems.append(f"[fabric] {table}: dangling patient_id {r['patient_id']}")
    for r in tables["patient_registry.csv"]:
        if r["treating_oncologist_id"] not in person_ids_set:
            problems.append(f"[fabric] patient_registry.csv: dangling treating_oncologist_id {r['treating_oncologist_id']}")
        if r["coordinator_id"] not in coordinator_ids_set:
            problems.append(f"[fabric] patient_registry.csv: dangling coordinator_id {r['coordinator_id']}")
    for r in tables["people.csv"]:
        if r["site_id"] not in site_ids_set:
            problems.append(f"[fabric] people.csv: dangling site_id {r['site_id']}")
    for r in tables["trials.csv"]:
        if r["site_id"] not in site_ids_set:
            problems.append(f"[fabric] trials.csv: dangling site_id {r['site_id']}")
    for table in ("trial_criteria.csv", "amendments.csv", "trial_enrollment.csv", "consent.csv"):
        for r in tables[table]:
            if r["trial_id"] not in trial_ids_set:
                problems.append(f"[fabric] {table}: dangling trial_id {r['trial_id']}")
    for r in tables["amendments.csv"]:
        if r["modifies_criterion_id"] not in criterion_ids_set:
            problems.append(f"[fabric] amendments.csv: dangling modifies_criterion_id {r['modifies_criterion_id']}")
    for r in tables["scheduling_slots.csv"]:
        if r["site_id"] not in site_ids_set:
            problems.append(f"[fabric] scheduling_slots.csv: dangling site_id {r['site_id']}")
        if r["assigned_patient_id"] and r["assigned_patient_id"] not in patient_ids_set:
            problems.append(f"[fabric] scheduling_slots.csv: dangling assigned_patient_id {r['assigned_patient_id']}")

    # Every trial must have at least one criterion.
    trials_with_criteria = {r["trial_id"] for r in tables["trial_criteria.csv"]}
    for tid in trial_ids_set:
        if tid not in trials_with_criteria:
            problems.append(f"[fabric] trial {tid} has no criteria")

    # Hero rows must be unchanged.
    hero_crcl = sorted(
        ((r["lab_date"], r["value"]) for r in tables["labs.csv"]
         if r["patient_id"] == "PT-1042" and r["lab_type"] == "CrCl_CKD-EPI"),
        key=lambda item: item[0],
    )
    if hero_crcl != HERO_CRCL_SERIES:
        problems.append(f"[fabric] hero PT-1042 CrCl series changed: {hero_crcl} (expected {HERO_CRCL_SERIES})")
    hero_trial_row = next((r for r in tables["trials.csv"] if r["trial_id"] == "NCT99004324"), None)
    if hero_trial_row is None:
        problems.append("[fabric] hero trial NCT99004324 missing from trials.csv")
    else:
        if hero_trial_row["crcl_min"] != "50":
            problems.append(f"[fabric] hero trial crcl_min changed: {hero_trial_row['crcl_min']} (expected 50)")
        if hero_trial_row["biomarker_required"] != "EGFR exon 20 insertion":
            problems.append(f"[fabric] hero trial biomarker_required changed: {hero_trial_row['biomarker_required']}")
        if hero_trial_row["short_title"] != "EGFR exon 20 NSCLC":
            problems.append(f"[fabric] hero trial short_title changed: {hero_trial_row['short_title']}")

    return problems


def validate_generated_fabric_output() -> list[str]:
    """Regenerate tables outside the repository and compare every CSV byte-for-byte."""
    problems: list[str] = []
    expected_paths = sorted(FABRIC_DIR.glob("*.csv"))
    with tempfile.TemporaryDirectory(prefix="amciq-fabric-") as temp_dir:
        generated_dir = Path(temp_dir)
        generate_fabric_tables(generated_dir)
        generated_paths = sorted(generated_dir.glob("*.csv"))
        if [path.name for path in generated_paths] != [path.name for path in expected_paths]:
            return ["[fabric] generated CSV file set differs from the checked-in file set"]
        for expected, generated in zip(expected_paths, generated_paths, strict=True):
            expected_bytes = expected.read_bytes().replace(b"\r\n", b"\n")
            generated_bytes = generated.read_bytes()
            if b"\r\n" in generated_bytes:
                problems.append(
                    f"[fabric] {generated.name} generator output does not use canonical LF endings"
                )
            elif expected_bytes != generated_bytes:
                problems.append(
                    f"[fabric] {expected.name} differs from deterministic generator output"
                )
    return problems


def _source_url_allowed(url: str, known_nct: set[str]) -> bool:
    parsed = urlsplit(url.rstrip(".,);"))
    if parsed.scheme != "https":
        return False
    if parsed.hostname == "amciq.example.invalid":
        return True
    return (
        parsed.hostname == "clinicaltrials.gov"
        and parsed.path.startswith("/study/")
        and parsed.path.removeprefix("/study/") in known_nct
    )


def validate_synthetic_provenance(known_nct: set[str]) -> list[str]:
    """Keep fictional identifiers and source metadata visibly synthetic and non-resolving."""
    problems: list[str] = []
    for trial_id in known_nct:
        if not re.fullmatch(r"NCT99\d{6}", trial_id):
            problems.append(
                f"[registry] trial {trial_id} is outside the documented NCT99 synthetic range"
            )

    for layer, directory in DATA_DIRS.items():
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for url in URL_RE.findall(text):
                if _source_url_allowed(url, known_nct):
                    continue
                parsed = urlsplit(url.rstrip(".,);"))
                problems.append(
                    f"[{layer}] {path.name}: disallowed source URL "
                    f"{parsed.scheme}://{parsed.hostname}"
                )

    manifest_path = DATA_DIRS["web"] / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"[web] cannot parse manifest.json: {exc}")
    else:
        seen_files: set[str] = set()
        for entry in manifest:
            entry_file = str(entry.get("file", ""))
            if (
                not entry_file
                or Path(entry_file).is_absolute()
                or ".." in Path(entry_file).parts
            ):
                problems.append(f"[web] manifest has unsafe file path {entry_file!r}")
                continue
            if entry_file in seen_files:
                problems.append(f"[web] manifest contains duplicate file {entry_file}")
            seen_files.add(entry_file)
            if not (DATA_DIRS["web"] / entry_file).is_file():
                problems.append(f"[web] manifest target does not exist: {entry_file}")
            if entry.get("synthetic") is not True:
                problems.append(
                    f"[web] manifest entry {entry_file or '<unknown>'} is not marked synthetic"
                )
            if entry_file.endswith(".json") and (DATA_DIRS["web"] / entry_file).is_file():
                try:
                    record = json.loads(
                        (DATA_DIRS["web"] / entry_file).read_text(encoding="utf-8")
                    )
                except json.JSONDecodeError as exc:
                    problems.append(f"[web] cannot parse {entry_file}: {exc}")
                else:
                    if isinstance(record, dict) and record.get("synthetic") is not True:
                        problems.append(f"[web] record {entry_file} is not marked synthetic")
    return problems


def scan() -> int:
    known_pt = patient_ids()
    known_nct = trial_ids()
    hero_pt = hero_patient()["id"]
    hero_nct = hero_trial()["id"]

    problems: list[str] = []
    layer_has_hero_pt: dict[str, bool] = {k: False for k in DATA_DIRS}
    layer_has_hero_nct: dict[str, bool] = {k: False for k in DATA_DIRS}
    file_count = 0

    # The web layer is external content (trial registry, labels, guidelines, evidence);
    # it references the hero *trial* but not internal patient IDs, by design.
    patient_required_layers = {"foundry", "fabric", "work"}
    trial_required_layers = set(DATA_DIRS)

    for layer, d in DATA_DIRS.items():
        if not d.exists():
            problems.append(f"[{layer}] directory missing: {d}")
            continue
        files = [
            f
            for f in d.rglob("*")
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc"
        ]
        if not files:
            problems.append(f"[{layer}] no files generated in {d}")
        for f in files:
            file_count += 1
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:  # noqa: BLE001
                problems.append(f"[{layer}] cannot read {f.name}: {e}")
                continue
            for pt in set(PT_RE.findall(text)):
                if pt not in known_pt:
                    problems.append(f"[{layer}] {f.name}: unknown patient id {pt}")
            for nct in set(NCT_RE.findall(text)):
                if nct not in known_nct:
                    problems.append(f"[{layer}] {f.name}: unknown trial id {nct}")
            if hero_pt in text:
                layer_has_hero_pt[layer] = True
            if hero_nct in text:
                layer_has_hero_nct[layer] = True

    for layer in DATA_DIRS:
        if layer in patient_required_layers and not layer_has_hero_pt[layer]:
            problems.append(f"[{layer}] hero patient {hero_pt} not referenced")
        if layer in trial_required_layers and not layer_has_hero_nct[layer]:
            problems.append(f"[{layer}] hero trial {hero_nct} not referenced")

    print(f"Scanned {file_count} files across {len(DATA_DIRS)} layers.")
    print(f"Known patients: {sorted(known_pt)}")
    print(f"Known trials:   {sorted(known_nct)}")

    fabric_problems = validate_fabric_tables()
    problems.extend(fabric_problems)
    print(f"Validated Fabric entity tables: {'OK' if not fabric_problems else f'{len(fabric_problems)} problem(s)'}.")

    generation_problems = validate_generated_fabric_output()
    problems.extend(generation_problems)
    print(
        "Validated deterministic Fabric regeneration: "
        f"{'OK' if not generation_problems else f'{len(generation_problems)} problem(s)'}."
    )

    provenance_problems = validate_synthetic_provenance(known_nct)
    problems.extend(provenance_problems)
    print(
        "Validated synthetic identifiers and source metadata: "
        f"{'OK' if not provenance_problems else f'{len(provenance_problems)} problem(s)'}."
    )

    if problems:
        print(f"\nFAIL, {len(problems)} consistency problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(
        "\nOK, all referenced IDs are canonical; hero patient & trial appear in every layer; "
        "Fabric tables have full referential integrity, unchanged hero rows, and byte-identical "
        "deterministic regeneration."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(scan())
