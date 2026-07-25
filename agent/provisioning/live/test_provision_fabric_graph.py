from __future__ import annotations

import base64
import csv
import asyncio
import json
import os
from pathlib import Path

import pytest
import requests

os.environ.setdefault("FABRIC_TOKEN", "test-token")
os.environ.setdefault("STORAGE_TOKEN", "test-token")
os.environ.setdefault("FABRIC_WS", "test-workspace")
os.environ.setdefault("FABRIC_LH", "test-lakehouse")
os.environ.setdefault("ELIGIBILITY_DATA_AGENT_ID", "eligibility-agent-id")
os.environ.setdefault("ONTOLOGY_NAME", "test-ontology")
os.environ.setdefault("DATA_AGENT_NAME", "test-eligibility-agent")
os.environ.setdefault("GRAPH_MODEL_NAME", "test-graph")
os.environ.setdefault("GRAPH_DATA_AGENT_NAME", "test-graph-agent")
os.environ.setdefault("ELIGIBILITY_DATA_AGENT_NAME", "test-eligibility-agent")

import provision_fabric_graph as graph


@pytest.fixture(autouse=True)
def _enable_data_agent_mutation_for_unit_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATTACH_GRAPH_TO_DA", "1")


class FakeResponse:
    def __init__(
        self,
        payload: dict | None = None,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload or {}
        self.status_code = status_code
        self.headers = headers or {}
        self.text = json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {self.status_code}", response=self
            )


def _mock_requests(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[tuple[str, str], list[FakeResponse]],
) -> list[dict]:
    calls: list[dict] = []

    def make_request(method: str):
        def request(url: str, **kwargs) -> FakeResponse:
            calls.append({"method": method, "url": url, **kwargs})
            key = (method, url)
            if key not in responses or not responses[key]:
                pytest.fail(f"Unexpected {method} request to {url}")
            return responses[key].pop(0)

        return request

    for method in ("GET", "POST", "PATCH", "DELETE"):
        monkeypatch.setattr(graph.requests, method.lower(), make_request(method))
    monkeypatch.setattr(graph.time, "sleep", lambda _seconds: None)
    return calls


def _valid_few_shots() -> list[dict]:
    return [
        {
            "id": f"fewshot-{index}",
            "question": question,
            "query": query,
            "validationStatus": {"value": "Valid"},
        }
        for index, (question, query) in enumerate(
            graph.GRAPH_FEW_SHOTS.items(), start=1
        )
    ]


def _decoded_parts(definition: dict) -> dict[str, dict]:
    return {
        part["path"]: json.loads(base64.b64decode(part["payload"]).decode("utf-8"))
        for part in definition["parts"]
    }


def _schemas() -> dict[str, list[tuple[str, str]]]:
    return {
        entity: [(column, "string") for column in spec.props]
        for entity, spec in graph.ontology.ENTITIES.items()
    }


def test_builds_all_entities_and_relationships(monkeypatch) -> None:
    schemas = _schemas()
    monkeypatch.setattr(
        graph.ontology,
        "delta_schema",
        lambda table: next(
            schemas[entity]
            for entity, spec in graph.ontology.ENTITIES.items()
            if spec.table == table
        ),
    )

    parts = _decoded_parts(graph.build_public_definition())
    graph_type = parts["graphType.json"]
    graph_definition = parts["graphDefinition.json"]

    assert len(graph_type["nodeTypes"]) == len(graph.ontology.ENTITIES)
    assert len(graph_type["edgeTypes"]) == len(graph.ontology.RELATIONSHIPS)
    assert len(graph_definition["nodeTables"]) == len(graph.ontology.ENTITIES)
    assert len(graph_definition["edgeTables"]) == len(graph.ontology.RELATIONSHIPS)


def test_uses_managed_onelake_delta_sources(monkeypatch) -> None:
    schemas = _schemas()
    monkeypatch.setattr(
        graph.ontology,
        "delta_schema",
        lambda table: next(
            schemas[entity]
            for entity, spec in graph.ontology.ENTITIES.items()
            if spec.table == table
        ),
    )

    parts = _decoded_parts(graph.build_public_definition())
    sources = parts["dataSources.json"]["dataSources"]
    references = parts["dataSources.json"]["itemReferences"]

    assert sources
    assert references == [
        {
            "name": "amciq_lakehouse",
            "item": {"workspaceId": graph.WS, "itemId": graph.LH},
        }
    ]
    assert all(source["type"] == "DeltaTable" for source in sources)
    assert all(
        source["properties"]["referenceName"] == "amciq_lakehouse"
        and source["properties"]["path"].startswith("Tables/")
        for source in sources
    )


