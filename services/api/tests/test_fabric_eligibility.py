from __future__ import annotations

import pytest

import app.sources.fabric_eligibility as fe
from app.config import Settings
from app.orchestrator import Orchestrator
from app.schemas import AskRequest

LIVE_EVALUATE = fe.evaluate_eligibility_fabric


VERDICT = """CRIT|NCT99004324-DX|diagnosis|met|NSCLC matches
CRIT|NCT99004324-BIO|biomarker|met|EGFR exon 20 detected
CRIT|NCT99004324-PS|performance|met|ECOG 1 <= 1
CRIT|NCT99004324-REN|renal|uncertain|CrCl 48 within 5 of 50; prior 55
CRIT|NCT99004324-RX|prior_therapy|uncertain|Platinum doublet; amendment -> PI confirmation
OVERALL|likely_eligible_pending"""

CRITERIA = """NCT99004324-DX | inclusion | diagnosis | Histologically confirmed metastatic NSCLC | Patient | cancer_type | matches | NSCLC
NCT99004324-BIO | inclusion | biomarker | Documented EGFR exon 20 insertion | Biomarker | marker | present | EGFR exon 20 insertion
NCT99004324-PS | inclusion | performance | ECOG performance status 0 to 1 | Patient | ecog_ps | <= | 1
NCT99004324-REN | inclusion | renal | CrCl >= 50 mL/min (CKD-EPI) | Lab | CrCl_CKD-EPI | >= | 50
NCT99004324-RX | exclusion | prior_therapy | Prior platinum doublet chemotherapy | Treatment | drug_class | excludes | Platinum doublet"""

FACTS = """cancer_type: NSCLC
ecog_ps: 1
EGFR exon 20 insertion=Detected
48 on 6/18/2026
55 on 5/20/2026
Carboplatin + Pemetrexed (Platinum doublet)
AMD-2 modifies NCT99004324-RX"""


def test_parse_verdict_maps_criteria() -> None:
    results = fe.parse_verdict(VERDICT)
    assert [r.criterion for r in results] == [
        "NCT99004324-DX", "NCT99004324-BIO", "NCT99004324-PS", "NCT99004324-REN", "NCT99004324-RX",
    ]
    by_id = {r.criterion: r for r in results}
    assert by_id["NCT99004324-REN"].status == "uncertain"
    assert by_id["NCT99004324-REN"].category == "renal"
    assert by_id["NCT99004324-BIO"].status == "met"
    # OVERALL is not a CRIT line and must not be parsed as a criterion.
    assert all(not r.criterion.startswith("OVERALL") for r in results)


def test_parse_verdict_ignores_prose() -> None:
    assert fe.parse_verdict("I could not evaluate this. Please rephrase.") == []


def test_parse_retrieved_criteria_maps_structured_rows() -> None:
    criteria = fe.parse_retrieved_criteria(CRITERIA)
    assert [criterion.criterion_id for criterion in criteria] == [
        "NCT99004324-DX",
        "NCT99004324-BIO",
        "NCT99004324-PS",
        "NCT99004324-REN",
        "NCT99004324-RX",
    ]
    assert criteria[-1].kind == "exclusion"
    assert criteria[-1].category == "prior_therapy"


def test_retrieve_criteria_retries_after_data_agent_apology(monkeypatch) -> None:
    answers = iter(
        [
            "There was a technical issue processing the request.",
            CRITERIA,
        ]
    )
    questions: list[str] = []

    def fake_data_agent(settings, question):  # noqa: ARG001
        questions.append(question)
        return next(answers)

    monkeypatch.setattr(fe, "_data_agent", fake_data_agent)

    assert fe.retrieve_criteria(Settings(), "NCT99004324") == CRITERIA
    assert len(questions) == 2
    assert all("Use only the Lakehouse datasource" in question for question in questions)
    assert "return criterion_id, kind, category" in questions[0]


