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
from services.documents import LocalDocumentService
from services.offline_ai import OfflineAIService
from services.legal_search import HistoricalDataUnavailableError, LegalSearchService
from services.matters import MatterNotFoundError
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
    verified_sources: List[str]


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
        intent_focus = "\n".join([current_turn, *answer_requirements]).strip()
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

        try:
            search_text = str(
                getattr(request, "search_query", None) or case_description
            ).strip()
            if document_spans:
                document_search = " ".join(
                    str(span.get("text") or "")[:350]
                    for span in document_spans[:4]
                )
                search_text = f"{search_text} {document_search}"[:2000].strip()
            laws, query_interpretation = await self.run_guarded_work(
                "retrieval",
                self.legal_service.search_laws_with_context,
                search_text,
                str(getattr(request, "event_date", "") or ""),
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
        answer_requirements = prepared.answer_requirements
        document_spans = prepared.document_spans
        relevance_text = prepared.relevance_text
        route_plan = prepared.route_plan
        analysis_laws = list(prepared.analysis_laws)

        fallback_used = False
        coverage_fallback_used = False
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
        if current_turn and (repeat_current_turn or answer_requirements):
            requirement_text = "\n".join(
                f"- {value}" for value in answer_requirements
            )
            if repeat_current_turn:
                analysis_case += (
                    f"\n\nKASUTAJA VIIMANE SÕNUM:\n{current_turn[:3000]}\n\n"
                    "Kasuta viimast sõnumit koos alltoodud vastusekohustustega. "
                    "Varasem tekst on ainult taust."
                )
            if requirement_text:
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
        if route_plan.employment_form_question and not form_answer_complete:
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

        cited_ids = set(verified_sources)
        cited_laws = [
            law for law in analysis_laws
            if str(law.get("id", "")).upper() in cited_ids
        ]
        answer_relevance = self.relevance_verifier.verify_answer(
            relevance_text, analysis_text, cited_laws
        )
        if not answer_relevance.relevant and not fallback_used:
            self.logger.warning(
                "AI response failed semantic relevance check; "
                "returning verified source digest"
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

        pipeline.complete(
            "model_analysis",
            fallback=fallback_used,
            mock=is_mock,
            structured_claim_count=len(structured_claims),
        )
        pipeline.complete(
            "source_verification",
            citation_valid=is_valid,
            semantic_relevance=answer_relevance.relevant,
            verified_source_count=len(verified_sources),
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
            verified_sources=list(verified_sources),
        )
