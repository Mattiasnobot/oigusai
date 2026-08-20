"""ÕigusAI V5 - deterministic query understanding for Estonian legal search.

This module deliberately does *not* produce legal facts. It only expands retrieval
terms using the trusted local corpus vocabulary. Fuzzy matches, compounds and
lightweight morphology may help find an authority, but only retrieved Riigi
Teataja records may later support a legal claim.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
import re
import unicodedata
from collections import defaultdict
from typing import Dict, Iterable, List, Mapping, Sequence, Set


# Common words should never be "corrected" into legal terminology just because a
# string happens to be similar. The list is intentionally small and conservative.
ESTONIAN_QUERY_STOPWORDS = {
    "aga", "ei", "ega", "et", "ja", "jah", "kas", "kes", "kui", "kuidas",
    "kaua", "kellel", "ma", "me", "mida", "miks", "millal", "milline",
    "millised", "millisel", "millistel", "millist", "millistest", "mis", "minu",
    "mind", "mul", "mulle",
    "nad", "ning", "on", "olen", "oli", "oma", "sa", "see", "seda", "selle",
    "siis", "ta", "tema", "te", "või", "voib", "võib", "võivad", "peab", "pean",
    "saab", "saan", "tahan", "soovin", "teha", "tegi", "tehtud", "tuleb",
    "olema", "olla", "palun", "kohta", "kasitatakse", "kasitletakse",
    "kehtib", "kehtivad", "loetakse", "reegel", "reeglid", "sisaldab",
    "sisaldama", "tahendab",
}

# Lightweight retrieval-only suffix variants. This is not presented as a full
# Estonian morphological analyzer; it merely provides additional candidates for
# corpus matching. Conservative length guards prevent aggressive stemming.
ESTONIAN_SUFFIXES = (
    "dega", "tele", "delt", "dest", "desse", "telt", "test", "tesse",
    "ga", "le", "lt", "st", "sse", "ks", "ni", "na", "ta",
    "de", "te", "id", "sid", "d", "t", "l", "s",
)


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = str(text).lower().translate(
        str.maketrans({"ä": "a", "ö": "o", "ü": "u", "õ": "o", "š": "s", "ž": "z"})
    )
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def tokenize(text: str) -> List[str]:
    return [token for token in re.findall(r"[a-z0-9]+", normalize_text(text)) if len(token) >= 2]


def char_ngrams(value: str, n: int = 3) -> Set[str]:
    value = value.strip()
    if not value:
        return set()
    if len(value) <= n:
        return {value}
    return {value[index:index + n] for index in range(len(value) - n + 1)}


@dataclass(frozen=True)
class QueryMatch:
    original: str
    candidate: str
    score: float
    domains: List[str] = field(default_factory=list)
    reason: str = "fuzzy"

    def to_dict(self) -> Dict:
        data = asdict(self)
        data["score"] = round(self.score, 3)
        return data


@dataclass(frozen=True)
class QueryUnderstandingResult:
    original_query: str
    normalized_query: str
    original_tokens: List[str]
    expanded_tokens: List[str]
    domain_hints: List[str]
    section_hints: List[str]
    matches: List[QueryMatch]
    notes: List[str]

    def to_dict(self) -> Dict:
        return {
            "original_query": self.original_query,
            "normalized_query": self.normalized_query,
            "original_tokens": list(self.original_tokens),
            "expanded_tokens": list(self.expanded_tokens),
            "domain_hints": list(self.domain_hints),
            "section_hints": list(self.section_hints),
            "matches": [item.to_dict() for item in self.matches],
            "notes": list(self.notes),
        }


class QueryUnderstandingService:
    """Corpus-backed typo tolerance and retrieval query expansion.

    The vocabulary is built only from law metadata/aliases plus explicitly supplied
    legal terminology. Section body text is intentionally excluded so arbitrary
    common words do not become fuzzy-correction targets.
    """

    def __init__(
        self,
        laws: Sequence[Mapping],
        *,
        enabled: bool = True,
        fuzzy_threshold: float = 0.82,
        fuzzy_max_matches: int = 1,
        fuzzy_min_token_length: int = 5,
        max_expanded_terms: int = 16,
        compound_enabled: bool = True,
        extra_terms: Mapping[str, Iterable[str]] | None = None,
        lexicon_entries: Sequence[Mapping] | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.fuzzy_threshold = float(fuzzy_threshold)
        self.fuzzy_max_matches = int(fuzzy_max_matches)
        self.fuzzy_min_token_length = int(fuzzy_min_token_length)
        self.max_expanded_terms = int(max_expanded_terms)
        self.compound_enabled = bool(compound_enabled)

        self._term_domains: Dict[str, Set[str]] = defaultdict(set)
        self._ngram_index: Dict[str, Set[str]] = defaultdict(set)
        self._lexicon_rules: List[
            tuple[List[str], List[str], List[str], List[str]]
        ] = []
        self._available_section_ids = {
            str(law.get("id", "")).strip().upper()
            for law in laws
            if law.get("id")
        }

        for law in laws:
            domain = str(law.get("domain") or str(law.get("id", "")).split("_", 1)[0]).upper()
            fields = [law.get("law_name", ""), law.get("title", "")]
            fields.extend(law.get("aliases") or [])
            for value in fields:
                self._register_phrase(value, domain)

        if extra_terms:
            for domain, terms in extra_terms.items():
                for term in terms:
                    self._register_phrase(term, str(domain).upper())

        if lexicon_entries:
            for entry in lexicon_entries:
                forms = [normalize_text(value) for value in (entry.get("forms") or [])]
                expansions = [normalize_text(value) for value in (entry.get("expand") or [])]
                domains = [str(value).upper() for value in (entry.get("domains") or [])]
                sections = [str(value).upper() for value in (entry.get("sections") or [])]
                forms = [value for value in forms if value]
                expansions = [value for value in expansions if value]
                domains = [value for value in domains if value]
                sections = [value for value in sections if value]
                if not forms or not expansions:
                    continue
                self._lexicon_rules.append((forms, expansions, domains, sections))
                for domain in domains:
                    for expansion in expansions:
                        self._register_phrase(expansion, domain)

        for term in self._term_domains:
            for gram in char_ngrams(term):
                self._ngram_index[gram].add(term)

    @property
    def vocabulary_size(self) -> int:
        return len(self._term_domains)

    def _register_phrase(self, value: object, domain: str) -> None:
        normalized = normalize_text(str(value or ""))
        if not normalized:
            return
        tokens = [
            token for token in normalized.split()
            if len(token) >= 3 and not token.isdigit()
        ]
        for token in tokens:
            if token not in ESTONIAN_QUERY_STOPWORDS:
                self._term_domains[token].add(domain)

        # Legal compounds are frequently typed with a space by users. Register the
        # joined form for short phrases so "abi politseinik" can resolve to a corpus
        # term such as "abipolitseinik" without changing the user's original text.
        if 2 <= len(tokens) <= 3:
            joined = "".join(tokens)
            if len(joined) >= self.fuzzy_min_token_length:
                self._term_domains[joined].add(domain)

    def analyze(self, query: str) -> QueryUnderstandingResult:
        original_query = str(query or "")
        normalized_query = normalize_text(original_query)
        original_tokens = tokenize(original_query)

        if not self.enabled or not original_tokens:
            return QueryUnderstandingResult(
                original_query=original_query,
                normalized_query=normalized_query,
                original_tokens=original_tokens,
                expanded_tokens=[],
                domain_hints=[],
                section_hints=[],
                matches=[],
                notes=[],
            )

        expanded: List[str] = []
        matches: List[QueryMatch] = []
        hinted_domains: Set[str] = set()
        hinted_sections: Set[str] = set()
        lexicon_notes: List[str] = []

        # Curated Estonian surface forms are deterministic and auditable. They help
        # map ordinary user language to retrieval vocabulary without asking the LLM
        # to rewrite the user's question.
        padded_query = f" {normalized_query} "
        token_set = set(original_tokens)
        for forms, expansions, domains, sections in self._lexicon_rules:
            matched_form = None
            for form in forms:
                if " " in form:
                    if f" {form} " in padded_query:
                        matched_form = form
                        break
                elif form in token_set:
                    matched_form = form
                    break
            if not matched_form:
                continue

            for expansion in expansions:
                self._append_unique(expanded, expansion)
                matches.append(QueryMatch(
                    original=matched_form,
                    candidate=expansion,
                    score=1.0,
                    domains=sorted(domains),
                    reason="lexicon",
                ))
            hinted_domains.update(domains)
            hinted_sections.update(sections)
            lexicon_notes.append(
                f'Otsing laiendas väljendit „{matched_form}” seotud terminitega: '
                + ", ".join(f'„{value}”' for value in expansions)
                + "."
            )

        # Ordinary language may put the alleged reason between the actor and
        # "trahv" ("abipolitsei tegi mulle niisama seismise eest trahvi").
        # Static phrase forms cannot cover that safely. Requiring both concepts
        # in one short span keeps the rule precise and prevents actor-only
        # section hints from hiding the procedure and challenge sources.
        if self._is_flexible_auxiliary_police_fine(normalized_query):
            situational_sections = [
                section_id
                for section_id in (
                    "ABIPOLS_3", "ABIPOLS_16", "VTMS_19", "VTMS_57", "VTMS_114"
                )
                if section_id in self._available_section_ids
            ]
            situational_expansions = [
                "abipolitseiniku pädevus",
                "menetlusaluse isiku õigused",
                "kiirmenetluse otsus",
                "kaebuse tähtaeg",
            ]
            if not set(situational_sections).issubset(hinted_sections):
                for expansion in situational_expansions:
                    if expansion in expanded:
                        continue
                    self._append_unique(expanded, expansion)
                    matches.append(QueryMatch(
                        original="abipolitsei … trahv",
                        candidate=expansion,
                        score=1.0,
                        domains=["ABIPOLS", "VTMS"],
                        reason="lexicon",
                    ))
                hinted_domains.update({"ABIPOLS", "VTMS"})
                hinted_sections.update(situational_sections)
                lexicon_notes.append(
                    "Otsing sidus abipolitseiniku kirjeldatud trahvijuhtumi "
                    "pädevuse ja väärteomenetluse allikatega."
                )

        # V6.3 treats the latest requested outcomes as independent retrieval
        # obligations. A single message may need both a missed-deadline route
        # and a payment-plan route, so these hints are additive.
        if self._is_fine_missed_deadline(normalized_query):
            deadline_sections = [
                section_id
                for section_id in ("VTMS_114", "VTMS_118")
                if section_id in self._available_section_ids
            ]
            for expansion in (
                "kaebuse tähtaeg",
                "tähtaja ennistamise taotlus",
                "kaebuse läbi vaatamata jätmine",
            ):
                self._append_unique(expanded, expansion)
                matches.append(QueryMatch(
                    original="kaebetähtaeg möödas",
                    candidate=expansion,
                    score=1.0,
                    domains=["VTMS"],
                    reason="lexicon",
                ))
            hinted_domains.add("VTMS")
            hinted_sections.update(deadline_sections)
            lexicon_notes.append(
                "Otsing käsitles möödunud kaebetähtaega eraldi vastamist vajava küsimusena."
            )

        if self._is_fine_payment_plan(normalized_query):
            payment_sections = [
                section_id
                for section_id in ("KARS_66", "VTMS_57", "VTMS_74", "VTMS_204")
                if section_id in self._available_section_ids
            ]
            for expansion in (
                "rahatrahvi tasumine ositi",
                "osastatud rahatrahvi osamaksed",
                "rahatrahvi täitmisele pööramine",
            ):
                self._append_unique(expanded, expansion)
                matches.append(QueryMatch(
                    original="järelmaks või osade kaupa",
                    candidate=expansion,
                    score=1.0,
                    domains=["KARS", "VTMS"],
                    reason="lexicon",
                ))
            hinted_domains.update({"KARS", "VTMS"})
            hinted_sections.update(payment_sections)
            lexicon_notes.append(
                "Otsing käsitles rahatrahvi ositi tasumist eraldi vastamist vajava küsimusena."
            )

        # Exact known legal terms can safely provide a domain hint. Broad terms that
        # occur across many acts are intentionally ignored as hints.
        for token in original_tokens:
            domains = self._term_domains.get(token, set())
            if 0 < len(domains) <= 3:
                hinted_domains.update(domains)

        # Join adjacent user words before fuzzy matching. This specifically handles
        # natural forms such as "abi politseinik" while remaining corpus-backed.
        if self.compound_enabled:
            for width in (2, 3):
                if len(original_tokens) < width:
                    continue
                for index in range(len(original_tokens) - width + 1):
                    parts = original_tokens[index:index + width]
                    if any(part in ESTONIAN_QUERY_STOPWORDS for part in parts):
                        continue
                    joined = "".join(parts)
                    domains = self._term_domains.get(joined)
                    if domains:
                        self._append_unique(expanded, joined)
                        if len(domains) <= 3:
                            hinted_domains.update(domains)
                        matches.append(QueryMatch(
                            original=" ".join(parts),
                            candidate=joined,
                            score=1.0,
                            domains=sorted(domains),
                            reason="compound",
                        ))

        for token in original_tokens:
            if len(expanded) >= self.max_expanded_terms:
                break
            if token in ESTONIAN_QUERY_STOPWORDS:
                continue

            # Retrieval-only suffix variants are useful even when the token itself
            # already exists in vocabulary (e.g. inflected legal terminology).
            found_suffix_variant = False
            for variant in self._suffix_variants(token):
                if variant in self._term_domains and variant != token:
                    self._append_unique(expanded, variant)
                    found_suffix_variant = True

            # If a conservative suffix removal already produced a corpus-backed
            # form, do not label the same change as a possible typo. This keeps
            # inflection handling transparent and avoids accidental fuzzy domain
            # hints from ordinary verb/adjective forms such as "avaldas".
            if (
                found_suffix_variant
                or token in self._term_domains
                or len(token) < self.fuzzy_min_token_length
            ):
                continue

            candidates = self._candidate_terms(token)
            accepted = 0
            for candidate, score in candidates:
                if score < self.fuzzy_threshold:
                    break
                domains = self._term_domains.get(candidate, set())
                self._append_unique(expanded, candidate)
                if len(domains) <= 3:
                    hinted_domains.update(domains)
                matches.append(QueryMatch(
                    original=token,
                    candidate=candidate,
                    score=score,
                    domains=sorted(domains),
                    reason="fuzzy",
                ))
                accepted += 1
                if accepted >= self.fuzzy_max_matches or len(expanded) >= self.max_expanded_terms:
                    break

        # Only expose meaningful user-facing notes; exact domain hints remain an
        # internal ranking signal and do not need to clutter the UI.
        notes = list(lexicon_notes)
        for match in matches:
            if match.reason == "fuzzy":
                notes.append(
                    f'Otsing laiendas võimalikku kirjaviga „{match.original}” → „{match.candidate}”.'
                )
            elif match.reason == "compound":
                notes.append(
                    f'Otsing käsitles väljendit „{match.original}” ka liitsõnana „{match.candidate}”.'
                )
            elif match.reason == "lexicon":
                # Grouped lexicon note was already added above.
                continue

        return QueryUnderstandingResult(
            original_query=original_query,
            normalized_query=normalized_query,
            original_tokens=original_tokens,
            expanded_tokens=expanded[:self.max_expanded_terms],
            domain_hints=sorted(hinted_domains),
            section_hints=sorted(hinted_sections),
            matches=matches,
            notes=self._deduplicate(notes),
        )

    @staticmethod
    def _is_flexible_auxiliary_police_fine(normalized_query: str) -> bool:
        text = str(normalized_query or "")
        return bool(
            re.search(r"\babipolitsei\w*\b.{0,160}\btrahv\w*\b", text)
            or re.search(r"\btrahv\w*\b.{0,160}\babipolitsei\w*\b", text)
        )

    @staticmethod
    def _is_fine_missed_deadline(normalized_query: str) -> bool:
        text = str(normalized_query or "")
        fine = bool(re.search(r"\b(?:trahv\w*|rahatrahv\w*|vaarte\w*)\b", text))
        missed = bool(
            re.search(r"\btahta\w*\b.{0,60}\b(?:moodas|uletatud|hiljaks|ennist\w*)\b", text)
            or re.search(r"\bennist\w*\b.{0,60}\btahta\w*\b", text)
        )
        return fine and missed

    @staticmethod
    def _is_fine_payment_plan(normalized_query: str) -> bool:
        text = str(normalized_query or "")
        fine = bool(re.search(r"\b(?:trahv\w*|rahatrahv\w*|vaarte\w*)\b", text))
        payment = bool(re.search(
            r"\b(?:jarelmaks\w*|osamak\w*|maksegraaf\w*|ositi)\b|"
            r"\bosade\s+kaupa\b",
            text,
        ))
        return fine and payment

    def _suffix_variants(self, token: str) -> List[str]:
        minimum_base_length = max(4, self.fuzzy_min_token_length - 1)
        if len(token) <= minimum_base_length:
            return []
        variants: List[str] = []
        for suffix in ESTONIAN_SUFFIXES:
            if token.endswith(suffix) and len(token) - len(suffix) >= minimum_base_length:
                variants.append(token[:-len(suffix)])
        return variants

    def _candidate_terms(self, token: str) -> List[tuple[str, float]]:
        grams = char_ngrams(token)
        candidate_hits: Dict[str, int] = defaultdict(int)
        for gram in grams:
            for candidate in self._ngram_index.get(gram, ()):
                candidate_hits[candidate] += 1

        # Character n-grams cheaply narrow a potentially large legal vocabulary.
        # The final score still uses full-string similarity to avoid accepting a
        # candidate based only on a shared substring.
        rough = sorted(candidate_hits, key=lambda value: candidate_hits[value], reverse=True)[:120]
        scored: List[tuple[str, float]] = []
        for candidate in rough:
            if candidate == token:
                continue
            if abs(len(candidate) - len(token)) > max(4, int(len(token) * 0.4)):
                continue
            candidate_grams = char_ngrams(candidate)
            union = grams | candidate_grams
            jaccard = len(grams & candidate_grams) / len(union) if union else 0.0
            sequence = SequenceMatcher(None, token, candidate).ratio()
            score = (sequence * 0.90) + (jaccard * 0.10)
            scored.append((candidate, score))

        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored

    @staticmethod
    def _append_unique(items: List[str], value: str) -> None:
        if value and value not in items:
            items.append(value)

    @staticmethod
    def _deduplicate(items: Iterable[str]) -> List[str]:
        seen: Set[str] = set()
        result: List[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result
