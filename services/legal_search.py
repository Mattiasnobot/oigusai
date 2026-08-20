"""ÕigusAI õigusaktide otsingu teenus.

Runtime eelistab lokaalselt imporditud data/laws.json korpust. Live Riigi Teataja
fallback on opt-in ning mock-seadusi production/runtime teel ei kasutata.
"""
import hashlib
import json
import logging
import math
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from config import Settings, load_settings
from services.query_understanding import (
    ESTONIAN_QUERY_STOPWORDS,
    QueryUnderstandingResult,
    QueryUnderstandingService,
)
from services.reranker import (
    LocalCrossEncoderReranker,
    RerankerUnavailableError,
)
from services.vector_search import LanceDBVectorSearch, VectorSearchUnavailableError

try:
    from services.riigiteataja import RiigiTeatajaService
except ImportError:
    try:
        from riigiteataja import RiigiTeatajaService
    except ImportError:
        RiigiTeatajaService = None

logger = logging.getLogger(__name__)


class LegalDataUnavailableError(RuntimeError):
    """Raised when no trusted legal corpus is available."""


class HistoricalDataUnavailableError(RuntimeError):
    """Raised when a historical/future date is requested without temporal corpus metadata."""


class QueryUnderstandingUnavailableError(RuntimeError):
    """Raised when the configured V5 query lexicon cannot be loaded safely."""


