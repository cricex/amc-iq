from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import Settings
from app.schemas import Evidence, Source
from app.sources.base import QueryContext, SourceResult


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class MockWebIQ:
    name = Source.WEB
    label = "Web IQ"

    def __init__(self, settings: Settings) -> None:
        self.web_dir = settings.DATA_DIR / "web"

    def query(self, context: QueryContext) -> SourceResult:
        manifest = _read_json(self.web_dir / "manifest.json")
        record_entry = next(
            (
                entry
                for entry in manifest
                if entry.get("nct_id") == context.trial_id
                and entry.get("file", "").endswith(".json")
            ),
            None,
        )
        if record_entry is None:
            return SourceResult(
                source=self.name,
                label=self.label,
                queries=[f"{context.trial_id} registry"],
                summary=f"No bundled synthetic registry fixture is available for {context.trial_id}.",
                citations=[],
                facts={
                    "trial_record": None,
                    "registry_status": None,
                    "registry_criteria": None,
                    "registry_synthetic": None,
                },
                duration_ms=20,
            )
        record = _read_json(self.web_dir / record_entry["file"])
        synthetic = record_entry.get("synthetic") is True and record.get("synthetic") is True
        if not synthetic:
            raise ValueError("Bundled Web IQ fixtures must be explicitly marked synthetic")
        inclusion = record["eligibility"]["criteria"]["inclusion"]
        exclusion = record["eligibility"]["criteria"]["exclusion"]
        snippet = (
            f"Synthetic fixture for {record['nct_id']}: status {record['overall_status']}; "
            f"inclusion includes {inclusion[1]} "
            f"and {inclusion[4]} Exclusion notes: {exclusion[-1]}"
        )
        return SourceResult(
            source=self.name,
            label=self.label,
            queries=[f"{context.trial_id} registry"],
            summary=(
                "Synthetic ClinicalTrials.gov-style fixture shows "
                f"{record['overall_status']} and a CrCl >= 50 criterion."
            ),
            citations=[
                Evidence(
                    refId="r7",
                    source=Source.WEB,
                    title=str(record_entry["title"]),
                    snippet=snippet,
                    url=str(record_entry["source_url"]),
                    sourceType=str(record_entry["source_type"]),
                )
            ],
            facts={
                "trial_record": record,
                "registry_status": record["overall_status"],
                "registry_criteria": record["eligibility"]["criteria"],
                "registry_synthetic": synthetic,
            },
            duration_ms=160,
        )


class LiveWebIQ:
    """Live Web IQ adapter: queries the Bing-backed `web` knowledge base (amciq-web-kb) for
    fresh external oncology context. Auth via DefaultAzureCredential (Search Index Data Reader)."""

    name = Source.WEB
    label = "Web IQ"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._endpoint = settings.SEARCH_ENDPOINT.rstrip("/")
        self._kb = settings.WEB_KB_NAME
        self._api_version = settings.SEARCH_API_VERSION

    def _token(self) -> str:
        from azure.identity import DefaultAzureCredential

        return DefaultAzureCredential().get_token("https://search.azure.com/.default").token

    def query(self, context: QueryContext) -> SourceResult:
        import httpx

        patient = next((p for p in context.registry["patients"] if p["id"] == context.patient_id), {})
        biomarkers = ", ".join(str(b) for b in patient.get("biomarkers", [])) or "EGFR exon 20 insertion"
        diagnosis = str(patient.get("diagnosis", "metastatic NSCLC"))
        question = (
            f"The user asks: {context.question}\n"
            f"Provide only relevant external trial-registry or treatment-landscape context for "
            f"{biomarkers} {diagnosis} and trial {context.trial_id}."
        )
        url = f"{self._endpoint}/knowledgebases/{self._kb}/retrieve?api-version={self._api_version}"
        payload = {"messages": [{"role": "user", "content": [{"type": "text", "text": question}]}]}
        headers = {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}
        with httpx.Client(timeout=90) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        answer = ""
        for message in data.get("response", []):
            for part in message.get("content", []):
                if part.get("type") == "text":
                    answer += part.get("text", "")
        references = data.get("references", [])
        first_url = next((r.get("url") or r.get("blobUrl") for r in references if r.get("url") or r.get("blobUrl")), None)
        clean = re.sub(r"\[ref_id:\d+\]", "", answer).strip()

        return SourceResult(
            source=self.name,
            label=self.label,
            queries=[question],
            summary="Web IQ (live Bing grounding): external treatment-landscape context for the patient's biomarker.",
            citations=[
                Evidence(
                    refId="r7",
                    source=Source.WEB,
                    title="External web: EGFR exon 20 NSCLC treatment landscape",
                    snippet=clean[:320],
                    url=first_url,
                    sourceType="web",
                )
            ],
            facts={"external_context": clean[:1200], "registry_status": None, "reference_count": len(references)},
            duration_ms=0,
        )
