"""
ÕigusAI v0.9.1 - Offline Legal Analysis System
Põhimõte: NO SOURCE -> NO LEGAL CLAIM
"""
import base64
import binascii
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
import requests
import uvicorn

from config import load_settings
from services.case_intake import CaseIntakeService, MAX_INPUT_CHARS
from services.case_workspace import CaseCardBuilder, UrgencyAnalyzer
from services.analysis_pipeline import AnalysisPipelineRun, VerifiedAnswerBuilder
from services.document_insights import SafeDraftService
from services.documents import DocumentProcessingError, LocalDocumentService
from services.feedback import FeedbackStore
from services.legal_search import (
    HistoricalDataUnavailableError,
    LegalDataUnavailableError,
    LegalSearchService,
    QueryUnderstandingUnavailableError,
)
from services.offline_ai import OfflineAIService
from services.matters import MatterNotFoundError, MatterStore
from services.metrics import QualityMetricsStore
from services.runtime_guard import (
    RateLimitExceededError,
    RuntimeGuard,
    WorkQueueFullError,
    WorkQueueTimeoutError,
)
from services.retrieval_policy import RetrievalPolicy
from services.turn_planner import ConversationTurnPlanner
from verifiers.relevance_verifier import RelevanceVerifier
from verifiers.evidence_verifier import EvidenceVerifier
from verifiers.source_verifier import SourceVerifier

settings = load_settings()
logging.basicConfig(level=getattr(logging, settings.log_level))
logger = logging.getLogger(__name__)
relevance_verifier = RelevanceVerifier()
evidence_verifier = EvidenceVerifier()
case_card_builder = CaseCardBuilder()
urgency_analyzer = UrgencyAnalyzer()
verified_answer_builder = VerifiedAnswerBuilder()
safe_draft_service = SafeDraftService()

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
APP_VERSION = "0.9.1"
ACCESS_CODE_HEADER = "X-OigusAI-Access-Code"


