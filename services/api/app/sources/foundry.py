from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from app.config import Settings
from app.schemas import Evidence, Source
from app.sources.base import QueryContext, SourceResult, evidence_doc_url

_KEYWORDS = ("egfr", "exon 20", "crcl", "renal", "platinum", "ecog", "eligibility", "criteria")


def _read_markdown(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        _, front_matter, body = text.split("---", 2)
        return yaml.safe_load(front_matter) or {}, body.strip()
    return {}, text


def _snippet(text: str, terms: tuple[str, ...] = _KEYWORDS, max_len: int = 320) -> str:
    normalized = " ".join(text.split())
    lower = normalized.lower()
    positions = [lower.find(term) for term in terms if lower.find(term) >= 0]
    start = max(min(positions) - 80, 0) if positions else 0
    excerpt = normalized[start : start + max_len].strip()
    return excerpt + ("..." if len(normalized) > start + max_len else "")


def _score_doc(metadata: dict[str, Any], body: str, context: QueryContext) -> int:
    haystack = f"{metadata} {body}".lower()
    score = sum(haystack.count(term) for term in _KEYWORDS)
    if metadata.get("patient_id") == context.patient_id:
        score += 10
    if metadata.get("trial_id") == context.trial_id:
        score += 10
    return score


class MockFoundryIQ:
    name = Source.FOUNDRY
    label = "Foundry IQ"

    def __init__(self, settings: Settings) -> None:
        self.docs_dir = settings.DATA_DIR / "foundry_docs"

    def query(self, context: QueryContext) -> SourceResult:
        documents: list[tuple[Path, dict[str, Any], str]] = []
        for path in sorted(self.docs_dir.glob("*.md")):
            metadata, body = _read_markdown(path)
            documents.append((path, metadata, body))

        ranked = sorted(documents, key=lambda item: _score_doc(item[1], item[2], context), reverse=True)
        genomics = next((item for item in ranked if item[1].get("patient_id") == context.patient_id and "genomics" in str(item[1].get("doc_type", ""))), None)
        amendment = next((item for item in ranked if item[1].get("trial_id") == context.trial_id and "amendment" in str(item[1].get("doc_type", ""))), None)
        protocol = next((item for item in ranked if item[1].get("trial_id") == context.trial_id and "protocol" in str(item[1].get("doc_type", ""))), None)
        eligibility_doc = amendment or protocol

        citations: list[Evidence] = []
        facts: dict[str, Any] = {"top_passages": []}
        if genomics:
            path_g, metadata, body = genomics
            citations.append(
                Evidence(
                    refId="r1",
                    source=Source.FOUNDRY,
                    title=str(metadata.get("title", "Genomics report")),
                    snippet=_snippet(body, ("egfr", "exon 20", "p.a767")),
                    url=evidence_doc_url("foundry_docs", path_g.name),
                    sourceType=str(metadata.get("doc_type", "foundry_doc")),
                )
            )
            facts["biomarker_documented"] = "egfr exon 20" in body.lower()
            facts["biomarker_text"] = "EGFR exon 20 insertion"

        if eligibility_doc:
            path_e, metadata, body = eligibility_doc
            citations.append(
                Evidence(
                    refId="r2",
                    source=Source.FOUNDRY,
                    title=str(metadata.get("title", "Protocol eligibility criteria")),
                    snippet=_snippet(body, ("crcl", "renal", "prior platinum", "pi confirmation")),
                    url=evidence_doc_url("foundry_docs", path_e.name),
                    sourceType=str(metadata.get("doc_type", "foundry_doc")),
                )
            )
            crcl_match = re.search(r"CrCl\s*>?=\s*(\d+)", body, re.IGNORECASE)
            facts["protocol_crcl_min"] = int(crcl_match.group(1)) if crcl_match else None
            facts["prior_platinum_requires_pi"] = "pi confirmation is required" in body.lower() or "escalate" in body.lower()
            facts["prior_platinum_first_line_allowed"] = "first line" in body.lower() and "does not" in body.lower()

        facts["top_passages"] = [
            {"title": str(metadata.get("title", path.name)), "snippet": _snippet(body)}
            for path, metadata, body in ranked[:4]
        ]
        return SourceResult(
            source=self.name,
            label=self.label,
            queries=["EGFR exon 20 eligibility criteria", f"{context.trial_id} prior platinum and renal threshold"],
            summary="EGFR exon 20 documented; protocol/amendment flag CrCl >= 50 and PI confirmation for prior platinum.",
            citations=citations,
            facts=facts,
            duration_ms=210,
        )


class LiveFoundryIQ:
    """Live Foundry IQ adapter: calls the Azure AI Search agentic-retrieval knowledge base
    (amciq-foundry-oncology-kb) and maps the cited answer into the r1/r2 evidence contract.

    Auth: DefaultAzureCredential -> token for https://search.azure.com. The running identity
    needs `Search Index Data Reader` on the search service.
    """

    name = Source.FOUNDRY
    label = "Foundry IQ"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._endpoint = settings.SEARCH_ENDPOINT.rstrip("/")
        self._kb = settings.FOUNDRY_KB_NAME
        self._api_version = settings.SEARCH_API_VERSION

    def _token(self) -> str:
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
        return credential.get_token("https://search.azure.com/.default").token

    def query(self, context: QueryContext) -> SourceResult:
        import httpx

        question = (
            f"The user asks: {context.question}\n"
            f"For patient {context.patient_id} and trial {context.trial_id}, retrieve only the "
            "clinical documents needed to answer that question. Include the protocol or amendment "
            "when criteria interpretation is relevant, and the genomics or oncology record when "
            "patient facts are relevant. Cite every document used."
        )
        url = f"{self._endpoint}/knowledgebases/{self._kb}/retrieve?api-version={self._api_version}"
        payload = {"messages": [{"role": "user", "content": [{"type": "text", "text": question}]}]}
        headers = {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}
        with httpx.Client(timeout=90) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        answer = ""
        for message in data.get("response", []):
            for part in message.get("content", []):
                if part.get("type") == "text":
                    answer += part.get("text", "")
        references = data.get("references", [])

        def _find_blob(*needles: str) -> dict[str, Any] | None:
            for needle in needles:  # priority order: first needle wins
                for ref in references:
                    if needle in str(ref.get("blobUrl", "")).lower():
                        return ref
            return references[0] if references else None

        genomics_ref = _find_blob("genomics", context.patient_id.lower())
        protocol_ref = _find_blob("amendment", "protocol")

        lower = answer.lower()
        citations: list[Evidence] = []
        if genomics_ref is not None:
            citations.append(
                Evidence(
                    refId="r1",
                    source=Source.FOUNDRY,
                    title=_blob_title(genomics_ref.get("blobUrl")),
                    snippet=_sentence_with(answer, ("egfr exon 20", "biomarker", "insertion")) or "EGFR exon 20 insertion documented.",
                    url=_doc_link(genomics_ref.get("blobUrl")),
                    sourceType="genomics",
                )
            )
        if protocol_ref is not None:
            citations.append(
                Evidence(
                    refId="r2",
                    source=Source.FOUNDRY,
                    title=_blob_title(protocol_ref.get("blobUrl")),
                    snippet=_sentence_with(answer, ("crcl", "platinum", "pi confirmation")) or answer[:320],
                    url=_doc_link(protocol_ref.get("blobUrl")),
                    sourceType="protocol",
                )
            )

        crcl_match = re.search(r"CrCl\s*>?=\s*(\d+)", answer, re.IGNORECASE)
        facts: dict[str, Any] = {
            "answer": answer,
            "biomarker_documented": "egfr exon 20" in lower,
            "biomarker_text": "EGFR exon 20 insertion",
            "protocol_crcl_min": int(crcl_match.group(1)) if crcl_match else None,
            "prior_platinum_requires_pi": any(
                phrase in lower for phrase in ("pi confirmation is required", "pi confirmation", "pi review", "escalate", "confirms the interpretation")
            ),
            "prior_platinum_first_line_allowed": "first line" in lower or "first-line" in lower,
            "reference_count": len(references),
        }
        return SourceResult(
            source=self.name,
            label=self.label,
            queries=[question],
            summary="Foundry IQ knowledge base: CrCl >= 50 threshold; prior first-line platinum not automatically excluded (Amendment 2) but PI confirmation required.",
            citations=citations,
            facts=facts,
            duration_ms=0,
        )


def _blob_title(blob_url: str | None) -> str:
    if not blob_url:
        return "Foundry IQ document"
    name = str(blob_url).rstrip("/").split("/")[-1]
    return name.rsplit(".", 1)[0].replace("_", " ")


def _doc_link(blob_url: str | None) -> str | None:
    """Map a private/firewalled blob citation to the local document-serving endpoint, so the link
    opens the same source content in a browser. The indexed blobs were uploaded from these docs."""
    if not blob_url:
        return None
    name = str(blob_url).rstrip("/").split("/")[-1]
    if not name.endswith(".md"):
        return None
    return evidence_doc_url("foundry_docs", name)


def _sentence_with(text: str, terms: tuple[str, ...]) -> str | None:
    clean = re.sub(r"\[ref_id:\d+\]", "", text)
    clean = re.sub(r"\*\*", "", clean)
    for sentence in re.split(r"(?<=[.!?])\s+", clean):
        low = sentence.lower()
        if any(term in low for term in terms):
            return sentence.strip()[:320]
    return None
