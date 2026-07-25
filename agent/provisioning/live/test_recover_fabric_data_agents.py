from __future__ import annotations

import asyncio
import base64
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import requests

MODULE_NAME = "recover_fabric_data_agents"
LIVE_DIR = Path(__file__).resolve().parent
POWERSHELL_SCRIPT = LIVE_DIR / "provision_fabric_admin.ps1"


def _reload_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("FABRIC_TOKEN", raising=False)
    monkeypatch.setenv("FABRIC_WORKSPACE_ID", "test-workspace")
    monkeypatch.setenv("FABRIC_ELIGIBILITY_AGENT_ID", "eligibility-agent-id")
    monkeypatch.setenv("FABRIC_LAKEHOUSE_ID", "test-lakehouse")
    monkeypatch.setenv("FABRIC_GRAPH_MODEL_ID", "test-graph-model")
    monkeypatch.setenv("FABRIC_GRAPH_AGENT_NAME", "test-graph-agent")
    monkeypatch.delitem(sys.modules, MODULE_NAME, raising=False)
    return importlib.import_module(MODULE_NAME)


class FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload or {}
        self.status_code = status_code
        self.headers = headers or {}
        self.content = json.dumps(self.payload).encode() if payload is not None else b""

    def json(self) -> dict[str, Any]:
        return self.payload


class QueueSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            pytest.fail(f"Unexpected request: {method} {url}")
        return self.responses.pop(0)


class StatefulClient:
    def __init__(self, recovery) -> None:
        self.recovery = recovery
        self.operation_timeout = 30
        self.sleep_fn = lambda _seconds: None
        self.sources: dict[str, list[dict[str, Any]]] = {}
        self.agents: list[dict[str, Any]] = []
        self.element_pages: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
        self.fewshots: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[dict[str, Any]] = []

    def _agent_id(self, path: str) -> str:
        return path.split("/dataAgents/")[1].split("/")[0]

    def list_values(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        self.calls.append({"method": "GET", "path": path, "params": params})
        if path.endswith("/dataAgents"):
            return [dict(item) for item in self.agents]
        if path.endswith("/staging/datasources"):
            return [dict(item) for item in self.sources.get(self._agent_id(path), [])]
        if path.endswith("/elements"):
            agent_id = self._agent_id(path)
            root = None if params is None else params.get("rootId")
            return [
                dict(item)
                for item in self.element_pages.get((agent_id, root), [])
            ]
        if path.endswith("/fewshots"):
            return [dict(item) for item in self.fewshots.get(path, [])]
        pytest.fail(f"Unexpected list path: {path}")

    def request_json(
        self,
        method: str,
        path: str,
        *,
        expected=(200,),
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        wait_lro: bool = False,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "expected": expected,
                "params": params,
                "json": json_body,
                "wait_lro": wait_lro,
            }
        )
        if path.endswith("/dataAgents") and method == "POST":
            created = {
                "id": "graph-agent-id",
                "displayName": self.recovery.GRAPH_AGENT_NAME,
            }
            self.agents.append(created)
            return dict(created)
        if path.endswith("/staging/datasources") and method == "POST":
            agent_id = self._agent_id(path)
            reference_key = (
                "lakehouseReference"
                if json_body["type"] == "LakehouseTables"
                else "itemReference"
            )
            created = {
                "id": f"{agent_id}-source",
                "type": json_body["type"],
                reference_key: dict(json_body[reference_key]),
            }
            self.sources.setdefault(agent_id, []).append(created)
            return dict(created)
        if path.endswith("/elements") and method == "PATCH":
            agent_id = self._agent_id(path)
            table_id = params["id"]
            for key, items in self.element_pages.items():
                if key[0] == agent_id:
                    for item in items:
                        if item.get("id") == table_id:
                            item["isSelected"] = json_body["isSelected"]
            return {}
        if path.endswith("/fewshots") and method == "POST":
            fewshot = {
                "id": f"fewshot-{len(self.fewshots.setdefault(path, [])) + 1}",
                **json_body,
                "validationStatus": {"value": "Valid"},
            }
            self.fewshots[path].append(fewshot)
            return dict(fewshot)
        if "/fewshots/" in path and method == "DELETE":
            parent, item_id = path.rsplit("/", 1)
            self.fewshots[parent] = [
                item for item in self.fewshots.get(parent, []) if item["id"] != item_id
            ]
            return {}
        if "/fewshots/" in path and method == "PATCH":
            parent, item_id = path.rsplit("/", 1)
            for item in self.fewshots.get(parent, []):
                if item["id"] == item_id:
                    item.update(json_body)
                    item["validationStatus"] = {"value": "Valid"}
            return {}
        if "/staging/datasources/" in path and method == "DELETE":
            agent_id = self._agent_id(path)
            source_id = path.rsplit("/", 1)[-1]
            self.sources[agent_id] = [
                item
                for item in self.sources.get(agent_id, [])
                if item["id"] != source_id
            ]
            return {}
        return {}


