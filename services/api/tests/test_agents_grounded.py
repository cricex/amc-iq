from __future__ import annotations

from app.agents.base import SpecialistName
from app.agents.grounded import run_grounded_team


def test_hero_team_is_uncertain_and_deepens_to_workflow() -> None:
    team = run_grounded_team("PT-1042", "NCT99004324", "Is PT-1042 eligible?", "eligibility")
    # The renal criterion is borderline -> the overall conclusion is uncertain, not a hard stop.
    assert team.synthesis.overall_status == "uncertain"
    assert SpecialistName.RENAL_LABS in team.specialists
    # Renal (and protocol) borderline findings spawn workflow follow-ups: dynamic deepening.
    assert team.synthesis.leads_pursued >= 1
    deepened_labels = {f.label for r in team.deepened for f in r.findings}
    assert "repeat_crcl_scheduling" in deepened_labels
    assert all(r.depth == 1 for r in team.deepened)
    # Every grounded specialist reads the same Fabric verdicts, so there are no Critic conflicts.
    assert team.synthesis.conflicts == []


def test_biomarker_mismatch_is_not_met_and_deepens_to_alternative_trials() -> None:
    # PT-1049 is a biomarker-mismatch archetype against the EGFR hero trial -> hard biomarker mismatch.
    team = run_grounded_team("PT-1049", "NCT99004324", "Is PT-1049 eligible?", "eligibility")
    assert team.synthesis.overall_status == "not_met"
    deepened_labels = {f.label for r in team.deepened for f in r.findings}
    assert "alternative_trials" in deepened_labels
    assert any(r.specialist == SpecialistName.EVIDENCE for r in team.deepened)


def test_grounded_team_is_deterministic() -> None:
    a = run_grounded_team("PT-1042", "NCT99004324", "Is PT-1042 eligible?", "eligibility")
    b = run_grounded_team("PT-1042", "NCT99004324", "Is PT-1042 eligible?", "eligibility")
    assert a.specialists == b.specialists
    assert a.synthesis.overall_status == b.synthesis.overall_status
    assert [r.specialist for r in a.deepened] == [r.specialist for r in b.deepened]
    assert {f.label for f in a.synthesis.findings} == {f.label for f in b.synthesis.findings}


def test_orchestrator_multi_agent_trace_renders_team_and_deepening() -> None:
    from app.orchestrator import Orchestrator
    from app.schemas import AskRequest

    orch = Orchestrator()
    ctx = orch._context_for(AskRequest(question="Is PT-1042 eligible for NCT99004324?"))
    steps = orch._multi_agent_trace("Is PT-1042 eligible for NCT99004324?", ctx, "eligibility")
    titles = [s.step for s in steps]
    assert titles[0] == "Planned specialist team"
    assert any(t.startswith("Queried ") for t in titles)
    assert any("Deepened via" in t for t in titles)
    assert titles[-1] == "Composed governed assessment"
