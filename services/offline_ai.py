"""
ÕigusAI - Offline AI analüüsi teenus

Kasutab Ollama kaudu lokaalset LLM mudelit.

Eesmärk:
- AI tohib kasutada ainult neid seadusi, mis talle anti;
- AI seob iga väite allika ID ja täpse tõendikatkendiga;
- kui vastus kontrolli ei läbi, teeme seadistatud arvu paranduspäringuid;
- rakendus vormindab kontrollitud JSON-vastuse kasutajale loetavaks;
- säilitame "NO SOURCE -> NO LEGAL CLAIM" põhimõtte.
"""

import html
import json
import logging
import re
from typing import Dict, List, Tuple

import requests

from config import Settings, load_settings
from services.turn_planner import ConversationTurnPlanner
from verifiers.source_verifier import SourceVerifier

logger = logging.getLogger(__name__)

SERVICE_CONFIG_VERSION = 8

AI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_id": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["text", "source_id", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["claims"],
    "additionalProperties": False,
}

AI_DOCUMENT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": AI_RESPONSE_SCHEMA["properties"]["claims"],
        "comparisons": {
            "type": "array",
            "maxItems": 2,
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "law_source_id": {"type": "string"},
                    "law_evidence": {"type": "string"},
                    "document_span_id": {"type": "string"},
                    "document_evidence": {"type": "string"},
                },
                "required": [
                    "text",
                    "law_source_id",
                    "law_evidence",
                    "document_span_id",
                    "document_evidence",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["claims", "comparisons"],
    "additionalProperties": False,
}


