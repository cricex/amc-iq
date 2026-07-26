"""Dynamic deepening: pursue the most valuable investigation leads within a hard budget.

After the parallel round of specialists returns, each may have emitted `Lead`s (things worth digging
into). This module scores those leads and re-dispatches the top ones to the best-suited specialist,
iterating up to `max_depth` rounds so a follow-up can itself spawn a further lead, always bounded in
depth, leads per round, total specialist calls, and wall-clock time so the investigation can never run
away.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from app.agents.base import Lead, SpecialistContext, SpecialistName, SpecialistResult

# A function that runs one specialist turn: (specialist, context) -> result.
RunFn = Callable[[SpecialistName, SpecialistContext], SpecialistResult]


@dataclass
class Budget:
    """Hard limits so deepening cannot run away. Defaults are scenario-safe (one extra round, two leads)."""

    max_depth: int = 1
    max_leads: int = 2
    time_budget_s: float = 60.0


def _score(lead: Lead) -> float:
    return lead.priority


def _eligible_leads(results: list[SpecialistResult], available: set[SpecialistName]) -> list[Lead]:
    """Leads whose target specialist is available and is not the lead's own origin, sorted by
    descending priority then lead id for determinism."""
    leads: list[Lead] = []
    for result in results:
        for lead in result.leads:
            target = lead.target_specialist
            if target is None or target not in available or target == lead.from_specialist:
                continue
            leads.append(lead)
    leads.sort(key=lambda lead: (-_score(lead), lead.id))
    return leads


def pursue_leads(
    round0: list[SpecialistResult],
    base: SpecialistContext,
    run_fn: RunFn,
    available: set[SpecialistName],
    budget: Budget | None = None,
) -> list[SpecialistResult]:
    """Iteratively pursue investigation leads. Each round takes the top `max_leads` leads from the
    previous round's results and dispatches them to their target specialist at the next depth, up to
    `max_depth` rounds, an aggregate call cap (`max_depth * max_leads`), and `time_budget_s`."""
    budget = budget or Budget()
    if budget.max_depth < 1 or budget.max_leads < 1:
        return []

    deadline = time.monotonic() + budget.time_budget_s
    call_cap = budget.max_depth * budget.max_leads
    deepened: list[SpecialistResult] = []
    frontier = round0

    for depth in range(1, budget.max_depth + 1):
        if time.monotonic() >= deadline or len(deepened) >= call_cap:
            break
        chosen = _eligible_leads(frontier, available)[: budget.max_leads]
        if not chosen:
            break
        round_results: list[SpecialistResult] = []
        for lead in chosen:
            if len(deepened) >= call_cap or time.monotonic() >= deadline:
                break
            assert lead.target_specialist is not None  # filtered in _eligible_leads
            context = SpecialistContext(
                question=base.question,
                intent=base.intent,
                patient_id=base.patient_id,
                trial_id=base.trial_id,
                depth=depth,
                focus_question=lead.focus_question or lead.rationale,
                extras={**base.extras, "lead_id": lead.id, "lead_kind": lead.kind},
            )
            result = run_fn(lead.target_specialist, context)
            round_results.append(result)
            deepened.append(result)
        frontier = round_results

    return deepened
