from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.schemas import Evidence, Source, SourceMapItem


def evidence_doc_url(subdir: str, filename: str) -> str:
    """Relative URL to the backend document-serving endpoint, so citation links open the real
    synthetic source content (served from data/<subdir>/<filename>)."""
    return f"/api/evidence/doc?path={subdir}/{filename}"


@dataclass(frozen=True)
class QueryContext:
    question: str
    patient_id: str
    trial_id: str
    registry: dict[str, Any]
    user_access_token: str | None = None


@dataclass
class SourceResult:
    source: Source
    label: str
    queries: list[str]
    summary: str
    citations: list[Evidence]
    facts: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    status: str = "complete"
    retrieving: str = ""
    evidence_count: int | None = None
    evidence_noun: str = "sources"

    def to_source_map_item(self) -> SourceMapItem:
        return SourceMapItem(
            source=self.source,
            label=self.label,
            status=self.status,  # type: ignore[arg-type]
            queries=self.queries,
            citations=[citation.refId for citation in self.citations],
            durationMs=self.duration_ms,
            retrieving=self.retrieving,
            evidenceCount=self.evidence_count if self.evidence_count is not None else len(self.citations),
            evidenceNoun=self.evidence_noun,
        )


class IQSource(Protocol):
    name: Source
    label: str

    def query(self, context: QueryContext) -> SourceResult:
        ...
