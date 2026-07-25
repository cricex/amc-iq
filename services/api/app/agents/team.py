"""Team engine: compose planner -> parallel dispatch -> bounded deepening -> synthesizer/critic.

Pure and synchronous: it takes a `run_fn` that executes one specialist turn, so it is fully unit-
testable with mock specialists and agnostic to whether a specialist is grounded or a live Foundry
agent. The orchestrator supplies a real (parallel/async) run_fn when wiring this in.
"""
from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from app.agents.base import SpecialistContext, SpecialistName, SpecialistResult
from app.agents.deepening import Budget, RunFn, pursue_leads
from app.agents.planner import select_specialists
from app.agents.synthesizer import Synthesis, synthesize


@dataclass
class TeamRun:
    specialists: list[SpecialistName] = field(default_factory=list)
    round0: list[SpecialistResult] = field(default_factory=list)
    deepened: list[SpecialistResult] = field(default_factory=list)
    synthesis: Synthesis | None = None


def run_team(
    context: SpecialistContext,
    run_fn: RunFn,
    available: Iterable[SpecialistName],
    ground_truth: dict[str, str] | None = None,
    budget: Budget | None = None,
) -> TeamRun:
    """Run one full team investigation for a question.

    1. Planner selects specialists (intersected with those available).
    2. Each runs a first (parallel) turn via `run_fn`.
    3. The deepening loop pursues the top investigation leads within budget.
    4. The synthesizer/critic reconciles every turn against ground truth.
    """
    available_set = set(available)
    names = [n for n in select_specialists(context.intent, context.question) if n in available_set]

    # First round runs the specialists concurrently; results are collected in planner order so the
    # trace and synthesis stay deterministic regardless of completion order.
    if names:
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            round0 = list(pool.map(lambda name: run_fn(name, context), names))
    else:
        round0 = []
    deepened = pursue_leads(round0, context, run_fn, available_set, budget)
    synthesis = synthesize(round0 + deepened, ground_truth, leads_pursued=len(deepened))

    return TeamRun(specialists=names, round0=round0, deepened=deepened, synthesis=synthesis)