def test_maps_composite_relationship_keys_in_entity_order() -> None:
    relationship = next(
        item
        for item in graph.ontology.RELATIONSHIPS
        if item.name == "requires_criterion"
    )

    assert graph._relationship_key_columns(
        relationship, relationship.src, relationship.src_map
    ) == ["trial_id"]
    assert graph._relationship_key_columns(
        relationship, relationship.tgt, relationship.tgt_map
    ) == ["trial_id", "criterion_id"]


def test_graph_uses_canonical_longitudinal_keys(monkeypatch) -> None:
    schemas = _schemas()
    monkeypatch.setattr(
        graph.ontology,
        "delta_schema",
        lambda table: next(
            schemas[entity]
            for entity, spec in graph.ontology.ENTITIES.items()
            if spec.table == table
        ),
    )

    parts = _decoded_parts(graph.build_public_definition())
    graph_type = parts["graphType.json"]
    graph_definition = parts["graphDefinition.json"]
    nodes = {node["alias"]: node for node in graph_type["nodeTypes"]}
    edges = {edge["edgeTypeAlias"]: edge for edge in graph_definition["edgeTables"]}

    assert nodes["Lab"]["primaryKeyProperties"] == ["patient_id", "lab_date", "lab_type"]
    assert nodes["Treatment"]["primaryKeyProperties"] == ["patient_id", "line", "drug_name"]
    assert edges["has_lab"]["destinationNodeKeyColumns"] == [
        "patient_id",
        "lab_date",
        "lab_type",
    ]
    assert edges["on_treatment"]["destinationNodeKeyColumns"] == [
        "patient_id",
        "line",
        "drug_name",
    ]


def test_graph_longitudinal_keys_are_unique_in_seed_data() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    cases = {
        "labs.csv": graph.GRAPH_PRIMARY_KEYS["Lab"],
        "treatment_history.csv": graph.GRAPH_PRIMARY_KEYS["Treatment"],
    }
    for filename, keys in cases.items():
        with (repo_root / "data" / "fabric" / filename).open(
            newline="", encoding="utf-8"
        ) as handle:
            rows = list(csv.DictReader(handle))
        identities = [tuple(row[key] for key in keys) for row in rows]
        assert len(identities) == len(set(identities)), filename


def test_maps_delta_types_to_graph_types() -> None:
    assert graph._graph_property_type("string") == "STRING"
    assert graph._graph_property_type("long") == "INT"
    assert graph._graph_property_type("double") == "FLOAT"
    assert graph._graph_property_type("boolean") == "BOOLEAN"
    assert graph._graph_property_type("timestamp") == "DATETIME"
    assert graph._graph_property_type("unknown") == "STRING"


def test_graph_few_shots_use_explicit_return_aliases() -> None:
    assert graph.GRAPH_FEW_SHOTS
    assert all(" AS " in query for query in graph.GRAPH_FEW_SHOTS.values())


def test_verify_graph_proves_longitudinal_lab_identity(monkeypatch) -> None:
    def fake_execute_query(_graph_id: str, query: str) -> dict:
        if "requires_criterion" in query:
            rows = [
                {"criterion_id": "NCT99004324-REN"},
                {"criterion_id": "NCT99004324-BIO"},
            ]
        elif "treated_by" in query:
            rows = [{"display_name": "Dr. Priya Anand"}]
        elif "has_lab" in query:
            rows = [
                {
                    "patient_id": "PT-1042",
                    "lab_date": "2026-05-20T00:00:00Z",
                    "lab_type": "CrCl_CKD-EPI",
                },
                {
                    "patient_id": "PT-1042",
                    "lab_date": "2026-06-18T00:00:00Z",
                    "lab_type": "CrCl_CKD-EPI",
                },
            ]
        elif "screened_for" in query:
            rows = [{"patient_id": "PT-1042", "trial_id": "NCT99004324"}]
        else:
            rows = [{"patient_id": "PT-1042", "display_name": "Elena Ramirez"}]
        return {"status": {"code": "00000"}, "result": {"data": rows}}

    monkeypatch.setattr(graph, "execute_query", fake_execute_query)
    graph.verify_graph("graph-id")