def _lakehouse_tree(client: StatefulClient, recovery, *, selected: bool = False) -> None:
    agent = recovery.ELIGIBILITY_AGENT_ID
    client.element_pages[(agent, None)] = [
        {
            "id": "schemas",
            "displayName": "Schemas",
            "type": "Schemas",
            "hasSubElements": True,
        }
    ]
    client.element_pages[(agent, "schemas")] = [
        {
            "id": "dbo",
            "displayName": "dbo",
            "type": "Schema",
            "hasSubElements": True,
        }
    ]
    client.element_pages[(agent, "dbo")] = [
        {
            "id": "tables",
            "displayName": "Tables",
            "type": "Tables",
            "hasSubElements": True,
        }
    ]
    client.element_pages[(agent, "tables")] = [
        {
            "id": f"{table_name}-table",
            "displayName": table_name,
            "type": "Table",
            "state": "Available",
            "hasSubElements": True,
            "isSelected": selected,
        }
        for table_name in sorted(recovery.REQUIRED_ELIGIBILITY_TABLES)
    ]


def test_import_does_not_require_fabric_token_or_msal(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "msal", None)
    recovery = _reload_module(monkeypatch)
    assert recovery.ELIGIBILITY_AGENT_ID
    assert "FABRIC_TOKEN" not in vars(recovery)


def test_device_code_uses_injected_app_and_never_prints_token(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)

    class App:
        def initiate_device_flow(self, scopes):
            assert scopes == [recovery.FABRIC_DEFAULT_SCOPE]
            return {"user_code": "ABCD", "message": "Use device code ABCD"}

        def acquire_token_by_device_flow(self, flow):
            assert flow["user_code"] == "ABCD"
            return {"access_token": "super-secret-token"}

    output: list[str] = []
    token = recovery.acquire_token_device_code(
        "tenant", "client", app=App(), print_fn=output.append
    )
    assert token == "super-secret-token"
    assert output == ["Use device code ABCD"]
    assert "super-secret-token" not in " ".join(output)


def test_device_code_uses_silent_cached_token_before_interactive_flow(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)

    class App:
        def get_accounts(self):
            return [{"home_account_id": "account"}]

        def acquire_token_silent(self, scopes, *, account, force_refresh):
            assert scopes == [recovery.FABRIC_DEFAULT_SCOPE]
            assert account["home_account_id"] == "account"
            assert force_refresh is True
            return {"access_token": "cached-token"}

        def initiate_device_flow(self, scopes):
            pytest.fail(f"Interactive flow should not run for cached token: {scopes}")

    output: list[str] = []
    token = recovery.acquire_token_device_code(
        "tenant", "client", app=App(), print_fn=output.append
    )
    assert token == "cached-token"
    assert output == []


def test_bootstrap_token_scope_validation(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)

    def token(scopes: set[str]) -> str:
        payload = base64.urlsafe_b64encode(
            json.dumps({"scp": " ".join(sorted(scopes))}).encode()
        ).decode().rstrip("=")
        return f"header.{payload}.signature"

    recovery._assert_bootstrap_token_scopes(
        token(recovery.REQUIRED_BOOTSTRAP_SCOPES)
    )
    with pytest.raises(recovery.RecoveryError, match="Workspace.Read.All"):
        recovery._assert_bootstrap_token_scopes(
            token(recovery.REQUIRED_BOOTSTRAP_SCOPES - {"Workspace.Read.All"})
        )


