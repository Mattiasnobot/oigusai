"""Analysis preparation orchestration for the HTTP boundary.

Step 1B moves request understanding, attached-document lookup, trusted legal
retrieval, deterministic routing and source relevance checks out of main.py.
The model, citation verification, evidence verification and response packaging
remain unchanged in main.py for this step.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from services.analysis_pipeline import AnalysisPipelineRun
from services.case_intake import CaseIntakeService
from services.coverage_verifier import CoverageVerifier
from services.documents import LocalDocumentService
from services.offline_ai import OfflineAIService
from services.legal_search import HistoricalDataUnavailableError, LegalSearchService
from services.matters import MatterNotFoundError
from services.retrieval_planner import MultiIssueRetrievalPlan, MultiIssueRetrievalPlanner
from services.retrieval_policy import RetrievalPlan, RetrievalPolicy
from services.turn_planner import ConversationTurnPlanner


class AnalysisOrchestrationError(Exception):
    """HTTP-neutral error raised by the analysis orchestration layer."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = str(detail)


@dataclass
class PreparedAnalysis:
    """Verified preparation state consumed by the remaining analysis stages."""

    analysis_started: float
    pipeline: AnalysisPipelineRun
    current_turn: str
    answer_requirements: List[str]
    intent_focus: str
    current_intents: List[str]
    fine_context: bool
    document_spans: List[Dict[str, Any]]
    case_card: Dict[str, Any]
    search_text: str
    query_context: Dict[str, Any]
    analysis_laws: List[Dict[str, Any]]
    relevance_text: str
    obligation_plan: MultiIssueRetrievalPlan
    route_plan: RetrievalPlan


@dataclass
class ExecutedAnalysis:
    """Model and source-verification result ready for response packaging."""

    analysis_case: str
    analysis_laws: List[Dict[str, Any]]
    document_claims: List[Dict[str, Any]]
    structured_claims: List[Dict[str, Any]]
    analysis_text: str
    is_mock: bool
    fallback_used: bool
    coverage_fallback_used: bool
    coverage_repair_used: bool
    coverage_report: Dict[str, Any]
    coverage_repair_diagnostics: Dict[str, Any]
    verified_sources: List[str]
    legal_context: Dict[str, Any]


