"""ÕigusAI citation verifier.

Fail-closed kontroll:
- vähemalt üks viide peab olemas olema;
- kõik [ID] viited peavad kuuluma mudelile antud allikate hulka;
- ID-de võrdlus on case-insensitive ja normaliseeritud;
- õigusliku kohaldamise iga sisuline lõik peab sisaldama vähemalt üht [ID] viidet;
- tekstilised seaduse/paragrahvi viited ilma [ID]-ta lükatakse tagasi.

NB! See kontroll kinnitab viidete terviklikkust, mitte semantilist tõestust, et
iga järeldus tuleneb materiaalõiguslikult viidatud normist. Seetõttu nimetab API
staatust CITATIONS_VERIFIED, mitte lihtsalt VERIFIED.
"""
from typing import Dict, List, Tuple
import json
import re


class SourceVerifier:
    CITATION_PATTERN = re.compile(r"\[\s*([A-ZÕÄÖÜa-zõäöü]+_\d+(?:[Bb]\d+|[A-Za-z])?)\s*\]", re.UNICODE)
    UNBRACKETED_LAW_REFERENCE = re.compile(
        r"\b(VÕS|VOS|TLS|KarS|KrMS|TsMS|seadus|seaduse|paragrahv|paragrahvi)\b|§",
        re.IGNORECASE,
    )
    APPLICATION_SECTION = re.compile(
        r"ÕIGUSLIK\s+KOHALDAMINE\s*:\s*(.*?)(?=\n\s*(?:SOOVITUSED|KASUTATUD\s+ALLIKAD)\s*:|\Z)",
        re.IGNORECASE | re.DOTALL,
    )

    @staticmethod
    def _normalize_id(value: str) -> str:
        value = str(value or "").strip().upper()
        value = re.sub(r"\s+", "", value)
        return re.sub(r"[^A-ZÕÄÖÜ0-9_]", "", value)

    def extract_cited_ids(self, ai_response: str) -> List[str]:
        seen: List[str] = []
        payload = self._parse_json_payload(ai_response)
        if isinstance(payload, dict):
            citations = payload.get("citations") or payload.get("sources") or payload.get("sources_used")
            if isinstance(citations, list):
                for item in citations:
                    if isinstance(item, str):
                        normalized = self._normalize_id(item)
                        if normalized and normalized not in seen:
                            seen.append(normalized)

        for match in self.CITATION_PATTERN.findall(ai_response):
            normalized = self._normalize_id(match)
            if normalized and normalized not in seen:
                seen.append(normalized)
        return seen

    def _parse_json_payload(self, ai_response: str):
        try:
            return json.loads(ai_response)
        except (json.JSONDecodeError, TypeError):
            decoder = json.JSONDecoder()
            for index, char in enumerate(ai_response or ""):
                if char in "[{":
                    try:
                        payload, _ = decoder.raw_decode(ai_response[index:])
                        return payload
                    except json.JSONDecodeError:
                        continue
        return None

    @staticmethod
    def _split_claims(text: str) -> List[str]:
        """Split legal application text into sentence-like claims.

        This is deliberately conservative: one citation at the end of a multi-sentence
        paragraph does not validate earlier sentences.
        """
        claims: List[str] = []
        for line in (text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÕÄÖÜ0-9])", line)
            claims.extend(part.strip() for part in parts if part.strip())
        return claims

    def _contains_unbracketed_law_reference(
        self, ai_response: str, available_laws: List[Dict]
    ) -> bool:
        """Detect textual law references that are not backed by a [ID] in the same claim."""
        dynamic_terms = set()
        for law in available_laws:
            law_id = self._normalize_id(law.get("id"))
            if "_" in law_id:
                dynamic_terms.add(law_id.split("_", 1)[0])
            law_name = str(law.get("law_name") or "").strip()
            if law_name:
                dynamic_terms.add(law_name)

        for claim in self._split_claims(ai_response or ""):
            has_reference = bool(self.UNBRACKETED_LAW_REFERENCE.search(claim))
            if not has_reference:
                upper_claim = claim.upper()
                for term in dynamic_terms:
                    if len(term) <= 12 and re.search(rf"(?<![A-ZÕÄÖÜ]){re.escape(term.upper())}(?![A-ZÕÄÖÜ])", upper_claim):
                        has_reference = True
                        break
                    if len(term) > 12 and term.casefold() in claim.casefold():
                        has_reference = True
                        break
            if has_reference and not self.CITATION_PATTERN.search(claim):
                return True
        return False

    def _application_claims_have_citations(self, ai_response: str) -> bool:
        """Require every substantive sentence/claim in legal application to carry a citation."""
        match = self.APPLICATION_SECTION.search(ai_response or "")
        if not match:
            return False

        section = match.group(1).strip()
        if not section:
            return False

        claims = self._split_claims(section)
        substantive = [
            claim
            for claim in claims
            if len(re.findall(r"[A-Za-zÕÄÖÜõäöü]{2,}", claim)) >= 3
        ]
        if not substantive:
            return False

        return all(self.CITATION_PATTERN.search(claim) for claim in substantive)

    def verify_sources(
        self, ai_response: str, available_laws: List[Dict]
    ) -> Tuple[bool, List[str]]:
        available_map = {
            self._normalize_id(law.get("id")): law.get("id")
            for law in available_laws
            if law.get("id")
        }
        cited_ids = self.extract_cited_ids(ai_response)

        if not cited_ids:
            return False, []

        invalid_ids = [cid for cid in cited_ids if cid not in available_map]
        if invalid_ids:
            return False, []

        if self._contains_unbracketed_law_reference(ai_response, available_laws):
            return False, []

        if not self._application_claims_have_citations(ai_response):
            return False, []

        # Tagasta kanoniseeritud ID-d samas järjekorras, milles mudel neid kasutas.
        return True, [self._normalize_id(cid) for cid in cited_ids]
