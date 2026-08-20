"""Deterministic semantic guardrails for retrieval and final answers.

Citation verification proves that an ID exists and that the model was given the
source.  It does not prove that the source answers the user's actual question.
This module adds a deliberately small, high-precision concept gate for actions
where an adjacent but irrelevant section would be especially misleading.

The rules are extensible: add a concept only when both its user-language forms
and its source-language anchors can be audited against the local corpus.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence


@dataclass(frozen=True)
class RelevanceResult:
    relevant: bool
    concepts: tuple[str, ...] = ()
    missing_concepts: tuple[str, ...] = ()
    clarification: str = ""


class RelevanceVerifier:
    """Check that retrieved/cited material covers the user's concrete action."""

    _CONCEPTS: Dict[str, Dict[str, object]] = {
        "fine": {
            # Personal-event forms are intentionally narrower than the noun
            # "trahv": a generic question may legitimately need broader law.
            "query": (
                r"\btrahvis\w*\b",
                r"\btrahviti\w*\b",
                r"\bsain\s+(?:ma\s+)?trahvi\b",
                r"\bsain\s+(?:ma\s+)?trahvitea\w*\b",
                r"\bsain\s+(?:ma\s+)?trahviotsus\w*\b",
                r"\btrahvitea\w*\b",
                r"\btrahviotsus\w*\b",
                r"\brahatrahv\w*\b",
                r"\bmaaras\w*\s+(?:mulle\s+)?trahvi\b",
                r"\btegi\w*\s+(?:mulle\s+)?trahvi\b",
                r"\bsain\b.{0,100}\btrahvi\b",
                r"\b(?:maaras|tegi|kirjutas)\w*\b.{0,120}\btrahvi\b",
                r"\babipolitsei\w*\b.{0,160}\btrahv\w*\b",
            ),
            "anchors": (
                r"\btrahv\w*\b",
                r"\brahatrahv\w*\b",
                r"\bvaarte\w*\b",
                r"\bkiirmenetl\w*\b",
            ),
            "clarification": (
                "Leitud sätted ei käsitle piisavalt täpselt trahvi ega selle "
                "vaidlustamist. Kirjelda palun, kas said trahviotsuse või "
                "trahviteate, mis rikkumine sinna märgiti ja millal said selle kätte."
            ),
        },
        "fine_challenge": {
            "query": (
                r"\bvaidlust\w*\b",
                r"\bkaeb\w*\b",
                r"\bei\s+(?:ole\s+)?noustu\b",
                r"\bei\s+ole\s+nous\b",
            ),
            "anchors": (
                r"\bvaidlust\w*\b",
                r"\bkaeb\w*\b",
                r"\bmaakoht\w*\b",
            ),
            "clarification": (
                "Leitud sätted või vastus ei käsitle veel trahvi vaidlustamise "
                "võimalust. Vaidlustamise korra valimiseks on vaja dokumendi "
                "täpset liiki, menetlejat ja kättesaamise aega."
            ),
        },
        "missed_deadline": {
            "query": (
                r"\btahta\w*\b.{0,60}\b(?:moodas|uletatud|hiljaks|ennist\w*)\b",
                r"\b(?:moodas|uletatud|hiljaks|ennist\w*)\b.{0,60}\btahta\w*\b",
            ),
            "anchors": (
                r"\bennist\w*\b",
                r"\btahtaja\s+moodum\w*\b",
                r"\bparast\s+.*tahta\w*\b",
                r"\blabi\s+vaatamata\b",
            ),
            "clarification": (
                "Leitud sätted või vastus ei käsitle veel tähtaja möödumise "
                "tagajärge ega tähtaja ennistamise võimalust. Täpseks vastuseks "
                "on vaja dokumendi liiki ja kättesaamise aega."
            ),
        },
        "payment_plan": {
            "query": (
                r"\bjarelmaks\w*\b",
                r"\bosamak\w*\b",
                r"\bmaksegraaf\w*\b",
                r"\bosade\s+kaupa\b",
                r"\bositi\b",
            ),
            "anchors": (
                r"\bositi\b",
                r"\bosamak\w*\b",
                r"\bosastatud\b",
                r"\bmaksegraaf\w*\b",
            ),
            "clarification": (
                "Leitud sätted või vastus ei käsitle veel rahatrahvi ositi "
                "tasumise võimalust. Täpsusta palun otsuse liik ja kas nõue on "
                "juba täitmisele pööratud."
            ),
        },
        "auxiliary_police": {
            "query": (
                r"\babipolitsei\w*\b",
                r"\babi\s+politsei\w*\b",
            ),
            "anchors": (
                r"\babipolitsei\w*\b",
            ),
            "clarification": (
                "Vastus ei käsitlenud abipolitseiniku rolli ega pädevust. "
                "Täpsusta palun, kas dokumendil on otsuse tegijana märgitud "
                "abipolitseinik või Politsei- ja Piirivalveameti ametnik."
            ),
        },
    }

    def verify_laws(self, query: str, laws: Sequence[Dict]) -> RelevanceResult:
        concepts = self.detect_concepts(query)
        if not concepts:
            return RelevanceResult(relevant=True)

        source_text = " ".join(
            " ".join(
                str(law.get(field, ""))
                for field in ("title", "text", "law_name", "domain")
            )
            for law in laws
        )
        return self._verify_text(concepts, source_text)

    def verify_answer(
        self,
        query: str,
        answer: str,
        cited_laws: Sequence[Dict],
    ) -> RelevanceResult:
        concepts = self.detect_concepts(query)
        if not concepts:
            return RelevanceResult(relevant=True)

        cited_result = self.verify_laws(query, cited_laws)
        if not cited_result.relevant:
            return cited_result

        answer_result = self._verify_text(concepts, answer)
        if not answer_result.relevant:
            return answer_result

        if not self._preserves_formal_notice_scope(query, answer, cited_laws):
            return RelevanceResult(
                relevant=False,
                concepts=tuple(concepts) + ("formal_notice_scope",),
                missing_concepts=("formal_notice_scope",),
                clarification=(
                    "Sõna „trahviteade” ei näita veel kindlalt, millise dokumendiga "
                    "on tegu. Kontrolli dokumendi täpset pealkirja, menetlejat ja "
                    "rikkumise kvalifikatsiooni."
                ),
            )

        return answer_result

    def _preserves_formal_notice_scope(
        self,
        query: str,
        answer: str,
        cited_laws: Sequence[Dict],
    ) -> bool:
        """Do not apply the motor-vehicle warning route as a generic fine route."""
        cited_ids = {
            str(law.get("id", "")).strip().upper()
            for law in cited_laws
            if law.get("id")
        }
        if not cited_ids.intersection({"VTMS_54B2", "VTMS_54B5"}):
            return True

        normalized_query = self._normalize(query)
        if any(term in normalized_query for term in (
            "mootorsoiduk", "hoiatustrahv", "kirjalik hoiatamismenetlus"
        )):
            return True

        normalized_answer = self._normalize(answer)
        return any(marker in normalized_answer for marker in (
            "kui saadud dokument",
            "kui dokument on",
            "kui tegemist on",
            "juhul kui saadud dokument",
            "eeldusel et saadud dokument",
            "ei saa veel kinnitada",
        ))

    def detect_concepts(self, query: str) -> List[str]:
        normalized = self._normalize(query)
        concepts = [
            name
            for name, rule in self._CONCEPTS.items()
            if name != "fine_challenge"
            if self._matches_any(normalized, rule["query"])
        ]
        if (
            "fine" in concepts
            and self._matches_any(
                normalized, self._CONCEPTS["fine_challenge"]["query"]
            )
        ):
            concepts.append("fine_challenge")
        return concepts

    def clarification_for(self, result: RelevanceResult) -> str:
        for concept in result.missing_concepts or result.concepts:
            rule = self._CONCEPTS.get(concept)
            if rule:
                return str(rule["clarification"])
        return (
            "Leitud sätted ei vasta veel piisavalt täpselt kirjeldatud tegevusele. "
            "Lisa palun, kes tegi mida ning milline otsus või dokument sul on."
        )

    def _verify_text(
        self,
        concepts: Iterable[str],
        text: str,
    ) -> RelevanceResult:
        normalized = self._normalize(text)
        concept_list = tuple(concepts)
        missing = tuple(
            concept
            for concept in concept_list
            if not self._matches_any(
                normalized, self._CONCEPTS[concept]["anchors"]
            )
        )
        result = RelevanceResult(
            relevant=not missing,
            concepts=concept_list,
            missing_concepts=missing,
        )
        if missing:
            return RelevanceResult(
                relevant=False,
                concepts=concept_list,
                missing_concepts=missing,
                clarification=self.clarification_for(result),
            )
        return result

    @staticmethod
    def _matches_any(text: str, patterns: Iterable[str]) -> bool:
        return any(re.search(pattern, text) for pattern in patterns)

    @staticmethod
    def _normalize(value: str) -> str:
        text = str(value or "").casefold().translate(
            str.maketrans({"ä": "a", "ö": "o", "ü": "u", "õ": "o", "š": "s", "ž": "z"})
        )
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z0-9]+", " ", text).strip()
