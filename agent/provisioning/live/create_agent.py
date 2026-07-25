"""Create (or update) the AMC IQ hosted eligibility agent in the Foundry project and
smoke-test it against the live Foundry IQ knowledge base MCP tool.

Auth: DefaultAzureCredential (your `az login`). No device login required for create/invoke
here because we use your user token; the agent itself calls the KB via the project's
managed identity and operator-selected RemoteTool connection.
"""
from __future__ import annotations

import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console safety
except Exception:  # pragma: no cover
    pass

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    MCPTool,
    PromptAgentDefinition,
)
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
CONNECTION = required_environment("KB_CONNECTION")
WEB_CONNECTION = required_environment("WEB_CONNECTION")
FABRIC_CONNECTION = required_environment("FABRIC_CONNECTION") if INCLUDE_FABRIC else ""
AGENT_NAME = required_environment("AGENT_NAME")
MODEL = required_environment("AGENT_MODEL")

INSTRUCTIONS = """You are the AMC IQ precision-oncology clinical-trial eligibility assistant.
You help a thoracic oncology care team assess whether a synthetic patient may be eligible for a
clinical trial and what the safe next step is. This is a SANDBOX with synthetic data only.

Tools (each is a Microsoft IQ layer):
- amciq_foundry_kb (Foundry IQ): institutional knowledge, trial protocols/amendments, IRB/consent
  policy, SOPs, and synthetic patient notes/pathology/genomics. Use this for eligibility criteria,
  institutional policy, and patient documentation. This is the authoritative source for eligibility.
- amciq_web_kb (Web IQ): live external/public web context, treatment landscape and standard of care.
  Use this only for external background context, clearly framed as public info, never as the
  institutional eligibility rule.
- Microsoft Fabric data agent (Fabric IQ): structured clinical/operational data, patient registry,
  labs (e.g. latest CrCl by date), treatment history, trial thresholds, scheduling. Use this to pull
  precise patient facts and trial numeric criteria.

Rules:
- ALWAYS ground eligibility answers in amciq_foundry_kb. Never answer eligibility from training data.
  Cite sources.
- Use Fabric IQ for the patient's structured facts (latest CrCl and date, ECOG, prior therapies).
- Be precise about eligibility criteria, especially borderline renal thresholds (e.g. CrCl vs a
  minimum) and ambiguous prior-therapy exclusions. Explicitly flag when a value is close to a
  threshold or when a protocol amendment requires PI confirmation or a repeat lab.
- Never give a definitive medical decision. Surface missing/uncertain data and recommend human
  review, routing to the trial coordinator and PI.
- Keep the answer concise and structured: assessment, key criteria (met / uncertain / not met),
  missing data, external context (Web IQ, optional), and the recommended next action with the
  responsible human owner.
"""


def main() -> None:
    credential = DefaultAzureCredential()
    client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)

    kb_tool = MCPTool(
        type="mcp",
        server_label="amciq_foundry_kb",
        server_url=KB_MCP,
        project_connection_id=CONNECTION,
        allowed_tools=["knowledge_base_retrieve"],
        require_approval="never",
    )
    web_tool = MCPTool(
        type="mcp",
        server_label="amciq_web_kb",
        server_url=WEB_MCP,
        project_connection_id=WEB_CONNECTION,
        allowed_tools=["knowledge_base_retrieve"],
        require_approval="never",
    )
    tools = [kb_tool, web_tool]
    # Fabric IQ: the published Fabric Data Agent is consumed over its MCP endpoint, wired the same
    # way as the Foundry IQ / Web IQ knowledge bases (operator-selected RemoteTool connection,
    # ProjectManagedIdentity auth, audience https://api.fabric.microsoft.com). The project managed
    # identity must be a member of the Fabric workspace and the backing capacity must be active.
    # This replaces the older AzureFabric/CustomKeys preview-tool path (OBO boundary). Gated behind
    # INCLUDE_FABRIC so the agent stays functional when the capacity is paused.
    if INCLUDE_FABRIC:
        tools.append(
            MCPTool(
                type="mcp",
                server_label="amciq_fabric_dataagent",
                server_url=FABRIC_MCP,
                project_connection_id=FABRIC_CONNECTION,
                allowed_tools=[FABRIC_MCP_TOOL],
                require_approval="never",
            )
        )
    definition = PromptAgentDefinition(model=MODEL, instructions=INSTRUCTIONS, tools=tools)
    agent = client.agents.create_version(agent_name=AGENT_NAME, definition=definition)
    version = getattr(agent, "version", "?")
    print(f"Agent '{AGENT_NAME}' created/updated (version {version}) with model {MODEL}.")

    print("\nInvoking agent (smoke test)...")
    openai_client = client.get_openai_client()
    response = openai_client.responses.create(
        input=(
            "For patient PT-1042 (metastatic NSCLC, EGFR exon 20 insertion, ECOG 1, most recent "
            "CrCl 48 mL/min, prior first-line carboplatin+pemetrexed), is trial NCT99004324 a fit, "
            "and what should the care team do next?"
        ),
        extra_body={"agent_reference": {"name": AGENT_NAME, "type": "agent_reference"}},
    )
    text = getattr(response, "output_text", None) or str(response)
    print("\n=== Agent answer ===\n" + text)


if __name__ == "__main__":
    main()
