from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

EligibilityAssessment = Literal[
    "eligible",
    "likely_eligible_pending",
    "not_eligible",
    "indeterminate",
]


class Source(str, Enum):
    FOUNDRY = "foundry"
    FABRIC = "fabric"
    WORK = "work"
    WEB = "web"


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    patientId: str | None = None
    trialId: str | None = None


class Owner(BaseModel):
    id: str
    display: str
    role: str


class PatientSnapshot(BaseModel):
    id: str
    mrn: str
    display: str
    age: int
    sex: str
    ecog: int
    diagnosis: str
    stage: str
    biomarkers: list[str]
    crcl: float | None
    crclDate: str | None


class Eligibility(BaseModel):
    assessment: EligibilityAssessment
    label: str
    confidence: Literal["low", "medium", "high"]


class TrialSummary(BaseModel):
    id: str
    short: str
    status: str
    humanName: str = ""
    title: str = ""
    phase: str = ""
    sponsor: str = ""
    keyIssue: str = ""
    latestProtocol: str = ""


class CriteriaItem(BaseModel):
    text: str
    status: Literal["met", "uncertain", "not_met"]
    evidenceRefs: list[str]


class Evidence(BaseModel):
    refId: str
    source: Source
    title: str
    snippet: str
    url: str | None
    sourceType: str


class NextAction(BaseModel):
    text: str
    steps: list[str] = Field(default_factory=list)
    owner: Owner
    taskType: str
    taskId: str = ""
    taskStatus: str = "Drafted (not submitted)"
    due: str = ""


class HumanReview(BaseModel):
    owner: Owner
    reason: str


class Reviewer(BaseModel):
    role: str
    owner: Owner
    status: Literal["assigned", "pending", "complete"]


SourceState = Literal["queued", "searching", "retrieved", "needs_review", "complete", "failed"]


class SourceMapItem(BaseModel):
    source: Source
    label: str
    status: SourceState
    queries: list[str]
    citations: list[str]
    durationMs: int
    retrieving: str = ""
    evidenceCount: int = 0
    evidenceNoun: str = "sources"


class TraceStep(BaseModel):
    step: str
    source: Source | None = None
    detail: str
    ts: int
    status: Literal["completed", "in_progress", "pending", "queued"] = "completed"


class AskResult(BaseModel):
    question: str
    answer: str
    answerRefs: list[str]
    intent: str = "eligibility"
    scope: str = ""
    bottomLine: str = ""
    patient: PatientSnapshot
    eligibility: Eligibility
    trial: TrialSummary
    criteria: list[CriteriaItem]
    evidence: list[Evidence]
    missingData: list[str]
    nextAction: NextAction
    humanReview: HumanReview
    reviewers: list[Reviewer] = Field(default_factory=list)
    unavailableSources: list[str] = Field(default_factory=list)
    sourceMap: list[SourceMapItem]
    trace: list[TraceStep]
    agentDriven: bool = False
    mode: Literal["mock", "live"]
    disclaimer: str


class PlanEventPayload(BaseModel):
    steps: list[str]


class SourceQueryEventPayload(BaseModel):
    source: Source
    label: str
    query: str
    retrieving: str = ""
    status: Literal["searching"] = "searching"


class SourceResultEventPayload(BaseModel):
    source: Source
    status: SourceState
    summary: str
    citations: list[str]
    durationMs: int
    retrieving: str = ""
    evidenceCount: int = 0
    evidenceNoun: str = "sources"


class TokenEventPayload(BaseModel):
    text: str


class AgentActivityEventPayload(BaseModel):
    phase: Literal["tool_start", "tool_done", "answer_delta"]
    source: Source | None = None
    query: str = ""
    text: str = ""


class FinalEventPayload(BaseModel):
    result: AskResult


class ErrorEventPayload(BaseModel):
    message: str
