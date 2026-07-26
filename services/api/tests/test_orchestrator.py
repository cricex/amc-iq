from __future__ import annotations

import csv
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.orchestrator import Orchestrator
from app.schemas import AskRequest, Source

QUESTION = "Is PT-1042 eligible for NCT99004324 and what should the care team do next?"
FABRIC_DIR = get_settings().DATA_DIR / "fabric"
ARCHETYPES = ("clear_eligible", "borderline", "hard_excluded", "biomarker_mismatch", "treatment_naive_hold")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _archetype_patient_trial_map() -> dict[str, tuple[str, str]]:
    patients = _read_csv(FABRIC_DIR / "patient_registry.csv")
    enrollment = {row["patient_id"]: row["trial_id"] for row in _read_csv(FABRIC_DIR / "trial_enrollment.csv")}
    selected: dict[str, tuple[str, str]] = {}
    for row in patients:
        archetype = row["archetype"]
        if archetype not in ARCHETYPES or archetype in selected:
            continue
        trial_id = enrollment.get(row["patient_id"])
        if trial_id:
            selected[archetype] = (row["patient_id"], trial_id)
    return selected


ARCHETYPE_PATIENT_TRIAL = _archetype_patient_trial_map()


def test_hero_question_returns_expected_eligibility() -> None:
    result = Orchestrator().answer(AskRequest(question=QUESTION))

    assert result.eligibility.assessment == "likely_eligible_pending"
    crcl = next(item for item in result.criteria if "clearance" in item.text.lower())
    assert crcl.status == "uncertain"
    assert result.nextAction.owner.id == "COORD-01"
    assert {item.source for item in result.sourceMap} == {Source.FOUNDRY, Source.FABRIC, Source.WORK, Source.WEB}

    evidence_refs = {item.refId for item in result.evidence}
    referenced = set(result.answerRefs)
    for criterion in result.criteria:
        referenced.update(criterion.evidenceRefs)
    assert referenced <= evidence_refs


def test_non_streaming_endpoint_returns_ask_result() -> None:
    client = TestClient(app)
    response = client.post("/api/ask", json={"question": QUESTION})

    assert response.status_code == 200
    payload = response.json()
    assert payload["eligibility"]["assessment"] == "likely_eligible_pending"
    assert payload["nextAction"]["owner"]["id"] == "COORD-01"
    assert {item["source"] for item in payload["sourceMap"]} == {"foundry", "fabric", "work", "web"}


def test_result_has_enriched_trial_reviewers_and_task() -> None:
    result = Orchestrator().answer(AskRequest(question=QUESTION))
    assert result.trial.humanName  # e.g. "EGFR Exon 20 NSCLC Trial"
    assert result.trial.phase == "Phase 2"
    assert result.trial.keyIssue
    assert result.nextAction.taskStatus == "Drafted (not submitted)"
    assert result.nextAction.due
    roles = {reviewer.role: reviewer.status for reviewer in result.reviewers}
    assert roles.get("Trial Coordinator") == "assigned"
    assert roles.get("PI Sign-Off") == "pending"
    assert all(item.retrieving for item in result.sourceMap)


def test_source_failure_degrades_gracefully() -> None:
    from app.schemas import Source
    from app.sources import QueryContext, SourceResult, create_sources

    class FailingWeb:
        name = Source.WEB
        label = "Web IQ"

        def query(self, context: QueryContext) -> SourceResult:  # noqa: ARG002
            raise RuntimeError("simulated Web IQ outage")

    base = create_sources(Orchestrator().settings)
    sources = [s for s in base if s.name != Source.WEB] + [FailingWeb()]
    result = Orchestrator(sources=sources).answer(AskRequest(question=QUESTION))

    # Assessment still composes from the other layers, and the failure is surfaced.
    assert result.eligibility.assessment == "likely_eligible_pending"
    assert "Web IQ" in result.unavailableSources
    web_entry = next(item for item in result.sourceMap if item.source == Source.WEB)
    assert web_entry.status == "failed"
    client = TestClient(app)
    response = client.post(
        "/api/ask",
        json={
            "question": "Is this case eligible?",
            "patientId": "PT-9999",
            "trialId": "NCT99004324",
        },
    )
    assert response.status_code == 404


