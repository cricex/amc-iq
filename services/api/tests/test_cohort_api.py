from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_eligibility_endpoint_marks_hero_renal_uncertain() -> None:
    response = client.get("/api/cohort/patients/PT-1042/trials/NCT99004324/eligibility")
    assert response.status_code == 200
    results = response.json()
    renal = [r for r in results if r["category"] == "renal"]
    assert renal and any(r["status"] == "uncertain" for r in renal)


def test_eligibility_endpoint_404_on_unknown_ids() -> None:
    assert client.get("/api/cohort/patients/PT-0000/trials/NCT99004324/eligibility").status_code == 404
    assert client.get("/api/cohort/patients/PT-1042/trials/NCT00000000/eligibility").status_code == 404
