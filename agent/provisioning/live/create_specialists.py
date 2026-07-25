"""Create (or update) the AMC IQ real Foundry specialist agents and smoke-test them.

Provisions the 6 scoped hosted Foundry agents that make up the "specialist team". Each is a
PromptAgentDefinition with its own instructions and a scoped set of the existing IQ MCP tools
(Foundry IQ KB, Web IQ KB, Fabric IQ Data Agent). The backend uses these behind USE_LIVE_SPECIALISTS
to narrate each role; structured eligibility findings stay Fabric-native ground truth (the
amciq-eligibility-evaluator + Data Agent), the agents provide the tool-grounded narrative.

Scope: all 6 roles by default (eligibility, renal-labs, genomics, protocol, workflow, evidence). Set
SPECIALISTS env to override the list.

Auth: DefaultAzureCredential (your `az login`). The agents call the KB/Fabric via the project's
managed identity (RemoteTool connections). Set INCLUDE_FABRIC=true to attach the Fabric tool.
"""
from __future__ import annotations

import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPTool, PromptAgentDefinition
from azure.identity import DefaultAzureCredential
from live_environment import required_environment

PROJECT_ENDPOINT = required_environment("PROJECT_ENDPOINT")
KB_MCP = required_environment("KB_MCP_ENDPOINT")
WEB_MCP = required_environment("WEB_MCP_ENDPOINT")
INCLUDE_FABRIC = os.getenv("INCLUDE_FABRIC", "false").lower() == "true"
if INCLUDE_FABRIC:
    FABRIC_MCP = os.getenv("FABRIC_MCP_ENDPOINT", "").strip()
    if not FABRIC_MCP:
        fabric_workspace_id = required_environment("FABRIC_WORKSPACE_ID")
        fabric_data_agent_id = required_environment("FABRIC_DATA_AGENT_ID")
        FABRIC_MCP = (
            "https://api.fabric.microsoft.com/v1/mcp/workspaces/"
            f"{fabric_workspace_id}/dataagents/{fabric_data_agent_id}/agent"
        )
else:
    FABRIC_MCP = ""
FABRIC_MCP_TOOL = required_environment("FABRIC_MCP_TOOL") if INCLUDE_FABRIC else ""
KB_CONNECTION = required_environment("KB_CONNECTION")
WEB_CONNECTION = required_environment("WEB_CONNECTION")
FABRIC_CONNECTION = required_environment("FABRIC_CONNECTION") if INCLUDE_FABRIC else ""
SPECIALIST_AGENT_PREFIX = required_environment("SPECIALIST_AGENT_PREFIX")
MODEL = required_environment("AGENT_MODEL")

_COMMON_RULES = """
This is a SANDBOX with synthetic data only, no PHI, not clinical decision support. Never give a
definitive medical decision. You may be given ESTABLISHED FACTS in the question: narrate concisely
over them and call a tool only to fill a specific gap, do not re-derive what you were given. Keep
your answer to 2 to 4 sentences, grounded and specific. Use clinical language ("renal function",
"CrCl"), never "renal failure". Surface uncertainty and recommend human review (trial coordinator and
PI). Do not use em-dashes.
"""

