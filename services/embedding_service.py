"""Local text embeddings through Ollama.

Embeddings are retrieval signals only. This module never creates legal claims
and never changes the source text that is passed to the answer verifier.
"""
from __future__ import annotations

import math
from typing import Iterable, List, Sequence

import httpx


class EmbeddingServiceError(RuntimeError):
    """Raised when Ollama cannot return a well-formed embedding batch."""


class OllamaEmbeddingService:
    def __init__(
        self,
        *,
        host: str,
        model: str,
        timeout: int,
        batch_size: int,
        keep_alive: str,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.batch_size = batch_size
        self.keep_alive = keep_alive

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        """Embed text in bounded batches and strictly validate Ollama output."""
        normalized = [str(text) for text in texts]
        if not normalized:
            return []

        vectors: List[List[float]] = []
        expected_dimension = None
        for start in range(0, len(normalized), self.batch_size):
            batch = normalized[start : start + self.batch_size]
            payload = {
                "model": self.model,
                "input": batch,
                "keep_alive": self.keep_alive,
                "truncate": True,
            }
            try:
                response = httpx.post(
                    f"{self.host}/api/embed",
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                body = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise EmbeddingServiceError(
                    f"Ollama embedding-päring ebaõnnestus mudeliga {self.model}: {exc}"
                ) from exc

            batch_vectors = body.get("embeddings") if isinstance(body, dict) else None
            if not isinstance(batch_vectors, list) or len(batch_vectors) != len(batch):
                raise EmbeddingServiceError(
                    "Ollama embedding-vastus ei sisaldanud iga sisendi jaoks vektorit."
                )

            for vector in batch_vectors:
                checked = self._validate_vector(vector)
                if expected_dimension is None:
                    expected_dimension = len(checked)
                elif len(checked) != expected_dimension:
                    raise EmbeddingServiceError(
                        "Ollama tagastas samas päringus erineva mõõtmega vektorid."
                    )
                vectors.append(checked)
        return vectors

    def embed_query(self, text: str) -> List[float]:
        vectors = self.embed_texts([text])
        if not vectors:
            raise EmbeddingServiceError("Tühja päringu embeddingut ei loodud.")
        return vectors[0]

    @staticmethod
    def _validate_vector(vector: object) -> List[float]:
        if not isinstance(vector, list) or not vector:
            raise EmbeddingServiceError("Ollama tagastas tühja või vigase vektori.")
        checked: List[float] = []
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EmbeddingServiceError("Ollama vektor sisaldas mittenumbrilist väärtust.")
            number = float(value)
            if not math.isfinite(number):
                raise EmbeddingServiceError("Ollama vektor sisaldas lõpmatut või NaN väärtust.")
            checked.append(number)
        return checked