def test_verify_graph_rejects_collapsed_lab_identity(monkeypatch) -> None:
    def fake_execute_query(_graph_id: str, query: str) -> dict:
        if "requires_criterion" in query:
            rows = [
                {"criterion_id": "NCT99004324-REN"},
                {"criterion_id": "NCT99004324-BIO"},
            ]
        elif "treated_by" in query:
            rows = [{"display_name": "Dr. Priya Anand"}]
        elif "has_lab" in query:
            rows = [
                {
                    "patient_id": "PT-1042",
                    "lab_date": "2026-06-18T00:00:00Z",
                    "lab_type": "CrCl_CKD-EPI",
                }
            ]
        elif "screened_for" in query:
            rows = [{"patient_id": "PT-1042", "trial_id": "NCT99004324"}]
        else:
            rows = [{"patient_id": "PT-1042", "display_name": "Elena Ramirez"}]
        return {"status": {"code": "00000"}, "result": {"data": rows}}

    monkeypatch.setattr(graph, "execute_query", fake_execute_query)
    with pytest.raises(RuntimeError, match="distinct longitudinal"):
        graph.verify_graph("graph-id")


def test_refuses_to_target_protected_eligibility_agent(monkeypatch) -> None:
    monkeypatch.setattr(
        graph, "GRAPH_DATA_AGENT_NAME", graph.ELIGIBILITY_DATA_AGENT_NAME
    )
    monkeypatch.setattr(
        graph.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail(
            "Protection must run before any Fabric request"
        ),
    )

    with pytest.raises(RuntimeError, match="protected eligibility"):
        graph._get_or_create_graph_data_agent()


def test_refuses_graph_name_alias_for_protected_eligibility_id(monkeypatch) -> None:
    agents_url = f"{graph.FABRIC}/workspaces/{graph.WS}/dataAgents"
    calls = _mock_requests(
        monkeypatch,
        {
            ("GET", agents_url): [
                FakeResponse(
                    {
                        "value": [
                            {
                                "id": graph.ELIGIBILITY_DATA_AGENT_ID,
                                "displayName": graph.GRAPH_DATA_AGENT_NAME,
                            }
                        ]
                    }
                )
            ]
        },
    )

    with pytest.raises(RuntimeError, match="protected eligibility"):
        graph._get_or_create_graph_data_agent()
    assert [call["method"] for call in calls] == ["GET"]


def test_direct_data_agent_provisioning_requires_explicit_gate(monkeypatch) -> None:
    monkeypatch.delenv("ATTACH_GRAPH_TO_DA")
    monkeypatch.setattr(
        graph.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail(
            "Mutation gate must run before any Fabric request"
        ),
    )

    with pytest.raises(RuntimeError, match="ATTACH_GRAPH_TO_DA=1"):
        graph.provision_graph_data_agent("graph-id")


def test_reuses_graph_agent_without_touching_eligibility_agent(monkeypatch) -> None:
    monkeypatch.setattr(graph, "GRAPH_DATA_AGENT_NAME", "amciq-fabric-graph-agent")
    monkeypatch.setattr(graph, "verify_graph_data_agent_mcp", lambda _agent_id: False)
    agents_url = f"{graph.FABRIC}/workspaces/{graph.WS}/dataAgents"
    base = f"{graph.FABRIC}/workspaces/{graph.WS}/dataAgents/graph-agent-id"
    sources_url = f"{base}/staging/datasources"
    fewshots_url = f"{sources_url}/graph-source-id/fewshots"
    graph_source = {
        "id": "graph-source-id",
        "itemReference": {"itemId": "graph-id"},
    }
    responses = {
        ("GET", agents_url): [
            FakeResponse(
                {
                    "value": [
                        {
                            "id": "eligibility-agent-id",
                            "displayName": graph.ELIGIBILITY_DATA_AGENT_NAME,
                        },
                        {
                            "id": "graph-agent-id",
                            "displayName": graph.GRAPH_DATA_AGENT_NAME,
                        },
                    ]
                }
            )
        ],
        ("GET", sources_url): [
            FakeResponse({"value": [graph_source]}),
            FakeResponse({"value": [graph_source]}),
        ],
        ("PATCH", f"{sources_url}/graph-source-id"): [FakeResponse()],
        ("GET", fewshots_url): [
            FakeResponse({"value": _valid_few_shots()}),
            FakeResponse({"value": _valid_few_shots()}),
        ],
        ("PATCH", f"{base}/staging/settings"): [FakeResponse()],
        ("POST", f"{base}/staging/publish"): [FakeResponse()],
    }
    calls = _mock_requests(monkeypatch, responses)

    assert graph.provision_graph_data_agent("graph-id") == "graph-agent-id"
    assert not any(call["method"] == "DELETE" for call in calls)
    assert not any("eligibility-agent-id" in call["url"] for call in calls)
    assert all(call.get("timeout") == graph.HTTP_TIMEOUT for call in calls)
    publish_call = next(
        call for call in calls if call["url"] == f"{base}/staging/publish"
    )
    assert "graph-only" in publish_call["json"]["publishedDescription"]


