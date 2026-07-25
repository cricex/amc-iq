"""Specialist roster: domain, the scoped Fabric data-agent neighborhood, and Foundry KB each owns.

This is the map from the abstract specialist team to the concrete Fabric/Foundry resources. The
`data_agent` values mirror the scoped data agents over the Fabric Lakehouse (labs/renal, trials/
criteria, workflow/scheduling); `kb` names the Foundry knowledge neighborhood. Live adapters use these
to route; grounded specialists ignore them.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.agents.base import SpecialistName


@dataclass(frozen=True)
class SpecialistSpec:
    name: SpecialistName
    domain: str
    data_agent: str
    kb: str


REGISTRY: dict[SpecialistName, SpecialistSpec] = {
    SpecialistName.ELIGIBILITY: SpecialistSpec(
        SpecialistName.ELIGIBILITY, "Criteria matching (Fabric eligibility)", "trials_criteria", "protocol_kb"
    ),
    SpecialistName.GENOMICS: SpecialistSpec(
        SpecialistName.GENOMICS, "Molecular reports and variant interpretation", "labs_renal", "genomics_kb"
    ),
    SpecialistName.RENAL_LABS: SpecialistSpec(
        SpecialistName.RENAL_LABS, "Renal function, lab thresholds, trends, staleness", "labs_renal", "labs_kb"
    ),
    SpecialistName.PROTOCOL: SpecialistSpec(
        SpecialistName.PROTOCOL, "Exclusion interpretation and amendment deltas", "trials_criteria", "protocol_kb"
    ),
    SpecialistName.WORKFLOW: SpecialistSpec(
        SpecialistName.WORKFLOW, "Tasks, ownership, scheduling, capacity", "workflow_scheduling", "workflow_kb"
    ),
    SpecialistName.EVIDENCE: SpecialistSpec(
        SpecialistName.EVIDENCE, "External literature, registry, guidelines", "none", "web_kb"
    ),
}


def all_specialists() -> list[SpecialistName]:
    return list(REGISTRY.keys())
