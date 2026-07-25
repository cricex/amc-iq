from __future__ import annotations

import app.agents.live as live
from app.agents.base import SpecialistContext, SpecialistName
from app.config import Settings


def _settings() -> Settings:
    return Settings(
        USE_MULTI_AGENT=True,
        USE_LIVE_SPECIALISTS=True,
        SPECIALIST_ELIGIBILITY_AGENT="test-specialist-eligibility",
        SPECIALIST_RENAL_AGENT="test-specialist-renal",
        SPECIALIST_GENOMICS_AGENT="test-specialist-genomics",
        SPECIALIST_PROTOCOL_AGENT="test-specialist-protocol",
        SPECIALIST_WORKFLOW_AGENT="test-specialist-workflow",
        SPECIALIST_EVIDENCE_AGENT="test-specialist-evidence",
    )


def _ctx(depth: int = 0) -> SpecialistContext:
    return SpecialistContext(
        question="Is PT-1042 eligible?", intent="eligibility",
        patient_id="PT-1042", trial_id="NCT99004324", depth=depth,
    )


def test_live_run_fn_uses_agent_summary_but_keeps_ground_truth_findings(monkeypatch) -> None:
    settings = _settings()
    monkeypatch.setattr(live, "_call_specialist_agent", lambda *a, **k: "LIVE narrative from Foundry agent.")
    run_fn = live.make_run_fn(settings)
    result = run_fn(SpecialistName.ELIGIBILITY, _ctx())
    # The live agent narrative replaces the summary...
    assert result.summary == "LIVE narrative from Foundry agent."
    # ...but the structured findings remain grounded ground truth (borderline renal present).
    assert result.findings
    assert any(f.category == "renal" for f in result.findings)


def test_live_run_fn_falls_back_to_grounded_summary_on_agent_failure(monkeypatch) -> None:
    settings = _settings()
    monkeypatch.setattr(live, "_call_specialist_agent", lambda *a, **k: "")  # simulate failure/empty
    run_fn = live.make_run_fn(settings)
    result = run_fn(SpecialistName.ELIGIBILITY, _ctx())
    # Falls back to the deterministic grounded summary (never blank), findings intact.
    assert result.summary
    assert "LIVE" not in result.summary
    assert result.findings


def test_live_run_fn_respects_role_set_and_depth(monkeypatch) -> None:
    settings = _settings()  # default roles: eligibility,renal_labs,genomics
    calls: list[str] = []
    monkeypatch.setattr(live, "_call_specialist_agent", lambda s, name, q: calls.append(name) or "X")
    run_fn = live.make_run_fn(settings)
    # A role NOT in LIVE_SPECIALIST_ROLES (workflow) -> no live call.
    run_fn(SpecialistName.WORKFLOW, _ctx())
    assert calls == []
    # An in-set role at depth 0 -> live call.
    run_fn(SpecialistName.ELIGIBILITY, _ctx())
    assert calls == [settings.SPECIALIST_ELIGIBILITY_AGENT]
    # In-set role but a deepened turn (depth>0) -> no additional live call.
    run_fn(SpecialistName.ELIGIBILITY, _ctx(depth=1))
    assert calls == [settings.SPECIALIST_ELIGIBILITY_AGENT]


def test_run_live_team_reconciles_against_ground_truth(monkeypatch) -> None:
    settings = _settings()
    monkeypatch.setattr(live, "_call_specialist_agent", lambda *a, **k: "Live specialist reasoning.")
    team = live.run_live_team(settings, "PT-1042", "NCT99004324", "Is PT-1042 eligible?", "eligibility")
    # Same deterministic conclusion as the grounded team (findings are ground truth).
    assert team.synthesis.overall_status == "uncertain"
    # The eligibility specialist ran with the live narrative.
    elig = next((r for r in team.round0 if r.specialist == SpecialistName.ELIGIBILITY), None)
    assert elig is not None and elig.summary == "Live specialist reasoning."