class AnalysisOrchestrator:
    """Prepare one analysis request without coupling the service to FastAPI."""

    def __init__(
        self,
        *,
        legal_service: LegalSearchService,
        matter_store: Optional[Any],
        relevance_verifier: Any,
        run_guarded_work: Callable[..., Awaitable[Any]],
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.legal_service = legal_service
        self.matter_store = matter_store
        self.relevance_verifier = relevance_verifier
        self.run_guarded_work = run_guarded_work
        self.logger = logger or logging.getLogger(__name__)

    def _multi_issue_limit(self) -> int:
        configured = getattr(self.legal_service, "max_results", 5)
        if not isinstance(configured, int):
            configured = 5
        return min(12, max(6, configured * 2))

    @staticmethod
    def _fuse_law_batches(
        batches: List[List[Dict[str, Any]]],
        *,
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Round-robin verified law batches so one issue cannot crowd out another."""
        clean_batches = [list(batch or []) for batch in batches]
        max_depth = max((len(batch) for batch in clean_batches), default=0)
        fused: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for rank in range(max_depth):
            for batch in clean_batches:
                if rank >= len(batch):
                    continue
                law = batch[rank]
                law_id = str(law.get("id", "")).strip().upper()
                if not law_id or law_id in seen:
                    continue
                seen.add(law_id)
                fused.append(law)
                if len(fused) >= limit:
                    return fused
        return fused

    async def _attempt_focused_coverage_repair(
        self,
        *,
        trigger: str,
        ai_service: OfflineAIService,
        source_verifier: Any,
        obligation_plan: Any,
        analysis_case: str,
        analysis_laws: List[Dict[str, Any]],
        coverage_report: Dict[str, Any],
        relevance_text: str,
        event_date: str,
    ) -> Dict[str, Any]:
        """Run at most one schema-constrained repair against audited source targets."""
        targets = CoverageVerifier.repair_targets(coverage_report)
        repair_laws = CoverageVerifier.repair_laws(coverage_report, analysis_laws)
        target_sources = [
            str(target.get("source_id") or "") for target in targets
            if str(target.get("source_id") or "")
        ]
        diagnostics: Dict[str, Any] = {
            "attempted": bool(targets and repair_laws),
            "trigger": str(trigger or ""),
            "target_sources": target_sources,
            "returned_sources": [],
            "citation_valid": False,
            "coverage_passed": False,
            "semantic_relevance": False,
            "missing_concepts": [],
            "accepted": False,
            "failure_reason": "",
        }
        if not targets or not repair_laws:
            diagnostics["failure_reason"] = "no_audited_repair_targets"
            return {"accepted": False, "diagnostics": diagnostics}

        response_schema = CoverageVerifier.repair_schema(coverage_report)
        repair_prompt = CoverageVerifier.repair_prompt(
            coverage_report,
            repair_laws,
            analysis_case,
            event_date,
        )
        if not response_schema or not repair_prompt:
            diagnostics["failure_reason"] = "repair_prompt_or_schema_missing"
            return {"accepted": False, "diagnostics": diagnostics}

        try:
            raw_response = await self.run_guarded_work(
                "analysis",
                ai_service.generate_structured,
                repair_prompt,
                response_schema,
            )
            (
                repair_text,
                repair_claims,
                repair_response_diagnostics,
            ) = ai_service.prepare_structured_repair_response(
                raw_response,
                repair_laws,
                analysis_case,
            )
            diagnostics.update(repair_response_diagnostics)
            repair_valid, repair_sources = source_verifier.verify_sources(
                repair_text,
                repair_laws,
            )
            diagnostics["returned_sources"] = list(repair_sources)
            diagnostics["citation_valid"] = bool(repair_valid)
            repaired_coverage = CoverageVerifier.verify(
                obligation_plan,
                analysis_laws,
                repair_sources if repair_valid else [],
                answer_text=repair_text,
            )
            diagnostics["coverage_passed"] = bool(
                repaired_coverage.get("passed")
            )
            cited_ids = {str(value).strip().upper() for value in repair_sources}
            cited_laws = [
                law for law in analysis_laws
                if str(law.get("id", "")).strip().upper() in cited_ids
            ]
            repaired_relevance = self.relevance_verifier.verify_answer(
                relevance_text,
                repair_text,
                cited_laws,
            )
            diagnostics["semantic_relevance"] = bool(repaired_relevance.relevant)
            diagnostics["missing_concepts"] = list(
                getattr(repaired_relevance, "missing_concepts", ()) or ()
            )
            accepted = bool(
                repair_valid
                and repaired_coverage.get("passed")
                and repaired_relevance.relevant
            )
            diagnostics["accepted"] = accepted
            if not accepted:
                if not repair_valid:
                    diagnostics["failure_reason"] = "citation_verification"
                elif not repaired_coverage.get("passed"):
                    diagnostics["failure_reason"] = "coverage_verification"
                else:
                    diagnostics["failure_reason"] = "semantic_relevance"
            return {
                "accepted": accepted,
                "diagnostics": diagnostics,
                "analysis_text": repair_text,
                "structured_claims": repair_claims,
                "verified_sources": list(repair_sources),
                "coverage_report": repaired_coverage,
            }
        except Exception as exc:
            diagnostics["failure_reason"] = f"{type(exc).__name__}: {exc}"[:300]
            self.logger.warning(
                "Focused coverage repair failed (%s): %s", trigger, exc
            )
            return {"accepted": False, "diagnostics": diagnostics}

    async def prepare(self, request: Any) -> PreparedAnalysis:
        analysis_started = time.perf_counter()
        pipeline = AnalysisPipelineRun()

        case_description = str(getattr(request, "case_description", "") or "")
        if not case_description.strip():
            raise AnalysisOrchestrationError(
                400, "Olukorra kirjeldus ei tohi olla tühi."
            )

        current_turn = str(getattr(request, "current_message", "") or "").strip()
        answer_requirements = [
            str(value).strip()
            for value in (getattr(request, "answer_requirements", None) or [])
            if str(value).strip()
        ][:5]
        intent_focus = (
            "\n".join([current_turn, *answer_requirements]).strip()
            or case_description.strip()
        )
        current_intents = ConversationTurnPlanner.detect_intents(intent_focus)
        fine_context = ConversationTurnPlanner.is_fine_context(case_description)
        pipeline.complete(
            "case_understanding",
            current_intent_count=len(current_intents),
            answer_requirement_count=len(answer_requirements),
        )

        document_spans: List[Dict[str, Any]] = []
        case_card: Dict[str, Any] = {}
        matter_id = str(getattr(request, "matter_id", "") or "").strip()
        if matter_id:
            if self.matter_store is None:
                raise AnalysisOrchestrationError(
                    503, "Juhtumiregister ei ole valmis."
                )
            try:
                case_card = self.matter_store.case_card(matter_id)
                document_spans = self.matter_store.relevant_spans(
                    matter_id,
                    getattr(request, "document_ids", None) or [],
                    intent_focus
                    or getattr(request, "search_query", None)
                    or case_description,
                    limit=5,
                )
            except MatterNotFoundError as exc:
                raise AnalysisOrchestrationError(
                    404, "Juhtumit ei leitud."
                ) from exc
        pipeline.complete(
            "document_evidence",
            span_count=len(document_spans),
            matter_attached=bool(matter_id),
        )

        search_text = str(
            getattr(request, "search_query", None) or case_description
        ).strip()
        if document_spans:
            document_search = " ".join(
                str(span.get("text") or "")[:350]
                for span in document_spans[:4]
            )
            search_text = f"{search_text} {document_search}"[:2000].strip()
        obligation_plan = MultiIssueRetrievalPlanner.plan(
            case_description=case_description,
            search_text=search_text,
            current_intents=current_intents,
            answer_requirements=answer_requirements,
            fine_context=fine_context,
        )
        event_date = str(getattr(request, "event_date", "") or "")

        try:
            laws, query_interpretation = await self.run_guarded_work(
                "retrieval",
                self.legal_service.search_laws_with_context,
                search_text,
                event_date,
            )
            if obligation_plan.multi_issue:
                obligation_batches: List[List[Dict[str, Any]]] = []
                for obligation in obligation_plan.obligations:
                    obligation_laws, _ = await self.run_guarded_work(
                        "retrieval",
                        self.legal_service.search_laws_with_context,
                        obligation.query,
                        event_date,
                    )
                    obligation_batches.append(obligation_laws)
                laws = self._fuse_law_batches(
                    [laws, *obligation_batches],
                    limit=self._multi_issue_limit(),
                )
        except HistoricalDataUnavailableError as exc:
            raise AnalysisOrchestrationError(422, str(exc)) from exc
        except ValueError as exc:
            raise AnalysisOrchestrationError(422, str(exc)) from exc

        if not laws:
            raise AnalysisOrchestrationError(
                404,
                "Ma ei leidnud veel piisavalt täpset õigusallikat. Lisa palun, "
                "kes tegi mida, millal see juhtus ja millist abi vajad.",
            )

        query_context = query_interpretation.to_dict()
        hinted_id_list = [
            str(value).upper()
            for value in query_context.get("section_hints", [])
        ]
        hinted_ids = set(hinted_id_list)
        analysis_laws = list(laws)
        if hinted_ids:
            available_by_id = {
                str(law.get("id", "")).upper(): law for law in laws
            }
            hinted_laws: List[Dict[str, Any]] = []
            for section_id in hinted_id_list[:16]:
                law = available_by_id.get(section_id)
                if law is None and isinstance(self.legal_service, LegalSearchService):
                    try:
                        law = self.legal_service.get_law_by_id(section_id)
                    except ValueError:
                        law = None
                if law is not None:
                    hinted_laws.append(law)
            if hinted_laws:
                if obligation_plan.multi_issue:
                    analysis_laws = self._fuse_law_batches(
                        [hinted_laws, analysis_laws],
                        limit=self._multi_issue_limit(),
                    )
                else:
                    analysis_laws = hinted_laws

        route_plan = RetrievalPolicy.plan(
            case_description=case_description,
            search_text=search_text,
            current_intents=current_intents,
            fine_context=fine_context,
        )
        routed_ids = list(route_plan.routed_ids)
        if routed_ids:
            routed_by_id = {
                str(law.get("id", "")).upper(): law for law in analysis_laws
            }
            routed_laws: List[Dict[str, Any]] = []
            for section_id in routed_ids:
                law = routed_by_id.get(section_id)
                if law is None and isinstance(self.legal_service, LegalSearchService):
                    try:
                        law = self.legal_service.get_law_by_id(section_id)
                    except ValueError:
                        law = None
                if law is not None:
                    routed_laws.append(law)
            if routed_laws:
                if obligation_plan.multi_issue:
                    analysis_laws = self._fuse_law_batches(
                        [routed_laws, analysis_laws],
                        limit=self._multi_issue_limit(),
                    )
                else:
                    analysis_laws = routed_laws

        relevance_text = intent_focus or search_text
        if fine_context and not ConversationTurnPlanner.is_fine_context(relevance_text):
            relevance_text = f"rahatrahv {relevance_text}".strip()
        source_relevance = self.relevance_verifier.verify_laws(
            relevance_text, analysis_laws
        )
        if not source_relevance.relevant:
            self.logger.warning(
                "Retrieved laws failed semantic relevance check for concepts: %s",
                ", ".join(source_relevance.missing_concepts),
            )
            raise AnalysisOrchestrationError(
                422,
                source_relevance.clarification
                or self.relevance_verifier.clarification_for(source_relevance),
            )
        pipeline.complete(
            "legal_retrieval",
            result_count=len(analysis_laws),
            semantic_relevance=True,
            hybrid_used=bool(getattr(self.legal_service, "hybrid_ready", False)),
            obligation_count=len(obligation_plan.obligations),
            multi_issue=obligation_plan.multi_issue,
            retrieval_query_count=(
                1 + len(obligation_plan.obligations)
                if obligation_plan.multi_issue
                else 1
            ),
        )

        return PreparedAnalysis(
            analysis_started=analysis_started,
            pipeline=pipeline,
            current_turn=current_turn,
            answer_requirements=answer_requirements,
            intent_focus=intent_focus,
            current_intents=current_intents,
            fine_context=fine_context,
            document_spans=document_spans,
            case_card=case_card,
            search_text=search_text,
            query_context=query_context,
            analysis_laws=analysis_laws,
            relevance_text=relevance_text,
            obligation_plan=obligation_plan,
            route_plan=route_plan,
        )

    async def execute(
        self,
        request: Any,
        prepared: PreparedAnalysis,
        *,
        ai_service: Any,
        source_verifier: Any,
    ) -> ExecutedAnalysis:
        """Run model analysis and return only source-verified output."""
        pipeline = prepared.pipeline
        current_turn = prepared.current_turn
        answer_requirements = list(prepared.answer_requirements)
        obligation_plan = getattr(prepared, "obligation_plan", None)
        if obligation_plan is not None and getattr(obligation_plan, "multi_issue", False):
            for requirement in obligation_plan.answer_requirements:
                if requirement not in answer_requirements:
                    answer_requirements.append(requirement)
        document_spans = prepared.document_spans
        relevance_text = prepared.relevance_text
        route_plan = prepared.route_plan
        analysis_laws = list(prepared.analysis_laws)

        fallback_used = False
        coverage_fallback_used = False
        coverage_repair_used = False
        repair_attempted = False
        coverage_report: Dict[str, Any] = {}
        coverage_repair_diagnostics: Dict[str, Any] = {
            "attempted": False,
            "trigger": "",
            "target_sources": [],
            "returned_sources": [],
            "citation_valid": False,
            "coverage_passed": False,
            "semantic_relevance": False,
            "missing_concepts": [],
            "accepted": False,
            "failure_reason": "",
        }
        case_description = str(getattr(request, "case_description", "") or "")
        analysis_case = (
            CaseIntakeService._user_evidence_text(case_description)
            or case_description.strip()
        )
        case_context = str(getattr(request, "case_context", "") or "").strip()
        if len(analysis_case) > 6000:
            if case_context:
                analysis_case = (
                    f"Kasutaja algteksti algus:\n{analysis_case[:3000]}\n\n"
                    f"Kontrollitud juhtumikokkuvõte:\n{case_context}"
                )[:6000]
            else:
                analysis_case = (
                    f"Kasutaja algteksti algus:\n{analysis_case[:4000]}\n\n"
                    f"Kasutaja algteksti lõpp:\n{analysis_case[-1500:]}"
                )

        normalized_current = " ".join(current_turn.casefold().split())
        normalized_analysis_case = " ".join(analysis_case.casefold().split())
        repeat_current_turn = bool(
            normalized_current and normalized_current != normalized_analysis_case
        )
        if current_turn and repeat_current_turn:
            analysis_case += (
                f"\n\nKASUTAJA VIIMANE SÕNUM:\n{current_turn[:3000]}\n\n"
                "Kasuta viimast sõnumit koos alltoodud vastusekohustustega. "
                "Varasem tekst on ainult taust."
            )
        if answer_requirements:
            requirement_text = "\n".join(
                f"- {value}" for value in answer_requirements
            )
            analysis_case += f"\nVASTUS PEAB KÄSITLEMA:\n{requirement_text}"

        model_case = analysis_case
        if document_spans and not isinstance(ai_service, OfflineAIService):
            document_context = "\n".join(
                f"[{span['span_id']}] {span['file_name']}, lk {span['page']}: "
                f"{span['text']}"
                for span in document_spans
            )
            model_case += (
                "\n\nKONTROLLITUD DOKUMENDIKATKENDID:\n"
                + document_context
                + "\nKasuta neid ainult juhtumi faktilise taustana. Ära muuda OCR-teksti "
                "seaduseallikaks ega mõtle puuduvaid dokumente juurde."
            )

        document_claims: List[Dict[str, Any]] = []
        for index, span in enumerate(document_spans[:4], start=1):
            excerpt = LocalDocumentService.focused_excerpt(span, relevance_text)
            method = str(span.get("method") or "text")
            document_claims.append({
                "claim_id": f"DOC-{index}",
                "kind": "document_excerpt",
                "text": excerpt["text"],
                "verification_status": (
                    "OCR_REVIEW_REQUIRED"
                    if method == "ocr"
                    else "DOCUMENT_TEXT_VERIFIED"
                ),
                "sources": [{
                    "kind": "document",
                    "id": span["span_id"],
                    "document_id": span["document_id"],
                    "title": span["file_name"],
                    "source": f"Dokument, lk {span['page']}",
                    "evidence": excerpt["text"],
                    "page": span["page"],
                    "start": excerpt["start"],
                    "end": excerpt["end"],
                    "method": method,
                }],
            })

        structured_claims: List[Dict[str, Any]] = []
        legal_context: Dict[str, Any] = {
            "mode": "LOCAL_CORPUS",
            "model_context_enabled": False,
        }
        try:
            if isinstance(ai_service, OfflineAIService):
                analysis_text, is_mock, structured_claims = await self.run_guarded_work(
                    "analysis",
                    ai_service.analyze_case_structured,
                    model_case,
                    analysis_laws,
                    str(getattr(request, "event_date", "") or ""),
                    document_spans,
                )
            else:
                analysis_text, is_mock = await self.run_guarded_work(
                    "analysis",
                    ai_service.analyze_case,
                    model_case,
                    analysis_laws,
                    str(getattr(request, "event_date", "") or ""),
                )
            if isinstance(ai_service, OfflineAIService):
                admissions = [
                    str(law.get("model_context_admission") or "")
                    for law in analysis_laws
                ]
                live_count = admissions.count("VERIFIED_LIVE_BINDING_SECTION")
                local_count = admissions.count("AUDITED_LOCAL_CORPUS_FALLBACK")
                enabled = bool(getattr(ai_service, "live_model_context_enabled", False))
                mode = (
                    "DISABLED" if not enabled
                    else "MIXED_VERIFIED_AND_LOCAL" if live_count and local_count
                    else "LIVE_VERIFIED" if live_count
                    else "LOCAL_FALLBACK"
                )
                legal_context = {
                    "version": "V11.6-live-pilot-observability-1",
                    "mode": mode,
                    "model_context_enabled": bool(live_count or local_count),
                    "live_count": live_count,
                    "local_count": local_count,
                    "source_count": len(analysis_laws),
                }
        except Exception as exc:
            self.logger.error(
                "AI analysis unavailable; returning verified source digest: %s",
                exc,
            )
            analysis_text = ai_service.build_source_only_fallback(
                analysis_case, analysis_laws
            )
            if isinstance(ai_service, OfflineAIService):
                structured_claims = ai_service.claims_from_verified_analysis(
                    analysis_text, analysis_laws
                )
            is_mock = False
            fallback_used = True

        normalized_answer = " ".join(str(analysis_text).casefold().split())
        form_answer_complete = (
            "kirjalikku taasesitamist võimaldavas vormis" in normalized_answer
            and "tühine" in normalized_answer
        )
        if (
            route_plan.employment_form_question
            and not getattr(obligation_plan, "obligations", ())
            and not form_answer_complete
        ):
            form_law = next(
                (
                    law for law in analysis_laws
                    if str(law.get("id", "")).upper() == "TLS_95"
                ),
                None,
            )
            if form_law is None and isinstance(
                self.legal_service, LegalSearchService
            ):
                try:
                    form_law = self.legal_service.get_law_by_id("TLS_95")
                except ValueError:
                    form_law = None
            if form_law is None:
                self.logger.error(
                    "TLS_95 missing for mandatory employment form coverage"
                )
                raise AnalysisOrchestrationError(
                    500, "Töölepingu ülesütlemise vorminõude allikas puudub."
                )
            analysis_laws = [form_law]
            analysis_text = (
                "OLUKORD:\n"
                "Küsimus puudutab, kas töölepingu saab üles öelda ainult suuliselt.\n\n"
                "LÜHIVASTUS:\n"
                "Töölepingu ülesütlemisavaldus tuleb teha kirjalikku taasesitamist "
                "võimaldavas vormis. Vorminõuet rikkudes tehtud ülesütlemisavaldus "
                "on tühine [TLS_95].\n\n"
                "ÕIGUSLIK KOHALDAMINE:\n"
                "Tööandja peab ülesütlemist põhjendama [TLS_95]. Põhjendus peab samuti olema "
                "kirjalikku taasesitamist võimaldavas vormis [TLS_95].\n\n"
                "SOOVITUSED:\n"
                "1. Säilita tööandjaga peetud kirjavahetus ja pane suulise vestluse aeg "
                "ning sisu enda jaoks kirja.\n"
                "2. Küsi tööandjalt ülesütlemisavaldus ja selle põhjendus kirjalikku "
                "taasesitamist võimaldavas vormis.\n\n"
                "KASUTATUD ALLIKAD:\n"
                "[TLS_95]"
            )
            if isinstance(ai_service, OfflineAIService):
                structured_claims = ai_service.claims_from_verified_analysis(
                    analysis_text, analysis_laws
                )
            else:
                structured_claims = []
            is_mock = False
            fallback_used = True
            coverage_fallback_used = True
            self.logger.warning(
                "Model answer missed mandatory employment form coverage; "
                "returning verified TLS_95 answer"
            )

        is_valid, verified_sources = source_verifier.verify_sources(
            analysis_text, analysis_laws
        )
        if not is_valid and not fallback_used:
            self.logger.warning(
                "AI response failed citation verification; returning verified source digest"
            )
            analysis_text = ai_service.build_source_only_fallback(
                analysis_case, analysis_laws
            )
            if isinstance(ai_service, OfflineAIService):
                structured_claims = ai_service.claims_from_verified_analysis(
                    analysis_text, analysis_laws
                )
            is_valid, verified_sources = source_verifier.verify_sources(
                analysis_text, analysis_laws
            )
            fallback_used = True

        if not is_valid:
            self.logger.error(
                "Deterministic source digest failed citation verification"
            )
            raise AnalysisOrchestrationError(
                500, "Kontrollitud allikate kuvamine ebaõnnestus."
            )

        coverage_report = CoverageVerifier.verify(
            obligation_plan,
            analysis_laws,
            verified_sources,
            answer_text=analysis_text,
        )
        if (
            coverage_report.get("needs_repair")
            and not fallback_used
            and isinstance(ai_service, OfflineAIService)
        ):
            repair_attempted = True
            repair_result = await self._attempt_focused_coverage_repair(
                trigger="coverage",
                ai_service=ai_service,
                source_verifier=source_verifier,
                obligation_plan=obligation_plan,
                analysis_case=analysis_case,
                analysis_laws=analysis_laws,
                coverage_report=coverage_report,
                relevance_text=relevance_text,
                event_date=str(getattr(request, "event_date", "") or ""),
            )
            coverage_repair_diagnostics = dict(
                repair_result.get("diagnostics") or {}
            )
            if repair_result.get("accepted"):
                analysis_text = str(repair_result.get("analysis_text") or "")
                structured_claims = list(
                    repair_result.get("structured_claims") or []
                )
                is_mock = False
                verified_sources = list(
                    repair_result.get("verified_sources") or []
                )
                is_valid = True
                coverage_report = dict(
                    repair_result.get("coverage_report") or {}
                )
                coverage_repair_used = True
                self.logger.info(
                    "Coverage repair succeeded for obligations: %s",
                    ", ".join(
                        row.get("kind", "")
                        for row in coverage_report.get("obligations", [])
                        if row.get("enforced")
                    ),
                )
            else:
                self.logger.info(
                    "Focused coverage repair rejected: trigger=%s reason=%s returned=%s",
                    coverage_repair_diagnostics.get("trigger"),
                    coverage_repair_diagnostics.get("failure_reason"),
                    ",".join(coverage_repair_diagnostics.get("returned_sources") or []),
                )

        if coverage_report.get("enforced") and not coverage_report.get("passed"):
            if coverage_report.get("missing_source"):
                raise AnalysisOrchestrationError(
                    422,
                    "Kontrollitud õigusallikad ei kata veel kõiki tuvastatud "
                    "küsimuse osi. Täpsusta dokumendi liiki või menetluskonteksti.",
                )
            coverage_digest = CoverageVerifier.build_source_digest(
                coverage_report,
                analysis_laws,
            )
            if not coverage_digest:
                raise AnalysisOrchestrationError(
                    500,
                    "Kontrollitud allikate katvuse kuvamine ebaõnnestus.",
                )
            analysis_text = coverage_digest
            if isinstance(ai_service, OfflineAIService):
                structured_claims = ai_service.claims_from_verified_analysis(
                    analysis_text,
                    analysis_laws,
                )
            else:
                structured_claims = []
            is_valid, verified_sources = source_verifier.verify_sources(
                analysis_text,
                analysis_laws,
            )
            fallback_used = True
            coverage_fallback_used = True
            coverage_report = CoverageVerifier.verify(
                obligation_plan,
                analysis_laws,
                verified_sources if is_valid else [],
            )
            if not is_valid or not coverage_report.get("passed"):
                self.logger.error("Coverage source digest failed verification")
                raise AnalysisOrchestrationError(
                    500,
                    "Kontrollitud allikate katvuse kuvamine ebaõnnestus.",
                )

        cited_ids = set(verified_sources)
        cited_laws = [
            law for law in analysis_laws
            if str(law.get("id", "")).upper() in cited_ids
        ]
        answer_relevance = self.relevance_verifier.verify_answer(
            relevance_text, analysis_text, cited_laws
        )
        if (
            not answer_relevance.relevant
            and not fallback_used
            and not repair_attempted
            and coverage_report.get("enforced")
            and isinstance(ai_service, OfflineAIService)
        ):
            repair_attempted = True
            repair_result = await self._attempt_focused_coverage_repair(
                trigger="semantic_relevance",
                ai_service=ai_service,
                source_verifier=source_verifier,
                obligation_plan=obligation_plan,
                analysis_case=analysis_case,
                analysis_laws=analysis_laws,
                coverage_report=coverage_report,
                relevance_text=relevance_text,
                event_date=str(getattr(request, "event_date", "") or ""),
            )
            coverage_repair_diagnostics = dict(
                repair_result.get("diagnostics") or {}
            )
            if repair_result.get("accepted"):
                analysis_text = str(repair_result.get("analysis_text") or "")
                structured_claims = list(
                    repair_result.get("structured_claims") or []
                )
                is_mock = False
                verified_sources = list(
                    repair_result.get("verified_sources") or []
                )
                is_valid = True
                coverage_report = dict(
                    repair_result.get("coverage_report") or {}
                )
                coverage_repair_used = True
                cited_ids = set(verified_sources)
                cited_laws = [
                    law for law in analysis_laws
                    if str(law.get("id", "")).upper() in cited_ids
                ]
                answer_relevance = self.relevance_verifier.verify_answer(
                    relevance_text, analysis_text, cited_laws
                )
            else:
                self.logger.info(
                    "Focused relevance repair rejected: reason=%s returned=%s missing=%s",
                    coverage_repair_diagnostics.get("failure_reason"),
                    ",".join(coverage_repair_diagnostics.get("returned_sources") or []),
                    ",".join(coverage_repair_diagnostics.get("missing_concepts") or []),
                )

        if not answer_relevance.relevant and not fallback_used:
            self.logger.warning(
                "AI response failed semantic relevance check after bounded repair; "
                "returning verified source digest"
            )
            coverage_digest = CoverageVerifier.build_source_digest(
                coverage_report,
                analysis_laws,
            )
            analysis_text = coverage_digest or ai_service.build_source_only_fallback(
                analysis_case, analysis_laws
            )
            if isinstance(ai_service, OfflineAIService):
                structured_claims = ai_service.claims_from_verified_analysis(
                    analysis_text, analysis_laws
                )
            is_valid, verified_sources = source_verifier.verify_sources(
                analysis_text, analysis_laws
            )
            fallback_used = True
            if coverage_digest:
                coverage_fallback_used = True
            coverage_report = CoverageVerifier.verify(
                obligation_plan,
                analysis_laws,
                verified_sources if is_valid else [],
            )
            cited_ids = set(verified_sources)
            cited_laws = [
                law for law in analysis_laws
                if str(law.get("id", "")).upper() in cited_ids
            ]
            answer_relevance = self.relevance_verifier.verify_answer(
                relevance_text, analysis_text, cited_laws
            )

        if not is_valid:
            self.logger.error("Relevance fallback failed citation verification")
            raise AnalysisOrchestrationError(
                500, "Kontrollitud allikate kuvamine ebaõnnestus."
            )
        if not answer_relevance.relevant:
            self.logger.warning(
                "Final answer failed semantic relevance check for concepts: %s",
                ", ".join(answer_relevance.missing_concepts),
            )
            raise AnalysisOrchestrationError(
                422,
                answer_relevance.clarification
                or self.relevance_verifier.clarification_for(answer_relevance),
            )
        if coverage_report.get("enforced") and not coverage_report.get("passed"):
            raise AnalysisOrchestrationError(
                500, "Lõppvastuse kohustuste katvuse kontroll ebaõnnestus."
            )

        pipeline.complete(
            "model_analysis",
            fallback=fallback_used,
            mock=is_mock,
            structured_claim_count=len(structured_claims),
            legal_context_mode=str(legal_context.get("mode") or "LOCAL_CORPUS"),
            live_context_enabled=bool(legal_context.get("model_context_enabled")),
        )
        pipeline.complete(
            "source_verification",
            citation_valid=is_valid,
            semantic_relevance=answer_relevance.relevant,
            verified_source_count=len(verified_sources),
            coverage_passed=bool(coverage_report.get("passed", True)),
            coverage_repair=coverage_repair_used,
            coverage_repair_attempted=bool(coverage_repair_diagnostics.get("attempted")),
            coverage_repair_trigger=str(coverage_repair_diagnostics.get("trigger") or ""),
            coverage_repair_accepted=bool(coverage_repair_diagnostics.get("accepted")),
            missing_coverage=len(coverage_report.get("missing_answer") or []),
        )

        return ExecutedAnalysis(
            analysis_case=analysis_case,
            analysis_laws=analysis_laws,
            document_claims=document_claims,
            structured_claims=structured_claims,
            analysis_text=analysis_text,
            is_mock=bool(is_mock),
            fallback_used=fallback_used,
            coverage_fallback_used=coverage_fallback_used,
            coverage_repair_used=coverage_repair_used,
            coverage_report=dict(coverage_report),
            coverage_repair_diagnostics=dict(coverage_repair_diagnostics),
            verified_sources=list(verified_sources),
            legal_context=legal_context,
        )


    def finalize(
        self,
        request: Any,
        prepared: PreparedAnalysis,
        executed: ExecutedAnalysis,
        *,
        evidence_verifier: Any,
        urgency_analyzer: Any,
        verified_answer_builder: Any,
        metrics_store: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Verify evidence and package the final HTTP-neutral analysis result."""
        pipeline = prepared.pipeline
        document_spans = prepared.document_spans
        case_card = prepared.case_card
        analysis_laws = list(executed.analysis_laws)
        analysis_text = executed.analysis_text
        fallback_used = executed.fallback_used
        verified_sources = list(executed.verified_sources)

        warning = (
            "Viidete ID-d ja mudeli valitud tõendikatkendid on kontrollitud etteantud "
            "õigusallikate vastu, kuid see ei tõenda automaatselt iga järelduse materiaalset "
            "õigsust. Tegemist on esmase analüüsiga, mitte õigusnõuga."
        )
        if executed.is_mock:
            warning = "TESTREŽIIM: Ollama vastus on näidisvastus. " + warning
        if (
            executed.coverage_fallback_used
            and getattr(prepared.route_plan, "employment_form_question", False)
            and {
                str(value).strip().upper()
                for value in verified_sources
                if str(value).strip()
            } == {"TLS_95"}
        ):
            warning = (
                "Vastus on piiritletud töölepingu ülesütlemise vorminõudega ja põhineb "
                "kontrollitud TLS §-l 95. Tegemist on esmase selgituse, mitte õigusnõuga."
            )
        elif executed.coverage_fallback_used:
            warning = (
                "Mudeli vastus ei katnud kõiki tuvastatud küsimuse osi. Lõppvastus "
                "piirati auditeeritud kohustustega seotud kontrollitud "
                "õigusallikakatkenditega. Tegemist on esmase selgituse, mitte õigusnõuga."
            )
        elif fallback_used:
            warning = (
                "Selgitus põhineb kontrollitud õigusallikatel. Täpsema vastuse saab anda "
                "dokumendi pealkirja ja sellele märgitud rikkumise põhjal."
            )
        if document_spans:
            warning += (
                " Dokumendikatkendid on seotud faili ja leheküljega; OCR-teksti puhul "
                "tuleb olulised nimed, kuupäevad ja summad originaalilt üle kontrollida."
            )

        combined_claims = [
            *executed.document_claims,
            *executed.structured_claims,
        ]
        evidence_valid = False
        if combined_claims:
            evidence_valid, combined_claims = evidence_verifier.verify(
                combined_claims,
                analysis_laws,
                document_spans,
            )
            if not evidence_valid:
                self.logger.error("Structured evidence failed the V7 API boundary")
                raise AnalysisOrchestrationError(
                    500, "Kontrollitud tõendite kuvamine ebaõnnestus."
                )
        pipeline.complete(
            "evidence_verification",
            valid=evidence_valid or not combined_claims,
            claim_count=len(combined_claims),
        )

        has_ocr_evidence = any(
            claim.get("verification_status") == "OCR_REVIEW_REQUIRED"
            for claim in combined_claims
        )
        has_cross_source_comparison = any(
            claim.get("kind") == "inference" for claim in combined_claims
        )
        verification_status = (
            "SOURCE_ONLY_FALLBACK"
            if fallback_used
            else "OCR_REVIEW_REQUIRED" if has_ocr_evidence
            else "INPUTS_VERIFIED" if has_cross_source_comparison
            else "EVIDENCE_VERIFIED"
            if evidence_valid
            and executed.structured_claims
            and all(
                claim.get("verification_status") == "EVIDENCE_VERIFIED"
                for claim in executed.structured_claims
            )
            else "CITATIONS_VERIFIED"
        )

        urgency = urgency_analyzer.analyze(
            str(getattr(request, "case_description", "") or ""),
            event_date=str(getattr(request, "event_date", "") or ""),
            document_spans=document_spans,
        )
        layered_answer = verified_answer_builder.build(
            analysis=analysis_text,
            claims=combined_claims,
            verification_status=verification_status,
            warning=warning,
            case_card=case_card,
            urgency=urgency,
            fallback_used=fallback_used,
        )
        pipeline.complete(
            "answer_packaging",
            layered=True,
            confidence=layered_answer.get("confidence", ""),
            unknown_count=len(layered_answer.get("unknowns") or []),
        )
        pipeline_result = pipeline.public()

        if metrics_store is not None:
            metrics_store.record_analysis(
                duration_ms=(time.perf_counter() - prepared.analysis_started) * 1000,
                verification_status=verification_status,
                fallback=fallback_used,
                claim_count=len(combined_claims),
                source_count=len(verified_sources),
                legal_context_mode=str(
                    getattr(executed, "legal_context", {}).get("mode")
                    or "LOCAL_CORPUS"
                ),
            )

        return {
            "analysis_text": analysis_text,
            "analysis_laws": analysis_laws,
            "verified_sources": verified_sources,
            "is_mock": executed.is_mock,
            "fallback_used": fallback_used,
            "warning": warning,
            "combined_claims": combined_claims,
            "verification_status": verification_status,
            "layered_answer": layered_answer,
            "pipeline": pipeline_result,
            "legal_context": dict(getattr(executed, "legal_context", {}) or {}),
            "coverage": dict(getattr(executed, "coverage_report", {}) or {}),
            "coverage_repair": dict(
                getattr(executed, "coverage_repair_diagnostics", {}) or {}
            ),
        }
