from __future__ import annotations

import asyncio
import csv
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.config import Settings
from app.schemas import Evidence, Source
from app.sources.base import QueryContext, SourceResult


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class MockFabricIQ:
    name = Source.FABRIC
    label = "Fabric IQ"

    def __init__(self, settings: Settings) -> None:
        self.fabric_dir = settings.DATA_DIR / "fabric"

    def query(self, context: QueryContext) -> SourceResult:
        patients = _read_csv(self.fabric_dir / "patient_registry.csv")
        labs = _read_csv(self.fabric_dir / "labs.csv")
        treatments = _read_csv(self.fabric_dir / "treatment_history.csv")
        trials = _read_csv(self.fabric_dir / "trials.csv")
        enrollment = _read_csv(self.fabric_dir / "trial_enrollment.csv")
        slots = _read_csv(self.fabric_dir / "scheduling_slots.csv")

        patient = next((row for row in patients if row["patient_id"] == context.patient_id), None)
        trial = next((row for row in trials if row["trial_id"] == context.trial_id), None)
        crcl_labs = sorted(
            (row for row in labs if row["patient_id"] == context.patient_id and row["lab_type"] == "CrCl_CKD-EPI"),
            key=lambda row: row["lab_date"],
        )
        latest_crcl = crcl_labs[-1] if crcl_labs else None
        prior_crcl = crcl_labs[-2] if len(crcl_labs) > 1 else None
        patient_treatments = [row for row in treatments if row["patient_id"] == context.patient_id]
        trial_enrollment = [row for row in enrollment if row["patient_id"] == context.patient_id and row["trial_id"] == context.trial_id]
        trial_site = trial.get("site_id") if trial else None
        open_slots = [
            row for row in slots
            if trial_site and row["site_id"] == trial_site and row.get("available", "").lower() == "true"
        ]
        lab_slot = next((row for row in open_slots if "lab" in row["slot_type"].lower()), None)
        screening_slot = next((row for row in open_slots if "screening" in row["slot_type"].lower()), None)

        prior_part = f"; prior {prior_crcl['value']} on {prior_crcl['lab_date']}" if prior_crcl else ""
        treatment_part = "; ".join(f"{row['drug_name']} ({row['drug_class']}, line {row['line']})" for row in patient_treatments) or "none recorded"
        slot_part = f"; open lab slot {lab_slot['date']} {lab_slot['time']}" if lab_slot else ""
        patient_id = patient["patient_id"] if patient else context.patient_id
        ecog = patient["ecog_ps"] if patient else "unknown"
        latest_part = (
            f"latest CrCl {latest_crcl['value']} mL/min on {latest_crcl['lab_date']}"
            if latest_crcl else
            "latest CrCl unavailable"
        )
        trial_crcl_min = trial.get("crcl_min") if trial else None
        trial_part = (
            f"trial CrCl minimum {trial_crcl_min} mL/min"
            if trial_crcl_min not in (None, "")
            else "trial CrCl minimum not specified"
        )
        snippet = (
            f"{patient_id} ECOG {ecog}; {latest_part}{prior_part}; {trial_part}; "
            f"prior therapy {treatment_part}{slot_part}."
        )

        facts: dict[str, Any] = {
            "patient": patient,
            "latest_crcl": {"value": float(latest_crcl["value"]), "date": latest_crcl["lab_date"], "unit": latest_crcl["unit"]} if latest_crcl else None,
            "prior_crcl": {"value": float(prior_crcl["value"]), "date": prior_crcl["lab_date"], "unit": prior_crcl["unit"]} if prior_crcl else None,
            "trial": trial,
            "treatments": patient_treatments,
            "enrollment": trial_enrollment,
            "open_slots": open_slots,
            "lab_slot": lab_slot,
            "screening_slot": screening_slot,
        }
        return SourceResult(
            source=self.name,
            label=self.label,
            queries=[f"latest CrCl {context.patient_id}", f"{context.trial_id} structured criteria and slots"],
            summary=(
                f"CrCl {latest_crcl['value']} ({latest_crcl['lab_date']}); ECOG {ecog}; trial minimum {trial_crcl_min}."
                if latest_crcl and trial_crcl_min not in (None, "")
                else f"Structured data retrieved for {context.patient_id} and {context.trial_id}; CrCl or threshold missing."
            ),
            citations=[
                Evidence(
                    refId="r3",
                    source=Source.FABRIC,
                    title="Fabric tables, patient_registry, labs, treatments, trials, scheduling_slots",
                    snippet=snippet,
                    url=None,
                    sourceType="structured",
                )
            ],
            facts=facts,
            duration_ms=120,
        )


