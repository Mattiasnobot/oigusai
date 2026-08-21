"""V11.4 verified current-revision section retrieval and corpus fallback labeling."""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from services.rt_current_revision import RTCurrentRetrievalError, RTCurrentRevisionResolver, clean_text
from services.rt_section_evidence import canonical_section, compute_section_provenance_sha256, extract_section

RT_CURRENT_RETRIEVAL_VERSION = "V11.4-rt-current-retrieval-1"


class VerifiedRTLiveRetrievalService:
    def __init__(self, *, resolver: RTCurrentRevisionResolver | None = None, **resolver_kwargs) -> None:
        self.resolver = resolver or RTCurrentRevisionResolver(**resolver_kwargs)

    def resolve_current_revision(
        self,
        title: str,
        *,
        as_of: date | None = None,
        document_types: Sequence[str] = ("seadus", "määrus"),
    ) -> Dict[str, Any]:
        check_date = as_of or date.today()
        resolved = self.resolver.resolve(title, as_of=check_date, document_types=document_types)
        binding = resolved.binding
        return {
            "version": RT_CURRENT_RETRIEVAL_VERSION,
            "status": "CURRENT_REVISION_VERIFIED",
            "query_title": clean_text(title),
            "official_title": resolved.official_title,
            "as_of_date": check_date.isoformat(),
            "act_id": binding["act_id"],
            "canonical_url": binding["canonical_url"],
            "xml_url": binding["xml_url"],
            "source_id": binding["source_id"],
            "authority_class": binding["authority_class"],
            "authority_verified": True,
            "currentness_verified": True,
            "revision_provenance_sha256": binding["revision_provenance_sha256"],
            "xml_sha256": binding["xml_sha256"],
            "text_sha256": binding["text_sha256"],
            "retrieval_enabled": True,
            "model_context_enabled": False,
            "corpus_write_enabled": False,
        }

    def retrieve_sections(
        self,
        title: str,
        sections: Iterable[str],
        *,
        as_of: date | None = None,
        domain: str = "",
        aliases_by_section: Mapping[str, Sequence[str]] | None = None,
    ) -> list[Dict[str, Any]]:
        check_date = as_of or date.today()
        wanted: list[str] = []
        for raw in sections:
            value = canonical_section(raw)
            if value not in wanted:
                wanted.append(value)
        if not wanted:
            return []
        resolved = self.resolver.resolve(title, as_of=check_date)
        binding = resolved.binding
        prefix = clean_text(domain).upper()
        aliases = aliases_by_section or {}
        output: list[Dict[str, Any]] = []
        for section_id in wanted:
            section = extract_section(resolved.xml_bytes, section_id)
            text = section["text"]
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            provenance = compute_section_provenance_sha256({
                "version": RT_CURRENT_RETRIEVAL_VERSION,
                "act_id": binding["act_id"],
                "revision_provenance_sha256": binding["revision_provenance_sha256"],
                "section": section_id,
                "content_hash": content_hash,
            })
            record_id = f"{prefix}_{section_id}" if prefix else f"RT_{binding['act_id']}_{section_id}"
            title_text = f"{resolved.official_title} § {section_id}"
            if section["heading"]:
                title_text += f" – {section['heading']}"
            output.append({
                "id": record_id,
                "title": title_text,
                "text": text,
                "source": f"Riigi Teataja live verified: {resolved.official_title}",
                "domain": prefix or "RT",
                "law_name": resolved.official_title,
                "section": section_id,
                "aliases": list(aliases.get(section_id, ())),
                "url": f"{binding['canonical_url']}#para{section_id.casefold()}",
                "content_hash": content_hash,
                "evidence_source": "rt_live_verified",
                "verification_status": "BINDING_SECTION_VERIFIED",
                "source_id": binding["source_id"],
                "authority_class": binding["authority_class"],
                "authority_verified": True,
                "currentness_verified": True,
                "as_of_date": check_date.isoformat(),
                "act_id": binding["act_id"],
                "canonical_url": binding["canonical_url"],
                "xml_url": binding["xml_url"],
                "revision_provenance_sha256": binding["revision_provenance_sha256"],
                "section_provenance_sha256": provenance,
                "xml_sha256": binding["xml_sha256"],
                "model_context_enabled": False,
                "corpus_write_enabled": False,
            })
        return output

    def upgrade_candidates(
        self,
        candidates: Sequence[Mapping[str, Any]],
        *,
        as_of: date | None = None,
    ) -> Dict[str, Any]:
        check_date = as_of or date.today()
        grouped: Dict[Tuple[str, str], list[Mapping[str, Any]]] = {}
        for candidate in candidates:
            law_name = clean_text(candidate.get("law_name", ""))
            domain = clean_text(candidate.get("domain", "")).upper()
            section = clean_text(candidate.get("section", ""))
            if law_name and domain and section:
                grouped.setdefault((law_name, domain), []).append(candidate)

        replacements: Dict[str, Dict[str, Any]] = {}
        failures: list[Dict[str, str]] = []
        resolved_acts: list[Dict[str, str]] = []
        for (law_name, domain), items in grouped.items():
            try:
                live_records = self.retrieve_sections(
                    law_name,
                    [str(item["section"]) for item in items],
                    as_of=check_date,
                    domain=domain,
                    aliases_by_section={
                        canonical_section(str(item["section"])): tuple(item.get("aliases") or ())
                        for item in items
                    },
                )
            except RTCurrentRetrievalError as exc:
                failures.append({"law_name": law_name, "domain": domain, "error": str(exc)})
                continue
            for record in live_records:
                replacements[record["id"]] = record
            if live_records:
                resolved_acts.append({
                    "law_name": law_name,
                    "domain": domain,
                    "act_id": live_records[0]["act_id"],
                    "source_id": live_records[0]["source_id"],
                })

        laws: list[Dict[str, Any]] = []
        verified_count = 0
        for candidate in candidates:
            replacement = replacements.get(str(candidate.get("id", "")))
            if replacement is not None:
                laws.append(replacement)
                verified_count += 1
            else:
                fallback = dict(candidate)
                fallback["evidence_source"] = "audited_local_corpus"
                fallback["verification_status"] = "LOCAL_CORPUS_FALLBACK"
                fallback["model_context_enabled"] = False
                laws.append(fallback)
        status = (
            "LIVE_VERIFIED" if candidates and verified_count == len(candidates)
            else "PARTIAL_LIVE_FALLBACK" if verified_count
            else "LOCAL_CORPUS_FALLBACK"
        )
        return {
            "version": RT_CURRENT_RETRIEVAL_VERSION,
            "status": status,
            "as_of_date": check_date.isoformat(),
            "laws": laws,
            "verified_count": verified_count,
            "fallback_count": len(candidates) - verified_count,
            "resolved_acts": resolved_acts,
            "failures": failures,
            "retrieval_enabled": True,
            "model_context_enabled": False,
            "corpus_write_enabled": False,
        }
