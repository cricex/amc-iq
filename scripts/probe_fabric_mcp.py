"""Probe: query the published AMC IQ Fabric Data Agent via its MCP endpoint using
the caller's delegated Fabric token (Azure CLI login). Proves the runtime consumption
surface works before wiring it into the backend + hosted agent.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date

from azure.identity import AzureCliCredential
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

def required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Required environment variable {name} is not set.")
    return value


QUESTION = "What is the latest CrCl for patient PT-1042, with the lab date? Also give the prior CrCl value and date."
EXPECTED_MARKERS = ("PT-1042", "48", "55")
EXPECTED_DATES = (date(2026, 6, 18), date(2026, 5, 20))


def _date_variants(value: date) -> tuple[str, ...]:
    return (
        value.isoformat(),
        f"{value.month}/{value.day}/{value.year}",
        f"{value.month:02d}/{value.day:02d}/{value.year}",
        f"{value.strftime('%B')} {value.day}, {value.year}",
        f"{value.strftime('%b')} {value.day}, {value.year}",
    )


def missing_expected_evidence(answer: str) -> list[str]:
    missing = [marker for marker in EXPECTED_MARKERS if marker not in answer]
    missing.extend(
        value.isoformat()
        for value in EXPECTED_DATES
        if not any(variant in answer for variant in _date_variants(value))
    )
    return missing


async def main() -> None:
    workspace_id = required_environment("FABRIC_WORKSPACE_ID")
    data_agent_id = required_environment("FABRIC_DATA_AGENT_ID")
    mcp_url = (
        f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{workspace_id}"
        f"/dataagents/{data_agent_id}/agent"
    )
    cred = AzureCliCredential()
    token = cred.get_token("https://api.fabric.microsoft.com/.default").token
    headers = {"Authorization": f"Bearer {token}"}

    async with streamablehttp_client(mcp_url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])
            if not tools.tools:
                raise RuntimeError("Fabric MCP returned no tools.")
            tool = tools.tools[0]
            props = (tool.inputSchema or {}).get("properties", {})
            arg_name = next(iter(props), "question")
            print("ARG:", arg_name)
            result = await session.call_tool(tool.name, {arg_name: QUESTION})
            if getattr(result, "isError", False):
                raise RuntimeError("Fabric MCP tool returned an error result.")
            answer = "\n".join(
                text
                for block in result.content
                if (text := getattr(block, "text", None))
            ).strip()
            if not answer:
                raise RuntimeError("Fabric MCP tool returned no text.")
            missing = missing_expected_evidence(answer)
            if missing:
                raise RuntimeError(
                    "Fabric MCP answer is missing expected evidence: "
                    + ", ".join(missing)
                )
            for block in result.content:
                text = getattr(block, "text", None)
                if text:
                    print("ANSWER:\n" + text)


if __name__ == "__main__":
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())
