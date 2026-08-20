"""Build the versioned local LanceDB index used by ÕigusAI V6."""
from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import lancedb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import load_settings
from services.embedding_service import OllamaEmbeddingService
from services.legal_search import LegalSearchService
from services.vector_search import (
    CURRENT_MANIFEST_FILE,
    INDEX_SCHEMA_VERSION,
    INDEX_TABLE_NAME,
    LanceDBVectorSearch,
    VectorIndexError,
    build_retrieval_text,
    compute_corpus_fingerprint,
    safe_model_slug,
)


WRITE_BATCH_SIZE = 256


def build_index(*, force: bool = False) -> dict:
    settings = load_settings()
    legal_service = LegalSearchService(
        use_riigi_teataja=False,
        data_file=settings.legal_data_file,
        settings=settings,
    )
    laws = legal_service.laws

    current = LanceDBVectorSearch(settings=settings, laws=laws)
    if current.ready and not force:
        print(
            f"V6 indeks on juba valmis: {current.row_count} kirjet, "
            f"mudel {current.model}. Kasuta --force ainult teadlikuks uuesti ehitamiseks."
        )
        return current.status()

    embedding_service = OllamaEmbeddingService(
        host=settings.ollama_host,
        model=settings.embedding_model,
        timeout=settings.embedding_timeout,
        batch_size=settings.embedding_batch_size,
        keep_alive=settings.embedding_keep_alive,
    )
    corpus_fingerprint = compute_corpus_fingerprint(laws)
    temporary_dir = Path(tempfile.mkdtemp(prefix="oigusai-v6-index-"))
    print(f"Ehitan ajutisse kausta: {temporary_dir}")
    print(
        f"Embedding-mudel: {settings.embedding_model}; "
        f"korpuse kirjeid: {len(laws)}; batch: {settings.embedding_batch_size}"
    )

    database = lancedb.connect(str(temporary_dir))
    table = None
    pending_rows = []
    embedding_dimension = 0
    started = time.perf_counter()

    try:
        for start in range(0, len(laws), settings.embedding_batch_size):
            batch = laws[start : start + settings.embedding_batch_size]
            texts = [
                build_retrieval_text(law, settings.embedding_max_chars)
                for law in batch
            ]
            vectors = embedding_service.embed_texts(texts)
            if len(vectors) != len(batch):
                raise VectorIndexError("Embeddingute arv ei vasta korpuse kirjete arvule.")

            for law, vector in zip(batch, vectors):
                if embedding_dimension == 0:
                    embedding_dimension = len(vector)
                elif len(vector) != embedding_dimension:
                    raise VectorIndexError("Embeddingute mõõtmed ei ole ühesugused.")
                pending_rows.append(
                    {
                        "law_id": law["id"],
                        "domain": str(law.get("domain", "")),
                        "content_hash": str(law.get("content_hash", "")),
                        "vector": vector,
                    }
                )

            completed = min(start + len(batch), len(laws))
            should_flush = len(pending_rows) >= WRITE_BATCH_SIZE or completed == len(laws)
            if should_flush:
                if table is None:
                    table = database.create_table(
                        INDEX_TABLE_NAME,
                        data=pending_rows,
                        mode="create",
                    )
                else:
                    table.add(pending_rows)
                pending_rows = []

            if completed == len(laws) or completed % 256 == 0:
                elapsed = max(0.001, time.perf_counter() - started)
                rate = completed / elapsed
                remaining = (len(laws) - completed) / max(rate, 0.001)
                print(
                    f"Vektoriseeritud {completed}/{len(laws)} "
                    f"({rate:.1f} kirjet/s, jäänud u {remaining / 60:.1f} min)",
                    flush=True,
                )

        if table is None:
            raise VectorIndexError("Tühja korpuse jaoks indeksit ei loodud.")
        actual_rows = table.count_rows()
        if actual_rows != len(laws):
            raise VectorIndexError(
                f"LanceDB tabelis on {actual_rows} kirjet, oodati {len(laws)}."
            )

        # Release native Lance handles before the atomic directory move. This
        # matters especially on Windows, where an open handle may block rename.
        del table
        del database
        gc.collect()

        index_root = settings.vector_index_dir
        index_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        final_name = (
            f"index-{corpus_fingerprint[:12]}-"
            f"{safe_model_slug(settings.embedding_model)}-{timestamp}"
        )
        final_dir = index_root / final_name
        if final_dir.exists():
            raise VectorIndexError(f"Uue indeksi sihtkaust on ootamatult olemas: {final_dir}")
        temporary_dir.replace(final_dir)

        manifest = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "corpus_fingerprint": corpus_fingerprint,
            "embedding_model": settings.embedding_model,
            "embedding_dimension": embedding_dimension,
            "embedding_max_chars": settings.embedding_max_chars,
            "row_count": actual_rows,
            "table_name": INDEX_TABLE_NAME,
            "database_dir": final_name,
            "built_at": datetime.now(timezone.utc).isoformat(),
        }
        manifest_temp = index_root / f".{CURRENT_MANIFEST_FILE}.{os.getpid()}.tmp"
        manifest_temp.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_temp.replace(index_root / CURRENT_MANIFEST_FILE)

        elapsed = time.perf_counter() - started
        print(
            f"V6 indeks valmis: {actual_rows} kirjet, {embedding_dimension} mõõdet, "
            f"{elapsed / 60:.1f} min."
        )
        return manifest
    except Exception:
        print(
            "Indeksi ehitus katkes. Olemasolevat current.json indeksit ei muudetud. "
            f"Diagnostikaks jäi ajutine kaust: {temporary_dir}"
        )
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ehita uus versioon ka siis, kui praegune indeks on korpusega kooskõlas.",
    )
    args = parser.parse_args()
    build_index(force=args.force)


if __name__ == "__main__":
    main()
