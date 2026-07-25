"""Convert a team run into the Assessment Steps trace the UI renders.

Maps planner -> specialist turns -> deepening hops -> Critic/synthesis into the existing TraceStep
contract so the multi-agent investigation (including where it deepened) is auditable in the UI without
any new frontend surface.
"""
from __future__ import annotations

from app.agents.team import TeamRun
from app.schemas import TraceStep


def _specialist_label(value: str) -> str:
    return value.replace("_", "/").title()


def team_to_trace(team: TeamRun) -> list[TraceStep]:
    steps: list[TraceStep] = []
    ts = 0

    def add(step: str, detail: str) -> None:
        nonlocal ts
        steps.append(TraceStep(step=step, source=None, detail=detail, ts=ts, status="completed"))
        ts += 1

    names = ", ".join(_specialist_label(n.value) for n in team.specialists)
    add("Planned specialist team", f"Dispatched {len(team.specialists)} specialists in parallel: {names}.")

    for result in team.round0:
        label = _specialist_label(result.specialist.value)
        if result.status == "failed":
            add(f"Queried {label} specialist", f"Unavailable: {result.error or 'no response'}.")
        else:
            add(f"Queried {label} specialist", result.summary or "Reviewed the relevant facts.")

    for result in team.deepened:
        label = _specialist_label(result.specialist.value)
        detail = result.summary or (result.findings[0].detail if result.findings else "Followed up on a lead.")
        add(f"Deepened via {label}", detail)

    synthesis = team.synthesis
    if synthesis is not None:
        conflict_note = (
            f"{len(synthesis.conflicts)} conflict(s) reconciled against ground truth"
            if synthesis.conflicts
            else "no conflicts with ground truth"
        )
        add(
            "Reconciled findings (Critic)",
            f"Overall {synthesis.overall_status}; {synthesis.leads_pursued} lead(s) pursued; {conflict_note}.",
        )
    add("Composed governed assessment", "Produced a cited, human-reviewed trial-readiness assessment.")
    return steps