class LegalSearchService:
    DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y")

    def __init__(
        self,
        use_riigi_teataja: bool = False,
        data_file: Optional[Path] = None,
        settings: Settings = None,
        vector_search=None,
        reranker=None,
    ):
        cfg = settings or load_settings()
        self.min_score = cfg.legal_min_score
        self.max_results = cfg.legal_max_results
        self.relative_threshold = cfg.legal_relative_threshold
        self.rt_service = (
            RiigiTeatajaService(settings=cfg)
            if (use_riigi_teataja and RiigiTeatajaService)
            else None
        )
        self.data_file = Path(data_file) if data_file is not None else cfg.legal_data_file

        self.laws = self._load_laws()

        # Abi-sõnastikud on retrieval-signaalid, mitte õigusallikad. V5 query
        # understanding võib neid kasutada otsingu laiendamiseks, kuid ainult
        # korpusest leitud paragrahvid võivad jõuda AI konteksti.
        self.keyword_groups = {
            "VOS": ["uuri", "uurileping", "korter", "eluase", "eluruum", "rent"],
            "TLS": ["too", "tooleping", "tootaja", "tooandja", "palk", "vallandamine"],
            "ABIPOLS": ["abipolitseinik", "abipolitseiniku", "politsei", "korrakaitse"],
            "VTMS": ["trahv", "vaie", "vaarteo", "menetlus"],
            "PKS": ["perekond", "abielu", "lahutus", "laps", "hooldus"],
            "KARS": ["karistus", "kuritegu", "varas", "pettus"],
        }
        self.synonyms = {
            "uuri": {"korter", "eluase", "eluruum", "rent", "uurileping"},
            "korter": {"uuri", "eluase", "eluruum"},
            "too": {"tooleping", "tootaja", "tooandja", "palk"},
            "trahv": {"vaie", "menetlus", "karistus"},
            "abipolitseinik": {"abipolitseiniku", "politsei", "korrakaitse"},
            "abipolitseiniku": {"abipolitseinik", "politsei", "korrakaitse"},
        }

        self.domain_hint_bonus = cfg.query_domain_hint_bonus
        self.curated_domain_hint_bonus = cfg.query_curated_domain_hint_bonus
        self.hybrid_enabled = cfg.hybrid_retrieval_enabled
        self.hybrid_rrf_k = cfg.hybrid_rrf_k
        self.hybrid_lexical_weight = cfg.hybrid_lexical_weight
        self.hybrid_dense_weight = cfg.hybrid_dense_weight
        self.hybrid_diversity_weight = cfg.hybrid_diversity_weight
        self.hybrid_multi_query_enabled = cfg.hybrid_multi_query_enabled
        self.hybrid_max_query_variants = cfg.hybrid_max_query_variants
        self.reranker_enabled = cfg.reranker_enabled
        self.reranker_weight = cfg.reranker_weight
        query_terms = {
            domain: terms for domain, terms in self.keyword_groups.items()
        }
        query_lexicon = self._load_query_lexicon(cfg.query_lexicon_file)
        self._validate_query_lexicon_sections(query_lexicon)
        self._legal_intent_terms = self._build_legal_intent_terms(query_lexicon)
        self.query_understanding = QueryUnderstandingService(
            self.laws,
            enabled=cfg.query_understanding_enabled,
            fuzzy_threshold=cfg.query_fuzzy_threshold,
            fuzzy_max_matches=cfg.query_fuzzy_max_matches,
            fuzzy_min_token_length=cfg.query_fuzzy_min_token_length,
            max_expanded_terms=cfg.query_max_expanded_terms,
            compound_enabled=cfg.query_compound_enabled,
            extra_terms=query_terms,
            lexicon_entries=query_lexicon,
        )
        self._build_index()
        self.vector_search = vector_search or LanceDBVectorSearch(
            settings=cfg,
            laws=self.laws,
        )
        self.reranker = reranker or LocalCrossEncoderReranker(settings=cfg)
        logger.info(
            "Query understanding initialized with %d legal terms",
            self.query_understanding.vocabulary_size,
        )
        if self.hybrid_ready:
            logger.info(
                "V6 hybrid retrieval ready with %d vectors (%s)",
                self.vector_search.row_count,
                self.vector_search.model,
            )
        elif self.hybrid_enabled:
            logger.info(
                "V6 hybrid retrieval is not ready; V5 lexical fallback remains active: %s",
                getattr(self.vector_search, "error", None),
            )
        if self.reranker_enabled:
            logger.info(
                "V6.1 reranker configured for lazy local loading (%s)",
                getattr(self.reranker, "model_name", "unknown"),
            )

    # ------------------------------------------------------------------
    # Avalik API (muutumatu - main.py töötab edasi)
    # ------------------------------------------------------------------
    def search_laws(self, query: str, event_date: str) -> List[Dict]:
        laws, _ = self.search_laws_with_context(query, event_date)
        return laws

    def search_laws_with_context(
        self, query: str, event_date: str
    ) -> tuple[List[Dict], QueryUnderstandingResult]:
        """Search laws and return the transparent V5 query interpretation.

        Query expansion only influences retrieval. The returned laws remain the
        sole legal sources available to downstream AI and verification.
        """
        interpretation = self.query_understanding.analyze(query)
        if not query or not query.strip():
            return [], interpretation

        if event_date:
            event = self._parse_date(event_date)
            if event is None:
                raise ValueError("Sündmuse kuupäev peab olema kujul YYYY-MM-DD.")
            today = date.today()
            if event > today:
                raise HistoricalDataUnavailableError(
                    "Tulevase kuupäeva õigusseisu ei saa usaldusväärselt ette ennustada."
                )
            has_complete_temporal_metadata = all(
                law.get("valid_from") or law.get("valid_to") for law in self.laws
            )
            if event < today and not has_complete_temporal_metadata:
                raise HistoricalDataUnavailableError(
                    "Valitud kuupäeva jaoks puuduvad korpuses täielikud ajalooliste redaktsioonide "
                    "kehtivusandmed. Analüüsi ei tehta vaikimisi tänase seaduseteksti põhjal."
                )

        q_norm = self._normalize_text(query)
        original_content_tokens = set(self._content_tokens(query))
        q_tokens = set(original_content_tokens)
        for expanded_term in interpretation.expanded_tokens:
            q_tokens.update(self._content_tokens(expanded_term))
        search_tokens = q_tokens | self._expand_tokens(q_tokens)
        legacy_tokens = set(self._tokenize(query))
        legacy_tokens.update(interpretation.expanded_tokens)
        legacy_search_tokens = legacy_tokens | self._expand_tokens(legacy_tokens)
        search_phrases = {
            self._normalize_text(term)
            for term in interpretation.expanded_tokens
            if " " in self._normalize_text(term)
        }
        domain_hints = set(interpretation.domain_hints)
        curated_domain_hints = {
            domain
            for match in interpretation.matches
            if match.reason == "lexicon"
            for domain in match.domains
        }
        section_hints = set(interpretation.section_hints)

        scored = []
        legacy_scored = []
        candidate_ids = self._lexical_candidate_ids(
            search_tokens,
            legacy_search_tokens,
            q_norm,
        )
        for law_id in sorted(candidate_ids):
            law = self._laws_by_id[law_id]
            if not self._valid_for_date(law, event_date):
                continue
            score = self._score_law(
                q_norm,
                search_tokens,
                law,
                domain_hints,
                curated_domain_hints,
                search_phrases,
            )
            if score >= self.min_score:
                evidence = self._match_evidence(search_tokens, law)
                scored.append((score, law, evidence))
            legacy_score = self._legacy_score_law(
                q_norm,
                legacy_search_tokens,
                law,
                domain_hints,
                curated_domain_hints,
            )
            if legacy_score >= self.min_score:
                evidence = self._match_evidence(search_tokens, law)
                legacy_scored.append((legacy_score, law, evidence))

        if not scored and not legacy_scored:
            return [], interpretation

        scored.sort(key=lambda x: (-x[0], x[1]["id"]))
        legacy_scored.sort(key=lambda x: (-x[0], x[1]["id"]))
        relevance_source = scored if scored else legacy_scored
        if not self._query_has_legal_relevance(
            q_norm,
            original_content_tokens,
            relevance_source[0][2],
            bool(curated_domain_hints),
        ):
            return [], interpretation

        lexical_fused = self._fuse_rankings(scored, legacy_scored)
        query_variants = (
            self._dense_query_variants(query) if self.hybrid_ready else [query]
        )
        fused = lexical_fused
        if self.hybrid_ready:
            variant_rankings = self._dense_variant_rankings(
                query,
                event_date,
                search_tokens,
                variants=query_variants,
            )
            dense_ranking = self._fuse_dense_variant_rankings(variant_rankings)
            if dense_ranking:
                rankings = [lexical_fused, dense_ranking]
                weights = [self.hybrid_lexical_weight, self.hybrid_dense_weight]
                diversity_ranking = self._dense_domain_champions(
                    lexical_fused,
                    dense_ranking,
                    variant_rankings=variant_rankings,
                )
                if diversity_ranking and self.hybrid_diversity_weight > 0:
                    rankings.append(diversity_ranking)
                    weights.append(self.hybrid_diversity_weight)
                fused = self._fuse_weighted_rankings(
                    rankings,
                    weights,
                    rank_constant=self.hybrid_rrf_k,
                )
            if fused:
                fused = self._rerank_candidates(query_variants, fused)
        selected = fused[: self.max_results]

        # A deterministic curated phrase may identify more than one applicable
        # act. Reserve one slot per such domain so a top-5 list does not become
        # five near-duplicate sections from only one act.
        for domain in sorted(curated_domain_hints):
            if any(str(item[1].get("domain", "")).upper() == domain for item in selected):
                continue
            domain_candidate = next(
                (
                    item
                    for item in fused
                    if str(item[1].get("domain", "")).upper() == domain
                    and item[2]["matched_terms"]
                ),
                None,
            )
            if domain_candidate is None:
                continue
            if len(selected) < self.max_results:
                selected.append(domain_candidate)
            else:
                replace_index = next(
                    (
                        index
                        for index in range(len(selected) - 1, -1, -1)
                        if str(selected[index][1].get("domain", "")).upper()
                        not in curated_domain_hints
                    ),
                    len(selected) - 1,
                )
                selected[replace_index] = domain_candidate

        # Section hints come only from an exact, audited lexicon phrase. They
        # reserve the named corpus-backed sections but never inject free text or
        # a non-existent authority.
        forced_score = (max((item[0] for item in fused), default=0.0) + 1.0)
        for offset, section_id in enumerate(sorted(section_hints), start=1):
            hinted_candidate = next(
                (item for item in fused if item[1]["id"] == section_id),
                None,
            )
            if hinted_candidate is None:
                hinted_law = self._laws_by_id.get(section_id)
                if hinted_law is None:
                    continue
                hinted_candidate = (
                    0.0,
                    hinted_law,
                    self._match_evidence(search_tokens, hinted_law),
                )
            selected = [item for item in selected if item[1]["id"] != section_id]
            selected.append(
                (forced_score + (offset / 1000.0), hinted_candidate[1], hinted_candidate[2])
            )
            if len(selected) > self.max_results:
                removable = next(
                    (
                        index
                        for index in range(len(selected))
                        if selected[index][1]["id"] not in section_hints
                    ),
                    0,
                )
                selected.pop(removable)

        selected.sort(key=lambda item: (-item[0], item[1]["id"]))
        results = [law for _, law, _ in selected[: self.max_results]]
        return results, interpretation

    def get_law_by_id(self, law_id: str) -> Dict:
        wanted = self._normalize_id(law_id)
        for law in self.laws:
            if law["id"] == wanted:
                return law
        raise ValueError(f"Seadust {law_id} ei leitud")

    @property
    def hybrid_ready(self) -> bool:
        return bool(
            self.hybrid_enabled
            and getattr(self, "vector_search", None) is not None
            and getattr(self.vector_search, "ready", False)
        )

    def hybrid_status(self) -> Dict[str, object]:
        if getattr(self, "vector_search", None) is None:
            return {
                "enabled": self.hybrid_enabled,
                "ready": False,
                "embedding_model": None,
                "embedding_dimension": 0,
                "vector_rows": 0,
                "error": "Vektorotsingu teenus puudub.",
            }
        status_method = getattr(self.vector_search, "status", None)
        if callable(status_method):
            return status_method()
        return {
            "enabled": self.hybrid_enabled,
            "ready": self.hybrid_ready,
            "embedding_model": getattr(self.vector_search, "model", None),
            "embedding_dimension": getattr(
                self.vector_search, "embedding_dimension", 0
            ),
            "vector_rows": getattr(self.vector_search, "row_count", 0),
            "error": getattr(self.vector_search, "error", None),
        }

    def reranker_status(self) -> Dict[str, object]:
        if getattr(self, "reranker", None) is None:
            return {
                "enabled": self.reranker_enabled,
                "loaded": False,
                "ready": False,
                "model": None,
                "device": None,
                "candidates": 0,
                "error": "Rerankeri teenus puudub.",
            }
        status_method = getattr(self.reranker, "status", None)
        if callable(status_method):
            return status_method()
        return {
            "enabled": self.reranker_enabled,
            "loaded": bool(getattr(self.reranker, "loaded", False)),
            "ready": bool(getattr(self.reranker, "ready", False)),
            "model": getattr(self.reranker, "model_name", None),
            "device": getattr(self.reranker, "device", None),
            "candidates": getattr(self.reranker, "candidates", 0),
            "error": getattr(self.reranker, "error", None),
        }

    # ------------------------------------------------------------------
    # Andmete laadimine (Variant B + fallback'id)
    # ------------------------------------------------------------------
    def _load_laws(self) -> List[Dict]:
        # 1) Runtime kasutab eelkõige lokaalselt imporditud korpust.
        file_laws = self._load_laws_from_file()
        if file_laws:
            return [self._normalize_law(law) for law in file_laws]

        # 2) Live RT fallback on teadlikult opt-in, sest suur registry võib muuta
        # rakenduse stardi aeglaseks ja võrgutõrke suhtes ebastabiilseks.
        if self.rt_service:
            try:
                rt_laws = self.rt_service.load_laws()
                if rt_laws:
                    logger.warning(
                        "Kohalik korpus puudus; kasutan live Riigi Teataja fallback'i (%d paragrahvi).",
                        len(rt_laws),
                    )
                    return [self._normalize_law(law) for law in rt_laws]
            except Exception as exc:
                logger.warning("RT live fallback ebaõnnestus: %s", exc)

        raise LegalDataUnavailableError(
            "Õigusandmete korpus puudub või on tühi. Käivita: "
            "python scripts/import_riigiteataja.py. Mock-seadusi runtime'is ei kasutata."
        )

    def _load_laws_from_file(self) -> List[Dict]:
        if not self.data_file.exists():
            return []
        try:
            raw = json.loads(self.data_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LegalDataUnavailableError(
                f"Õigusandmete korpust ei saa lugeda: {exc}"
            ) from exc

        if isinstance(raw, dict):
            raw = raw.get("laws", [])
        if not isinstance(raw, list):
            raise LegalDataUnavailableError("data/laws.json peab sisaldama JSON massiivi.")
        if not raw:
            return []

        required = {
            "id", "title", "text", "source", "domain", "law_name",
            "section", "aliases", "url", "content_hash",
        }
        validated: List[Dict] = []
        for index, law in enumerate(raw):
            if not isinstance(law, dict):
                raise LegalDataUnavailableError(
                    f"Korpuse kirje #{index + 1} ei ole JSON objekt."
                )
            missing = required - set(law)
            if missing:
                raise LegalDataUnavailableError(
                    f"Korpuse kirjel {law.get('id', index + 1)} puuduvad importeri väljad: "
                    + ", ".join(sorted(missing))
                )
            if not str(law.get("url", "")).startswith("https://www.riigiteataja.ee/"):
                raise LegalDataUnavailableError(
                    f"Korpuse kirjel {law.get('id')} puudub usaldatud Riigi Teataja URL."
                )
            text = str(law.get("text", ""))
            expected_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if law.get("content_hash") != expected_hash:
                raise LegalDataUnavailableError(
                    f"Korpuse kirje {law.get('id')} content_hash ei vasta tekstile."
                )
            validated.append(law)

        return validated

    def _normalize_law(self, law: Dict) -> Dict:
        normalized = dict(law)
        normalized["id"] = self._normalize_id(normalized.get("id", ""))
        if normalized.get("domain"):
            normalized["domain"] = self._normalize_id(normalized["domain"])
        return normalized

    def _load_query_lexicon(self, path: Path) -> List[Dict]:
        if not path.exists():
            logger.warning("V5 query lexicon puudub: %s. Jätkan korpuse sõnavaraga.", path)
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QueryUnderstandingUnavailableError(
                f"V5 query lexicon'i ei saa lugeda: {exc}"
            ) from exc
        if not isinstance(payload, list):
            raise QueryUnderstandingUnavailableError(
                "V5 query lexicon peab sisaldama JSON massiivi."
            )
        for index, entry in enumerate(payload, start=1):
            if not isinstance(entry, dict):
                raise QueryUnderstandingUnavailableError(
                    f"V5 query lexicon kirje #{index} ei ole objekt."
                )
            if not isinstance(entry.get("forms"), list) or not isinstance(entry.get("expand"), list):
                raise QueryUnderstandingUnavailableError(
                    f"V5 query lexicon kirjel #{index} peavad olema forms[] ja expand[]."
                )
            domains = entry.get("domains", [])
            if domains is not None and not isinstance(domains, list):
                raise QueryUnderstandingUnavailableError(
                    f"V5 query lexicon kirjel #{index} peab domains olema massiiv."
                )
            sections = entry.get("sections", [])
            if sections is not None and not isinstance(sections, list):
                raise QueryUnderstandingUnavailableError(
                    f"V5 query lexicon kirjel #{index} peab sections olema massiiv."
                )
        return payload

    def _validate_query_lexicon_sections(self, entries: List[Dict]) -> None:
        # Unit tests and deliberately small deployments may use a partial corpus;
        # strict cross-file integrity applies to the production-sized corpus.
        if len(self.laws) < 1000:
            return
        laws_by_id = {law["id"]: law for law in self.laws}
        for index, entry in enumerate(entries, start=1):
            domains = {
                self._normalize_id(domain) for domain in (entry.get("domains") or [])
            }
            for raw_section in entry.get("sections") or []:
                section_id = self._normalize_id(raw_section)
                law = laws_by_id.get(section_id)
                if law is None:
                    raise QueryUnderstandingUnavailableError(
                        f"V5 query lexicon kirje #{index} viitab puuduvale sättele {section_id}."
                    )
                law_domain = self._normalize_id(law.get("domain"))
                if domains and law_domain not in domains:
                    raise QueryUnderstandingUnavailableError(
                        f"V5 query lexicon kirje #{index} säte {section_id} kuulub "
                        f"valdkonda {law_domain}, mitte {sorted(domains)}."
                    )

    # ------------------------------------------------------------------
    # Otsinguindeks ja skoorimine
    # ------------------------------------------------------------------
    def _build_index(self):
        """Precompute field-aware tokens and inverse document frequencies."""
        self._token_sets = {}
        self._laws_by_id = {law["id"]: law for law in self.laws}
        self._prefix_sets = {}
        self._token_lists = {}
        self._normalized_texts = {}
        self._normalized_headings = {}
        self._heading_token_sets = {}
        token_postings = defaultdict(set)
        prefix_postings = defaultdict(set)
        alias_token_postings = defaultdict(set)
        token_document_frequency: Counter[str] = Counter()
        prefix_document_frequency: Counter[str] = Counter()
        for law in self.laws:
            law_id = law["id"]
            token_list = self._content_tokens(
                law.get("title", "") + " " + law.get("text", "")
            )
            tokens = set(token_list)
            prefixes = {self._stem_key(token) for token in tokens if len(token) >= 5}
            heading_tokens = set(self._content_tokens(self._section_heading(law)))
            self._token_lists[law_id] = token_list
            self._normalized_texts[law_id] = self._normalize_text(law.get("text", ""))
            self._normalized_headings[law_id] = self._normalize_text(
                self._section_heading(law)
            )
            self._token_sets[law_id] = tokens
            self._prefix_sets[law_id] = prefixes
            self._heading_token_sets[law_id] = heading_tokens
            token_document_frequency.update(tokens)
            prefix_document_frequency.update(prefixes)
            for token in tokens:
                token_postings[token].add(law_id)
            for prefix in prefixes:
                prefix_postings[prefix].add(law_id)
            alias_tokens = set(
                self._content_tokens(" ".join(str(alias) for alias in law.get("aliases", [])))
            )
            for token in alias_tokens:
                alias_token_postings[token].add(law_id)

        document_count = max(1, len(self.laws))
        self._average_document_length = max(
            1.0,
            sum(len(tokens) for tokens in self._token_lists.values()) / document_count,
        )
        self._token_idf = {
            token: math.log((document_count + 1) / (count + 1)) + 1.0
            for token, count in token_document_frequency.items()
        }
        self._prefix_idf = {
            prefix: math.log((document_count + 1) / (count + 1)) + 1.0
            for prefix, count in prefix_document_frequency.items()
        }
        self._token_postings = dict(token_postings)
        self._prefix_postings = dict(prefix_postings)
        self._alias_token_postings = dict(alias_token_postings)

    def _lexical_candidate_ids(
        self,
        search_tokens: Set[str],
        legacy_search_tokens: Set[str],
        normalized_query: str,
    ) -> Set[str]:
        """Return every record that can receive a non-zero lexical score."""
        candidates: Set[str] = set()
        for token in search_tokens | legacy_search_tokens:
            candidates.update(self._token_postings.get(token, ()))
            candidates.update(self._alias_token_postings.get(token, ()))
            if len(token) >= 5:
                candidates.update(self._prefix_postings.get(self._stem_key(token), ()))

        # A canonical ID match receives score even if the query contains no
        # normal content token (for example "VOS_308"). The scan is cheap and
        # preserves that proven V5 behaviour exactly.
        for law_id in self._laws_by_id:
            normalized_id = self._normalize_text(law_id)
            if normalized_id and normalized_id in normalized_query:
                candidates.add(law_id)
        return candidates

    def _score_law(
        self,
        q_norm: str,
        search_tokens: Set[str],
        law: Dict,
        domain_hints: Set[str] | None = None,
        curated_domain_hints: Set[str] | None = None,
        search_phrases: Set[str] | None = None,
    ) -> float:
        score = 0.0
        law_id = law["id"]

        # Otsene ID või pealkirja/aliasi vaste
        if law_id.lower() in q_norm:
            score += 15

        title_norm = self._normalize_text(law.get("title", ""))
        if title_norm and title_norm in q_norm:
            score += 8

        # Field-aware, IDF-weighted token overlap. Rare legal concepts and the
        # actual section heading matter more than ubiquitous boilerplate words.
        token_set = self._token_sets.get(law_id, set())
        prefix_set = self._prefix_sets.get(law_id, set())
        heading_tokens = self._heading_token_sets.get(law_id, set())
        for token in search_tokens:
            if token in token_set:
                score += 2.4 * min(5.0, self._token_idf.get(token, 1.0))
            elif len(token) >= 5 and token[:5] in prefix_set:
                score += 1.7 * min(
                    5.0, self._prefix_idf.get(self._stem_key(token), 1.0)
                )

            if token in heading_tokens:
                score += 4.5 * min(5.0, self._token_idf.get(token, 1.0))
            elif len(token) >= 5 and any(
                self._stem_key(token) == self._stem_key(value)
                for value in heading_tokens
                if len(value) >= 5
            ):
                score += 3.2 * min(
                    5.0, self._prefix_idf.get(self._stem_key(token), 1.0)
                )

        evidence = self._match_evidence(search_tokens, law)
        if evidence["matched_terms"] >= 2:
            score += min(8.0, 2.0 + evidence["proximity_bonus"])
        score += 7.0 * evidence["matched_terms"]

        normalized_heading = self._normalized_headings.get(law_id, "")
        normalized_text = self._normalized_texts.get(law_id, "")
        for phrase in search_phrases or ():
            if phrase and phrase in normalized_heading:
                score += 30.0
            elif phrase and phrase in normalized_text:
                score += 14.0

        length_ratio = len(self._token_lists.get(law_id, ())) / self._average_document_length
        length_factor = 1.1 / (0.6 + (0.4 * length_ratio))
        score *= max(0.65, min(1.25, length_factor))

        # Märksõnagrupi boonus
        prefix = self._normalize_id(law.get("domain") or law_id.split("_", 1)[0])
        for keyword in self.keyword_groups.get(prefix, []):
            if keyword in q_norm or keyword in search_tokens:
                score += 4
                break

        # Hints may rank only a section that already matched the user's search
        # terms. Even the stronger audited-lexicon signal can therefore never
        # turn an unrelated paragraph into a legal source on its own.
        if score > 0:
            if curated_domain_hints and prefix in curated_domain_hints:
                score += self.curated_domain_hint_bonus
            elif domain_hints and prefix in domain_hints:
                score += self.domain_hint_bonus

        return score

    def _legacy_score_law(
        self,
        q_norm: str,
        search_tokens: Set[str],
        law: Dict,
        domain_hints: Set[str] | None = None,
        curated_domain_hints: Set[str] | None = None,
    ) -> float:
        """Keep the proven V5 lexical rank as one signal in rank fusion."""
        score = 0.0
        law_id = law["id"]
        if law_id.lower() in q_norm:
            score += 15
        title_norm = self._normalize_text(law.get("title", ""))
        if title_norm and title_norm in q_norm:
            score += 8
        for alias in law.get("aliases", []):
            alias_norm = self._normalize_text(alias)
            if alias_norm and alias_norm in q_norm:
                score += 6

        token_set = self._token_sets.get(law_id, set())
        prefix_set = self._prefix_sets.get(law_id, set())
        exact = 0
        stem = 0
        for token in search_tokens:
            if token in token_set:
                exact += 1
            elif len(token) >= 5 and self._stem_key(token) in prefix_set:
                stem += 1
        score += min(exact * 3, 18)
        score += min(stem * 2, 12)

        domain = self._normalize_id(law.get("domain") or law_id.split("_", 1)[0])
        for keyword in self.keyword_groups.get(domain, []):
            if keyword in q_norm or keyword in search_tokens:
                score += 4
                break
        if score > 0:
            if curated_domain_hints and domain in curated_domain_hints:
                score += self.curated_domain_hint_bonus
            elif domain_hints and domain in domain_hints:
                score += self.domain_hint_bonus
        return score

    def _dense_variant_rankings(
        self,
        query: str,
        event_date: str,
        search_tokens: Set[str],
        *,
        variants: Optional[List[str]] = None,
    ) -> List[List[tuple[float, Dict, Dict[str, float]]]]:
        """Map dense hits back to unchanged, checksum-verified corpus records.

        Returns one ranking per query variant (clause), unfused, so callers can
        inspect what each individually understood clause found on its own.
        """
        variants = variants or self._dense_query_variants(query)
        try:
            search_many = getattr(self.vector_search, "search_many", None)
            if callable(search_many):
                dense_result_batches = search_many(variants)
            else:
                dense_result_batches = [
                    self.vector_search.search(variant) for variant in variants
                ]
        except VectorSearchUnavailableError as exc:
            logger.warning(
                "V6 dense retrieval failed; using V5 lexical ranking for this query: %s",
                exc,
            )
            return []

        rankings: List[List[tuple[float, Dict, Dict[str, float]]]] = []
        for dense_results in dense_result_batches:
            ranking: List[tuple[float, Dict, Dict[str, float]]] = []
            for result in dense_results:
                law = self._laws_by_id.get(result.law_id)
                if law is None:
                    continue
                if str(law.get("content_hash", "")) != result.content_hash:
                    logger.warning(
                        "Ignoring dense result %s because its content hash is stale.",
                        result.law_id,
                    )
                    continue
                if not self._valid_for_date(law, event_date):
                    continue
                ranking.append(
                    (
                        1.0 - result.distance,
                        law,
                        self._match_evidence(search_tokens, law),
                    )
                )
            if ranking:
                rankings.append(ranking)
        return rankings

    def _fuse_dense_variant_rankings(
        self,
        variant_rankings: List[List[tuple[float, Dict, Dict[str, float]]]],
    ) -> List[tuple[float, Dict, Dict[str, float]]]:
        if not variant_rankings:
            return []
        if len(variant_rankings) == 1:
            return variant_rankings[0]
        return self._fuse_weighted_rankings(
            variant_rankings,
            [1.0] * len(variant_rankings),
            rank_constant=self.hybrid_rrf_k,
        )

    def _dense_query_variants(self, query: str) -> List[str]:
        original = str(query).strip()
        if not self.hybrid_multi_query_enabled or not original:
            return [original] if original else []
        original_without_punctuation = original.strip(" ,.;:!?-\t\r\n")
        parts = re.split(
            r"\s*(?:;|\bning\b|\bja\s+seej[aä]rel\b|\bseej[aä]rel\b|\bja\b)\s*",
            original,
            flags=re.IGNORECASE,
        )
        variants = [original]
        for part in parts:
            cleaned = part.strip(" ,.;:!?-\t\r\n")
            if cleaned == original_without_punctuation or len(cleaned) < 12:
                continue
            if len(self._content_tokens(cleaned)) < 2:
                continue
            if cleaned not in variants:
                variants.append(cleaned)
            if len(variants) >= self.hybrid_max_query_variants:
                break
        return variants

    def _dense_domain_champions(
        self,
        lexical_ranking: List[tuple[float, Dict, Dict[str, float]]],
        dense_ranking: List[tuple[float, Dict, Dict[str, float]]],
        *,
        variant_rankings: Optional[
            List[List[tuple[float, Dict, Dict[str, float]]]]
        ] = None,
    ) -> List[tuple[float, Dict, Dict[str, float]]]:
        """Give each supported domain one semantic representative.

        The branch is intentionally derived from already retrieved corpus
        records. It cannot invent a domain, law ID or source, and it is skipped
        for single-domain queries.
        """
        supported_domains = {
            self._normalize_id(item[1].get("domain", ""))
            for item in lexical_ranking[: max(10, self.max_results * 3)]
            if int(item[2].get("matched_terms", 0)) >= 1
        }
        supported_domains.discard("")

        # A compound question split into 2+ clauses (see _dense_query_variants)
        # may need a domain that plain keyword overlap never touches at all -
        # for example PKS for a child-maintenance clause when nothing in that
        # clause happens to share a lexical token with any PKS section. Only a
        # clause's own top dense hit counts, never its full candidate list, so
        # one tangential semantic match cannot flood an unrelated domain in.
        if variant_rankings and len(variant_rankings) >= 2:
            for ranking in variant_rankings:
                if not ranking:
                    continue
                top_domain = self._normalize_id(ranking[0][1].get("domain", ""))
                if top_domain:
                    supported_domains.add(top_domain)

        if len(supported_domains) < 2:
            return []

        champions = []
        seen_domains = set()
        for item in dense_ranking:
            domain = self._normalize_id(item[1].get("domain", ""))
            if domain not in supported_domains or domain in seen_domains:
                continue
            champions.append(item)
            seen_domains.add(domain)
        return champions

    def _rerank_candidates(
        self,
        query_variants: List[str],
        fused: List[tuple[float, Dict, Dict[str, float]]],
    ) -> List[tuple[float, Dict, Dict[str, float]]]:
        """Blend verified cross-encoder ranks without changing the candidate set."""
        if not self.reranker_enabled or not self.hybrid_ready or not fused:
            return fused
        try:
            reranked_variants = []
            for variant in query_variants or []:
                reranked = self.reranker.rerank(variant, fused)
                if reranked:
                    reranked_variants.append(reranked)
            if not reranked_variants:
                return fused
            # RRF sum alone rewards a generic section that ranks moderately
            # for every clause. Round-robin keeps the best result of each
            # separately understood clause near the top, which is essential
            # for questions that require two different acts or procedures.
            reranked_fused = self._round_robin_rankings(reranked_variants)
            return self._fuse_weighted_rankings(
                [fused, reranked_fused],
                [1.0, self.reranker_weight],
                rank_constant=self.hybrid_rrf_k,
            )
        except RerankerUnavailableError as exc:
            logger.warning(
                "V6.1 reranker failed; keeping the verified V6 ranking: %s", exc
            )
            return fused

    @staticmethod
    def _round_robin_rankings(
        rankings: List[List[tuple[float, Dict, Dict[str, float]]]],
    ) -> List[tuple[float, Dict, Dict[str, float]]]:
        """Interleave variant ranks while keeping every corpus ID unique."""
        result = []
        seen = set()
        maximum = max((len(ranking) for ranking in rankings), default=0)
        for rank_index in range(maximum):
            for ranking in rankings:
                if rank_index >= len(ranking):
                    continue
                item = ranking[rank_index]
                law_id = item[1]["id"]
                if law_id in seen:
                    continue
                seen.add(law_id)
                result.append(item)
        return result

    @staticmethod
    def _fuse_rankings(
        primary: List[tuple[float, Dict, Dict[str, float]]],
        legacy: List[tuple[float, Dict, Dict[str, float]]],
        rank_constant: int = 20,
    ) -> List[tuple[float, Dict, Dict[str, float]]]:
        """Reciprocal-rank fusion rewards agreement without trusting one scorer."""
        return LegalSearchService._fuse_weighted_rankings(
            (primary, legacy),
            (1.0, 1.0),
            rank_constant=rank_constant,
        )

    @staticmethod
    def _fuse_weighted_rankings(
        rankings,
        weights,
        *,
        rank_constant: int = 20,
    ) -> List[tuple[float, Dict, Dict[str, float]]]:
        """Weighted RRF keeps scorer scales separate and combines only rank."""
        fused_scores: Dict[str, float] = {}
        items: Dict[str, tuple[Dict, Dict[str, float]]] = {}
        for ranking, weight in zip(rankings, weights):
            if weight <= 0:
                continue
            for rank, (_, law, evidence) in enumerate(ranking[:200], start=1):
                law_id = law["id"]
                fused_scores[law_id] = fused_scores.get(law_id, 0.0) + (
                    weight / (rank_constant + rank)
                )
                items[law_id] = (law, evidence)
        return sorted(
            (
                (score, items[law_id][0], items[law_id][1])
                for law_id, score in fused_scores.items()
            ),
            key=lambda item: (-item[0], item[1]["id"]),
        )

    def _match_evidence(self, search_tokens: Set[str], law: Dict) -> Dict[str, float]:
        law_id = law["id"]
        token_set = self._token_sets.get(law_id, set())
        prefix_set = self._prefix_sets.get(law_id, set())
        matched = {
            self._stem_key(token)
            for token in search_tokens
            if len(token) >= 3
            and (
                token in token_set
                or (len(token) >= 5 and self._stem_key(token) in prefix_set)
            )
        }
        return {
            "matched_terms": len(matched),
            "proximity_bonus": 0.0,
        }

    def _query_has_legal_relevance(
        self,
        normalized_query: str,
        query_tokens: Set[str],
        best_evidence: Dict[str, float],
        has_curated_hint: bool,
    ) -> bool:
        if not query_tokens:
            return False
        matched = int(best_evidence["matched_terms"])
        intent_keys = {self._stem_key(token) for token in self._legal_intent_terms}
        has_intent_term = any(
            token in self._legal_intent_terms
            or (
                self.query_understanding.enabled
                and self._stem_key(token) in intent_keys
            )
            for token in query_tokens
        )
        if has_curated_hint or has_intent_term:
            return matched >= 1
        legal_question_signals = {
            "keelatud", "kohtusse", "kohustus", "lubatud", "oigus", "peab",
            "tohib", "trahv", "vaidlustada", "voib",
        }
        if not (set(normalized_query.split()) & legal_question_signals):
            return False
        if not self.query_understanding.enabled:
            coverage = matched / len(query_tokens)
            return matched >= 2 and coverage >= 0.4
        return matched >= 1

    def _build_legal_intent_terms(self, query_lexicon: List[Dict]) -> Set[str]:
        terms = {
            "amet", "ametnik", "arest", "elatis", "haldusakt", "isikuandmed",
            "abikaasa", "ahvardus", "alkoholijoove", "hagiavaldus", "juurdepaas",
            "kaebus", "kahju", "karistus", "kaup", "kelmus", "kohus", "kohtutaitur",
            "korter", "kuritegu", "laps", "leping", "maks", "omanik",
            "krunt", "loi", "mootorsoidukijuht", "muuja", "naaber", "otsus",
            "paasu", "palk", "politsei", "pood", "pohioigus", "reaalservituut",
            "seadus", "soidukiirus", "sund", "taitemenetlus", "tapma", "tarbija",
            "teave", "tootaja", "tooandja", "tooleping", "trahv", "tsiviilkohus",
            "uhisvara", "vaie", "valdus", "vanem", "varaühisus", "vargus",
            "vigane", "vigast", "vaarkohtlemine", "vaartegu", "volgnik", "uuri",
        }
        for values in self.keyword_groups.values():
            for value in values:
                terms.update(self._content_tokens(value))
        for entry in query_lexicon:
            for value in entry.get("expand") or []:
                terms.update(self._content_tokens(value))
        return terms

    def _section_heading(self, law: Dict) -> str:
        text = str(law.get("text", ""))[:500]
        text = re.sub(r"^\s*\S+\s+§\s+\S+\.\s*", "", text)
        heading = re.split(r"\s+\d+\s+\(\d+", text, maxsplit=1)[0]
        if heading == text:
            heading = re.split(r"(?<=\.)\s+", heading, maxsplit=1)[0]
        return heading[:180]

    @staticmethod
    def _stem_key(token: str) -> str:
        return token[:5] if len(token) >= 5 else token

    def _content_tokens(self, text: str) -> List[str]:
        return [
            token
            for token in self._tokenize(text)
            if token not in ESTONIAN_QUERY_STOPWORDS and not token.isdigit()
        ]

    def _expand_tokens(self, tokens: Set[str]) -> Set[str]:
        expanded = set(tokens)
        for token in tokens:
            for base, related in self.synonyms.items():
                # V5: sünonüüme laiendame ainult täpse termini põhjal. Varasem
                # esimese 5 tähe heuristic oli läbipaistmatu pseudo-fuzzy loogika
                # ning võis juhuslikult vale valdkonda aktiveerida. Kirjavead ja
                # sõnavariandid lahendab nüüd QueryUnderstandingService koos
                # mõõdetava confidence lävendiga.
                if token == base:
                    expanded.add(base)
                    expanded.update(related)
        return expanded

    # ------------------------------------------------------------------
    # Kuupäev + abifunktsioonid
    # ------------------------------------------------------------------
    def _valid_for_date(self, law: Dict, event_date: str) -> bool:
        if not event_date:
            return True
        event = self._parse_date(event_date)
        if not event:
            return True
        valid_from = self._parse_date(law.get("valid_from"))
        valid_to = self._parse_date(law.get("valid_to"))
        if valid_from and event < valid_from:
            return False
        if valid_to and event > valid_to:
            return False
        return True

    def _parse_date(self, value):
        if not value:
            return None
        if isinstance(value, datetime):
            return value.date()
        text = str(value).strip()
        for fmt in self.DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None

    def _normalize_text(self, text: str) -> str:
        if not text:
            return ""
        text = text.lower().translate(
            str.maketrans({"ä": "a", "ö": "o", "ü": "u", "õ": "o", "š": "s", "ž": "z"})
        )
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z0-9]+", " ", text).strip()

    def _tokenize(self, text: str) -> List[str]:
        return [t for t in re.findall(r"[a-z0-9]+", self._normalize_text(text)) if len(t) >= 2]

    def _normalize_id(self, law_id: str) -> str:
        return re.sub(r"\s+", "", str(law_id or "")).strip().upper()
