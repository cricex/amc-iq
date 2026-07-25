"""Small helpers for explicit live-provisioning configuration."""
from __future__ import annotations

import os


def required_environment(name: str) -> str:
    """Return a non-empty operator value or stop before any cloud request is attempted."""
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Required environment variable {name} is not set.")
    return value
