"""Live hosted-agent client.

Invokes the hosted Foundry agent (`amciq-agent-eligibility`) through the Responses API with the user's
actual question, so the answer and the reasoning steps are generated per question by the agent using
its Foundry IQ / Fabric IQ / Web IQ MCP tools, rather than composed by hardcoded logic.

We parse `response.output` for:
- `mcp_call` items: the real tool calls the agent made (which IQ layer, the query, status) in order.
- the final `message` item / `output_text`: the question-specific answer.

The call is blocking (one Responses API round trip that internally runs the tools), so callers run it
in a thread. On any failure it returns None and the orchestrator falls back to deterministic composition.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.schemas import Source

# Map the agent's MCP server labels to our IQ source enum.
SERVER_LABEL_TO_SOURCE: dict[str, Source] = {
    "amciq_foundry_kb": Source.FOUNDRY,
    "amciq_web_kb": Source.WEB,
    "amciq_fabric_dataagent": Source.FABRIC,
}


@dataclass
class AgentToolCall:
    source: Source
    server_label: str
    tool_name: str
    query: str
    status: str  # "completed" | "failed"


@dataclass
class AgentRun:
    answer: str
    tool_calls: list[AgentToolCall] = field(default_factory=list)


def _clean_answer(text: str) -> str:
    """Strip inline citation markers, remove em/en dashes (house style), and normalize whitespace."""
    cleaned = re.sub(r"\u3010[^\u3011]*\u3011", "", text)  # remove CJK-bracket source markers
    cleaned = re.sub(r"\[ref_id:\d+\]", "", cleaned)
    cleaned = cleaned.replace(" \u2014 ", ", ").replace(" \u2013 ", ", ")
    cleaned = cleaned.replace("\u2014", "-").replace("\u2013", "-")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _query_from_arguments(arguments: Any) -> str:
    if isinstance(arguments, str):
        text = arguments
    else:
        text = str(arguments or "")
    # arguments is a JSON string like {"queries":["..."]} or {"userQuestion":"..."}
    match = re.search(r'"(?:queries|userQuestion)"\s*:\s*(?:\[\s*)?"([^"]+)"', text)
    if match:
        return match.group(1)
    return text[:200]


def run_agent(settings: Settings, question: str, timeout: float = 120.0) -> AgentRun | None:
    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        client = AIProjectClient(endpoint=settings.PROJECT_ENDPOINT, credential=DefaultAzureCredential())
        openai_client = client.get_openai_client()
        response = openai_client.with_options(timeout=timeout).responses.create(
            input=question,
            extra_body={"agent_reference": {"name": settings.AGENT_NAME, "type": "agent_reference"}},
        )
    except Exception:
        return None

    answer = _clean_answer(getattr(response, "output_text", "") or "")
    tool_calls: list[AgentToolCall] = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "mcp_call":
            continue
        server_label = str(getattr(item, "server_label", "") or "")
        source = SERVER_LABEL_TO_SOURCE.get(server_label)
        if source is None:
            continue
        tool_calls.append(
            AgentToolCall(
                source=source,
                server_label=server_label,
                tool_name=str(getattr(item, "name", "") or ""),
                query=_query_from_arguments(getattr(item, "arguments", "")),
                status="completed" if str(getattr(item, "status", "")) == "completed" else "failed",
            )
        )

    if not answer and not tool_calls:
        return None
    return AgentRun(answer=answer, tool_calls=tool_calls)


def stream_agent(settings: Settings, question: str, timeout: float = 180.0):
    """Streaming variant: yields live progress dicts as the agent works, then a final
    {"phase": "done", "run": AgentRun | None}. Progress phases:
      - {"phase": "tool_start", "source": Source, "query": str}
      - {"phase": "tool_done", "source": Source}
      - {"phase": "answer_delta", "text": str}
    Used to drive an ephemeral 'thinking' preview in the UI. Runs the blocking SDK iterator, so the
    caller executes it in a worker thread and bridges events onto the event loop.
    """
    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        client = AIProjectClient(endpoint=settings.PROJECT_ENDPOINT, credential=DefaultAzureCredential())
        openai_client = client.get_openai_client()
        stream = openai_client.with_options(timeout=timeout).responses.create(
            input=question,
            extra_body={"agent_reference": {"name": settings.AGENT_NAME, "type": "agent_reference"}},
            stream=True,
        )
    except Exception:
        yield {"phase": "done", "run": None}
        return

    item_source: dict[str, Source] = {}
    tool_calls: list[AgentToolCall] = []
    raw_parts: list[str] = []
    buffer = ""

    def _dedash(text: str) -> str:
        return text.replace("\u2014", "-").replace("\u2013", "-")

    try:
        for event in stream:
            etype = str(getattr(event, "type", "") or "")
            if etype == "response.output_item.added":
                item = getattr(event, "item", None)
                if item is not None and getattr(item, "type", None) == "mcp_call":
                    label = str(getattr(item, "server_label", "") or "")
                    source = SERVER_LABEL_TO_SOURCE.get(label)
                    if source is not None:
                        item_source[str(getattr(item, "id", "") or "")] = source
            elif etype == "response.mcp_call_arguments.done":
                source = item_source.get(str(getattr(event, "item_id", "") or ""))
                if source is not None:
                    query = _query_from_arguments(getattr(event, "arguments", ""))
                    tool_calls.append(AgentToolCall(source=source, server_label=source.value, tool_name="", query=query, status="completed"))
            elif etype == "response.output_text.delta":
                delta = str(getattr(event, "delta", "") or "")
                raw_parts.append(delta)
                buffer += delta
                if len(buffer) >= 24 or "\n" in buffer:
                    yield {"phase": "answer_delta", "text": _dedash(buffer)}
                    buffer = ""
    except Exception:
        pass

    if buffer:
        yield {"phase": "answer_delta", "text": _dedash(buffer)}
    answer = _clean_answer("".join(raw_parts))
    run = AgentRun(answer=answer, tool_calls=tool_calls) if (answer or tool_calls) else None
    yield {"phase": "done", "run": run}
