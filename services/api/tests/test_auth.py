from __future__ import annotations

import time
from dataclasses import dataclass

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.auth import (
    AccessTokenValidator,
    ApiAuthError,
    get_access_token_validator,
    require_api_user,
)
from app.config import Settings, get_settings
from app.main import app

TENANT_ID = "11111111-1111-1111-1111-111111111111"
AUDIENCE = "api://22222222-2222-2222-2222-222222222222"
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class _SigningKey:
    key: object


class _StaticJwksClient:
    def get_signing_key_from_jwt(self, token: str) -> _SigningKey:  # noqa: ARG002
        return _SigningKey(PRIVATE_KEY.public_key())


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        AZURE_TENANT_ID=TENANT_ID,
        API_AUDIENCE=AUDIENCE,
        API_REQUIRED_SCOPE="access_as_user",
    )


def _token(**overrides: object) -> str:
    now = int(time.time())
    claims = {
        "iss": f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
        "aud": AUDIENCE,
        "tid": TENANT_ID,
        "sub": "user-123",
        "scp": "access_as_user other_scope",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return jwt.encode(claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": "test-key"})


def _validator() -> AccessTokenValidator:
    return AccessTokenValidator(_settings(), jwks_client=_StaticJwksClient())  # type: ignore[arg-type]


def test_validates_tenant_audience_and_scope() -> None:
    principal = _validator().validate(_token())
    assert principal.tenant_id == TENANT_ID
    assert principal.subject == "user-123"
    assert "access_as_user" in principal.scopes


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"aud": "api://wrong"}, "audience"),
        ({"tid": "33333333-3333-3333-3333-333333333333"}, "tenant"),
        ({"scp": "other_scope"}, "required scope"),
    ],
)
def test_rejects_invalid_required_claims(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ApiAuthError, match=message):
        _validator().validate(_token(**overrides))


def test_anonymous_mock_routes_succeed_but_public_routes_remain_public() -> None:
    saved = app.dependency_overrides.pop(require_api_user)
    try:
        client = TestClient(app)
        assert (
            client.post(
                "/api/ask",
                json={"question": "Is PT-1042 eligible for NCT99004324?"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/ask/stream",
                json={"question": "Is PT-1042 eligible for NCT99004324?"},
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/api/cohort/patients/PT-1042/trials/NCT99004324/eligibility"
            ).status_code
            == 200
        )
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/fabric/status").status_code == 200
        assert (
            client.get(
                "/api/evidence/doc",
                params={"path": "foundry_docs/protocol_NCT99004324.md"},
            ).status_code
            == 200
        )
    finally:
        app.dependency_overrides[require_api_user] = saved


def _configure_non_mock_environment(
    monkeypatch: pytest.MonkeyPatch, *, production: bool, live_foundry: bool
) -> None:
    values = {
        "APP_ENVIRONMENT": "production" if production else "development",
        "USE_LIVE_FOUNDRY": str(live_foundry).lower(),
        "USE_LIVE_FABRIC": "false",
        "USE_LIVE_WORK": "false",
        "USE_LIVE_WEB": "false",
        "USE_LIVE_AGENT": "false",
        "USE_LIVE_SPECIALISTS": "false",
        "AZURE_TENANT_ID": TENANT_ID,
        "API_AUDIENCE": AUDIENCE,
        "API_REQUIRED_SCOPE": "access_as_user",
        "FABRIC_WORKSPACE_ID": "11111111-2222-3333-4444-555555555555",
        "FABRIC_DATA_AGENT_ID": "22222222-3333-4444-5555-666666666666",
        "PROJECT_ENDPOINT": "https://operator-foundry.example.com/api/projects/poc",
        "ELIGIBILITY_EVALUATOR_AGENT": "eligibility-evaluator",
        "SEARCH_ENDPOINT": "https://operator-search.example.com",
        "FOUNDRY_KB_NAME": "operator-kb",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    get_access_token_validator.cache_clear()


@pytest.mark.parametrize(
    ("production", "live_foundry"),
    [
        (True, False),
        (False, True),
    ],
)
def test_production_or_live_configuration_requires_bearer_authentication(
    monkeypatch: pytest.MonkeyPatch,
    production: bool,
    live_foundry: bool,
) -> None:
    saved = app.dependency_overrides.pop(require_api_user)
    try:
        _configure_non_mock_environment(
            monkeypatch,
            production=production,
            live_foundry=live_foundry,
        )
        client = TestClient(app)
        response = client.post(
            "/api/ask",
            json={"question": "Is PT-1042 eligible for NCT99004324?"},
        )
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
    finally:
        app.dependency_overrides[require_api_user] = saved
        get_settings.cache_clear()
        get_access_token_validator.cache_clear()


def test_mock_mode_rejects_a_non_bearer_authorization_header() -> None:
    saved = app.dependency_overrides.pop(require_api_user)
    try:
        client = TestClient(app)
        response = client.post(
            "/api/ask",
            headers={"Authorization": "Basic not-a-bearer-token"},
            json={"question": "Is PT-1042 eligible for NCT99004324?"},
        )
        assert response.status_code == 401
    finally:
        app.dependency_overrides[require_api_user] = saved
