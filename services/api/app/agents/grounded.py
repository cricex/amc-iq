"""Grounded specialists (Fabric-native ground truth).

Deterministic specialist implementations whose clinical findings are derived from the Fabric-native
eligibility evaluation (``app.eligibility.evaluate_eligibility``: Fabric Data Agent facts + the
Foundry eligibility-evaluator agent). Non-eligibility context (coordinator ownership, candidate
trials) is read from the canonical synthetic registry, the permitted local metadata source.

They give the whole multi-agent flow a stable spine: every eligibility specialist reads the same
Fabric verdicts, so their findings agree and the Critic's ground truth matches. The live Foundry
specialists (``app.agents.live``) narrate over these same verdicts and are cross-checked against
them. Specialists that discover something worth pursuing emit an investigation Lead, which the
deepening loop dispatches to another specialist (for example, a borderline renal criterion -> a
workflow lead to schedule a repeat draw).
"""
from __future__ import annotations

from app.agents.base import Finding, Lead, SpecialistContext, SpecialistName, SpecialistResult
from app.agents.deepening import Budget
from app.agents.registry import all_specialists
from app.agents.team import TeamRun, run_team
from app.config import get_settings
from app.eligibility import NOT_MET, UNCERTAIN, CriterionResult, evaluate_eligibility
from app.registry import load_registry, maybe_find_by_id

# Which criterion categories each domain specialist owns.
_RENAL_CATEGORIES = frozenset({"renal", "hepatic", "hematologic"})
_GENOMICS_CATEGORIES = frozenset({"biomarker"})
_PROTOCOL_CATEGORIES = frozenset({"prior_therapy", "consent", "diagnosis"})


def _criteria(ctx: SpecialistContext) -> list[CriterionResult]:
    """The Fabric-evaluated criteria for this (patient, trial). Cached per pair in app.eligibility."""
    return evaluate_eligibility(get_settings(), ctx.patient_id, ctx.trial_id)


def _finding(result: CriterionResult) -> Finding:
    return Finding(
        label=result.criterion,
        detail=f"{result.description}: {result.evidence}",
        status=result.status,
        criterion_id=result.criterion,
        category=result.category,
    )


def _summary(kind: str, findings: list[Finding]) -> str:
    if not findings:
        return f"No {kind} criteria applied."
    met = sum(1 for f in findings if f.status == "met")
    unc = sum(1 for f in findings if f.status == UNCERTAIN)
    bad = sum(1 for f in findings if f.status == NOT_MET)
    return f"{kind}: {met} met, {unc} uncertain, {bad} not met."


def _coordinator_name(patient_id: str) -> str:
    reg = load_registry()
    patient = maybe_find_by_id(reg["patients"], patient_id) or {}
    coord_id = str(patient.get("coordinator", ""))
    person = maybe_find_by_id(reg["people"], coord_id)
    return str(person["display"]) if person else (coord_id or "the assigned coordinator")


def _cancer_compatible(patient_ct: str, trial_ct: str) -> bool:
    if not trial_ct or trial_ct == "Solid tumor":
        return True
    if patient_ct == trial_ct:
        return True
    return bool(patient_ct) and bool(trial_ct) and patient_ct.split()[0] == trial_ct.split()[0]


def _candidate_trials(patient_id: str, exclude_trial_id: str) -> list[dict]:
    """Other open trials whose tumor type is compatible with the patient (registry scan, not a graph
    ranking; cohort ranking needs the Fabric Graph, which is provisioned separately)."""
    reg = load_registry()
    patient = maybe_find_by_id(reg["patients"], patient_id) or {}
    patient_ct = str(patient.get("cancer_type", ""))
    out: list[dict] = []
    for trial in reg["trials"]:
        if str(trial.get("id")) == exclude_trial_id:
            continue
        if str(trial.get("status", "")).lower() not in ("recruiting", "open", ""):
            continue
        if _cancer_compatible(patient_ct, str(trial.get("cancer_type", ""))):
            out.append(trial)
    return out[:3]


