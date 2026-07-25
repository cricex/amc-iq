from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Iterable
from concurrent.futures import as_completed
from typing import Any

from app.config import Settings, get_settings
from app.eligibility import CriterionResult
from app.registry import find_by_id, load_registry, maybe_find_by_id
from app.schemas import (
    AgentActivityEventPayload,
    AskRequest,
    AskResult,
    CriteriaItem,
    Eligibility,
    Evidence,
    FinalEventPayload,
    HumanReview,
    EligibilityAssessment,
    NextAction,
    Owner,
    PatientSnapshot,
    PlanEventPayload,
    Reviewer,
    Source,
    SourceQueryEventPayload,
    SourceResultEventPayload,
    TokenEventPayload,
    TraceStep,
    TrialSummary,
)
from app.sources import IQSource, QueryContext, SourceResult, create_mock_fallbacks, create_sources
from app.agent_client import AgentRun, run_agent, stream_agent

RETRIEVING: dict[Source, str] = {
    Source.FOUNDRY: "protocol criteria, genomics report, pathology, consent policy",
    Source.FABRIC: "patient registry, CrCl trend, ECOG, treatment history",
    Source.WORK: "open tasks, coordinator ownership, tumor board context",
    Source.WEB: "external trial registry and biomarker treatment context",
}
EVIDENCE_NOUN: dict[Source, str] = {
    Source.FOUNDRY: "citations",
    Source.FABRIC: "citations",
    Source.WORK: "citations",
    Source.WEB: "citations",
}

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, settings: Settings | None = None, sources: list[IQSource] | None = None) -> None:
        self.settings = settings or get_settings()
        self.registry = load_registry(str(self.settings.registry_path))
        self.sources = sources or create_sources(self.settings)
        self._fallbacks = create_mock_fallbacks(self.settings)

    @staticmethod
    def plan_steps() -> list[str]:
        return [
            "Checking diagnosis & stage",
            "Reading protocol eligibility criteria",
            "Pulling labs and treatment history",
            "Reviewing care-team workflow",
            "Checking external trial registry",
            "Applying deterministic eligibility rules",
        ]

    def _run_source(self, source: IQSource, context: QueryContext) -> SourceResult:
        """Query one IQ layer, decorating the result with UI metadata. On failure, fall back to the
        deterministic mock for that layer and mark the result `failed` so the assessment still
        composes while the source map surfaces the outage."""
        try:
            result = source.query(context)
            result.status = "complete"
        except Exception as exc:  # noqa: BLE001 - resilience: one failed layer must not break the rest
            try:
                result = self._fallbacks[source.name].query(context)
                result.status = "failed"
                result.summary = f"{source.label} retrieval failed; showing fallback context. ({exc})"
            except Exception as fallback_exc:  # noqa: BLE001 - hard stop fallback still must not break composition
                result = SourceResult(
                    source=source.name,
                    label=source.label,
                    queries=[],
                    summary=f"{source.label} retrieval failed and fallback is unavailable. ({exc}; {fallback_exc})",
                    citations=[],
                    facts={},
                    duration_ms=0,
                    status="failed",
                )
        result.retrieving = RETRIEVING.get(source.name, "")
        result.evidence_noun = EVIDENCE_NOUN.get(source.name, "sources")
        result.evidence_count = self._evidence_count(source.name, result)
        return result

    @staticmethod
    def _evidence_count(source: Source, result: SourceResult) -> int:
        # Count = citations actually surfaced for this layer, so the IQ activity bar matches the
        # evidence packet (a layer that shows 2 citations reads "2 citations", not a larger scan count).
        return len(result.citations)

    def answer(self, request: AskRequest, user_access_token: str | None = None) -> AskResult:
        context = self._context_for(request, user_access_token)
        from concurrent.futures import ThreadPoolExecutor

        agent_run: AgentRun | None = None
        if self.settings.USE_LIVE_AGENT:
            # Run the hosted agent concurrently with the grounded adapters so total latency is the
            # slower of the two rather than the sum.
            with ThreadPoolExecutor(max_workers=1) as agent_executor:
                future = agent_executor.submit(run_agent, self.settings, request.question)
                results, criterion_results = self._query_all_and_eligibility(context)
                agent_run = future.result()
        else:
            results, criterion_results = self._query_all_and_eligibility(context)
        return self._assemble(
            request.question,
            context,
            results,
            agent_run,
            criterion_results=criterion_results,
        )

    def _query_all(self, context: QueryContext) -> list[SourceResult]:
        """Run all IQ layers concurrently (they are independent context providers)."""
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max(1, len(self.sources))) as executor:
            return list(executor.map(lambda source: self._run_source(source, context), self.sources))

    def _query_all_and_eligibility(
        self, context: QueryContext
    ) -> tuple[list[SourceResult], list[CriterionResult]]:
        """Start eligibility as soon as Fabric retrieval completes, overlapping it with slower IQ
        layers such as live Work IQ without competing with the initial Fabric query."""
        from concurrent.futures import Future, ThreadPoolExecutor

        results: list[SourceResult | None] = [None] * len(self.sources)
        eligibility_future: Future[list[CriterionResult]] | None = None
        with ThreadPoolExecutor(max_workers=max(1, len(self.sources) + 1)) as executor:
            futures = {
                executor.submit(self._run_source, source, context): index
                for index, source in enumerate(self.sources)
            }
            for future in as_completed(futures):
                index = futures[future]
                result = future.result()
                results[index] = result
                if result.source == Source.FABRIC and eligibility_future is None:
                    eligibility_future = executor.submit(self._eligibility_results, context)

            if eligibility_future is None:
                eligibility_future = executor.submit(self._eligibility_results, context)
            criterion_results = eligibility_future.result()

        return [result for result in results if result is not None], criterion_results

    async def stream(
        self, request: AskRequest, user_access_token: str | None = None
    ) -> AsyncIterator[tuple[str, str]]:
        context = self._context_for(request, user_access_token)
        yield "plan", PlanEventPayload(steps=self.plan_steps()).model_dump_json()

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        # Grounded adapters: each pushes its result onto the shared queue when done.
        async def _run_adapter(source: IQSource) -> None:
            result = await asyncio.to_thread(self._run_source, source, context)
            await queue.put(("adapter", result))

        adapter_tasks = [asyncio.ensure_future(_run_adapter(source)) for source in self.sources]
        eligibility_future: asyncio.Task[list[CriterionResult]] | None = None

        agent_holder: dict[str, AgentRun | None] = {"run": None}
        agent_enabled = self.settings.USE_LIVE_AGENT

        # The grounded adapters stream in real time (searching -> complete with a genuine window), so
        # they drive the live IQ activity in both the bar and the reasoning ticker. The hosted agent's
        # tool calls are reported only on completion (no real-time progress), so the agent contributes
        # the drafting answer, not the live retrieval status.
        for source in self.sources:
            yield "source_query", SourceQueryEventPayload(
                source=source.name,
                label=source.label,
                query=self._primary_query_for(source, context),
                retrieving=RETRIEVING.get(source.name, ""),
            ).model_dump_json()

        def _agent_worker() -> None:
            try:
                for event in stream_agent(self.settings, request.question):
                    if event.get("phase") == "done":
                        run = event.get("run")
                        agent_holder["run"] = run if isinstance(run, AgentRun) else None
                    else:
                        loop.call_soon_threadsafe(queue.put_nowait, ("agent", event))
            except Exception:
                agent_holder["run"] = None
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, ("agent_end", None))

        agent_future = asyncio.ensure_future(asyncio.to_thread(_agent_worker)) if agent_enabled else None

        results: list[SourceResult] = []
        adapters_done = 0
        agent_done = not agent_enabled
        while adapters_done < len(self.sources) or not agent_done:
            kind, payload = await queue.get()
            if kind == "adapter":
                result = payload
                results.append(result)
                adapters_done += 1
                if result.source == Source.FABRIC and eligibility_future is None:
                    eligibility_future = asyncio.ensure_future(
                        asyncio.to_thread(self._eligibility_results, context)
                    )
                yield "source_result", SourceResultEventPayload(
                    source=result.source,
                    status=result.status,  # type: ignore[arg-type]
                    summary=result.summary,
                    citations=[citation.refId for citation in result.citations],
                    durationMs=result.duration_ms,
                    retrieving=result.retrieving,
                    evidenceCount=result.evidence_count if result.evidence_count is not None else len(result.citations),
                    evidenceNoun=result.evidence_noun,
                ).model_dump_json()
            elif kind == "agent":
                event = payload
                yield "agent_activity", AgentActivityEventPayload(
                    phase=event["phase"],
                    source=event.get("source"),
                    query=str(event.get("query", "")),
                    text=str(event.get("text", "")),
                ).model_dump_json()
            elif kind == "agent_end":
                agent_done = True

        if agent_future is not None:
            await agent_future
        agent_run = agent_holder["run"]

        # Restore canonical source order so the final sourceMap matches the non-streaming /api/ask path.
        source_order = [source.name for source in self.sources]
        results.sort(key=lambda result: source_order.index(result.source))

        if eligibility_future is None:
            eligibility_future = asyncio.ensure_future(
                asyncio.to_thread(self._eligibility_results, context)
            )
        criterion_results = await eligibility_future

        final = self._assemble(
            request.question,
            context,
            results,
            agent_run,
            criterion_results=criterion_results,
        )
        yield "token", TokenEventPayload(text=final.answer).model_dump_json()
        yield "final", FinalEventPayload(result=final).model_dump_json()

    def _context_for(
        self, request: AskRequest, user_access_token: str | None = None
    ) -> QueryContext:
        """Resolve the patient and trial the question is actually about, from the question text (by
        name or ID) or explicit case context supplied by the portal. Subjects named in the question
        take precedence, so editing the prompt can intentionally switch away from the current case."""
        question = request.question
        patient_id = self._patient_id_from_question(question)
        if patient_id is None:
            named = re.search(r"PT-\d+", question, re.IGNORECASE)
            if named:
                raise ValueError(f"Unknown patient: {named.group(0).upper()}")
            patient_id = request.patientId
        if patient_id is None:
            raise ValueError(
                "Name a patient in the question or select a case before asking."
            )
        if maybe_find_by_id(self.registry["patients"], patient_id) is None:
            raise ValueError(f"Unknown patient: {patient_id}")

        trial_id = self._trial_id_from_question(question)
        if trial_id is None:
            named = re.search(r"NCT\d{4,}", question, re.IGNORECASE)
            if named:
                raise ValueError(f"Unknown trial: {named.group(0).upper()}")
            trial_id = request.trialId
        if trial_id is None:
            raise ValueError(
                "Name a trial in the question or select a case before asking."
            )
        if maybe_find_by_id(self.registry["trials"], trial_id) is None:
            raise ValueError(f"Unknown trial: {trial_id}")
        return QueryContext(
            question=question,
            patient_id=patient_id,
            trial_id=trial_id,
            registry=self.registry,
            user_access_token=user_access_token,
        )

    def _patient_id_from_question(self, question: str) -> str | None:
        """Identify the patient the question is about, by patient ID (PT-1061) or full display name
        (Marlowe Price), case-insensitively. Returns None when no known patient is referenced."""
        q = question.lower()
        for match in re.findall(r"pt-\d+", q):
            pid = match.upper()
            if maybe_find_by_id(self.registry["patients"], pid) is not None:
                return pid
        for patient in self.registry["patients"]:
            name = str(patient.get("display", "")).strip().lower()
            if name and name in q:
                return str(patient["id"])
        return None

    def _trial_id_from_question(self, question: str) -> str | None:
        """Identify the trial the question is about, by trial ID (NCT99004324) or short title
        (EGFR exon 20 NSCLC trial), case-insensitively. Returns None when no known trial is referenced."""
        q = question.lower()
        for match in re.findall(r"nct\d{4,}", q):
            tid = match.upper()
            if maybe_find_by_id(self.registry["trials"], tid) is not None:
                return tid
        for trial in self.registry["trials"]:
            short = str(trial.get("short", "")).strip().lower()
            if short and short in q:
                return str(trial["id"])
        return None

    def _eligibility_results(self, context: QueryContext) -> list[CriterionResult]:
        """Evaluate through the strict eligibility boundary.

        Explicit local mock mode uses deterministic synthetic verdicts. Every other environment
        remains Fabric-native, and a Fabric failure raises rather than falling back."""
        from app.eligibility import evaluate_eligibility

        return evaluate_eligibility(self.settings, context.patient_id, context.trial_id)

    def _assemble(
        self,
        question: str,
        context: QueryContext,
        results: list[SourceResult],
        agent_run: AgentRun | None = None,
        *,
        criterion_results: list[CriterionResult] | None = None,
    ) -> AskResult:
        by_source = {result.source: result for result in results}
        fabric = by_source[Source.FABRIC].facts
        work = by_source[Source.WORK].facts
        web = by_source[Source.WEB].facts

        registry_patient = find_by_id(self.registry["patients"], context.patient_id)
        registry_trial = find_by_id(self.registry["trials"], context.trial_id)
        # Patient/trial metadata come from the selected Fabric IQ adapter's facts (with the registry
        # as the stable fallback for display fields), not from the eligibility evaluator.
        patient_row = dict(fabric.get("patient") or {})
        trial_row = dict(fabric.get("trial") or {})

        latest_crcl = fabric.get("latest_crcl")
        prior_crcl = fabric.get("prior_crcl")

        patient_ecog: int
        try:
            patient_ecog = int(float(patient_row.get("ecog_ps", registry_patient.get("ecog", 0))))
        except (TypeError, ValueError):
            patient_ecog = 0
        crcl_value: float | None
        try:
            raw_crcl_value = (latest_crcl or {}).get("value")
            crcl_value = float(raw_crcl_value) if raw_crcl_value is not None else None
        except (TypeError, ValueError):
            crcl_value = None
        crcl_min: int | None
        try:
            raw_min = trial_row.get("crcl_min")
            crcl_min = int(float(raw_min)) if raw_min not in (None, "") else None
        except (TypeError, ValueError):
            crcl_min = None

        intent = classify_intent(question)
        criterion_results = (
            criterion_results
            if criterion_results is not None
            else self._eligibility_results(context)
        )
        all_evidence = self._dedupe_evidence(
            citation for result in results for citation in result.citations
        )
        selected_refs = self._evidence_refs_for_intent(intent, criterion_results)
        evidence = [
            item
            for item in all_evidence
            if item.refId in selected_refs
            or ("r4" in selected_refs and item.refId.startswith("r4-"))
        ]
        evidence_ids = {item.refId for item in evidence}
        source_map = []
        for result in results:
            item = result.to_source_map_item()
            item.citations = [
                citation.refId for citation in result.citations if citation.refId in evidence_ids
            ]
            item.evidenceCount = len(item.citations)
            source_map.append(item)

        patient_biomarkers = [str(item) for item in registry_patient.get("biomarkers", [])]

        criteria = [
            CriteriaItem(
                text=result.description,
                status=result.status,  # type: ignore[arg-type]
                evidenceRefs=self._criterion_evidence_refs(result.category, evidence_ids),
            )
            for result in criterion_results
        ]
        uncertain_results = [result for result in criterion_results if result.status == "uncertain"]
        not_met_results = [result for result in criterion_results if result.status == "not_met"]
        missing_data = self._missing_data_from_uncertain(uncertain_results)

        assessment = self._assessment(criteria)
        next_owner_id = str(work.get("next_action_owner_id") or registry_patient.get("coordinator") or patient_row.get("coordinator_id") or "COORD-01")
        coordinator = self._owner(next_owner_id)
        pi = self._owner(str(registry_patient.get("treating_oncologist") or patient_row.get("treating_oncologist_id") or "PI-01"))
        task_id = str((work.get("crcl_task") or {}).get("task_id") or f"TASK-{context.patient_id}-ELIGIBILITY")
        next_action = self._next_action_for_assessment(
            assessment=assessment,
            uncertain_results=uncertain_results,
            not_met_results=not_met_results,
            owner=coordinator,
            task_id=task_id,
        )
        reviewers = [
            Reviewer(role="Trial Coordinator", owner=coordinator, status="assigned"),
            Reviewer(role="PI Sign-Off", owner=pi, status="pending"),
        ]
        unavailable_sources = [result.label for result in results if result.status == "failed"]
        patient_mrn = str(patient_row.get("mrn") or registry_patient.get("mrn") or context.patient_id)
        patient_display = str(patient_row.get("display_name") or registry_patient.get("display") or context.patient_id)
        patient_age: int
        try:
            patient_age = int(float(patient_row.get("age", registry_patient.get("age", 0))))
        except (TypeError, ValueError):
            patient_age = 0
        crcl_date = (latest_crcl or {}).get("date")
        patient = PatientSnapshot(
            id=context.patient_id,
            mrn=patient_mrn,
            display=patient_display,
            age=patient_age,
            sex=str(patient_row.get("sex") or registry_patient.get("sex") or ""),
            ecog=patient_ecog,
            diagnosis=self._display_diagnosis(str(patient_row.get("primary_diagnosis") or registry_patient.get("diagnosis") or "")),
            stage=str(patient_row.get("stage") or registry_patient.get("stage") or ""),
            biomarkers=patient_biomarkers,
            crcl=crcl_value,
            crclDate=str(crcl_date) if crcl_date else None,
        )
        trial_short = str(registry_trial.get("short") or trial_row.get("short_title") or context.trial_id)
        trial_title = str(registry_trial.get("title") or "")
        trial = TrialSummary(
            id=context.trial_id,
            short=trial_short,
            status=str(web.get("registry_status") or registry_trial.get("status") or trial_row.get("status") or ""),
            humanName=_humanize_trial(trial_short),
            title=trial_title,
            phase=_phase_from_title(trial_title),
            sponsor="AMC Cancer Center",
            keyIssue=self._key_issue(registry_trial),
            latestProtocol=str(trial_row.get("latest_protocol") or registry_trial.get("latest_protocol") or ""),
        )
        answer_refs = [item.refId for item in evidence]
        composed_answer = self._compose_answer(
            intent=intent,
            patient=patient,
            trial=trial,
            assessment=assessment,
            criterion_results=criterion_results,
            uncertain_results=uncertain_results,
            not_met_results=not_met_results,
            missing_data=missing_data,
            required_biomarker=str(trial_row.get("biomarker_required") or ""),
            next_action=next_action,
            crcl_min=crcl_min,
            web=web,
        )
        # When the hosted agent answered, use its question-specific narrative and its real tool-call
        # sequence for the assessment steps, so different questions produce different answers and steps.
        agent_driven = bool(agent_run and agent_run.answer)
        answer = agent_run.answer if agent_run is not None and agent_driven else composed_answer
        if agent_run and agent_run.tool_calls:
            trace = self._agent_trace(agent_run)
        else:
            trace = self._trace(by_source, criterion_results, latest_crcl)
        # Only override the trace with the specialist team when the deterministic path produced it;
        # when the live hosted agent answered, keep its real tool-call trace (do not replace it with
        # the grounded team's steps).
        if self.settings.USE_MULTI_AGENT and not agent_driven:
            multi_trace = self._multi_agent_trace(question, context, intent)
            if multi_trace:
                trace = multi_trace
        result = AskResult(
            question=question,
            answer=answer,
            answerRefs=answer_refs,
            intent=intent,
            scope=(
                "External public context only. Not used by itself to determine AMC trial eligibility."
                if intent == "external_context"
                else ""
            ),
            bottomLine=_bottom_line(intent, assessment),
            patient=patient,
            eligibility=Eligibility(
                assessment=assessment,
                label=self._eligibility_label(assessment, missing_data),
                confidence="medium" if assessment == "likely_eligible_pending" else "high" if assessment == "eligible" else "low",
            ),
            trial=trial,
            criteria=criteria,
            evidence=evidence,
            missingData=missing_data,
            nextAction=next_action,
            humanReview=HumanReview(owner=pi, reason=self._human_review_reason(assessment, uncertain_results, not_met_results)),
            reviewers=reviewers,
            unavailableSources=unavailable_sources,
            sourceMap=source_map,
            trace=trace,
            agentDriven=agent_driven,
            mode=self.settings.mode,
            disclaimer=str(self.registry["meta"]["disclaimer"]),
        )
        return result

    def _criterion_evidence_refs(self, category: str, evidence_ids: set[str]) -> list[str]:
        return self._refs(self._criterion_ref_candidates(category), evidence_ids)

    @staticmethod
    def _missing_data_line(result: CriterionResult) -> str:
        detail = f"{result.description} {result.evidence}".lower()
        if result.category == "renal":
            if "no crcl" in detail or "baseline draw" in detail:
                return "Baseline CrCl_CKD-EPI is needed to assess renal function."
            return "Repeat CrCl is needed to confirm renal function at the protocol threshold."
        if result.category == "prior_therapy":
            if "pi confirmation" in detail:
                return "PI confirmation on prior-therapy exclusion interpretation."
            return "Clarify prior therapy details against protocol exclusions."
        if result.category == "biomarker":
            if "molecular report" in detail or "ngs" in detail:
                return "A molecular report is needed to confirm required biomarker status."
            return "Confirm required biomarker status from molecular documentation."
        if result.category == "performance":
            return "Updated ECOG performance status documentation is needed."
        if result.category == "diagnosis":
            return "Diagnosis and histology confirmation is needed for protocol matching."
        return f"{result.description.rstrip('.')}: {result.evidence.rstrip('.')}"

    def _missing_data_from_uncertain(self, uncertain_results: list[CriterionResult]) -> list[str]:
        missing_data: list[str] = []
        seen: set[str] = set()
        for result in uncertain_results:
            line = self._missing_data_line(result)
            if line not in seen:
                missing_data.append(line)
                seen.add(line)
        return missing_data

    @staticmethod
    def _criterion_action_step(result: CriterionResult) -> str:
        if result.category == "renal":
            return "Order repeat CrCl to confirm renal function."
        if result.category == "prior_therapy":
            return "Request PI confirmation on prior-therapy exclusion interpretation."
        if result.category == "biomarker":
            return "Obtain or upload molecular profiling for the required biomarker."
        if result.category == "performance":
            return "Document an updated ECOG performance status assessment."
        if result.category == "diagnosis":
            return "Confirm diagnosis and histology against protocol criteria."
        return f"Resolve uncertain criterion: {result.description.rstrip('.')}"

    def _next_action_for_assessment(
        self,
        assessment: str,
        uncertain_results: list[CriterionResult],
        not_met_results: list[CriterionResult],
        owner: Owner,
        task_id: str,
    ) -> NextAction:
        if assessment == "not_eligible":
            blocking = [f"Review blocking criterion: {item.description.rstrip('.')}" for item in not_met_results[:2]]
            steps = [
                "Do not advance to formal screening until PI review is complete.",
                *blocking,
                "Review alternative candidate trials for this patient.",
            ]
            text = "Hold formal screening, review blocking criteria, and route for PI-directed alternatives."
            task_type = "review_blockers"
            due = "Within 24 hours"
        elif assessment == "likely_eligible_pending":
            criteria_steps = [self._criterion_action_step(item) for item in uncertain_results]
            if not criteria_steps:
                criteria_steps = ["Resolve remaining uncertain eligibility criteria."]
            steps = [*criteria_steps, "Route the updated packet to the trial coordinator and PI for screening decision."]
            primary = " and ".join(step.rstrip(".") for step in criteria_steps[:2])
            text = f"{primary}, then route the updated packet to the trial coordinator for PI review."
            task_type = "route_to_coordinator"
            due = "Within 24 hours"
        else:
            steps = [
                "Route the eligibility packet to the trial coordinator.",
                "Proceed with consent and screening scheduling per protocol.",
                "Send the final packet to PI for sign-off before study procedures.",
            ]
            text = "Proceed with coordinator-led screening workflow and PI sign-off."
            task_type = "advance_screening"
            due = "Per screening calendar"

        return NextAction(
            text=text,
            steps=steps,
            owner=owner,
            taskType=task_type,
            taskId=task_id,
            taskStatus="Drafted (not submitted)",
            due=due,
        )

    @staticmethod
    def _criteria_summary(results: list[CriterionResult], max_items: int = 2) -> str:
        if not results:
            return ""
        snippets = [item.evidence.rstrip(".") for item in results[:max_items]]
        return "; ".join(snippets)

    def _compose_answer(
        self,
        intent: str,
        patient: PatientSnapshot,
        trial: TrialSummary,
        assessment: str,
        criterion_results: list[CriterionResult],
        uncertain_results: list[CriterionResult],
        not_met_results: list[CriterionResult],
        missing_data: list[str],
        required_biomarker: str,
        next_action: NextAction,
        crcl_min: int | None,
        web: dict[str, Any],
    ) -> str:
        """Compose a deterministic answer that directly addresses the QUESTION's intent.

        The answer leads with what the question asked (missing data, workflow ownership, evidence,
        protocol interpretation, external context, or blockers), not always the eligibility verdict.
        The eligibility label lives in the assessment panel; this text answers the specific question.
        """
        case = f"{patient.display} ({trial.id})"

        if intent == "data_gaps":
            if missing_data:
                items = "; ".join(item.rstrip(".") for item in missing_data)
                return (
                    f"Outstanding data for {case}: {items}. "
                    f"These items should be resolved and refreshed before the case advances to formal screening."
                )
            return (
                f"No outstanding data gaps for {case}: the recorded facts (labs, biomarker, ECOG, and "
                f"prior therapy) are complete for the current trial criteria. Confirm recency before screening."
            )

        if intent == "workflow":
            steps = "; ".join(step.rstrip(".") for step in next_action.steps[:3]) if next_action.steps else next_action.text
            return (
                f"Owner for {case}: {next_action.owner.display} should act next. "
                f"Task {next_action.taskId} is {next_action.taskStatus.lower()}, due {next_action.due.lower()}. "
                f"Steps: {steps}."
            )

        if intent == "evidence":
            met = sum(1 for r in criterion_results if r.status == "met")
            unc = len(uncertain_results)
            bad = len(not_met_results)
            open_items = self._criteria_summary(not_met_results + uncertain_results, max_items=3) or "none outstanding"
            return (
                f"Evidence packet for {case}: {met} criteria met, {unc} uncertain, {bad} not met. "
                f"Unresolved for PI review: {open_items}. "
                f"The packet consolidates patient facts, protocol criteria, and open issues for sign-off."
            )

        if intent == "external_context":
            marker = required_biomarker or "the target biomarker"
            web_summary = str(web.get("summary") or "").strip()
            lead = web_summary or (
                f"External trial-registry and treatment-landscape context for {marker} is available as background."
            )
            return (
                f"External context for {case}: {lead.rstrip('.')}. "
                f"This is public context only; it informs but does not by itself determine AMC eligibility, "
                f"which is governed by the institutional criteria and PI review."
            )

        if intent == "protocol":
            protocol_results = [r for r in (not_met_results + uncertain_results) if r.category in {"prior_therapy", "consent", "diagnosis"}]
            detail = self._criteria_summary(protocol_results, max_items=3)
            if not detail:
                return (
                    f"Protocol interpretation for {case}: no prior-therapy or protocol exclusion is triggered by "
                    f"the recorded history. Defer the final reading to PI review."
                )
            return (
                f"Protocol interpretation for {case}: {detail}. "
                f"This supports PI review and is not a final eligibility determination."
            )

        if intent == "screening":
            blockers = self._criteria_summary(not_met_results + uncertain_results, max_items=3)
            if not blockers:
                return (
                    f"Nothing structural is blocking {case} from screening on the recorded facts; proceed per the "
                    f"coordinator workflow with PI sign-off. Next action: {next_action.text}"
                )
            return (
                f"Blocking {case} from formal screening: {blockers}. "
                f"Next action: {next_action.text}"
            )

        # Default: eligibility / match narrative.
        support_items = [patient.diagnosis]
        if required_biomarker:
            support_items.append(required_biomarker)
        support_items.append(f"ECOG {patient.ecog}")
        if crcl_min is not None:
            support_items.append(f"trial CrCl threshold {crcl_min} mL/min")
        support = ", ".join(item for item in support_items if item)

        if assessment == "not_eligible":
            blocking = self._criteria_summary(not_met_results, max_items=3) or "one or more criteria are not met."
            return (
                f"{patient.display} is not currently eligible for {trial.id}. "
                f"Blocking criteria: {blocking}. "
                f"Next action: {next_action.text}"
            )
        if assessment == "likely_eligible_pending":
            open_items = self._criteria_summary(uncertain_results, max_items=3) or "additional verification is required."
            return (
                f"{patient.display} is likely eligible for {trial.id} pending resolution of open criteria. "
                f"The match is supported by {support}. "
                f"Open items: {open_items}. "
                f"Next action: {next_action.text}"
            )

        met_items = self._criteria_summary([item for item in criterion_results if item.status == "met"], max_items=3)
        if met_items:
            met_items = f" Key criteria met: {met_items}."
        return (
            f"{patient.display} appears eligible for {trial.id}. "
            f"Supporting facts include {support}.{met_items} "
            f"Next action: {next_action.text}"
        )

    @staticmethod
    def _human_review_reason(
        assessment: str,
        uncertain_results: list[CriterionResult],
        not_met_results: list[CriterionResult],
    ) -> str:
        if assessment == "not_eligible":
            if not_met_results:
                lead = not_met_results[0].description.rstrip(".")
                return f"Review blocking criterion and alternatives: {lead}."
            return "Review blocking criteria and alternatives before any screening decision."
        if uncertain_results:
            lead = uncertain_results[0].description.rstrip(".")
            return f"Confirm unresolved criterion before screening: {lead}."
        return "Final PI sign-off before formal screening."

    def _multi_agent_trace(self, question: str, context: QueryContext, intent: str) -> list[TraceStep]:
        """Run the grounded specialist team and render its investigation (including where it deepened)
        as the Assessment Steps trace. Returns [] on any failure so the caller keeps the default trace."""
        try:
            from app.agents.deepening import Budget
            from app.agents.trace import team_to_trace

            budget = Budget(max_depth=self.settings.AGENT_MAX_DEPTH, max_leads=self.settings.AGENT_MAX_LEADS)
            if self.settings.USE_LIVE_SPECIALISTS:
                from app.agents.live import run_live_team

                team = run_live_team(
                    self.settings, context.patient_id, context.trial_id, question, intent, budget
                )
            else:
                from app.agents.grounded import run_grounded_team

                team = run_grounded_team(
                    context.patient_id, context.trial_id, question, intent, budget
                )
            return team_to_trace(team)
        except Exception:
            logger.exception("Multi-agent trace failed; falling back to the default trace.")
            return []

    @staticmethod
    def _primary_query_for(source: IQSource, context: QueryContext) -> str:
        question = " ".join(context.question.split())
        if len(question) > 180:
            question = f"{question[:177]}..."
        return f"{source.label}: {question}"

    @staticmethod
    def _crcl_status(value: float, minimum: int, prior_crcl: dict[str, Any] | None) -> str:
        if value >= minimum:
            return "met"
        prior_value = float(prior_crcl["value"]) if prior_crcl else None
        if minimum - value <= 5 or (prior_value is not None and prior_value >= minimum):
            return "uncertain"
        return "not_met"

    @staticmethod
    def _assessment(criteria: list[CriteriaItem]) -> EligibilityAssessment:
        statuses = {item.status for item in criteria}
        if "not_met" in statuses:
            return "not_eligible"
        if "uncertain" in statuses:
            return "likely_eligible_pending"
        return "eligible"

    @staticmethod
    def _eligibility_label(assessment: str, missing_data: list[str]) -> str:
        if assessment == "likely_eligible_pending":
            if any("CrCl" in item for item in missing_data) and any("PI" in item for item in missing_data):
                return "Likely eligible, pending repeat CrCl and PI confirmation"
            return "Likely eligible, pending missing data"
        if assessment == "eligible":
            return "Eligible"
        if assessment == "not_eligible":
            return "Not eligible"
        return "Indeterminate"

    @staticmethod
    def _display_diagnosis(raw: str) -> str:
        if "nsclc" in raw.lower() and "adenocarcinoma" in raw.lower():
            return "Metastatic NSCLC (adenocarcinoma)"
        return raw

    @staticmethod
    def _dedupe_evidence(items: Iterable[Evidence]) -> list[Evidence]:
        seen: set[str] = set()
        evidence: list[Evidence] = []
        for item in items:
            if item.refId not in seen:
                evidence.append(item)
                seen.add(item.refId)
        return evidence

    @classmethod
    def _evidence_refs_for_intent(
        cls, intent: str, criterion_results: list[CriterionResult]
    ) -> set[str]:
        """Select evidence that supports the question and any criteria the UI will surface."""
        if intent == "evidence":
            return {"r1", "r2", "r3", "r4", "r7"}
        if intent == "workflow":
            return {"r4"}
        if intent == "protocol":
            return {"r2", "r4"}
        if intent == "external_context":
            return {"r7"}
        if intent == "screening":
            unresolved = [
                result for result in criterion_results if result.status != "met"
            ]
            refs = {"r4"}
            for result in unresolved:
                refs.update(cls._criterion_ref_candidates(result.category))
            return refs if len(refs) > 1 else {"r3", "r4"}
        if intent == "data_gaps":
            refs = {"r4"}
            for result in criterion_results:
                if result.status != "uncertain":
                    continue
                if result.category == "prior_therapy":
                    continue
                refs.update(cls._criterion_ref_candidates(result.category))
            return refs if len(refs) > 1 else {"r3", "r4"}
        return {"r1", "r2", "r3", "r4"}

    @staticmethod
    def _criterion_ref_candidates(category: str) -> tuple[str, ...]:
        mapping: dict[str, tuple[str, ...]] = {
            "diagnosis": ("r1", "r3", "r7"),
            "biomarker": ("r1", "r3", "r7"),
            "performance": ("r3",),
            "renal": ("r3",),
            "prior_therapy": ("r2", "r4"),
        }
        return mapping.get(category, ("r3",))

    @staticmethod
    def _refs(candidates: tuple[str, ...], evidence_ids: set[str]) -> list[str]:
        return [ref for ref in candidates if ref in evidence_ids]

    def _owner(self, owner_id: str) -> Owner:
        person = find_by_id(self.registry["people"], owner_id)
        role = str(person["role"])
        if role == "Principal Investigator (Thoracic Oncology)":
            role = "Principal Investigator"
        return Owner(id=str(person["id"]), display=str(person["display"]), role=role)

    def _key_issue(self, registry_trial: dict[str, Any]) -> str:
        criteria = registry_trial.get("key_criteria") or {}
        has_renal = bool((criteria.get("renal_threshold") or {}).get("crcl_min"))
        has_prior = any("platinum" in str(item).lower() for item in criteria.get("exclusion", []) or [])
        if has_renal and has_prior:
            return "Renal function threshold and prior therapy interpretation"
        if has_renal:
            return "Renal function threshold interpretation"
        return "Eligibility criteria interpretation"

    @staticmethod
    def _trace(
        by_source: dict[Source, SourceResult],
        criterion_results: list[CriterionResult],
        latest_crcl: dict[str, Any] | None,
    ) -> list[TraceStep]:
        by_category = {result.category: result for result in criterion_results}
        diagnosis = by_category.get("diagnosis")
        biomarker = by_category.get("biomarker")
        performance = by_category.get("performance")
        renal = by_category.get("renal")
        prior_therapy = by_category.get("prior_therapy")

        renal_state = "in_progress" if renal and renal.status == "uncertain" else "completed"
        prior_state = "in_progress" if prior_therapy and prior_therapy.status == "uncertain" else "completed"
        renal_detail = renal.evidence if renal else "No renal criterion found for this protocol."
        if not latest_crcl and renal and renal.status == "uncertain":
            renal_detail = "CrCl value unavailable in Fabric facts; using the Fabric eligibility evaluation."
        return [
            TraceStep(
                step="Verified diagnosis and stage",
                source=Source.FABRIC,
                detail=diagnosis.evidence if diagnosis else "Loaded diagnosis and staging facts from the patient registry.",
                ts=0,
                status="completed",
            ),
            TraceStep(
                step="Confirmed biomarker status",
                source=Source.FOUNDRY,
                detail=biomarker.evidence if biomarker else by_source[Source.FOUNDRY].summary,
                ts=1,
                status="completed",
            ),
            TraceStep(
                step="Reviewed treatment history",
                source=Source.FABRIC,
                detail=prior_therapy.evidence if prior_therapy else "Reviewed prior treatment records in Fabric.",
                ts=2,
                status="completed",
            ),
            TraceStep(
                step="Checked renal function against protocol threshold",
                source=Source.FABRIC,
                detail=renal_detail,
                ts=3,
                status=renal_state,
            ),
            TraceStep(
                step="Reviewed performance status criterion",
                source=Source.FABRIC,
                detail=performance.evidence if performance else "Reviewed ECOG performance status against protocol limits.",
                ts=4,
                status="completed",
            ),
            TraceStep(
                step="Interpreted prior therapy criterion language",
                source=Source.FOUNDRY,
                detail=by_source[Source.FOUNDRY].summary,
                ts=5,
                status=prior_state,
            ),
            TraceStep(step="Checked institutional policy and consent", source=Source.FOUNDRY, detail="IRB and consent policy require signed consent before study procedures.", ts=6, status="pending"),
            TraceStep(step="Assessed care team workflow and ownership", source=Source.WORK, detail=by_source[Source.WORK].summary, ts=7, status="completed"),
            TraceStep(step="Prepared evidence summary and next action", source=None, detail="Mapped met, uncertain, and not-met criteria into a governed next action.", ts=8, status="completed"),
        ]

    @staticmethod
    def _agent_trace(agent_run: AgentRun) -> list[TraceStep]:
        """Assessment steps derived from the hosted agent's real tool calls, so the steps reflect what
        the agent actually did for this specific question."""
        labels = {Source.FOUNDRY: "Foundry IQ", Source.FABRIC: "Fabric IQ", Source.WEB: "Web IQ", Source.WORK: "Work IQ"}
        steps: list[TraceStep] = [
            TraceStep(
                step="Planned retrieval across the IQ layers",
                source=None,
                detail="The agent decomposed the question and selected which knowledge sources to query.",
                ts=0,
                status="completed",
            )
        ]
        for index, call in enumerate(agent_run.tool_calls, start=1):
            label = labels.get(call.source, call.source.value)
            steps.append(
                TraceStep(
                    step=f"Queried {label}",
                    source=call.source,
                    detail=call.query,
                    ts=index,
                    status="completed" if call.status == "completed" else "in_progress",
                )
            )
        steps.append(
            TraceStep(
                step="Composed governed assessment",
                source=None,
                detail="Merged the retrieved evidence into a cited, human-reviewed trial-readiness assessment.",
                ts=len(steps),
                status="completed",
            )
        )
        return steps