def test_graph_fewshot_sync_deletes_duplicate_questions(monkeypatch) -> None:
    base = f"{graph.FABRIC}/workspaces/{graph.WS}/dataAgents/graph-agent-id"
    fewshots_url = f"{base}/staging/datasources/graph-source-id/fewshots"
    valid = _valid_few_shots()
    duplicate = {
        **valid[0],
        "id": "duplicate-fewshot",
        "query": "MATCH (wrong) RETURN wrong",
    }
    calls = _mock_requests(
        monkeypatch,
        {
            ("GET", fewshots_url): [
                FakeResponse({"value": [*valid, duplicate]}),
                FakeResponse({"value": valid}),
            ],
            ("DELETE", f"{fewshots_url}/duplicate-fewshot"): [FakeResponse()],
        },
    )

    graph.sync_graph_few_shots(base, "graph-source-id")

    assert any(
        call["method"] == "DELETE"
        and call["url"] == f"{fewshots_url}/duplicate-fewshot"
        for call in calls
    )


def test_creates_graph_only_agent_and_attaches_verified_graph(monkeypatch) -> None:
    monkeypatch.setattr(graph, "GRAPH_DATA_AGENT_NAME", "amciq-fabric-graph-agent")
    monkeypatch.setattr(graph, "verify_graph_data_agent_mcp", lambda _agent_id: False)
    agents_url = f"{graph.FABRIC}/workspaces/{graph.WS}/dataAgents"
    base = f"{graph.FABRIC}/workspaces/{graph.WS}/dataAgents/graph-agent-id"
    sources_url = f"{base}/staging/datasources"
    fewshots_url = f"{sources_url}/graph-source-id/fewshots"
    graph_source = {
        "id": "graph-source-id",
        "itemReference": {"itemId": "verified-graph-id"},
    }
    responses = {
        ("GET", agents_url): [
            FakeResponse(
                {
                    "value": [
                        {
                            "id": "eligibility-agent-id",
                            "displayName": graph.ELIGIBILITY_DATA_AGENT_NAME,
                        }
                    ]
                }
            ),
            FakeResponse(
                {
                    "value": [
                        {
                            "id": "graph-agent-id",
                            "displayName": graph.GRAPH_DATA_AGENT_NAME,
                        }
                    ]
                }
            ),
        ],
        ("POST", agents_url): [FakeResponse(status_code=201)],
        ("GET", sources_url): [
            FakeResponse({"value": []}),
            FakeResponse({"value": [graph_source]}),
            FakeResponse({"value": [graph_source]}),
        ],
        ("POST", sources_url): [FakeResponse(status_code=201)],
        ("PATCH", f"{sources_url}/graph-source-id"): [FakeResponse()],
        ("GET", fewshots_url): [
            FakeResponse({"value": []}),
            FakeResponse({"value": _valid_few_shots()}),
        ],
        ("POST", fewshots_url): [
            FakeResponse(status_code=201)
            for _ in graph.GRAPH_FEW_SHOTS
        ],
        ("PATCH", f"{base}/staging/settings"): [FakeResponse()],
        ("POST", f"{base}/staging/publish"): [FakeResponse()],
    }
    calls = _mock_requests(monkeypatch, responses)

    assert graph.provision_graph_data_agent("verified-graph-id") == "graph-agent-id"
    create_agent = next(
        call
        for call in calls
        if call["method"] == "POST" and call["url"] == agents_url
    )
    assert create_agent["json"] == {
        "displayName": "amciq-fabric-graph-agent",
        "description": (
            "AMC IQ graph-only Fabric Data Agent over the verified oncology GraphModel."
        ),
    }
    attach = next(
        call
        for call in calls
        if call["method"] == "POST" and call["url"] == sources_url
    )
    assert attach["json"]["itemReference"]["itemId"] == "verified-graph-id"
    assert len(
        [
            call
            for call in calls
            if call["method"] == "POST" and call["url"] == fewshots_url
        ]
    ) == len(graph.GRAPH_FEW_SHOTS)


