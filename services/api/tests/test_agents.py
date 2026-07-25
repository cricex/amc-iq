from __future__ import annotations

from app.agents.base import Finding, Lead, SpecialistContext, SpecialistName, SpecialistResult
from app.agents.deepening import Budget, pursue_leads
from app.agents.planner import select_specialists
from app.agents.registry import all_specialists
from app.agents.synthesizer import synthesize
from app.agents.team import run_team


def _ctx(intent: str = "eligibility", question: str = "Is PT-1042 eligible?") -> SpecialistContext:
    return SpecialistContext(question=question, intent=intent, patient_id="PT-1042", trial_id="NCT99004324")


def test_planner_selects_by_intent_and_keywords() -> None:
    names = select_specialists("workflow", "who owns the CrCl task?")
    assert SpecialistName.WORKFLOW in names
    assert SpecialistName.RENAL_LABS in names  # pulled in by the "crcl" keyword
    assert select_specialists("external_context", "external registry landscape") == [SpecialistName.EVIDENCE]


def test_planner_dedupes_and_leads_with_eligibility() -> None:
    names = select_specialists("eligibility", "EGFR exon 20 and CrCl renal function")
    assert len(names) == len(set(names))
    assert names[0] == SpecialistName.ELIGIBILITY


def test_deepening_respects_budget_targets_and_no_self_investigation() -> None:
    origin = SpecialistName.RENAL_LABS
    result = SpecialistResult(
        specialist=origin,
        leads=[
            Lead(id="L1", from_specialist=origin, kind="alt", rationale="co-mutation",
                 target_specialist=SpecialistName.GENOMICS, focus_question="check resistance", priority=0.9),
            Lead(id="L2", from_specialist=origin, kind="self", rationale="x",
                 target_specialist=origin, priority=0.95),  # self-target -> ignored
            Lead(id="L3", from_specialist=origin, kind="wf", rationale="task",
                 target_specialist=SpecialistName.WORKFLOW, priority=0.5),
        ],
    )
    calls: list[tuple[SpecialistName, int, str]] = []

    def run_fn(name: SpecialistName, ctx: SpecialistContext) -> SpecialistResult:
        calls.append((name, ctx.depth, ctx.focus_question))
        return SpecialistResult(specialist=name, depth=ctx.depth)

    out = pursue_leads([result], _ctx(), run_fn, {SpecialistName.GENOMICS, SpecialistName.WORKFLOW},
                       Budget(max_depth=1, max_leads=1))
    assert len(out) == 1  # max_leads honored
    assert calls[0][0] == SpecialistName.GENOMICS  # highest priority pursuable lead
    assert calls[0][1] == 1  # dispatched at depth+1
    assert all(name != origin for name, _, _ in calls)  # never re-investigates its own lead


def test_deepening_disabled_when_depth_zero() -> None:
    origin = SpecialistName.RENAL_LABS
    result = SpecialistResult(
        specialist=origin,
        leads=[Lead(id="L1", from_specialist=origin, kind="x", rationale="y",
                    target_specialist=SpecialistName.GENOMICS, priority=1.0)],
    )
    out = pursue_leads([result], _ctx(), lambda n, c: SpecialistResult(specialist=n),
                       {SpecialistName.GENOMICS}, Budget(max_depth=0))
    assert out == []


def test_deepening_honors_max_depth_across_rounds() -> None:
    # A -> (lead) -> Genomics(depth 1) -> (lead) -> Evidence(depth 2). max_depth must gate the rounds.
    def run_fn(name: SpecialistName, ctx: SpecialistContext) -> SpecialistResult:
        if name == SpecialistName.GENOMICS and ctx.depth == 1:
            return SpecialistResult(
                specialist=name, depth=1,
                leads=[Lead(id="L2", from_specialist=name, kind="k", rationale="r",
                            target_specialist=SpecialistName.EVIDENCE, priority=0.9)],
            )
        return SpecialistResult(specialist=name, depth=ctx.depth)

    round0 = [SpecialistResult(
        specialist=SpecialistName.RENAL_LABS,
        leads=[Lead(id="L1", from_specialist=SpecialistName.RENAL_LABS, kind="k", rationale="r",
                    target_specialist=SpecialistName.GENOMICS, priority=0.9)],
    )]
    available = {SpecialistName.GENOMICS, SpecialistName.EVIDENCE}

    one = pursue_leads(round0, _ctx(), run_fn, available, Budget(max_depth=1, max_leads=2))
    assert [r.depth for r in one] == [1]  # a single round, as before

    two = pursue_leads(round0, _ctx(), run_fn, available, Budget(max_depth=2, max_leads=2))
    assert [r.depth for r in two] == [1, 2]  # the depth-1 lead is itself pursued at depth 2


def test_synthesizer_critic_overrides_and_logs_conflict() -> None:
    results = [
        SpecialistResult(
            specialist=SpecialistName.RENAL_LABS,
            findings=[Finding(label="CrCl", detail="48 vs 50", status="met", citations=["r3"])],
        )
    ]
    syn = synthesize(results, ground_truth={"CrCl": "uncertain"})
    assert syn.findings[0].status == "uncertain"  # ground truth wins over the specialist
    assert syn.conflicts and "CrCl" in syn.conflicts[0]
    assert syn.overall_status == "uncertain"


def test_synthesizer_dedupes_to_worst_status() -> None:
    results = [
        SpecialistResult(specialist=SpecialistName.ELIGIBILITY,
                         findings=[Finding(label="Biomarker", detail="x", status="met")]),
        SpecialistResult(specialist=SpecialistName.GENOMICS,
                         findings=[Finding(label="Biomarker", detail="x2", status="not_met", citations=["r1"])]),
    ]
    syn = synthesize(results)
    assert len(syn.findings) == 1
    assert syn.findings[0].status == "not_met"
    assert "r1" in syn.findings[0].citations
    assert syn.overall_status == "not_met"


def test_synthesizer_records_failures() -> None:
    results = [SpecialistResult(specialist=SpecialistName.WORKFLOW, status="failed", error="timeout")]
    syn = synthesize(results)
    assert syn.failures and "workflow" in syn.failures[0]
    assert syn.specialists_run == 0


def test_run_team_end_to_end_with_deepening() -> None:
    def run_fn(name: SpecialistName, ctx: SpecialistContext) -> SpecialistResult:
        if name == SpecialistName.RENAL_LABS and ctx.depth == 0:
            return SpecialistResult(
                specialist=name,
                summary="borderline",
                findings=[Finding(label="CrCl", detail="48", status="uncertain", citations=["r3"])],
                leads=[Lead(id="L1", from_specialist=name, kind="alt_trials", rationale="co-mutation",
                            target_specialist=SpecialistName.GENOMICS, focus_question="resistance?", priority=0.9)],
            )
        if name == SpecialistName.GENOMICS and ctx.depth == 1:
            return SpecialistResult(specialist=name, depth=1,
                                    findings=[Finding(label="Resistance", detail="none", status="met", citations=["r1"])])
        return SpecialistResult(specialist=name)

    team = run_team(_ctx("eligibility", "Is PT-1042 eligible? EGFR CrCl"), run_fn, all_specialists(),
                    ground_truth={"CrCl": "uncertain"})
    assert SpecialistName.RENAL_LABS in team.specialists
    assert len(team.deepened) == 1  # the genomics lead was pursued at depth 1
    assert team.deepened[0].depth == 1
    labels = {f.label for f in team.synthesis.findings}
    assert {"CrCl", "Resistance"} <= labels
    assert team.synthesis.overall_status == "uncertain"