def test_distractor_patient_returns_valid_envelope() -> None:
    # Biomarker-mismatched patient (ALK) against the EGFR hero trial: must still return a
    # well-formed envelope with all four sources and internally-consistent citations.
    result = Orchestrator().answer(
        AskRequest(question="Is PT-1045 eligible for NCT99004324?", patientId="PT-1045")
    )
    assert result.patient.id == "PT-1045"
    assert len(result.criteria) >= 4
    assert {item.source for item in result.sourceMap} == {Source.FOUNDRY, Source.FABRIC, Source.WORK, Source.WEB}
    evidence_refs = {item.refId for item in result.evidence}
    referenced = set(result.answerRefs)
    for criterion in result.criteria:
        referenced.update(criterion.evidenceRefs)
    assert referenced <= evidence_refs
    assert "Alex Morgan" not in result.answer


@pytest.mark.parametrize(
    ("archetype", "expected"),
    [
        ("clear_eligible", {"eligible", "likely_eligible_pending"}),
        ("borderline", {"likely_eligible_pending"}),
        ("hard_excluded", {"not_eligible"}),
        ("biomarker_mismatch", {"not_eligible"}),
        ("treatment_naive_hold", {"eligible", "likely_eligible_pending"}),
    ],
)
def test_archetype_assessment_expectations(archetype: str, expected: set[str]) -> None:
    patient_id, trial_id = ARCHETYPE_PATIENT_TRIAL[archetype]
    question = f"Is {patient_id} eligible for {trial_id} and what should happen next?"
    result = Orchestrator().answer(AskRequest(question=question, patientId=patient_id))

    assert result.patient.id == patient_id
    assert result.trial.id == trial_id
    assert result.eligibility.assessment in expected
    assert len(result.criteria) >= 4
    if patient_id != "PT-1042":
        assert "Alex Morgan" not in result.answer


def test_stream_emits_plan_and_final_events() -> None:
    client = TestClient(app)
    with client.stream("POST", "/api/ask/stream", json={"question": QUESTION}) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    assert "event: plan" in body
    assert "event: final" in body
    assert "event: source_result" in body


def test_eligibility_starts_after_fabric_and_overlaps_slower_work_iq() -> None:
    from app.eligibility import CriterionResult
    from app.sources import QueryContext, SourceResult

    class SourceStub:
        def __init__(self, name: Source, label: str) -> None:
            self.name = name
            self.label = label

        def query(self, context: QueryContext) -> SourceResult:  # pragma: no cover
            raise AssertionError("The timed orchestrator overrides _run_source")

    timestamps: dict[str, float] = {}

    class TimedOrchestrator(Orchestrator):
        def _run_source(self, source: SourceStub, context: QueryContext) -> SourceResult:  # noqa: ARG002
            if source.name == Source.FABRIC:
                time.sleep(0.03)
                timestamps["fabric_done"] = time.perf_counter()
            else:
                time.sleep(0.2)
                timestamps["work_done"] = time.perf_counter()
            return SourceResult(
                source=source.name,
                label=source.label,
                queries=[],
                summary="done",
                citations=[],
            )

        def _eligibility_results(self, context: QueryContext) -> list[CriterionResult]:  # noqa: ARG002
            timestamps["eligibility_start"] = time.perf_counter()
            time.sleep(0.03)
            timestamps["eligibility_done"] = time.perf_counter()
            return [
                CriterionResult(
                    criterion="criterion-1",
                    kind="inclusion",
                    category="diagnosis",
                    description="Diagnosis matches.",
                    status="met",
                    evidence="Fabric",
                )
            ]

    orchestrator = TimedOrchestrator(
        sources=[
            SourceStub(Source.FABRIC, "Fabric IQ"),
            SourceStub(Source.WORK, "Work IQ"),
        ]
    )
    context = orchestrator._context_for(AskRequest(question=QUESTION))

    results, criterion_results = orchestrator._query_all_and_eligibility(context)

    assert [result.source for result in results] == [Source.FABRIC, Source.WORK]
    assert criterion_results[0].status == "met"
    assert timestamps["eligibility_start"] >= timestamps["fabric_done"]
    assert timestamps["eligibility_done"] < timestamps["work_done"]


