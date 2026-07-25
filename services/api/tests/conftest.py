"""Test configuration: force deterministic mock mode so the suite never depends on a developer's
local .env or reaches the live Azure IQ services. Live adapters are exercised manually / in
integration checks, not in unit tests.

Env vars are set at import time (before `app.main`/`app.config` are imported during collection) and
the cached settings are cleared, so `USE_LIVE_*` flags from a repo-local .env cannot leak in.
Environment variables take precedence over .env values in pydantic-settings.

Eligibility uses the same narrowly gated deterministic mock path as the public local walkthrough.
Tests that specifically exercise the Fabric-native path opt into a production/live setting and mock
that external boundary directly.
"""
from __future__ import annotations

import os

import pytest

os.environ["APP_ENVIRONMENT"] = "test"
for _flag in (
    "USE_LIVE_FOUNDRY",
    "USE_LIVE_FABRIC",
    "USE_LIVE_WORK",
    "USE_LIVE_WEB",
    "USE_LIVE_AGENT",
    "USE_LIVE_SPECIALISTS",
):
    os.environ[_flag] = "false"

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_eligibility_cache():
    """Keep deterministic eligibility results isolated between tests."""
    from app import eligibility as elig

    elig.clear_cache()
    yield
    elig.clear_cache()


@pytest.fixture(autouse=True)
def _authenticated_api_requests():
    """Existing endpoint tests exercise application behavior, not Entra. Override only the FastAPI
    authentication boundary; dedicated auth tests validate real JWT handling and missing headers."""
    from app.auth import AuthenticatedUser, require_api_user
    from app.main import app

    app.dependency_overrides[require_api_user] = lambda: AuthenticatedUser(
        access_token="test-user-token",
        tenant_id="test-tenant",
        subject="test-user",
        scopes=frozenset({"access_as_user"}),
        claims={},
    )
    yield
    app.dependency_overrides.pop(require_api_user, None)