def test_cached_access_tokens_are_evicted_but_refresh_tokens_are_retained(
    monkeypatch,
) -> None:
    recovery = _reload_module(monkeypatch)

    class Cache:
        def __init__(self) -> None:
            self.removed: list[dict[str, str]] = []

        def find(self, credential_type):
            assert credential_type == "AccessToken"
            return [{"secret": "access"}]

        def remove_at(self, entry):
            self.removed.append(entry)

    cache = Cache()
    recovery._evict_cached_access_tokens(cache)
    assert cache.removed == [{"secret": "access"}]


def test_mutation_confirmation_precedes_authentication(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)
    called = False

    def acquire(*_args, **_kwargs):
        nonlocal called
        called = True
        return "token"

    with pytest.raises(recovery.RecoveryError, match="--confirm-mutations RECOVER"):
        recovery.run_recovery(
            tenant_id="tenant",
            client_id="client",
            workspace_id="workspace",
            confirmation="no",
            request_timeout=1,
            operation_timeout=1,
            mcp_timeout=1,
            token_acquirer=acquire,
        )
    assert called is False


def test_fabric_client_retries_with_timeout_and_no_token_in_error(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)
    session = QueueSession(
        [
            FakeResponse(
                {"error": {"code": "Throttled", "message": "wait"}},
                status_code=429,
                headers={"Retry-After": "0"},
            ),
            FakeResponse({"value": []}),
        ]
    )
    client = recovery.FabricClient(
        "secret-token",
        request_timeout=17,
        session=session,
        sleep_fn=lambda _seconds: None,
    )
    assert client.list_values("/workspaces/w/dataAgents") == []
    assert len(session.calls) == 2
    assert all(call["timeout"] == 17 for call in session.calls)
    assert all(call["headers"]["Authorization"] == "Bearer secret-token" for call in session.calls)

    failed = recovery.FabricClient(
        "secret-token",
        session=QueueSession(
            [
                FakeResponse(
                    {"error": {"code": "Denied", "message": "not allowed"}},
                    status_code=403,
                )
            ]
        ),
    )
    with pytest.raises(recovery.RecoveryError) as exc:
        failed.request_json("GET", "/denied")
    assert "secret-token" not in str(exc.value)
    assert "Denied" in str(exc.value)


def test_lro_requires_location_header(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)
    client = recovery.FabricClient(
        "token", session=QueueSession([FakeResponse(status_code=202)])
    )
    with pytest.raises(recovery.RecoveryError, match="Location"):
        client.request_json("POST", "/create", wait_lro=True)


def test_non_idempotent_post_transport_failure_is_not_retried(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)

    class TimeoutSession:
        def __init__(self) -> None:
            self.calls = 0

        def request(self, *_args, **_kwargs):
            self.calls += 1
            raise requests.Timeout("response lost")

    session = TimeoutSession()
    client = recovery.FabricClient(
        "token", session=session, sleep_fn=lambda _seconds: None
    )
    with pytest.raises(recovery.RecoveryError, match="after 1 attempts"):
        client.request_json("POST", "/workspaces/w/dataAgents", wait_lro=True)
    assert session.calls == 1
    assert client.mutation_started is True