def test_stream_works_for_non_hero_patient_trial() -> None:
    patient_id, trial_id = ARCHETYPE_PATIENT_TRIAL["biomarker_mismatch"]
    question = f"Is {patient_id} eligible for {trial_id}?"
    client = TestClient(app)
    with client.stream("POST", "/api/ask/stream", json={"question": question, "patientId": patient_id}) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    assert "event: final" in body
    assert trial_id in body


def test_evidence_doc_serves_whitelisted_source() -> None:
    client = TestClient(app)
    response = client.get("/api/evidence/doc", params={"path": "foundry_docs/protocol_NCT99004324.md"})
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "AMC IQ synthetic source document" in response.text


def test_evidence_doc_rejects_traversal_and_unknown_dir() -> None:
    client = TestClient(app)
    assert client.get("/api/evidence/doc", params={"path": "foundry_docs/../../secrets.md"}).status_code == 404
    assert client.get("/api/evidence/doc", params={"path": "registry/registry.yaml"}).status_code == 404
    assert client.get("/api/evidence/doc", params={"path": "foundry_docs/missing.md"}).status_code == 404


def test_citation_urls_are_openable_doc_links() -> None:
    client = TestClient(app)
    payload = client.post("/api/ask", json={"question": QUESTION}).json()
    by_ref = {item["refId"]: item for item in payload["evidence"]}
    # Foundry (r1/r2) and Work (r4) citations resolve through the local document endpoint.
    for ref in ("r1", "r2", "r4"):
        url = by_ref[ref]["url"]
        assert url.startswith("/api/evidence/doc?path=")
        assert client.get(url).status_code == 200


def test_activity_counts_equal_citation_counts() -> None:
    result = Orchestrator().answer(AskRequest(question=QUESTION))
    for item in result.sourceMap:
        assert item.evidenceCount == len(item.citations)
        assert item.evidenceNoun == "citations"


def test_agent_run_overrides_answer_and_trace() -> None:
    from app.agent_client import AgentRun, AgentToolCall
    from app.schemas import Source

    orchestrator = Orchestrator()
    context = orchestrator._context_for(AskRequest(question=QUESTION))
    results = orchestrator._query_all(context)
    agent_run = AgentRun(
        answer="Agent-specific narrative answer about missing data.",
        tool_calls=[
            AgentToolCall(source=Source.FOUNDRY, server_label="amciq_foundry_kb", tool_name="knowledge_base_retrieve", query="renal threshold?", status="completed"),
            AgentToolCall(source=Source.FABRIC, server_label="amciq_fabric_dataagent", tool_name="DataAgent", query="latest CrCl?", status="completed"),
        ],
    )
    result = orchestrator._assemble(QUESTION, context, results, agent_run)
    assert result.agentDriven is True
    assert result.answer == "Agent-specific narrative answer about missing data."
    steps = [step.step for step in result.trace]
    assert steps[0].startswith("Planned retrieval")
    assert any("Queried Foundry IQ" in step for step in steps)
    assert any("Queried Fabric IQ" in step for step in steps)
    assert steps[-1] == "Composed governed assessment"
    # Grounded eligibility facts remain stable regardless of the agent narrative.
    assert result.eligibility.assessment == "likely_eligible_pending"