_ACRONYMS = {"egfr", "nsclc", "alk", "ecog", "crcl", "irb", "pi", "sop", "nsc"}
_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5"}


def _humanize_trial(short: str) -> str:
    """Title-case a trial short name while preserving clinical acronyms (EGFR, NSCLC)."""
    words = []
    for word in short.split():
        lower = word.lower()
        if lower in _ACRONYMS:
            words.append(word.upper())
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)


def _phase_from_title(title: str) -> str:
    match = re.search(r"phase\s+([ivx]+|\d+)", title, re.IGNORECASE)
    if not match:
        return ""
    token = match.group(1).lower()
    return f"Phase {_ROMAN.get(token, token)}"


def classify_intent(question: str) -> str:
    """Map a question to one of the answer templates so the UI can adapt its structure.

    Explicit eligibility phrasing wins over every other template so an eligibility question is never
    downgraded to a partial (screening/protocol/data-gaps) view that hides the recommendation. The
    external-context triggers are deliberately specific so bare words like "web" or "outside" do not
    suppress the eligibility sections under an "external only" banner.
    """
    q = question.lower()
    if re.search(r"\beligib", q) or re.search(r"\bqualif", q) or "meet the criteria" in q or "meets the criteria" in q:
        return "eligibility"
    if any(
        k in q
        for k in (
            "external",
            "registry",
            "treatment landscape",
            "landscape context",
            "public context",
            "outside literature",
            "web iq",
        )
    ):
        return "external_context"
    if any(k in q for k in ("missing", "stale", "data gap", "gaps", "incomplete")):
        return "data_gaps"
    if any(k in q for k in ("who should own", "next step", "owner", "task should", "handoff", "coordinate")):
        return "workflow"
    if any(k in q for k in ("evidence packet", "prepare an evidence", "packet for", "evidence summary")):
        return "evidence"
    if any(k in q for k in ("preventing", "blocking", "blocker", "before screening", "moving to", "advance")):
        return "screening"
    if any(k in q for k in ("conflict", "exclusion", "prior therapy", "protocol", "interpretation", "amendment")):
        return "protocol"
    return "eligibility"


