from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings, SettingsConfigurationError

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_anonymous_mock_requires_explicit_local_environment_and_no_live_components() -> None:
    local = Settings(_env_file=None, APP_ENVIRONMENT="development")
    production = Settings(_env_file=None, APP_ENVIRONMENT="production")
    live_local = Settings(
        _env_file=None,
        APP_ENVIRONMENT="development",
        USE_LIVE_FOUNDRY=True,
    )

    assert local.anonymous_mock_enabled is True
    assert local.mode == "mock"
    assert production.anonymous_mock_enabled is False
    assert production.mode == "live"
    assert live_local.anonymous_mock_enabled is False
    assert live_local.mode == "live"


def test_live_configuration_rejects_public_placeholders() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENVIRONMENT="production",
        AZURE_TENANT_ID="your-tenant-id",
        API_AUDIENCE="api://your-api-client-id",
        FABRIC_WORKSPACE_ID="your-fabric-workspace-id",
        FABRIC_DATA_AGENT_ID="your-fabric-data-agent-id",
        PROJECT_ENDPOINT="https://your-foundry-project.example.invalid",
    )

    with pytest.raises(
        SettingsConfigurationError,
        match="AZURE_TENANT_ID.*FABRIC_WORKSPACE_ID.*PROJECT_ENDPOINT",
    ):
        settings.validate_runtime_configuration()


def test_production_live_work_requires_key_vault_certificate_source() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENVIRONMENT="production",
        USE_LIVE_WORK=True,
        AZURE_TENANT_ID="00000000-0000-0000-0000-000000000001",
        API_AUDIENCE="api://00000000-0000-0000-0000-000000000002",
        FABRIC_WORKSPACE_ID="00000000-0000-0000-0000-000000000003",
        FABRIC_DATA_AGENT_ID="00000000-0000-0000-0000-000000000004",
        PROJECT_ENDPOINT="https://example.services.ai.azure.com/api/projects/example",
        ELIGIBILITY_EVALUATOR_AGENT="eligibility-evaluator",
        WORK_IQ_CLIENT_ID="00000000-0000-0000-0000-000000000005",
        WORK_IQ_ENDPOINT="https://example.invalid",
        WORK_IQ_SCOPE="api://example/.default",
    )

    with pytest.raises(
        SettingsConfigurationError,
        match=(
            "AZURE_CLIENT_ID.*WORK_IQ_CLIENT_CERTIFICATE_SECRET_NAME"
            ".*WORK_IQ_KEY_VAULT_URL"
        ),
    ):
        settings.validate_runtime_configuration()


def test_local_live_work_requires_environment_certificate_source() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENVIRONMENT="development",
        USE_LIVE_WORK=True,
        AZURE_TENANT_ID="00000000-0000-0000-0000-000000000001",
        API_AUDIENCE="api://00000000-0000-0000-0000-000000000002",
        FABRIC_WORKSPACE_ID="00000000-0000-0000-0000-000000000003",
        FABRIC_DATA_AGENT_ID="00000000-0000-0000-0000-000000000004",
        PROJECT_ENDPOINT="https://example.services.ai.azure.com/api/projects/example",
        ELIGIBILITY_EVALUATOR_AGENT="eligibility-evaluator",
        WORK_IQ_CLIENT_ID="00000000-0000-0000-0000-000000000005",
        WORK_IQ_ENDPOINT="https://example.invalid",
        WORK_IQ_SCOPE="api://example/.default",
    )

    with pytest.raises(
        SettingsConfigurationError,
        match="WORK_IQ_CLIENT_CERTIFICATE.*WORK_IQ_CLIENT_CERTIFICATE_THUMBPRINT",
    ):
        settings.validate_runtime_configuration()


def test_dev_script_overrides_every_live_flag_and_frontend_auth() -> None:
    script = (REPO_ROOT / "scripts" / "dev.ps1").read_text(encoding="utf-8")
    for name in (
        "USE_LIVE_FOUNDRY",
        "USE_LIVE_FABRIC",
        "USE_LIVE_WORK",
        "USE_LIVE_WEB",
        "USE_LIVE_AGENT",
        "USE_LIVE_SPECIALISTS",
    ):
        assert f'{name} = "false"' in script
    assert 'APP_ENVIRONMENT = "development"' in script
    assert 'CORS_ORIGINS = "http://localhost:$WebPort"' in script
    assert 'DATA_DIR = (Join-Path $root "data")' in script
    assert 'VITE_ENTRA_TENANT_ID = ""' in script
    assert 'VITE_ENTRA_CLIENT_ID = ""' in script
    assert 'VITE_API_SCOPE = ""' in script


def test_live_workflow_requires_operator_endpoint_and_defaults_probe_off() -> None:
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "live-integration.yml"
    ).read_text(encoding="utf-8")

    assert "api_base_url:" in workflow
    assert "API_BASE_URL: ${{ inputs.api_base_url }}" in workflow
    assert "run_fabric_probe:" in workflow
    assert "default: false" in workflow
    assert ".azurecontainerapps.io" not in workflow