def test_eligibility_recovery_is_lakehouse_only_and_selects_all_tables(
    monkeypatch,
) -> None:
    recovery = _reload_module(monkeypatch)
    client = StatefulClient(recovery)
    client.sources[recovery.ELIGIBILITY_AGENT_ID] = [
        {
            "id": "wrong-graph",
            "type": "FabricItem",
            "itemReference": {"itemId": recovery.GRAPH_MODEL_ID},
        },
        {
            "id": "lakehouse-source",
            "type": "LakehouseTables",
            "lakehouseReference": {"itemId": recovery.LAKEHOUSE_ID},
        },
        {
            "id": "duplicate-lakehouse",
            "type": "LakehouseTables",
            "lakehouseReference": {"itemId": recovery.LAKEHOUSE_ID},
        },
    ]
    _lakehouse_tree(client, recovery)

    selected = recovery.recover_eligibility_agent(client, "workspace")

    assert set(selected) == recovery.REQUIRED_ELIGIBILITY_TABLES
    assert client.sources[recovery.ELIGIBILITY_AGENT_ID] == [
        {
            "id": "lakehouse-source",
            "type": "LakehouseTables",
            "lakehouseReference": {"itemId": recovery.LAKEHOUSE_ID},
        }
    ]
    deletes = {
        call["path"]
        for call in client.calls
        if call["method"] == "DELETE"
    }
    assert any(path.endswith("/wrong-graph") for path in deletes)
    assert any(path.endswith("/duplicate-lakehouse") for path in deletes)
    settings = next(
        call
        for call in client.calls
        if call["path"].endswith("/staging/settings")
    )
    assert settings["json"]["aiInstructions"] == recovery.ELIGIBILITY_INSTRUCTIONS
    assert settings["wait_lro"] is True
    publish = next(
        call
        for call in client.calls
        if call["method"] == "POST"
        and call["path"].endswith("/staging/publish")
    )
    assert publish["wait_lro"] is True
    assert any(
        call["method"] == "POST"
        and call["path"].endswith("/staging/publish")
        for call in client.calls
    )


def test_eligibility_recovery_adds_missing_lakehouse(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)
    client = StatefulClient(recovery)
    client.sources[recovery.ELIGIBILITY_AGENT_ID] = []
    _lakehouse_tree(client, recovery, selected=True)

    recovery.recover_eligibility_agent(client, "workspace")


def test_eligibility_recovery_rejects_incomplete_lakehouse(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)
    client = StatefulClient(recovery)
    client.sources[recovery.ELIGIBILITY_AGENT_ID] = [
        {
            "id": "lakehouse-source",
            "type": "LakehouseTables",
            "lakehouseReference": {"itemId": recovery.LAKEHOUSE_ID},
        }
    ]
    _lakehouse_tree(client, recovery)
    client.element_pages[(recovery.ELIGIBILITY_AGENT_ID, "tables")] = [
        element
        for element in client.element_pages[
            (recovery.ELIGIBILITY_AGENT_ID, "tables")
        ]
        if element["displayName"] != "trial_criteria"
    ]

    with pytest.raises(recovery.RecoveryError, match="trial_criteria"):
        recovery.recover_eligibility_agent(client, "workspace")

    assert not any(
        call["method"] == "POST"
        and call["path"].endswith("/staging/publish")
        for call in client.calls
    )

    source = client.sources[recovery.ELIGIBILITY_AGENT_ID][0]
    assert source["type"] == "LakehouseTables"
    assert source["lakehouseReference"]["itemId"] == recovery.LAKEHOUSE_ID


def test_table_selection_fails_when_no_tables_exist(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)
    client = StatefulClient(recovery)
    with pytest.raises(recovery.RecoveryError, match="no available tables"):
        recovery.select_all_lakehouse_tables(
            client, "workspace", recovery.ELIGIBILITY_AGENT_ID, "source"
        )


def test_table_selection_waits_for_schema_tree_availability(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)
    client = StatefulClient(recovery)
    _lakehouse_tree(client, recovery, selected=True)
    original_walk = recovery._walk_elements
    discovery_reads = 0

    def delayed_walk(*args):
        nonlocal discovery_reads
        discovery_reads += 1
        if discovery_reads < 3:
            return [
                {
                    "id": "schemas",
                    "type": "Schemas",
                    "state": "SchemaUnavailable",
                    "hasSubElements": False,
                }
            ]
        return original_walk(*args)

    monkeypatch.setattr(recovery, "_walk_elements", delayed_walk)

    selected = recovery.select_all_lakehouse_tables(
        client, "workspace", recovery.ELIGIBILITY_AGENT_ID, "source"
    )

    assert set(selected) == recovery.REQUIRED_ELIGIBILITY_TABLES
    assert discovery_reads == 4


