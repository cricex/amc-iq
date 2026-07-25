"""Core types for the multi-agent framework.

Everything is plain dataclasses + a Protocol so specialists can be implemented as deterministic
"grounded" objects (findings from the Fabric eligibility verdicts) or as adapters over live Foundry
agents, interchangeably.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class SpecialistName(str, Enum):
    """The domain specialists that make up the Foundry IQ team."""

    ELIGIBILITY = "eligibility"
    GENOMICS = "genomics"
    RENAL_LABS = "renal_labs"
    PROTOCOL = "protocol"
    WORKFLOW = "workflow"
    EVIDENCE = "evidence"


# Status vocabulary shared with the UI sentiment system (met/uncertain/not_met/info).
FindingStatus = str  # "met" | "uncertain" | "not_met" | "info"


@dataclass
class Finding:
    """One atomic thing a specialist established, with a clinical status and its supporting citations.

    `criterion_id` links the finding to the criterion it evaluates so the Critic can cross-
    check it against ground truth even when a live agent uses a natural-language `label`. `category`
    is the clinical category (renal, biomarker, ...) or "" for non-eligibility findings (workflow,
    evidence)."""

    label: str
    detail: str
    status: FindingStatus = "info"
    citations: list[str] = field(default_factory=list)
    criterion_id: str = ""
    category: str = ""


@dataclass
class Lead:
    """An investigation lead: something a specialist noticed that is worth pursuing deeper. The
    deepening loop scores leads and re-dispatches the top ones to the best-suited specialist."""

    id: str
    from_specialist: SpecialistName
    kind: str
    rationale: str
    target_specialist: SpecialistName | None = None
    focus_question: str = ""
    priority: float = 0.5


@dataclass
class SpecialistContext:
    """Everything a specialist needs for one turn. `depth` and `focus_question` are set when the turn
    is a deepened follow-up spawned from a lead rather than the initial parallel dispatch."""

    question: str
    intent: str
    patient_id: str
    trial_id: str
    depth: int = 0
    focus_question: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class SpecialistResult:
    """What a specialist returns for one turn: a short summary, structured findings, citations, and any
    new leads. `status='failed'` lets the orchestrator degrade gracefully without dropping the case."""

    specialist: SpecialistName
    summary: str = ""
    findings: list[Finding] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    leads: list[Lead] = field(default_factory=list)
    depth: int = 0
    status: str = "completed"  # "completed" | "failed"
    error: str = ""


@runtime_checkable
class Specialist(Protocol):
    """A domain agent. Implemented either as a deterministic grounded specialist (findings from the
    Fabric eligibility verdicts) or as an adapter over a live Foundry agent."""

    name: SpecialistName
    domain: str

    def run(self, context: SpecialistContext) -> SpecialistResult:
        ...
