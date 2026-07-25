from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from app.config import Settings
from app.schemas import Evidence, Source
from app.sources.base import QueryContext, SourceResult, evidence_doc_url
from app.workiq import WorkIQClient


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_markdown(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {}, ""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        _, front_matter, body = text.split("---", 2)
        return yaml.safe_load(front_matter) or {}, body.strip()
    return {}, text


def _snippet(text: str, max_len: int = 360) -> str:
    normalized = " ".join(text.split())
    start = normalized.lower().find("alex morgan")
    if start < 0:
        start = normalized.lower().find("assessment")
    start = max(start - 60, 0) if start >= 0 else 0
    excerpt = normalized[start : start + max_len].strip()
    return excerpt + ("..." if len(normalized) > start + max_len else "")


class MockWorkIQ:
    name = Source.WORK
    label = "Work IQ"

    def __init__(self, settings: Settings) -> None:
        self.work_dir = settings.DATA_DIR / "work"

    def query(self, context: QueryContext) -> SourceResult:
        tasks = _read_json(self.work_dir / "open_tasks.json")
        referral_queue = _read_json(self.work_dir / "referral_queue.json")
        messages = _read_json(self.work_dir / "teams_messages.json")
        pi_availability = _read_json(self.work_dir / "pi_availability.json")
        metadata, tumor_board = _read_markdown(self.work_dir / "tumor_board_summary_2026-06-30.md")
        handoff_metadata, handoff = _read_markdown(
            self.work_dir / f"coordinator_handoff_{context.patient_id}.md"
        )

        related_tasks = [task for task in tasks if task.get("related_patient_id") == context.patient_id and task.get("related_trial_id") == context.trial_id]
        crcl_task = next((task for task in related_tasks if "CRCL" in task.get("task_id", "")), related_tasks[0] if related_tasks else None)
        referral_entry = next((entry for entry in referral_queue.get("entries", []) if entry.get("patient_id") == context.patient_id), None)
        owner_id = crcl_task.get("owner_id") if crcl_task else referral_queue.get("owner")
        owner_name = crcl_task.get("owner_name") if crcl_task else None
        tumor_board_relevant = (
            context.patient_id.lower() in tumor_board.lower()
            and context.trial_id.lower() in tumor_board.lower()
        )
        workplace_text = handoff or (tumor_board if tumor_board_relevant else "")
        snippet = _snippet(workplace_text)
        if crcl_task:
            snippet = (
                f"{snippet} Open task: {crcl_task['task_id']} owned by {owner_name}."
            ).strip()

        facts: dict[str, Any] = {
            "related_tasks": related_tasks,
            "crcl_task": crcl_task,
            "next_action_owner_id": owner_id,
            "referral_entry": referral_entry,
            "tumor_board_recommendation": (
                "likely eligible pending repeat CrCl and PI confirmation"
                if tumor_board_relevant and "likely eligible pending" in tumor_board.lower()
                else "review required"
            ),
            "messages": [message for message in messages if context.patient_id in message.get("text", "")],
            "pi_availability": pi_availability,
            "handoff": handoff,
        }
        summary = f"Open task {crcl_task['task_id']} owned by {owner_name}; tumor board recommends repeat CrCl and PI confirmation." if crcl_task else "Tumor board workflow review required."
        citations: list[Evidence] = []
        if tumor_board_relevant:
            citations.append(
                Evidence(
                    refId="r4",
                    source=Source.WORK,
                    title=str(metadata.get("title", "Tumor board and open tasks")),
                    snippet=snippet,
                    url=evidence_doc_url("work", "tumor_board_summary_2026-06-30.md"),
                    sourceType="workplace",
                )
            )
        elif handoff:
            citations.append(
                Evidence(
                    refId="r4",
                    source=Source.WORK,
                    title=str(handoff_metadata.get("title", "Coordinator handoff")),
                    snippet=snippet,
                    url=evidence_doc_url(
                        "work", f"coordinator_handoff_{context.patient_id}.md"
                    ),
                    sourceType="workplace",
                )
            )
        return SourceResult(
            source=self.name,
            label=self.label,
            queries=[f"tumor board {context.patient_id} owner", f"open tasks {context.patient_id} {context.trial_id}"],
            summary=summary,
            citations=citations,
            facts=facts,
            duration_ms=90,
        )


class LiveWorkIQ:
    name = Source.WORK
    label = "Work IQ"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = WorkIQClient(settings)

    def query(self, context: QueryContext) -> SourceResult:
        focus = _work_iq_focus(context.question)
        prompt = (
            f"For the synthetic AMC IQ case of patient {context.patient_id} and trial "
            f"{context.trial_id}, answer this user question: {context.question}\n"
            f"Retrieval focus: {focus} "
            "Use only relevant Microsoft 365 mail, calendar, To Do, Teams, and SharePoint evidence. "
            "Prefer the smallest set of sources that directly supports the question. "
            "Do not infer clinical eligibility or invent missing facts."
        )
        answer = self.client.ask(
            user_assertion=context.user_access_token or "",
            question=prompt,
        )
        snippet = _snippet(answer.text)
        citations = [
            Evidence(
                refId="r4" if index == 0 else f"r4-{index + 1}",
                source=Source.WORK,
                title=attribution.title,
                snippet=snippet,
                url=attribution.url,
                sourceType="work_iq_citation",
            )
            for index, attribution in enumerate(answer.attributions)
        ]
        if not citations:
            citations = [
                Evidence(
                    refId="r4",
                    source=Source.WORK,
                    title="Work IQ workplace context",
                    snippet=snippet,
                    url=None,
                    sourceType="workplace",
                )
            ]
        return SourceResult(
            source=self.name,
            label=self.label,
            queries=[prompt],
            summary=answer.text,
            citations=citations,
            facts={
                "work_iq_task_id": answer.task_id,
                "work_iq_context_id": answer.context_id,
                "work_iq_attribution_count": len(answer.attributions),
            },
            duration_ms=answer.duration_ms,
        )


def _work_iq_focus(question: str) -> str:
    lower = question.lower()
    if any(term in lower for term in ("owner", "task", "next step", "workflow")):
        return "Identify the discrete open tasks, named owners, due dates, and scheduling constraints."
    if any(term in lower for term in ("prior platinum", "prior therapy", "exclusion", "protocol")):
        return (
            "Find the prior-treatment line and response, Amendment 2/AMD-2 clarification, "
            "and the PI-review task."
        )
    if any(term in lower for term in ("missing", "stale", "data gap")):
        return (
            "Find ECOG status and the CrCl CKD-EPI value, measurement date, threshold, "
            "staleness statement, and repeat-lab task."
        )
    if any(term in lower for term in ("evidence packet", "pi review")):
        return (
            "Find the evidence memo or deck containing all protocol criteria, patient facts, "
            "and unresolved items."
        )
    if any(term in lower for term in ("prevent", "block", "screening")):
        return "Find the documented screening blockers and the work items required to clear them."
    return "Find the tumor-board decision, current blockers, open tasks, and named owners."
