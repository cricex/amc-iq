from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sse_starlette.sse import EventSourceResponse

from app.orchestrator import Orchestrator
from app.schemas import AskRequest, ErrorEventPayload

logger = logging.getLogger(__name__)


async def event_generator(
    request: AskRequest,
    orchestrator: Orchestrator,
    user_access_token: str | None = None,
) -> AsyncIterator[dict[str, str]]:
    try:
        async for event, data in orchestrator.stream(request, user_access_token):
            yield {"event": event, "data": data}
    except Exception as exc:  # pragma: no cover
        logger.exception("Assessment stream failed.")
        yield {"event": "error", "data": ErrorEventPayload(message=str(exc)).model_dump_json()}


def sse_response(
    request: AskRequest,
    orchestrator: Orchestrator,
    user_access_token: str | None = None,
) -> EventSourceResponse:
    return EventSourceResponse(event_generator(request, orchestrator, user_access_token))
