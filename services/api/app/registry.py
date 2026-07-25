from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings


@lru_cache(maxsize=4)
def load_registry(path: str | None = None) -> dict[str, Any]:
    registry_path = Path(path) if path else get_settings().registry_path
    with registry_path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def find_by_id(items: list[dict[str, Any]], item_id: str) -> dict[str, Any]:
    return next(item for item in items if str(item.get("id")) == item_id)


def maybe_find_by_id(items: list[dict[str, Any]], item_id: str) -> dict[str, Any] | None:
    return next((item for item in items if str(item.get("id")) == item_id), None)