class EligibilitySpecialist:
    name = SpecialistName.ELIGIBILITY
    domain = "Criteria matching (Fabric eligibility)"

    def run(self, ctx: SpecialistContext) -> SpecialistResult:
        findings = [_finding(r) for r in _criteria(ctx)]
        return SpecialistResult(
            specialist=self.name, summary=_summary("Eligibility", findings), findings=findings, depth=ctx.depth
        )


class _CategorySpecialist:
    """A specialist that evaluates only the criteria in its owned categories."""

    name: SpecialistName
    domain: str
    categories: frozenset[str]

    def _mine(self, ctx: SpecialistContext) -> list[CriterionResult]:
        return [r for r in _criteria(ctx) if r.category in self.categories]


class RenalLabsSpecialist(_CategorySpecialist):
    name = SpecialistName.RENAL_LABS
    domain = "Renal function, lab thresholds, trends, staleness"
    categories = _RENAL_CATEGORIES

    def run(self, ctx: SpecialistContext) -> SpecialistResult:
        mine = self._mine(ctx)
        findings = [_finding(r) for r in mine]
        leads: list[Lead] = []
        if ctx.depth == 0 and any(r.status == UNCERTAIN for r in mine):
            leads.append(
                Lead(
                    id=f"{ctx.patient_id}-renal-verify",
                    from_specialist=self.name,
                    kind="verify_repeat_lab",
                    rationale="Renal function is borderline against the threshold; a repeat draw and PI review are warranted.",
                    target_specialist=SpecialistName.WORKFLOW,
                    focus_question=f"Confirm the repeat CrCl scheduling and PI review path for {ctx.patient_id}.",
                    priority=0.85,
                )
            )
        return SpecialistResult(
            specialist=self.name, summary=_summary("Renal/labs", findings), findings=findings, leads=leads, depth=ctx.depth
        )


class GenomicsSpecialist(_CategorySpecialist):
    name = SpecialistName.GENOMICS
    domain = "Molecular reports and variant interpretation"
    categories = _GENOMICS_CATEGORIES

    def run(self, ctx: SpecialistContext) -> SpecialistResult:
        mine = self._mine(ctx)
        findings = [_finding(r) for r in mine]
        leads: list[Lead] = []
        if ctx.depth == 0 and any(r.status == NOT_MET for r in mine):
            leads.append(
                Lead(
                    id=f"{ctx.patient_id}-genomics-alt",
                    from_specialist=self.name,
                    kind="alternative_trials",
                    rationale="The required biomarker is not present; other trials may match the patient's actual profile.",
                    target_specialist=SpecialistName.EVIDENCE,
                    focus_question=f"Identify candidate trials matching {ctx.patient_id}'s actual biomarker profile.",
                    priority=0.7,
                )
            )
        return SpecialistResult(
            specialist=self.name, summary=_summary("Genomics", findings), findings=findings, leads=leads, depth=ctx.depth
        )


class ProtocolSpecialist(_CategorySpecialist):
    name = SpecialistName.PROTOCOL
    domain = "Exclusion interpretation and amendment deltas"
    categories = _PROTOCOL_CATEGORIES

    def run(self, ctx: SpecialistContext) -> SpecialistResult:
        mine = self._mine(ctx)
        findings = [_finding(r) for r in mine]
        leads: list[Lead] = []
        if ctx.depth == 0 and any(r.status == UNCERTAIN for r in mine):
            leads.append(
                Lead(
                    id=f"{ctx.patient_id}-protocol-pi",
                    from_specialist=self.name,
                    kind="pi_confirmation",
                    rationale="A protocol amendment revised an exclusion; PI confirmation is required before it is cleared.",
                    target_specialist=SpecialistName.WORKFLOW,
                    focus_question=f"Route the prior-therapy exclusion for {ctx.patient_id} to the PI for confirmation.",
                    priority=0.8,
                )
            )
        return SpecialistResult(
            specialist=self.name, summary=_summary("Protocol", findings), findings=findings, leads=leads, depth=ctx.depth
        )


