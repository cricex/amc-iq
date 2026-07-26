"""Provision the REAL Fabric IQ Ontology for the AMC IQ proof-of-concept (no GUI, pure REST).

The Fabric IQ Ontology is a first-class Fabric item (type "Ontology") in the Fabric IQ (preview)
workload. This script defines the precision-oncology domain ontology (typed entity types,
relationship types, and Lakehouse data bindings) and pushes it to the ontology item via the
updateDefinition REST API, so the ontology is live and portal-visible and can be consumed by a
Fabric Data Agent (NL2Ontology).

It maps the portable contract in data/ontology/ontology.yaml onto real Fabric constructs and binds
each entity type to the corresponding Delta table in the amciq_fabric_oncology_clinical Lakehouse.

Synthetic data only. No PHI. Not clinical decision support.

Environment:
  FABRIC_TOKEN   token for https://api.fabric.microsoft.com/.default (Contributor on the workspace)
  STORAGE_TOKEN  token for https://storage.azure.com/.default (to read Delta schemas from OneLake)
  FABRIC_WS      workspace id       (required)
  FABRIC_LH      lakehouse id       (required)
  ONTOLOGY_ID    existing ontology item id (optional; otherwise looked up/created by name)
  ONLY_ENTITY    optional: build just one entity type (validation mode)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid

import requests
from live_environment import required_environment

FABRIC = "https://api.fabric.microsoft.com/v1"
ONELAKE = "https://onelake.dfs.fabric.microsoft.com"

WS = required_environment("FABRIC_WS")
LH = required_environment("FABRIC_LH")
ONTOLOGY_NAME = required_environment("ONTOLOGY_NAME")
DATA_AGENT_NAME = required_environment("DATA_AGENT_NAME")
FABRIC_TOKEN = required_environment("FABRIC_TOKEN")
STORAGE_TOKEN = required_environment("STORAGE_TOKEN")
# ONTOLOGY_ID may be provided to target an existing item; otherwise it is created/looked up by name.
ONT = os.environ.get("ONTOLOGY_ID", "")
ONLY_ENTITY = os.environ.get("ONLY_ENTITY")

# Delta type -> Fabric Ontology property valueType. Allowed ontology valueTypes are:
# String, Boolean, DateTime, Object, BigInt, Double (there is no "Integer"). Ontology does not
# support Decimal, so double/float/decimal all map to Double.
TYPE_MAP = {
    "string": "String",
    "integer": "BigInt",
    "long": "BigInt",
    "short": "BigInt",
    "byte": "BigInt",
    "double": "Double",
    "float": "Double",
    "decimal": "Double",
    "boolean": "Boolean",
    "date": "DateTime",
    "timestamp": "DateTime",
}

from dataclasses import dataclass


@dataclass(frozen=True)
class EntitySpec:
    table: str
    keys: tuple[str, ...]      # columns that together uniquely identify an instance
    display: str               # column used as the human-readable node label
    description: str           # entity-type description (semanticEnrichment) for AI agents / NL2Ontology
    synonyms: tuple[str, ...]  # entity-type synonyms (semanticEnrichment)
    props: dict[str, str]      # column -> property description (semanticEnrichment)


@dataclass(frozen=True)
class RelSpec:
    name: str
    src: str
    tgt: str
    table: str                       # relationship (fact/dimension) table carrying both keys
    src_map: dict[str, str]          # rel-table column -> source entity key column
    tgt_map: dict[str, str]          # rel-table column -> target entity key column


# Entity type -> spec. Keys are proper (composite where natural) so the graph has unique instances;
# `display` gives each node a human-readable label; `description`/`synonyms`/`props` populate the
# ontology semanticEnrichment metadata so AI agents and NL2Ontology get rich business context.
ENTITIES: dict[str, EntitySpec] = {
    "Patient": EntitySpec(
        "patient_registry", ("patient_id",), "display_name",
        "An oncology patient in the AMC IQ precision-oncology cohort, with demographics, diagnosis, "
        "stage, ECOG performance status, care-team owners, and the scenario eligibility archetype. The root of the "
        "clinical-trial eligibility graph.",
        ("subject", "case", "individual"),
        {
            "patient_id": "Unique patient identifier, for example PT-1042. Entity key.",
            "mrn": "Medical record number.",
            "display_name": "Patient display name, used as the graph node label.",
            "sex": "Biological sex.",
            "age": "Patient age in years.",
            "ecog_ps": "ECOG performance status (0-4); lower is better function. Compared against a trial's ecog_max.",
            "primary_diagnosis": "Primary cancer diagnosis in clinical text.",
            "diagnosis_icd10": "ICD-10 code for the primary diagnosis.",
            "cancer_type": "Normalized cancer type (for example NSCLC) used for trial matching.",
            "stage": "Cancer stage at the staging date.",
            "staging_date": "Date the cancer stage was assigned.",
            "treating_oncologist_id": "person_id of the treating oncologist / principal investigator.",
            "coordinator_id": "person_id of the trial coordinator who owns this patient's screening tasks.",
            "archetype": "Scenario eligibility archetype: clear_eligible, borderline, hard_excluded, biomarker_mismatch, or treatment_naive_hold.",
        },
    ),
    "Trial": EntitySpec(
        "trials", ("trial_id",), "short_title",
        "A clinical trial the cancer center can screen patients for, including its condition, phase, status, "
        "site, and structured eligibility thresholds (maximum ECOG, minimum CrCl, required biomarker).",
        ("study", "clinical trial", "protocol"),
        {
            "trial_id": "Unique trial identifier, for example NCT99004324. Entity key.",
            "short_title": "Short human-readable trial title, used as the graph node label.",
            "condition": "Condition or indication the trial targets.",
            "cancer_type": "Cancer type the trial enrolls (for example NSCLC; Solid tumor matches any).",
            "phase": "Clinical trial phase.",
            "status": "Trial operational status, for example Open or Recruiting.",
            "site_id": "site_id where the trial runs.",
            "ecog_max": "Maximum ECOG performance status allowed for eligibility.",
            "crcl_min": "Minimum required creatinine clearance (CrCl_CKD-EPI, mL/min) for eligibility.",
            "biomarker_required": "Biomarker required for inclusion, for example EGFR exon 20 insertion.",
            "latest_protocol": "Latest protocol or amendment version in effect.",
        },
    ),
    "Criterion": EntitySpec(
        "trial_criteria", ("trial_id", "criterion_id"), "criterion_id",
        "A single inclusion or exclusion eligibility criterion of a trial, expressed as a structured rule "
        "(references_entity, param, comparator, value) so eligibility is a relationship traversal, not free text.",
        ("eligibility criterion", "requirement", "rule"),
        {
            "trial_id": "Trial this criterion belongs to. Part of the entity key.",
            "criterion_id": "Unique criterion identifier within the trial. Node label and part of the key.",
            "kind": "Whether this is an inclusion or an exclusion criterion.",
            "category": "Clinical category: diagnosis, biomarker, performance, renal, prior_therapy, hepatic, hematologic, or consent.",
            "description": "Human-readable statement of the criterion.",
            "references_entity": "The fact entity the criterion evaluates (Lab, Biomarker, Treatment, or Patient); enables relationship-aware matching.",
            "param": "The specific attribute evaluated, for example CrCl_CKD-EPI, ecog_ps, or drug_class.",
            "comparator": "Comparison operator, for example >=, <=, ==, present, or absent.",
            "value": "Threshold or required value the parameter is compared against.",
        },
    ),
    "Biomarker": EntitySpec(
        "biomarkers", ("patient_id", "marker"), "marker",
        "A tested molecular marker or variant for a patient (for example EGFR exon 20 insertion), with its gene, "
        "category, detection status, and assay method.",
        ("variant", "molecular marker", "mutation"),
        {
            "patient_id": "Patient this marker was tested for. Part of the key.",
            "marker": "Marker or variant name, for example EGFR exon 20 insertion. Node label and part of the key.",
            "gene": "Gene the marker relates to.",
            "category": "Marker category, for example mutation, amplification, or fusion.",
            "status": "Detection status, for example Detected, Not detected, Positive, Negative, or Amplified.",
            "method": "Assay method, for example NGS.",
            "assessed_date": "Date the marker was assessed.",
        },
    ),
    "Lab": EntitySpec(
        "labs", ("patient_id",), "lab_type",
        "A longitudinal laboratory result for a patient. CrCl_CKD-EPI is renal function in mL/min and drives the "
        "renal eligibility criterion; each row carries a value, unit, reference range, and collection date.",
        ("lab result", "laboratory value", "test result"),
        {
            "patient_id": "Patient the lab result belongs to. Entity key.",
            "lab_date": "Collection date of the result.",
            "lab_type": "Lab analyte, for example CrCl_CKD-EPI for renal function. Node label.",
            "value": "Numeric result value.",
            "unit": "Unit of the value, for example mL/min.",
            "ref_low": "Lower bound of the reference range.",
            "ref_high": "Upper bound of the reference range.",
        },
    ),
    "Treatment": EntitySpec(
        "treatment_history", ("patient_id",), "drug_name",
        "A prior or ongoing line of anticancer therapy for a patient, with the drug, drug class, line number, "
        "dates, cycles, and best response. Drug class drives prior-therapy exclusion matching.",
        ("therapy", "regimen", "prior therapy"),
        {
            "patient_id": "Patient who received the therapy. Entity key.",
            "drug_name": "Drug name. Node label.",
            "drug_class": "Drug class, for example platinum doublet, used for prior-therapy exclusion matching.",
            "line": "Line of therapy (1 = first line).",
            "start_date": "Date therapy started.",
            "end_date": "Date therapy ended.",
            "cycles_completed": "Number of treatment cycles completed.",
            "best_response": "Best response achieved, RECIST-style.",
            "reason_stopped": "Reason the therapy was stopped.",
        },
    ),
    "Amendment": EntitySpec(
        "amendments", ("trial_id", "amendment_id"), "label",
        "A protocol amendment that revises a trial's criteria (for example softening a prior-therapy exclusion), "
        "with its effective date and the criterion it modifies.",
        ("protocol amendment", "revision"),
        {
            "trial_id": "Trial the amendment applies to. Part of the key.",
            "amendment_id": "Unique amendment identifier within the trial. Part of the key.",
            "label": "Amendment label, for example Amendment 2. Node label.",
            "effective_date": "Date the amendment took effect.",
            "modifies_criterion_id": "criterion_id of the criterion this amendment revises.",
            "summary": "Summary of what the amendment changes.",
        },
    ),
    "Enrollment": EntitySpec(
        "trial_enrollment", ("patient_id", "trial_id"), "status",
        "The screening and enrollment relationship between a patient and a trial, capturing screening status, "
        "date, and coordinator notes.",
        ("screening", "enrollment record"),
        {
            "patient_id": "Patient in the screening relationship. Part of the key.",
            "trial_id": "Trial in the screening relationship. Part of the key.",
            "status": "Screening or enrollment status: Pre-screening, Screening, Not enrolled, or Enrolled. Node label.",
            "screening_date": "Date of the screening activity.",
            "coordinator_id": "person_id of the coordinator who owns this screening.",
            "notes": "Coordinator notes on the screening.",
        },
    ),
    "Person": EntitySpec(
        "people", ("person_id",), "display_name",
        "A member of the care team (principal investigator, coordinator, or navigator) associated with a site.",
        ("care team member", "staff", "clinician"),
        {
            "person_id": "Unique person identifier. Entity key.",
            "display_name": "Person's name. Node label.",
            "role": "Care-team role, for example Principal Investigator, Coordinator, or Navigator.",
            "site_id": "site_id where the person works.",
        },
    ),
    "Site": EntitySpec(
        "sites", ("site_id",), "display_name",
        "A clinical research site where trials run and care-team members work.",
        ("location", "center", "facility"),
        {
            "site_id": "Unique site identifier. Entity key.",
            "display_name": "Site name. Node label.",
            "short_name": "Short site code.",
        },
    ),
}

# Relationship types with contextualizations: each relationship is bound to the table that carries
# both endpoints' keys, so the graph materializes real edges (Patient-has-Lab, Trial-requires-
# Criterion, ...) rather than dangling relationship types. Mirrors data/ontology/ontology.yaml.
RELATIONSHIPS: list[RelSpec] = [
    RelSpec("has_biomarker", "Patient", "Biomarker", "biomarkers",
            {"patient_id": "patient_id"}, {"patient_id": "patient_id", "marker": "marker"}),
    RelSpec("has_lab", "Patient", "Lab", "labs",
            {"patient_id": "patient_id"}, {"patient_id": "patient_id"}),
    RelSpec("on_treatment", "Patient", "Treatment", "treatment_history",
            {"patient_id": "patient_id"}, {"patient_id": "patient_id"}),
    RelSpec("screened_for", "Patient", "Trial", "trial_enrollment",
            {"patient_id": "patient_id"}, {"trial_id": "trial_id"}),
    RelSpec("requires_criterion", "Trial", "Criterion", "trial_criteria",
            {"trial_id": "trial_id"}, {"trial_id": "trial_id", "criterion_id": "criterion_id"}),
    RelSpec("has_amendment", "Trial", "Amendment", "amendments",
            {"trial_id": "trial_id"}, {"trial_id": "trial_id", "amendment_id": "amendment_id"}),
    RelSpec("amendment_modifies", "Amendment", "Criterion", "amendments",
            {"trial_id": "trial_id", "amendment_id": "amendment_id"}, {"trial_id": "trial_id", "modifies_criterion_id": "criterion_id"}),
    RelSpec("treated_by", "Patient", "Person", "patient_registry",
            {"patient_id": "patient_id"}, {"treating_oncologist_id": "person_id"}),
    RelSpec("trial_at_site", "Trial", "Site", "trials",
            {"trial_id": "trial_id"}, {"site_id": "site_id"}),
]


def _pos_int64(*parts: str) -> str:
    """Deterministic positive 63-bit integer id (as string) from the given name parts."""
    h = hashlib.sha256("::".join(parts).encode()).digest()
    return str(int.from_bytes(h[:8], "big") & 0x7FFFFFFFFFFFFFFF)


def _det_guid(*parts: str) -> str:
    return str(uuid.UUID(hashlib.sha256("::".join(parts).encode()).hexdigest()[:32]))


def _b64(obj: object) -> str:
    return base64.b64encode(json.dumps(obj).encode()).decode()


def delta_schema(table: str) -> list[tuple[str, str]]:
    """Return [(column, delta_type)] from the latest Delta log commit for a table."""
    base = f"{ONELAKE}/{WS}/{LH}/Tables/{table}/_delta_log"
    h = {"Authorization": f"Bearer {STORAGE_TOKEN}"}
    listing = requests.get(f"{base}?recursive=false&resource=filesystem", headers=h)
    listing.raise_for_status()
    commits = sorted(
        p["name"].split("/")[-1]
        for p in listing.json().get("paths", [])
        if p["name"].endswith(".json")
    )
    schema: list[tuple[str, str]] = []
    for commit in commits:  # later commits override earlier metaData
        raw = requests.get(f"{base}/{commit}", headers=h)
        raw.raise_for_status()
        for line in raw.content.decode("utf-8").split("\n"):
            if '"metaData"' not in line:
                continue
            meta = json.loads(line)["metaData"]
            fields = json.loads(meta["schemaString"])["fields"]
            schema = [(f["name"], str(f["type"])) for f in fields]
    return schema


def _prop_id(entity: str, col: str) -> str:
    return _pos_int64(entity, col)


def build_entity_part(entity: str, spec: EntitySpec) -> tuple[str, dict, dict]:
    """Return (entity_type_id, entity_definition, data_binding) for one entity type."""
    cols = delta_schema(spec.table)
    col_names = {c for c, _ in cols}
    props = []
    prop_bindings = []
    for col, dtype in cols:
        pid = _prop_id(entity, col)
        vtype = TYPE_MAP.get(dtype.lower(), "String")
        prop = {"id": pid, "name": col, "redefines": None, "baseTypeNamespaceType": None, "valueType": vtype}
        desc = spec.props.get(col)
        if desc:  # property-level semanticEnrichment carries the Description column in the portal
            prop["semanticEnrichment"] = {"description": desc, "customAttributes": {}}
        props.append(prop)
        prop_bindings.append({"sourceColumnName": col, "targetPropertyId": pid})
    key_ids = [_prop_id(entity, k) for k in spec.keys if k in col_names] or [props[0]["id"]]
    display_id = _prop_id(entity, spec.display) if spec.display in col_names else key_ids[0]
    et_id = _pos_int64("entity", entity)
    entity_def = {
        "id": et_id,
        "namespace": "usertypes",
        "baseEntityTypeId": None,
        "name": entity,
        "entityIdParts": key_ids,
        "displayNamePropertyId": display_id,
        "namespaceType": "Custom",
        "visibility": "Visible",
        "properties": props,
        "timeseriesProperties": [],
        # Entity-level semanticEnrichment = the portal Metadata panel (Description + Synonyms), giving
        # AI agents and NL2Ontology rich business context.
        "semanticEnrichment": {
            "description": spec.description,
            "synonyms": list(spec.synonyms),
            "customAttributes": {},
        },
    }
    binding = {
        "id": _det_guid("binding", entity),
        "dataBindingConfiguration": {
            "dataBindingType": "NonTimeSeries",
            "propertyBindings": prop_bindings,
            "sourceTableProperties": {
                "sourceType": "LakehouseTable",
                "workspaceId": WS,
                "itemId": LH,
                "sourceTableName": spec.table,
                "sourceSchema": "dbo",
            },
        },
    }
    return et_id, entity_def, binding


def build_definition() -> dict:
    parts = [
        {"path": "definition.json", "payload": _b64({}), "payloadType": "InlineBase64"},
        {
            "path": ".platform",
            "payload": _b64(
                {
                    "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
                    "metadata": {"type": "Ontology", "displayName": ONTOLOGY_NAME, "description": "AMC IQ precision-oncology domain ontology for clinical-trial readiness: patients, trials, criteria, biomarkers, labs, treatments, and care-team workflow."},
                    "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"},
                }
            ),
            "payloadType": "InlineBase64",
        },
    ]
    entity_ids: dict[str, str] = {}
    items = list(ENTITIES.items())
    if ONLY_ENTITY:
        items = [(ONLY_ENTITY, ENTITIES[ONLY_ENTITY])]
    for entity, spec in items:
        et_id, entity_def, binding = build_entity_part(entity, spec)
        entity_ids[entity] = et_id
        parts.append({"path": f"EntityTypes/{et_id}/definition.json", "payload": _b64(entity_def), "payloadType": "InlineBase64"})
        parts.append({"path": f"EntityTypes/{et_id}/DataBindings/{binding['id']}.json", "payload": _b64(binding), "payloadType": "InlineBase64"})

    if not ONLY_ENTITY:
        for rel in RELATIONSHIPS:
            rid = _pos_int64("rel", rel.name)
            rel_def = {
                "namespace": "usertypes",
                "id": rid,
                "name": rel.name,
                "namespaceType": "Custom",
                "source": {"entityTypeId": entity_ids[rel.src]},
                "target": {"entityTypeId": entity_ids[rel.tgt]},
            }
            parts.append({"path": f"RelationshipTypes/{rid}/definition.json", "payload": _b64(rel_def), "payloadType": "InlineBase64"})
            # Contextualization: bind the relationship to the table carrying both endpoints' keys so
            # the graph materializes real edges. Map rel-table columns to each side's key property ids.
            ctx_id = _det_guid("ctx", rel.name)
            ctx = {
                "id": ctx_id,
                "dataBindingTable": {
                    "workspaceId": WS,
                    "itemId": LH,
                    "sourceTableName": rel.table,
                    "sourceSchema": "dbo",
                    "sourceType": "LakehouseTable",
                },
                "sourceKeyRefBindings": [
                    {"sourceColumnName": col, "targetPropertyId": _prop_id(rel.src, key)} for col, key in rel.src_map.items()
                ],
                "targetKeyRefBindings": [
                    {"sourceColumnName": col, "targetPropertyId": _prop_id(rel.tgt, key)} for col, key in rel.tgt_map.items()
                ],
            }
            parts.append({"path": f"RelationshipTypes/{rid}/Contextualizations/{ctx_id}.json", "payload": _b64(ctx), "payloadType": "InlineBase64"})

    return {"definition": {"parts": parts}}


def update_definition(definition: dict) -> None:
    url = f"{FABRIC}/workspaces/{WS}/ontologies/{ONT}/updateDefinition?updateMetadata=true"
    h = {"Authorization": f"Bearer {FABRIC_TOKEN}", "Content-Type": "application/json"}
    r = requests.post(url, headers=h, json=definition)
    print(f"updateDefinition HTTP {r.status_code}")
    if r.status_code == 202:
        op = r.headers.get("Location")
        print(f"  polling {op}")
        _poll(op)
    elif r.status_code not in (200, 201):
        print(f"  ERROR body: {r.text[:2000]}")
        r.raise_for_status()
    else:
        print("  applied synchronously")


def _poll(op_url: str | None) -> None:
    if not op_url:
        return
    h = {"Authorization": f"Bearer {FABRIC_TOKEN}"}
    for _ in range(30):
        time.sleep(4)
        r = requests.get(op_url, headers=h)
        if not r.headers.get("content-type", "").startswith("application/json"):
            continue
        status = r.json().get("status")
        if status in ("Succeeded", "Completed"):
            print("  -> succeeded")
            return
        if status == "Failed":
            print(f"  -> FAILED: {r.text[:2000]}")
            raise SystemExit(1)
    print("  -> timeout")


def _lro_wait(resp: requests.Response) -> None:
    if resp.status_code == 202:
        _poll(resp.headers.get("Location"))


def get_or_create_ontology() -> str:
    """Return the ontology item id, creating the item (by name) if it does not exist."""
    h = {"Authorization": f"Bearer {FABRIC_TOKEN}", "Content-Type": "application/json"}
    base = f"{FABRIC}/workspaces/{WS}/ontologies"
    existing = requests.get(base, headers=h).json().get("value", [])
    for o in existing:
        if o.get("displayName") == ONTOLOGY_NAME:
            print(f"  ontology exists: {ONTOLOGY_NAME} ({o['id']})")
            return o["id"]
    body = {"displayName": ONTOLOGY_NAME, "description": "AMC IQ precision-oncology domain ontology for clinical-trial readiness: patients, trials, criteria, biomarkers, labs, treatments, and care-team workflow."}
    r = requests.post(base, headers=h, json=body)
    _lro_wait(r)
    for _ in range(15):
        time.sleep(3)
        for o in requests.get(base, headers=h).json().get("value", []):
            if o.get("displayName") == ONTOLOGY_NAME:
                print(f"  ontology created: {ONTOLOGY_NAME} ({o['id']})")
                return o["id"]
    raise SystemExit("Ontology item did not appear after creation.")


def attach_to_data_agent() -> None:
    """Attach the ontology as a FabricItem datasource to the published Data Agent and publish."""
    h = {"Authorization": f"Bearer {FABRIC_TOKEN}", "Content-Type": "application/json"}
    items = requests.get(f"{FABRIC}/workspaces/{WS}/items", headers=h).json().get("value", [])
    da = next((i for i in items if i.get("type") == "DataAgent" and i.get("displayName") == DATA_AGENT_NAME), None)
    if not da:
        print(f"  Data Agent '{DATA_AGENT_NAME}' not found; skipping attach.")
        return
    b = f"{FABRIC}/workspaces/{WS}/dataAgents/{da['id']}"
    sources = requests.get(f"{b}/staging/datasources", headers=h).json().get("value", [])
    if any(s.get("id") == ONT for s in sources):
        print("  ontology already attached to Data Agent.")
    else:
        body = {"type": "FabricItem", "itemReference": {"referenceType": "ById", "itemId": ONT, "workspaceId": WS}}
        _lro_wait(requests.post(f"{b}/staging/datasources", headers=h, json=body))
        print("  ontology attached to Data Agent as FabricItem datasource.")
    # Add the documented group-by GQL instruction and (re)publish.
    instr = (
        "You answer questions about oncology patients and clinical trial operations using "
        f"the {ONTOLOGY_NAME} Fabric IQ Ontology (entity types Patient, Trial, Criterion, Biomarker, "
        "Lab, Treatment, Amendment, Enrollment, Person, Site and their relationships) for "
        "relationship-aware questions, and the Lakehouse tables for direct structured lookups. "
        "CrCl_CKD-EPI in labs is renal function in mL/min. Patient IDs look like PT-1042; trial IDs "
        "like NCT99004324. Give precise values with dates. Support group by in GQL. Do not give medical advice."
    )
    try:
        requests.patch(f"{b}/staging/settings", headers=h, json={"aiInstructions": instr})
    except Exception as exc:  # noqa: BLE001
        print(f"  (settings patch skipped: {exc})")
    _lro_wait(requests.post(f"{b}/staging/publish", headers=h, json={"publishedDescription": "AMC IQ Fabric Data Agent: Lakehouse + Fabric IQ Ontology (NL2Ontology)"}))
    print("  Data Agent published with ontology source.")


def main() -> None:
    global ONT
    if not ONT:
        ONT = get_or_create_ontology()
    print(f"Building ontology definition for {ONT} (only={ONLY_ENTITY or 'ALL'})")
    definition = build_definition()
    print(f"  parts: {len(definition['definition']['parts'])}")
    update_definition(definition)
    # Keep the Ontology source detached while its system-owned child GraphModel is empty. The
    # supported standalone GraphModel is provisioned and attached by provision_fabric_graph.py.
    if not ONLY_ENTITY and os.environ.get("ATTACH_ONTOLOGY_TO_DA"):
        attach_to_data_agent()
    print("Done.")


if __name__ == "__main__":
    main()