def _bottom_line(intent: str, assessment: str) -> str:
    """Plain-language closing statement, tailored to the question intent so it never contradicts the
    action panel or the status banner shown above it. A hard-stop assessment (not_eligible) overrides
    the intent copy so we never advise advancing a patient who fails a criterion."""
    if intent == "external_context":
        return (
            "This is external public context only. It does not by itself determine AMC trial "
            "eligibility, which is governed by the institutional criteria and PI review."
        )
    if assessment == "not_eligible":
        return (
            "This patient does not currently meet the trial criteria; formal screening should not "
            "proceed until the failing criterion is resolved and reviewed."
        )
    if intent == "workflow":
        return (
            "The next action is drafted and assigned; it still needs coordinator execution and PI "
            "review before formal screening proceeds."
        )
    if intent == "data_gaps":
        return "Resolve the open data items above before this case can advance to formal screening."
    if intent == "evidence":
        return (
            "This evidence packet is ready for PI review; it consolidates the patient facts, protocol "
            "criteria, and unresolved issues in one place."
        )
    if intent == "protocol":
        return (
            "This is a protocol interpretation to support PI review, not a final eligibility "
            "determination."
        )
    if assessment == "likely_eligible_pending":
        return (
            "This is a potential match, but formal screening should not proceed until the open items "
            "above are resolved and PI review is complete."
        )
    if assessment == "eligible":
        return "This patient appears to meet the criteria; proceed to screening per institutional workflow."
    return "Human review is required before any screening decision."
