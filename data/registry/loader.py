"""Canonical synthetic-scenario registry loader.

`data/registry/registry.yaml` is the single source of truth for every synthetic
identifier in the scenario. Import from here; never hand-type IDs.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REGISTRY_PATH = Path(__file__).resolve().parent / "registry.yaml"


@lru_cache(maxsize=1)
def load_registry(path: str | Path | None = None) -> dict[str, Any]:
    """Load and cache the registry YAML as a dict."""
    p = Path(path) if path else REGISTRY_PATH
    with p.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _ids(items: list[dict[str, Any]] | None) -> set[str]:
    return {str(i["id"]) for i in (items or []) if "id" in i}


def patient_ids() -> set[str]:
    return _ids(load_registry().get("patients"))


def trial_ids() -> set[str]:
    return _ids(load_registry().get("trials"))


def people_ids() -> set[str]:
    return _ids(load_registry().get("people"))


def site_ids() -> set[str]:
    return _ids(load_registry().get("sites"))


def hero_patient() -> dict[str, Any]:
    return next(p for p in load_registry()["patients"] if p.get("hero"))


def hero_trial() -> dict[str, Any]:
    return next(t for t in load_registry()["trials"] if t.get("hero"))


def all_known_ids() -> set[str]:
    return patient_ids() | trial_ids() | people_ids() | site_ids()


if __name__ == "__main__":
    reg = load_registry()
    print(f"Loaded registry: {len(reg.get('patients', []))} patients, "
          f"{len(reg.get('trials', []))} trials, {len(reg.get('people', []))} people")
    print(f"Hero patient: {hero_patient()['id']} ({hero_patient()['display']})")
    print(f"Hero trial:   {hero_trial()['id']} ({hero_trial()['short']})")
