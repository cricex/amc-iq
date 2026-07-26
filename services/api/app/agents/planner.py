"""Planner: decide which specialists to dispatch for a question, and their focus.

Keeps the deterministic, auditable selection in code (rather than a black-box router) so the proof-of-concept is
reproducible and the choice is explainable in the assessment trace.
"""
from __future__ import annotations

from app.agents.base import SpecialistName

# Base specialist set per question intent (see orchestrator.classify_intent).
_BY_INTENT: dict[str, list[SpecialistName]] = {
    "eligibility": [
        SpecialistName.ELIGIBILITY,
        SpecialistName.RENAL_LABS,
        SpecialistName.GENOMICS,
        SpecialistName.PROTOCOL,
        SpecialistName.WORKFLOW,
    ],
    "screening": [
        SpecialistName.ELIGIBILITY,
        SpecialistName.RENAL_LABS,
        SpecialistName.PROTOCOL,
        SpecialistName.WORKFLOW,
    ],
    "protocol": [SpecialistName.PROTOCOL, SpecialistName.ELIGIBILITY, SpecialistName.GENOMICS],
    "data_gaps": [SpecialistName.RENAL_LABS, SpecialistName.WORKFLOW, SpecialistName.ELIGIBILITY],
    "workflow": [SpecialistName.WORKFLOW, SpecialistName.ELIGIBILITY],
    "evidence": [SpecialistName.EVIDENCE, SpecialistName.ELIGIBILITY, SpecialistName.GENOMICS],
    "external_context": [SpecialistName.EVIDENCE],
}

# Extra specialists pulled in when the question text mentions their domain, regardless of intent.
_KEYWORD_SPECIALISTS: list[tuple[tuple[str, ...], SpecialistName]] = [
    (("crcl", "renal", "creatinine", "kidney", "lab", "labs"), SpecialistName.RENAL_LABS),
    (("egfr", "exon", "alk", "kras", "braf", "biomarker", "mutation", "variant", "genomic"), SpecialistName.GENOMICS),
    (("amendment", "exclusion", "protocol", "prior therapy", "prior platinum"), SpecialistName.PROTOCOL),
    (("coordinator", "task", "schedule", "scheduling", "tumor board", "handoff", "owner"), SpecialistName.WORKFLOW),
    (("registry", "literature", "guideline", "external", "landscape"), SpecialistName.EVIDENCE),
]


def select_specialists(intent: str, question: str) -> list[SpecialistName]:
    """Return the ordered, de-duplicated set of specialists to dispatch for this question."""
    q = question.lower()
    selected: list[SpecialistName] = list(_BY_INTENT.get(intent, _BY_INTENT["eligibility"]))
    for keywords, specialist in _KEYWORD_SPECIALISTS:
        if specialist not in selected and any(k in q for k in keywords):
            selected.append(specialist)
    # Preserve order, drop duplicates.
    seen: set[SpecialistName] = set()
    ordered: list[SpecialistName] = []
    for name in selected:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered
