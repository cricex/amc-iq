"""Read-only Fabric capacity state.

Surfaces whether an explicitly configured backing Fabric capacity is running or paused so the UI can
tell users up front whether a live assessment will work. This is strictly read-only: we never pause
or resume the capacity here.

State is read from Azure Resource Manager (the capacity is an ARM resource
`Microsoft.Fabric/capacities`), whose `properties.state` is one of Active / Paused / Pausing /
Resuming. We authenticate with `DefaultAzureCredential` (the same credential the live IQ adapters
use) for an ARM management token. Any failure (no credential, missing Reader permission, network,
throttling) degrades gracefully to `Unknown` so the endpoint never errors the UI. A short in-process
cache keeps UI polling from hammering ARM.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import httpx

from app.config import Settings

# ARM reports these; we normalize casing. Anything else (or a failure) becomes "Unknown".
_KNOWN_STATES = {"active": "Active", "paused": "Paused", "pausing": "Pausing", "resuming": "Resuming"}

_CACHE_TTL_SECONDS = 15.0


@dataclass(frozen=True)
class FabricStatus:
    state: str  # "Active" | "Paused" | "Pausing" | "Resuming" | "Unknown"
    capacity_name: str
    portal_url: str
    detail: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "state": self.state,
            "capacityName": self.capacity_name,
            "portalUrl": self.portal_url,
            "detail": self.detail,
        }


_lock = threading.Lock()
_cached: tuple[float, FabricStatus] | None = None


def _normalize_state(raw: object) -> str:
    if isinstance(raw, str):
        return _KNOWN_STATES.get(raw.strip().lower(), "Unknown")
    return "Unknown"


def _read_arm_state(settings: Settings) -> FabricStatus:
    """One ARM GET on the capacity resource. Returns an Unknown status (never raises) on any failure."""
    if settings.anonymous_mock_enabled:
        return FabricStatus(
            state="Unknown",
            capacity_name="",
            portal_url="",
            detail="Fabric capacity status is disabled in mock mode.",
        )
    portal_url = settings.fabric_portal_url
    name = settings.FABRIC_CAPACITY_NAME
    if not all(
        value.strip()
        for value in (
            settings.AZURE_SUBSCRIPTION_ID,
            settings.FABRIC_CAPACITY_RG,
            settings.FABRIC_CAPACITY_NAME,
        )
    ):
        return FabricStatus(
            state="Unknown",
            capacity_name="",
            portal_url=portal_url,
            detail="Fabric capacity status is not configured.",
        )
    try:
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
        token = credential.get_token(settings.ARM_SCOPE).token
        response = httpx.get(
            settings.fabric_capacity_arm_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        response.raise_for_status()
        properties = response.json().get("properties", {})
        state = _normalize_state(properties.get("state"))
        detail = None if state != "Unknown" else "Capacity state not reported by ARM."
        return FabricStatus(state=state, capacity_name=name, portal_url=portal_url, detail=detail)
    except Exception as exc:  # noqa: BLE001 - any failure degrades to Unknown, never errors the UI
        return FabricStatus(
            state="Unknown",
            capacity_name=name,
            portal_url=portal_url,
            detail=f"Capacity state unavailable: {type(exc).__name__}",
        )


def get_fabric_status(settings: Settings, *, force_refresh: bool = False) -> FabricStatus:
    """Cached (15s TTL) read of the Fabric capacity state. Thread-safe; never raises."""
    global _cached
    now = time.monotonic()
    with _lock:
        snapshot = _cached
        if not force_refresh and snapshot is not None and (now - snapshot[0]) < _CACHE_TTL_SECONDS:
            return snapshot[1]
    started = time.monotonic()
    status = _read_arm_state(settings)
    with _lock:
        # Compare-and-swap: only publish if no fresher result landed while we were fetching, and stamp
        # with the request start time so a slow response cannot mask a newer value for a full TTL.
        if _cached is snapshot:
            _cached = (started, status)
            return status
        if _cached is None:
            _cached = (started, status)
            return status
        return _cached[1]


def clear_cache() -> None:
    """Reset the cached status (used by tests)."""
    global _cached
    with _lock:
        _cached = None