def test_classify_intent_maps_each_library_question() -> None:
    from app.orchestrator import classify_intent

    cases = {
        "Is Alex Morgan eligible for the EGFR exon 20 NSCLC trial (NCT99004324), and what needs review before screening?": "eligibility",
        "What is preventing PT-1042 from moving to formal trial screening for NCT99004324?": "screening",
        "Prepare an evidence packet for PI review of PT-1042 and NCT99004324.": "evidence",
        "For PT-1042 and NCT99004324, who should own the next step, and what action should be drafted for human review?": "workflow",
        "Does the prior platinum therapy history for PT-1042 conflict with the NCT99004324 exclusion criteria?": "protocol",
        "What patient data is missing or stale for PT-1042 before the case can advance?": "data_gaps",
        "What external trial registry or treatment landscape context is relevant to the EGFR exon 20 biomarker?": "external_context",
    }
    for question, expected in cases.items():
        assert classify_intent(question) == expected, question


def test_classify_intent_explicit_eligibility_beats_partial_templates() -> None:
    # M1/M2 robustness: explicit eligibility phrasing must not be downgraded to a partial view, and
    # bare tokens like "web"/"outside" must not force an external-context (no-eligibility) banner.
    from app.orchestrator import classify_intent

    assert classify_intent("Is PT-1042 eligible, and what data is missing?") == "eligibility"
    assert classify_intent("Is the patient eligible given the prior therapy exclusion?") == "eligibility"
    assert classify_intent("Does PT-1042 qualify for the protocol?") == "eligibility"
    # Substring guards: ineligible/disqualify must NOT force the eligibility layout (Anvil R2 M1).
    assert classify_intent("Who should own the next step for ineligible patients?") == "workflow"
    assert classify_intent("What data is missing to disqualify this patient?") == "data_gaps"


def test_bottom_line_not_eligible_overrides_intent() -> None:
    from app.orchestrator import _bottom_line

    # A hard-stop assessment must never advise advancing, even for action-oriented intents (Anvil R2 M3).
    for intent in ("workflow", "data_gaps", "evidence", "protocol", "eligibility"):
        text = _bottom_line(intent, "not_eligible").lower()
        assert "does not currently meet" in text
    # The pending copy no longer hardcodes "renal" so it fits any gap.
    assert "renal" not in _bottom_line("eligibility", "likely_eligible_pending").lower()


def test_bottom_line_is_intent_aware() -> None:
    from app.orchestrator import _bottom_line

    workflow = _bottom_line("workflow", "likely_eligible_pending")
    eligibility = _bottom_line("eligibility", "likely_eligible_pending")
    assert workflow != eligibility
    assert "next action" in workflow.lower()
    assert "resolve" in _bottom_line("data_gaps", "likely_eligible_pending").lower()


def test_unknown_or_missing_subject_raises_instead_of_defaulting() -> None:
    import pytest

    from app.schemas import AskRequest

    orch = Orchestrator()
    # A named-but-unknown NCT must not silently produce an answer for another trial.
    with pytest.raises(ValueError):
        orch.answer(AskRequest(question="Is PT-1042 eligible for NCT99999999?"))
    # A named-but-unknown patient must not silently produce an answer for another patient.
    with pytest.raises(ValueError):
        orch.answer(AskRequest(question="Is PT-9999 eligible for NCT99004324?"))
    # No patient/trial named and no explicit case context must still raise.
    with pytest.raises(ValueError):
        orch.answer(AskRequest(question="Is this patient eligible and what needs review?"))


def test_free_form_question_uses_explicit_case_context() -> None:
    result = Orchestrator().answer(
        AskRequest(
            question="Who owns the next step?",
            patientId="PT-1042",
            trialId="NCT99004324",
        )
    )

    assert result.patient.id == "PT-1042"
    assert result.trial.id == "NCT99004324"
    assert result.intent == "workflow"
    assert "Dana Whitfield" in result.answer


def test_subject_named_in_question_overrides_current_case_context() -> None:
    result = Orchestrator().answer(
        AskRequest(
            question="Is Marlowe Price eligible for NCT99004324?",
            patientId="PT-1042",
            trialId="NCT99004324",
        )
    )

    assert result.patient.id == "PT-1061"


