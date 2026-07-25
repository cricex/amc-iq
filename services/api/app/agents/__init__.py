"""Multi-agent orchestration for AMC IQ (Foundry phase).

This package turns the single hosted Foundry agent into a coordinated TEAM of domain specialists that
work in parallel and DEEPEN their investigation as they discover related leads, reconciled by a
synthesizer/critic into a governed, cited, human-reviewed assessment.

Layers:
- base.py        core types: Specialist protocol, SpecialistContext, Finding, Lead, SpecialistResult.
- registry.py    the specialist roster + which data-agent/KB neighborhood each owns; live vs grounded.
- planner.py     question/intent -> which specialists to dispatch (+ their focus).
- deepening.py   bounded investigation-lead loop with a depth/time/cost budget.
- synthesizer.py reconcile specialist findings + Critic checks against the Fabric ground-truth verdicts.

Design notes: app/eligibility.py (Fabric Data Agent facts + the Foundry eligibility-evaluator agent)
is the GROUND TRUTH for eligibility verdicts; specialists narrate/interpret over it and never invent
labs. The whole framework is decoupled from that module via an injected `ground_truth` mapping, so it
is unit-testable in isolation.
"""
from __future__ import annotations

from app.agents.base import (
    Finding,
    Lead,
    Specialist,
    SpecialistContext,
    SpecialistName,
    SpecialistResult,
)

__all__ = [
    "Finding",
    "Lead",
    "Specialist",
    "SpecialistContext",
    "SpecialistName",
    "SpecialistResult",
]