def test_table_selection_waits_for_read_after_write_propagation(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)
    client = StatefulClient(recovery)
    _lakehouse_tree(client, recovery)
    original_walk = recovery._walk_elements
    verification_reads = 0

    def delayed_walk(*args):
        nonlocal verification_reads
        elements = original_walk(*args)
        if any(element.get("isSelected") is True for element in elements):
            verification_reads += 1
            if verification_reads < 3:
                for element in elements:
                    if element.get("type") in {"Table", "ExternalTable"}:
                        element["isSelected"] = False
        return elements

    monkeypatch.setattr(recovery, "_walk_elements", delayed_walk)

    selected = recovery.select_all_lakehouse_tables(
        client, "workspace", recovery.ELIGIBILITY_AGENT_ID, "source"
    )

    assert set(selected) == recovery.REQUIRED_ELIGIBILITY_TABLES
    assert verification_reads == 3


def test_graph_recovery_creates_via_official_dataagents_api_and_attaches_only_graph(
    monkeypatch,
) -> None:
    recovery = _reload_module(monkeypatch)
    client = StatefulClient(recovery)
    graph_fewshots_path = (
        "/workspaces/workspace/dataAgents/graph-agent-id/staging/"
        "datasources/graph-agent-id-source/fewshots"
    )
    client.fewshots[graph_fewshots_path] = [
        {
            "id": "extra",
            "question": "Unexpected example",
            "query": "MATCH (n) RETURN n",
            "validationStatus": {"value": "Valid"},
        }
    ]
    monkeypatch.setattr(recovery, "verify_graph_mcp", lambda *_args, **_kwargs: None)

    agent_id = recovery.recover_graph_agent(
        client, "token", "workspace", mcp_timeout=10
    )

    assert agent_id == "graph-agent-id"
    create = next(
        call
        for call in client.calls
        if call["method"] == "POST" and call["path"].endswith("/dataAgents")
    )
    assert create["wait_lro"] is True
    source = client.sources["graph-agent-id"]
    assert len(source) == 1
    assert source[0]["type"] == "FabricItem"
    assert source[0]["itemReference"]["itemId"] == recovery.GRAPH_MODEL_ID
    source_patch = next(
        call
        for call in client.calls
        if call["method"] == "PATCH"
        and call["path"].endswith("/graph-agent-id-source")
    )
    assert source_patch["json"] == {
        "description": recovery.GRAPH_SOURCE_DESCRIPTION,
        "instructions": recovery.GRAPH_SOURCE_INSTRUCTIONS,
    }
    assert source_patch["wait_lro"] is True
    graph_settings = next(
        call
        for call in client.calls
        if call["path"].endswith("/staging/settings")
    )
    assert graph_settings["wait_lro"] is True
    graph_publish = next(
        call
        for call in client.calls
        if call["method"] == "POST"
        and call["path"].endswith("/staging/publish")
    )
    assert graph_publish["wait_lro"] is True
    assert {
        item["question"] for item in client.fewshots[graph_fewshots_path]
    } == set(recovery.GRAPH_FEW_SHOTS)
    assert any(
        call["method"] == "DELETE" and call["path"].endswith("/extra")
        for call in client.calls
    )


def test_graph_recovery_reuses_exact_name_and_removes_other_sources(
    monkeypatch,
) -> None:
    recovery = _reload_module(monkeypatch)
    client = StatefulClient(recovery)
    client.agents = [
        {"id": "eligibility", "displayName": "amciq-fabric-eligibility-agent"},
        {"id": "graph-agent-id", "displayName": recovery.GRAPH_AGENT_NAME},
    ]
    client.sources["graph-agent-id"] = [
        {
            "id": "lakehouse",
            "type": "LakehouseTables",
            "lakehouseReference": {"itemId": recovery.LAKEHOUSE_ID},
        },
        {
            "id": "graph-source",
            "type": "FabricItem",
            "itemReference": {"itemId": recovery.GRAPH_MODEL_ID},
        },
    ]
    path = (
        "/workspaces/workspace/dataAgents/graph-agent-id/staging/"
        "datasources/graph-source/fewshots"
    )
    client.fewshots[path] = [
        {
            "id": f"few-{index}",
            "question": question,
            "query": query,
            "validationStatus": {"value": "Valid"},
        }
        for index, (question, query) in enumerate(recovery.GRAPH_FEW_SHOTS.items())
    ]
    monkeypatch.setattr(recovery, "verify_graph_mcp", lambda *_args, **_kwargs: None)

    recovery.recover_graph_agent(client, "token", "workspace", mcp_timeout=10)

    assert len(client.sources["graph-agent-id"]) == 1
    assert client.sources["graph-agent-id"][0]["id"] == "graph-source"
    assert not any(
        call["method"] == "POST" and call["path"].endswith("/dataAgents")
        for call in client.calls
    )