# name suffix -> (display role, scoped instructions, tools)
SPECIALIST_DEFS = {
    "eligibility": (
        "criteria matching",
        """You are the AMC IQ ELIGIBILITY specialist. You match a patient against a trial's inclusion
and exclusion criteria. Use Foundry IQ (amciq_foundry_kb) for the protocol criteria, amendments, and
institutional policy, and Fabric IQ for the patient's structured facts. For each criterion state
met / uncertain / not met with the specific evidence, and call out borderline renal thresholds and
amendment-modified exclusions that need PI confirmation.""",
        ["kb", "fabric"],
    ),
    "renal-labs": (
        "renal function and lab safety",
        """You are the AMC IQ RENAL/LABS specialist. You focus on renal function and lab thresholds,
trends, and staleness. Use Fabric IQ for the patient's CrCl_CKD-EPI series (latest and prior with
dates), ECOG, and other labs. Assess the latest CrCl against the trial minimum, whether it is
borderline, whether a prior value was above threshold, and whether the reading is stale and warrants
a repeat draw before screening.""",
        ["kb", "fabric"],
    ),
    "genomics": (
        "molecular and biomarker interpretation",
        """You are the AMC IQ GENOMICS specialist. You interpret molecular reports and variant status.
Use Foundry IQ (amciq_foundry_kb) for pathology/genomics documentation and Fabric IQ for the
patient's recorded biomarkers. Confirm whether the trial's required biomarker is documented as
present, absent, or unknown (no molecular workup), and flag when NGS is needed or when a co-mutation
suggests alternative trials.""",
        ["kb", "fabric"],
    ),
    "protocol": (
        "exclusion interpretation and amendment deltas",
        """You are the AMC IQ PROTOCOL specialist. You interpret exclusion criteria and protocol
amendment deltas. Use Foundry IQ (amciq_foundry_kb) for the protocol document, its amendments, and
institutional policy (IRB, consent). Determine whether an exclusion applies as written and whether a
later amendment modifies or relaxes it (for example prior-therapy or line-of-therapy language) so it
requires PI confirmation rather than an automatic exclusion. Name the governing protocol version and
the specific amendment clause, and use Fabric IQ to check the patient's treatment history against the
exclusion.""",
        ["kb", "fabric"],
    ),
    "workflow": (
        "tasks, ownership, scheduling, capacity",
        """You are the AMC IQ WORKFLOW specialist. You cover care-team tasks, ownership, scheduling,
and capacity. Use Fabric IQ (the Data Agent over the Lakehouse) with these tables and keys:
- patient_registry: the patient's coordinator_id and treating_oncologist_id (the owners).
- coordinator_workload (keyed by coordinator_id): that coordinator's open_screenings, pending_tasks,
  and capacity_this_week.
- scheduling_slots: the patient's already-assigned slots (where assigned_patient_id = the patient),
  plus available slots (available = true) of slot_type Screening or "PI consult" at the patient's
  site_id.
Report who owns the next action (name the coordinator and PI), whether the coordinator has capacity,
and the earliest available slots to schedule a repeat CrCl draw and a PI review before formal
screening. Scheduling is by patient and site, not by trial; do not try to join slots to a trial. Do
not make eligibility judgments; you enable the next step.""",
        ["fabric"],
    ),
    "evidence": (
        "external literature, registry, and guidelines",
        """You are the AMC IQ EVIDENCE specialist. You provide external literature, registry, and
guideline context. Use Web IQ (amciq_web_kb) for external trial registry entries, FDA labels, and
guideline or standard-of-care context relevant to the patient's biomarker and disease. Cite the
external sources, and be explicit that this external context INFORMS but does not by itself determine
AMC trial eligibility (institutional criteria and PI review govern).""",
        ["web"],
    ),
}


def _tools(scoped: list[str]) -> list[MCPTool]:
    tools: list[MCPTool] = []
    if "kb" in scoped:
        tools.append(MCPTool(type="mcp", server_label="amciq_foundry_kb", server_url=KB_MCP,
                             project_connection_id=KB_CONNECTION, allowed_tools=["knowledge_base_retrieve"],
                             require_approval="never"))
    if "web" in scoped:
        tools.append(MCPTool(type="mcp", server_label="amciq_web_kb", server_url=WEB_MCP,
                             project_connection_id=WEB_CONNECTION, allowed_tools=["knowledge_base_retrieve"],
                             require_approval="never"))
    if "fabric" in scoped and INCLUDE_FABRIC:
        tools.append(MCPTool(type="mcp", server_label="amciq_fabric_dataagent", server_url=FABRIC_MCP,
                             project_connection_id=FABRIC_CONNECTION, allowed_tools=[FABRIC_MCP_TOOL],
                             require_approval="never"))
    return tools


def main() -> None:
    which = os.getenv("SPECIALISTS", "eligibility,renal-labs,genomics,protocol,workflow,evidence").split(",")
    client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())
    for suffix in [w.strip() for w in which if w.strip()]:
        if suffix not in SPECIALIST_DEFS:
            print(f"skip unknown specialist '{suffix}'")
            continue
        role, instructions, scoped = SPECIALIST_DEFS[suffix]
        agent_name = f"{SPECIALIST_AGENT_PREFIX}-{suffix}"
        tools = _tools(scoped)
        # A specialist scoped only to Fabric ends up with NO tools when INCLUDE_FABRIC=false, which
        # would make its live narration ungrounded. Fail loudly rather than provision a toolless agent.
        if not tools:
            needs_fabric = scoped == ["fabric"]
            hint = " Re-run with INCLUDE_FABRIC=true." if needs_fabric else ""
            raise SystemExit(
                f"refusing to provision '{agent_name}': it would have NO tools (scoped={scoped}, "
                f"INCLUDE_FABRIC={str(INCLUDE_FABRIC).lower()}).{hint}"
            )
        definition = PromptAgentDefinition(
            model=MODEL,
            instructions=instructions.strip() + "\n" + _COMMON_RULES.strip(),
            tools=tools,
        )
        agent = client.agents.create_version(agent_name=agent_name, definition=definition)
        print(f"specialist '{agent_name}' ({role}) -> version {getattr(agent, 'version', '?')}, tools={len(definition.tools)}")

    # Smoke test the eligibility specialist if provisioned.
    if "eligibility" in [w.strip() for w in which]:
        print("\nSmoke test (eligibility specialist)...")
        oc = client.get_openai_client()
        r = oc.responses.create(
            input="For patient PT-1042 and trial NCT99004324, which criteria are met, uncertain, or not met, and why?",
            extra_body={"agent_reference": {"name": f"{SPECIALIST_AGENT_PREFIX}-eligibility", "type": "agent_reference"}},
        )
        print((getattr(r, "output_text", None) or str(r))[:1200])


if __name__ == "__main__":
    main()
