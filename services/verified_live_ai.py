"""V11.5 verified Riigi Teataja live evidence -> model-context admission.

Only exact V11.4 ``BINDING_SECTION_VERIFIED`` records may be promoted as live
model context. Any malformed/tampered live record fails closed to the original
audited local corpus candidate. The service mutates the caller-provided laws
list in place so Ollama and every downstream citation/evidence verifier inspect
the exact same source records.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date
from typing import Any, Dict, Mapping, Sequence
from urllib.parse import urlparse

from config import Settings, load_settings
from services.offline_ai import OfflineAIService
from services.rt_current_retrieval import (
    RT_CURRENT_RETRIEVAL_VERSION,
    VerifiedRTLiveRetrievalService,
)
from services.rt_section_evidence import compute_section_provenance_sha256

V11_5_MODEL_CONTEXT_VERSION = "V11.5-verified-live-model-context-1"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_AUTHORITY = {
    "RT_NATIONAL_LAW": "binding_national_law",
    "RT_LOCAL_LAW": "binding_local_law",
}


class LiveModelContextAdmissionError(RuntimeError):
    """A purported live RT record cannot be admitted to model context."""


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _valid_sha(value: Any) -> bool:
    return bool(_HEX64.fullmatch(_clean(value).casefold()))


def _official_rt_url(value: Any, *, xml: bool = False) -> bool:
    try:
        parsed = urlparse(_clean(value))
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.hostname != "www.riigiteataja.ee":
        return False
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return False
    if xml:
        return bool(re.fullmatch(r"/public-api/api/v1/akt/\d+/xml", parsed.path))
    return bool(re.fullmatch(r"/akt/\d+", parsed.path))


class VerifiedLiveModelContextGate:
    """Admit only a complete, untampered V11.4 binding-section record."""

    def admit_live(self, record: Mapping[str, Any], *, as_of: date) -> Dict[str, Any]:
        if _clean(record.get("verification_status")) != "BINDING_SECTION_VERIFIED":
            raise LiveModelContextAdmissionError("Live model context requires BINDING_SECTION_VERIFIED.")
        if _clean(record.get("evidence_source")) != "rt_live_verified":
            raise LiveModelContextAdmissionError("Live model context requires rt_live_verified provenance.")
        if record.get("model_context_enabled") is not False:
            raise LiveModelContextAdmissionError("V11.4 live evidence must be disabled before V11.5 admission.")
        if record.get("corpus_write_enabled") is not False:
            raise LiveModelContextAdmissionError("Live model context cannot enable corpus writes.")
        if record.get("authority_verified") is not True or record.get("currentness_verified") is not True:
            raise LiveModelContextAdmissionError("Authority/currentness verification is incomplete.")

        source_id = _clean(record.get("source_id"))
        expected_authority = _EXPECTED_AUTHORITY.get(source_id)
        if expected_authority is None:
            raise LiveModelContextAdmissionError(f"Unsupported binding source class: {source_id!r}.")
        if _clean(record.get("authority_class")) != expected_authority:
            raise LiveModelContextAdmissionError("Binding source/authority class mismatch.")
        if _clean(record.get("as_of_date")) != as_of.isoformat():
            raise LiveModelContextAdmissionError("Live evidence date does not match the requested legal date.")

        required_text = ("id", "law_name", "section", "text", "act_id")
        missing = [name for name in required_text if not _clean(record.get(name))]
        if missing:
            raise LiveModelContextAdmissionError("Live evidence is missing required fields: " + ", ".join(missing))
        for field in (
            "content_hash",
            "section_provenance_sha256",
            "revision_provenance_sha256",
            "xml_sha256",
        ):
            if not _valid_sha(record.get(field)):
                raise LiveModelContextAdmissionError(f"Live evidence has invalid {field}.")

        text = str(record.get("text") or "")
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if content_hash != _clean(record.get("content_hash")).casefold():
            raise LiveModelContextAdmissionError("Live evidence content_hash does not match the exact section text.")
        expected_section_provenance = compute_section_provenance_sha256({
            "version": RT_CURRENT_RETRIEVAL_VERSION,
            "act_id": _clean(record.get("act_id")),
            "revision_provenance_sha256": _clean(record.get("revision_provenance_sha256")).casefold(),
            "section": _clean(record.get("section")),
            "content_hash": content_hash,
        })
        if expected_section_provenance != _clean(record.get("section_provenance_sha256")).casefold():
            raise LiveModelContextAdmissionError("Live evidence section provenance does not match the V11.4 chain.")

        if not _official_rt_url(record.get("canonical_url")):
            raise LiveModelContextAdmissionError("Live evidence canonical URL is not an exact RT act URL.")
        if not _official_rt_url(record.get("xml_url"), xml=True):
            raise LiveModelContextAdmissionError("Live evidence XML URL is not an exact RT XML endpoint.")

        admitted = dict(record)
        admitted["model_context_enabled"] = True
        admitted["model_context_source"] = "rt_live_verified"
        admitted["model_context_admission"] = "VERIFIED_LIVE_ADMITTED"
        admitted["model_context_version"] = V11_5_MODEL_CONTEXT_VERSION
        return admitted

    @staticmethod
    def admit_local(record: Mapping[str, Any]) -> Dict[str, Any]:
        admitted = dict(record)
        admitted["model_context_enabled"] = True
        admitted["model_context_source"] = "audited_local_corpus"
        admitted["model_context_admission"] = "AUDITED_LOCAL_ADMITTED"
        admitted["model_context_version"] = V11_5_MODEL_CONTEXT_VERSION
        return admitted


class VerifiedLiveModelContextService:
    """Upgrade audited local candidates and safely build the model source list."""

    def __init__(self, *, live_retrieval=None, gate=None) -> None:
        self.live_retrieval = live_retrieval or VerifiedRTLiveRetrievalService()
        self.gate = gate or VerifiedLiveModelContextGate()

    @staticmethod
    def _parse_event_date(event_date: str) -> date:
        if not event_date:
            return date.today()
        try:
            parsed = date.fromisoformat(event_date)
        except ValueError as exc:
            raise ValueError("Sündmuse kuupäev peab olema kujul YYYY-MM-DD.") from exc
        if parsed > date.today():
            raise ValueError("Tulevase kuupäeva live-õigusseisu ei verifitseerita.")
        return parsed

    def upgrade_for_model(self, laws: Sequence[Mapping[str, Any]], event_date: str = "") -> Dict[str, Any]:
        originals = {str(item.get("id", "")): dict(item) for item in laws}
        check_date = self._parse_event_date(event_date)
        live = self.live_retrieval.upgrade_candidates(list(laws), as_of=check_date)

        output: list[Dict[str, Any]] = []
        admitted_live = 0
        local_context = 0
        admission_failures: list[Dict[str, str]] = []
        for candidate in live.get("laws", []):
            candidate_id = str(candidate.get("id", ""))
            if candidate.get("verification_status") == "BINDING_SECTION_VERIFIED":
                try:
                    output.append(self.gate.admit_live(candidate, as_of=check_date))
                    admitted_live += 1
                    continue
                except LiveModelContextAdmissionError as exc:
                    admission_failures.append({"id": candidate_id, "error": str(exc)})
            original = originals.get(candidate_id, candidate)
            output.append(self.gate.admit_local(original))
            local_context += 1

        return {
            "version": V11_5_MODEL_CONTEXT_VERSION,
            "status": (
                "LIVE_MODEL_CONTEXT" if output and admitted_live == len(output)
                else "MIXED_MODEL_CONTEXT" if admitted_live
                else "LOCAL_MODEL_CONTEXT"
            ),
            "as_of_date": check_date.isoformat(),
            "laws": output,
            "live_admitted_count": admitted_live,
            "local_context_count": local_context,
            "admission_failures": admission_failures,
            "retrieval_failures": list(live.get("failures") or []),
            "model_context_enabled": True,
            "corpus_write_enabled": False,
        }


class VerifiedLiveOfflineAIService(OfflineAIService):
    """Drop-in OfflineAIService that opt-in upgrades laws before prompt creation."""

    def __init__(
        self,
        *args,
        settings: Settings | None = None,
        live_model_context_enabled: bool | None = None,
        live_context_service: VerifiedLiveModelContextService | None = None,
        **kwargs,
    ) -> None:
        cfg = settings or load_settings()
        super().__init__(*args, settings=cfg, **kwargs)
        configured = bool(getattr(cfg, "rt_verified_live_model_context_enabled", False))
        self.live_model_context_enabled = configured if live_model_context_enabled is None else bool(live_model_context_enabled)
        self.live_context_service = live_context_service or VerifiedLiveModelContextService()
        self.last_live_model_context: Dict[str, Any] = {
            "version": V11_5_MODEL_CONTEXT_VERSION,
            "status": "DISABLED",
            "model_context_enabled": False,
        }
        self._live_context_logger = logging.getLogger(__name__)

    def analyze_case_structured(
        self,
        case_desc: str,
        laws: list[Dict],
        event_date: str = "",
        document_spans: list[Dict] | None = None,
    ):
        if self.live_model_context_enabled and laws:
            try:
                context = self.live_context_service.upgrade_for_model(laws, event_date)
                # Critical invariant: mutate the exact list object used later by
                # SourceVerifier/CoverageVerifier so model and verifier see one source set.
                laws[:] = context["laws"]
                self.last_live_model_context = {key: value for key, value in context.items() if key != "laws"}
            except Exception as exc:
                self._live_context_logger.warning(
                    "V11.5 live model-context upgrade failed; keeping audited local corpus: %s",
                    exc,
                )
                self.last_live_model_context = {
                    "version": V11_5_MODEL_CONTEXT_VERSION,
                    "status": "LOCAL_MODEL_CONTEXT",
                    "model_context_enabled": False,
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                }
        return super().analyze_case_structured(case_desc, laws, event_date, document_spans)