def test_reconciles_graph_agent_to_exactly_one_graph_source(monkeypatch) -> None:
    monkeypatch.setattr(graph, "GRAPH_DATA_AGENT_NAME", "amciq-fabric-graph-agent")
    monkeypatch.setattr(graph, "verify_graph_data_agent_mcp", lambda _agent_id: False)
    agents_url = f"{graph.FABRIC}/workspaces/{graph.WS}/dataAgents"
    base = f"{graph.FABRIC}/workspaces/{graph.WS}/dataAgents/graph-agent-id"
    sources_url = f"{base}/staging/datasources"
    graph_source = {
        "id": "graph-source-id",
        "itemReference": {"itemId": "graph-id"},
    }
    responses = {
        ("GET", agents_url): [
            FakeResponse(
                {
                    "value": [
                        {
                            "id": "graph-agent-id",
                            "displayName": graph.GRAPH_DATA_AGENT_NAME,
                        }
                    ]
                }
            )
        ],
        ("GET", sources_url): [
            FakeResponse(
                {
                    "value": [
                        {"id": "lakehouse-source-id"},
                        graph_source,
                        {
                            "id": "duplicate-graph-source-id",
                            "itemReference": {"itemId": "graph-id"},
                        },
                    ]
                }
            ),
            FakeResponse({"value": [graph_source]}),
        ],
        ("DELETE", f"{sources_url}/lakehouse-source-id"): [FakeResponse()],
        ("DELETE", f"{sources_url}/duplicate-graph-source-id"): [FakeResponse()],
        ("PATCH", f"{sources_url}/graph-source-id"): [FakeResponse()],
        ("GET", f"{sources_url}/graph-source-id/fewshots"): [
            FakeResponse({"value": _valid_few_shots()}),
            FakeResponse({"value": _valid_few_shots()}),
        ],
        ("PATCH", f"{base}/staging/settings"): [FakeResponse()],
        ("POST", f"{base}/staging/publish"): [FakeResponse()],
    }
    calls = _mock_requests(monkeypatch, responses)

    graph.provision_graph_data_agent("graph-id")

    deleted = {
        call["url"]
        for call in calls
        if call["method"] == "DELETE"
    }
    assert deleted == {
        f"{sources_url}/lakehouse-source-id",
        f"{sources_url}/duplicate-graph-source-id",
    }
    assert not any(
        call["url"] == f"{sources_url}/graph-source-id"
        and call["method"] == "DELETE"
        for call in calls
    )


def test_duplicate_graph_agents_fail_loudly(monkeypatch) -> None:
    monkeypatch.setattr(graph, "GRAPH_DATA_AGENT_NAME", "amciq-fabric-graph-agent")
    agents_url = f"{graph.FABRIC}/workspaces/{graph.WS}/dataAgents"
    duplicate = {
        "displayName": graph.GRAPH_DATA_AGENT_NAME,
    }
    _mock_requests(
        monkeypatch,
        {
            ("GET", agents_url): [
                FakeResponse(
                    {
                        "value": [
                            {"id": "graph-agent-1", **duplicate},
                            {"id": "graph-agent-2", **duplicate},
                        ]
                    }
                )
            ]
        },
    )

    with pytest.raises(RuntimeError, match="found 2"):
        graph._get_or_create_graph_data_agent()


