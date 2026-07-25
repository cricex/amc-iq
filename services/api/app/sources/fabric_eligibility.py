"""Fabric-native eligibility evaluation (per the agreed architecture).

Split responsibilities so the result is both Fabric-native and reliable:
  1. RETRIEVAL: the Fabric Data Agent (NL2SQL over the Lakehouse) returns the trial's criteria and
     the patient's facts. The Data Agent retrieves facts consistently.
  2. EVALUATION: a dedicated Foundry eligibility-expert agent (`amciq-eligibility-evaluator`, no
     tools, explicit rules in its instructions) reasons over the PROVIDED facts and emits a strict,
     machine-readable verdict. Reasoning over provided facts is far more consistent than combining
     NL2SQL and reasoning in one agent (which produced different verdicts and hallucinated criteria).

The verdict is parsed into the shared `CriterionResult` shape that every eligibility caller consumes.
This is the only place criterion verdicts are produced; the retired ontology.py traversal is gone.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.config import Settings
from app.eligibility import CriterionResult
from app.sources.fabric import _call_fabric_mcp, _run_coro_blocking

logger = logging.getLogger("amciq.fabric_eligibility")

_CRIT = re.compile(r"^\s*CRIT\|([^|]+)\|([^|]*)\|(met|uncertain|not_met)\|(.*)$", re.IGNORECASE | re.MULTILINE)
_KINDS = {"inclusion", "exclusion"}


@dataclass(frozen=True)
class RetrievedCriterion:
    criterion_id: str
    kind: str
    category: str
    description: str
    references_entity: str
    param: str
    comparator: str
    value: str


def _data_agent(settings: Settings, question: str) -> str:
    return _run_coro_blocking(
        lambda: _call_fabric_mcp(settings.fabric_mcp_url, settings.FABRIC_API_SCOPE, question)
    )


def _evaluator(settings: Settings, facts: str, instruction: str = "Evaluate eligibility.") -> str:
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    client = AIProjectClient(endpoint=settings.PROJECT_ENDPOINT, credential=DefaultAzureCredential())
    oc = client.get_openai_client()
    response = oc.with_options(timeout=settings.SPECIALIST_TIMEOUT).responses.create(
        input=f"{instruction}\n\n{facts}",
        extra_body={"agent_reference": {"name": settings.ELIGIBILITY_EVALUATOR_AGENT, "type": "agent_reference"}},
    )
    return (getattr(response, "output_text", "") or "").strip()


def retrieve_criteria(settings: Settings, trial_id: str) -> str:
    queries = (
        (
            "Use only the Lakehouse datasource, not any GraphModel. From trial_criteria, "
            "return criterion_id, kind, category, description, "
            "references_entity, param, comparator, value where trial_id is "
            f"{trial_id}. Output plain text rows only."
        ),
        (
            "Use only the Lakehouse datasource, not any GraphModel. "
            f"Query trial_criteria for trial_id '{trial_id}'. Return exactly these eight "
            "pipe-separated columns for every matching row: criterion_id | kind | category | "
            "description | references_entity | param | comparator | value. No prose."
        ),
    )
    for attempt, question in enumerate(queries, 1):
        answer = _data_agent(settings, question)
        if parse_retrieved_criteria(answer):
            return answer
        logger.warning(
            "Fabric criteria retrieval attempt %d returned no structured rows for %s "
            "(response_chars=%d)",
            attempt,
            trial_id,
            len(answer),
        )
    raise RuntimeError(
        f"Fabric Data Agent returned no structured trial criteria for {trial_id} after "
        f"{len(queries)} attempts"
    )


def retrieve_patient_facts(settings: Settings, patient_id: str, trial_id: str) -> str:
    return _data_agent(
        settings,
        "Use only the Lakehouse datasource, not any GraphModel. "
        f"For patient {patient_id} report these facts as plain lines and nothing else: "
        f"cancer_type and ecog_ps from patient_registry; every biomarkers row as marker=status; "
        f"the latest two CrCl_CKD-EPI labs rows as value on lab_date; treatment_history rows as "
        f"drug_name (drug_class); and any amendments rows for trial {trial_id} as "
        f"amendment_id modifies modifies_criterion_id.",
    )


def parse_retrieved_criteria(text: str) -> list[RetrievedCriterion]:
    criteria: list[RetrievedCriterion] = []
    seen: set[str] = set()
    for line in text.splitlines():
        parts = [part.strip() for part in line.strip().lstrip("-").strip().split("|")]
        if len(parts) != 8:
            continue
        criterion_id, kind, category, description, entity, param, comparator, value = parts
        kind = kind.lower()
        if not criterion_id or kind not in _KINDS or not category or criterion_id in seen:
            continue
        criteria.append(
            RetrievedCriterion(
                criterion_id=criterion_id,
                kind=kind,
                category=category.lower(),
                description=description,
                references_entity=entity,
                param=param,
                comparator=comparator,
                value=value,
            )
        )
        seen.add(criterion_id)
    return criteria


def parse_verdict(verdict: str) -> list[CriterionResult]:
    results: list[CriterionResult] = []
    for match in _CRIT.finditer(verdict):
        criterion_id = match.group(1).strip()
        category = match.group(2).strip().lower()
        status = match.group(3).lower()
        reason = match.group(4).strip()
        results.append(
            CriterionResult(
                criterion=criterion_id,
                kind="",
                category=category,
                description=reason,
                status=status,
                evidence=reason,
            )
        )
    return results


def _complete_verdict(
    verdict: str,
    criteria: list[RetrievedCriterion],
) -> list[CriterionResult] | None:
    parsed = parse_verdict(verdict)
    by_id: dict[str, CriterionResult] = {}
    duplicates: set[str] = set()
    for result in parsed:
        if result.criterion in by_id:
            duplicates.add(result.criterion)
        by_id[result.criterion] = result

    expected_ids = [criterion.criterion_id for criterion in criteria]
    if duplicates or set(by_id) != set(expected_ids) or len(parsed) != len(expected_ids):
        return None

    completed: list[CriterionResult] = []
    for criterion in criteria:
        verdict_result = by_id[criterion.criterion_id]
        completed.append(
            CriterionResult(
                criterion=criterion.criterion_id,
                kind=criterion.kind,
                category=criterion.category,
                description=criterion.description,
                status=verdict_result.status,
                evidence=verdict_result.evidence,
            )
        )
    return completed


def evaluate_eligibility_fabric(settings: Settings, patient_id: str, trial_id: str) -> list[CriterionResult]:
    """Retrieve criteria + facts from the Fabric Data Agent, evaluate them with the Foundry
    eligibility-expert agent, and return parsed CriterionResults. Raises on empty/unparseable output
    so the caller can fall back."""
    criteria_text = retrieve_criteria(settings, trial_id)
    criteria = parse_retrieved_criteria(criteria_text)
    if not criteria:
        raise RuntimeError(f"Fabric Data Agent returned no structured trial criteria for {trial_id}")

    facts = retrieve_patient_facts(settings, patient_id, trial_id)
    evaluator_input = (
        f"Trial {trial_id} criteria:\n{criteria_text}\n\nPatient {patient_id} facts:\n{facts}"
    )
    expected_ids = [criterion.criterion_id for criterion in criteria]
    verdict = _evaluator(settings, evaluator_input)
    completed = _complete_verdict(verdict, criteria)
    if completed is None:
        logger.warning(
            "Eligibility evaluator returned an incomplete verdict for %s/%s "
            "(expected=%d parsed=%d response_chars=%d)",
            patient_id,
            trial_id,
            len(expected_ids),
            len(parse_verdict(verdict)),
            len(verdict),
        )
        instruction = (
            "Return the complete machine-readable eligibility block now. Your previous response "
            f"was incomplete. Emit exactly one CRIT line for each of these criterion IDs, in this "
            f"order: {', '.join(expected_ids)}. Then emit one OVERALL line. Output no prose."
        )
        verdict = _evaluator(settings, evaluator_input, instruction)
        completed = _complete_verdict(verdict, criteria)

    if completed is None:
        parsed_count = len(parse_verdict(verdict))
        raise RuntimeError(
            f"Eligibility evaluator omitted required criteria after 2 attempts "
            f"(expected {len(expected_ids)}, parsed {parsed_count})"
        )
    logger.info("Fabric eligibility for %s/%s: %d criteria", patient_id, trial_id, len(completed))
    return completed
