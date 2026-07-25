from __future__ import annotations

from app.config import Settings
from app.schemas import Source
from app.sources.base import IQSource
from app.sources.fabric import LiveFabricIQ, MockFabricIQ
from app.sources.foundry import LiveFoundryIQ, MockFoundryIQ
from app.sources.web import LiveWebIQ, MockWebIQ
from app.sources.work import LiveWorkIQ, MockWorkIQ


def create_sources(settings: Settings) -> list[IQSource]:
    return [
        LiveFoundryIQ(settings) if settings.USE_LIVE_FOUNDRY else MockFoundryIQ(settings),
        LiveFabricIQ(settings) if settings.USE_LIVE_FABRIC else MockFabricIQ(settings),
        LiveWorkIQ(settings) if settings.USE_LIVE_WORK else MockWorkIQ(settings),
        LiveWebIQ(settings) if settings.USE_LIVE_WEB else MockWebIQ(settings),
    ]


def create_mock_fallbacks(settings: Settings) -> dict[Source, IQSource]:
    """Deterministic mock adapters keyed by source, used to keep the assessment composable when a
    live layer fails (the failed layer is still surfaced as `failed` in the source map)."""
    return {
        Source.FOUNDRY: MockFoundryIQ(settings),
        Source.FABRIC: MockFabricIQ(settings),
        Source.WORK: MockWorkIQ(settings),
        Source.WEB: MockWebIQ(settings),
    }
