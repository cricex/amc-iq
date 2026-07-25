from __future__ import annotations

from app.config import Settings
from app.eligibility import evaluate_eligibility_mock


def test_mock_eligibility_uses_selected_trial_criteria() -> None:
    settings = Settings(_env_file=None, APP_ENVIRONMENT="development")

    matched = evaluate_eligibility_mock(settings, "PT-1043", "NCT99004325")
    unrelated = evaluate_eligibility_mock(settings, "PT-1043", "NCT99004501")

    assert all(result.status == "met" for result in matched)
    assert any(result.status == "not_met" for result in unrelated)
    assert matched != unrelated


def test_mock_eligibility_marks_treatment_naive_hold_uncertain() -> None:
    settings = Settings(_env_file=None, APP_ENVIRONMENT="development")

    results = evaluate_eligibility_mock(settings, "PT-1045", "NCT99004326")

    assert any(
        result.category == "prior_therapy" and result.status == "uncertain"
        for result in results
    )