class OfflineAIService:
    def __init__(
        self,
        ollama_url: str = None,
        model_name: str = None,
        timeout: int = None,
        allow_mock: bool = None,
        settings: Settings = None,
        generation_seed: int = None,
        repair_debug: bool = False,
    ):
        cfg = settings or load_settings()
        self.ollama_url = (ollama_url or cfg.ollama_host).rstrip("/")
        self.model_name = model_name or cfg.ollama_model
        self.timeout = timeout if timeout is not None else cfg.ollama_timeout
        self.allow_mock = cfg.allow_mock_analysis if allow_mock is None else bool(allow_mock)

        self.temperature = cfg.ollama_temperature
        self.top_p = cfg.ollama_top_p
        self.num_ctx = cfg.ollama_num_ctx
        self.num_predict = cfg.ollama_num_predict
        self.think = cfg.ollama_think
        self.keep_alive = cfg.ollama_keep_alive
        self.citation_retries = cfg.ollama_citation_retries
        self.generation_seed = (
            None if generation_seed is None else int(generation_seed)
        )
        self.repair_debug = bool(repair_debug)
        # Paranduspäring ja API lõppkontroll peavad kasutama täpselt sama reeglistikku.
        self.source_verifier = SourceVerifier()

    # ------------------------------------------------------------------
    # Avalik API
    # ------------------------------------------------------------------

    def analyze_case(
        self,
        case_desc: str,
        laws: List[Dict],
        event_date: str = "",
    ) -> Tuple[str, bool]:
        analysis, is_mock, _ = self.analyze_case_structured(
            case_desc,
            laws,
            event_date,
        )
        return analysis, is_mock

    def analyze_case_structured(
        self,
        case_desc: str,
        laws: List[Dict],
        event_date: str = "",
        document_spans: List[Dict] = None,
    ) -> Tuple[str, bool, List[Dict]]:
        """
        Saadab päringu Ollama API-le ja tagastab analüüsi.

        Tagastab:
            (analysis_text, is_mock)

        is_mock == True tähendab, et Ollama polnud saadaval ja tagastati
        testvastus.
        """
        if not laws:
            return (
                "Antud seaduste põhjal ei ole võimalik analüüsi teha. "
                "Süsteem ei leidnud sobivaid õiguslikke allikaid.",
                False,
                [],
            )

        document_spans = list(document_spans or [])[:5]
        prompt = self._build_prompt(case_desc, laws, event_date, document_spans)
        response_schema = (
            AI_DOCUMENT_RESPONSE_SCHEMA if document_spans else AI_RESPONSE_SCHEMA
        )

        try:
            raw_response = self._call_ollama(prompt, response_schema=response_schema)
        except requests.exceptions.ConnectionError:
            logger.warning(
                "Ollama ei ole käivitatud või ei vasta aadressil %s.",
                self.ollama_url,
            )

            if not self.allow_mock:
                raise RuntimeError("Ollama ei ole saadaval ja testvastuseid pole lubatud.")

            logger.warning("Kasutan testvastust.")
            mock = self._mock_analysis(case_desc, laws)
            return mock, True, self.claims_from_verified_analysis(mock, laws)

        except requests.exceptions.Timeout:
            logger.warning(
                "Ollama päring aegus (%ss). Kui mudel on suur või CPU aeglane, "
                "tõsta OLLAMA_TIMEOUT .env failis.",
                self.timeout,
            )

            if not self.allow_mock:
                raise RuntimeError("Ollama päring aegus ja testvastuseid pole lubatud.")

            logger.warning("Kasutan testvastust.")
            mock = self._mock_analysis(case_desc, laws)
            return mock, True, self.claims_from_verified_analysis(mock, laws)

        except Exception as exc:
            logger.error("Viga AI analüüsil: %s", exc)

            if not self.allow_mock:
                raise

            mock = self._mock_analysis(case_desc, laws)
            return mock, True, self.claims_from_verified_analysis(mock, laws)

        analysis = self._prepare_output(raw_response, laws, case_desc)

        # Kui AI ei lisanud korrektseid viiteid, tee seadistatud arv paranduspäringuid.
        previous_response = raw_response
        for attempt in range(self.citation_retries):
            if self._has_valid_citations(analysis, laws):
                break
            try:
                retry_prompt = self._build_retry_prompt(
                    case_desc=case_desc,
                    laws=laws,
                    event_date=event_date,
                    previous_response=previous_response,
                    document_spans=document_spans,
                )
                retry_raw = self._call_ollama(
                    retry_prompt,
                    response_schema=response_schema,
                )
                retry_analysis = self._prepare_output(retry_raw, laws, case_desc)
                previous_response = retry_raw
                analysis = retry_analysis
            except Exception as exc:
                logger.warning(
                    "AI viidete paranduspäring %d/%d ebaõnnestus: %s",
                    attempt + 1,
                    self.citation_retries,
                    exc,
                )
                break

        if not self._has_valid_citations(analysis, laws):
            logger.warning(
                "AI vastus ei läbinud allikakontrolli ka pärast %d paranduspäringut.",
                self.citation_retries,
            )

        return analysis, False, self._verified_claims_from_raw(
            previous_response,
            laws,
            document_spans,
        )

    def _verified_claims_from_raw(
        self,
        raw_response: str,
        laws: List[Dict],
        document_spans: List[Dict] = None,
    ) -> List[Dict]:
        """Preserve the exact evidence that passed the V7 model-output gate."""
        payload = self._extract_json_payload(self._strip_code_fences(raw_response or ""))
        if not isinstance(payload, dict) or not isinstance(payload.get("claims"), list):
            return []
        law_map = {
            self._normalize_id(law.get("id")): law
            for law in laws
            if law.get("id")
        }
        verified: List[Dict] = []
        for item in payload["claims"]:
            if not isinstance(item, dict):
                continue
            source_id = self._normalize_id(item.get("source_id"))
            law = law_map.get(source_id)
            evidence = self._clean_generated_text(item.get("evidence"))
            claim_text = self._clean_generated_text(item.get("text"))
            if law is None or not self._evidence_is_valid(evidence, law):
                continue
            if not self._claim_is_supported_by_evidence(claim_text, evidence):
                claim_text = evidence
            if not claim_text:
                continue
            verified.append({
                "claim_id": f"LAW-{len(verified) + 1}",
                "kind": "law",
                "text": claim_text,
                "verification_status": "EVIDENCE_VERIFIED",
                "sources": [{
                    "kind": "law",
                    "id": source_id,
                    "title": str(law.get("title", source_id)),
                    "source": str(law.get("source", "")),
                    "evidence": evidence,
                }],
            })
            if len(verified) > 1:
                previous = verified[:-1]
                current = verified[-1]
                duplicate = any(
                    self._normalize_evidence_text(item.get("text"))
                    == self._normalize_evidence_text(current.get("text"))
                    or (
                        item.get("sources", [{}])[0].get("id") == source_id
                        and self._normalize_evidence_text(
                            item.get("sources", [{}])[0].get("evidence")
                        ) == self._normalize_evidence_text(evidence)
                    )
                    for item in previous
                    if item.get("kind") == "law"
                )
                if duplicate:
                    verified.pop()
        span_map = {
            self._normalize_id(span.get("span_id")): span
            for span in (document_spans or [])
            if span.get("span_id")
        }
        comparisons = payload.get("comparisons", [])
        if isinstance(comparisons, list):
            for item in comparisons[:2]:
                if not isinstance(item, dict):
                    continue
                law_id = self._normalize_id(item.get("law_source_id"))
                span_id = self._normalize_id(item.get("document_span_id"))
                law = law_map.get(law_id)
                span = span_map.get(span_id)
                law_evidence = self._clean_generated_text(item.get("law_evidence"))
                document_evidence = self._clean_generated_text(
                    item.get("document_evidence")
                )
                comparison_text = self._clean_generated_text(item.get("text"))
                if (
                    law is None
                    or span is None
                    or not self._evidence_is_valid(law_evidence, law)
                    or not self._document_evidence_is_valid(document_evidence, span)
                    or not self._comparison_is_supported_by_evidence(
                        comparison_text,
                        law_evidence,
                        document_evidence,
                    )
                ):
                    continue
                document_offset = str(span.get("text", "")).find(document_evidence)
                evidence_start = int(span.get("start") or 0) + document_offset
                verified.append({
                    "claim_id": f"CMP-{len(verified) + 1}",
                    "kind": "inference",
                    "text": comparison_text,
                    "verification_status": "INPUTS_VERIFIED",
                    "sources": [
                        {
                            "kind": "law",
                            "id": law_id,
                            "title": str(law.get("title", law_id)),
                            "source": str(law.get("source", "")),
                            "evidence": law_evidence,
                        },
                        {
                            "kind": "document",
                            "id": str(span.get("span_id", "")),
                            "document_id": str(span.get("document_id", "")),
                            "title": str(span.get("file_name", "")),
                            "source": f"Dokument, lk {span.get('page', 1)}",
                            "evidence": document_evidence,
                            "page": int(span.get("page") or 1),
                            "start": evidence_start,
                            "end": evidence_start + len(document_evidence),
                            "method": str(span.get("method") or "text"),
                        },
                    ],
                })
        return verified

    def claims_from_verified_analysis(
        self,
        analysis_text: str,
        laws: List[Dict],
    ) -> List[Dict]:
        """Build an inspectable claim list for a citation-verified fallback."""
        law_map = {
            self._normalize_id(law.get("id")): law
            for law in laws
            if law.get("id")
        }
        match = self.source_verifier.APPLICATION_SECTION.search(analysis_text or "")
        if not match:
            return []
        results: List[Dict] = []
        for sentence in self.source_verifier._split_claims(match.group(1)):
            cited = self.source_verifier.CITATION_PATTERN.findall(sentence)
            if not cited:
                continue
            source_id = self._normalize_id(cited[0])
            law = law_map.get(source_id)
            if law is None:
                continue
            clean_text = self._clean_generated_text(sentence).rstrip(".!?;:")
            evidence = self._best_source_excerpt(clean_text, str(law.get("text", "")))
            results.append({
                "claim_id": f"LAW-{len(results) + 1}",
                "kind": "law",
                "text": clean_text,
                "verification_status": "CITATION_VERIFIED",
                "sources": [{
                    "kind": "law",
                    "id": source_id,
                    "title": str(law.get("title", source_id)),
                    "source": str(law.get("source", "")),
                    "evidence": evidence,
                }],
            })
        return results

    @staticmethod
    def _best_source_excerpt(claim: str, source_text: str) -> str:
        sentences = [
            value.strip()
            for value in re.split(r"(?<=[.!?])\s+|\n+", source_text or "")
            if value.strip()
        ]
        if not sentences:
            return re.sub(r"\s+", " ", source_text or "").strip()[:500]
        tokens = set(re.findall(r"[a-zõäöü]{4,}", (claim or "").casefold()))
        best = max(
            sentences,
            key=lambda value: len(tokens.intersection(
                re.findall(r"[a-zõäöü]{4,}", value.casefold())
            )),
        )
        return re.sub(r"\s+", " ", best).strip()[:500]

    def build_source_only_fallback(
        self,
        case_desc: str,
        laws: List[Dict],
    ) -> str:
        """Return a deterministic cited source digest when model output is unavailable.

        This does not invent an applied legal conclusion. It still gives the user
        useful, verified source text instead of turning a local model or citation
        formatting failure into an empty error response.
        """
        turn_fallback = self._build_focused_turn_fallback(case_desc, laws)
        if turn_fallback:
            return turn_fallback
        focused_fallback = self._build_focused_fine_fallback(case_desc, laws)
        if focused_fallback:
            return focused_fallback

        lines = [
            "OLUKORD:",
            "Leidsin küsimusega seotud kontrollitud sätted ja toon välja nende põhisisu.",
            "",
            "ÕIGUSLIK KOHALDAMINE:",
        ]
        for law in laws:
            text = re.sub(r"\s+", " ", str(law.get("text", ""))).strip()
            text = re.sub(r"^\S+\s+§\s+\S+\.\s*", "", text)
            first_sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
            excerpt = first_sentence.strip().rstrip(".!?;:")
            if len(excerpt) > 320:
                excerpt = excerpt[:317].rsplit(" ", 1)[0] + "…"
            lines.append(
                f"{law['title']}: {excerpt} [{law['id']}]."
            )

        citations = " ".join(f"[{law['id']}]" for law in laws)
        recommendations = self._build_recommendations(case_desc, laws)
        lines.extend([
            "",
            "SOOVITUSED:",
            *recommendations,
            "",
            f"KASUTATUD ALLIKAD: {citations}",
        ])
        return "\n".join(lines).strip()

    @staticmethod
    def _build_focused_turn_fallback(case_desc: str, laws: List[Dict]) -> str:
        """Answer every detected latest-turn obligation from audited source text."""
        current_message = str(case_desc or "")
        marker = "KASUTAJA VIIMANE SÕNUM:"
        if marker in current_message:
            current_message = current_message.split(marker, 1)[1]
            current_message = current_message.replace(
                "Kasuta viimast sõnumit koos alltoodud vastusekohustustega. "
                "Varasem tekst on ainult taust.",
                " ",
            ).strip()
        intents = set(ConversationTurnPlanner.detect_intents(current_message))
        if not intents.intersection({"missed_deadline", "payment_plan"}):
            return ""

        available = {
            str(law.get("id", "")).strip().upper(): law
            for law in laws
            if law.get("id")
        }
        claims: List[str] = []
        used_ids: List[str] = []
        if "missed_deadline" in intents and "VTMS_118" in available:
            claims.append(
                "Kui kaebus esitatakse pärast VTMS §-s 114 sätestatud tähtaja "
                "möödumist ning tähtaja ennistamise taotlust ei esitata või tähtaega "
                "ei ennistata, jäetakse kaebus läbi vaatamata [VTMS_118]."
            )
            used_ids.append("VTMS_118")
        if "payment_plan" in intents and "KARS_66" in available:
            claims.append(
                "Kohus või väärtegu menetlev kohtuväline menetleja võib mõjuvatel "
                "põhjustel määrata rahatrahvi tasumise ositi [KARS_66]."
            )
            used_ids.append("KARS_66")
        if "payment_plan" in intents and "VTMS_204" in available:
            claims.append(
                "Kui rahatrahvi ei tasuta tähtajaks või osastatud rahatrahvi "
                "maksetähtaegu ei järgita, saadetakse lahend täitmiseks "
                "kohtutäiturile [VTMS_204]."
            )
            used_ids.append("VTMS_204")
        if not claims:
            return ""

        return (
            "OLUKORD:\n"
            "Viimane küsimus puudutab eraldi möödunud kaebetähtaega ja rahatrahvi "
            "ositi tasumist. Täpne menetlustee sõltub dokumendi liigist, "
            "kättesaamise ajast ja sellest, kas lahend on juba täitmisel.\n\n"
            "ÕIGUSLIK KOHALDAMINE:\n"
            + "\n".join(claims)
            + "\n\nSOOVITUSED:\n"
            "1. Kontrolli dokumendi täpset pealkirja ja sellele märgitud "
            "edasikaebamise korda.\n"
            "2. Pane kirja dokumendi kättesaamise kuupäev ja tähtaja möödalaskmise "
            "põhjus.\n"
            "3. Kontrolli, kas rahatrahv on veel menetleja juures või juba "
            "kohtutäituri käes.\n\n"
            "KASUTATUD ALLIKAD: "
            + " ".join(f"[{law_id}]" for law_id in used_ids)
        )

    @staticmethod
    def _build_focused_fine_fallback(
        case_desc: str,
        laws: List[Dict],
    ) -> str:
        """Give a useful, conditional answer for the audited trahviteade route.

        Users often call every fine document a ``trahviteade``.  The dedicated
        fallback therefore explains what the formal document would mean without
        silently treating a lühimenetluse or kiirmenetluse decision as the same
        document.  It is enabled only when the exact audited source set is
        available.
        """
        normalized_case = str(case_desc or "").casefold()
        available_ids = {
            str(law.get("id", "")).strip().upper()
            for law in laws
            if law.get("id")
        }
        required_ids = {
            "ABIPOLS_3",
            "ABIPOLS_16",
            "VTMS_54B2",
            "VTMS_54B5",
        }
        is_auxiliary_police_fine = (
            "abipolitsei" in normalized_case
            and any(term in normalized_case for term in (
                "trahv", "trahvitea", "trahviotsus", "väärteo"
            ))
        )
        if not is_auxiliary_police_fine or not required_ids.issubset(available_ids):
            return ""

        return (
            "OLUKORD:\n"
            "Sinu kirjelduse järgi tuleb eraldi kontrollida abipolitseiniku rolli "
            "ja saadud dokumendi liiki. Sõna „trahviteade” võib tavakeeles tähendada "
            "eri dokumente, seega ei saa ainult selle nimetuse põhjal veel valida "
            "kindlat vaidlustamise tähtaega.\n\n"
            "ÕIGUSLIK KOHALDAMINE:\n"
            "Abipolitseiniku pädevus hõlmab politsei abistamist avalikku korda "
            "ähvardava ohu ennetamisel, väljaselgitamisel ja tõrjumisel ning avaliku "
            "korra rikkumise kõrvaldamisel [ABIPOLS_3].\n"
            "Abipolitseiniku kasutatavad järelevalvemeetmed sõltuvad muu hulgas "
            "sellest, kas ta tegutseb politseiametniku korraldusel või täidab politsei "
            "ülesannet iseseisvalt [ABIPOLS_16].\n"
            "Ametlik hoiatustrahvi trahviteade peab sisaldama kohtuvälise menetleja "
            "nime, teate koostanud ametniku andmeid, väärteo lühikirjeldust ja "
            "kvalifikatsiooni [VTMS_54B2].\n"
            "Kui saadud dokument on just selline mootorsõiduki eest vastutavale "
            "isikule saadetud hoiatustrahvi trahviteade, saab selle vaidlustada "
            "30 päeva jooksul kättesaamisest ning kirjalik kaebus esitatakse teate "
            "koostanud kohtuvälisele menetlejale [VTMS_54B5].\n"
            "Praeguse info põhjal ei saa veel kinnitada, et see 30-päevane kord sinu "
            "dokumendile kehtib, ega hinnata trahvi sisulist põhjendatust; selleks on "
            "vaja dokumendi täpset pealkirja, menetlejat ja rikkumise kvalifikatsiooni "
            "[VTMS_54B2].\n\n"
            "SOOVITUSED:\n"
            "1. Kui sulle anti paber või e-kiri, säilita see ja pane kirja, millal "
            "sa selle said.\n"
            "2. Kopeeri dokumendilt selle pealkiri, väljaandja nimi ja rikkumise "
            "kirjeldus; nende põhjal saab valida õige vaidlustamise viisi.\n"
            "3. Kui dokumenti ei antud, küsi selle koopia. Kui dokument on olemas, "
            "ära lase sellel märgitud vaidlustamise tähtajal mööduda.\n\n"
            "KASUTATUD ALLIKAD: [ABIPOLS_3] [ABIPOLS_16] [VTMS_54B2] [VTMS_54B5]"
        )

    # ------------------------------------------------------------------
    # Ollama päring
    # ------------------------------------------------------------------

    def generate_structured(self, prompt: str, response_schema: Dict) -> str:
        """Generate one JSON-schema-constrained response with the local model."""
        return self._call_ollama(prompt, response_schema=response_schema)

    def prepare_structured_response(
        self,
        raw_response: str,
        laws: List[Dict],
        case_desc: str = "",
    ) -> Tuple[str, List[Dict]]:
        """Render and evidence-gate one already generated structured response."""
        analysis = self._prepare_output(raw_response, laws, case_desc)
        claims = self._verified_claims_from_raw(raw_response, laws, [])
        return analysis, claims

    def prepare_structured_repair_response(
        self,
        raw_response: str,
        laws: List[Dict],
        case_desc: str = "",
    ) -> Tuple[str, List[Dict], Dict]:
        """Evidence-gate a focused repair with deterministic evidence recovery.

        Recovery is intentionally narrow: the model must already return an audited
        source_id and a claim that is supported by the recovered exact source
        sentence. Only an inexact evidence quotation is repaired. Unsupported claim
        text and unknown source IDs remain fail-closed.
        """
        diagnostics: Dict = {
            "parse_valid": False,
            "raw_claim_count": 0,
            "raw_source_ids": [],
            "accepted_claim_count": 0,
            "accepted_source_ids": [],
            "evidence_recovered_count": 0,
            "dropped_claims": [],
        }
        if getattr(self, "repair_debug", False):
            diagnostics["claim_diagnostics"] = []
        payload = self._extract_json_payload(
            self._strip_code_fences(raw_response or "")
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("claims"), list):
            diagnostics["dropped_claims"].append({
                "index": 0,
                "source_id": "",
                "reason": "invalid_json_or_claims",
            })
            return "", [], diagnostics

        diagnostics["parse_valid"] = True
        raw_claims = list(payload.get("claims") or [])
        diagnostics["raw_claim_count"] = len(raw_claims)
        law_map = {
            self._normalize_id(law.get("id")): law
            for law in laws
            if law.get("id")
        }
        normalized_claims: List[Dict] = []

        for index, item in enumerate(raw_claims, start=1):
            if not isinstance(item, dict):
                diagnostics["dropped_claims"].append({
                    "index": index,
                    "source_id": "",
                    "reason": "invalid_claim_shape",
                })
                continue
            source_id = self._normalize_id(item.get("source_id"))
            if source_id:
                diagnostics["raw_source_ids"].append(source_id)
            law = law_map.get(source_id)
            if law is None:
                diagnostics["dropped_claims"].append({
                    "index": index,
                    "source_id": source_id,
                    "reason": "unknown_source",
                })
                continue

            claim_text = self._clean_generated_text(item.get("text"))
            evidence = self._clean_generated_text(item.get("evidence"))
            if not claim_text:
                diagnostics["dropped_claims"].append({
                    "index": index,
                    "source_id": source_id,
                    "reason": "empty_claim_text",
                })
                continue

            claim_debug = None
            if getattr(self, "repair_debug", False):
                claim_debug = {
                    "index": index,
                    "source_id": source_id,
                    "claim_text": claim_text[:600],
                    "model_evidence": evidence[:600],
                    "model_evidence_is_exact": self._evidence_is_valid(evidence, law),
                    "model_support": self._claim_support_debug(claim_text, evidence),
                    "candidate_attempts": [],
                    "outcome": "",
                }
                diagnostics["claim_diagnostics"].append(claim_debug)

            recovered = False
            if not self._evidence_is_valid(evidence, law):
                evidence = self._recover_source_evidence(
                    claim_text, evidence, law, debug=claim_debug
                )
                if not evidence:
                    if claim_debug is not None:
                        claim_debug["outcome"] = "evidence_not_recoverable"
                    diagnostics["dropped_claims"].append({
                        "index": index,
                        "source_id": source_id,
                        "reason": "evidence_not_recoverable",
                    })
                    continue
                recovered = True

            support_passed = self._claim_is_supported_by_evidence(claim_text, evidence)
            if claim_debug is not None:
                claim_debug["final_evidence"] = evidence[:600]
                claim_debug["final_evidence_is_exact"] = self._evidence_is_valid(
                    evidence, law
                )
                claim_debug["final_support"] = self._claim_support_debug(
                    claim_text, evidence
                )
            if not support_passed:
                reason = (
                    "claim_not_supported_by_recovered_evidence"
                    if recovered
                    else "claim_not_supported_by_evidence"
                )
                if claim_debug is not None:
                    claim_debug["outcome"] = reason
                diagnostics["dropped_claims"].append({
                    "index": index,
                    "source_id": source_id,
                    "reason": reason,
                })
                continue

            if recovered:
                diagnostics["evidence_recovered_count"] += 1
            if claim_debug is not None:
                claim_debug["outcome"] = "accepted"
            normalized_claims.append({
                "text": claim_text,
                "source_id": source_id,
                "evidence": evidence,
            })

        normalized_raw = json.dumps(
            {"claims": normalized_claims},
            ensure_ascii=False,
        )
        analysis = self._prepare_output(normalized_raw, laws, case_desc)
        claims = self._verified_claims_from_raw(normalized_raw, laws, [])
        diagnostics["accepted_claim_count"] = len(claims)
        accepted_ids: List[str] = []
        for claim in claims:
            for source in claim.get("sources") or []:
                source_id = self._normalize_id(source.get("id"))
                if source_id and source_id not in accepted_ids:
                    accepted_ids.append(source_id)
        diagnostics["accepted_source_ids"] = accepted_ids
        return analysis, claims, diagnostics

    def _recover_source_evidence(
        self,
        claim_text: str,
        model_evidence: str,
        law: Dict,
        debug: Dict = None,
    ) -> str:
        """Choose a close exact source sentence without creating legal text."""
        source_text = str(law.get("text", "") or "")
        candidates = [
            value.strip()
            for value in re.split(r"(?<=[.!?])\s+|\n+", source_text)
            if value.strip() and self._evidence_is_valid(value.strip(), law)
        ]
        if not candidates:
            return ""

        ignored = {
            "selle", "ning", "kuid", "vaid", "tuleb", "saab", "peab",
            "kohta", "korral", "alusel", "vastavalt",
        }
        query_tokens = {
            token
            for token in re.findall(
                r"[a-zõäöü]{4,}",
                f"{claim_text} {model_evidence}".casefold(),
            )
            if token not in ignored
        }
        if not query_tokens:
            return ""

        def score(candidate: str) -> Tuple[int, float, int]:
            candidate_tokens = set(
                re.findall(r"[a-zõäöü]{4,}", candidate.casefold())
            )
            overlap = len(query_tokens.intersection(candidate_tokens))
            ratio = overlap / max(1, len(query_tokens))
            return overlap, ratio, -len(candidate)

        ranked = sorted(candidates, key=score, reverse=True)
        required_overlap = 1 if len(query_tokens) < 3 else 2
        for rank, candidate in enumerate(ranked, start=1):
            overlap, ratio, _length = score(candidate)
            meets_overlap = overlap >= required_overlap
            support_passed = (
                self._claim_is_supported_by_evidence(claim_text, candidate)
                if meets_overlap
                else False
            )
            if debug is not None:
                debug["candidate_attempts"].append({
                    "rank": rank,
                    "excerpt": candidate[:600],
                    "lexical_overlap": overlap,
                    "lexical_ratio": round(ratio, 4),
                    "required_overlap": required_overlap,
                    "meets_overlap": meets_overlap,
                    "support_passed": support_passed,
                    "support": self._claim_support_debug(claim_text, candidate),
                })
            if not meets_overlap or not support_passed:
                continue
            return candidate
        return ""

    @staticmethod
    def _extract_quantities(value: str) -> set:
        """Extract quantities while canonicalizing only Estonian case variants.

        Word forms stay separate from digits so a structural source number such as
        ``1`` cannot accidentally support a model claim that says ``üks kuu``.
        """
        quantity_pattern = re.compile(
            r"(?<!\w)(?:\d+(?:[.,]\d+)?%?|üks|ühe|üht|kaks|kahe|kahte|"
            r"kolm|kolme|nelja|neli|viis|viie|kuus|kuue|seitse|seitsme|"
            r"kaheksa|üheksa|kümme|kümne|sada|saja|tuhat|tuhande)(?!\w)",
            re.IGNORECASE,
        )
        canonical_words = {
            "ühe": "üks",
            "üht": "üks",
            "kahe": "kaks",
            "kahte": "kaks",
            "kolme": "kolm",
            "nelja": "neli",
            "viie": "viis",
            "kuue": "kuus",
            "seitsme": "seitse",
            "kümne": "kümme",
            "saja": "sada",
            "tuhande": "tuhat",
        }
        return {
            canonical_words.get(token.casefold(), token.casefold())
            for token in quantity_pattern.findall(str(value or ""))
        }

    def _claim_support_debug(self, claim: str, evidence: str) -> Dict:
        """Explain the existing lexical support gate without changing its result."""
        claim_norm = self._normalize_evidence_text(claim)
        evidence_norm = self._normalize_evidence_text(evidence)
        inference_markers = (
            "mis tähendab",
            "seega",
            "järelikult",
            "mistõttu",
            "sellest tuleneb",
        )
        missing_inference_markers = [
            marker
            for marker in inference_markers
            if marker in claim_norm and marker not in evidence_norm
        ]
        claim_quantities = self._extract_quantities(claim_norm)
        evidence_quantities = self._extract_quantities(evidence_norm)
        ignored = {"selle", "ning", "kuid", "vaid", "tuleb", "saab", "peab"}
        claim_tokens = {
            token
            for token in re.findall(r"[a-zõäöü]{4,}", claim_norm)
            if token not in ignored
        }
        evidence_tokens = set(re.findall(r"[a-zõäöü]{4,}", evidence_norm))
        overlap_tokens = claim_tokens.intersection(evidence_tokens)
        overlap_ratio = (
            len(overlap_tokens) / len(claim_tokens) if claim_tokens else 0.0
        )
        return {
            "passed": self._claim_is_supported_by_evidence(claim, evidence),
            "missing_inference_markers": missing_inference_markers,
            "claim_quantities": sorted(claim_quantities),
            "evidence_quantities": sorted(evidence_quantities),
            "missing_quantities": sorted(claim_quantities - evidence_quantities),
            "claim_token_count": len(claim_tokens),
            "overlap_token_count": len(overlap_tokens),
            "token_overlap_ratio": round(overlap_ratio, 4),
            "overlap_tokens": sorted(overlap_tokens)[:40],
            "missing_claim_tokens": sorted(claim_tokens - evidence_tokens)[:40],
        }

    def _call_ollama(self, prompt: str, response_schema: Dict = None) -> str:
        options = {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
        }
        if self.generation_seed is not None:
            options["seed"] = self.generation_seed

        response = requests.post(
            f"{self.ollama_url}/api/generate",
            json={
                "model": self.model_name,
                "prompt": prompt,
                "stream": False,
                # Ollama piirab väljundi JSON skeemiga. Nii ei sõltu [ID] viidete
                # paigutus väikese kohaliku mudeli vormindusoskusest.
                "format": response_schema or AI_RESPONSE_SCHEMA,
                "think": self.think,
                "keep_alive": self.keep_alive,
                "options": options,
            },
            timeout=self.timeout,
        )

        if response.status_code == 200:
            result = response.json()
            text = result.get("response", "").strip()

            if not text:
                raise ValueError("Ollama tagastas tühja vastuse.")

            return text

        if response.status_code == 404:
            raise RuntimeError(
                f"Mudelit '{self.model_name}' ei leitud Ollamast. "
                f"Käivita: ollama pull {self.model_name}"
            )

        raise RuntimeError(
            f"Ollama API viga: {response.status_code} - {response.text[:200]}"
        )

    # ------------------------------------------------------------------
    # Promptide ehitamine
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        case_desc: str,
        laws: List[Dict],
        event_date: str = "",
        document_spans: List[Dict] = None,
    ) -> str:
        laws_text = "\n\n".join(
            f"{law['title']} [{law['id']}]: {law['text']}" for law in laws
        )

        event_line = ""
        if event_date:
            event_line = f"Sündmuse kuupäev: {event_date}\n"

        allowed_ids = ", ".join(law["id"] for law in laws)
        focus_rules = self._build_focus_rules(case_desc, laws)
        document_spans = list(document_spans or [])[:5]
        document_section = ""
        document_rules = ""
        response_example = """{
  "claims": [
    {
      "text": "Üks konkreetne õiguslik väide ühe lausena.",
      "source_id": "VOS_308",
      "evidence": "Sama väidet toetav täpne katkend allika tekstist."
    }
  ]
}"""
        if document_spans:
            document_section = "\n\nDOKUMENDIKATKENDID:\n" + "\n".join(
                f"[{span['span_id']}] {span.get('file_name', 'dokument')}, "
                f"lk {span.get('page', 1)}: {span.get('text', '')}"
                for span in document_spans
            )
            document_rules = """
13. Dokumendikatkendid on faktiline sisend, mitte õigusallikad.
14. Kui dokumendi ja seaduse otsene võrdlus aitab küsimusele vastata, lisa kuni kaks comparisons elementi.
15. comparison peab viitama ühele lubatud seaduse ID-le ja ühele täpselt etteantud dokumendikatkendi ID-le.
16. law_evidence ja document_evidence peavad olema vastavast allikast täpselt kopeeritud katkematud katkendid.
17. comparison tekst tohib võrrelda ainult neis kahes katkendis sõnaselgelt olevat. Ära nimeta dokumenti kehtetuks, ebaseaduslikuks ega lõplikult õigeks/valeks.
18. Kui turvalist otsest võrdlust ei saa teha, tagasta comparisons tühja massiivina.
"""
            response_example = """{
  "claims": [
    {
      "text": "Üks konkreetne õiguslik väide ühe lausena.",
      "source_id": "VOS_308",
      "evidence": "Sama väidet toetav täpne katkend seaduse tekstist."
    }
  ],
  "comparisons": [
    {
      "text": "Dokumendi ja sätte kitsas võrdlus ilma lõpliku õigusliku hinnanguta.",
      "law_source_id": "VOS_308",
      "law_evidence": "Täpselt kopeeritud katkend seadusest.",
      "document_span_id": "DOC-ABC-P1-S1",
      "document_evidence": "Täpselt kopeeritud katkend dokumendist."
    }
  ]
}"""

        return f"""Sa oled Eesti õiguse analüütik.

Sinu ülesanne on analüüsida kasutaja olukorda AINULT allpool loetletud seaduste põhjal.

{event_line}JUHTUM:
{case_desc}

KOHALDATAVAD SEADUSED:
{laws_text}
{document_section}

RANGED SISUREEGLID:
1. Kasuta AINULT ülal loetletud allikate teksti.
2. Ära mõtle välja ühtegi normi, paragrahvi, ID-d, tähtaega ega allikat.
3. Vasta kasutaja konkreetsele küsimusele, mitte ära piirdu allikate ümberjutustamisega.
4. Iga õiguslik väide peab olema üks terviklik lause ja selle juures peab olema üks source_id.
5. source_id tohib olla AINULT üks neist ID-dest: {allowed_ids}.
6. evidence peab olema sama source_id allika tekstist kopeeritud üks täpne lause või katkematu lauseosa.
7. Väide peab olema evidence teksti otsene ja kitsas ümbersõnastus. Ära lisa evidence tekstis puuduvat järeldust, erandit ega tagajärge.
8. Kõik väites olevad arvud, summad, protsendid ja ajavahemikud peavad esinema evidence tekstis samas tähenduses.
9. Kui allikad ei võimalda lõplikku järeldust, kirjelda ainult seda, mida neist saab kindlalt järeldada.
10. Ära korda kasutaja mainitud arvu väites, kui see arv ei esine toetavas evidence tekstis.
11. Tagasta 1–3 kõige otsesemalt küsimusele vastavat väidet. Ära lisa kõrvalteemalisi sätteid pelgalt sarnaste sõnade tõttu.
12. Eelista nimekirjas eespool olevat allikat, kui see vastab küsimusele täielikult.
{focus_rules}
{document_rules}

TAGASTA AINULT KEHTIV JSON, ilma Markdowni ja selgitava tekstita:
{response_example}
"""

    @staticmethod
    def _build_focus_rules(case_desc: str, laws: List[Dict]) -> str:
        normalized_case = str(case_desc or "").casefold()
        current_message = str(case_desc or "")
        marker = "KASUTAJA VIIMANE SÕNUM:"
        if marker in current_message:
            current_message = current_message.split(marker, 1)[1]
            current_message = current_message.replace(
                "Kasuta viimast sõnumit koos alltoodud vastusekohustustega. "
                "Varasem tekst on ainult taust.",
                " ",
            ).strip()
        current_intents = ConversationTurnPlanner.detect_intents(current_message)
        domains = {
            str(law.get("domain") or law.get("id", "").split("_", 1)[0]).upper()
            for law in laws
        }
        is_auxiliary_police_fine = (
            "abipolitsei" in normalized_case
            and any(term in normalized_case for term in (
                "trahv", "trahvitea", "trahviotsus", "väärteo"
            ))
            and {"ABIPOLS", "VTMS"}.issubset(domains)
        )
        rules: List[str] = []
        latest_turn_changes_focus = any(
            value in current_intents
            for value in ("missed_deadline", "payment_plan", "document_help")
        )
        if is_auxiliary_police_fine and not latest_turn_changes_focus:
            rules.append(
                "13. Juhtum puudutab nii abipolitseiniku pädevust kui trahvimenetlust. "
                "Tagasta vähemalt kaks väidet: vähemalt üks ABIPOLS allikast "
                "abipolitseiniku pädevuse või meetmete kohta ja vähemalt üks VTMS "
                "allikast menetlusaluse õiguste, otsuse sisu või vaidlustamise kohta."
            )
        if "trahv" in normalized_case or "väärteo" in normalized_case:
            rules.append(
                "14. Kui allikate hulgas ei ole dokumendil märgitud rikkumise täpset "
                "sätet, ära nimeta trahvi sisuliselt põhjendatuks ega põhjendamatuks."
            )
        if {"VTMS_54B2", "VTMS_54B5"}.intersection(
            {str(law.get("id", "")).upper() for law in laws}
        ):
            rules.append(
                "15. VTMS_54B2 ja VTMS_54B5 käsitlevad mootorsõiduki eest vastutavale "
                "isikule saadetud hoiatustrahvi trahviteadet. Kui juhtumikirjeldus ei "
                "kinnita mootorsõidukit ega hoiatustrahvi, sõnasta nende kohaldamine "
                "selgelt tingimuslikult."
            )
        if {"VTMS_57", "VTMS_114"}.intersection(
            {str(law.get("id", "")).upper() for law in laws}
        ):
            rules.append(
                "16. Kui juhtumikirjeldusest ei selgu dokumendi liik, ära eelda, et "
                "tegemist on kiirmenetluse otsusega. Dokumendiliigist sõltuv kord tuleb "
                "esitada tingimuslikult."
            )
        if "missed_deadline" in current_intents:
            rules.append(
                "17. Viimane sõnum küsib möödunud kaebetähtaja kohta. Vastus peab "
                "eraldi käsitlema tähtaja möödumise tagajärge ja tähtaja ennistamise "
                "taotluse võimalust; ära piirdu tavalise kaebetähtaja nimetamisega."
            )
        if "payment_plan" in current_intents:
            rules.append(
                "18. Viimane sõnum küsib rahatrahvi ositi tasumise kohta. Vastus peab "
                "eraldi käsitlema ositi tasumist ja seda, miks täitmise seis võib "
                "muuta pädevat adressaati; ära vasta üksnes vaidlustamise kohta."
            )
        if {"missed_deadline", "payment_plan"}.issubset(set(current_intents)):
            rules.append(
                "19. Kasutaja esitas kaks iseseisvat küsimust. Tagasta vähemalt üks "
                "kontrollitud väide tähtaja kohta ja vähemalt üks kontrollitud väide "
                "ositi tasumise kohta."
            )
        return "\n".join(rules)

    def _build_retry_prompt(
        self,
        case_desc: str,
        laws: List[Dict],
        event_date: str,
        previous_response: str,
        document_spans: List[Dict] = None,
    ) -> str:
        base_prompt = self._build_prompt(
            case_desc,
            laws,
            event_date,
            document_spans,
        )

        previous = (previous_response or "").strip()
        if len(previous) > 1500:
            previous = previous[:1500] + "..."

        allowed_ids = ", ".join(law["id"] for law in laws)

        return f"""{base_prompt}

EELMINE VASTUS:
{previous}

PARANDUSJUHI:
Eelmine vastus ei läbinud allikakontrolli.

Palun kirjuta vastus uuesti nii, et:
- väljund on ainult kehtiv JSON täpselt ülal näidatud struktuuriga;
- claims massiivi iga element sisaldab üht terviklikku lauset, üht source_id väärtust ja allikast täpselt kopeeritud evidence katkendit;
- kasutatakse ainult neid ID-sid: {allowed_ids};
- väide ei lisa evidence katkendis puuduvat arvu, tähtaega ega järeldust;
- vastus ei sisalda ühtegi muud välja, pealkirja ega Markdowni.
"""

    # ------------------------------------------------------------------
    # Väljundi ettevalmistamine
    # ------------------------------------------------------------------

    def _prepare_output(
        self,
        text: str,
        laws: List[Dict],
        case_desc: str = "",
    ) -> str:
        """
        Tegeleb AI toorvastusega:
        - eemaldab koodiplokid;
        - kui vastus on JSON, vormindab selle tekstiks;
        - normaliseerib [ID] viiteid;
        - märgib tundmatud ID-laadsed tokenid, et verifier saaks need tagasi lükata.
        """
        if not text:
            return ""

        text = self._strip_code_fences(text)

        payload = self._extract_json_payload(text)

        if payload and isinstance(payload, dict):
            text = self._format_json_payload(payload, laws, case_desc)

        text = self._normalize_citation_markers(text, laws)
        text = self._wrap_id_like_tokens(text, laws)

        return text.strip()

    def _strip_code_fences(self, text: str) -> str:
        text = text.strip()

        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        return text.strip()

    def _extract_json_payload(self, text: str):
        """
        Proovib teksti seest leida esimest kehtivat JSON objekti.
        """
        decoder = json.JSONDecoder()

        for index, char in enumerate(text):
            if char in "{[":
                try:
                    obj, _ = decoder.raw_decode(text[index:])
                    return obj
                except json.JSONDecodeError:
                    continue

        return None

    def _format_json_payload(
        self,
        payload: Dict,
        laws: List[Dict],
        case_desc: str = "",
    ) -> str:
        """
        Kui AI tagastab JSONi, teeme selle kasutajale loetavaks.

        Uue `claims` vormingu puhul seob mudel iga väite allika ID-ga ning
        rakendus paigutab kontrollitud [ID] viite deterministlikult iga lause
        juurde. Nii ei saa muidu sisuliselt kasutatav vastus pelga vormindusvea
        tõttu kaduma minna.
        """
        valid_ids = {law["id"] for law in laws}
        law_map = {self._normalize_id(law["id"]): law for law in laws}

        if "claims" in payload:
            application_lines: List[str] = []
            used_ids: List[str] = []
            seen_evidence_keys = set()
            claims = payload.get("claims")

            if isinstance(claims, list):
                for claim in claims:
                    if not isinstance(claim, dict):
                        continue

                    claim_text = self._clean_generated_text(claim.get("text"))
                    citation_id = self._normalize_id(claim.get("source_id"))
                    evidence = self._clean_generated_text(claim.get("evidence"))

                    # Fail closed: tundmatu ID või allikast puuduv tõend muudab
                    # väite kasutuskõlbmatuks; seda ei seota oletusliku allikaga.
                    if (
                        citation_id not in valid_ids
                        or not self._evidence_is_valid(evidence, law_map[citation_id])
                    ):
                        continue
                    evidence_key = (
                        citation_id,
                        self._normalize_evidence_text(evidence),
                    )
                    if evidence_key in seen_evidence_keys:
                        continue
                    seen_evidence_keys.add(evidence_key)

                    # Kui mudeli ümbersõnastus lisab tõendis puuduva arvu või
                    # järelduse, kuva selle asemel mudeli valitud täpne
                    # allikakatkend. Kasulik vastus säilib, lisandust mitte.
                    render_text = claim_text
                    if not self._claim_is_supported_by_evidence(claim_text, evidence):
                        render_text = evidence

                    citation = f"[{citation_id}]"
                    sentences = self.source_verifier._split_claims(render_text)[:3]
                    rendered_sentence = False
                    for sentence in sentences:
                        sentence = self._clean_generated_text(sentence).rstrip(".!?;:")
                        if len(re.findall(r"[A-Za-zÕÄÖÜõäöü]{2,}", sentence)) < 3:
                            continue
                        application_lines.append(f"{sentence} {citation}.")
                        rendered_sentence = True

                    if rendered_sentence and citation_id not in used_ids:
                        used_ids.append(citation_id)

            if not application_lines:
                # Tühi väljund sunnib sama range kontrolliga paranduspäringu.
                return ""

            # Soovitused on teadlikult deterministlikud. Mudeli vabas vormis
            # soovitus võib muidu sisaldada uut, tõendamata õiguslikku järeldust.
            recommendations = self._build_recommendations(case_desc, laws)

            citations_line = " ".join(f"[{citation_id}]" for citation_id in used_ids)
            return "\n".join([
                "OLUKORD:",
                "Kasutaja kirjeldatud olukorda hinnati leitud kontrollitud allikate põhjal.",
                "",
                "ÕIGUSLIK KOHALDAMINE:",
                *application_lines,
                "",
                "SOOVITUSED:",
                *recommendations,
                "",
                f"KASUTATUD ALLIKAD: {citations_line}",
            ]).strip()

        summary = str(payload.get("summary", "")).strip()
        analysis = str(payload.get("analysis", "")).strip()
        recommendations = str(payload.get("recommendations", "")).strip()

        if not analysis and payload.get("response"):
            analysis = str(payload.get("response")).strip()

        citations_raw = (
            payload.get("citations")
            or payload.get("sources")
            or payload.get("sources_used")
            or []
        )

        if isinstance(citations_raw, str):
            citations_raw = [citations_raw]

        citations = []

        if isinstance(citations_raw, list):
            for citation in citations_raw:
                citation_id = self._normalize_id(str(citation))

                # Siia jõuavad ainult ID-laadsed kandidaadid.
                # Kui ID on vale, võib source_verifier selle hiljem tagasi lükata,
                # kui see jõuab lõppteksti.
                if citation_id and "_" in citation_id and citation_id not in citations:
                    citations.append(citation_id)

        # Leia ka teksti sees olevad viited.
        combined_text = " ".join([summary, analysis, recommendations])

        for match in re.findall(r"\[([A-ZÕÄÖÜa-zõäöü]+_\d+(?:[Bb]\d+|[A-Za-z])?)\]", combined_text):
            citation_id = match.upper()

            if citation_id in valid_ids and citation_id not in citations:
                citations.append(citation_id)

        lines = []

        if summary:
            lines.append(f"OLUKORD:\n{summary}")

        if analysis:
            lines.append(f"\nÕIGUSLIK KOHALDAMINE:\n{analysis}")

        if recommendations:
            lines.append(f"\nSOOVITUSED:\n{recommendations}")

        if citations:
            citations_line = " ".join(f"[{citation_id}]" for citation_id in citations)
            lines.append(f"\nKASUTATUD ALLIKAD: {citations_line}")

        return "\n".join(lines).strip()

    @staticmethod
    def _build_recommendations(case_desc: str, laws: List[Dict]) -> List[str]:
        """Return practical evidence-preservation steps for the detected context."""
        context = " ".join([
            str(case_desc or ""),
            " ".join(str(law.get("domain", "")) for law in laws),
        ]).casefold()

        if any(term in context for term in ("trahv", "väärteo", "vtms")):
            if any(term in context for term in (
                "dokumenti mulle ei antud",
                "dokumenti ei saanud",
                "otsust ei saanud",
                "trahviteadet ei saanud",
            )):
                return [
                    "Pane kirja, kes, millal ja millisel alusel trahvi teatavaks tegi.",
                    "Palu kirjalikult otsuse või trahviteate koopiat koos selle kättetoimetamise andmetega.",
                ]
            return [
                "Säilita trahviotsus, trahviteade või muu saadud dokument muutmata kujul.",
                "Pane kirja dokumendi kättesaamise aeg ning kontrolli sellelt menetlejat, rikkumise kirjeldust, tõendeid ja vaidlustamise tähtaega.",
            ]
        if any(term in context for term in ("üür", "tagatisraha", "vos")):
            return [
                "Säilita üürileping, maksekinnitused ning üürileandjaga peetud kirjavahetus.",
                "Pane kirja nõutud summa, nõude kuupäev ja üürileandja põhjendus.",
            ]
        if any(term in context for term in ("tööandja", "töötaja", "koond", "tls")):
            return [
                "Säilita tööleping, tööandja teated ja töösuhtega seotud kirjavahetus.",
                "Pane kirja teate saamise ning töösuhte muutumise või lõppemise kuupäevad.",
            ]
        return [
            "Säilita olukorraga seotud otsused, teated ja kirjavahetus.",
            "Pane kirja olulised kuupäevad ning täpsusta puuduv asjaolu, kui see võib vastust muuta.",
        ]

    def _clean_generated_text(self, value) -> str:
        """Make one model-provided field safe to place in the rendered response."""
        text = str(value or "")
        text = re.sub(
            r"\[\s*[A-ZÕÄÖÜa-zõäöü]+_\d+(?:[Bb]\d+|[A-Za-z])?\s*\]",
            "",
            text,
        )
        text = re.sub(
            r"\b(?:OLUKORD|ÕIGUSLIK\s+KOHALDAMINE|SOOVITUSED|KASUTATUD\s+ALLIKAD)\s*:",
            " ",
            text,
            flags=re.IGNORECASE,
        )
        return re.sub(r"\s+", " ", text).strip()

    def _evidence_supports_claim(self, claim: str, evidence: str, law: Dict) -> bool:
        """Reject claims whose quoted evidence is absent or materially too weak.

        This is intentionally lexical and conservative. It does not pretend to
        prove legal entailment, but it catches fabricated quotations, unrelated
        source links and extra amounts/time limits added by the model.
        """
        return self._evidence_is_valid(
            evidence, law
        ) and self._claim_is_supported_by_evidence(claim, evidence)

    def _evidence_is_valid(self, evidence: str, law: Dict) -> bool:
        evidence_norm = self._normalize_evidence_text(evidence)
        source_norm = self._normalize_evidence_text(law.get("text", ""))
        return len(evidence_norm) >= 24 and evidence_norm in source_norm

    def _claim_is_supported_by_evidence(self, claim: str, evidence: str) -> bool:
        claim_norm = self._normalize_evidence_text(claim)
        evidence_norm = self._normalize_evidence_text(evidence)
        if not claim_norm:
            return False

        inference_markers = (
            "mis tähendab",
            "seega",
            "järelikult",
            "mistõttu",
            "sellest tuleneb",
        )
        if any(marker in claim_norm and marker not in evidence_norm for marker in inference_markers):
            return False

        claim_quantities = self._extract_quantities(claim_norm)
        evidence_quantities = self._extract_quantities(evidence_norm)
        if not claim_quantities.issubset(evidence_quantities):
            return False

        claim_tokens = {
            token for token in re.findall(r"[a-zõäöü]{4,}", claim_norm)
            if token not in {"selle", "ning", "kuid", "vaid", "tuleb", "saab", "peab"}
        }
        evidence_tokens = set(re.findall(r"[a-zõäöü]{4,}", evidence_norm))
        if len(claim_tokens) >= 4:
            overlap = len(claim_tokens & evidence_tokens) / len(claim_tokens)
            if overlap < 0.5:
                return False

        return True

    def _document_evidence_is_valid(self, evidence: str, span: Dict) -> bool:
        """Require a literal document substring so character offsets stay exact."""
        evidence = str(evidence or "").strip()
        return len(evidence) >= 8 and evidence in str(span.get("text", ""))

    def _comparison_is_supported_by_evidence(
        self,
        claim: str,
        law_evidence: str,
        document_evidence: str,
    ) -> bool:
        """Conservatively admit only comparisons grounded in both exact inputs."""
        claim_norm = self._normalize_evidence_text(claim)
        law_norm = self._normalize_evidence_text(law_evidence)
        document_norm = self._normalize_evidence_text(document_evidence)
        if not claim_norm:
            return False
        forbidden = (
            "seega",
            "järelikult",
            "kindlasti",
            "vaieldamatult",
            "ebaseaduslik",
            "õigustühine",
            "kehtetu",
        )
        if any(marker in claim_norm for marker in forbidden):
            return False

        claim_quantities = self._extract_quantities(claim_norm)
        evidence_quantities = self._extract_quantities(
            f"{law_norm} {document_norm}"
        )
        if not claim_quantities.issubset(evidence_quantities):
            return False

        ignored = {"selle", "ning", "kuid", "vaid", "tuleb", "saab", "peab"}
        claim_tokens = {
            token for token in re.findall(r"[a-zõäöü]{4,}", claim_norm)
            if token not in ignored
        }
        law_tokens = set(re.findall(r"[a-zõäöü]{4,}", law_norm))
        document_tokens = set(re.findall(r"[a-zõäöü]{4,}", document_norm))
        if len(claim_tokens) >= 4:
            combined_overlap = len(
                claim_tokens.intersection(law_tokens | document_tokens)
            ) / len(claim_tokens)
            if combined_overlap < 0.5:
                return False
        return bool(claim_tokens & law_tokens) and bool(claim_tokens & document_tokens)

    @staticmethod
    def _normalize_evidence_text(value) -> str:
        text = html.unescape(str(value or ""))
        text = re.sub(r"<[^>]+>", " ", text)
        text = text.casefold()
        text = text.translate(str.maketrans({"“": '"', "”": '"', "„": '"', "–": "-", "—": "-"}))
        return re.sub(r"\s+", " ", text).strip()

    def _normalize_citation_markers(self, text: str, laws: List[Dict]) -> str:
        """
        Normaliseerib viiteid.

        Näiteks:
        [ vos_123 ] -> [VOS_123]
        [VOS 123]   -> [VOS_123]
        """
        valid_ids = {law["id"] for law in laws}

        def fix_spaced_citation(match: re.Match) -> str:
            prefix = match.group(1)
            number = match.group(2)
            citation_id = self._normalize_id(f"{prefix}_{number}")

            if citation_id in valid_ids:
                return f"[{citation_id}]"

            return match.group(0)

        text = re.sub(
            r"\[\s*([A-ZÕÄÖÜa-zõäöü]+)\s+(\d+(?:[Bb]\d+|[A-Za-z])?)\s*\]",
            fix_spaced_citation,
            text,
            flags=re.IGNORECASE,
        )

        def fix_bracket_citation(match: re.Match) -> str:
            citation_id = self._normalize_id(match.group(1))

            if citation_id in valid_ids:
                return f"[{citation_id}]"

            return match.group(0)

        text = re.sub(
            r"\[\s*([A-ZÕÄÖÜa-zõäöü]+_\d+(?:[Bb]\d+|[A-Za-z])?)\s*\]",
            fix_bracket_citation,
            text,
            flags=re.IGNORECASE,
        )

        return text

    def _wrap_id_like_tokens(self, text: str, laws: List[Dict]) -> str:
        """
        Märgib ID-laadsed tokenid [ID] kujule.

        See aitab source_verifier-il näha ka neid ID-sid, mille AI
        kogemata ilma sulgudeta kirjutas.

        Kui AI kirjutab tundmatu ID, märgitakse ka see [ID]-ks,
        et source_verifier saaks vastuse vajadusel tagasi lükata.
        """
        valid_ids = {law["id"] for law in laws}

        # Kõigepealt märgi teadaolevad ID-d.
        for law in laws:
            law_id = law["id"]

            pattern = re.compile(
                rf"(?<!\[)\b{re.escape(law_id)}\b(?!\])",
                re.IGNORECASE,
            )

            text = pattern.sub(f"[{law_id}]", text)

        # Seejärel märgi kõik ID-laadsed tokenid, ka tundmatud.
        # See on fail-closed põhimõtte jaoks hea.
        general_pattern = re.compile(
            r"(?<!\[)\b([A-ZÕÄÖÜa-zõäöü]{2,20}_\d+(?:[Bb]\d+|[A-Za-z])?)\b(?!\])",
            re.UNICODE,
        )

        def replace_unknown(match: re.Match) -> str:
            token = match.group(1)
            citation_id = self._normalize_id(token)
            return f"[{citation_id}]"

        text = general_pattern.sub(replace_unknown, text)

        return text

    def _has_valid_citations(self, text: str, laws: List[Dict]) -> bool:
        """Use the same fail-closed verifier here and at the API boundary."""
        valid, _ = self.source_verifier.verify_sources(text, laws)
        return valid

    # ------------------------------------------------------------------
    # Mock
    # ------------------------------------------------------------------

    def _mock_analysis(self, case_desc: str, laws: List[Dict]) -> str:
        """
        Testvastus juhuks, kui Ollama pole saadaval.

        NB! See on mock, mitte päris AI analüüs.
        """
        citations = [law["id"] for law in laws]
        citations_line = " ".join(f"[{citation_id}]" for citation_id in citations)

        parts = [
            "TESTREŽIIM: Ollama pole ühendatud. See on näidisvastus, mitte päris AI analüüs.",
            "",
            "OLUKORD:",
            "Antud kirjelduse põhjal vajab olukord täpsemat õiguslikku hinnangut.",
            "",
            "ÕIGUSLIK KOHALDAMINE:",
            (
                "Süsteem leidis võimalikud allikad, kuid Ollama mudel ei teinud reaalset analüüsi; "
                "kontrolli seaduste tekste ja asjakohasust käsitsi "
                + (f"[{laws[0]['id']}]." if laws else ".")
            ),
            "",
            "SOOVITUSED:",
            "1. Säilita kõik dokumendid ja kirjavahetus.",
            "2. Kontrolli tähtaegu.",
            "3. Konsulteeri juristiga, kui vaidlus võib jõuda kohtusse.",
        ]

        if citations_line:
            parts.append("")
            parts.append(f"KASUTATUD ALLIKAD: {citations_line}")

        return "\n".join(parts).strip()

    # ------------------------------------------------------------------
    # Väikesed abifunktsioonid
    # ------------------------------------------------------------------

    def _normalize_id(self, raw_id: str) -> str:
        """
        Normaliseerib ID.

        Näiteks:
        " vos_123 " -> "VOS_123"
        "[VOS_123]" -> "VOS_123"
        """
        raw_id = str(raw_id or "").strip().upper()
        raw_id = re.sub(r"\s+", "", raw_id)
        raw_id = re.sub(r"[^A-ZÕÄÖÜ0-9_]", "", raw_id)

        return raw_id
