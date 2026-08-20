"""Local multilingual cross-encoder reranker for ÕigusAI V6.1.

The reranker never introduces a source. It only reorders candidate records
that have already been mapped back to the checksum-verified legal corpus.
Model loading is lazy so startup and the V6/V5 fallbacks remain available even
when optional ML dependencies or GPU memory are unavailable.
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, List, Sequence, Tuple

from config import Settings

logger = logging.getLogger(__name__)

RankedLaw = Tuple[float, Dict, Dict[str, float]]


class RerankerUnavailableError(RuntimeError):
    """Raised when reranking fails and the caller must keep the V6 order."""


class LocalCrossEncoderReranker:
    """Lazy BGE query-passage scorer with a deterministic safe fallback."""

    def __init__(self, *, settings: Settings) -> None:
        self.enabled = settings.reranker_enabled
        self.model_name = settings.reranker_model
        self.requested_device = settings.reranker_device
        self.candidates = settings.reranker_candidates
        self.batch_size = settings.reranker_batch_size
        self.max_length = settings.reranker_max_length
        self.max_chars = settings.reranker_max_chars
        self.weight = settings.reranker_weight

        self.device = ""
        self.loaded = False
        self.error = None
        self._fatal_error = None
        self._torch = None
        self._tokenizer = None
        self._model = None
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if not self.enabled:
            raise RerankerUnavailableError("Reranker on välja lülitatud.")
        if self.loaded:
            return
        if self._fatal_error:
            raise RerankerUnavailableError(self._fatal_error)

        with self._load_lock:
            if self.loaded:
                return
            if self._fatal_error:
                raise RerankerUnavailableError(self._fatal_error)
            try:
                import torch
                from transformers import (
                    AutoModelForSequenceClassification,
                    AutoTokenizer,
                )

                device = self.requested_device
                if device == "auto":
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                if device == "cuda" and not torch.cuda.is_available():
                    raise RuntimeError("CUDA ei ole PyTorchile saadaval.")

                tokenizer = AutoTokenizer.from_pretrained(self.model_name)
                model = AutoModelForSequenceClassification.from_pretrained(
                    self.model_name
                )
                model.eval()
                if device == "cuda":
                    model = model.half().to("cuda")
                else:
                    model = model.to("cpu")

                self._torch = torch
                self._tokenizer = tokenizer
                self._model = model
                self.device = device
                self.loaded = True
                self.error = None
                logger.info(
                    "V6.1 reranker loaded on %s (%s)", device, self.model_name
                )
            except Exception as exc:
                message = f"Rerankeri laadimine ebaõnnestus: {exc}"
                self.error = message
                self._fatal_error = message
                logger.warning("%s; V6 order remains active", message)
                raise RerankerUnavailableError(message) from exc

    def _passage(self, law: Dict) -> str:
        parts = [
            str(law.get("law_name", "")).strip(),
            str(law.get("title", "")).strip(),
            str(law.get("text", "")).strip(),
        ]
        return "\n".join(part for part in parts if part)[: self.max_chars]

    def rerank(self, query: str, ranking: Sequence[RankedLaw]) -> List[RankedLaw]:
        """Return only the supplied corpus candidates in cross-encoder order."""
        candidates = list(ranking[: self.candidates])
        if not str(query).strip() or not candidates:
            return candidates
        self._ensure_loaded()

        pairs = [
            [str(query).strip(), self._passage(law)]
            for _, law, _ in candidates
        ]
        scores: List[float] = []
        try:
            with self._inference_lock:
                for start in range(0, len(pairs), self.batch_size):
                    batch = pairs[start : start + self.batch_size]
                    inputs = self._tokenizer(
                        batch,
                        padding=True,
                        truncation=True,
                        return_tensors="pt",
                        max_length=self.max_length,
                    )
                    inputs = {
                        name: tensor.to(self.device)
                        for name, tensor in inputs.items()
                    }
                    with self._torch.inference_mode():
                        logits = self._model(
                            **inputs, return_dict=True
                        ).logits.view(-1).float().cpu().tolist()
                    scores.extend(float(value) for value in logits)
        except Exception as exc:
            message = f"Rerankeri hindamine ebaõnnestus: {exc}"
            self.error = message
            logger.warning("%s; V6 order remains active", message)
            raise RerankerUnavailableError(message) from exc

        reranked = [
            (score, candidate[1], candidate[2])
            for score, candidate in zip(scores, candidates)
        ]
        reranked.sort(key=lambda item: (-item[0], item[1]["id"]))
        self.error = None
        return reranked

    def status(self) -> Dict[str, object]:
        return {
            "enabled": self.enabled,
            "loaded": self.loaded,
            "ready": bool(self.enabled and self.loaded and not self.error),
            "model": self.model_name,
            "device": self.device or self.requested_device,
            "candidates": self.candidates,
            "error": self.error,
        }