def test_duplicate_graph_agents_fail_before_mutation(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)
    client = StatefulClient(recovery)
    client.agents = [
        {"id": "one", "displayName": recovery.GRAPH_AGENT_NAME},
        {"id": "two", "displayName": recovery.GRAPH_AGENT_NAME},
    ]
    with pytest.raises(recovery.RecoveryError, match="found 2"):
        recovery.get_or_create_graph_agent(client, "workspace")
    assert not any(call["method"] != "GET" for call in client.calls)


def test_graph_agent_name_cannot_alias_protected_eligibility_id(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)
    client = StatefulClient(recovery)
    client.agents = [
        {
            "id": recovery.ELIGIBILITY_AGENT_ID.upper(),
            "displayName": recovery.GRAPH_AGENT_NAME,
        }
    ]
    with pytest.raises(recovery.RecoveryError, match="protected eligibility"):
        recovery.get_or_create_graph_agent(client, "workspace")
    assert not any(call["method"] != "GET" for call in client.calls)


def test_graph_agent_create_response_cannot_return_protected_eligibility_id(
    monkeypatch,
) -> None:
    recovery = _reload_module(monkeypatch)

    class ProtectedCreateClient(StatefulClient):
        def request_json(self, method, path, **kwargs):
            if path.endswith("/dataAgents") and method == "POST":
                self.calls.append(
                    {
                        "method": method,
                        "path": path,
                        "expected": kwargs.get("expected"),
                        "params": kwargs.get("params"),
                        "json": kwargs.get("json_body"),
                        "wait_lro": kwargs.get("wait_lro", False),
                    }
                )
                return {
                    "id": recovery.ELIGIBILITY_AGENT_ID.upper(),
                    "displayName": recovery.GRAPH_AGENT_NAME,
                }
            return super().request_json(method, path, **kwargs)

    client = ProtectedCreateClient(recovery)
    monkeypatch.setattr(recovery, "verify_graph_mcp", lambda *_args, **_kwargs: None)

    with pytest.raises(
        recovery.RecoveryError, match="immediate graph create response"
    ):
        recovery.recover_graph_agent(client, "token", "workspace", mcp_timeout=10)

    assert client.sources == {}
    assert [
        (call["method"], call["path"])
        for call in client.calls
        if call["method"] != "GET"
    ] == [("POST", "/workspaces/workspace/dataAgents")]


def test_graph_agent_polling_cannot_return_protected_eligibility_id(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)

    class ProtectedPollingClient(StatefulClient):
        def request_json(self, method, path, **kwargs):
            if path.endswith("/dataAgents") and method == "POST":
                self.calls.append(
                    {
                        "method": method,
                        "path": path,
                        "expected": kwargs.get("expected"),
                        "params": kwargs.get("params"),
                        "json": kwargs.get("json_body"),
                        "wait_lro": kwargs.get("wait_lro", False),
                    }
                )
                return {}
            return super().request_json(method, path, **kwargs)

        def list_values(self, path, *, params=None):
            result = super().list_values(path, params=params)
            if path.endswith("/dataAgents") and len(
                [call for call in self.calls if call["method"] == "GET"]
            ) > 1:
                return [
                    {
                        "id": recovery.ELIGIBILITY_AGENT_ID.upper(),
                        "displayName": recovery.GRAPH_AGENT_NAME,
                    }
                ]
            return result

    client = ProtectedPollingClient(recovery)
    client.operation_timeout = 0.01

    with pytest.raises(recovery.RecoveryError, match="protected eligibility"):
        recovery.get_or_create_graph_agent(client, "workspace")


