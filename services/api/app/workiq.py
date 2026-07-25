from __future__ import annotations

import time
import uuid
import logging
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
import msal

from app.config import Settings
from app.keyvault_certificate import (
    CertificateCredentialError,
    CertificateCredentialProvider,
    create_certificate_credential_provider,
)

logger = logging.getLogger(__name__)


class WorkIQError(RuntimeError):
    """Base class for typed Work IQ integration failures."""


class WorkIQConfigurationError(WorkIQError):
    pass


class WorkIQTokenExchangeError(WorkIQError):
    pass


class WorkIQRequestError(WorkIQError):
    pass


class WorkIQProtocolError(WorkIQError):
    pass


class OboApplication(Protocol):
    def acquire_token_on_behalf_of(self, *, user_assertion: str, scopes: list[str]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class WorkIQAttribution:
    title: str
    url: str


@dataclass(frozen=True)
class WorkIQAnswer:
    text: str
    task_id: str | None
    context_id: str | None
    duration_ms: int
    attributions: tuple[WorkIQAttribution, ...] = ()


def _create_obo_application(
    settings: Settings,
    certificate_provider: CertificateCredentialProvider,
) -> OboApplication:
    missing = [
        name
        for name, value in (
            ("AZURE_TENANT_ID", settings.AZURE_TENANT_ID),
            ("WORK_IQ_CLIENT_ID", settings.WORK_IQ_CLIENT_ID),
        )
        if not value.strip()
    ]
    if missing:
        raise WorkIQConfigurationError(f"Work IQ OBO configuration is missing: {', '.join(missing)}")
    try:
        certificate = certificate_provider.get_client_credential()
    except CertificateCredentialError as exc:
        raise WorkIQConfigurationError(str(exc)) from exc
    return msal.ConfidentialClientApplication(
        client_id=settings.WORK_IQ_CLIENT_ID,
        authority=settings.obo_authority,
        client_credential=certificate,
        timeout=settings.WORK_IQ_TIMEOUT_SECONDS,
    )


def _extract_text(response: dict[str, Any]) -> tuple[str, str | None, str | None]:
    if error := response.get("error"):
        if isinstance(error, dict):
            code = error.get("code", "unknown")
            message = error.get("message", "Work IQ returned a JSON-RPC error")
            raise WorkIQProtocolError(f"Work IQ JSON-RPC error {code}: {message}")
        raise WorkIQProtocolError(f"Work IQ JSON-RPC error: {error}")

    result = response.get("result")
    if not isinstance(result, dict):
        raise WorkIQProtocolError("Work IQ response is missing result")

    task = result.get("task")
    if isinstance(task, dict):
        status = task.get("status")
        state = str(status.get("state", "")) if isinstance(status, dict) else ""
        if state in {"TASK_STATE_FAILED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED"}:
            raise WorkIQProtocolError(f"Work IQ task ended in {state}")
        artifacts = task.get("artifacts")
        if not isinstance(artifacts, list):
            artifacts = []
        texts = _texts_from_parts(
            part
            for artifact in artifacts
            if isinstance(artifact, dict)
            for part in _part_list(artifact)
        )
        if texts:
            return "\n".join(texts), _string_or_none(task.get("id")), _string_or_none(task.get("contextId"))

    message = result.get("message")
    if isinstance(message, dict):
        texts = _texts_from_parts(message.get("parts", []))
        if texts:
            return "\n".join(texts), None, _string_or_none(message.get("contextId"))

    raise WorkIQProtocolError("Work IQ response contains no text answer")


def _texts_from_parts(parts: Any) -> list[str]:
    texts: list[str] = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"].strip():
            texts.append(part["text"].strip())
    return texts


def _part_list(container: dict[str, Any]) -> list[Any]:
    parts = container.get("parts")
    return parts if isinstance(parts, list) else []


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _extract_attributions(response: dict[str, Any]) -> tuple[WorkIQAttribution, ...]:
    result = response.get("result")
    if not isinstance(result, dict):
        return ()

    containers: list[dict[str, Any]] = []
    message = result.get("message")
    if isinstance(message, dict):
        containers.append(message)

    task = result.get("task")
    if isinstance(task, dict):
        containers.append(task)
        status = task.get("status")
        if isinstance(status, dict):
            status_message = status.get("message")
            if isinstance(status_message, dict):
                containers.append(status_message)
        artifacts = task.get("artifacts")
        if isinstance(artifacts, list):
            containers.extend(item for item in artifacts if isinstance(item, dict))

    attributions: list[WorkIQAttribution] = []
    seen: set[tuple[str, str]] = set()
    for container in containers:
        metadata = container.get("metadata")
        if not isinstance(metadata, dict):
            continue
        raw_attributions = metadata.get("attributions")
        if not isinstance(raw_attributions, list):
            continue
        for raw in raw_attributions:
            if not isinstance(raw, dict):
                continue
            attribution_type = str(raw.get("attributionType", ""))
            if attribution_type.lower() != "citation":
                continue
            title = _string_or_none(raw.get("providerDisplayName")) or "Work IQ citation"
            url = _validated_web_url(raw.get("seeMoreWebUrl"))
            if url is None:
                continue
            key = (title, url)
            if key in seen:
                continue
            attributions.append(WorkIQAttribution(title=title, url=url))
            seen.add(key)
    return tuple(attributions)


def _validated_web_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return url


def _http_error_detail(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    message = payload.get("message")
    if isinstance(error, dict):
        code = _string_or_none(error.get("code"))
        nested_message = _string_or_none(error.get("message"))
        parts = [part for part in (code, nested_message) if part]
        return ": ".join(parts) if parts else None
    parts = [
        part
        for part in (
            _string_or_none(error),
            _string_or_none(message),
        )
        if part
    ]
    return ": ".join(parts) if parts else None


class WorkIQClient:
    def __init__(
        self,
        settings: Settings,
        *,
        obo_application: OboApplication | None = None,
        http_client: httpx.Client | None = None,
        certificate_provider: CertificateCredentialProvider | None = None,
    ) -> None:
        self.settings = settings
        self._obo_application = obo_application
        self._http_client = http_client
        self._certificate_provider = certificate_provider or create_certificate_credential_provider(
            settings
        )

    def ask(self, *, user_assertion: str, question: str) -> WorkIQAnswer:
        if not user_assertion:
            raise WorkIQTokenExchangeError("A delegated user token is required for Work IQ")

        application = self._obo_application or _create_obo_application(
            self.settings,
            self._certificate_provider,
        )
        token_result = application.acquire_token_on_behalf_of(
            user_assertion=user_assertion,
            scopes=[self.settings.WORK_IQ_SCOPE],
        )
        access_token = token_result.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            error = str(token_result.get("error", "token_exchange_failed"))
            description = str(token_result.get("error_description", "OBO token exchange returned no access token"))
            logger.warning("Work IQ OBO failed: error=%s description=%s", error, description)
            raise WorkIQTokenExchangeError(f"Work IQ OBO failed ({error})")

        return ask_with_access_token(
            access_token=access_token,
            question=question,
            endpoint=self.settings.WORK_IQ_ENDPOINT,
            timeout_seconds=self.settings.WORK_IQ_TIMEOUT_SECONDS,
            timezone_offset_minutes=self.settings.WORK_IQ_TIMEZONE_OFFSET_MINUTES,
            timezone=self.settings.WORK_IQ_TIMEZONE,
            http_client=self._http_client,
        )


def ask_with_access_token(
    *,
    access_token: str,
    question: str,
    endpoint: str,
    timeout_seconds: float,
    timezone_offset_minutes: int,
    timezone: str,
    http_client: httpx.Client | None = None,
) -> WorkIQAnswer:
    """Send an A2A v1.0 message using an already-acquired delegated Work IQ token."""
    if not access_token:
        raise WorkIQTokenExchangeError("A delegated Work IQ access token is required")

    request_id = str(uuid.uuid4())
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "SendMessage",
        "params": {
            "message": {
                "role": "ROLE_USER",
                "messageId": str(uuid.uuid4()),
                "parts": [{"text": question}],
                "metadata": {
                    "Location": {
                        "timeZoneOffset": timezone_offset_minutes,
                        "timeZone": timezone,
                    }
                },
            }
        },
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "A2A-Version": "1.0",
    }

    started = time.perf_counter()
    owned_client = http_client is None
    client = http_client or httpx.Client(
        timeout=httpx.Timeout(timeout_seconds, connect=10.0)
    )
    try:
        response = client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise WorkIQRequestError("Work IQ request timed out") from exc
    except httpx.HTTPStatusError as exc:
        request_id_header = exc.response.headers.get("request-id") or exc.response.headers.get(
            "x-ms-request-id"
        )
        suffix = f" (request-id: {request_id_header})" if request_id_header else ""
        detail = _http_error_detail(exc.response)
        detail_suffix = f": {detail}" if detail else ""
        logger.warning(
            "Work IQ HTTP failure: status=%s request_id=%s detail=%s",
            exc.response.status_code,
            request_id_header or "unavailable",
            detail or "unavailable",
        )
        raise WorkIQRequestError(
            f"Work IQ returned HTTP {exc.response.status_code}{detail_suffix}{suffix}"
        ) from exc
    except httpx.HTTPError as exc:
        raise WorkIQRequestError("Work IQ request failed") from exc
    finally:
        if owned_client:
            client.close()

    try:
        body = response.json()
    except ValueError as exc:
        raise WorkIQProtocolError("Work IQ returned invalid JSON") from exc
    if not isinstance(body, dict):
        raise WorkIQProtocolError("Work IQ returned an invalid JSON-RPC envelope")
    if body.get("error") is not None:
        _extract_text(body)
    if str(body.get("id")) != request_id:
        raise WorkIQProtocolError("Work IQ response id does not match the request")

    text, task_id, context_id = _extract_text(body)
    attributions = _extract_attributions(body)
    duration_ms = round((time.perf_counter() - started) * 1000)
    logger.info(
        "Work IQ completed: task_id=%s attributions=%d duration_ms=%d",
        task_id or "unavailable",
        len(attributions),
        duration_ms,
    )
    return WorkIQAnswer(
        text=text,
        task_id=task_id,
        context_id=context_id,
        duration_ms=duration_ms,
        attributions=attributions,
    )
