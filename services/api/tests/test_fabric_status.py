from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from app import fabric_status as fs
import app.main as main
from app.config import Settings

client = TestClient(main.app)


def _clear() -> None:
    fs.clear_cache()


def _configured_settings() -> Settings:
    return Settings(
        _env_file=None,
        APP_ENVIRONMENT="production",
        AZURE_SUBSCRIPTION_ID="11111111-2222-3333-4444-555555555555",
        FABRIC_CAPACITY_RG="test-capacity-rg",
        FABRIC_CAPACITY_NAME="test-capacity",
        FABRIC_WORKSPACE_ID="22222222-3333-4444-5555-666666666666",
    )


def test_fabric_status_active(monkeypatch) -> None:
    _clear()
    monkeypatch.setattr(main, "settings", _configured_settings())

    class _FakeCredential:
        def get_token(self, _scope):  # noqa: ANN001
            return type("T", (), {"token": "fake-token"})()

    def _fake_get(url, headers=None, timeout=None):  # noqa: ANN001
        return httpx.Response(200, json={"properties": {"state": "Active"}}, request=httpx.Request("GET", url))

    monkeypatch.setattr("azure.identity.DefaultAzureCredential", lambda *a, **k: _FakeCredential())
    monkeypatch.setattr(fs.httpx, "get", _fake_get)

    response = client.get("/api/fabric/status")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "Active"
    assert body["capacityName"]
    assert body["portalUrl"].startswith("https://app.fabric.microsoft.com/")
    _clear()


def test_fabric_status_paused_normalizes_casing(monkeypatch) -> None:
    _clear()
    monkeypatch.setattr(main, "settings", _configured_settings())

    class _FakeCredential:
        def get_token(self, _scope):  # noqa: ANN001
            return type("T", (), {"token": "fake-token"})()

    def _fake_get(url, headers=None, timeout=None):  # noqa: ANN001
        return httpx.Response(200, json={"properties": {"state": "paused"}}, request=httpx.Request("GET", url))

    monkeypatch.setattr("azure.identity.DefaultAzureCredential", lambda *a, **k: _FakeCredential())
    monkeypatch.setattr(fs.httpx, "get", _fake_get)

    body = client.get("/api/fabric/status").json()
    assert body["state"] == "Paused"
    _clear()


def test_fabric_status_unknown_on_failure(monkeypatch) -> None:
    _clear()
    monkeypatch.setattr(main, "settings", _configured_settings())

    def _boom(*_a, **_k):
        raise RuntimeError("no credential")

    monkeypatch.setattr("azure.identity.DefaultAzureCredential", _boom)

    response = client.get("/api/fabric/status")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "Unknown"
    assert body["detail"]
    _clear()


def test_fabric_status_unconfigured_does_not_try_azure(monkeypatch) -> None:
    _clear()
    monkeypatch.setattr(
        "azure.identity.DefaultAzureCredential",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Azure credential should not be constructed")
        ),
    )

    body = client.get("/api/fabric/status").json()

    assert body["state"] == "Unknown"
    assert body["capacityName"] == ""
    assert body["detail"] == "Fabric capacity status is disabled in mock mode."
    _clear()
