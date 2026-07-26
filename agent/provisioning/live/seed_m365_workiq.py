"""Seed synthetic Microsoft 365 Work IQ proof-of-concept content for the AMC IQ care-team thread.

This seeder signs in with the MSAL device-code flow as a real user (a dedicated public-client
app registration), then writes searchable, synthetic workplace evidence into the signed-in
user's own Microsoft 365 workloads over Microsoft Graph v1.0:

  * one self-addressed mail (Mail.Send), guarded by a subject probe (Mail.ReadBasic),
  * two calendar events for the repeat CrCl and the PI review (Calendars.ReadWrite),
  * a To Do list "AMC IQ Demo" with discrete TASK-1042-CRCL and TASK-1042-PI-REVIEW tasks
    (Tasks.ReadWrite),
  * two Word meeting transcripts in SharePoint (Files.ReadWrite.All),
  * a Word operations memo, Excel tracker, and PowerPoint status deck in a separate SharePoint
    productivity folder (Files.ReadWrite.All),
  * synthetic notes in the signed-in user's Teams self-chat (Chat.Read, ChatMessage.Send),
  * a strict Work IQ A2A verification over the seeded facts (WorkIQAgent.Ask),
  * optional Microsoft 365 Copilot Retrieval API probes over the SharePoint transcripts and
    productivity files (Files.Read.All, Sites.Read.All).

Design rules honored here:
  * Delegated permissions only; no application permissions, credentials, or broad directory access.
  * Access tokens live only in memory; refresh tokens use a Windows DPAPI-encrypted MSAL cache.
    Nothing here ever prints or returns an access token.
  * Idempotent: every write probes for an existing item first, then upserts (create or patch).
  * Bounded retry with backoff for HTTP 429 and 5xx; explicit failure on permission/workload errors.
  * No OneDrive dependency (that tenant's /me/drive is itemNotFound).
  * Synthetic content only. No PHI. Labeled as not clinical decision support.

The module is import-safe: payload builders and selection helpers are pure functions with no
network, so they can be unit tested directly. Only ``main`` performs sign-in and Graph calls.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import quote, unquote
from xml.sax.saxutils import escape

import httpx

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
WORK_IQ_ENDPOINT = "https://workiq.svc.cloud.microsoft/a2a/"
WORK_IQ_SCOPE = "api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask"
WORK_IQ_PROMPT = (
    "Using only my Microsoft 365 work data, summarize the synthetic AMC IQ case for patient "
    "PT-1042 and trial NCT99004324. Quote the matching IDs, names, and dates. Include: "
    "(1) TASK-1042-CRCL owner, due date, last CrCl value, threshold, and method; "
    "(2) TB-2026-06-30 recommendation; "
    "(3) TASK-1042-PI-REVIEW owner, due date, and prior-platinum clarification; and "
    "(4) RQ-THORACIC status plus the Patient Reviews scheduling slot. "
    "Do not infer clinical eligibility beyond the synthetic records."
)

# Delegated Microsoft Graph scopes requested at sign-in. The read-all file/site scopes are required
# by the Copilot Retrieval API even though writes remain limited to the signed-in user's access.
# Chat.Read is required only to make Teams self-chat writes idempotent. WorkIQAgent.Ask is acquired
# separately for the A2A verification because it belongs to the Work IQ resource, not Graph.
DELEGATED_SCOPES: tuple[str, ...] = (
    "User.Read",
    "Mail.ReadBasic",
    "Mail.Send",
    "Calendars.ReadWrite",
    "Tasks.ReadWrite",
    "Files.ReadWrite.All",
    "Files.Read.All",
    "Sites.Read.All",
    "Chat.Read",
    "ChatMessage.Send",
)

# Deterministic, versioned subject stems. Bump the version to seed a fresh generation without
# colliding with earlier seeded content. Every write keys idempotency off these exact strings.
SUBJECT_VERSION = "v2"
EMAIL_SUBJECT = (
    f"[AMC IQ Demo {SUBJECT_VERSION}] PT-1042 / NCT99004324 coordination: "
    "TASK-1042-CRCL repeat CrCl and PI confirmation"
)
CRCL_EVENT_SUBJECT = f"[AMC IQ Demo {SUBJECT_VERSION}] Repeat CrCl for PT-1042 (TASK-1042-CRCL)"
PI_EVENT_SUBJECT = (
    f"[AMC IQ Demo {SUBJECT_VERSION}] PI review: prior-platinum exclusion for PT-1042 (NCT99004324)"
)
TODO_LIST_NAME = "AMC IQ Demo"
TODO_TASK_TITLE = f"TASK-1042-CRCL: Repeat CrCl for Alex Morgan (PT-1042) [AMC IQ Demo {SUBJECT_VERSION}]"
PI_TODO_TASK_TITLE = (
    f"TASK-1042-PI-REVIEW: Confirm prior-platinum interpretation for Alex Morgan (PT-1042) "
    f"[AMC IQ Demo {SUBJECT_VERSION}]"
)
SHAREPOINT_FOLDER_PATH = "AMC IQ Demo/Teams Transcripts"
PRODUCTIVITY_FOLDER_PATH = "AMC IQ Demo/Productivity"
TUMOR_BOARD_TRANSCRIPT_FILENAME = (
    f"AMC-IQ-TB-2026-06-30-Teams-Transcript-{SUBJECT_VERSION}.docx"
)
SCREENING_HUDDLE_TRANSCRIPT_FILENAME = (
    f"AMC-IQ-PT-1042-Screening-Huddle-2026-07-01-{SUBJECT_VERSION}.docx"
)
PRODUCTIVITY_MEMO_FILENAME = (
    f"AMC-IQ-PT-1042-Screening-Operations-Memo-{SUBJECT_VERSION}.docx"
)
PRODUCTIVITY_TRACKER_FILENAME = f"AMC-IQ-Trial-Screening-Tracker-{SUBJECT_VERSION}.xlsx"
PRODUCTIVITY_DECK_FILENAME = (
    f"AMC-IQ-Thoracic-Trials-Weekly-Operations-{SUBJECT_VERSION}.pptx"
)
TEAMS_SELF_CHAT_ID = "48:notes"
SELF_CHAT_MARKERS: tuple[str, ...] = (
    f"[AMC IQ Demo {SUBJECT_VERSION}][SELF-CHAT-RENAL]",
    f"[AMC IQ Demo {SUBJECT_VERSION}][SELF-CHAT-PI]",
    f"[AMC IQ Demo {SUBJECT_VERSION}][SELF-CHAT-SCHEDULING]",
)

# Windows time zone name accepted by Microsoft Graph for dateTimeTimeZone values. The synthetic
# source timestamps in data/work use US Eastern (-04:00).
EVENT_TIME_ZONE = "Eastern Standard Time"

SYNTHETIC_NOTICE = (
    "Synthetic AMC IQ Work IQ proof-of-concept content. No PHI. Not clinical decision support; "
    "for scenario walkthrough and human review only."
)

# Stable synthetic clinical facts sourced from data/work (tumor board summary and coordinator
# handoff). Kept as constants because they are fixed scenario values, not live data.
TUMOR_BOARD_ID = "TB-2026-06-30"
CRCL_LAST_VALUE = 48
CRCL_LAST_DATE = "2026-06-18"
CRCL_THRESHOLD = 50
DEFAULT_PROTOCOL_CRITERIA: tuple[tuple[str, str], ...] = (
    (
        "NCT99004324-DX",
        "Histologically confirmed Metastatic NSCLC with EGFR exon 20 insertion.",
    ),
    ("NCT99004324-BIO", "Documented EGFR exon 20 insertion."),
    ("NCT99004324-PS", "ECOG performance status 0 to 1."),
    ("NCT99004324-REN", "Creatinine clearance >= 50 mL/min (CKD-EPI)."),
    (
        "NCT99004324-RX",
        "Prior platinum-based chemotherapy for advanced or metastatic disease.",
    ),
)


class SeedError(Exception):
    """Base error for the seeder."""


class GraphError(SeedError):
    """A Microsoft Graph request failed."""


class GraphPermissionError(GraphError):
    """A Graph request failed on authorization or an unavailable workload (401/403).

    Raised without retry because backoff will not resolve a missing scope or a disabled workload.
    """


class GraphNotFoundError(GraphError):
    """A Graph resource was not found and can be created by an idempotent upsert."""


class GraphPreconditionFailedError(GraphError):
    """A conditional Graph write failed because the target state changed."""


class GraphThrottledError(GraphError):
    """A non-retried Graph write was rejected with HTTP 429."""

    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class DeviceCodeAuthError(SeedError):
    """The MSAL device-code sign-in did not yield an access token."""


class WorkIQVerificationError(SeedError):
    """Work IQ did not return the required seeded evidence."""


@dataclass(frozen=True)
class SeedContext:
    """The synthetic facts a single seed run writes into Microsoft 365.

    Every field is non-secret. ``user_address`` is the signed-in user's own mailbox address, used
    only as the self-mail recipient and never logged as a credential.
    """

    user_address: str
    patient_id: str = "PT-1042"
    patient_name: str = "Alex Morgan"
    trial_id: str = "NCT99004324"
    task_id: str = "TASK-1042-CRCL"
    pi_task_id: str = "TASK-1042-PI-REVIEW"
    owner_id: str = "COORD-01"
    owner_name: str = "Dana Whitfield"
    pi_owner_id: str = "PI-01"
    pi_name: str = "Dr. Priya Anand"
    tumor_board_id: str = TUMOR_BOARD_ID
    crcl_last_value: int = CRCL_LAST_VALUE
    crcl_last_date: str = CRCL_LAST_DATE
    crcl_threshold: int = CRCL_THRESHOLD
    crcl_due_date: str = "2026-07-08"
    pi_review_due_date: str = "2026-07-09"
    pi_slot_date: str = "2026-07-07"
    pi_slot_start: str = "09:00"
    pi_slot_end: str = "10:00"
    referral_queue_id: str = "RQ-THORACIC"
    referral_status: str = "Awaiting screening"
    ecog_status: int = 1
    crcl_method: str = "CKD-EPI"
    crcl_staleness: str = "several days old; repeat required before screening can advance"
    amendment_id: str = "AMD-2"
    amendment_label: str = "Amendment 2"
    amendment_summary: str = (
        "Revised the prior-platinum exclusion to a line-of-therapy interpretation; "
        "prior first-line platinum may be permitted with PI confirmation."
    )
    prior_therapy_name: str = "Carboplatin + Pemetrexed"
    prior_therapy_line: str = "1"
    prior_therapy_response: str = "Partial response"
    protocol_criteria: tuple[tuple[str, str], ...] = DEFAULT_PROTOCOL_CRITERIA
    time_zone: str = EVENT_TIME_ZONE


@dataclass(frozen=True)
class TranscriptDocument:
    """A deterministic synthetic transcript prepared for SharePoint upload."""

    filename: str
    title: str
    paragraphs: tuple[str, ...]


@dataclass(frozen=True)
class ProductivityFile:
    """A generated Office productivity file prepared for SharePoint upload."""

    filename: str
    content_type: str
    content: bytes
    retrieval_mode: str


# --------------------------------------------------------------------------------------------------
# Evidence loading (reads data/work so the seeded content tracks the synthetic source artifacts)
# --------------------------------------------------------------------------------------------------
def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _load_csv(path: Path, required_columns: Iterable[str]) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = set(reader.fieldnames or ())
            missing = set(required_columns) - fieldnames
            if missing:
                raise SeedError(
                    f"{path} is missing required columns: {', '.join(sorted(missing))}."
                )
            return list(reader)
    except OSError as exc:
        raise SeedError(f"Could not read required seed source {path}: {exc}") from exc
    except UnicodeError as exc:
        raise SeedError(f"Could not decode required seed source {path}: {exc}") from exc
    except csv.Error as exc:
        raise SeedError(f"Could not parse required seed source {path}: {exc}") from exc


def _shift_stale_action_dates(
    action_dates: Mapping[str, str], reference_date: date
) -> dict[str, str]:
    """Shift the immutable source schedule as one block so seeded actions remain future.

    A later seed run intentionally recalculates from the source dates and patches existing Graph
    items forward again; idempotency here means stable identities/no duplicates, not frozen due dates.
    """
    try:
        parsed = {name: date.fromisoformat(value) for name, value in action_dates.items()}
    except ValueError as exc:
        raise SeedError(f"Invalid ISO action date in seed sources: {exc}") from exc
    earliest = min(parsed.values())
    if earliest > reference_date:
        return dict(action_dates)
    shift = reference_date + timedelta(days=1) - earliest
    return {
        name: (scheduled_date + shift).isoformat()
        for name, scheduled_date in parsed.items()
    }


def build_seed_context(
    data_dir: Path, user_address: str, reference_date: date | None = None
) -> SeedContext:
    """Assemble a SeedContext from the synthetic artifacts under ``data_dir`` (data/work).

    Structured facts are read from data/work and protocol/treatment facts from sibling data/fabric.
    Stale actionable dates are shifted forward as one block relative to ``reference_date`` (today by
    default), preserving their source ordering and offsets. The historical CrCl measurement date is
    intentionally never shifted because its age is clinical evidence.
    """
    work = Path(data_dir)
    overrides: dict[str, Any] = {"user_address": user_address}

    tasks_path = work / "open_tasks.json"
    if tasks_path.exists():
        tasks = _load_json(tasks_path)
        crcl = _first_matching(tasks, "task_id", "TASK-1042-CRCL")
        if crcl:
            overrides.update(
                task_id=crcl.get("task_id", "TASK-1042-CRCL"),
                owner_id=crcl.get("owner_id", "COORD-01"),
                owner_name=crcl.get("owner_name", "Dana Whitfield"),
                patient_id=crcl.get("related_patient_id", "PT-1042"),
                trial_id=crcl.get("related_trial_id", "NCT99004324"),
                crcl_due_date=crcl.get("due_date", "2026-07-08"),
            )
        pi = _first_matching(tasks, "task_id", "TASK-1042-PI-REVIEW")
        if pi:
            overrides["pi_task_id"] = pi.get("task_id", "TASK-1042-PI-REVIEW")
            overrides["pi_review_due_date"] = pi.get("due_date", "2026-07-09")
            overrides["pi_owner_id"] = pi.get("owner_id", "PI-01")
            overrides["pi_name"] = pi.get("owner_name", "Dr. Priya Anand")

    referral_path = work / "referral_queue.json"
    if referral_path.exists():
        referral = _load_json(referral_path)
        overrides["referral_queue_id"] = referral.get("queue_id", "RQ-THORACIC")
        entry = _first_matching(referral.get("entries", []), "patient_id", "PT-1042")
        if entry:
            overrides["referral_status"] = entry.get("status", "Awaiting screening")
            overrides.setdefault("patient_name", entry.get("display_name", "Alex Morgan"))

    pi_path = work / "pi_availability.json"
    if pi_path.exists():
        availability = _load_json(pi_path)
        slot = _first_matching(availability.get("slots", []), "purpose", "Patient Reviews")
        if slot:
            overrides["pi_slot_date"] = slot.get("date", "2026-07-07")
            overrides["pi_slot_start"] = slot.get("start", "09:00")
            overrides["pi_slot_end"] = slot.get("end", "10:00")

    fabric = work.parent / "fabric"
    amendments = _load_csv(
        fabric / "amendments.csv",
        ("trial_id", "amendment_id", "label", "summary"),
    )
    amendment = next(
        (
            row
            for row in amendments
            if row["trial_id"] == "NCT99004324" and row["amendment_id"] == "AMD-2"
        ),
        None,
    )
    if amendment is None:
        raise SeedError("data/fabric/amendments.csv is missing NCT99004324 AMD-2.")
    overrides.update(
        amendment_id=amendment["amendment_id"],
        amendment_label=amendment["label"],
        amendment_summary=amendment["summary"],
    )

    treatments = _load_csv(
        fabric / "treatment_history.csv",
        ("patient_id", "line", "drug_name", "best_response"),
    )
    treatment = next(
        (row for row in treatments if row["patient_id"] == "PT-1042" and row["line"] == "1"),
        None,
    )
    if treatment is None:
        raise SeedError("data/fabric/treatment_history.csv is missing PT-1042 line 1.")
    overrides.update(
        prior_therapy_name=treatment["drug_name"],
        prior_therapy_line=treatment["line"],
        prior_therapy_response=treatment["best_response"],
    )

    criteria = tuple(
        (row["criterion_id"], row["description"])
        for row in _load_csv(
            fabric / "trial_criteria.csv",
            ("trial_id", "criterion_id", "description"),
        )
        if row["trial_id"] == "NCT99004324"
    )
    if {criterion_id.rsplit("-", 1)[-1] for criterion_id, _ in criteria} != {
        "DX",
        "BIO",
        "PS",
        "REN",
        "RX",
    }:
        raise SeedError(
            "data/fabric/trial_criteria.csv must contain DX/BIO/PS/REN/RX for NCT99004324."
        )
    overrides["protocol_criteria"] = criteria

    scheduled = _shift_stale_action_dates(
        {
            "pi_slot_date": str(overrides.get("pi_slot_date", "2026-07-07")),
            "crcl_due_date": str(overrides.get("crcl_due_date", "2026-07-08")),
            "pi_review_due_date": str(
                overrides.get("pi_review_due_date", "2026-07-09")
            ),
        },
        reference_date or date.today(),
    )
    overrides.update(scheduled)

    valid = {f for f in SeedContext.__dataclass_fields__}
    return SeedContext(**{k: v for k, v in overrides.items() if k in valid})


def _first_matching(rows: Iterable[Mapping[str, Any]], key: str, value: str) -> Mapping[str, Any] | None:
    for row in rows or []:
        if isinstance(row, Mapping) and row.get(key) == value:
            return row
    return None


def _prior_therapy_summary(ctx: SeedContext) -> str:
    return (
        f"Prior therapy: {ctx.prior_therapy_name}, line {ctx.prior_therapy_line} / first-line, "
        f"best response {ctx.prior_therapy_response}."
    )


def _amendment_summary(ctx: SeedContext) -> str:
    return (
        f"{ctx.amendment_label} ({ctx.amendment_id}) line-of-therapy clarification: "
        f"{ctx.amendment_summary}"
    )


def _data_gap_summary(ctx: SeedContext) -> str:
    return (
        f"ECOG {ctx.ecog_status} is documented. Renal data gap: CrCl calculation method "
        f"{ctx.crcl_method}; CrCl {ctx.crcl_last_value} mL/min on {ctx.crcl_last_date} against "
        f"the >= {ctx.crcl_threshold} mL/min threshold. The measurement is "
        f"{ctx.crcl_staleness}."
    )


def _protocol_criterion_lines(ctx: SeedContext) -> tuple[str, ...]:
    statuses = {
        "DX": "Matched: metastatic NSCLC with adenocarcinoma histology.",
        "BIO": "Matched: EGFR exon 20 insertion documented.",
        "PS": f"Matched: ECOG {ctx.ecog_status}.",
        "REN": (
            f"Unresolved: CrCl ({ctx.crcl_method}) {ctx.crcl_last_value} mL/min on "
            f"{ctx.crcl_last_date}; repeat required."
        ),
        "RX": (
            f"PI review required under {ctx.amendment_label} ({ctx.amendment_id}); "
            "prior first-line platinum may be permitted with PI confirmation."
        ),
    }
    return tuple(
        f"{criterion_id}: {description} Status: {statuses[criterion_id.rsplit('-', 1)[-1]]}"
        for criterion_id, description in ctx.protocol_criteria
    )


# --------------------------------------------------------------------------------------------------
# Pure payload builders (no network, no secrets)
# --------------------------------------------------------------------------------------------------
def _email_body(ctx: SeedContext) -> str:
    return "\n".join(
        [
            SYNTHETIC_NOTICE,
            "",
            f"Task: {ctx.task_id} (Open).",
            f"Clinical owner: {ctx.owner_name} ({ctx.owner_id}).",
            f"Patient: {ctx.patient_name} ({ctx.patient_id}). Trial: {ctx.trial_id}.",
            "",
            f"Tumor board {ctx.tumor_board_id} decision: likely eligible pending repeat CrCl and PI "
            "confirmation.",
            _data_gap_summary(ctx),
            f"Repeat CrCl is due {ctx.crcl_due_date}.",
            _prior_therapy_summary(ctx),
            _amendment_summary(ctx),
            f"PI confirmation needed: ask {ctx.pi_name} to confirm whether the prior first-line "
            f"platinum is permitted (task {ctx.pi_task_id}, due {ctx.pi_review_due_date}).",
            f"Referral state: {ctx.referral_queue_id} entry for {ctx.patient_id} stays "
            f"{ctx.referral_status} until repeat CrCl and PI confirmation are documented.",
            "",
            "Scheduling constraints: keep the repeat CrCl coordination ahead of the "
            f"{ctx.crcl_due_date} due date, and align the PI review with {ctx.pi_name}'s "
            f"Patient Reviews slot on {ctx.pi_slot_date} {ctx.pi_slot_start} to {ctx.pi_slot_end}.",
        ]
    )


def build_email_message(ctx: SeedContext) -> dict[str, Any]:
    """Build the /me/sendMail request body for the deterministic self-addressed seed mail."""
    return {
        "message": {
            "subject": EMAIL_SUBJECT,
            "body": {"contentType": "Text", "content": _email_body(ctx)},
            "toRecipients": [{"emailAddress": {"address": ctx.user_address}}],
        },
        "saveToSentItems": True,
    }


def _event_transaction_id(stem: str, ctx: SeedContext) -> str:
    # Deterministic client-side dedup key. Graph rejects a create whose transactionId was used
    # recently, so reruns within the dedup window will not double-create even before the subject probe.
    return f"amciq-workiq-{stem}-{ctx.patient_id}-{SUBJECT_VERSION}".lower()


def _crcl_event_body(ctx: SeedContext) -> str:
    return "\n".join(
        [
            SYNTHETIC_NOTICE,
            "",
            f"{ctx.task_id}: repeat CrCl for {ctx.patient_name} ({ctx.patient_id}) before "
            f"{ctx.trial_id} screening.",
            f"Clinical owner: {ctx.owner_name} ({ctx.owner_id}). Due {ctx.crcl_due_date}.",
            _data_gap_summary(ctx),
            f"Tumor board {ctx.tumor_board_id} flagged this as the immediate blocker.",
        ]
    )


def _pi_event_body(ctx: SeedContext) -> str:
    return "\n".join(
        [
            SYNTHETIC_NOTICE,
            "",
            f"PI review with {ctx.pi_name} for {ctx.patient_name} ({ctx.patient_id}), trial "
            f"{ctx.trial_id}.",
            f"Task: {ctx.pi_task_id}. Owner: {ctx.pi_name} ({ctx.pi_owner_id}). Due "
            f"{ctx.pi_review_due_date}.",
            _prior_therapy_summary(ctx),
            _amendment_summary(ctx),
            _data_gap_summary(ctx),
            "Evidence packet protocol criteria:",
            *_protocol_criterion_lines(ctx),
        ]
    )


def build_calendar_events(ctx: SeedContext) -> list[dict[str, Any]]:
    """Build the two /me/events request bodies (repeat CrCl and PI review), with transactionId.

    Returns deterministic payloads whose ``subject`` is the idempotency probe key and whose
    ``transactionId`` is a stable client dedup key on create.
    """
    crcl = {
        "subject": CRCL_EVENT_SUBJECT,
        "body": {"contentType": "text", "content": _crcl_event_body(ctx)},
        "start": {"dateTime": f"{ctx.crcl_due_date}T09:00:00", "timeZone": ctx.time_zone},
        "end": {"dateTime": f"{ctx.crcl_due_date}T09:30:00", "timeZone": ctx.time_zone},
        "isReminderOn": False,
        "transactionId": _event_transaction_id("crcl", ctx),
    }
    pi = {
        "subject": PI_EVENT_SUBJECT,
        "body": {"contentType": "text", "content": _pi_event_body(ctx)},
        "start": {"dateTime": f"{ctx.pi_slot_date}T{ctx.pi_slot_start}:00", "timeZone": ctx.time_zone},
        "end": {"dateTime": f"{ctx.pi_slot_date}T{ctx.pi_slot_end}:00", "timeZone": ctx.time_zone},
        "isReminderOn": False,
        "transactionId": _event_transaction_id("pireview", ctx),
    }
    return [crcl, pi]


def _crcl_todo_task_body(ctx: SeedContext) -> str:
    return "\n".join(
        [
            SYNTHETIC_NOTICE,
            "",
            f"Task: {ctx.task_id}.",
            f"Clinical owner: {ctx.owner_name} ({ctx.owner_id}).",
            f"Patient: {ctx.patient_name} ({ctx.patient_id}). Trial: {ctx.trial_id}.",
            f"Due: {ctx.crcl_due_date}.",
            f"Tumor board {ctx.tumor_board_id} decision: likely eligible pending repeat CrCl and PI "
            "confirmation.",
            _data_gap_summary(ctx),
            f"Related PI task: {ctx.pi_task_id}, owned by {ctx.pi_name}, due "
            f"{ctx.pi_review_due_date}.",
        ]
    )


def _pi_todo_task_body(ctx: SeedContext) -> str:
    return "\n".join(
        [
            SYNTHETIC_NOTICE,
            "",
            f"Task: {ctx.pi_task_id}.",
            f"Clinical owner: {ctx.pi_name} ({ctx.pi_owner_id}).",
            f"Patient: {ctx.patient_name} ({ctx.patient_id}). Trial: {ctx.trial_id}.",
            f"Due: {ctx.pi_review_due_date}.",
            _prior_therapy_summary(ctx),
            _amendment_summary(ctx),
            _data_gap_summary(ctx),
            "Review all protocol criteria in the attached evidence packet:",
            *_protocol_criterion_lines(ctx),
        ]
    )


def build_todo_tasks(ctx: SeedContext) -> tuple[dict[str, Any], ...]:
    """Build both discrete open To Do tasks required by the PT-1042 workflow."""
    return (
        {
            "title": TODO_TASK_TITLE,
            "body": {"contentType": "text", "content": _crcl_todo_task_body(ctx)},
            "status": "notStarted",
            "dueDateTime": {
                "dateTime": f"{ctx.crcl_due_date}T00:00:00.0000000",
                "timeZone": ctx.time_zone,
            },
        },
        {
            "title": PI_TODO_TASK_TITLE,
            "body": {"contentType": "text", "content": _pi_todo_task_body(ctx)},
            "status": "notStarted",
            "dueDateTime": {
                "dateTime": f"{ctx.pi_review_due_date}T00:00:00.0000000",
                "timeZone": ctx.time_zone,
            },
        },
    )


def build_todo_task(ctx: SeedContext) -> dict[str, Any]:
    """Return the CRCL task for callers that predate the two-task collection."""
    return build_todo_tasks(ctx)[0]


def build_transcript_documents(ctx: SeedContext) -> tuple[TranscriptDocument, ...]:
    """Build two synthetic Teams-style meeting transcripts for SharePoint semantic retrieval."""
    tumor_board = TranscriptDocument(
        filename=TUMOR_BOARD_TRANSCRIPT_FILENAME,
        title="Thoracic Tumor Board Teams Transcript, 2026-06-30",
        paragraphs=(
            SYNTHETIC_NOTICE,
            "Meeting ID: TB-2026-06-30",
            f"Attendees: {ctx.pi_name} (PI-01), {ctx.owner_name} ({ctx.owner_id}), "
            "Morgan Lee (NAV-01).",
            "",
            "Transcript",
            f"{ctx.pi_name}: We are reviewing {ctx.patient_name} ({ctx.patient_id}) for "
            f"{ctx.trial_id}. The EGFR exon 20 biomarker and ECOG 1 appear aligned.",
            f"{ctx.owner_name}: The immediate blocker is renal eligibility. The last CrCl was "
            f"{ctx.crcl_last_value} mL/min on {ctx.crcl_last_date}, below the "
            f"{ctx.crcl_threshold} mL/min threshold using {ctx.crcl_method}; it is several days "
            "old and repeat is required.",
            f"Morgan Lee: I will help coordinate repeat labs before the {ctx.crcl_due_date} due date "
            f"and keep the {ctx.referral_queue_id} referral in {ctx.referral_status}.",
            f"{ctx.pi_name}: {_prior_therapy_summary(ctx)}",
            f"{ctx.pi_name}: {_amendment_summary(ctx)}",
            f"{ctx.owner_name}: I will own {ctx.task_id}, attach the tumor board evidence, and "
            f"package the repeat CrCl and {ctx.pi_task_id} PI confirmation for screening.",
            "",
            "Decision: likely eligible pending repeat CrCl and PI confirmation.",
        ),
    )
    screening_huddle = TranscriptDocument(
        filename=SCREENING_HUDDLE_TRANSCRIPT_FILENAME,
        title="PT-1042 Trial Screening Huddle Teams Transcript, 2026-07-01",
        paragraphs=(
            SYNTHETIC_NOTICE,
            "Meeting: Thoracic Trials Coordination screening huddle",
            f"Patient: {ctx.patient_name} ({ctx.patient_id})",
            f"Trial: {ctx.trial_id}",
            "",
            "Transcript",
            f"09:12 {ctx.owner_name}: Flagging {ctx.patient_id} from {ctx.tumor_board_id}. "
            f"{_data_gap_summary(ctx)}",
            f"09:25 Morgan Lee: I can coordinate repeat labs. We will keep the referral in "
            f"{ctx.referral_queue_id} as {ctx.referral_status} until the repeat result is back.",
            f"09:41 {ctx.owner_name}: I opened {ctx.task_id}, due {ctx.crcl_due_date}, and will "
            f"attach the tumor board evidence to the {ctx.trial_id} screening packet.",
            f"10:03 {ctx.owner_name}: I also opened {ctx.pi_task_id} for {ctx.pi_name}, due "
            f"{ctx.pi_review_due_date}. {_prior_therapy_summary(ctx)}",
            f"10:10 {ctx.owner_name}: {_amendment_summary(ctx)}",
            f"10:28 {ctx.pi_name}: Likely eligible pending repeat CrCl. Send the protocol excerpt "
            "on prior platinum before screening is finalized.",
            f"15:16 Morgan Lee: Repeat CrCl coordination is in progress. I will notify "
            f"{ctx.owner_id} when the lab appointment is confirmed.",
            "",
            f"Scheduling note: align PI review with {ctx.pi_name}'s Patient Reviews slot on "
            f"{ctx.pi_slot_date} from {ctx.pi_slot_start} to {ctx.pi_slot_end}.",
        ),
    )
    return tumor_board, screening_huddle


def build_self_chat_messages(ctx: SeedContext) -> tuple[dict[str, Any], ...]:
    """Build synthetic reminders for the signed-in user's Teams chat-with-yourself thread."""
    contents = (
        f"{SELF_CHAT_MARKERS[0]} Synthetic reminder: {ctx.patient_name} ({ctx.patient_id}) is "
        f"likely eligible for {ctx.trial_id} pending repeat CrCl. {_data_gap_summary(ctx)} "
        f"{ctx.task_id} is due {ctx.crcl_due_date}.",
        f"{SELF_CHAT_MARKERS[1]} Synthetic reminder: {ctx.pi_task_id} is owned by {ctx.pi_name} "
        f"({ctx.pi_owner_id}) and due {ctx.pi_review_due_date}. {_prior_therapy_summary(ctx)} "
        f"{_amendment_summary(ctx)}",
        f"{SELF_CHAT_MARKERS[2]} Synthetic reminder: keep {ctx.referral_queue_id} in "
        f"{ctx.referral_status}, coordinate repeat labs because {_data_gap_summary(ctx)} Align PI review with the "
        f"{ctx.pi_slot_date} {ctx.pi_slot_start}-{ctx.pi_slot_end} Patient Reviews slot.",
    )
    return tuple(
        {"body": {"contentType": "text", "content": f"{SYNTHETIC_NOTICE}\n\n{content}"}}
        for content in contents
    )