def test_publish_failure_is_not_swallowed(monkeypatch) -> None:
    monkeypatch.setattr(graph, "GRAPH_DATA_AGENT_NAME", "amciq-fabric-graph-agent")
    monkeypatch.setattr(graph, "verify_graph_data_agent_mcp", lambda _agent_id: False)
    agents_url = f"{graph.FABRIC}/workspaces/{graph.WS}/dataAgents"
    base = f"{graph.FABRIC}/workspaces/{graph.WS}/dataAgents/graph-agent-id"
    sources_url = f"{base}/staging/datasources"
    graph_source = {
        "id": "graph-source-id",
        "itemReference": {"itemId": "graph-id"},
    }
    responses = {
        ("GET", agents_url): [
            FakeResponse(
                {
                    "value": [
                        {
                            "id": "graph-agent-id",
                            "displayName": graph.GRAPH_DATA_AGENT_NAME,
                        }
                    ]
                }
            )
        ],
        ("GET", sources_url): [
            FakeResponse({"value": [graph_source]}),
            FakeResponse({"value": [graph_source]}),
        ],
        ("PATCH", f"{sources_url}/graph-source-id"): [FakeResponse()],
        ("GET", f"{sources_url}/graph-source-id/fewshots"): [
            FakeResponse({"value": _valid_few_shots()}),
            FakeResponse({"value": _valid_few_shots()}),
        ],
        ("PATCH", f"{base}/staging/settings"): [FakeResponse()],
        ("POST", f"{base}/staging/publish"): [
            FakeResponse({"error": "publish failed"}, status_code=500)
        ],
    }
    _mock_requests(monkeypatch, responses)

    with pytest.raises(requests.HTTPError, match="HTTP 500"):
        graph.provision_graph_data_agent("graph-id")


def test_mcp_verification_skips_only_when_sdk_is_unavailable(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(graph.importlib.util, "find_spec", lambda _name: None)

    assert graph.verify_graph_data_agent_mcp("graph-agent-id") is False
    assert "MCP verification skipped" in capsys.readouterr().out


def test_mcp_verification_retries_while_published_agent_initializes(
    monkeypatch,
) -> None:
    attempts: list[str] = []
    sleeps: list[int] = []

    async def fake_verify(agent_id: str) -> None:
        attempts.append(agent_id)
        if len(attempts) == 1:
            raise ConnectionError("agent is still initializing")

    monkeypatch.setattr(graph.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(graph, "_verify_graph_data_agent_mcp_async", fake_verify)
    monkeypatch.setattr(graph.time, "sleep", sleeps.append)

    assert graph.verify_graph_data_agent_mcp("graph-agent-id") is True
    assert attempts == ["graph-agent-id", "graph-agent-id"]
    assert sleeps == [10]


def test_mcp_verification_fails_on_missing_expected_markers(monkeypatch) -> None:
    async def fake_ask(_agent_id: str, question: str) -> str:
        if "screened" in question:
            return "Patient PT-1042 is screened for NCT99004324."
        if "criteria" in question:
            return "Only NCT99004324-REN was returned."
        return "Dr. Priya Anand treats PT-1042."

    monkeypatch.setattr(graph, "_ask_graph_data_agent_mcp", fake_ask)

    with pytest.raises(RuntimeError, match="NCT99004324-BIO"):
        asyncio.run(graph._verify_graph_data_agent_mcp_async("graph-agent-id"))


def test_main_keeps_data_agent_mutation_gated(monkeypatch) -> None:
    monkeypatch.delenv("ATTACH_GRAPH_TO_DA", raising=False)
    monkeypatch.setattr(graph, "build_public_definition", lambda: {})
    monkeypatch.setattr(graph, "get_or_create_graph", lambda _definition: "graph-id")
    monkeypatch.setattr(graph, "refresh_graph", lambda _graph_id: None)
    monkeypatch.setattr(graph, "verify_graph", lambda _graph_id: None)
    monkeypatch.setattr(
        graph,
        "provision_graph_data_agent",
        lambda _graph_id: pytest.fail("Data Agent must remain gated"),
    )

    graph.main()


def test_main_provisions_graph_agent_when_gate_is_enabled(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setenv("ATTACH_GRAPH_TO_DA", "1")
    monkeypatch.setattr(graph, "build_public_definition", lambda: {})
    monkeypatch.setattr(graph, "get_or_create_graph", lambda _definition: "graph-id")
    monkeypatch.setattr(graph, "refresh_graph", lambda _graph_id: None)
    monkeypatch.setattr(graph, "verify_graph", lambda _graph_id: None)
    monkeypatch.setattr(
        graph, "provision_graph_data_agent", lambda graph_id: calls.append(graph_id)
    )

    graph.main()

    assert calls == ["graph-id"]
