"""Recover the dedicated AMC IQ Fabric Data Agents with delegated device-code auth.

The companion PowerShell script temporarily grants this public client
``Item.ReadWrite.All`` (needed to create a Data Agent) and
``DataAgent.ReadWrite.All`` (needed to configure and publish one), while retaining
``Lakehouse.Read.All`` and ``SQLEndpoint.Read.All`` to enumerate the eligibility
datasource schema and table metadata. It removes the bootstrap-only
``Item.ReadWrite.All`` grant after this program succeeds.

Access tokens are held only in process memory. This module deliberately does not
read ``FABRIC_TOKEN`` or import any repository module that does so at import time.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import importlib.util
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_DEFAULT_SCOPE = "https://api.fabric.microsoft.com/.default"
DEFAULT_WORKSPACE_ID = os.getenv("FABRIC_WORKSPACE_ID", "").strip()
ELIGIBILITY_AGENT_ID = os.getenv("FABRIC_ELIGIBILITY_AGENT_ID", "").strip()
LAKEHOUSE_ID = os.getenv("FABRIC_LAKEHOUSE_ID", "").strip()
GRAPH_MODEL_ID = os.getenv("FABRIC_GRAPH_MODEL_ID", "").strip()
GRAPH_AGENT_NAME = os.getenv("FABRIC_GRAPH_AGENT_NAME", "").strip()
MUTATION_CONFIRMATION = "RECOVER"
REQUIRED_BOOTSTRAP_SCOPES = {
    "DataAgent.ReadWrite.All",
    "Item.ReadWrite.All",
    "Lakehouse.Read.All",
    "SQLEndpoint.Read.All",
    "Workspace.Read.All",
}
REQUIRED_ELIGIBILITY_TABLES = frozenset(
    {
        "adverse_events",
        "amendments",
        "biomarkers",
        "comorbidities",
        "consent",
        "coordinator_workload",
        "labs",
        "patient_registry",
        "people",
        "recist_assessments",
        "scheduling_slots",
        "sites",
        "treatment_history",
        "trial_criteria",
        "trial_enrollment",
        "trials",
    }
)

ELIGIBILITY_INSTRUCTIONS = (
    "Answer questions about synthetic oncology patients and clinical-trial operations "
    "using only the selected Lakehouse tables. Use patient_registry for demographics, "
    "ECOG, diagnosis, stage, and care-team ownership; labs for dated laboratory values "
    "(the latest CrCl is the most recent row where lab_type='CrCl_CKD-EPI'); "
    "treatment_history for prior therapies; biomarkers for molecular findings; trials "
    "and trial_criteria for eligibility requirements; amendments for criterion changes; "
    "trial_enrollment for screening state; and coordinator_workload and scheduling_slots "
    "for operational questions. Patient IDs look like PT-1042 and trial IDs like "
    "NCT99004324. Return precise values and dates, state when data is absent, and do not "
    "give medical advice."
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
    "Always use AS aliases for every returned property. Do not invent nodes, edges, or "
    "relationships that are not present in the GraphModel."
)

GRAPH_AGENT_INSTRUCTIONS = (
    "Use the single attached AMC IQ GraphModel for supported oncology relationship and "
    "traversal questions. Generate GQL over patient-to-trial screening, trial criteria, "
    "biomarkers, longitudinal labs, treatments, amendments, care-team owners, and trial "
    "sites. Use exact identifiers; patient IDs look like PT-1042 and trial IDs like "
    "NCT99004324. Return precise values and dates. Do not claim access to Lakehouse, "
    "semantic model, or ontology sources. Do not give medical advice."
)

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

GRAPH_MCP_CHECKS = (
    ("Which trial is patient PT-1042 screened for?", ("NCT99004324",)),
    (
        "Which criteria are required by trial NCT99004324?",
        ("NCT99004324-REN", "NCT99004324-BIO"),
    ),
    ("Who treats patient PT-1042?", ("Dr. Priya Anand",)),
)


class RecoveryError(RuntimeError):
    """Raised when recovery cannot establish or prove the required state."""

    def __init__(self, message: str, *, mutation_started: bool = False) -> None:
        super().__init__(message)
        self.mutation_started = mutation_started


def acquire_token_device_code(
    tenant_id: str,
    client_id: str,
    *,
    cache_path: Path | None = None,
    app: Any | None = None,
    print_fn: Callable[[str], None] = print,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> str:
    """Acquire a Fabric token silently from encrypted storage, then use device code."""
    cache = None
    if app is None:
        try:
            import msal
            from msal_extensions import (
                PersistedTokenCache,
                build_encrypted_persistence,
            )
        except ImportError as exc:
            raise RecoveryError(
                "The 'msal' and 'msal-extensions' packages are required for encrypted "
                "delegated-token caching."
            ) from exc
        resolved_cache_path = cache_path or _default_token_cache_path(client_id)
        resolved_cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache = PersistedTokenCache(
            build_encrypted_persistence(str(resolved_cache_path))
        )
        app = msal.PublicClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            token_cache=cache,
        )

    if hasattr(app, "get_accounts") and hasattr(app, "acquire_token_silent"):
        for account in app.get_accounts():
            for attempt in range(1, 13):
                result = app.acquire_token_silent(
                    [FABRIC_DEFAULT_SCOPE],
                    account=account,
                    force_refresh=True,
                )
                if result and result.get("access_token"):
                    token = str(result["access_token"])
                    missing = (
                        _missing_bootstrap_token_scopes(token)
                        if cache is not None
                        else []
                    )
                    _evict_cached_access_tokens(cache)
                    if not missing:
                        return token
                    if attempt < 12:
                        if attempt == 1:
                            print_fn(
                                "Waiting for expanded Fabric delegated scopes to propagate."
                            )
                        sleep_fn(5)
                        continue
                    break
                if result and result.get("error") not in {
                    "interaction_required",
                    "consent_required",
                    "invalid_grant",
                }:
                    detail = result.get("error_description") or result.get("error")
                    raise RecoveryError(f"Silent token acquisition failed: {detail}")
                break

    flow = app.initiate_device_flow(scopes=[FABRIC_DEFAULT_SCOPE])
    if "user_code" not in flow:
        detail = flow.get("error_description") or flow.get("error") or "unknown error"
        raise RecoveryError(f"Failed to start device-code authentication: {detail}")
    print_fn(flow.get("message") or "Complete the Microsoft device-code sign-in.")
    sys.stdout.flush()
    result = app.acquire_token_by_device_flow(flow)
    token = result.get("access_token")
    if not token:
        detail = (
            result.get("error_description")
            or result.get("error")
            or "unknown authentication error"
        )
        raise RecoveryError(f"Device-code authentication failed: {detail}")
    token = str(token)
    if cache is not None:
        _assert_bootstrap_token_scopes(token)
    _evict_cached_access_tokens(cache)
    return token


def _assert_bootstrap_token_scopes(token: str) -> None:
    missing = _missing_bootstrap_token_scopes(token)
    if missing:
        raise RecoveryError(
            "Fabric access token is missing bootstrap scopes: " + ", ".join(missing)
        )


def _missing_bootstrap_token_scopes(token: str) -> list[str]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        scopes = set(str(claims.get("scp", "")).split())
    except (IndexError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RecoveryError("Fabric access token claims could not be inspected.") from exc
    return sorted(REQUIRED_BOOTSTRAP_SCOPES - scopes)


def _evict_cached_access_tokens(cache: Any | None) -> None:
    if cache is None:
        return
    for entry in list(cache.find("AccessToken")):
        cache.remove_at(entry)


def _default_token_cache_path(client_id: str) -> Path:
    cache_root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".cache"))
    return cache_root / "AMC IQ" / "msal" / f"{client_id}.bin"


class FabricClient:
    """Small Fabric REST client with bounded retries and LRO handling."""

    def __init__(
        self,
        token: str,
        *,
        request_timeout: float = 60,
        operation_timeout: float = 600,
        session: requests.Session | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token:
            raise ValueError("A non-empty in-memory token is required.")
        self._token = token
        self.request_timeout = request_timeout
        self.operation_timeout = operation_timeout
        self.session = session or requests.Session()
        self.sleep_fn = sleep_fn
        self.mutation_started = False

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("https://", "http://")):
            return path_or_url
        return urljoin(f"{FABRIC_API}/", path_or_url.lstrip("/"))

    @staticmethod
    def _error_detail(response: requests.Response) -> str:
        request_id = response.headers.get("request-id") or response.headers.get(
            "x-ms-request-id"
        )
        code = None
        message = None
        try:
            payload = response.json()
            error = payload.get("error", payload)
            if isinstance(error, dict):
                code = error.get("errorCode") or error.get("code")
                message = error.get("message")
        except (ValueError, AttributeError):
            pass
        parts = [f"HTTP {response.status_code}"]
        if code:
            parts.append(str(code))
        if message:
            parts.append(str(message)[:500])
        if request_id:
            parts.append(f"request-id={request_id}")
        return ": ".join(parts)

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        expected: Sequence[int] = (200,),
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> requests.Response:
        url = self._url(path_or_url)
        method = method.upper()
        idempotent = method in {"GET", "HEAD", "OPTIONS", "PUT", "PATCH", "DELETE"}
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            self.mutation_started = True
        last_response: requests.Response | None = None
        for attempt in range(1, 6):
            try:
                response = self.session.request(
                    method,
                    url,
                    headers=self.headers,
                    params=params,
                    json=json_body,
                    timeout=self.request_timeout,
                )
            except requests.RequestException as exc:
                if not idempotent or attempt == 5:
                    raise RecoveryError(
                        f"{method} {url} failed after {attempt} attempts: "
                        f"{type(exc).__name__}"
                    ) from exc
                self.sleep_fn(min(2**attempt, 15))
                continue
            last_response = response
            if response.status_code in expected:
                return response
            retriable_status = response.status_code == 429 or (
                idempotent and 500 <= response.status_code < 600
            )
            if retriable_status:
                if attempt < 5:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        delay = float(retry_after) if retry_after else min(2**attempt, 15)
                    except ValueError:
                        delay = min(2**attempt, 15)
                    self.sleep_fn(max(delay, 0))
                    continue
            raise RecoveryError(
                f"{method} {url} failed: {self._error_detail(response)}"
            )
        assert last_response is not None
        raise RecoveryError(
            f"{method} {url} failed: {self._error_detail(last_response)}"
        )

    def request_json(
        self,
        method: str,
        path_or_url: str,
        *,
        expected: Sequence[int] = (200,),
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        wait_lro: bool = False,
    ) -> dict[str, Any]:
        statuses = tuple(dict.fromkeys((*expected, 202))) if wait_lro else expected
        response = self.request(
            method,
            path_or_url,
            expected=statuses,
            params=params,
            json_body=json_body,
        )
        if response.status_code == 202:
            return self._wait_for_operation(response)
        if response.status_code == 204 or not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise RecoveryError(
                f"{method} {self._url(path_or_url)} returned invalid JSON."
            ) from exc
        return payload if isinstance(payload, dict) else {}

    def _wait_for_operation(self, response: requests.Response) -> dict[str, Any]:
        location = response.headers.get("Location")
        if not location:
            raise RecoveryError("Fabric returned 202 without an operation Location header.")
        deadline = time.monotonic() + self.operation_timeout
        while time.monotonic() < deadline:
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 2.0
            except ValueError:
                delay = 2.0
            self.sleep_fn(max(delay, 0))
            response = self.request("GET", location, expected=(200, 202))
            if response.status_code == 202:
                continue
            payload = response.json() if response.content else {}
            status = str(payload.get("status", "")).casefold()
            if status in {"failed", "cancelled"}:
                raise RecoveryError(
                    "Fabric long-running operation failed: "
                    + json.dumps(payload.get("error", {}), ensure_ascii=True)[:500]
                )
            if status in {"notstarted", "running"}:
                continue
            return payload if isinstance(payload, dict) else {}
        raise RecoveryError(
            f"Fabric long-running operation exceeded {self.operation_timeout:g}s."
        )

    def list_values(
        self, path_or_url: str, *, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        next_url: str | None = path_or_url
        next_params = params
        while next_url:
            payload = self.request_json("GET", next_url, params=next_params)
            page = payload.get("value", [])
            if not isinstance(page, list):
                raise RecoveryError(f"Fabric list response for {next_url} has no value array.")
            values.extend(item for item in page if isinstance(item, dict))
            next_url = payload.get("continuationUri")
            next_params = None
        return values


def _source_item_id(source: dict[str, Any], reference_key: str) -> str | None:
    reference = source.get(reference_key)
    if isinstance(reference, dict) and reference.get("itemId"):
        return str(reference["itemId"])
    return None


def _list_sources(
    client: FabricClient, workspace_id: str, agent_id: str
) -> list[dict[str, Any]]:
    return client.list_values(
        f"/workspaces/{workspace_id}/dataAgents/{agent_id}/staging/datasources"
    )


def reconcile_single_source(
    client: FabricClient,
    workspace_id: str,
    agent_id: str,
    *,
    source_type: str,
    item_id: str,
) -> str:
    """Reconcile a staging agent to exactly one requested Fabric datasource."""
    if source_type == "LakehouseTables":
        reference_key = "lakehouseReference"
    elif source_type == "FabricItem":
        reference_key = "itemReference"
    else:
        raise ValueError(f"Unsupported source type: {source_type}")

    sources_path = (
        f"/workspaces/{workspace_id}/dataAgents/{agent_id}/staging/datasources"
    )
    sources = _list_sources(client, workspace_id, agent_id)
    if any(not source.get("id") for source in sources):
        raise RecoveryError(
            "A staging datasource has no id; refusing a destructive reconciliation."
        )
    matches = [
        source
        for source in sources
        if source.get("type") == source_type
        and _source_item_id(source, reference_key) == item_id
    ]
    keep = matches[0] if matches else None
    for source in sources:
        if source is keep:
            continue
        client.request_json(
            "DELETE",
            f"{sources_path}/{source['id']}",
            expected=(200, 204),
            wait_lro=True,
        )
        print(f"  removed staging datasource {source['id']}")

    if keep is None:
        body = {
            "type": source_type,
            reference_key: {
                "referenceType": "ById",
                "itemId": item_id,
                "workspaceId": workspace_id,
            },
        }
        created = client.request_json(
            "POST",
            sources_path,
            expected=(200, 201),
            json_body=body,
            wait_lro=True,
        )
        if created.get("id"):
            keep = created
        else:
            deadline = time.monotonic() + client.operation_timeout
            while time.monotonic() < deadline:
                client.sleep_fn(2)
                refreshed = _list_sources(client, workspace_id, agent_id)
                keep = next(
                    (
                        source
                        for source in refreshed
                        if source.get("type") == source_type
                        and _source_item_id(source, reference_key) == item_id
                    ),
                    None,
                )
                if keep:
                    break
        if keep is None:
            raise RecoveryError(f"{source_type} datasource did not appear after creation.")
        print(f"  attached {source_type} item {item_id}")

    final_sources = _list_sources(client, workspace_id, agent_id)
    if (
        len(final_sources) != 1
        or final_sources[0].get("type") != source_type
        or _source_item_id(final_sources[0], reference_key) != item_id
    ):
        raise RecoveryError(
            f"Datasource reconciliation failed: expected exactly one {source_type} "
            f"source for item {item_id}."
        )
    datasource_id = final_sources[0].get("id")
    if not datasource_id:
        raise RecoveryError("The reconciled datasource has no id.")
    return str(datasource_id)


def _walk_elements(
    client: FabricClient,
    workspace_id: str,
    agent_id: str,
    datasource_id: str,
) -> list[dict[str, Any]]:
    path = (
        f"/workspaces/{workspace_id}/dataAgents/{agent_id}/staging/"
        f"datasources/{datasource_id}/elements"
    )
    queue: list[str | None] = [None]
    expanded: set[str] = set()
    seen_elements: set[str] = set()
    found: list[dict[str, Any]] = []
    while queue:
        root_id = queue.pop(0)
        params = {"rootId": root_id} if root_id else None
        for element in client.list_values(path, params=params):
            element_id = element.get("id")
            identity = str(element_id) if element_id else json.dumps(
                element, sort_keys=True, ensure_ascii=True
            )
            if identity in seen_elements:
                continue
            seen_elements.add(identity)
            found.append(element)
            if (
                element.get("hasSubElements")
                and element.get("type") not in {"Table", "ExternalTable"}
                and element_id
                and element_id not in expanded
            ):
                expanded.add(str(element_id))
                queue.append(str(element_id))
    return found


def select_all_lakehouse_tables(
    client: FabricClient,
    workspace_id: str,
    agent_id: str,
    datasource_id: str,
) -> list[str]:
    elements_path = (
        f"/workspaces/{workspace_id}/dataAgents/{agent_id}/staging/"
        f"datasources/{datasource_id}/elements"
    )
    tables: list[dict[str, Any]] = []
    observed_states: set[str] = set()
    for attempt in range(1, 13):
        elements = _walk_elements(client, workspace_id, agent_id, datasource_id)
        observed_states.update(
            str(element.get("state"))
            for element in elements
            if element.get("state")
        )
        tables = [
            element
            for element in elements
            if element.get("type") in {"Table", "ExternalTable"}
            and element.get("state", "Available") == "Available"
        ]
        if tables:
            break
        if attempt < 12:
            if attempt == 1:
                print("  waiting for the Lakehouse schema tree to become available")
            client.sleep_fn(5)
    if not tables:
        states = ", ".join(sorted(observed_states)) or "none"
        raise RecoveryError(
            "The Lakehouse datasource exposed no available tables after 60 seconds "
            f"(observed states: {states})."
        )
    available_names = {
        str(table.get("displayName") or "").strip() for table in tables
    }
    missing = sorted(REQUIRED_ELIGIBILITY_TABLES - available_names)
    if missing:
        raise RecoveryError(
            "The Lakehouse datasource is missing required eligibility tables: "
            + ", ".join(missing)
        )
    for table in tables:
        if not table.get("id"):
            raise RecoveryError("A Lakehouse table element has no id.")
        if table.get("isSelected") is not True:
            client.request_json(
                "PATCH",
                elements_path,
                params={"id": table["id"]},
                json_body={"isSelected": True},
            )

    expected_count = len(tables)
    selected: list[str] = []
    for attempt in range(1, 13):
        verified = _walk_elements(client, workspace_id, agent_id, datasource_id)
        selected = [
            str(element.get("displayName") or element.get("id"))
            for element in verified
            if element.get("type") in {"Table", "ExternalTable"}
            and element.get("state", "Available") == "Available"
            and element.get("isSelected") is True
        ]
        if len(selected) == expected_count:
            print(f"  selected {len(selected)} Lakehouse tables")
            return selected
        if attempt < 12:
            if attempt == 1:
                print(
                    "  waiting for Lakehouse table selections to propagate "
                    f"({len(selected)}/{expected_count} visible)"
                )
            client.sleep_fn(5)
    raise RecoveryError(
        f"Lakehouse table selection verification failed after 60 seconds: "
        f"selected {len(selected)} of {expected_count} available tables."
    )


def recover_eligibility_agent(
    client: FabricClient, workspace_id: str
) -> list[str]:
    print(f"Recovering eligibility Data Agent {ELIGIBILITY_AGENT_ID}")
    datasource_id = reconcile_single_source(
        client,
        workspace_id,
        ELIGIBILITY_AGENT_ID,
        source_type="LakehouseTables",
        item_id=LAKEHOUSE_ID,
    )
    tables = select_all_lakehouse_tables(
        client, workspace_id, ELIGIBILITY_AGENT_ID, datasource_id
    )
    base = f"/workspaces/{workspace_id}/dataAgents/{ELIGIBILITY_AGENT_ID}"
    client.request_json(
        "PATCH",
        f"{base}/staging/settings",
        json_body={"aiInstructions": ELIGIBILITY_INSTRUCTIONS},
        wait_lro=True,
    )
    client.request_json(
        "POST",
        f"{base}/staging/publish",
        json_body={
            "publishedDescription": (
                "AMC IQ eligibility agent recovered to its Lakehouse-only source."
            )
        },
        wait_lro=True,
    )
    print("  eligibility Data Agent published")
    return tables


def get_or_create_graph_agent(
    client: FabricClient, workspace_id: str
) -> str:
    def is_protected_eligibility_agent_id(agent_id: Any) -> bool:
        return (
            agent_id is not None
            and str(agent_id).strip().casefold() == ELIGIBILITY_AGENT_ID.casefold()
        )

    agents_path = f"/workspaces/{workspace_id}/dataAgents"
    matches = [
        agent
        for agent in client.list_values(agents_path)
        if agent.get("displayName") == GRAPH_AGENT_NAME
    ]
    if len(matches) > 1:
        raise RecoveryError(
            f"Expected at most one Data Agent named {GRAPH_AGENT_NAME!r}; "
            f"found {len(matches)}."
        )
    if matches:
        agent_id = matches[0].get("id")
        if not agent_id:
            raise RecoveryError(f"Data Agent {GRAPH_AGENT_NAME!r} has no id.")
        if is_protected_eligibility_agent_id(agent_id):
            raise RecoveryError(
                "Refusing to repurpose the protected eligibility Data Agent as graph-only."
            )
        print(f"Reusing graph Data Agent {agent_id}")
        return str(agent_id)

    print(
        "Creating graph Data Agent with temporary bootstrap elevation "
        "(Item.ReadWrite.All + DataAgent.ReadWrite.All)"
    )
    created = client.request_json(
        "POST",
        agents_path,
        expected=(200, 201),
        json_body={
            "displayName": GRAPH_AGENT_NAME,
            "description": (
                "AMC IQ graph-only Fabric Data Agent over the verified oncology GraphModel."
            ),
        },
        wait_lro=True,
    )
    if created.get("id"):
        agent_id = str(created["id"])
        if is_protected_eligibility_agent_id(agent_id):
            raise RecoveryError(
                "Fabric returned the protected eligibility Data Agent in the "
                "immediate graph create response."
            )
        return agent_id
    deadline = time.monotonic() + client.operation_timeout
    while time.monotonic() < deadline:
        client.sleep_fn(2)
        matches = [
            agent
            for agent in client.list_values(agents_path)
            if agent.get("displayName") == GRAPH_AGENT_NAME
        ]
        if len(matches) > 1:
            raise RecoveryError(f"Creation produced duplicate {GRAPH_AGENT_NAME!r} agents.")
        if matches and matches[0].get("id"):
            agent_id = str(matches[0]["id"])
            if is_protected_eligibility_agent_id(agent_id):
                raise RecoveryError(
                    "Fabric returned the protected eligibility Data Agent for graph creation."
                )
            return agent_id
    raise RecoveryError(f"Data Agent {GRAPH_AGENT_NAME!r} did not appear after creation.")


def sync_graph_few_shots(
    client: FabricClient,
    workspace_id: str,
    agent_id: str,
    datasource_id: str,
) -> None:
    path = (
        f"/workspaces/{workspace_id}/dataAgents/{agent_id}/staging/"
        f"datasources/{datasource_id}/fewshots"
    )
    existing = client.list_values(path)
    by_question: dict[str, dict[str, Any]] = {}
    for item in existing:
        question = item.get("question")
        item_id = item.get("id")
        if not item_id:
            raise RecoveryError("A graph few-shot has no id; refusing reconciliation.")
        if question not in GRAPH_FEW_SHOTS or question in by_question:
            client.request_json(
                "DELETE", f"{path}/{item_id}", expected=(200, 204)
            )
            continue
        by_question[str(question)] = item

    for question, query in GRAPH_FEW_SHOTS.items():
        current = by_question.get(question)
        body = {"question": question, "query": query}
        if current is None:
            client.request_json("POST", path, expected=(200, 201), json_body=body)
        elif (
            current.get("query") != query
            or current.get("validationStatus", {}).get("value") != "Valid"
        ):
            client.request_json(
                "PATCH", f"{path}/{current['id']}", json_body=body
            )

    deadline = time.monotonic() + min(client.operation_timeout, 300)
    last_invalid: dict[str, Any] = {}
    while time.monotonic() < deadline:
        current = client.list_values(path)
        desired = {
            str(item.get("question")): item
            for item in current
            if item.get("question") in GRAPH_FEW_SHOTS
        }
        last_invalid = {
            question: item.get("validationStatus", {}).get("reason", "unknown reason")
            for question, item in desired.items()
            if item.get("validationStatus", {}).get("value") == "Invalid"
        }
        if (
            len(current) == len(GRAPH_FEW_SHOTS)
            and set(desired) == set(GRAPH_FEW_SHOTS)
            and all(
                item.get("validationStatus", {}).get("value") == "Valid"
                and item.get("query") == GRAPH_FEW_SHOTS[question]
                for question, item in desired.items()
            )
        ):
            print(f"  synchronized {len(GRAPH_FEW_SHOTS)} validated graph few-shots")
            return
        client.sleep_fn(5)
    detail = (
        " Last invalid statuses: " + json.dumps(last_invalid, ensure_ascii=True)
        if last_invalid
        else ""
    )
    raise RecoveryError(
        f"Graph few-shot validation exceeded "
        f"{min(client.operation_timeout, 300):g} seconds.{detail}"
    )


async def _ask_graph_data_agent_mcp(
    token: str,
    workspace_id: str,
    agent_id: str,
    question: str,
    *,
    timeout_seconds: float,
) -> str:
    if importlib.util.find_spec("mcp") is None:
        raise RecoveryError(
            "The 'mcp' package is required because MCP response verification is mandatory."
        )
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def invoke() -> str:
        url = (
            f"{FABRIC_API}/mcp/workspaces/{workspace_id}/"
            f"dataagents/{agent_id}/agent"
        )
        async with streamablehttp_client(
            url, headers={"Authorization": f"Bearer {token}"}
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                if not tools.tools:
                    raise RecoveryError("The graph Data Agent MCP endpoint exposed no tools.")
                tool = tools.tools[0]
                properties = (tool.inputSchema or {}).get("properties", {})
                argument = next(iter(properties), "userQuestion")
                result = await session.call_tool(tool.name, {argument: question})
                answer = "".join(
                    getattr(block, "text", "") for block in result.content
                ).strip()
                if not answer:
                    raise RecoveryError(
                        f"The graph Data Agent MCP response was empty for {question!r}."
                    )
                return answer

    return await asyncio.wait_for(invoke(), timeout=timeout_seconds)


async def _verify_graph_mcp_once(
    token: str,
    workspace_id: str,
    agent_id: str,
    *,
    timeout_seconds: float,
) -> None:
    failures: list[str] = []
    for question, expected_markers in GRAPH_MCP_CHECKS:
        answer = await _ask_graph_data_agent_mcp(
            token,
            workspace_id,
            agent_id,
            question,
            timeout_seconds=timeout_seconds,
        )
        missing = [
            marker
            for marker in expected_markers
            if marker.casefold() not in answer.casefold()
        ]
        if missing:
            failures.append(f"{question!r} missing {missing}")
    if failures:
        raise RecoveryError("Graph MCP verification failed: " + "; ".join(failures))


def verify_graph_mcp(
    token: str,
    workspace_id: str,
    agent_id: str,
    *,
    timeout_seconds: float = 120,
    attempts: int = 6,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            asyncio.run(
                _verify_graph_mcp_once(
                    token,
                    workspace_id,
                    agent_id,
                    timeout_seconds=timeout_seconds,
                )
            )
            print("  graph Data Agent MCP responses verified")
            return
        except Exception as exc:  # noqa: BLE001 - retry preserves the final cause
            last_error = exc
            if attempt < attempts:
                print(
                    f"  MCP verification attempt {attempt}/{attempts} failed; "
                    "waiting for the published agent"
                )
                sleep_fn(10)
    raise RecoveryError(
        f"Graph MCP verification failed after {attempts} attempts."
    ) from last_error


def recover_graph_agent(
    client: FabricClient,
    token: str,
    workspace_id: str,
    *,
    mcp_timeout: float,
) -> str:
    agent_id = get_or_create_graph_agent(client, workspace_id)
    datasource_id = reconcile_single_source(
        client,
        workspace_id,
        agent_id,
        source_type="FabricItem",
        item_id=GRAPH_MODEL_ID,
    )
    base = f"/workspaces/{workspace_id}/dataAgents/{agent_id}"
    client.request_json(
        "PATCH",
        f"{base}/staging/datasources/{datasource_id}",
        json_body={
            "description": GRAPH_SOURCE_DESCRIPTION,
            "instructions": GRAPH_SOURCE_INSTRUCTIONS,
        },
        wait_lro=True,
    )
    sync_graph_few_shots(client, workspace_id, agent_id, datasource_id)
    client.request_json(
        "PATCH",
        f"{base}/staging/settings",
        json_body={"aiInstructions": GRAPH_AGENT_INSTRUCTIONS},
        wait_lro=True,
    )
    client.request_json(
        "POST",
        f"{base}/staging/publish",
        json_body={
            "publishedDescription": (
                "AMC IQ graph-only Data Agent recovered with its verified GraphModel."
            )
        },
        wait_lro=True,
    )
    print("  graph Data Agent published")
    verify_graph_mcp(
        token,
        workspace_id,
        agent_id,
        timeout_seconds=mcp_timeout,
    )
    return agent_id


def run_recovery(
    *,
    tenant_id: str,
    client_id: str,
    workspace_id: str,
    confirmation: str,
    request_timeout: float,
    operation_timeout: float,
    mcp_timeout: float,
    token_acquirer: Callable[..., str] = acquire_token_device_code,
) -> dict[str, Any]:
    if confirmation != MUTATION_CONFIRMATION:
        raise RecoveryError(
            f"Mutations are disabled. Pass --confirm-mutations {MUTATION_CONFIRMATION}."
        )
    print(
        "TEMPORARY BOOTSTRAP ELEVATION ACTIVE: Item.ReadWrite.All + "
        "DataAgent.ReadWrite.All + Lakehouse.Read.All + SQLEndpoint.Read.All. "
        "The wrapper must remove Item.ReadWrite.All after success."
    )
    token = token_acquirer(tenant_id, client_id)
    client = FabricClient(
        token,
        request_timeout=request_timeout,
        operation_timeout=operation_timeout,
    )
    try:
        tables = recover_eligibility_agent(client, workspace_id)
        graph_agent_id = recover_graph_agent(
            client,
            token,
            workspace_id,
            mcp_timeout=mcp_timeout,
        )
    except RecoveryError as exc:
        exc.mutation_started = client.mutation_started
        raise
    return {
        "eligibilityAgentId": ELIGIBILITY_AGENT_ID,
        "selectedLakehouseTables": tables,
        "graphAgentId": graph_agent_id,
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument(
        "--workspace-id",
        default=DEFAULT_WORKSPACE_ID or None,
        required=not bool(DEFAULT_WORKSPACE_ID),
    )
    parser.add_argument(
        "--eligibility-agent-id",
        default=ELIGIBILITY_AGENT_ID or None,
        required=not bool(ELIGIBILITY_AGENT_ID),
    )
    parser.add_argument(
        "--lakehouse-id",
        default=LAKEHOUSE_ID or None,
        required=not bool(LAKEHOUSE_ID),
    )
    parser.add_argument(
        "--graph-model-id",
        default=GRAPH_MODEL_ID or None,
        required=not bool(GRAPH_MODEL_ID),
    )
    parser.add_argument(
        "--graph-agent-name",
        default=GRAPH_AGENT_NAME or None,
        required=not bool(GRAPH_AGENT_NAME),
    )
    parser.add_argument(
        "--confirm-mutations",
        metavar="RECOVER",
        help=f"Required explicit mutation confirmation: {MUTATION_CONFIRMATION}",
    )
    parser.add_argument("--request-timeout", type=float, default=60)
    parser.add_argument("--operation-timeout", type=float, default=600)
    parser.add_argument("--mcp-timeout", type=float, default=120)
    return parser.parse_args(argv)


def _configure_resource_ids(args: argparse.Namespace) -> None:
    global ELIGIBILITY_AGENT_ID, LAKEHOUSE_ID, GRAPH_MODEL_ID, GRAPH_AGENT_NAME
    ELIGIBILITY_AGENT_ID = args.eligibility_agent_id
    LAKEHOUSE_ID = args.lakehouse_id
    GRAPH_MODEL_ID = args.graph_model_id
    GRAPH_AGENT_NAME = args.graph_agent_name


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    _configure_resource_ids(args)
    try:
        result = run_recovery(
            tenant_id=args.tenant_id,
            client_id=args.client_id,
            workspace_id=args.workspace_id,
            confirmation=args.confirm_mutations,
            request_timeout=args.request_timeout,
            operation_timeout=args.operation_timeout,
            mcp_timeout=args.mcp_timeout,
        )
    except RecoveryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1 if exc.mutation_started else 2
    print(
        "Recovery succeeded: "
        f"eligibility={result['eligibilityAgentId']} "
        f"graph={result['graphAgentId']} "
        f"tables={len(result['selectedLakehouseTables'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