class LiveFabricIQ:
    """Live Fabric IQ adapter.

    Queries the published Fabric Data Agent (`amciq-fabric-eligibility-agent`) through its MCP
    runtime endpoint for the patient's authoritative renal-function facts (latest and prior CrCl),
    which drive the borderline eligibility decision. The remaining structured/dimensional rows
    (patient demographics, trial thresholds, prior therapies) are read from the same seed data that
    was loaded into the Lakehouse, giving the deterministic orchestrator a complete fact contract.

    Auth: DefaultAzureCredential -> token for https://api.fabric.microsoft.com. The running identity
    must be a member of the Fabric workspace and have access to the Data Agent. The Data Agent's
    backing Fabric capacity must be resumed (an inactive capacity returns `CapacityNotActive`).

    The MCP call runs in an isolated thread with its own event loop so `query()` is safe from both
    the sync `/api/ask` threadpool and the async SSE streaming generator.
    """

    name = Source.FABRIC
    label = "Fabric IQ"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._scaffold = MockFabricIQ(settings)

    def query(self, context: QueryContext) -> SourceResult:
        base = self._scaffold.query(context)
        facts: dict[str, Any] = dict(base.facts)

        try:
            answer = self._ask_data_agent(context)
        except Exception as exc:  # keep the proof-of-concept resilient; fall back to seed values
            facts["fabric_live"] = False
            facts["fabric_live_error"] = str(exc)
            return SourceResult(
                source=self.name,
                label=self.label,
                queries=[f"latest CrCl for {context.patient_id} (Fabric Data Agent, unavailable)"],
                summary=f"Fabric IQ Data Agent unavailable; using seeded structured values. ({exc})",
                citations=base.citations,
                facts=facts,
                duration_ms=0,
            )

        parsed = _parse_crcl_readings(answer)
        base_latest = base.facts.get("latest_crcl") or {}
        base_prior = base.facts.get("prior_crcl") or {}
        unit = base_latest.get("unit", "mL/min")

        if parsed:
            latest_value, latest_date = parsed[0]
            facts["latest_crcl"] = {
                "value": latest_value,
                "date": latest_date or base_latest.get("date", ""),
                "unit": unit,
            }
            if len(parsed) > 1:
                prior_value, prior_date = parsed[1]
                facts["prior_crcl"] = {
                    "value": prior_value,
                    "date": prior_date or base_prior.get("date", ""),
                    "unit": unit,
                }

        facts["fabric_live"] = True
        facts["fabric_answer"] = answer

        latest = facts.get("latest_crcl") or base_latest
        trial = base.facts.get("trial") or {}
        summary = (
            f"Fabric IQ Data Agent (live): latest CrCl {latest.get('value')} {unit} on "
            f"{latest.get('date')}; trial minimum {trial.get('crcl_min')} {unit}."
        )
        citation = Evidence(
            refId="r3",
            source=Source.FABRIC,
            title="Fabric Data Agent (live)",
            snippet=answer.strip()[:320],
            url=self.settings.fabric_portal_url,
            sourceType="fabric_data_agent",
        )
        return SourceResult(
            source=self.name,
            label=self.label,
            queries=[f"latest and prior CrCl for {context.patient_id} (Fabric Data Agent, NL2SQL)"],
            summary=summary,
            citations=[citation],
            facts=facts,
            duration_ms=0,
        )

    def _ask_data_agent(self, context: QueryContext) -> str:
        question = (
            "Use only the Lakehouse datasource, not any GraphModel. "
            f"For patient {context.patient_id}, report the latest CrCl (lab_type CrCl_CKD-EPI) with "
            f"its lab_date, and the immediately prior CrCl value with its lab_date. "
            f"State each as a number in mL/min with the date."
        )
        return _run_coro_blocking(
            lambda: _call_fabric_mcp(self.settings.fabric_mcp_url, self.settings.FABRIC_API_SCOPE, question)
        )


_DATE_PATTERNS = re.compile(
    r"(\d{4}-\d{1,2}-\d{1,2}"                 # ISO 2026-06-18
    r"|\d{1,2}/\d{1,2}/\d{2,4}"               # US 6/18/2026
    r"|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}"     # June 18, 2026
    r"|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})"      # 18 June 2026
)


def _parse_crcl_readings(text: str) -> list[tuple[float, str | None]]:
    """Extract (value, iso_date | None) CrCl readings from the Data Agent's natural-language answer,
    in the order they appear (latest first, then prior)."""
    readings: list[tuple[float, str | None]] = []
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*mL\s*/?\s*min", text, re.IGNORECASE):
        value = float(match.group(1))
        tail = text[match.end() : match.end() + 40]
        # Bound the tail before the next reading so we do not steal its date.
        next_reading = re.search(r"\d+(?:\.\d+)?\s*mL", tail)
        if next_reading:
            tail = tail[: next_reading.start()]
        date_match = _DATE_PATTERNS.search(tail)
        iso = _normalize_date(date_match.group(1)) if date_match else None
        readings.append((value, iso))
    return readings


def _normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    candidate = raw.strip().rstrip(".").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%d %B %Y"):
        try:
            return datetime.strptime(candidate, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _run_coro_blocking(make_coro: Callable[[], Any]) -> Any:
    """Run an async coroutine to completion from any context (sync threadpool or running event loop)
    by executing it on a dedicated thread with a fresh event loop."""
    box: dict[str, Any] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            box["value"] = loop.run_until_complete(make_coro())
        except BaseException as exc:  # noqa: BLE001 - re-raised on the calling thread
            box["error"] = exc
        finally:
            loop.close()

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


async def _call_fabric_mcp(mcp_url: str, scope: str, question: str) -> str:
    from azure.identity import DefaultAzureCredential
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    token = DefaultAzureCredential().get_token(scope).token
    headers = {"Authorization": f"Bearer {token}"}

    async with streamablehttp_client(mcp_url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            if not tools.tools:
                raise RuntimeError("Fabric Data Agent MCP endpoint exposed no tools.")
            tool = tools.tools[0]
            properties = (tool.inputSchema or {}).get("properties", {})
            arg_name = next(iter(properties), "userQuestion")
            result = await session.call_tool(tool.name, {arg_name: question})
            parts = [getattr(block, "text", "") for block in result.content]
            return "".join(part for part in parts if part).strip()
