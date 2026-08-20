"""Ephemeral local matters for V8 document-aware conversations."""

from __future__ import annotations

import re
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional


class MatterNotFoundError(KeyError):
    pass


class MatterStore:
    """Thread-safe in-memory registry; raw uploaded files are never persisted."""

    def __init__(
        self,
        max_matters: int = 100,
        max_documents_per_matter: int = 20,
        ttl_minutes: int = 120,
        clock=None,
    ):
        self.max_matters = max_matters
        self.max_documents_per_matter = max_documents_per_matter
        self.ttl_seconds = max(60, int(ttl_minutes) * 60)
        self._clock = clock or time.monotonic
        self._matters: Dict[str, Dict] = {}
        self._lock = threading.RLock()

    def create(self, title: str = "Uus juhtum") -> Dict:
        with self._lock:
            self._cleanup_expired_locked()
            if len(self._matters) >= self.max_matters:
                oldest = min(
                    self._matters.values(),
                    key=lambda item: item["updated_at"],
                )
                self._matters.pop(oldest["matter_id"], None)
            now = datetime.now(timezone.utc).isoformat()
            matter = {
                "matter_id": str(uuid.uuid4()),
                "title": str(title or "Uus juhtum").strip()[:120],
                "created_at": now,
                "updated_at": now,
                "last_touched": self._clock(),
                "documents": {},
                "case_card": {},
            }
            self._matters[matter["matter_id"]] = matter
            return self._public(matter)

    def add_document(self, matter_id: Optional[str], document: Dict) -> Dict:
        with self._lock:
            self._cleanup_expired_locked()
            if not matter_id:
                matter_id = self.create()["matter_id"]
            matter = self._matters.get(matter_id)
            if matter is None:
                raise MatterNotFoundError(matter_id)
            is_new = document["document_id"] not in matter["documents"]
            if is_new and len(matter["documents"]) >= self.max_documents_per_matter:
                raise ValueError("Ühes juhtumis võib olla kuni 20 dokumenti.")
            matter["documents"][document["document_id"]] = document
            matter["updated_at"] = datetime.now(timezone.utc).isoformat()
            matter["last_touched"] = self._clock()
            return {"matter": self._public(matter), "document": self._document_public(document)}

    def update_case_card(self, matter_id: str, case_card: Dict) -> Dict:
        """Replace the structured card while keeping the matter memory-only."""
        with self._lock:
            self._cleanup_expired_locked()
            matter = self._matters.get(matter_id)
            if matter is None:
                raise MatterNotFoundError(matter_id)
            matter["case_card"] = deepcopy(case_card or {})
            title = str(matter["case_card"].get("title") or "").strip()
            if title:
                matter["title"] = title[:120]
            matter["updated_at"] = datetime.now(timezone.utc).isoformat()
            matter["last_touched"] = self._clock()
            return deepcopy(matter["case_card"])

    def case_card(self, matter_id: str) -> Dict:
        with self._lock:
            self._cleanup_expired_locked()
            matter = self._matters.get(matter_id)
            if matter is None:
                raise MatterNotFoundError(matter_id)
            matter["last_touched"] = self._clock()
            return deepcopy(matter.get("case_card") or {})

    def documents(self, matter_id: str, *, include_spans: bool = False) -> List[Dict]:
        with self._lock:
            self._cleanup_expired_locked()
            matter = self._matters.get(matter_id)
            if matter is None:
                raise MatterNotFoundError(matter_id)
            matter["last_touched"] = self._clock()
            if include_spans:
                return [deepcopy(item) for item in matter["documents"].values()]
            return [self._document_public(item) for item in matter["documents"].values()]

    def get(self, matter_id: str) -> Dict:
        with self._lock:
            self._cleanup_expired_locked()
            matter = self._matters.get(matter_id)
            if matter is None:
                raise MatterNotFoundError(matter_id)
            matter["last_touched"] = self._clock()
            return self._public(matter)

    def delete(self, matter_id: str) -> bool:
        with self._lock:
            return self._matters.pop(matter_id, None) is not None

    def count(self) -> int:
        with self._lock:
            self._cleanup_expired_locked()
            return len(self._matters)

    def relevant_spans(
        self,
        matter_id: str,
        document_ids: Iterable[str],
        query: str,
        limit: int = 8,
    ) -> List[Dict]:
        with self._lock:
            self._cleanup_expired_locked()
            matter = self._matters.get(matter_id)
            if matter is None:
                raise MatterNotFoundError(matter_id)
            matter["last_touched"] = self._clock()
            allowed = set(document_ids or matter["documents"].keys())
            spans = [
                span.copy()
                for document_id, document in matter["documents"].items()
                if document_id in allowed
                for span in document.get("spans", [])
            ]
        query_terms = set(re.findall(r"[a-zõäöü]{4,}", str(query or "").casefold()))
        scored = []
        for index, span in enumerate(spans):
            terms = set(re.findall(r"[a-zõäöü]{4,}", span["text"].casefold()))
            overlap = len(query_terms.intersection(terms))
            score = overlap * 10 - min(index, 20) * 0.01
            scored.append((score, index, span))
        scored.sort(key=lambda item: (-item[0], item[1]))
        selected = [item[2] for item in scored[:limit]]
        return selected

    def _cleanup_expired_locked(self) -> None:
        cutoff = self._clock() - self.ttl_seconds
        expired = [
            matter_id
            for matter_id, matter in self._matters.items()
            if float(matter.get("last_touched", 0.0)) <= cutoff
        ]
        for matter_id in expired:
            self._matters.pop(matter_id, None)

    @staticmethod
    def _document_public(document: Dict) -> Dict:
        public = {
            key: value
            for key, value in document.items()
            if key != "spans"
        } | {"span_count": len(document.get("spans", []))}
        return deepcopy(public)

    @classmethod
    def _public(cls, matter: Dict) -> Dict:
        return {
            "matter_id": matter["matter_id"],
            "title": matter["title"],
            "created_at": matter["created_at"],
            "updated_at": matter["updated_at"],
            "case_card": deepcopy(matter.get("case_card") or {}),
            "documents": [
                cls._document_public(document)
                for document in matter["documents"].values()
            ],
        }