class WorkflowSpecialist:
    name = SpecialistName.WORKFLOW
    domain = "Tasks, ownership, scheduling, capacity"

    def run(self, ctx: SpecialistContext) -> SpecialistResult:
        coord = _coordinator_name(ctx.patient_id)
        kind = ctx.extras.get("lead_kind")
        if kind == "verify_repeat_lab":
            finding = Finding(
                label="repeat_crcl_scheduling",
                detail=f"{coord} owns the screening task and can schedule a repeat CrCl before formal screening.",
                status="info",
            )
        elif kind == "pi_confirmation":
            finding = Finding(
                label="pi_confirmation_routing",
                detail=f"{coord} can route the prior-therapy exclusion to the PI for confirmation.",
                status="info",
            )
        else:
            finding = Finding(
                label="workflow_status",
                detail=f"Screening state: Not yet screened; coordinator {coord}.",
                status="info",
            )
        return SpecialistResult(specialist=self.name, summary=finding.detail, findings=[finding], depth=ctx.depth)


class EvidenceSpecialist:
    name = SpecialistName.EVIDENCE
    domain = "External literature, registry, guidelines"

    def run(self, ctx: SpecialistContext) -> SpecialistResult:
        if ctx.extras.get("lead_kind") == "alternative_trials":
            candidates = _candidate_trials(ctx.patient_id, ctx.trial_id)
            listed = "; ".join(f"{c.get('short', c.get('title', ''))} ({c['id']})" for c in candidates) or "no close matches on file"
            finding = Finding(
                label="alternative_trials",
                detail=f"Candidate trials for the patient's profile: {listed}.",
                status="info",
            )
        else:
            finding = Finding(
                label="external_context",
                detail="External registry and treatment-landscape context is available; it informs but does not decide AMC eligibility.",
                status="info",
            )
        return SpecialistResult(specialist=self.name, summary=finding.detail, findings=[finding], depth=ctx.depth)


# The grounded team, one instance per specialist.
GROUNDED: dict[SpecialistName, object] = {
    SpecialistName.ELIGIBILITY: EligibilitySpecialist(),
    SpecialistName.RENAL_LABS: RenalLabsSpecialist(),
    SpecialistName.GENOMICS: GenomicsSpecialist(),
    SpecialistName.PROTOCOL: ProtocolSpecialist(),
    SpecialistName.WORKFLOW: WorkflowSpecialist(),
    SpecialistName.EVIDENCE: EvidenceSpecialist(),
}


def _run_specialist(name: SpecialistName, ctx: SpecialistContext) -> SpecialistResult:
    specialist = GROUNDED[name]
    try:
        return specialist.run(ctx)  # type: ignore[attr-defined]
    except Exception as exc:  # keep the team resilient; a failed specialist degrades gracefully
        return SpecialistResult(specialist=name, status="failed", error=str(exc), depth=ctx.depth)


def build_ground_truth(patient_id: str, trial_id: str) -> dict[str, str]:
    """Authoritative per-criterion statuses (Fabric eligibility), keyed by criterion id, for the Critic."""
    return {r.criterion: r.status for r in evaluate_eligibility(get_settings(), patient_id, trial_id)}


def run_grounded_team(
    patient_id: str,
    trial_id: str,
    question: str,
    intent: str,
    budget: Budget | None = None,
) -> TeamRun:
    """Run the full grounded specialist team for a (patient, trial, question) with deepening."""
    ctx = SpecialistContext(question=question, intent=intent, patient_id=patient_id, trial_id=trial_id)
    ground_truth = build_ground_truth(patient_id, trial_id)
    return run_team(ctx, _run_specialist, all_specialists(), ground_truth=ground_truth, budget=budget)
