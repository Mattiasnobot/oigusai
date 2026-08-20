"""Versioned local LanceDB index for ÕigusAI V6 hybrid retrieval."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from config import Settings
from services.embedding_service import EmbeddingServiceError, OllamaEmbeddingService

try:
    import lancedb
except ImportError:  # Runtime must retain the V5 lexical fallback.
    lancedb = None


logger = logging.getLogger(__name__)

INDEX_SCHEMA_VERSION = 1
INDEX_TABLE_NAME = "law_sections"
CURRENT_MANIFEST_FILE = "current.json"


class VectorIndexError(RuntimeError):
    """Raised when a vector index cannot be built or trusted."""


class VectorSearchUnavailableError(RuntimeError):
    """Raised when dense retrieval fails and lexical fallback must be used."""


@dataclass(frozen=True)
class DenseSearchResult:
    law_id: str
    content_hash: str
    distance: float


def compute_corpus_fingerprint(laws: Sequence[Dict]) -> str:
    """Return a deterministic fingerprint without trusting file order."""
    digest = hashlib.sha256()
    for law in sorted(laws, key=lambda item: str(item.get("id", ""))):
        digest.update(str(law.get("id", "")).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(law.get("content_hash", "")).encode("ascii", errors="ignore"))
        digest.update(b"\n")
    return digest.hexdigest()


def build_retrieval_text(law: Dict, max_chars: int) -> str:
    """Create stable embedding input from trusted corpus fields."""
    aliases = law.get("aliases") or []
    if not isinstance(aliases, list):
        aliases = []
    parts = [
        str(law.get("law_name", "")),
        str(law.get("title", "")),
        "; ".join(str(alias) for alias in aliases[:20]),
        str(law.get("text", "")),
    ]
    text = "\n".join(part.strip() for part in parts if part and part.strip())
    return text[:max_chars]


def safe_model_slug(model: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", model).strip("-.")
    return slug[:60] or "embedding-model"


class LanceDBVectorSearch:
    """Read-only runtime access to the currently verified vector index."""

    def __init__(
        self,
        *,
        settings: Settings,
        laws: Sequence[Dict],
        embedding_service: Optional[OllamaEmbeddingService] = None,
    ) -> None:
        self.enabled = settings.hybrid_retrieval_enabled
        self.index_dir = settings.vector_index_dir
        self.model = settings.embedding_model
        self.max_chars = settings.embedding_max_chars
        self.dense_candidates = settings.hybrid_dense_candidates
        self.embedding_service = embedding_service or OllamaEmbeddingService(
            host=settings.ollama_host,
            model=settings.embedding_model,
            timeout=settings.embedding_timeout,
            batch_size=settings.embedding_batch_size,
            keep_alive=settings.embedding_keep_alive,
        )
        self.corpus_fingerprint = compute_corpus_fingerprint(laws)
        self.ready = False
        self.error: Optional[str] = None
        self.row_count = 0
        self.embedding_dimension = 0
        self._table = None
        self._load_current_index(expected_rows=len(laws))

    def _load_current_index(self, *, expected_rows: int) -> None:
        if not self.enabled:
            self.error = "Hübriidotsing on seadetes välja lülitatud."
            return
        if lancedb is None:
            self.error = "LanceDB pakett ei ole paigaldatud."
            return

        manifest_path = self.index_dir / CURRENT_MANIFEST_FILE
        if not manifest_path.exists():
            self.error = "V6 vektorindeks puudub; kasutatakse V5 otsingut."
            return
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("schema_version") != INDEX_SCHEMA_VERSION:
                raise VectorIndexError("Vektorindeksi skeem ei vasta rakendusele.")
            if manifest.get("corpus_fingerprint") != self.corpus_fingerprint:
                raise VectorIndexError("Vektorindeks ei vasta praegusele seadusekorpusele.")
            if manifest.get("embedding_model") != self.model:
                raise VectorIndexError("Vektorindeks on loodud teise embedding-mudeliga.")
            if int(manifest.get("embedding_max_chars", 0)) != self.max_chars:
                raise VectorIndexError("Vektorindeksi tekstipiirang ei vasta seadetele.")
            if int(manifest.get("row_count", 0)) != expected_rows:
                raise VectorIndexError("Vektorindeksi kirjete arv ei vasta korpusele.")

            relative_database_dir = Path(str(manifest.get("database_dir", "")))
            if relative_database_dir.is_absolute() or ".." in relative_database_dir.parts:
                raise VectorIndexError("Vektorindeksi asukoht manifestis ei ole lubatud.")
            root = self.index_dir.resolve()
            database_dir = (root / relative_database_dir).resolve()
            if root != database_dir and root not in database_dir.parents:
                raise VectorIndexError("Vektorindeksi asukoht jääb lubatud kaustast välja.")
            if not database_dir.is_dir():
                raise VectorIndexError("Manifestis nimetatud vektorindeksi kaust puudub.")

            database = lancedb.connect(str(database_dir))
            table = database.open_table(str(manifest.get("table_name", INDEX_TABLE_NAME)))
            actual_rows = table.count_rows()
            if actual_rows != expected_rows:
                raise VectorIndexError("LanceDB tabeli kirjete arv ei vasta manifestile.")

            self._table = table
            self.row_count = actual_rows
            self.embedding_dimension = int(manifest.get("embedding_dimension", 0))
            if self.embedding_dimension <= 0:
                raise VectorIndexError("Manifestis puudub embeddingu mõõde.")
            self.ready = True
            self.error = None
        except (OSError, ValueError, TypeError, json.JSONDecodeError, VectorIndexError) as exc:
            self.error = str(exc)
            logger.warning("V6 vektorindeksit ei kasutata: %s", exc)
        except Exception as exc:  # LanceDB backend errors must not break V5.
            self.error = f"Vektorindeksi avamine ebaõnnestus: {exc}"
            logger.warning("V6 vektorindeksit ei kasutata: %s", exc)

    def search(self, query: str, *, limit: Optional[int] = None) -> List[DenseSearchResult]:
        batches = self.search_many([query], limit=limit)
        return batches[0] if batches else []

    def search_many(
        self,
        queries: Sequence[str],
        *,
        limit: Optional[int] = None,
    ) -> List[List[DenseSearchResult]]:
        """Embed query variants in one Ollama batch, then search each vector."""
        if not self.ready or self._table is None:
            raise VectorSearchUnavailableError(self.error or "Vektorindeks ei ole valmis.")
        normalized = [str(query).strip() for query in queries if str(query).strip()]
        if not normalized:
            return []
        try:
            vectors = self.embedding_service.embed_texts(normalized)
            row_batches = []
            for vector in vectors:
                if len(vector) != self.embedding_dimension:
                    raise VectorSearchUnavailableError(
                        "Päringu embeddingu mõõde ei vasta vektorindeksile."
                    )
                row_batches.append(
                    self._table.search(vector, vector_column_name="vector")
                    .metric("cosine")
                    .limit(limit or self.dense_candidates)
                    .to_list()
                )
        except (EmbeddingServiceError, VectorSearchUnavailableError) as exc:
            raise VectorSearchUnavailableError(str(exc)) from exc
        except Exception as exc:
            raise VectorSearchUnavailableError(
                f"LanceDB päring ebaõnnestus: {exc}"
            ) from exc

        result_batches: List[List[DenseSearchResult]] = []
        for rows in row_batches:
            results: List[DenseSearchResult] = []
            seen = set()
            for row in rows:
                law_id = str(row.get("law_id", ""))
                if not law_id or law_id in seen:
                    continue
                seen.add(law_id)
                results.append(
                    DenseSearchResult(
                        law_id=law_id,
                        content_hash=str(row.get("content_hash", "")),
                        distance=float(row.get("_distance", 0.0)),
                    )
                )
            result_batches.append(results)
        return result_batches

    def status(self) -> Dict[str, object]:
        return {
            "enabled": self.enabled,
            "ready": self.ready,
            "embedding_model": self.model,
            "embedding_dimension": self.embedding_dimension,
            "vector_rows": self.row_count,
            "error": self.error,
        }