def test_fewshot_invalid_status_fails(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)

    class InvalidClient(StatefulClient):
        def request_json(self, method, path, **kwargs):
            result = super().request_json(method, path, **kwargs)
            if "/fewshots/" in path and method == "PATCH":
                parent, item_id = path.rsplit("/", 1)
                for item in self.fewshots[parent]:
                    if item["id"] == item_id:
                        item["validationStatus"] = {
                            "value": "Invalid",
                            "reason": "bad GQL",
                        }
            return result

    client = InvalidClient(recovery)
    client.operation_timeout = 0.01
    path = (
        "/workspaces/workspace/dataAgents/agent/staging/"
        "datasources/source/fewshots"
    )
    client.fewshots[path] = [
        {
            "id": f"few-{index}",
            "question": question,
            "query": query,
            "validationStatus": {
                "value": "Invalid" if index == 0 else "Valid",
                "reason": "bad GQL",
            },
        }
        for index, (question, query) in enumerate(recovery.GRAPH_FEW_SHOTS.items())
    ]
    with pytest.raises(recovery.RecoveryError, match="Last invalid statuses.*bad GQL"):
        recovery.sync_graph_few_shots(
            client, "workspace", "agent", "source"
        )


def test_mcp_verification_retries_and_requires_expected_markers(
    monkeypatch,
) -> None:
    recovery = _reload_module(monkeypatch)
    attempts = 0
    sleeps: list[float] = []

    async def verify_once(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("not ready")

    monkeypatch.setattr(recovery, "_verify_graph_mcp_once", verify_once)
    recovery.verify_graph_mcp(
        "token",
        "workspace",
        "agent",
        attempts=2,
        sleep_fn=sleeps.append,
    )
    assert attempts == 2
    assert sleeps == [10]

    recovery = _reload_module(monkeypatch)

    async def fake_ask(_token, _workspace, _agent, question, *, timeout_seconds):
        assert timeout_seconds == 9
        if "screened" in question:
            return "NCT99004324"
        if "criteria" in question:
            return "NCT99004324-REN only"
        return "Dr. Priya Anand"

    monkeypatch.setattr(recovery, "_ask_graph_data_agent_mcp", fake_ask)
    with pytest.raises(recovery.RecoveryError, match="NCT99004324-BIO"):
        asyncio.run(
            recovery._verify_graph_mcp_once(
                "token", "workspace", "agent", timeout_seconds=9
            )
        )


def test_mcp_package_is_mandatory(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)
    monkeypatch.setattr(recovery.importlib.util, "find_spec", lambda _name: None)
    with pytest.raises(recovery.RecoveryError, match="verification is mandatory"):
        asyncio.run(
            recovery._ask_graph_data_agent_mcp(
                "token",
                "workspace",
                "agent",
                "question",
                timeout_seconds=1,
            )
        )


def test_run_recovery_marks_post_mutation_failures(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)

    class MutatingClient:
        def __init__(self, *_args, **_kwargs) -> None:
            self.mutation_started = True

    monkeypatch.setattr(recovery, "FabricClient", MutatingClient)
    monkeypatch.setattr(
        recovery,
        "recover_eligibility_agent",
        lambda *_args: (_ for _ in ()).throw(recovery.RecoveryError("failed")),
    )
    with pytest.raises(recovery.RecoveryError) as exc:
        recovery.run_recovery(
            tenant_id="tenant",
            client_id="client",
            workspace_id="workspace",
            confirmation="RECOVER",
            request_timeout=1,
            operation_timeout=1,
            mcp_timeout=1,
            token_acquirer=lambda *_args: "token",
        )
    assert exc.value.mutation_started is True


def test_main_reports_pre_and_post_mutation_exit_codes_without_token(
    monkeypatch, capsys
) -> None:
    recovery = _reload_module(monkeypatch)

    def fail_before_mutation(**_kwargs):
        raise recovery.RecoveryError("safe error")

    monkeypatch.setattr(recovery, "run_recovery", fail_before_mutation)
    arguments = [
        "--tenant-id",
        "tenant",
        "--client-id",
        "client",
        "--confirm-mutations",
        "RECOVER",
    ]
    assert recovery.main(arguments) == 2
    captured = capsys.readouterr()
    assert "safe error" in captured.err
    assert "Bearer" not in captured.err

    def fail_after_mutation(**_kwargs):
        raise recovery.RecoveryError("mutation error", mutation_started=True)

    monkeypatch.setattr(recovery, "run_recovery", fail_after_mutation)
    assert recovery.main(arguments) == 1
    captured = capsys.readouterr()
    assert "mutation error" in captured.err
    assert "Bearer" not in captured.err


def test_main_success_arguments_remain_explicit(monkeypatch) -> None:
    recovery = _reload_module(monkeypatch)
    seen: dict[str, Any] = {}

    def succeed(**kwargs):
        seen.update(kwargs)
        return {
            "eligibilityAgentId": recovery.ELIGIBILITY_AGENT_ID,
            "selectedLakehouseTables": ["table"],
            "graphAgentId": "graph-agent",
        }

    monkeypatch.setattr(recovery, "run_recovery", succeed)
    assert recovery.main(
        [
            "--tenant-id",
            "tenant",
            "--client-id",
            "client",
            "--confirm-mutations",
            "RECOVER",
        ]
    ) == 0
    assert seen["confirmation"] == "RECOVER"


def test_powershell_declares_temporary_elevation_and_automatic_downgrade() -> None:
    text = POWERSHELL_SCRIPT.read_text(encoding="utf-8")
    assert "TEMPORARY BOOTSTRAP ELEVATION" in text
    assert '$ItemScope = "Item.ReadWrite.All"' in text
    assert '$DataAgentScope = "DataAgent.ReadWrite.All"' in text
    assert '$LakehouseScope = "Lakehouse.Read.All"' in text
    assert '$SqlEndpointScope = "SQLEndpoint.Read.All"' in text
    assert '$WorkspaceScope = "Workspace.Read.All"' in text
    assert "Set-BootstrapElevation" in text
    assert "Set-SteadyStatePermissions" in text
    assert "$allowedLegacy = @($script:DataAgentScopeId)" in text
    assert "SQLEndpoint.Read.All." in text
    assert "--confirm-mutations RECOVER" in text
    assert "bootstrap elevation will be removed before exit" in text
    assert "intentionally retained" not in text
    assert "if ($script:BootstrapManaged)" in text
    assert '"--uri", "`"$Uri`""' in text
    assert '"--body", "@$bodyPath"' in text
    assert "Remove-Item -LiteralPath $bodyPath -Force" in text
    data_agent_script = (
        LIVE_DIR / "provision_fabric_data_agent.ps1"
    ).read_text(encoding="utf-8")
    assert "Where-Object { $_.id -ne $lakehouseSource.id }" in data_agent_script
    assert "$finalSources.Count -ne 1" in data_agent_script


def test_powershell_has_no_credential_creation_and_checks_extra_privilege() -> None:
    text = POWERSHELL_SCRIPT.read_text(encoding="utf-8")
    assert "passwordCredentials = @()" in text
    assert "keyCredentials = @()" in text
    assert "credential reset" not in text.casefold()
    assert "application permission" in text
    assert "extra delegated permission grants" in text
    assert "extra or unexpected principal assignment" in text
    assert "appRoleAssignmentRequired = $true" in text


def test_powershell_detects_preexisting_bootstrap_before_preflight_cleanup() -> None:
    text = POWERSHELL_SCRIPT.read_text(encoding="utf-8")
    assert "function Test-PreExistingBootstrapElevation" in text
    app_probe = (
        "$script:BootstrapManaged = Test-PreExistingBootstrapElevation "
        "-App $script:Application"
    )
    grant_probe = "$script:Grant = Get-DelegatedGrant -ClientSp $script:ClientServicePrincipal"
    assert app_probe in text
    assert grant_probe in text
    assert text.index(app_probe) < text.rindex("Ensure-SingleAssignment")
    assert text.index(grant_probe) < text.rindex("Ensure-SingleAssignment")


@pytest.mark.skipif(
    not (os.environ.get("COMSPEC") or sys.platform == "win32"),
    reason="PowerShell syntax validation is Windows-specific in this repository.",
)
def test_powershell_syntax() -> None:
    command = (
        "$errors=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{str(POWERSHELL_SCRIPT).replace(chr(39), chr(39) * 2)}',"
        "[ref]$null,[ref]$errors) | Out-Null; "
        "if ($errors.Count) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