def test_question_library_intents_return_distinct_evidence_packets() -> None:
    questions = [
        "Is Alex Morgan eligible for the EGFR exon 20 NSCLC trial (NCT99004324), and what needs review before screening?",
        "What is preventing PT-1042 from moving to formal trial screening for NCT99004324?",
        "Prepare an evidence packet for PI review of PT-1042 and NCT99004324 with patient facts, protocol criteria, and unresolved issues.",
        "For PT-1042 and NCT99004324, who should own the next step, and what action should be drafted for human review?",
        "Does the prior platinum therapy history for PT-1042 conflict with the NCT99004324 exclusion criteria?",
        "What patient data is missing or stale for PT-1042 before the NCT99004324 case can advance?",
        "For Alex Morgan and NCT99004324, what external trial registry or treatment landscape context is relevant to the EGFR exon 20 biomarker?",
    ]
    orchestrator = Orchestrator()
    signatures = []
    for question in questions:
        result = orchestrator.answer(AskRequest(question=question))
        signatures.append(tuple(item.refId for item in result.evidence))
        selected = {item.refId for item in result.evidence}
        assert set(result.answerRefs) == selected
        for source in result.sourceMap:
            assert set(source.citations) <= selected
            assert source.evidenceCount == len(source.citations)

    assert len(set(signatures)) == len(questions)


def test_screening_packet_keeps_evidence_for_the_actual_blocking_category() -> None:
    from app.eligibility import CriterionResult

    selected = Orchestrator._evidence_refs_for_intent(
        "screening",
        [
            CriterionResult(
                criterion="NCT-TEST-BIO",
                description="Required biomarker documented as present",
                category="biomarker",
                kind="inclusion",
                status="not_met",
                evidence="Biomarker mismatch.",
            )
        ],
    )

    assert {"r1", "r3", "r7"} <= selected


def test_mock_fallbacks_do_not_attach_hero_citations_to_other_cases() -> None:
    from app.sources.base import QueryContext
    from app.sources.web import MockWebIQ
    from app.sources.work import MockWorkIQ

    orchestrator = Orchestrator()
    context = QueryContext(
        question="What external context and workflow evidence is available?",
        patient_id="PT-1049",
        trial_id="NCT99004501",
        registry=orchestrator.registry,
    )

    web = MockWebIQ(orchestrator.settings).query(context)
    work = MockWorkIQ(orchestrator.settings).query(context)

    assert web.citations == []
    assert web.facts["registry_status"] is None
    assert work.citations == []
    assert "Alex Morgan" not in work.summary


def test_mock_web_fixture_is_explicitly_synthetic() -> None:
    from app.sources.base import QueryContext
    from app.sources.web import MockWebIQ

    orchestrator = Orchestrator()
    context = QueryContext(
        question="What registry context is available?",
        patient_id="PT-1042",
        trial_id="NCT99004324",
        registry=orchestrator.registry,
    )

    result = MockWebIQ(orchestrator.settings).query(context)

    assert result.facts["registry_synthetic"] is True
    assert result.summary.startswith("Synthetic ClinicalTrials.gov-style fixture")
    assert result.citations[0].snippet.startswith("Synthetic fixture")


def test_patient_resolved_by_name_not_defaulted() -> None:
    from app.schemas import AskRequest

    # Marlowe Price is PT-1061; the answer must be about PT-1061, never a default patient.
    result = Orchestrator().answer(
        AskRequest(question="Is Marlowe Price eligible for NCT99004324, and what needs review?")
    )
    assert result.patient.id == "PT-1061"
    assert result.patient.display == "Marlowe Price"
    assert "PT-1042" not in result.answer


def test_result_exposes_intent_scope_and_bottom_line() -> None:
    eligibility = Orchestrator().answer(AskRequest(question=QUESTION))
    assert eligibility.intent == "eligibility"
    assert eligibility.scope == ""
    assert eligibility.bottomLine

    external = Orchestrator().answer(
        AskRequest(question="For PT-1042 and NCT99004324, what external registry context is relevant to this biomarker?")
    )
    assert external.intent == "external_context"
    assert external.scope  # external questions carry the guardrail note
    assert "external" in external.bottomLine.lower()
