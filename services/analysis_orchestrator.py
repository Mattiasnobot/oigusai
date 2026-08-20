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
