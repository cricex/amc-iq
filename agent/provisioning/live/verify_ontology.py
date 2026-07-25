"""Verify the Fabric Data Agent answers relationship-aware questions via GraphModel NL2GQL.

Queries the published Data Agent MCP endpoint with a relationship question and a direct-fact
question, using the same MCP call path as the backend LiveFabricIQ adapter.
"""
from __future__ import annotations

import asyncio
import os

import requests
from live_environment import required_environment

WS = required_environment("FABRIC_WS")
DATA_AGENT_NAME = required_environment("DATA_AGENT_NAME")
GRAPH_MODEL_NAME = required_environment("GRAPH_MODEL_NAME")
SCOPE = "https://api.fabric.microsoft.com/.default"

CHECKS = [
    (
        f"Using {GRAPH_MODEL_NAME}, which trial is patient PT-1042 screened for?",
        ("NCT99004324",),
    ),
    (
        f"Using {GRAPH_MODEL_NAME}, which criteria are required by trial NCT99004324?",
        ("NCT99004324-REN", "NCT99004324-BIO"),
    ),
    (
        f"Using {GRAPH_MODEL_NAME}, who treats patient PT-1042?",
        ("Dr. Priya Anand",),
    ),
]


def resolve_data_agent_id() -> str:
    configured = os.environ.get("FABRIC_DA")
    if configured:
        return configured

    from azure.identity import DefaultAzureCredential

    token = DefaultAzureCredential().get_token(SCOPE).token
    response = requests.get(
        f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/items",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    response.raise_for_status()
    matches = [
        item["id"]
        for item in response.json().get("value", [])
        if item.get("type") == "DataAgent"
        and item.get("displayName") == DATA_AGENT_NAME
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one Data Agent named {DATA_AGENT_NAME!r}, found {len(matches)}. "
            "Set FABRIC_DA to its item id or provision it first with "
            "ATTACH_GRAPH_TO_DA=1."
        )
    return matches[0]


async def ask(question: str, data_agent_id: str) -> str:
    from azure.identity import DefaultAzureCredential
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    mcp_url = (
        f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{WS}"
        f"/dataagents/{data_agent_id}/agent"
    )
    token = DefaultAzureCredential().get_token(SCOPE).token
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(mcp_url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool = tools.tools[0]
            props = (tool.inputSchema or {}).get("properties", {})
            arg = next(iter(props), "userQuestion")
            result = await session.call_tool(tool.name, {arg: question})
            if os.environ.get("FABRIC_VERIFY_DEBUG") == "1":
                print(result.model_dump_json(indent=2))
            return "".join(getattr(b, "text", "") for b in result.content).strip()


async def main() -> None:
    data_agent_id = resolve_data_agent_id()
    failures = []
    for question, expected_markers in CHECKS:
        print("=" * 70)
        print("Q:", question)
        answer = await ask(question, data_agent_id)
        print("A:", answer)
        missing = [
            marker for marker in expected_markers if marker.casefold() not in answer.casefold()
        ]
        if missing:
            failures.append(f"{question!r} missing {missing}")
    if failures:
        raise RuntimeError(
            "Fabric Data Agent GraphModel verification failed: " + "; ".join(failures)
        )


if __name__ == "__main__":
    asyncio.run(main())
