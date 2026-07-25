from __future__ import annotations

from pathlib import Path

import pytest

from live_environment import required_environment

LIVE_DIR = Path(__file__).resolve().parent


def test_required_environment_fails_before_cloud_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPERATOR_RESOURCE_ID", raising=False)

    with pytest.raises(
        SystemExit,
        match="Required environment variable OPERATOR_RESOURCE_ID is not set",
    ):
        required_environment("OPERATOR_RESOURCE_ID")


def test_required_environment_strips_operator_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPERATOR_RESOURCE_ID", "  test-resource  ")

    assert required_environment("OPERATOR_RESOURCE_ID") == "test-resource"


def test_teardown_requires_explicit_foundry_account_deletion_switch() -> None:
    script = (LIVE_DIR / "teardown.ps1").read_text(encoding="utf-8")

    guard = script.index("if ($DeleteFoundryAccount)")
    account_delete = script.index("az cognitiveservices account delete")
    assert guard < account_delete


def test_web_deploy_reads_azd_only_when_auth_arguments_are_missing() -> None:
    script = (LIVE_DIR / "deploy_web.ps1").read_text(encoding="utf-8")

    guard = script.index("if (-not $TenantId -or -not $WebClientId -or -not $ApiScope)")
    azd_read = script.index("azd env get-values")
    assert guard < azd_read
