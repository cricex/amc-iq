"""Synthesizer + Critic: reconcile specialist findings into one governed conclusion.

The Critic step is the safety spine: every specialist finding that maps to a ground-truth criterion
(the Fabric eligibility verdict) is cross-checked. Ground truth WINS on any disagreement, and the
disagreement is recorded as a conflict so it is visible and auditable, never silently dropped.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.base import Finding, SpecialistResult

# Worst-status-wins precedence for the overall conclusion.
_STATUS_RANK = {"not_met": 3, "uncertain": 2, "met": 1, "info": 0}


@dataclass
class Synthesis:
    overall_status: str
    findings: list[Finding] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    specialists_run: int = 0
    leads_pursued: int = 0
    failures: list[str] = field(default_factory=list)


def synthesize(
    results: list[SpecialistResult],
    ground_truth: dict[str, str] | None = None,
    leads_pursued: int = 0,
) -> Synthesis:
    """Reconcile findings across specialists. `ground_truth` maps a finding label to its authoritative
    status (the Fabric eligibility verdict); the Critic overrides any specialist that disagrees and
    logs the conflict."""
    ground_truth = ground_truth or {}
    findings: list[Finding] = []
    citations: list[str] = []
    conflicts: list[str] = []
    failures: list[str] = []
    by_key: dict[str, Finding] = {}

    for result in results:
        if result.status == "failed":
            failures.append(f"{result.specialist.value}: {result.error or 'unavailable'}")
            continue
        for citation in result.citations:
            if citation not in citations:
                citations.append(citation)
        for finding in result.findings:
            status = finding.status
            # Critic: cross-check against ground truth keyed by criterion id (preferred) or label, so a
            # live agent that drifts on a clinical criterion is caught even with a free-text label.
            truth = None
            if finding.criterion_id and finding.criterion_id in ground_truth:
                truth = ground_truth[finding.criterion_id]
            elif finding.label in ground_truth:
                truth = ground_truth[finding.label]
            if truth is not None and truth != status:
                conflicts.append(
                    f"{result.specialist.value} reported '{finding.label}' as {status}; "
                    f"ground truth is {truth} (ground truth applied)."
                )
                status = truth
            key = finding.criterion_id or finding.label
            existing = by_key.get(key)
            if existing is not None:
                if _STATUS_RANK.get(status, 0) > _STATUS_RANK.get(existing.status, 0):
                    existing.status = status
                for c in finding.citations:
                    if c not in existing.citations:
                        existing.citations.append(c)
                continue
            merged = Finding(
                label=finding.label,
                detail=finding.detail,
                status=status,
                citations=list(finding.citations),
                criterion_id=finding.criterion_id,
                category=finding.category,
            )
            by_key[key] = merged
            findings.append(merged)

    overall = "info"
    for finding in findings:
        if _STATUS_RANK.get(finding.status, 0) > _STATUS_RANK.get(overall, 0):
            overall = finding.status

    return Synthesis(
        overall_status=overall,
        findings=findings,
        citations=citations,
        conflicts=conflicts,
        specialists_run=sum(1 for r in results if r.status != "failed"),
        leads_pursued=leads_pursued,
        failures=failures,
    )