def _ollama_readiness() -> dict:
    """Return bounded local-model readiness without making startup depend on it."""
    try:
        response = requests.get(f"{settings.ollama_host}/api/tags", timeout=2)
        response.raise_for_status()
        payload = response.json()
        available = {
            str(item.get("name") or item.get("model") or "").strip().casefold()
            for item in payload.get("models", [])
            if isinstance(item, dict)
        }
    except Exception as exc:
        return {
            "ollama_ready": False,
            "analysis_model_ready": False,
            "ocr_model_ready": False,
            "ollama_error": str(exc)[:240],
        }

    def has_model(configured: str) -> bool:
        wanted = str(configured or "").strip().casefold()
        if wanted in available:
            return True
        if ":" not in wanted:
            return any(name.split(":", 1)[0] == wanted for name in available)
        return False

    return {
        "ollama_ready": True,
        "analysis_model_ready": has_model(settings.ollama_model),
        "ocr_model_ready": has_model(settings.ollama_vision_model),
        "ollama_error": None,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services once without making application import depend on legal-data availability."""
    app.state.runtime_guard = RuntimeGuard(
        access_code=settings.app_access_code,
        rate_limit_per_minute=settings.app_rate_limit_per_minute,
        upload_limit_per_minute=settings.app_upload_limit_per_minute,
        max_concurrent_work=settings.app_max_concurrent_work,
        max_queued_work=settings.app_max_queued_work,
        queue_timeout=settings.app_queue_timeout,
    )
    app.state.legal_service = None
    app.state.legal_service_error = None

    try:
        app.state.legal_service = LegalSearchService(
            use_riigi_teataja=settings.allow_live_rt_fallback,
            data_file=settings.legal_data_file,
        )
        logger.info("Legal corpus initialized with %d sections", len(app.state.legal_service.laws))
    except (LegalDataUnavailableError, QueryUnderstandingUnavailableError) as exc:
        app.state.legal_service_error = str(exc)
        logger.error("Legal corpus unavailable: %s", exc)

    # OfflineAIService loeb sama keskse config.py ise. Konstruktorisse ei
    # süstita Settings objekti, et vältida tugevat versioonisõltuvust main.py
    # ja teenuse vahel.
    app.state.ai_service = OfflineAIService()

    required_ai_config = (
        "temperature",
        "top_p",
        "num_ctx",
        "num_predict",
        "think",
        "keep_alive",
        "citation_retries",
    )
    missing_ai_config = [
        name for name in required_ai_config if not hasattr(app.state.ai_service, name)
    ]
    if missing_ai_config:
        raise RuntimeError(
            "ÕigusAI failid on eri versioonidest: services/offline_ai.py on liiga vana "
            "v0.9.1 konfiguratsiooni jaoks. Puuduvad väljad: "
            + ", ".join(missing_ai_config)
            + ". Asenda kogu services/ kaust v0.9.1 paketist."
        )
    app.state.intake_service = CaseIntakeService(app.state.ai_service)
    app.state.verifier = SourceVerifier()
    app.state.document_service = LocalDocumentService(
        ollama_host=settings.ollama_host,
        vision_model=settings.ollama_vision_model,
        timeout=settings.ollama_ocr_timeout,
    )
    app.state.matter_store = MatterStore(ttl_minutes=settings.matter_ttl_minutes)
    app.state.feedback_store = FeedbackStore()
    app.state.metrics_store = QualityMetricsStore()
    yield


app = FastAPI(
    title=f"ÕigusAI v{APP_VERSION}",
    description="Kasutajasõbralik Eesti õiguse lokaalne analüüs kontrollitud viidetega",
    lifespan=lifespan,
    docs_url=None if settings.app_access_code else "/docs",
    redoc_url=None if settings.app_access_code else "/redoc",
    openapi_url=None if settings.app_access_code else "/openapi.json",
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path != "/health":
        response.headers.setdefault("Cache-Control", "no-store")
    metrics = getattr(request.app.state, "metrics_store", None)
    if metrics is not None:
        route_group = request.url.path.strip("/").split("/", 1)[0] or "root"
        metrics.record_request(route_group, response.status_code)
    response.headers["Server-Timing"] = (
        f"app;dur={(time.perf_counter() - started) * 1000:.1f}"
    )
    return response


class LawReference(BaseModel):
    id: str
    title: str
    source: str


class EvidenceSourceResponse(BaseModel):
    kind: str
    id: str
    title: str = ""
    source: str = ""
    evidence: str
    page: Optional[int] = None
    document_id: str = ""
    start: Optional[int] = None
    end: Optional[int] = None
    method: str = ""


class EvidenceClaimResponse(BaseModel):
    claim_id: str
    kind: str
    text: str
    verification_status: str
    sources: List[EvidenceSourceResponse] = Field(default_factory=list)


class CaseAnalysisRequest(BaseModel):
    case_description: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)
    event_date: Optional[str] = None
    search_query: Optional[str] = Field(default=None, max_length=2000)
    case_context: Optional[str] = Field(default=None, max_length=6000)
    current_message: Optional[str] = Field(default=None, max_length=10_000)
    answer_requirements: List[str] = Field(default_factory=list)
    matter_id: Optional[str] = Field(default=None, max_length=64)
    document_ids: List[str] = Field(default_factory=list, max_length=20)


class DocumentUploadRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=17_000_000)
    matter_id: Optional[str] = Field(default=None, max_length=64)


class DocumentInfoResponse(BaseModel):
    document_id: str
    file_name: str
    sha256: str
    file_type: str
    byte_size: int
    page_count: int
    text_length: int
    extraction_method: str
    warnings: List[str] = Field(default_factory=list)
    span_count: int
    insights: Dict[str, Any] = Field(default_factory=dict)


class MatterResponse(BaseModel):
    matter_id: str
    title: str
    created_at: str
    updated_at: str
    case_card: Dict[str, Any] = Field(default_factory=dict)
    documents: List[DocumentInfoResponse] = Field(default_factory=list)


class DocumentUploadResponse(BaseModel):
    matter: MatterResponse
    document: DocumentInfoResponse


class CaseIntakeRequest(BaseModel):
    case_description: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)
    event_date: Optional[str] = None
    current_message: Optional[str] = Field(default=None, max_length=10_000)
    matter_id: Optional[str] = Field(default=None, max_length=64)


class IntakePartyResponse(BaseModel):
    role: str = ""
    label: str = ""
    evidence: str = ""


class IntakeEventResponse(BaseModel):
    date: str = ""
    actor: str = ""
    action: str = ""
    evidence: str = ""


class IntakeAmountResponse(BaseModel):
    label: str = ""
    value: str = ""
    evidence: str = ""


class IntakeDocumentResponse(BaseModel):
    name: str = ""
    evidence: str = ""


class CaseIntakeResponse(BaseModel):
    input_type: str
    topic: str
    summary: str
    user_goal: str
    help_types: List[str] = Field(default_factory=list)
    parties: List[IntakePartyResponse] = Field(default_factory=list)
    events: List[IntakeEventResponse] = Field(default_factory=list)
    amounts: List[IntakeAmountResponse] = Field(default_factory=list)
    documents: List[IntakeDocumentResponse] = Field(default_factory=list)
    missing_facts: List[str] = Field(default_factory=list)
    clarification_questions: List[str] = Field(default_factory=list)
    ready_for_analysis: bool
    search_query: str
    analysis_context: str
    input_length: int
    used_ai: bool
    current_intents: List[str] = Field(default_factory=list)
    next_action: str = "analyze"
    decision_reason: str = ""
    answer_requirements: List[str] = Field(default_factory=list)
    turn_summary: str = ""
    matter_id: str = ""
    case_card: Dict[str, Any] = Field(default_factory=dict)


class QueryMatchResponse(BaseModel):
    original: str
    candidate: str
    score: float
    domains: List[str] = Field(default_factory=list)
    reason: str


class QueryInterpretationResponse(BaseModel):
    expanded_tokens: List[str] = Field(default_factory=list)
    domain_hints: List[str] = Field(default_factory=list)
    section_hints: List[str] = Field(default_factory=list)
    matches: List[QueryMatchResponse] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class CaseAnalysisResponse(BaseModel):
    analysis: str
    sources_used: List[str]
    verification_status: str
    is_mock: bool
    warning: Optional[str] = None
    found_laws: List[LawReference] = Field(default_factory=list)
    query_interpretation: Optional[QueryInterpretationResponse] = None
    claims: List[EvidenceClaimResponse] = Field(default_factory=list)
    layered_answer: Dict[str, Any] = Field(default_factory=dict)
    pipeline: Dict[str, Any] = Field(default_factory=dict)


class MatterCreateRequest(BaseModel):
    title: str = Field(default="Uus juhtum", max_length=120)


class CaseCardPatchRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    changes: Dict[str, Any] = Field(default_factory=dict)


class DraftRequest(BaseModel):
    draft_type: str = Field(min_length=1, max_length=40)


class DraftResponse(BaseModel):
    draft_type: str
    title: str
    body: str
    placeholders_present: bool
    warning: str


class FeedbackRequest(BaseModel):
    rating: str = Field(min_length=1, max_length=24)
    verification_status: str = Field(default="", max_length=48)


def get_legal_service(request: Request) -> LegalSearchService:
    service = getattr(request.app.state, "legal_service", None)
    if service is None:
        detail = getattr(request.app.state, "legal_service_error", None) or (
            "Õigusandmete korpus ei ole saadaval. Käivita esmalt Riigi Teataja importer."
        )
        raise HTTPException(status_code=503, detail=detail)
    return service


def get_ai_service(request: Request) -> OfflineAIService:
    return request.app.state.ai_service


def get_verifier(request: Request) -> SourceVerifier:
    return request.app.state.verifier


def get_intake_service(request: Request) -> CaseIntakeService:
    return request.app.state.intake_service


def _client_key(request: Request) -> str:
    # Do not trust X-Forwarded-For on a directly exposed local server.
    return request.client.host if request.client else "unknown"


def _runtime_guard(request: Request) -> RuntimeGuard:
    guard = getattr(request.app.state, "runtime_guard", None)
    if guard is None:
        raise HTTPException(status_code=503, detail="Teenuse kaitsekiht ei ole valmis.")
    return guard


def _apply_rate_limit(request: Request, scope: str, limit: int) -> RuntimeGuard:
    guard = _runtime_guard(request)
    try:
        guard.check_rate(_client_key(request), scope, limit)
    except RateLimitExceededError as exc:
        raise HTTPException(
            status_code=429,
            detail=(
                "Päringuid tuli korraga liiga palju. Oota palun hetk ja proovi uuesti."
            ),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    return guard


async def protect_api_request(request: Request) -> None:
    guard = _runtime_guard(request)
    supplied = request.headers.get(ACCESS_CODE_HEADER, "")
    if not guard.authorized(supplied):
        raise HTTPException(status_code=401, detail="Juurdepääsukood puudub või on vale.")
    _apply_rate_limit(request, "api", settings.app_rate_limit_per_minute)


async def protect_upload_request(request: Request) -> None:
    guard = _runtime_guard(request)
    supplied = request.headers.get(ACCESS_CODE_HEADER, "")
    if not guard.authorized(supplied):
        raise HTTPException(status_code=401, detail="Juurdepääsukood puudub või on vale.")
    _apply_rate_limit(request, "upload", settings.app_upload_limit_per_minute)


async def _run_guarded_work(label: str, func, *args):
    guard = getattr(app.state, "runtime_guard", None)
    if guard is None:
        return await run_in_threadpool(func, *args)
    try:
        async with guard.work_slot(label):
            return await run_in_threadpool(func, *args)
    except WorkQueueFullError as exc:
        raise HTTPException(
            status_code=429,
            detail="ÕigusAI tegeleb praegu teiste vastustega. Proovi palun mõne hetke pärast uuesti.",
            headers={"Retry-After": "15"},
        ) from exc
    except WorkQueueTimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail="Vastuse ooteaeg sai täis. Sinu tekst on alles; proovi palun uuesti.",
        ) from exc


@app.post("/access/check")
async def check_access(request: Request):
    guard = _apply_rate_limit(request, "access", 10)
    supplied = request.headers.get(ACCESS_CODE_HEADER, "")
    if not guard.authorized(supplied):
        raise HTTPException(status_code=401, detail="Juurdepääsukood ei ole õige.")
    return {"ok": True, "protected": guard.access_required}


@app.post("/feedback")
async def save_feedback(
    request: Request,
    payload: FeedbackRequest,
    _access: None = Depends(protect_api_request),
):
    store = getattr(request.app.state, "feedback_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Tagasiside ei ole praegu saadaval.")
    try:
        return store.record(payload.rating, payload.verification_status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(
        request,
        "chat.html",
        {
            "request": request,
            "access_required": bool(settings.app_access_code),
        },
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/admin", response_class=HTMLResponse)
async def read_admin(request: Request):
    return templates.TemplateResponse(
        request,
        "admin.html",
        {"request": request, "version": APP_VERSION},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/admin/metrics")
async def admin_metrics(
    request: Request,
    _access: None = Depends(protect_api_request),
):
    metrics = getattr(request.app.state, "metrics_store", None)
    feedback = getattr(request.app.state, "feedback_store", None)
    guard = getattr(request.app.state, "runtime_guard", None)
    matters = getattr(request.app.state, "matter_store", None)
    return {
        "version": APP_VERSION,
        "metrics": metrics.snapshot() if metrics is not None else {},
        "feedback": feedback.snapshot() if feedback is not None else {},
        "work_queue": guard.snapshot() if guard is not None else {},
        "active_matters": matters.count() if matters is not None else 0,
        "retains_user_text": False,
    }


@app.get("/health")
async def health(request: Request):
    """Report trusted-corpus readiness and the optional V6 dense-search state."""
    legal_service = getattr(request.app.state, "legal_service", None)
    hybrid_status = (
        legal_service.hybrid_status()
        if legal_service is not None
        else {
            "enabled": settings.hybrid_retrieval_enabled,
            "ready": False,
            "embedding_model": settings.embedding_model,
            "embedding_dimension": 0,
            "vector_rows": 0,
            "error": "Õiguskorpus ei ole valmis.",
        }
    )
    reranker_status = (
        legal_service.reranker_status()
        if legal_service is not None
        else {
            "enabled": settings.reranker_enabled,
            "loaded": False,
            "ready": False,
            "model": settings.reranker_model,
            "device": settings.reranker_device,
            "candidates": settings.reranker_candidates,
            "error": "Õiguskorpus ei ole valmis.",
        }
    )
    model_status = await run_in_threadpool(_ollama_readiness)
    runtime_guard = getattr(request.app.state, "runtime_guard", None)
    queue_status = runtime_guard.snapshot() if runtime_guard is not None else {
        "active": 0,
        "waiting": 0,
        "max_concurrent": settings.app_max_concurrent_work,
        "max_queued": settings.app_max_queued_work,
    }
    matter_store = getattr(request.app.state, "matter_store", None)
    feedback_store = getattr(request.app.state, "feedback_store", None)
    metrics_store = getattr(request.app.state, "metrics_store", None)
    ready_for_demo = bool(
        legal_service is not None and model_status["analysis_model_ready"]
    )
    return {
        "status": "ok" if ready_for_demo else "degraded",
        "version": APP_VERSION,
        "ready_for_demo": ready_for_demo,
        "legal_corpus_ready": legal_service is not None,
        "legal_sections": len(legal_service.laws) if legal_service is not None else 0,
        "query_understanding_ready": bool(
            legal_service is not None
            and getattr(legal_service, "query_understanding", None)
            and legal_service.query_understanding.enabled
        ),
        "query_vocabulary_terms": (
            legal_service.query_understanding.vocabulary_size
            if legal_service is not None and getattr(legal_service, "query_understanding", None)
            else 0
        ),
        "case_intake_ready": getattr(request.app.state, "intake_service", None) is not None,
        "document_ingest_ready": getattr(request.app.state, "document_service", None) is not None,
        "document_privacy": "memory_only",
        "matter_ttl_minutes": settings.matter_ttl_minutes,
        "active_matters": matter_store.count() if matter_store is not None else 0,
        "feedback_received": (
            feedback_store.snapshot()["total"] if feedback_store is not None else 0
        ),
        "quality_metrics": metrics_store.snapshot() if metrics_store is not None else {},
        "capabilities": {
            "v8_2_case_card": True,
            "v8_2_layered_answer": True,
            "v8_2_urgency": True,
            "v8_3_document_insights": True,
            "v8_3_safe_drafts": True,
            "v9_0_verified_pipeline": True,
            "v9_1_quality_dashboard": True,
        },
        "access_protected": bool(settings.app_access_code),
        "transport_security": "https_required_for_public_use",
        "work_queue": queue_status,
        "ocr_model": settings.ollama_vision_model,
        "analysis_model": settings.ollama_model,
        **model_status,
        "hybrid_enabled": hybrid_status["enabled"],
        "hybrid_ready": hybrid_status["ready"],
        "embedding_model": hybrid_status["embedding_model"],
        "embedding_dimension": hybrid_status["embedding_dimension"],
        "vector_rows": hybrid_status["vector_rows"],
        "hybrid_error": hybrid_status["error"],
        "reranker_enabled": reranker_status["enabled"],
        "reranker_loaded": reranker_status["loaded"],
        "reranker_ready": reranker_status["ready"],
        "reranker_model": reranker_status["model"],
        "reranker_device": reranker_status["device"],
        "reranker_candidates": reranker_status["candidates"],
        "reranker_error": reranker_status["error"],
        "legal_corpus_error": getattr(request.app.state, "legal_service_error", None),
    }


@app.post("/documents", response_model=DocumentUploadResponse)
async def upload_document(
    request: Request,
    payload: DocumentUploadRequest,
    _access: None = Depends(protect_upload_request),
):
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Dokumendi sisu ei ole kehtiv base64.") from exc
    service = getattr(request.app.state, "document_service", None)
    store = getattr(request.app.state, "matter_store", None)
    if service is None or store is None:
        raise HTTPException(status_code=503, detail="Dokumenditöötlus ei ole valmis.")
    try:
        document = await _run_guarded_work(
            "document", service.process, payload.file_name, content
        )
        result = store.add_document(payload.matter_id, document)
    except DocumentProcessingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MatterNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Juhtumit ei leitud.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DocumentUploadResponse(**result)


@app.post("/matters", response_model=MatterResponse)
async def create_matter(
    request: Request,
    payload: MatterCreateRequest,
    _access: None = Depends(protect_api_request),
):
    store = getattr(request.app.state, "matter_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Juhtumiregister ei ole valmis.")
    return MatterResponse(**store.create(payload.title))


@app.get("/matters/{matter_id}", response_model=MatterResponse)
async def get_matter(
    request: Request,
    matter_id: str,
    _access: None = Depends(protect_api_request),
):
    store = getattr(request.app.state, "matter_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Juhtumiregister ei ole valmis.")
    try:
        return MatterResponse(**store.get(matter_id))
    except MatterNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Juhtumit ei leitud.") from exc


@app.patch("/matters/{matter_id}/case-card", response_model=MatterResponse)
async def patch_case_card(
    request: Request,
    matter_id: str,
    payload: CaseCardPatchRequest,
    _access: None = Depends(protect_api_request),
):
    store = getattr(request.app.state, "matter_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Juhtumiregister ei ole valmis.")
    try:
        current = store.case_card(matter_id)
        revised = case_card_builder.revise(
            current, payload.changes, payload.expected_revision
        )
        store.update_case_card(matter_id, revised)
        return MatterResponse(**store.get(matter_id))
    except MatterNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Juhtumit ei leitud.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/matters/{matter_id}/drafts", response_model=DraftResponse)
async def create_draft(
    request: Request,
    matter_id: str,
    payload: DraftRequest,
    _access: None = Depends(protect_api_request),
):
    store = getattr(request.app.state, "matter_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Juhtumiregister ei ole valmis.")
    try:
        card = store.case_card(matter_id)
        documents = store.documents(matter_id)
        return DraftResponse(**safe_draft_service.build(
            payload.draft_type, card, documents
        ))
    except MatterNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Juhtumit ei leitud.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/matters/{matter_id}")
async def delete_matter(
    request: Request,
    matter_id: str,
    _access: None = Depends(protect_api_request),
):
    store = getattr(request.app.state, "matter_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Juhtumiregister ei ole valmis.")
    return {"deleted": bool(store.delete(matter_id))}


@app.post("/intake", response_model=CaseIntakeResponse)
async def understand_case(
    request: CaseIntakeRequest,
    intake_service: CaseIntakeService = Depends(get_intake_service),
    _access: None = Depends(protect_api_request),
):
    try:
        result = await _run_guarded_work(
            "intake",
            intake_service.understand,
            request.case_description,
            request.event_date or "",
            request.current_message or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store = getattr(app.state, "matter_store", None)
    matter_id = str(request.matter_id or "").strip()
    previous_card = {}
    if store is not None:
        try:
            if matter_id:
                previous_card = store.case_card(matter_id)
            else:
                matter_id = store.create(result.get("topic") or "Uus juhtum")["matter_id"]
            card = case_card_builder.from_intake(result, previous_card)
            store.update_case_card(matter_id, card)
        except MatterNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Juhtumit ei leitud.") from exc
    else:
        card = case_card_builder.from_intake(result)
    result["matter_id"] = matter_id
    result["case_card"] = card
    return CaseIntakeResponse(**result)


@app.post("/analyze", response_model=CaseAnalysisResponse)
async def analyze_case(
    request: CaseAnalysisRequest,
    legal_service: LegalSearchService = Depends(get_legal_service),
    ai_service: OfflineAIService = Depends(get_ai_service),
    verifier: SourceVerifier = Depends(get_verifier),
    _access: None = Depends(protect_api_request),
):
    analysis_started = time.perf_counter()
    pipeline = AnalysisPipelineRun()
    if not request.case_description or not request.case_description.strip():
        raise HTTPException(status_code=400, detail="Olukorra kirjeldus ei tohi olla tühi.")

    current_turn = (request.current_message or "").strip()
    answer_requirements = [
        str(value).strip()
        for value in request.answer_requirements
        if str(value).strip()
    ][:5]
    # A short clarification answer such as "Ma ei tea" is not the legal
    # question itself.  The intake layer has already preserved that question as
    # answer requirements, so use both inputs for routing and final coverage.
    intent_focus = "\n".join([current_turn, *answer_requirements]).strip()
    current_intents = ConversationTurnPlanner.detect_intents(intent_focus)
    fine_context = ConversationTurnPlanner.is_fine_context(request.case_description)
    pipeline.complete(
        "case_understanding",
        current_intent_count=len(current_intents),
        answer_requirement_count=len(answer_requirements),
    )

    document_spans = []
    case_card: Dict[str, Any] = {}
    if request.matter_id:
        store = getattr(app.state, "matter_store", None)
        if store is None:
            raise HTTPException(status_code=503, detail="Juhtumiregister ei ole valmis.")
        try:
            case_card = store.case_card(request.matter_id)
            document_spans = store.relevant_spans(
                request.matter_id,
                request.document_ids,
                intent_focus or request.search_query or request.case_description,
                limit=5,
            )
        except MatterNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Juhtumit ei leitud.") from exc
    pipeline.complete(
        "document_evidence",
        span_count=len(document_spans),
        matter_attached=bool(request.matter_id),
    )

    try:
        search_text = (request.search_query or request.case_description).strip()
        if document_spans:
            document_search = " ".join(
                span["text"][:350] for span in document_spans[:4]
            )
            search_text = f"{search_text} {document_search}"[:2000].strip()
        laws, query_interpretation = await _run_guarded_work(
            "retrieval",
            legal_service.search_laws_with_context,
            search_text,
            request.event_date or "",
        )
    except HistoricalDataUnavailableError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not laws:
        raise HTTPException(
            status_code=404,
            detail=(
                "Ma ei leidnud veel piisavalt täpset õigusallikat. Lisa palun, "
                "kes tegi mida, millal see juhtus ja millist abi vajad."
            ),
        )

    query_context = query_interpretation.to_dict()
    hinted_id_list = [
        str(value).upper() for value in query_context.get("section_hints", [])
    ]
    hinted_ids = set(hinted_id_list)
    analysis_laws = laws
    if hinted_ids:
        # Section hints activate only on exact, audited lexicon phrases. When
        # such a hint exists, pass only those exact audited sections to the
        # model. A domain-level filter is too broad: adjacent sections in the
        # same act can be genuine sources yet completely unrelated to the case.
        available_by_id = {
            str(law.get("id", "")).upper(): law for law in laws
        }
        hinted_laws = []
        for section_id in hinted_id_list[:16]:
            law = available_by_id.get(section_id)
            if law is None and isinstance(legal_service, LegalSearchService):
                try:
                    law = legal_service.get_law_by_id(section_id)
                except ValueError:
                    law = None
            if law is not None:
                hinted_laws.append(law)
        if hinted_laws:
            analysis_laws = hinted_laws

    # Auditable routing policy only proposes section IDs. Every ID is still
    # resolved below from the trusted corpus before it can reach the model.
    route_plan = RetrievalPolicy.plan(
        case_description=request.case_description,
        search_text=search_text,
        current_intents=current_intents,
        fine_context=fine_context,
    )
    routed_ids = list(route_plan.routed_ids)
    if routed_ids:
        routed_by_id = {
            str(law.get("id", "")).upper(): law for law in analysis_laws
        }
        routed_laws = []
        for section_id in routed_ids:
            law = routed_by_id.get(section_id)
            if law is None and isinstance(legal_service, LegalSearchService):
                try:
                    law = legal_service.get_law_by_id(section_id)
                except ValueError:
                    law = None
            if law is not None:
                routed_laws.append(law)
        if routed_laws:
            analysis_laws = routed_laws

    relevance_text = intent_focus or search_text
    if fine_context and not ConversationTurnPlanner.is_fine_context(relevance_text):
        relevance_text = f"rahatrahv {relevance_text}".strip()
    source_relevance = relevance_verifier.verify_laws(relevance_text, analysis_laws)
    if not source_relevance.relevant:
        logger.warning(
            "Retrieved laws failed semantic relevance check for concepts: %s",
            ", ".join(source_relevance.missing_concepts),
        )
        raise HTTPException(
            status_code=422,
            detail=source_relevance.clarification
            or relevance_verifier.clarification_for(source_relevance),
        )
    pipeline.complete(
        "legal_retrieval",
        result_count=len(analysis_laws),
        semantic_relevance=True,
        hybrid_used=bool(getattr(legal_service, "hybrid_ready", False)),
    )

    fallback_used = False
    coverage_fallback_used = False
    # The structured intake summary may deliberately omit a legally decisive
    # word (for example "abipolitsei"). Keep the original account for ordinary
    # inputs and use the compact summary only to make very long inputs fit.
    analysis_case = CaseIntakeService._user_evidence_text(
        request.case_description
    ) or request.case_description.strip()
    if len(analysis_case) > 6000:
        if request.case_context and request.case_context.strip():
            analysis_case = (
                f"Kasutaja algteksti algus:\n{analysis_case[:3000]}\n\n"
                f"Kontrollitud juhtumikokkuvõte:\n"
                f"{request.case_context.strip()}"
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
    document_claims = []
    for index, span in enumerate(document_spans[:4], start=1):
        excerpt = LocalDocumentService.focused_excerpt(span, relevance_text)
        method = str(span.get("method") or "text")
        document_claims.append({
            "claim_id": f"DOC-{index}",
            "kind": "document_excerpt",
            "text": excerpt["text"],
            "verification_status": (
                "OCR_REVIEW_REQUIRED" if method == "ocr" else "DOCUMENT_TEXT_VERIFIED"
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
    structured_claims = []
    try:
        if isinstance(ai_service, OfflineAIService):
            analysis_text, is_mock, structured_claims = await _run_guarded_work(
                "analysis",
                ai_service.analyze_case_structured,
                model_case,
                analysis_laws,
                request.event_date or "",
                document_spans,
            )
        else:
            analysis_text, is_mock = await _run_guarded_work(
                "analysis",
                ai_service.analyze_case,
                model_case,
                analysis_laws,
                request.event_date or "",
            )
    except Exception as exc:
        logger.error("AI analysis unavailable; returning verified source digest: %s", exc)
        analysis_text = ai_service.build_source_only_fallback(
            analysis_case, analysis_laws
        )
        if isinstance(ai_service, OfflineAIService):
            structured_claims = ai_service.claims_from_verified_analysis(
                analysis_text, analysis_laws
            )
        is_mock = False
        fallback_used = True

    # Source and citation checks alone do not prove that the answer addressed
    # the decisive part of the user's question.  For an employment termination
    # form question, require both halves of TLS § 95: the reproducible written
    # form and the consequence of violating that form requirement.  If the
    # model omitted either one, answer deterministically from that single
    # audited section instead of returning a broadly related employment rule.
    employment_form_question = route_plan.employment_form_question
    normalized_answer = " ".join(analysis_text.casefold().split())
    form_answer_complete = (
        "kirjalikku taasesitamist võimaldavas vormis" in normalized_answer
        and "tühine" in normalized_answer
    )
    if employment_form_question and not form_answer_complete:
        form_law = next(
            (
                law for law in analysis_laws
                if str(law.get("id", "")).upper() == "TLS_95"
            ),
            None,
        )
        if form_law is None and isinstance(legal_service, LegalSearchService):
            try:
                form_law = legal_service.get_law_by_id("TLS_95")
            except ValueError:
                form_law = None
        if form_law is None:
            logger.error("TLS_95 missing for mandatory employment form coverage")
            raise HTTPException(
                status_code=500,
                detail="Töölepingu ülesütlemise vorminõude allikas puudub.",
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
        logger.warning(
            "Model answer missed mandatory employment form coverage; "
            "returning verified TLS_95 answer"
        )

    pipeline.complete(
        "model_analysis",
        fallback=fallback_used,
        mock=is_mock,
        structured_claim_count=len(structured_claims),
    )

    is_valid, verified_sources = verifier.verify_sources(analysis_text, analysis_laws)

    if not is_valid and not fallback_used:
        logger.warning(
            "AI response failed citation verification; returning verified source digest"
        )
        analysis_text = ai_service.build_source_only_fallback(
            analysis_case, analysis_laws
        )
        if isinstance(ai_service, OfflineAIService):
            structured_claims = ai_service.claims_from_verified_analysis(
                analysis_text, analysis_laws
            )
        is_valid, verified_sources = verifier.verify_sources(analysis_text, analysis_laws)
        fallback_used = True

    if not is_valid:
        logger.error("Deterministic source digest failed citation verification")
        raise HTTPException(status_code=500, detail="Kontrollitud allikate kuvamine ebaõnnestus.")

    cited_ids = set(verified_sources)
    cited_laws = [
        law for law in analysis_laws
        if str(law.get("id", "")).upper() in cited_ids
    ]
    answer_relevance = relevance_verifier.verify_answer(
        relevance_text, analysis_text, cited_laws
    )
    if not answer_relevance.relevant and not fallback_used:
        logger.warning(
            "AI response failed semantic relevance check; returning verified source digest"
        )
        analysis_text = ai_service.build_source_only_fallback(
            analysis_case, analysis_laws
        )
        if isinstance(ai_service, OfflineAIService):
            structured_claims = ai_service.claims_from_verified_analysis(
                analysis_text, analysis_laws
            )
        is_valid, verified_sources = verifier.verify_sources(
            analysis_text, analysis_laws
        )
        fallback_used = True
        cited_ids = set(verified_sources)
        cited_laws = [
            law for law in analysis_laws
            if str(law.get("id", "")).upper() in cited_ids
        ]
        answer_relevance = relevance_verifier.verify_answer(
            relevance_text, analysis_text, cited_laws
        )

    if not is_valid:
        logger.error("Relevance fallback failed citation verification")
        raise HTTPException(
            status_code=500,
            detail="Kontrollitud allikate kuvamine ebaõnnestus.",
        )

    if not answer_relevance.relevant:
        logger.warning(
            "Final answer failed semantic relevance check for concepts: %s",
            ", ".join(answer_relevance.missing_concepts),
        )
        raise HTTPException(
            status_code=422,
            detail=answer_relevance.clarification
            or relevance_verifier.clarification_for(answer_relevance),
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

    warning = (
        "Viidete ID-d ja mudeli valitud tõendikatkendid on kontrollitud etteantud "
        "õigusallikate vastu, kuid see ei tõenda automaatselt iga järelduse materiaalset "
        "õigsust. Tegemist on esmase analüüsiga, mitte õigusnõuga."
    )
    if is_mock:
        warning = "TESTREŽIIM: Ollama vastus on näidisvastus. " + warning
    if coverage_fallback_used:
        warning = (
            "Vastus on piiritletud töölepingu ülesütlemise vorminõudega ja põhineb "
            "kontrollitud TLS §-l 95. Tegemist on esmase selgituse, mitte õigusnõuga."
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

    combined_claims = [*document_claims, *structured_claims]
    evidence_valid = False
    if combined_claims:
        evidence_valid, combined_claims = evidence_verifier.verify(
            combined_claims,
            analysis_laws,
            document_spans,
        )
        if not evidence_valid:
            logger.error("Structured evidence failed the V7 API boundary")
            raise HTTPException(
                status_code=500,
                detail="Kontrollitud tõendite kuvamine ebaõnnestus.",
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

    final_verification_status = (
        "SOURCE_ONLY_FALLBACK"
        if fallback_used
        else "OCR_REVIEW_REQUIRED" if has_ocr_evidence
        else "INPUTS_VERIFIED" if has_cross_source_comparison
        else "EVIDENCE_VERIFIED" if evidence_valid and structured_claims
        and all(
            claim.get("verification_status") == "EVIDENCE_VERIFIED"
            for claim in structured_claims
        )
        else "CITATIONS_VERIFIED"
    )
    urgency = urgency_analyzer.analyze(
        request.case_description,
        event_date=request.event_date or "",
        document_spans=document_spans,
    )
    layered_answer = verified_answer_builder.build(
        analysis=analysis_text,
        claims=combined_claims,
        verification_status=final_verification_status,
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
    metrics = getattr(app.state, "metrics_store", None)
    if metrics is not None:
        metrics.record_analysis(
            duration_ms=(time.perf_counter() - analysis_started) * 1000,
            verification_status=final_verification_status,
            fallback=fallback_used,
            claim_count=len(combined_claims),
            source_count=len(verified_sources),
        )

    return CaseAnalysisResponse(
        analysis=analysis_text,
        sources_used=verified_sources,
        verification_status=final_verification_status,
        is_mock=is_mock,
        warning=warning,
        found_laws=[
            LawReference(id=law["id"], title=law["title"], source=law["source"])
            for law in analysis_laws
        ],
        query_interpretation=QueryInterpretationResponse(**query_context),
        claims=[
            EvidenceClaimResponse(**claim)
            for claim in combined_claims
        ],
        layered_answer=layered_answer,
        pipeline=pipeline_result,
    )


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_reload,
        log_level=settings.log_level.lower(),
    )
