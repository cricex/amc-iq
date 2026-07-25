"""Provision a queryable Fabric GraphModel for AMC IQ.

Fabric IQ Ontology creates a system-owned child GraphModel, but REST-provisioned ontologies can
leave that child without data sources, nodes, or edges. This script uses the supported public
GraphModel APIs to create a separate managed graph from the same OneLake Delta tables. The graph
can be queried directly with GQL and added to a Fabric Data Agent for NL2GQL reasoning.

Environment:
  FABRIC_TOKEN       token for https://api.fabric.microsoft.com/.default
  STORAGE_TOKEN      token for https://storage.azure.com/.default
  FABRIC_WS          workspace id
  FABRIC_LH          Lakehouse id
  GRAPH_MODEL_NAME   graph display name
  GRAPH_DATA_AGENT_NAME graph-only Data Agent display name
  ELIGIBILITY_DATA_AGENT_ID protected eligibility Data Agent item id
  ATTACH_GRAPH_TO_DA set to 1 to provision the graph-only Data Agent and publish
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import json
import os
import time
from typing import Any

import requests

import provision_fabric_ontology as ontology
from live_environment import required_environment

FABRIC = "https://api.fabric.microsoft.com/v1"
WS = os.environ.get("FABRIC_WS", ontology.WS)
LH = os.environ.get("FABRIC_LH", ontology.LH)
GRAPH_MODEL_NAME = required_environment("GRAPH_MODEL_NAME")
GRAPH_DATA_AGENT_NAME = required_environment("GRAPH_DATA_AGENT_NAME")
ELIGIBILITY_DATA_AGENT_NAME = required_environment("ELIGIBILITY_DATA_AGENT_NAME")
ELIGIBILITY_DATA_AGENT_ID = required_environment("ELIGIBILITY_DATA_AGENT_ID")
FABRIC_TOKEN = required_environment("FABRIC_TOKEN")
HTTP_TIMEOUT = (10, 60)

GRAPH_TYPE_MAP = {
    "string": "STRING",
    "integer": "INT",
    "long": "INT",
    "short": "INT",
    "byte": "INT",
    "double": "FLOAT",
    "float": "FLOAT",
    "decimal": "FLOAT",
    "boolean": "BOOLEAN",
    "date": "DATETIME",
    "timestamp": "DATETIME",
}

GRAPH_PRIMARY_KEYS = {
    "Lab": ("patient_id", "lab_date", "lab_type"),
    "Treatment": ("patient_id", "line", "drug_name"),
}

GRAPH_RELATIONSHIP_TARGET_MAPS = {
    "has_lab": {
        "patient_id": "patient_id",
        "lab_date": "lab_date",
        "lab_type": "lab_type",
    },
    "on_treatment": {
        "patient_id": "patient_id",
        "line": "line",
        "drug_name": "drug_name",
    },
}

GRAPH_FEW_SHOTS = {
    "Which trial is patient PT-1042 screened for?": (
        "MATCH (p:Patient)-[:screened_for]->(t:Trial) "
        "WHERE p.patient_id = 'PT-1042' "
        "RETURN p.patient_id AS patient_id, t.trial_id AS trial_id"
    ),
    "Which criteria are required by trial NCT99004324?": (
        "MATCH (t:Trial)-[:requires_criterion]->(c:Criterion) "
        "WHERE t.trial_id = 'NCT99004324' "
        "RETURN c.criterion_id AS criterion_id, c.description AS description"
    ),
    "Who treats patient PT-1042?": (
        "MATCH (p:Patient)-[:treated_by]->(person:Person) "
        "WHERE p.patient_id = 'PT-1042' "
        "RETURN person.display_name AS display_name, person.role AS role"
    ),
}

GRAPH_MCP_CHECKS = tuple(
    (question, expected)
    for question, expected in (
        ("Which trial is patient PT-1042 screened for?", ("NCT99004324",)),
        (
            "Which criteria are required by trial NCT99004324?",
            ("NCT99004324-REN", "NCT99004324-BIO"),
        ),
        ("Who treats patient PT-1042?", ("Dr. Priya Anand",)),
    )
)

GRAPH_SOURCE_DESCRIPTION = (
    "AMC IQ oncology relationship graph for patient-to-trial screening, criteria, "
    "biomarkers, labs, treatments, amendments, care teams, and sites."
)

GRAPH_SOURCE_INSTRUCTIONS = (
    "Use this GraphModel for relationship, traversal, and connected-entity questions. "
    "Node labels are Patient, Trial, Criterion, Biomarker, Lab, Treatment, Amendment, "
    "Enrollment, Person, and Site. Edge labels are has_biomarker, has_lab, on_treatment, "
    "screened_for, requires_criterion, has_amendment, amendment_modifies, treated_by, and "
    "trial_at_site. Use exact identifier properties such as patient_id and trial_id. "
    "Examples: MATCH (p:Patient)-[:screened_for]->(t:Trial) WHERE p.patient_id = 'PT-1042' "
    "RETURN p.patient_id AS patient_id, t.trial_id AS trial_id; "
    "MATCH (t:Trial)-[:requires_criterion]->(c:Criterion) "
    "WHERE t.trial_id = 'NCT99004324' "
    "RETURN c.criterion_id AS criterion_id, c.description AS description; "
    "MATCH (p:Patient)-[:treated_by]->(person:Person) WHERE p.patient_id = 'PT-1042' "
    "RETURN person.display_name AS display_name, person.role AS role. "
    "Always use AS aliases for every returned property."
)

GRAPH_AGENT_INSTRUCTIONS = (
    f"Use {GRAPH_MODEL_NAME} for all supported oncology questions. This agent has exactly one "
    "source: the verified GraphModel. Generate GQL for relationship, traversal, and "
    "connected-entity questions covering patient-to-trial screening, trial criteria, "
    "biomarkers, longitudinal labs, treatments, amendments, care-team owners, and trial sites. "
    "Use exact identifier properties; patient IDs look like PT-1042 and trial IDs look like "
    "NCT99004324. Return precise values and dates. Do not claim access to Lakehouse, semantic "
    "model, or ontology sources. Do not give medical advice."
)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {FABRIC_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _b64(value: dict[str, Any]) -> str:
    raw = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _part(path: str, value: dict[str, Any]) -> dict[str, str]:
    return {"path": path, "payload": _b64(value), "payloadType": "InlineBase64"}


def _stable_id(prefix: str, name: str) -> str:
    digest = hashlib.sha256(f"{prefix}:{name}".encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _source_name(table: str) -> str:
    return f"{table}_source"


def _graph_property_type(delta_type: str) -> str:
    return GRAPH_TYPE_MAP.get(delta_type.lower(), "STRING")


def _entity_keys(entity_name: str) -> tuple[str, ...]:
    return GRAPH_PRIMARY_KEYS.get(entity_name, ontology.ENTITIES[entity_name].keys)


def _relationship_key_columns(
    relationship: ontology.RelSpec,
    entity_name: str,
    mapping: dict[str, str],
) -> list[str]:
    by_property = {target_property: source_column for source_column, target_property in mapping.items()}
    keys = _entity_keys(entity_name)
    missing = [key for key in keys if key not in by_property]
    if missing:
        raise ValueError(
            f"Relationship {relationship.name} does not map every {entity_name} key: {missing}"
        )
    return [by_property[key] for key in keys]


def build_public_definition() -> dict[str, Any]:
    schemas = {
        entity_name: ontology.delta_schema(spec.table)
        for entity_name, spec in ontology.ENTITIES.items()
    }
    tables = {
        spec.table for spec in ontology.ENTITIES.values()
    } | {
        relationship.table for relationship in ontology.RELATIONSHIPS
    }

    data_sources = [
        {
            "name": _source_name(table),
            "type": "DeltaTable",
            "properties": {
                "referenceName": "amciq_lakehouse",
                "path": f"Tables/{table}",
            },
        }
        for table in sorted(tables)
    ]

    node_types = []
    node_tables = []
    for entity_name, spec in ontology.ENTITIES.items():
        properties = [
            {"name": column, "type": _graph_property_type(delta_type)}
            for column, delta_type in schemas[entity_name]
        ]
        node_types.append(
            {
                "alias": entity_name,
                "labels": [entity_name],
                "primaryKeyProperties": list(_entity_keys(entity_name)),
                "properties": properties,
            }
        )
        node_tables.append(
            {
                "id": _stable_id("node", entity_name),
                "nodeTypeAlias": entity_name,
                "dataSourceName": _source_name(spec.table),
                "propertyMappings": [
                    {"propertyName": column, "sourceColumn": column}
                    for column, _ in schemas[entity_name]
                ],
            }
        )

    edge_types = []
    edge_tables = []
    for relationship in ontology.RELATIONSHIPS:
        target_mapping = GRAPH_RELATIONSHIP_TARGET_MAPS.get(
            relationship.name, relationship.tgt_map
        )
        edge_types.append(
            {
                "alias": relationship.name,
                "labels": [relationship.name],
                "sourceNodeType": {"alias": relationship.src},
                "destinationNodeType": {"alias": relationship.tgt},
                "properties": [],
            }
        )
        edge_tables.append(
            {
                "id": _stable_id("edge", relationship.name),
                "edgeTypeAlias": relationship.name,
                "dataSourceName": _source_name(relationship.table),
                "sourceNodeKeyColumns": _relationship_key_columns(
                    relationship, relationship.src, relationship.src_map
                ),
                "destinationNodeKeyColumns": _relationship_key_columns(
                    relationship, relationship.tgt, target_mapping
                ),
                "propertyMappings": [],
            }
        )

    positions = {}
    styles = {}
    for index, entity_name in enumerate(ontology.ENTITIES):
        positions[entity_name] = {"x": (index % 5) * 240, "y": (index // 5) * 180}
        styles[entity_name] = {"size": 30}
    for relationship in ontology.RELATIONSHIPS:
        styles[relationship.name] = {"size": 20}

    graph_type = {
        "$schema": (
            "https://developer.microsoft.com/json-schemas/fabric/item/graphIndex/"
            "definition/graphType/1.0.0/schema.json"
        ),
        "nodeTypes": node_types,
        "edgeTypes": edge_types,
    }
    graph_definition = {
        "$schema": (
            "https://developer.microsoft.com/json-schemas/fabric/item/graphIndex/"
            "definition/graphDefinition/1.0.0/schema.json"
        ),
        "nodeTables": node_tables,
        "edgeTables": edge_tables,
    }
    sources_definition = {
        "$schema": (
            "https://developer.microsoft.com/json-schemas/fabric/item/graphIndex/"
            "definition/dataSources/1.1.0/schema.json"
        ),
        "itemReferences": [
            {
                "name": "amciq_lakehouse",
                "item": {"workspaceId": WS, "itemId": LH},
            }
        ],
        "dataSources": data_sources,
    }
    styling = {
        "$schema": (
            "https://developer.microsoft.com/json-schemas/fabric/item/graphIndex/"
            "definition/stylingConfiguration/1.0.0/schema.json"
        ),
        "modelLayout": {
            "positions": positions,
            "styles": styles,
            "pan": {"x": 0, "y": 0},
            "zoomLevel": 1,
        },
    }
    platform = {
        "$schema": (
            "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/"
            "platformProperties/2.0.0/schema.json"
        ),
        "metadata": {
            "type": "GraphModel",
            "displayName": GRAPH_MODEL_NAME,
            "description": (
                "AMC IQ oncology relationship graph over patients, trials, criteria, biomarkers, "
                "labs, treatments, amendments, care teams, and sites."
            ),
        },
        "config": {
            "version": "2.0",
            "logicalId": "00000000-0000-0000-0000-000000000000",
        },
    }
    return {
        "format": "json",
        "parts": [
            _part("graphType.json", graph_type),
            _part("graphDefinition.json", graph_definition),
            _part("dataSources.json", sources_definition),
            _part("stylingConfiguration.json", styling),
            _part(".platform", platform),
        ],
    }


def _wait_for_operation(response: requests.Response, *, timeout_seconds: int = 600) -> None:
    if response.status_code != 202:
        response.raise_for_status()
        return
    location = response.headers.get("Location")
    if not location:
        raise RuntimeError("Fabric returned HTTP 202 without an operation location.")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        wait_seconds = int(response.headers.get("Retry-After", "5"))
        time.sleep(max(1, min(wait_seconds, 15)))
        operation = requests.get(
            location, headers=_headers(), timeout=HTTP_TIMEOUT
        )
        operation.raise_for_status()
        payload = operation.json()
        status = payload.get("status")
        if status in {"Succeeded", "Completed"}:
            return
        if status in {"Failed", "Cancelled"}:
            raise RuntimeError(
                f"Fabric operation {status.lower()}: "
                f"{json.dumps(payload.get('failureReason') or payload, ensure_ascii=True)}"
            )
    raise TimeoutError(f"Fabric operation did not complete within {timeout_seconds} seconds.")


def get_or_create_graph(public_definition: dict[str, Any]) -> str:
    base = f"{FABRIC}/workspaces/{WS}/graphModels"
    response = requests.get(base, headers=_headers())
    response.raise_for_status()
    existing = next(
        (
            item
            for item in response.json().get("value", [])
            if item.get("displayName") == GRAPH_MODEL_NAME
        ),
        None,
    )
    if existing:
        graph_id = existing["id"]
        update = requests.post(
            f"{base}/{graph_id}/updateDefinition?updateMetadata=true",
            headers=_headers(),
            json={"definition": public_definition},
        )
        _wait_for_operation(update)
        print(f"  GraphModel updated: {GRAPH_MODEL_NAME} ({graph_id})")
        return graph_id

    create = requests.post(
        base,
        headers=_headers(),
        json={
            "displayName": GRAPH_MODEL_NAME,
            "description": (
                "Query-ready AMC IQ oncology graph over managed OneLake Delta tables."
            ),
            "definition": public_definition,
        },
    )
    _wait_for_operation(create)
    for _ in range(20):
        time.sleep(3)
        listing = requests.get(base, headers=_headers())
        listing.raise_for_status()
        for item in listing.json().get("value", []):
            if item.get("displayName") == GRAPH_MODEL_NAME:
                print(f"  GraphModel created: {GRAPH_MODEL_NAME} ({item['id']})")
                return item["id"]
    raise RuntimeError("GraphModel did not appear after creation.")


def refresh_graph(graph_id: str) -> None:
    response = requests.post(
        f"{FABRIC}/workspaces/{WS}/graphModels/{graph_id}/jobs/refreshGraph/instances",
        headers=_headers(),
        json={},
    )
    _wait_for_operation(response, timeout_seconds=900)
    print("  GraphModel refresh completed.")


def execute_query(graph_id: str, query: str) -> dict[str, Any]:
    response = requests.post(
        f"{FABRIC}/workspaces/{WS}/GraphModels/{graph_id}/executeQuery?preview=true",
        headers=_headers(),
        json={"query": query},
    )
    response.raise_for_status()
    payload = response.json()
    code = str(payload.get("status", {}).get("code", ""))
    if not code.startswith(("00", "01", "02", "03")):
        raise RuntimeError(
            f"GQL query failed with status {code}: "
            f"{payload.get('status', {}).get('description', 'unknown error')}"
        )
    return payload


def verify_graph(graph_id: str) -> None:
    patient_result = execute_query(
        graph_id,
        "MATCH (p:Patient) WHERE p.patient_id = 'PT-1042' "
        "RETURN p.patient_id AS patient_id, p.display_name AS display_name",
    )
    patient_rows = patient_result.get("result", {}).get("data", [])
    if not any(row.get("patient_id") == "PT-1042" for row in patient_rows):
        raise RuntimeError("GQL verification did not return patient PT-1042.")

    relationship_result = execute_query(
        graph_id,
        "MATCH (p:Patient)-[:screened_for]->(t:Trial) "
        "RETURN p.patient_id AS patient_id, t.trial_id AS trial_id "
        "ORDER BY patient_id, trial_id LIMIT 20",
    )
    relationship_rows = relationship_result.get("result", {}).get("data", [])
    expected = any(
        row.get("patient_id") == "PT-1042"
        and row.get("trial_id") == "NCT99004324"
        for row in relationship_rows
    )
    if not expected:
        raise RuntimeError(
            "GQL verification did not return PT-1042 screened_for NCT99004324."
        )

    criteria_result = execute_query(
        graph_id,
        "MATCH (t:Trial)-[:requires_criterion]->(c:Criterion) "
        "WHERE t.trial_id = 'NCT99004324' "
        "RETURN c.criterion_id AS criterion_id, c.description AS description",
    )
    criterion_ids = {
        row.get("criterion_id")
        for row in criteria_result.get("result", {}).get("data", [])
    }
    expected_criteria = {"NCT99004324-REN", "NCT99004324-BIO"}
    if not expected_criteria.issubset(criterion_ids):
        raise RuntimeError(
            "GQL verification did not return the expected NCT99004324 criteria."
        )

    clinician_result = execute_query(
        graph_id,
        "MATCH (p:Patient)-[:treated_by]->(person:Person) "
        "WHERE p.patient_id = 'PT-1042' "
        "RETURN person.display_name AS display_name, person.role AS role",
    )
    clinician_rows = clinician_result.get("result", {}).get("data", [])
    if not any(row.get("display_name") == "Dr. Priya Anand" for row in clinician_rows):
        raise RuntimeError(
            "GQL verification did not return Dr. Priya Anand for PT-1042."
        )

    labs_result = execute_query(
        graph_id,
        "MATCH (p:Patient)-[:has_lab]->(l:Lab) "
        "WHERE p.patient_id = 'PT-1042' "
        "RETURN l.patient_id AS patient_id, l.lab_date AS lab_date, "
        "l.lab_type AS lab_type",
    )
    lab_rows = labs_result.get("result", {}).get("data", [])
    lab_keys = {
        (str(row.get("lab_date", ""))[:10], row.get("lab_type"))
        for row in lab_rows
    }
    expected_labs = {
        ("2026-05-20", "CrCl_CKD-EPI"),
        ("2026-06-18", "CrCl_CKD-EPI"),
    }
    if not expected_labs.issubset(lab_keys):
        raise RuntimeError(
            "GQL verification did not return distinct longitudinal PT-1042 lab nodes."
        )
    print(
        "  GQL verification returned the expected patient, trial, criteria, clinician, "
        f"and {len(lab_keys)} longitudinal lab nodes."
    )


def sync_graph_few_shots(base: str, datasource_id: str) -> None:
    _require_data_agent_mutation_enabled()
    few_shots_url = f"{base}/staging/datasources/{datasource_id}/fewshots"
    response = requests.get(
        few_shots_url, headers=_headers(), timeout=HTTP_TIMEOUT
    )
    response.raise_for_status()
    existing: dict[str, dict[str, Any]] = {}
    for item in response.json().get("value", []):
        question = item.get("question")
        item_id = item.get("id")
        if not item_id:
            raise RuntimeError("A graph few-shot has no id; refusing reconciliation.")
        if question not in GRAPH_FEW_SHOTS or question in existing:
            removal = requests.delete(
                f"{few_shots_url}/{item_id}",
                headers=_headers(),
                timeout=HTTP_TIMEOUT,
            )
            removal.raise_for_status()
            continue
        existing[str(question)] = item

    for question, query in GRAPH_FEW_SHOTS.items():
        current = existing.get(question)
        if current:
            if (
                current.get("query") == query
                and current.get("validationStatus", {}).get("value") == "Valid"
            ):
                continue
            update = requests.patch(
                f"{few_shots_url}/{current['id']}",
                headers=_headers(),
                json={"question": question, "query": query},
                timeout=HTTP_TIMEOUT,
            )
            update.raise_for_status()
        else:
            create = requests.post(
                few_shots_url,
                headers=_headers(),
                json={"question": question, "query": query},
                timeout=HTTP_TIMEOUT,
            )
            create.raise_for_status()

    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        time.sleep(5)
        status_response = requests.get(
            few_shots_url, headers=_headers(), timeout=HTTP_TIMEOUT
        )
        status_response.raise_for_status()
        current = status_response.json().get("value", [])
        desired = {
            str(item.get("question")): item
            for item in current
            if item.get("question") in GRAPH_FEW_SHOTS
        }
        invalid = {
            question: item.get("validationStatus", {}).get(
                "reason", "unknown validation error"
            )
            for question, item in desired.items()
            if item.get("validationStatus", {}).get("value") == "Invalid"
        }
        if invalid:
            raise RuntimeError(
                f"GraphModel few-shot validation failed: {json.dumps(invalid, ensure_ascii=True)}"
            )
        if (
            len(current) == len(GRAPH_FEW_SHOTS)
            and set(desired) == set(GRAPH_FEW_SHOTS)
            and all(
                item.get("validationStatus", {}).get("value") == "Valid"
                and item.get("query") == GRAPH_FEW_SHOTS[question]
                for question, item in desired.items()
            )
        ):
            print(f"  {len(GRAPH_FEW_SHOTS)} validated NL2GQL examples synchronized.")
            return
    raise TimeoutError("GraphModel few-shot validation did not complete within 300 seconds.")


def _assert_graph_agent_name_is_safe() -> None:
    if GRAPH_DATA_AGENT_NAME.casefold() == ELIGIBILITY_DATA_AGENT_NAME.casefold():
        raise RuntimeError(
            "Refusing to provision the graph Data Agent with the protected eligibility "
            f"agent name {ELIGIBILITY_DATA_AGENT_NAME!r}."
        )


def _require_data_agent_mutation_enabled() -> None:
    if os.environ.get("ATTACH_GRAPH_TO_DA") != "1":
        raise RuntimeError(
            "Graph Data Agent mutation is disabled. "
            "Set ATTACH_GRAPH_TO_DA=1 to enable it."
        )


def _get_or_create_graph_data_agent() -> str:
    _require_data_agent_mutation_enabled()
    _assert_graph_agent_name_is_safe()
    agents_url = f"{FABRIC}/workspaces/{WS}/dataAgents"
    items = requests.get(
        agents_url,
        headers=_headers(),
        timeout=HTTP_TIMEOUT,
    )
    items.raise_for_status()
    matches = [
        item
        for item in items.json().get("value", [])
        if item.get("displayName") == GRAPH_DATA_AGENT_NAME
    ]
    if len(matches) > 1:
        raise RuntimeError(
            f"Expected at most one Data Agent named {GRAPH_DATA_AGENT_NAME!r}, "
            f"found {len(matches)}."
        )
    if matches:
        data_agent_id = matches[0].get("id")
        if not data_agent_id:
            raise RuntimeError(
                f"Data Agent {GRAPH_DATA_AGENT_NAME!r} did not include an item id."
            )
        if data_agent_id == ELIGIBILITY_DATA_AGENT_ID:
            raise RuntimeError(
                "Refusing to repurpose the protected eligibility Data Agent as graph-only."
            )
        print(
            f"  graph Data Agent exists: {GRAPH_DATA_AGENT_NAME} ({data_agent_id})"
        )
        return data_agent_id

    create = requests.post(
        agents_url,
        headers=_headers(),
        json={
            "displayName": GRAPH_DATA_AGENT_NAME,
            "description": (
                "AMC IQ graph-only Fabric Data Agent over the verified oncology GraphModel."
            ),
        },
        timeout=HTTP_TIMEOUT,
    )
    _wait_for_operation(create)

    for _ in range(20):
        time.sleep(2)
        listing = requests.get(
            agents_url,
            headers=_headers(),
            timeout=HTTP_TIMEOUT,
        )
        listing.raise_for_status()
        matches = [
            item
            for item in listing.json().get("value", [])
            if item.get("displayName") == GRAPH_DATA_AGENT_NAME
        ]
        if len(matches) > 1:
            raise RuntimeError(
                f"Created duplicate Data Agents named {GRAPH_DATA_AGENT_NAME!r}."
            )
        if matches and matches[0].get("id"):
            data_agent_id = matches[0]["id"]
            if data_agent_id == ELIGIBILITY_DATA_AGENT_ID:
                raise RuntimeError(
                    "Fabric returned the protected eligibility Data Agent for graph creation."
                )
            print(
                f"  graph Data Agent created: {GRAPH_DATA_AGENT_NAME} "
                f"({data_agent_id})"
            )
            return data_agent_id
    raise RuntimeError(
        f"Data Agent {GRAPH_DATA_AGENT_NAME!r} did not appear after creation."
    )


def _source_references_graph(source: dict[str, Any], graph_id: str) -> bool:
    return (
        source.get("itemReference", {}).get("itemId") == graph_id
        or source.get("id") == graph_id
    )


def _list_data_agent_sources(base: str) -> list[dict[str, Any]]:
    source_response = requests.get(
        f"{base}/staging/datasources",
        headers=_headers(),
        timeout=HTTP_TIMEOUT,
    )
    source_response.raise_for_status()
    return source_response.json().get("value", [])


def _reconcile_graph_source(base: str, graph_id: str) -> str:
    _require_data_agent_mutation_enabled()
    sources = _list_data_agent_sources(base)
    for source in sources:
        if not source.get("id"):
            raise RuntimeError(
                "Data Agent staging datasource did not include an id; refusing to reconcile."
            )

    graph_sources = [
        source for source in sources if _source_references_graph(source, graph_id)
    ]
    keep_source = graph_sources[0] if graph_sources else None
    remove_sources = [
        source
        for source in sources
        if source is not keep_source
    ]
    for source in remove_sources:
        remove = requests.delete(
            f"{base}/staging/datasources/{source['id']}",
            headers=_headers(),
            timeout=HTTP_TIMEOUT,
        )
        _wait_for_operation(remove)
        print(f"  removed non-graph staging datasource {source['id']}.")

    if not keep_source:
        add_source = requests.post(
            f"{base}/staging/datasources",
            headers=_headers(),
            json={
                "type": "FabricItem",
                "itemReference": {
                    "referenceType": "ById",
                    "itemId": graph_id,
                    "workspaceId": WS,
                },
            },
            timeout=HTTP_TIMEOUT,
        )
        _wait_for_operation(add_source)
        print("  GraphModel added to the graph Data Agent.")
        for _ in range(20):
            time.sleep(2)
            refreshed_sources = _list_data_agent_sources(base)
            graph_source = next(
                (
                    source
                    for source in refreshed_sources
                    if _source_references_graph(source, graph_id)
                ),
                None,
            )
            if graph_source:
                break
        if not graph_source:
            raise RuntimeError("GraphModel datasource did not appear after attachment.")
    else:
        graph_source = keep_source
        print("  GraphModel already attached to the graph Data Agent.")

    final_sources = _list_data_agent_sources(base)
    if len(final_sources) != 1 or not _source_references_graph(
        final_sources[0], graph_id
    ):
        raise RuntimeError(
            "Graph Data Agent reconciliation failed: expected exactly the verified "
            "GraphModel datasource."
        )
    datasource_id = final_sources[0].get("id")
    if not datasource_id:
        raise RuntimeError(
            "Verified GraphModel datasource did not include a datasource id."
        )
    return datasource_id


async def _ask_graph_data_agent_mcp(data_agent_id: str, question: str) -> str:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    mcp_url = (
        f"{FABRIC}/mcp/workspaces/{WS}/dataagents/{data_agent_id}/agent"
    )
    headers = {"Authorization": f"Bearer {FABRIC_TOKEN}"}
    async with streamablehttp_client(mcp_url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            if not tools.tools:
                raise RuntimeError("Graph Data Agent MCP endpoint exposed no tools.")
            tool = tools.tools[0]
            properties = (tool.inputSchema or {}).get("properties", {})
            argument = next(iter(properties), "userQuestion")
            result = await session.call_tool(tool.name, {argument: question})
            answer = "".join(
                getattr(block, "text", "") for block in result.content
            ).strip()
            if not answer:
                raise RuntimeError(
                    f"Graph Data Agent MCP returned an empty answer for {question!r}."
                )
            return answer


async def _verify_graph_data_agent_mcp_async(data_agent_id: str) -> None:
    failures = []
    for question, expected_markers in GRAPH_MCP_CHECKS:
        answer = await _ask_graph_data_agent_mcp(data_agent_id, question)
        missing = [
            marker
            for marker in expected_markers
            if marker.casefold() not in answer.casefold()
        ]
        if missing:
            failures.append(f"{question!r} missing {missing}")
    if failures:
        raise RuntimeError(
            "Graph Data Agent MCP verification failed: " + "; ".join(failures)
        )


def verify_graph_data_agent_mcp(data_agent_id: str) -> bool:
    if importlib.util.find_spec("mcp") is None:
        print("  MCP verification skipped: Python package 'mcp' is not installed.")
        return False
    last_error: Exception | None = None
    for attempt in range(1, 7):
        try:
            asyncio.run(_verify_graph_data_agent_mcp_async(data_agent_id))
            print("  Graph Data Agent MCP verification returned all expected markers.")
            return True
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < 6:
                print(
                    f"  MCP verification attempt {attempt}/6 failed; "
                    "waiting for the published agent to become ready."
                )
                time.sleep(10)
    raise RuntimeError(
        "Graph Data Agent MCP verification failed after 6 attempts."
    ) from last_error


def provision_graph_data_agent(graph_id: str) -> str:
    _require_data_agent_mutation_enabled()
    data_agent_id = _get_or_create_graph_data_agent()
    base = f"{FABRIC}/workspaces/{WS}/dataAgents/{data_agent_id}"
    datasource_id = _reconcile_graph_source(base, graph_id)

    source_settings = requests.patch(
        f"{base}/staging/datasources/{datasource_id}",
        headers=_headers(),
        json={
            "description": GRAPH_SOURCE_DESCRIPTION,
            "instructions": GRAPH_SOURCE_INSTRUCTIONS,
        },
        timeout=HTTP_TIMEOUT,
    )
    _wait_for_operation(source_settings)
    print("  GraphModel routing description and NL2GQL instructions updated.")
    sync_graph_few_shots(base, datasource_id)

    settings = requests.patch(
        f"{base}/staging/settings",
        headers=_headers(),
        json={"aiInstructions": GRAPH_AGENT_INSTRUCTIONS},
        timeout=HTTP_TIMEOUT,
    )
    _wait_for_operation(settings)
    publish = requests.post(
        f"{base}/staging/publish",
        headers=_headers(),
        json={
            "publishedDescription": (
                "AMC IQ graph-only Fabric Data Agent: verified GraphModel NL2GQL"
            )
        },
        timeout=HTTP_TIMEOUT,
    )
    _wait_for_operation(publish)
    print("  graph-only Data Agent published with the verified GraphModel source.")
    verify_graph_data_agent_mcp(data_agent_id)
    return data_agent_id


def main() -> None:
    print(f"Building Fabric GraphModel definition for {GRAPH_MODEL_NAME}")
    public_definition = build_public_definition()
    print(
        f"  entities={len(ontology.ENTITIES)} "
        f"relationships={len(ontology.RELATIONSHIPS)}"
    )
    graph_id = get_or_create_graph(public_definition)
    refresh_graph(graph_id)
    verify_graph(graph_id)
    if os.environ.get("ATTACH_GRAPH_TO_DA") == "1":
        provision_graph_data_agent(graph_id)
    else:
        print(
            "  graph Data Agent provisioning skipped. "
            "Set ATTACH_GRAPH_TO_DA=1 to enable."
        )
    print(f"Done. GraphModel id: {graph_id}")


if __name__ == "__main__":
    main()
