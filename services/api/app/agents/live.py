"""Live Foundry specialist adapter.

Backs the LIVE_SPECIALIST_ROLES with a real hosted Foundry agent that narrates its role by reasoning
over the facts already established for it (calling a tool only to fill a gap). Design (consistent with
the repo principle "Fabric is ground truth; agents narrate over it"):

- Structured findings and leads always come from the grounded specialist (whose verdicts are the
  Fabric-native eligibility evaluation), so the assessment stays consistent and the Critic can
  cross-check.
- The live Foundry agent supplies the natural-language SUMMARY for its role, reasoning over the
  grounded facts passed in its prompt, which drives the Assessment Steps trace.
- Any live-agent failure, timeout, or empty answer falls back to the grounded summary. Deepened
  (depth > 0) turns stay grounded to bound cost and latency.

Only a few roles narrate live (LIVE_SPECIALIST_ROLES): narrating all six live, each doing its own KB +
Fabric retrieval, fans out too far and times out on the single F64 Data Agent. The team engine
(app/agents/team.py) runs the planner-selected specialists in parallel, so the live agent calls also
run concurrently, each under SPECIALIST_TIMEOUT.
"""
from __future__ import annotations

import logging

from app.agents.base import SpecialistContext, SpecialistName, SpecialistResult
from app.agents.deepening import Budget
from app.agents.grounded import _run_specialist, build_ground_truth
from app.agents.registry import all_specialists
from app.agents.team import TeamRun, run_team
from app.config import Settings

logger = logging.getLogger("amciq.agents.live")


def _agent_name_for(settings: Settings, name: SpecialistName) -> str | None:
    return {
        SpecialistName.ELIGIBILITY: settings.SPECIALIST_ELIGIBILITY_AGENT,
        SpecialistName.RENAL_LABS: settings.SPECIALIST_RENAL_AGENT,
        SpecialistName.GENOMICS: settings.SPECIALIST_GENOMICS_AGENT,
        SpecialistName.PROTOCOL: settings.SPECIALIST_PROTOCOL_AGENT,
        SpecialistName.WORKFLOW: settings.SPECIALIST_WORKFLOW_AGENT,
        SpecialistName.EVIDENCE: settings.SPECIALIST_EVIDENCE_AGENT,
    }.get(name)


def _focus_question(name: SpecialistName, ctx: SpecialistContext) -> str:
    base = f"For patient {ctx.patient_id} and trial {ctx.trial_id}: "
    if name == SpecialistName.ELIGIBILITY:
        return base + "which inclusion and exclusion criteria are met, uncertain, or not met, and why?"
    if name == SpecialistName.RENAL_LABS:
        return base + "assess renal function and lab thresholds (latest and prior CrCl with dates, staleness) against the trial minimum."
    if name == SpecialistName.GENOMICS:
        return base + "is the required biomarker documented as present, absent, or unknown, and what does that imply?"
    if name == SpecialistName.PROTOCOL:
        return base + "which exclusions apply, and does any protocol amendment modify them so PI confirmation is needed rather than an automatic exclusion?"
    if name == SpecialistName.WORKFLOW:
        return base + ("using patient_registry for the patient's coordinator_id and "
                       "treating_oncologist_id, coordinator_workload for that coordinator's capacity, "
                       "and scheduling_slots (by assigned_patient_id and the patient's site_id) for "
                       "assigned and available Screening/PI-consult slots: who owns the next action, "
                       "is there capacity, and what are the earliest slots for a repeat CrCl and PI review?")
    if name == SpecialistName.EVIDENCE:
        return base + "what external registry, FDA label, or guideline context is relevant to the biomarker and disease (informational only)?"
    return base + (ctx.focus_question or ctx.question)


def _call_specialist_agent(settings: Settings, agent_name: str, question: str) -> str:
    """Invoke a hosted Foundry specialist agent and return its cleaned answer (or "" on failure)."""
    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        from app.agent_client import _clean_answer

        client = AIProjectClient(endpoint=settings.PROJECT_ENDPOINT, credential=DefaultAzureCredential())
        oc = client.get_openai_client()
        response = oc.with_options(timeout=settings.SPECIALIST_TIMEOUT).responses.create(
            input=question,
            extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
        )
        return _clean_answer(getattr(response, "output_text", "") or "")
    except Exception as exc:  # noqa: BLE001 - resilience: fall back to the grounded summary
        logger.warning("Live specialist '%s' failed: %s", agent_name, exc)
        return ""


def _facts_block(result: SpecialistResult) -> str:
    """The findings already established (grounded, from the Fabric verdicts) for this role, so the
    live agent narrates over them instead of re-deriving from scratch. Cuts its tool round-trips."""
    lines = [f"- {f.label}: {f.status} ({f.detail})" for f in result.findings]
    if not lines:
        return ""
    body = "\n".join(lines)
    return (
        "\n\nEstablished facts for your role (narrate concisely over these; call a tool only to fill a "
        f"specific gap):\n{body}"
    )


def _live_roles(settings: Settings) -> set[SpecialistName]:
    """Parse LIVE_SPECIALIST_ROLES (comma-separated SpecialistName values) into the set of roles that
    narrate with a live agent. Unknown/blank entries are ignored."""
    valid = {r.value: r for r in SpecialistName}
    roles: set[SpecialistName] = set()
    for token in settings.LIVE_SPECIALIST_ROLES.split(","):
        role = valid.get(token.strip().lower())
        if role is not None:
            roles.add(role)
    return roles


def make_run_fn(settings: Settings):
    """Return a team run_fn that narrates the LIVE_SPECIALIST_ROLES with their live Foundry agent on
    the first (parallel) turn (reasoning over the already-established grounded facts), while structured
    findings/leads stay grounded in the Fabric verdicts. Deepened (depth > 0) turns stay grounded to
    bound cost and latency."""
    live_roles = _live_roles(settings)

    def run_fn(name: SpecialistName, ctx: SpecialistContext) -> SpecialistResult:
        result = _run_specialist(name, ctx)  # grounded ground-truth findings + leads
        agent_name = _agent_name_for(settings, name)
        # Only narrate with the live agent on the first (parallel) turn, for the configured roles.
        if name not in live_roles or ctx.depth > 0 or result.status == "failed" or not agent_name:
            return result
        answer = _call_specialist_agent(settings, agent_name, _focus_question(name, ctx) + _facts_block(result))
        if answer:
            result.summary = answer
        return result

    return run_fn


def run_live_team(
    settings: Settings,
    patient_id: str,
    trial_id: str,
    question: str,
    intent: str,
    budget: Budget | None = None,
) -> TeamRun:
    """Run the specialist team with live Foundry agents narrating the core roles."""
    ctx = SpecialistContext(question=question, intent=intent, patient_id=patient_id, trial_id=trial_id)
    ground_truth = build_ground_truth(patient_id, trial_id)
    return run_team(ctx, make_run_fn(settings), all_specialists(), ground_truth=ground_truth, budget=budget)