def test_patient_facts_forces_lakehouse_source(monkeypatch) -> None:
    questions: list[str] = []
    monkeypatch.setattr(
        fe,
        "_data_agent",
        lambda settings, question: questions.append(question) or FACTS,  # noqa: ARG005
    )

    assert fe.retrieve_patient_facts(Settings(), "PT-1042", "NCT99004324") == FACTS
    assert len(questions) == 1
    assert "Use only the Lakehouse datasource" in questions[0]
    assert "not any GraphModel" in questions[0]


def test_live_evaluator_retries_partial_verdict_and_normalizes_criteria(monkeypatch) -> None:
    verdicts = iter(["OVERALL|likely_eligible_pending", VERDICT])
    instructions: list[str] = []
    monkeypatch.setattr(fe, "retrieve_criteria", lambda settings, trial_id: CRITERIA)
    monkeypatch.setattr(
        fe,
        "retrieve_patient_facts",
        lambda settings, patient_id, trial_id: FACTS,
    )

    def fake_evaluator(settings, facts, instruction="Evaluate eligibility."):  # noqa: ARG001
        instructions.append(instruction)
        return next(verdicts)

    monkeypatch.setattr(fe, "_evaluator", fake_evaluator)

    results = LIVE_EVALUATE(Settings(), "PT-1042", "NCT99004324")

    assert len(results) == 5
    assert results[0].kind == "inclusion"
    assert results[0].description == "Histologically confirmed metastatic NSCLC"
    assert results[-1].kind == "exclusion"
    assert "exactly one CRIT line" in instructions[1]


def test_live_evaluator_raises_after_two_incomplete_verdicts(monkeypatch) -> None:
    monkeypatch.setattr(fe, "retrieve_criteria", lambda settings, trial_id: CRITERIA)
    monkeypatch.setattr(
        fe,
        "retrieve_patient_facts",
        lambda settings, patient_id, trial_id: FACTS,
    )
    monkeypatch.setattr(
        fe,
        "_evaluator",
        lambda settings, facts, instruction="Evaluate eligibility.": (
            "OVERALL|likely_eligible_pending"
        ),
    )

    with pytest.raises(RuntimeError, match=r"expected 5, parsed 0"):
        LIVE_EVALUATE(Settings(), "PT-1042", "NCT99004324")


def test_orchestrator_uses_fabric_eligibility(monkeypatch) -> None:
    called = {}

    def fake_eval(settings, patient_id, trial_id):
        called["args"] = (patient_id, trial_id)
        return fe.parse_verdict(VERDICT)

    monkeypatch.setattr(fe, "evaluate_eligibility_fabric", fake_eval)
    orch = Orchestrator(settings=Settings(APP_ENVIRONMENT="production"))
    result = orch.answer(AskRequest(question="Is PT-1042 eligible for NCT99004324?"))
    assert called["args"] == ("PT-1042", "NCT99004324")
    assert result.eligibility.assessment == "likely_eligible_pending"


def test_orchestrator_raises_when_fabric_fails(monkeypatch) -> None:
    from app.eligibility import EligibilityUnavailable

    def boom(settings, patient_id, trial_id):
        raise RuntimeError("data agent unavailable")

    monkeypatch.setattr(fe, "evaluate_eligibility_fabric", boom)
    orch = Orchestrator(settings=Settings(APP_ENVIRONMENT="production"))
    # Live/production evaluation remains Fabric-native: a Fabric failure surfaces (normalized to
    # EligibilityUnavailable) rather than being silently substituted by the local mock evaluator.
    with pytest.raises(EligibilityUnavailable):
        orch.answer(AskRequest(question="Is PT-1042 eligible for NCT99004324?"))


def test_ask_endpoint_returns_503_when_fabric_unavailable(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    import app.main as main

    def boom(settings, patient_id, trial_id):
        raise RuntimeError("data agent unavailable")

    monkeypatch.setattr(fe, "evaluate_eligibility_fabric", boom)
    monkeypatch.setattr(
        main.orchestrator,
        "settings",
        Settings(APP_ENVIRONMENT="production"),
    )
    client = TestClient(main.app)
    response = client.post("/api/ask", json={"question": "Is PT-1042 eligible for NCT99004324?"})
    # Sync path mirrors the stream path's controlled error instead of a raw 500.
    assert response.status_code == 503