def build_docx(document: TranscriptDocument) -> bytes:
    """Create a small deterministic Word document without adding an external package dependency."""
    paragraph_xml: list[str] = []
    for paragraph in (document.title, "", *document.paragraphs):
        if not paragraph:
            paragraph_xml.append("<w:p/>")
            continue
        paragraph_xml.append(
            '<w:p><w:r><w:t xml:space="preserve">'
            f"{escape(paragraph)}"
            "</w:t></w:r></w:p>"
        )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(paragraph_xml)}<w:sectPr/></w:body>"
        "</w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in (
            ("[Content_Types].xml", content_types),
            ("_rels/.rels", relationships),
            ("word/document.xml", document_xml),
        ):
            info = zipfile.ZipInfo(name, date_time=(2026, 6, 30, 12, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content.encode("utf-8"))
    return output.getvalue()


def docx_text(content: bytes) -> str:
    """Extract normalized paragraph text while ignoring SharePoint-added package metadata."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            document_xml = archive.read("word/document.xml")
        root = ET.fromstring(document_xml)
    except (KeyError, ET.ParseError, zipfile.BadZipFile):
        return ""
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    return "\n".join(
        text
        for node in root.iter(f"{namespace}t")
        if (text := str(node.text or "").strip())
    )


def transcript_content_matches(stored: bytes, expected: bytes) -> bool:
    expected_text = docx_text(expected)
    return bool(expected_text) and docx_text(stored) == expected_text


def build_productivity_files(
    ctx: SeedContext, data_dir: Path
) -> tuple[ProductivityFile, ...]:
    """Build Word, Excel, and PowerPoint artifacts from the deterministic work data."""
    memo = TranscriptDocument(
        filename=PRODUCTIVITY_MEMO_FILENAME,
        title="PT-1042 Screening Operations Memo",
        paragraphs=(
            SYNTHETIC_NOTICE,
            f"Prepared for: {ctx.owner_name} ({ctx.owner_id})",
            f"Patient: {ctx.patient_name} ({ctx.patient_id})",
            f"Trial: {ctx.trial_id}",
            "",
            "Executive summary",
            f"{ctx.patient_name} is likely eligible pending repeat CrCl and PI confirmation. "
            f"{_data_gap_summary(ctx)}",
            _prior_therapy_summary(ctx),
            _amendment_summary(ctx),
            "",
            "Protocol criteria evidence packet",
            *_protocol_criterion_lines(ctx),
            "",
            "Required actions",
            f"1. {ctx.owner_name} owns {ctx.task_id}; coordinate repeat CrCl by "
            f"{ctx.crcl_due_date}.",
            f"2. {ctx.pi_name} ({ctx.pi_owner_id}) owns {ctx.pi_task_id}; clarify the "
            f"prior-platinum exclusion by {ctx.pi_review_due_date}.",
            f"3. Keep {ctx.referral_queue_id} in {ctx.referral_status} until both items are "
            "documented.",
            f"4. Use the {ctx.pi_slot_date} {ctx.pi_slot_start}-{ctx.pi_slot_end} Patient Reviews "
            "slot for PI follow-up.",
            "",
            f"Source context: {ctx.tumor_board_id}, {ctx.task_id}, {ctx.referral_queue_id}.",
        ),
    )
    return (
        ProductivityFile(
            filename=memo.filename,
            content_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            content=build_docx(memo),
            retrieval_mode="semantic",
        ),
        ProductivityFile(
            filename=PRODUCTIVITY_TRACKER_FILENAME,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            content=_build_productivity_workbook(ctx, data_dir),
            retrieval_mode="lexical",
        ),
        ProductivityFile(
            filename=PRODUCTIVITY_DECK_FILENAME,
            content_type=(
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            ),
            content=_build_productivity_deck(ctx, data_dir),
            retrieval_mode="semantic",
        ),
    )


def _build_productivity_workbook(ctx: SeedContext, data_dir: Path) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    tasks = _load_json(data_dir / "open_tasks.json")
    referrals = _load_json(data_dir / "referral_queue.json")
    availability = _load_json(data_dir / "pi_availability.json")
    tasks_by_patient: dict[str, list[Mapping[str, Any]]] = {}
    for task in tasks:
        if not isinstance(task, Mapping):
            continue
        patient_id = str(task.get("related_patient_id") or "")
        if patient_id:
            tasks_by_patient.setdefault(patient_id, []).append(task)

    scheduled_due_dates = {
        ctx.task_id: ctx.crcl_due_date,
        ctx.pi_task_id: ctx.pi_review_due_date,
    }

    def task_due_date(task: Mapping[str, Any]) -> Any:
        return scheduled_due_dates.get(str(task.get("task_id") or ""), task.get("due_date"))

    workbook = Workbook()
    tracker = workbook.active
    tracker.title = "Screening Tracker"
    tracker.append(
        [
            "Patient ID",
            "Patient",
            "Trial ID",
            "Priority",
            "Referral Status",
            "Owner",
            "Open Tasks",
            "Due Date",
            "Next Action",
        ]
    )
    for entry in referrals.get("entries", []):
        patient_id = entry.get("patient_id")
        patient_tasks = sorted(
            tasks_by_patient.get(str(patient_id), []),
            key=lambda task: (str(task.get("due_date") or ""), str(task.get("task_id") or "")),
        )
        primary_task = patient_tasks[0] if patient_tasks else {}
        trial_id = primary_task.get("related_trial_id") or (
            ctx.trial_id if patient_id == ctx.patient_id else ""
        )
        tracker.append(
            [
                patient_id,
                entry.get("display_name"),
                trial_id,
                entry.get("priority"),
                entry.get("status"),
                primary_task.get("owner_name") or referrals.get("owner"),
                ", ".join(str(task.get("task_id") or "") for task in patient_tasks),
                task_due_date(primary_task),
                "; ".join(str(task.get("title") or "") for task in patient_tasks)
                or entry.get("reason"),
            ]
        )

    task_sheet = workbook.create_sheet("Open Tasks")
    task_sheet.append(
        [
            "Task ID",
            "Title",
            "Owner ID",
            "Owner",
            "Patient ID",
            "Trial ID",
            "Due Date",
            "Status",
        ]
    )
    for task in tasks:
        task_sheet.append(
            [
                task.get("task_id"),
                task.get("title"),
                task.get("owner_id"),
                task.get("owner_name"),
                task.get("related_patient_id"),
                task.get("related_trial_id"),
                task_due_date(task),
                task.get("status"),
            ]
        )

    pi_sheet = workbook.create_sheet("PI Availability")
    pi_sheet.append(["PI ID", "PI", "Date", "Start", "End", "Purpose"])
    shifted_patient_review = False
    for slot in availability.get("slots", []):
        is_selected_patient_review = (
            not shifted_patient_review and slot.get("purpose") == "Patient Reviews"
        )
        if is_selected_patient_review:
            shifted_patient_review = True
        pi_sheet.append(
            [
                availability.get("pi_id"),
                availability.get("display_name"),
                ctx.pi_slot_date if is_selected_patient_review else slot.get("date"),
                ctx.pi_slot_start if is_selected_patient_review else slot.get("start"),
                ctx.pi_slot_end if is_selected_patient_review else slot.get("end"),
                slot.get("purpose"),
            ]
        )

    notes = workbook.create_sheet("Read Me")
    notes.append(["AMC IQ Productivity Workbook"])
    notes.append([SYNTHETIC_NOTICE])
    notes.append(
        [
            "Excel is included as a structured operational artifact. Critical PT-1042 and "
            "NCT99004324 facts are duplicated in the Word memo and PowerPoint deck for semantic "
            "retrieval."
        ]
    )

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    for sheet in (tracker, task_sheet, pi_sheet):
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")
        for column_cells in sheet.columns:
            values = [str(cell.value or "") for cell in column_cells]
            width = min(max(len(value) for value in values) + 2, 60)
            sheet.column_dimensions[column_cells[0].column_letter].width = width

    workbook.properties.title = "AMC IQ Trial Screening Productivity Tracker"
    workbook.properties.subject = f"{ctx.patient_id} / {ctx.trial_id} operational workflow"
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _build_productivity_deck(ctx: SeedContext, data_dir: Path) -> bytes:
    from pptx import Presentation
    from pptx.util import Pt

    tasks = _load_json(data_dir / "open_tasks.json")
    referrals = _load_json(data_dir / "referral_queue.json")
    presentation = Presentation()
    presentation.core_properties.title = "AMC IQ Thoracic Trials Weekly Operations"
    presentation.core_properties.subject = f"{ctx.patient_id} / {ctx.trial_id}"

    title_slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    title_slide.shapes.title.text = "Thoracic Trials Weekly Operations"
    title_slide.placeholders[1].text = (
        f"AMC IQ synthetic productivity deck | {ctx.patient_id} / {ctx.trial_id}\n"
        "No PHI. Not clinical decision support."
    )

    _add_bullet_slide(
        presentation,
        f"{ctx.patient_name} ({ctx.patient_id}) readiness",
        (
            f"Assessment: likely eligible for {ctx.trial_id} pending repeat CrCl and PI confirmation",
            _data_gap_summary(ctx),
            f"Owner: {ctx.owner_name} ({ctx.owner_id}); task {ctx.task_id} due "
            f"{ctx.crcl_due_date}",
            f"PI clarification: {ctx.pi_name} ({ctx.pi_owner_id}); task {ctx.pi_task_id} due "
            f"{ctx.pi_review_due_date}",
        ),
    )
    _add_bullet_slide(
        presentation,
        "PT-1042 protocol evidence packet",
        (
            *_protocol_criterion_lines(ctx),
            _prior_therapy_summary(ctx),
            _amendment_summary(ctx),
        ),
    )
    _add_bullet_slide(
        presentation,
        "This week's action plan",
        (
            f"Coordinate repeat CrCl before {ctx.crcl_due_date}",
            f"Use {ctx.pi_name}'s {ctx.pi_slot_date} {ctx.pi_slot_start}-{ctx.pi_slot_end} "
            "Patient Reviews slot",
            f"Keep {ctx.referral_queue_id} in {ctx.referral_status}",
            f"{_data_gap_summary(ctx)}",
            "Attach tumor board, repeat lab, protocol criteria, and PI clarification to the "
            "screening packet",
        ),
    )
    _add_bullet_slide(
        presentation,
        "Operational workload",
        (
            f"{len(tasks)} open trial-coordination tasks",
            f"{len(referrals.get('entries', []))} thoracic referral-queue entries",
            f"High priority: {ctx.patient_id} for {ctx.trial_id}",
            SYNTHETIC_NOTICE,
        ),
    )
    for slide in presentation.slides:
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                for paragraph in shape.text_frame.paragraphs:
                    for run in paragraph.runs:
                        run.font.size = Pt(20 if shape == slide.shapes.title else 16)

    output = io.BytesIO()
    presentation.save(output)
    return output.getvalue()


def _add_bullet_slide(presentation: Any, title: str, bullets: Sequence[str]) -> None:
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = title
    text_frame = slide.placeholders[1].text_frame
    text_frame.clear()
    for index, bullet in enumerate(bullets):
        paragraph = text_frame.paragraphs[0] if index == 0 else text_frame.add_paragraph()
        paragraph.text = bullet
        paragraph.level = 0


def xlsx_text(content: bytes) -> str:
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False)
    except (OSError, ValueError, zipfile.BadZipFile):
        return ""
    values: list[str] = []
    try:
        for sheet in workbook.worksheets:
            values.append(sheet.title)
            for row in sheet.iter_rows():
                values.extend(str(cell.value).strip() for cell in row if cell.value is not None)
    finally:
        workbook.close()
    return "\n".join(value for value in values if value)


def pptx_text(content: bytes) -> str:
    from pptx import Presentation

    try:
        presentation = Presentation(io.BytesIO(content))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return ""
    values: list[str] = []
    for slide in presentation.slides:
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                text = str(shape.text or "").strip()
                if text:
                    values.append(text)
    return "\n".join(values)


def office_content_matches(filename: str, stored: bytes, expected: bytes) -> bool:
    extension = Path(filename).suffix.casefold()
    extractor = {
        ".docx": docx_text,
        ".xlsx": xlsx_text,
        ".pptx": pptx_text,
    }.get(extension)
    if not extractor:
        return False
    expected_text = extractor(expected)
    return bool(expected_text) and extractor(stored) == expected_text


# --------------------------------------------------------------------------------------------------
# Pure idempotent-selection helpers (decide create vs patch/skip from a listing)
# --------------------------------------------------------------------------------------------------
def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def select_by_field(items: Sequence[Mapping[str, Any]], field_name: str, wanted: str) -> Mapping[str, Any] | None:
    """Return the first item whose ``field_name`` equals ``wanted`` (trimmed, case-insensitive)."""
    target = _norm(wanted)
    for item in items or []:
        if _norm(item.get(field_name)) == target:
            return item
    return None


def select_existing_message(messages: Sequence[Mapping[str, Any]], subject: str) -> Mapping[str, Any] | None:
    return select_by_field(messages, "subject", subject)


def select_existing_event(events: Sequence[Mapping[str, Any]], subject: str) -> Mapping[str, Any] | None:
    return select_by_field(events, "subject", subject)


def select_existing_list(lists: Sequence[Mapping[str, Any]], name: str) -> Mapping[str, Any] | None:
    return select_by_field(lists, "displayName", name)


def select_existing_task(tasks: Sequence[Mapping[str, Any]], title: str) -> Mapping[str, Any] | None:
    return select_by_field(tasks, "title", title)


# --------------------------------------------------------------------------------------------------
# Graph client with bounded retry/backoff
# --------------------------------------------------------------------------------------------------
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass
class GraphClient:
    """A thin Microsoft Graph v1.0 client with bounded retry for 429/5xx.

    The access token is held only in this instance and injected into the Authorization header per
    request. It is never logged, printed, or included in ``__repr__``. Inject ``http_client`` and
    ``sleep`` for testing.
    """

    access_token: str
    http_client: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=30.0))
    max_attempts: int = 5
    base_backoff: float = 1.0
    max_backoff: float = 30.0
    sleep: Callable[[float], None] = time.sleep

    def __repr__(self) -> str:  # never leak the token
        return f"GraphClient(max_attempts={self.max_attempts})"

    def _headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
        if extra:
            headers.update(extra)
        return headers

    def _retry_after_seconds(self, response: httpx.Response, attempt: int) -> float:
        header = response.headers.get("Retry-After")
        if header:
            try:
                return min(float(header), self.max_backoff)
            except ValueError:
                pass
        return min(self.base_backoff * (2 ** (attempt - 1)), self.max_backoff)

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        content: bytes | str | None = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        retry_transient: bool = True,
    ) -> httpx.Response:
        if json_body is not None and content is not None:
            raise ValueError("Graph request cannot contain both json_body and raw content.")
        url = path if path.startswith("http") else f"{GRAPH_BASE}{path}"
        request_headers = self._headers(headers)
        if json_body is not None:
            request_headers.setdefault("Content-Type", "application/json")

        last_error: str = ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.http_client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    content=content,
                    headers=request_headers,
                    follow_redirects=True,
                )
            except httpx.RequestError as exc:
                # Transient network/timeout failures are retried with backoff and, on exhaustion,
                # surfaced as GraphError so main() reports a clean error instead of a raw traceback.
                last_error = f"network error: {type(exc).__name__}"
                if retry_transient and attempt < self.max_attempts:
                    self.sleep(min(self.base_backoff * (2 ** (attempt - 1)), self.max_backoff))
                    continue
                raise GraphError(
                    f"{method} {url} failed after {attempt} attempts: {last_error}"
                ) from exc
            if response.status_code in (401, 403):
                raise GraphPermissionError(
                    f"{method} {url} failed with HTTP {response.status_code}: "
                    f"{_error_detail(response)}. This is a permission or unavailable-workload error; "
                    "check the granted delegated scopes and that the workload is licensed."
                )
            if response.status_code == 404:
                raise GraphNotFoundError(
                    f"{method} {url} failed with HTTP 404: {_error_detail(response)}"
                )
            if response.status_code == 412:
                raise GraphPreconditionFailedError(
                    f"{method} {url} failed with HTTP 412: {_error_detail(response)}"
                )
            if response.status_code in _RETRYABLE_STATUS:
                last_error = f"HTTP {response.status_code}: {_error_detail(response)}"
                if retry_transient and attempt < self.max_attempts:
                    self.sleep(self._retry_after_seconds(response, attempt))
                    continue
                if response.status_code == 429:
                    raise GraphThrottledError(
                        f"{method} {url} was throttled: {last_error}",
                        self._retry_after_seconds(response, attempt),
                    )
                raise GraphError(f"{method} {url} still failing after {attempt} attempts: {last_error}")
            if response.status_code >= 400:
                raise GraphError(f"{method} {url} failed with HTTP {response.status_code}: {_error_detail(response)}")
            return response
        raise GraphError(f"{method} {url} exhausted retries: {last_error}")

    def get_json(self, path: str, *, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.request("GET", path, params=params).json()


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return response.text[:300]
    error = payload.get("error") if isinstance(payload, Mapping) else None
    if isinstance(error, Mapping):
        return f"{error.get('code', 'unknown')}: {error.get('message', '')}"[:400]
    return json.dumps(payload)[:300]


# --------------------------------------------------------------------------------------------------
# Sign-in (MSAL device code) - never prints or returns the token
# --------------------------------------------------------------------------------------------------
def acquire_token_device_code(
    tenant_id: str,
    client_id: str,
    scopes: Sequence[str] = DELEGATED_SCOPES,
    *,
    cache_path: Path | None = None,
    app: Any | None = None,
    print_fn: Callable[[str], None] = print,
) -> str:
    """Acquire a delegated token silently from an encrypted cache, then use device code if needed.

    The default cache uses msal-extensions encrypted persistence (Windows DPAPI on this host), so
    refresh tokens survive process restarts without being stored in plaintext. Prints only the
    device-login message when interaction is actually required. The returned access token is held
    in memory by the caller and is never printed here. ``app`` is injectable for testing.
    """
    if app is None:
        import msal
        from msal_extensions import PersistedTokenCache, build_encrypted_persistence

        resolved_cache_path = cache_path or _default_token_cache_path(client_id)
        resolved_cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache = PersistedTokenCache(build_encrypted_persistence(str(resolved_cache_path)))
        app = msal.PublicClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            token_cache=cache,
        )
    if hasattr(app, "get_accounts") and hasattr(app, "acquire_token_silent"):
        for account in app.get_accounts():
            result = app.acquire_token_silent(list(scopes), account=account)
            if result and "access_token" in result:
                return result["access_token"]
            if result and result.get("error") not in {
                "interaction_required",
                "consent_required",
                "invalid_grant",
            }:
                raise DeviceCodeAuthError(
                    "Silent token acquisition failed: "
                    f"{result.get('error_description', result.get('error', 'unknown error'))}"
                )
    flow = app.initiate_device_flow(scopes=list(scopes))
    if "user_code" not in flow:
        raise DeviceCodeAuthError(
            f"Failed to start device flow: {flow.get('error_description', flow.get('error', 'unknown error'))}"
        )
    print_fn(flow["message"])
    sys.stdout.flush()
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise DeviceCodeAuthError(
            f"Device-code sign-in failed: {result.get('error_description', result.get('error', 'unknown error'))}"
        )
    return result["access_token"]


def _default_token_cache_path(client_id: str) -> Path:
    cache_root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".cache"))
    return cache_root / "AMC IQ" / "msal" / f"{client_id}.bin"


# --------------------------------------------------------------------------------------------------
# Seeding operations (idempotent upserts)
# --------------------------------------------------------------------------------------------------
def seed_email(graph: GraphClient, ctx: SeedContext) -> dict[str, Any]:
    existing = graph.get_json(
        "/me/messages",
        params={"$filter": f"subject eq '{_escape_odata(EMAIL_SUBJECT)}'", "$select": "id,subject", "$top": 5},
    ).get("value", [])
    if select_existing_message(existing, EMAIL_SUBJECT):
        return {"workload": "mail", "action": "skipped", "reason": "existing message with subject", "subject": EMAIL_SUBJECT}
    graph.request("POST", "/me/sendMail", json_body=build_email_message(ctx))
    return {"workload": "mail", "action": "sent", "subject": EMAIL_SUBJECT}


def seed_events(graph: GraphClient, ctx: SeedContext) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for payload in build_calendar_events(ctx):
        subject = payload["subject"]
        existing = graph.get_json(
            "/me/events",
            params={"$filter": f"subject eq '{_escape_odata(subject)}'", "$select": "id,subject", "$top": 5},
        ).get("value", [])
        match = select_existing_event(existing, subject)
        if match:
            patch = {k: v for k, v in payload.items() if k != "transactionId"}
            graph.request("PATCH", f"/me/events/{match['id']}", json_body=patch)
            results.append({"workload": "event", "action": "patched", "subject": subject, "id": match["id"]})
        else:
            created = graph.request("POST", "/me/events", json_body=payload).json()
            results.append({"workload": "event", "action": "created", "subject": subject, "id": created.get("id")})
    return results


def seed_todo(graph: GraphClient, ctx: SeedContext) -> dict[str, Any]:
    lists = graph.get_json("/me/todo/lists", params={"$top": 100}).get("value", [])
    todo_list = select_existing_list(lists, TODO_LIST_NAME)
    if todo_list:
        list_id = todo_list["id"]
        list_action = "reused"
    else:
        todo_list = graph.request("POST", "/me/todo/lists", json_body={"displayName": TODO_LIST_NAME}).json()
        list_id = todo_list["id"]
        list_action = "created"

    tasks = graph.get_json(f"/me/todo/lists/{list_id}/tasks", params={"$top": 100}).get("value", [])
    task_results: list[dict[str, Any]] = []
    for payload in build_todo_tasks(ctx):
        title = payload["title"]
        match = select_existing_task(tasks, title)
        if match:
            graph.request(
                "PATCH",
                f"/me/todo/lists/{list_id}/tasks/{match['id']}",
                json_body=payload,
            )
            task_results.append({"title": title, "action": "patched", "id": match["id"]})
        else:
            created = graph.request(
                "POST", f"/me/todo/lists/{list_id}/tasks", json_body=payload
            ).json()
            task_results.append(
                {"title": title, "action": "created", "id": created.get("id")}
            )
    return {
        "workload": "todo",
        "list": TODO_LIST_NAME,
        "list_action": list_action,
        "tasks": task_results,
    }


def _graph_path(value: str) -> str:
    return quote(value, safe="/")


def ensure_sharepoint_folder(graph: GraphClient, folder_path: str) -> dict[str, str]:
    """Resolve the root SharePoint drive and create a deterministic folder path."""
    site = graph.get_json("/sites/root", params={"$select": "id,displayName,webUrl"})
    site_id = site.get("id")
    if not site_id:
        raise GraphError("Microsoft Graph /sites/root did not return a site ID.")

    drive = graph.get_json(f"/sites/{site_id}/drive", params={"$select": "id,name,webUrl"})
    drive_id = drive.get("id")
    if not drive_id:
        raise GraphError("The root SharePoint site does not expose a default document library.")
    root = graph.get_json(f"/drives/{drive_id}/root", params={"$select": "id"})
    parent_id = root.get("id")
    if not parent_id:
        raise GraphError("The root SharePoint document library did not return a root item ID.")

    current_path = ""
    folder_web_url = drive.get("webUrl") or site.get("webUrl") or ""
    for segment in folder_path.split("/"):
        current_path = f"{current_path}/{segment}".strip("/")
        try:
            folder = graph.get_json(
                f"/drives/{drive_id}/root:/{_graph_path(current_path)}",
                params={"$select": "id,name,webUrl,folder"},
            )
            if "folder" not in folder:
                raise GraphError(
                    f"SharePoint path '{current_path}' exists but is not a folder."
                )
        except GraphNotFoundError:
            folder = graph.request(
                "POST",
                f"/drives/{drive_id}/items/{parent_id}/children",
                json_body={
                    "name": segment,
                    "folder": {},
                    "@microsoft.graph.conflictBehavior": "fail",
                },
            ).json()
        parent_id = folder.get("id")
        if not parent_id:
            raise GraphError(f"SharePoint folder '{current_path}' did not return an item ID.")
        folder_web_url = folder.get("webUrl") or folder_web_url

    return {
        "site_id": site_id,
        "site_name": site.get("displayName") or "root SharePoint site",
        "site_web_url": site.get("webUrl") or "",
        "drive_id": drive_id,
        "drive_name": drive.get("name") or "Documents",
        "folder_id": parent_id,
        "folder_path": folder_path,
        "folder_web_url": folder_web_url,
    }


def ensure_sharepoint_transcript_folder(graph: GraphClient) -> dict[str, str]:
    return ensure_sharepoint_folder(graph, SHAREPOINT_FOLDER_PATH)


def _read_existing_sharepoint_file(
    graph: GraphClient, drive_id: str, path: str
) -> tuple[dict[str, Any], bytes] | None:
    try:
        existing = graph.get_json(
            f"/drives/{drive_id}/root:/{_graph_path(path)}",
            params={"$select": "id,name,webUrl,eTag"},
        )
    except GraphNotFoundError:
        return None
    item_id = existing.get("id")
    if not item_id:
        raise GraphError(f"Existing SharePoint file '{path}' has no item ID.")
    stored = graph.request("GET", f"/drives/{drive_id}/items/{item_id}/content").content
    return existing, stored


def _sharepoint_file_result(
    filename: str,
    existing: Mapping[str, Any],
    *,
    action: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "filename": filename,
        "action": action,
        "reason": reason,
        "id": existing.get("id"),
        "web_url": existing.get("webUrl"),
    }


def _seed_sharepoint_file(
    graph: GraphClient,
    target: Mapping[str, str],
    folder_path: str,
    filename: str,
    content: bytes,
    content_type: str,
    matches: Callable[[bytes, bytes], bool],
    label: str,
) -> dict[str, Any]:
    path = f"{folder_path}/{filename}"
    existing_file = _read_existing_sharepoint_file(graph, target["drive_id"], path)
    if existing_file:
        existing, stored = existing_file
        if not matches(stored, content):
            raise GraphError(
                f"SharePoint {label} '{filename}' already exists with different content. "
                "Refusing to overwrite it; bump SUBJECT_VERSION for a new seed."
            )
        return _sharepoint_file_result(
            filename,
            existing,
            action="skipped",
            reason="identical file already exists",
        )
    try:
        response = graph.request(
            "PUT",
            f"/drives/{target['drive_id']}/root:/{_graph_path(path)}:/content",
            content=content,
            headers={"Content-Type": content_type, "If-None-Match": "*"},
            retry_transient=False,
        )
    except GraphError:
        recovered_file = _read_existing_sharepoint_file(graph, target["drive_id"], path)
        if not recovered_file:
            raise
        recovered, stored = recovered_file
        if not matches(stored, content):
            raise GraphError(
                f"SharePoint {label} '{filename}' appeared after an ambiguous upload but its "
                "content differs. Refusing to treat the write as successful."
            )
        return _sharepoint_file_result(
            filename,
            recovered,
            action="recovered",
            reason="upload response was ambiguous; identical file was re-read",
        )
    item = response.json()
    return {
        "filename": filename,
        "action": "created",
        "id": item.get("id"),
        "web_url": item.get("webUrl"),
    }


def seed_sharepoint_transcripts(graph: GraphClient, ctx: SeedContext) -> dict[str, Any]:
    """Upsert deterministic Word transcripts into the root SharePoint document library."""
    target = ensure_sharepoint_transcript_folder(graph)
    files = [
        _seed_sharepoint_file(
            graph,
            target,
            SHAREPOINT_FOLDER_PATH,
            document.filename,
            build_docx(document),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            transcript_content_matches,
            "transcript",
        )
        for document in build_transcript_documents(ctx)
    ]
    return _sharepoint_seed_result(target, files)


def seed_sharepoint_productivity(
    graph: GraphClient, ctx: SeedContext, data_dir: Path
) -> dict[str, Any]:
    """Upsert deterministic Word, Excel, and PowerPoint productivity artifacts."""
    target = ensure_sharepoint_folder(graph, PRODUCTIVITY_FOLDER_PATH)
    files = [
        {
            **_seed_sharepoint_file(
                graph,
                target,
                PRODUCTIVITY_FOLDER_PATH,
                artifact.filename,
                artifact.content,
                artifact.content_type,
                lambda stored, expected, filename=artifact.filename: office_content_matches(
                    filename, stored, expected
                ),
                "productivity file",
            ),
            "retrieval_mode": artifact.retrieval_mode,
        }
        for artifact in build_productivity_files(ctx, data_dir)
    ]
    return _sharepoint_seed_result(target, files)


def _sharepoint_seed_result(
    target: Mapping[str, str], files: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    return {
        "workload": "sharepoint",
        "site_name": target["site_name"],
        "site_id": target["site_id"],
        "site_web_url": target["site_web_url"],
        "drive_name": target["drive_name"],
        "folder_path": target["folder_path"],
        "folder_web_url": target["folder_web_url"],
        "files": list(files),
    }


def _chat_message_content(message: Mapping[str, Any]) -> str:
    body = message.get("body")
    if not isinstance(body, Mapping):
        return ""
    return str(body.get("content") or "")


def list_teams_self_chat_messages(graph: GraphClient) -> list[Mapping[str, Any]]:
    """List the self-chat messages needed to prove idempotency before sending."""
    messages: list[Mapping[str, Any]] = []
    next_path: str | None = f"/chats/{TEAMS_SELF_CHAT_ID}/messages"
    params: Mapping[str, Any] | None = {"$top": 50}
    for _ in range(50):
        if not next_path:
            return messages
        try:
            page = graph.get_json(next_path, params=params)
        except GraphNotFoundError as exc:
            raise GraphError(
                "The Teams chat-with-yourself thread is unavailable. Open your own name in Teams "
                "once to initialize the self-chat, then rerun the seeder."
            ) from exc
        messages.extend(item for item in page.get("value", []) if isinstance(item, Mapping))
        next_path = page.get("@odata.nextLink")
        params = None
    raise GraphError(
        "The Teams self-chat has more than 2,500 messages; refusing to send because idempotency "
        "could not be proven."
    )


def seed_teams_self_chat(graph: GraphClient, ctx: SeedContext) -> dict[str, Any]:
    """Seed missing synthetic notes into the signed-in user's Teams self-chat."""
    existing = list_teams_self_chat_messages(graph)
    results: list[dict[str, Any]] = []
    for marker, payload in zip(SELF_CHAT_MARKERS, build_self_chat_messages(ctx), strict=True):
        expected_content = payload["body"]["content"]
        matched = next(
            (
                message
                for message in existing
                if marker in _chat_message_content(message)
            ),
            None,
        )
        if matched and _chat_message_content(matched).strip() != expected_content.strip():
            raise GraphError(
                f"Teams self-chat marker '{marker}' already exists with different content. "
                "Refusing to send a conflicting seed message."
            )
        if matched:
            results.append({"marker": marker, "action": "skipped", "reason": "existing message"})
            continue
        for send_attempt in range(1, 4):
            try:
                created = graph.request(
                    "POST",
                    f"/chats/{TEAMS_SELF_CHAT_ID}/messages",
                    json_body=payload,
                    retry_transient=False,
                ).json()
                break
            except GraphThrottledError as exc:
                if send_attempt == 3:
                    raise
                graph.sleep(exc.retry_after)
                continue
            except GraphError as exc:
                recovered = None
                for delay in (1.0, 2.0, 4.0):
                    graph.sleep(delay)
                    refreshed = list_teams_self_chat_messages(graph)
                    recovered = next(
                        (
                            message
                            for message in refreshed
                            if _chat_message_content(message).strip() == expected_content.strip()
                        ),
                        None,
                    )
                    if recovered:
                        break
                if not recovered:
                    raise
                created = recovered
                results.append(
                    {
                        "marker": marker,
                        "action": "recovered",
                        "reason": f"send response was ambiguous: {type(exc).__name__}",
                        "id": created.get("id"),
                    }
                )
                existing.append(created)
                break
        if results and results[-1].get("marker") == marker and results[-1]["action"] == "recovered":
            continue
        results.append({"marker": marker, "action": "sent", "id": created.get("id")})
        existing.append(created)
    return {
        "workload": "teams_self_chat",
        "chat_id": TEAMS_SELF_CHAT_ID,
        "messages": results,
    }


def verify_copilot_sharepoint_retrieval(
    graph: GraphClient, sharepoint_result: Mapping[str, Any]
) -> dict[str, Any]:
    """Probe the Copilot semantic index for both seeded SharePoint transcript documents."""
    expected_filenames = {TUMOR_BOARD_TRANSCRIPT_FILENAME, SCREENING_HUDDLE_TRANSCRIPT_FILENAME}
    expected_urls: dict[str, str] = {}
    for item in sharepoint_result.get("files", []):
        if not isinstance(item, Mapping):
            continue
        filename = str(item.get("filename") or "")
        web_url = str(item.get("web_url") or "")
        if filename in expected_filenames and web_url:
            expected_urls[_normalize_web_url(web_url)] = filename
    if set(expected_urls.values()) != expected_filenames:
        raise GraphError(
            "SharePoint transcript upload did not return web URLs for both expected files; "
            "Copilot retrieval cannot be verified safely."
        )
    folder_web_url = str(sharepoint_result.get("folder_web_url") or "")
    if not folder_web_url:
        raise GraphError("SharePoint transcript folder URL is required for scoped Copilot retrieval.")
    filter_expression = " OR ".join(
        f'Filename:"{filename}"' for filename in sorted(expected_filenames)
    )
    response = graph.request(
        "POST",
        "/copilot/retrieval",
        json_body={
            "queryString": (
                "What renal blocker, PI clarification, task owner, and scheduling actions were "
                "recorded for Alex Morgan PT-1042 and trial NCT99004324?"
            ),
            "dataSource": "sharePoint",
            "filterExpression": (
                f'Path:"{folder_web_url.rstrip("/")}" AND ({filter_expression})'
            ),
            "resourceMetadata": ["title"],
            "maximumNumberOfResults": 10,
        },
    ).json()
    hits = response.get("retrievalHits", [])
    matched_files: set[str] = set()
    relevant_extracts: list[str] = []
    for hit in hits if isinstance(hits, list) else []:
        if not isinstance(hit, Mapping):
            continue
        web_url = str(hit.get("webUrl") or "")
        filename = expected_urls.get(_normalize_web_url(web_url))
        if not filename:
            continue
        matched_files.add(filename)
        for extract in hit.get("extracts", []):
            if isinstance(extract, Mapping):
                text = str(extract.get("text") or "")
                if text:
                    relevant_extracts.append(text)
    combined = "\n".join(relevant_extracts).casefold()
    evidence_found = "pt-1042" in combined and "nct99004324" in combined
    indexed = matched_files == expected_filenames and evidence_found
    return {
        "workload": "copilot_retrieval",
        "data_source": "sharePoint",
        "status": "indexed" if indexed else "pending",
        "matched_files": sorted(matched_files),
        "expected_files": sorted(expected_filenames),
        "evidence_found": evidence_found,
        "folder_web_url": folder_web_url,
    }


def verify_copilot_productivity_retrieval(
    graph: GraphClient, productivity_result: Mapping[str, Any]
) -> dict[str, Any]:
    """Probe semantic Word/PowerPoint retrieval and lexical Excel retrieval separately."""
    expected_modes = {
        PRODUCTIVITY_MEMO_FILENAME: "semantic",
        PRODUCTIVITY_TRACKER_FILENAME: "lexical",
        PRODUCTIVITY_DECK_FILENAME: "semantic",
    }
    expected_urls: dict[str, str] = {}
    for item in productivity_result.get("files", []):
        if not isinstance(item, Mapping):
            continue
        filename = str(item.get("filename") or "")
        web_url = str(item.get("web_url") or "")
        if filename in expected_modes and web_url:
            expected_urls[_normalize_web_url(web_url)] = filename
    if set(expected_urls.values()) != set(expected_modes):
        raise GraphError(
            "SharePoint productivity upload did not return web URLs for all expected files; "
            "Copilot retrieval cannot be verified safely."
        )
    folder_web_url = str(productivity_result.get("folder_web_url") or "")
    if not folder_web_url:
        raise GraphError("SharePoint productivity folder URL is required for scoped retrieval.")
    filter_expression = " OR ".join(
        f'Filename:"{filename}"' for filename in sorted(expected_modes)
    )
    response = graph.request(
        "POST",
        "/copilot/retrieval",
        json_body={
            "queryString": (
                "What is the PT-1042 screening operations plan for NCT99004324, including the "
                "renal blocker, owner, PI clarification, due dates, and referral status?"
            ),
            "dataSource": "sharePoint",
            "filterExpression": (
                f'Path:"{folder_web_url.rstrip("/")}" AND ({filter_expression})'
            ),
            "resourceMetadata": ["title"],
            "maximumNumberOfResults": 10,
        },
    ).json()
    matched_files: set[str] = set()
    extracts: list[str] = []
    for hit in response.get("retrievalHits", []):
        if not isinstance(hit, Mapping):
            continue
        filename = expected_urls.get(_normalize_web_url(str(hit.get("webUrl") or "")))
        if not filename:
            continue
        matched_files.add(filename)
        for extract in hit.get("extracts", []):
            if isinstance(extract, Mapping) and extract.get("text"):
                extracts.append(str(extract["text"]))
    combined = "\n".join(extracts).casefold()
    evidence_found = "pt-1042" in combined and "nct99004324" in combined
    semantic_expected = {
        filename for filename, mode in expected_modes.items() if mode == "semantic"
    }
    semantic_indexed = semantic_expected.issubset(matched_files) and evidence_found
    return {
        "workload": "copilot_retrieval",
        "data_source": "sharePoint",
        "status": "indexed" if semantic_indexed else "pending",
        "semantic_expected_files": sorted(semantic_expected),
        "lexical_expected_files": [PRODUCTIVITY_TRACKER_FILENAME],
        "matched_files": sorted(matched_files),
        "evidence_found": evidence_found,
        "folder_web_url": folder_web_url,
    }


def _normalize_web_url(value: str) -> str:
    return unquote(value).rstrip("/").casefold()


def _escape_odata(value: str) -> str:
    return value.replace("'", "''")


def resolve_user_address(graph: GraphClient) -> str:
    me = graph.get_json("/me", params={"$select": "userPrincipalName,mail,displayName"})
    address = me.get("mail") or me.get("userPrincipalName")
    if not address:
        raise GraphError("Could not resolve the signed-in user's mail or userPrincipalName from /me.")
    return address


def resolve_seed_reference_date(
    graph: GraphClient,
    unshifted_context: SeedContext,
    *,
    fallback_date: date | None = None,
) -> date:
    """Reuse the first schedule created for this seed version.

    SharePoint documents and Teams messages are immutable for a version, while calendar and To Do
    items are patchable. Pinning reruns to the existing CrCl event prevents daily date recalculation
    from making those immutable artifacts conflict with their original content.
    """
    existing = graph.get_json(
        "/me/events",
        params={
            "$filter": f"subject eq '{_escape_odata(CRCL_EVENT_SUBJECT)}'",
            "$select": "id,subject,start",
            "$top": 5,
        },
    ).get("value", [])
    match = select_existing_event(existing, CRCL_EVENT_SUBJECT)
    if not match:
        return fallback_date or date.today()

    start = match.get("start")
    start_value = start.get("dateTime") if isinstance(start, Mapping) else None
    if not isinstance(start_value, str):
        raise GraphError(
            f"Existing event '{CRCL_EVENT_SUBJECT}' is missing start.dateTime."
        )
    try:
        existing_crcl_date = date.fromisoformat(start_value[:10])
        source_crcl_date = date.fromisoformat(unshifted_context.crcl_due_date)
        source_dates = (
            date.fromisoformat(unshifted_context.pi_slot_date),
            source_crcl_date,
            date.fromisoformat(unshifted_context.pi_review_due_date),
        )
    except ValueError as exc:
        raise GraphError(f"Existing seed schedule contains an invalid date: {exc}") from exc

    shift = existing_crcl_date - source_crcl_date
    if shift.days < 0:
        raise GraphError(
            f"Existing event '{CRCL_EVENT_SUBJECT}' predates the source schedule."
        )
    return min(source_dates) + shift - timedelta(days=1)


def validate_workiq_answer(text: str, ctx: SeedContext) -> dict[str, bool]:
    """Require each scenario-critical fact group in the synthesized Work IQ answer."""
    normalized = " ".join(text.casefold().split())
    requirements: dict[str, tuple[str, ...]] = {
        "patient": (ctx.patient_id.casefold(),),
        "trial": (ctx.trial_id.casefold(),),
        "repeat_crcl_task": (ctx.task_id.casefold(),),
        "repeat_crcl_owner": (ctx.owner_name.casefold(), ctx.owner_id.casefold()),
        "repeat_crcl_due_date": (ctx.crcl_due_date.casefold(),),
        "renal_value": (f"{ctx.crcl_last_value} ml/min",),
        "renal_threshold": (f"{ctx.crcl_threshold} ml/min",),
        "renal_method": (ctx.crcl_method.casefold(),),
        "tumor_board": (ctx.tumor_board_id.casefold(), "tumor board"),
        "recommendation": ("likely eligible",),
        "pi_review_task": (ctx.pi_task_id.casefold(),),
        "pi_review_owner": (ctx.pi_name.casefold(), ctx.pi_owner_id.casefold()),
        "pi_review_due_date": (ctx.pi_review_due_date.casefold(),),
        "prior_platinum": ("prior-platinum", "prior platinum"),
        "referral_queue": (ctx.referral_queue_id.casefold(),),
        "referral_status": (ctx.referral_status.casefold(),),
        "pi_slot_date": (ctx.pi_slot_date.casefold(),),
    }
    checks = {
        name: any(candidate in normalized for candidate in alternatives)
        for name, alternatives in requirements.items()
    }
    missing = [name for name, passed in checks.items() if not passed]
    if missing:
        raise WorkIQVerificationError(
            "Work IQ answer is missing required seeded evidence: "
            + ", ".join(missing)
        )
    return checks


def verify_workiq_retrieval(access_token: str, ctx: SeedContext) -> dict[str, Any]:
    """Query the GA Work IQ A2A endpoint and validate the seeded proof-of-concept evidence."""
    api_root = Path(__file__).resolve().parents[3] / "services" / "api"
    api_root_text = str(api_root)
    if api_root_text not in sys.path:
        sys.path.insert(0, api_root_text)

    from app.workiq import WorkIQError, ask_with_access_token

    try:
        answer = ask_with_access_token(
            access_token=access_token,
            question=WORK_IQ_PROMPT,
            endpoint=WORK_IQ_ENDPOINT,
            timeout_seconds=120,
            timezone_offset_minutes=-240,
            timezone="America/New_York",
        )
    except WorkIQError as exc:
        raise WorkIQVerificationError(f"Work IQ A2A verification failed: {exc}") from exc

    checks = validate_workiq_answer(answer.text, ctx)
    return {
        "workload": "work_iq",
        "status": "verified",
        "task_id": answer.task_id,
        "context_id": answer.context_id,
        "duration_ms": answer.duration_ms,
        "attribution_count": len(answer.attributions),
        "checks": checks,
        "answer": answer.text,
    }


def run_seed(
    graph: GraphClient, ctx: SeedContext, data_dir: Path | None = None
) -> dict[str, Any]:
    """Run all M365 upserts and return a structured, non-secret result summary."""
    resolved_data_dir = data_dir or _default_data_dir()
    sharepoint = seed_sharepoint_transcripts(graph, ctx)
    productivity = seed_sharepoint_productivity(graph, ctx, resolved_data_dir)
    mail = seed_email(graph, ctx)
    events = seed_events(graph, ctx)
    todo = seed_todo(graph, ctx)
    teams_self_chat = seed_teams_self_chat(graph, ctx)
    try:
        copilot_retrieval = verify_copilot_sharepoint_retrieval(graph, sharepoint)
    except GraphPermissionError as exc:
        copilot_retrieval = {
            "workload": "copilot_retrieval",
            "data_source": "sharePoint",
            "status": "blocked",
            "error": str(exc),
            "folder_web_url": sharepoint.get("folder_web_url"),
        }
        copilot_productivity_retrieval = {
            "workload": "copilot_retrieval",
            "data_source": "sharePoint",
            "status": "blocked",
            "error": str(exc),
            "folder_web_url": productivity.get("folder_web_url"),
        }
    else:
        try:
            copilot_productivity_retrieval = verify_copilot_productivity_retrieval(
                graph, productivity
            )
        except GraphPermissionError as exc:
            copilot_productivity_retrieval = {
                "workload": "copilot_retrieval",
                "data_source": "sharePoint",
                "status": "blocked",
                "error": str(exc),
                "folder_web_url": productivity.get("folder_web_url"),
            }
    return {
        "status": "ok",
        "patient_id": ctx.patient_id,
        "trial_id": ctx.trial_id,
        "task_id": ctx.task_id,
        "mail": mail,
        "events": events,
        "todo": todo,
        "sharepoint": sharepoint,
        "sharepoint_productivity": productivity,
        "teams_self_chat": teams_self_chat,
        "copilot_retrieval": copilot_retrieval,
        "copilot_productivity_retrieval": copilot_productivity_retrieval,
    }


# --------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------
def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed synthetic Microsoft 365 Work IQ proof-of-concept content.")
    parser.add_argument("--tenant-id", default=os.environ.get("AMCIQ_SEED_TENANT_ID"), help="Entra tenant ID.")
    parser.add_argument("--client-id", default=os.environ.get("AMCIQ_SEED_CLIENT_ID"), help="Public-client app (application) ID.")
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("AMCIQ_SEED_DATA_DIR"),
        help="Path to the data/work synthetic artifacts. Defaults to <repo>/data/work.",
    )
    parser.add_argument(
        "--token-cache",
        default=os.environ.get("AMCIQ_SEED_TOKEN_CACHE"),
        help=(
            "Encrypted MSAL token-cache path. Defaults to "
            "%LOCALAPPDATA%/AMC IQ/msal/<client-id>.bin on Windows."
        ),
    )
    return parser.parse_args(argv)


def _default_data_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "work"


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.tenant_id or not args.client_id:
        print("ERROR: --tenant-id and --client-id are required (or set AMCIQ_SEED_TENANT_ID / AMCIQ_SEED_CLIENT_ID).", file=sys.stderr)
        return 2
    data_dir = Path(args.data_dir) if args.data_dir else _default_data_dir()
    token_cache = Path(args.token_cache) if args.token_cache else None

    try:
        token = acquire_token_device_code(
            args.tenant_id, args.client_id, cache_path=token_cache
        )
        graph = GraphClient(token)
        address = resolve_user_address(graph)
        unshifted_context = build_seed_context(
            data_dir, address, reference_date=date.min
        )
        reference_date = resolve_seed_reference_date(graph, unshifted_context)
        ctx = build_seed_context(data_dir, address, reference_date=reference_date)
        result = run_seed(graph, ctx, data_dir)
        work_iq_token = acquire_token_device_code(
            args.tenant_id,
            args.client_id,
            scopes=(WORK_IQ_SCOPE,),
            cache_path=token_cache,
        )
        result["work_iq"] = verify_workiq_retrieval(work_iq_token, ctx)
    except SeedError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
