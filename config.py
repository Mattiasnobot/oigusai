"""ÕigusAI keskne konfiguratsioon.

Kõik kasutaja poolt muudetavad runtime/inference/otsingu/RT seaded tulevad
`.env` failist. Rakenduse koodis jäävad ainult turvalised vaikeväärtused ja
algoritmilised konstandid.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from dotenv import load_dotenv

CONFIG_SCHEMA_VERSION = 10

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(ENV_FILE)


class ConfigurationError(ValueError):
    """Raised when an environment setting is invalid."""


def _value(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name)
    return default if value is None or not str(value).strip() else str(value).strip()


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigurationError(
        f"{name} peab olema true/false (või 1/0, yes/no, on/off), mitte {raw!r}."
    )


def _int(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        value = default
    else:
        try:
            value = int(str(raw).strip())
        except ValueError as exc:
            raise ConfigurationError(f"{name} peab olema täisarv, mitte {raw!r}.") from exc
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"{name} peab olema vähemalt {minimum}, praegu {value}.")
    if maximum is not None and value > maximum:
        raise ConfigurationError(f"{name} peab olema kuni {maximum}, praegu {value}.")
    return value


def _float(
    env: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        value = default
    else:
        try:
            value = float(str(raw).strip())
        except ValueError as exc:
            raise ConfigurationError(f"{name} peab olema arv, mitte {raw!r}.") from exc
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"{name} peab olema vähemalt {minimum}, praegu {value}.")
    if maximum is not None and value > maximum:
        raise ConfigurationError(f"{name} peab olema kuni {maximum}, praegu {value}.")
    return value


def _path(env: Mapping[str, str], name: str, default: str) -> Path:
    raw = _value(env, name, default)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


@dataclass(frozen=True)
class Settings:
    # FastAPI / logging
    app_host: str
    app_port: int
    app_reload: bool
    log_level: str
    app_access_code: str
    app_rate_limit_per_minute: int
    app_upload_limit_per_minute: int
    app_max_concurrent_work: int
    app_max_queued_work: int
    app_queue_timeout: int
    matter_ttl_minutes: int

    # Ollama
    ollama_host: str
    ollama_model: str
    ollama_timeout: int
    ollama_temperature: float
    ollama_top_p: float
    ollama_num_ctx: int
    ollama_num_predict: int
    ollama_think: bool
    ollama_keep_alive: str
    ollama_citation_retries: int
    allow_mock_analysis: bool
    ollama_vision_model: str
    ollama_ocr_timeout: int

    # Legal retrieval / corpus
    legal_data_file: Path
    legal_min_score: int
    legal_max_results: int
    legal_relative_threshold: float
    allow_live_rt_fallback: bool

    # V5 query understanding / typo tolerance
    query_understanding_enabled: bool
    query_lexicon_file: Path
    query_fuzzy_threshold: float
    query_fuzzy_max_matches: int
    query_fuzzy_min_token_length: int
    query_max_expanded_terms: int
    query_compound_enabled: bool
    query_domain_hint_bonus: int
    query_curated_domain_hint_bonus: int

    # V6 hybrid retrieval / local embeddings
    hybrid_retrieval_enabled: bool
    vector_index_dir: Path
    embedding_model: str
    embedding_timeout: int
    embedding_batch_size: int
    embedding_keep_alive: str
    embedding_max_chars: int
    hybrid_dense_candidates: int
    hybrid_rrf_k: int
    hybrid_lexical_weight: float
    hybrid_dense_weight: float
    hybrid_diversity_weight: float
    hybrid_multi_query_enabled: bool
    hybrid_max_query_variants: int

    # V6.1 verified candidate reranker
    reranker_enabled: bool
    reranker_model: str
    reranker_device: str
    reranker_candidates: int
    reranker_batch_size: int
    reranker_max_length: int
    reranker_max_chars: int
    reranker_weight: float

    # Riigi Teataja
    rt_base_url: str
    rt_registry_file: Path
    rt_cache_dir: Path
    rt_service_cache_dir: Path
    rt_timeout: int
    rt_retries: int
    rt_request_delay: float
    rt_cache_ttl_days: int
    rt_user_agent: str


def load_settings(environ: Optional[Mapping[str, str]] = None) -> Settings:
    """Read and validate settings.

    `environ` exists primarily for deterministic tests. Runtime defaults to
    `os.environ`, which already includes values loaded from project `.env`.
    """
    env = os.environ if environ is None else environ

    host = _value(env, "OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    rt_base = _value(env, "RT_BASE_URL", "https://www.riigiteataja.ee").rstrip("/")
    log_level = _value(env, "LOG_LEVEL", "INFO").upper()
    if log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        raise ConfigurationError(
            "LOG_LEVEL peab olema üks: CRITICAL, ERROR, WARNING, INFO, DEBUG."
        )

    reranker_device = _value(env, "RERANKER_DEVICE", "auto").lower()
    if reranker_device not in {"auto", "cuda", "cpu"}:
        raise ConfigurationError(
            "RERANKER_DEVICE peab olema üks: auto, cuda, cpu."
        )

    access_code = _value(env, "APP_ACCESS_CODE", "")
    if access_code and len(access_code) < 8:
        raise ConfigurationError("APP_ACCESS_CODE peab olema vähemalt 8 märki pikk.")
    if len(access_code) > 128:
        raise ConfigurationError("APP_ACCESS_CODE võib olla kuni 128 märki pikk.")

    return Settings(
        app_host=_value(env, "APP_HOST", "0.0.0.0"),
        app_port=_int(env, "APP_PORT", 8000, minimum=1, maximum=65535),
        app_reload=_bool(env, "APP_RELOAD", True),
        log_level=log_level,
        app_access_code=access_code,
        app_rate_limit_per_minute=_int(
            env, "APP_RATE_LIMIT_PER_MINUTE", 30, minimum=4, maximum=600
        ),
        app_upload_limit_per_minute=_int(
            env, "APP_UPLOAD_LIMIT_PER_MINUTE", 6, minimum=1, maximum=60
        ),
        app_max_concurrent_work=_int(
            env, "APP_MAX_CONCURRENT_WORK", 1, minimum=1, maximum=8
        ),
        app_max_queued_work=_int(
            env, "APP_MAX_QUEUED_WORK", 8, minimum=0, maximum=100
        ),
        app_queue_timeout=_int(
            env, "APP_QUEUE_TIMEOUT", 360, minimum=10, maximum=1800
        ),
        matter_ttl_minutes=_int(
            env, "MATTER_TTL_MINUTES", 120, minimum=5, maximum=1440
        ),
        ollama_host=host,
        ollama_model=_value(env, "OLLAMA_MODEL", "qwen3.5:9b-q4_K_M"),
        ollama_timeout=_int(env, "OLLAMA_TIMEOUT", 300, minimum=1),
        ollama_temperature=_float(env, "OLLAMA_TEMPERATURE", 0.1, minimum=0.0, maximum=2.0),
        ollama_top_p=_float(env, "OLLAMA_TOP_P", 0.9, minimum=0.0, maximum=1.0),
        ollama_num_ctx=_int(env, "OLLAMA_NUM_CTX", 8192, minimum=512),
        ollama_num_predict=_int(env, "OLLAMA_NUM_PREDICT", 1536, minimum=64),
        ollama_think=_bool(env, "OLLAMA_THINK", False),
        ollama_keep_alive=_value(env, "OLLAMA_KEEP_ALIVE", "10m"),
        ollama_citation_retries=_int(env, "OLLAMA_CITATION_RETRIES", 2, minimum=0, maximum=3),
        allow_mock_analysis=_bool(env, "ALLOW_MOCK_ANALYSIS", False),
        ollama_vision_model=_value(env, "OLLAMA_VISION_MODEL", "llama3.2-vision"),
        ollama_ocr_timeout=_int(env, "OLLAMA_OCR_TIMEOUT", 180, minimum=10, maximum=900),
        legal_data_file=_path(env, "LEGAL_DATA_FILE", "data/laws.json"),
        legal_min_score=_int(env, "LEGAL_MIN_SCORE", 6, minimum=0),
        legal_max_results=_int(env, "LEGAL_MAX_RESULTS", 5, minimum=1, maximum=50),
        legal_relative_threshold=_float(
            env, "LEGAL_RELATIVE_THRESHOLD", 0.6, minimum=0.0, maximum=1.0
        ),
        allow_live_rt_fallback=_bool(env, "ALLOW_LIVE_RT_FALLBACK", False),
        query_understanding_enabled=_bool(env, "QUERY_UNDERSTANDING_ENABLED", True),
        query_lexicon_file=_path(env, "QUERY_LEXICON_FILE", "data/query_lexicon.json"),
        query_fuzzy_threshold=_float(
            env, "QUERY_FUZZY_THRESHOLD", 0.82, minimum=0.5, maximum=1.0
        ),
        query_fuzzy_max_matches=_int(
            env, "QUERY_FUZZY_MAX_MATCHES", 1, minimum=1, maximum=5
        ),
        query_fuzzy_min_token_length=_int(
            env, "QUERY_FUZZY_MIN_TOKEN_LENGTH", 5, minimum=3, maximum=20
        ),
        query_max_expanded_terms=_int(
            env, "QUERY_MAX_EXPANDED_TERMS", 16, minimum=1, maximum=100
        ),
        query_compound_enabled=_bool(env, "QUERY_COMPOUND_ENABLED", True),
        query_domain_hint_bonus=_int(
            env, "QUERY_DOMAIN_HINT_BONUS", 2, minimum=0, maximum=20
        ),
        query_curated_domain_hint_bonus=_int(
            env, "QUERY_CURATED_DOMAIN_HINT_BONUS", 18, minimum=0, maximum=30
        ),
        hybrid_retrieval_enabled=_bool(env, "HYBRID_RETRIEVAL_ENABLED", True),
        vector_index_dir=_path(env, "VECTOR_INDEX_DIR", "data/lancedb"),
        embedding_model=_value(env, "EMBEDDING_MODEL", "bge-m3"),
        embedding_timeout=_int(env, "EMBEDDING_TIMEOUT", 180, minimum=1),
        embedding_batch_size=_int(
            env, "EMBEDDING_BATCH_SIZE", 64, minimum=1, maximum=128
        ),
        embedding_keep_alive=_value(env, "EMBEDDING_KEEP_ALIVE", "30m"),
        embedding_max_chars=_int(
            env, "EMBEDDING_MAX_CHARS", 6000, minimum=500, maximum=24000
        ),
        hybrid_dense_candidates=_int(
            env, "HYBRID_DENSE_CANDIDATES", 60, minimum=5, maximum=500
        ),
        hybrid_rrf_k=_int(env, "HYBRID_RRF_K", 20, minimum=1, maximum=200),
        hybrid_lexical_weight=_float(
            env, "HYBRID_LEXICAL_WEIGHT", 1.0, minimum=0.0, maximum=10.0
        ),
        hybrid_dense_weight=_float(
            env, "HYBRID_DENSE_WEIGHT", 1.0, minimum=0.0, maximum=10.0
        ),
        hybrid_diversity_weight=_float(
            env, "HYBRID_DIVERSITY_WEIGHT", 0.75, minimum=0.0, maximum=10.0
        ),
        hybrid_multi_query_enabled=_bool(env, "HYBRID_MULTI_QUERY_ENABLED", True),
        hybrid_max_query_variants=_int(
            env, "HYBRID_MAX_QUERY_VARIANTS", 3, minimum=1, maximum=5
        ),
        reranker_enabled=_bool(env, "RERANKER_ENABLED", False),
        reranker_model=_value(
            env, "RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"
        ),
        reranker_device=reranker_device,
        reranker_candidates=_int(
            env, "RERANKER_CANDIDATES", 20, minimum=5, maximum=100
        ),
        reranker_batch_size=_int(
            env, "RERANKER_BATCH_SIZE", 8, minimum=1, maximum=32
        ),
        reranker_max_length=_int(
            env, "RERANKER_MAX_LENGTH", 512, minimum=128, maximum=1024
        ),
        reranker_max_chars=_int(
            env, "RERANKER_MAX_CHARS", 5000, minimum=500, maximum=16000
        ),
        reranker_weight=_float(
            env, "RERANKER_WEIGHT", 2.0, minimum=0.0, maximum=10.0
        ),
        rt_base_url=rt_base,
        rt_registry_file=_path(env, "RT_REGISTRY_FILE", "data/rt_registry.json"),
        rt_cache_dir=_path(env, "RT_CACHE_DIR", "data/.rt_cache"),
        rt_service_cache_dir=_path(env, "RT_SERVICE_CACHE_DIR", "cache/rt"),
        rt_timeout=_int(env, "RT_TIMEOUT", 45, minimum=1),
        rt_retries=_int(env, "RT_RETRIES", 3, minimum=1, maximum=10),
        rt_request_delay=_float(env, "RT_REQUEST_DELAY", 0.5, minimum=0.0, maximum=10.0),
        rt_cache_ttl_days=_int(env, "RT_CACHE_TTL_DAYS", 7, minimum=0),
        rt_user_agent=_value(
            env,
            "RT_USER_AGENT",
            "OigusAI/0.9.1 (local legal analysis prototype)",
        ),
    )
