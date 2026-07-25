from __future__ import annotations

import asyncio

from app.config import get_settings
from app.sources.base import QueryContext
from app.sources.fabric import LiveFabricIQ, MockFabricIQ, _normalize_date, _parse_crcl_readings


def test_parse_crcl_readings_orders_latest_then_prior() -> None:
    answer = (
        "The latest CrCl (CrCl_CKD-EPI) for patient PT-1042 is 48 mL/min on 6/18/2026. "
        "The prior CrCl was 55 mL/min on 5/20/2026."
    )
    readings = _parse_crcl_readings(answer)
    assert readings[0] == (48.0, "2026-06-18")
    assert readings[1] == (55.0, "2026-05-20")


def test_parse_crcl_handles_iso_and_missing_dates() -> None:
    readings = _parse_crcl_readings("Latest value 48 mL/min on 2026-06-18; another reading of 60 mL/min.")
    assert readings[0] == (48.0, "2026-06-18")
    assert readings[1] == (60.0, None)


def test_normalize_date_variants() -> None:
    assert _normalize_date("6/18/2026") == "2026-06-18"
    assert _normalize_date("2026-06-18") == "2026-06-18"
    assert _normalize_date("June 18, 2026") == "2026-06-18"
    assert _normalize_date("not a date") is None
    assert _normalize_date(None) is None


def test_mock_fabric_query_handles_missing_crcl_and_threshold(monkeypatch) -> None:
    def fake_read_csv(path):  # noqa: ANN001
        name = path.name
        if name == "patient_registry.csv":
            return [{"patient_id": "PT-0001", "ecog_ps": "1"}]
        if name == "labs.csv":
            return []
        if name == "treatment_history.csv":
            return []
        if name == "trials.csv":
            return [{"trial_id": "NCT00000001", "site_id": "SITE-01", "crcl_min": ""}]
        if name == "trial_enrollment.csv":
            return []
        if name == "scheduling_slots.csv":
            return []
        return []

    monkeypatch.setattr("app.sources.fabric._read_csv", fake_read_csv)
    source = MockFabricIQ(get_settings())
    context = QueryContext(question="q", patient_id="PT-0001", trial_id="NCT00000001", registry={})

    result = source.query(context)
    assert result.facts["latest_crcl"] is None
    assert result.facts["prior_crcl"] is None
    assert result.facts["treatments"] == []
    assert "missing" in result.summary.lower()


def test_mock_fabric_query_handles_unknown_patient_and_trial(monkeypatch) -> None:
    def fake_read_csv(path):  # noqa: ANN001
        if path.name in {"patient_registry.csv", "labs.csv", "treatment_history.csv", "trials.csv", "trial_enrollment.csv", "scheduling_slots.csv"}:
            return []
        return []

    monkeypatch.setattr("app.sources.fabric._read_csv", fake_read_csv)
    source = MockFabricIQ(get_settings())
    context = QueryContext(question="q", patient_id="PT-404", trial_id="NCT40400000", registry={})

    result = source.query(context)
    assert result.facts["patient"] is None
    assert result.facts["trial"] is None
    assert result.facts["latest_crcl"] is None


def test_live_fabric_crcl_query_forces_lakehouse_source(monkeypatch) -> None:
    source = LiveFabricIQ(get_settings())
    questions: list[str] = []

    async def fake_call(mcp_url, scope, question):  # noqa: ANN001, ARG001
        questions.append(question)
        return ""

    monkeypatch.setattr("app.sources.fabric._call_fabric_mcp", fake_call)
    monkeypatch.setattr(
        "app.sources.fabric._run_coro_blocking",
        lambda make_coro: asyncio.run(make_coro()),
    )
    context = QueryContext(
        question="q",
        patient_id="PT-1042",
        trial_id="NCT99004324",
        registry={},
    )

    assert source._ask_data_agent(context) == ""

    assert "Use only the Lakehouse datasource" in questions[0]
    assert "not any GraphModel" in questions[0]
