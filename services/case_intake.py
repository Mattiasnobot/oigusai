"""Turn free-form user text into a small, inspectable case description.

This layer performs no legal analysis. It extracts only facts and the kind of
help requested, asks a few high-value clarification questions when necessary,
and creates a focused search query for the legal retrieval layer.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List

from services.offline_ai import OfflineAIService
from services.turn_planner import ConversationTurnPlanner


logger = logging.getLogger(__name__)

MAX_INPUT_CHARS = 50_000
CHUNK_CHARS = 15_000
MAX_CHUNKS = 4

CASE_INTAKE_SCHEMA = {
    "type": "object",
    "properties": {
        "input_type": {
            "type": "string",
            "enum": ["fragment", "question", "narrative", "document_dump"],
        },
        "topic": {"type": "string"},
        "summary": {"type": "string"},
        "user_goal": {"type": "string"},
        "help_types": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "rights_explanation",
                    "next_steps",
                    "deadline",
                    "amount_or_claim",
                    "document_help",
                    "challenge_decision",
                    "missed_deadline",
                    "payment_plan",
                    "find_authority",
                    "general_information",
                ],
            },
            "maxItems": 5,
        },
        "parties": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                    "label": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["role", "label", "evidence"],
                "additionalProperties": False,
            },
            "maxItems": 8,
        },
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "actor": {"type": "string"},
                    "action": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["date", "actor", "action", "evidence"],
                "additionalProperties": False,
            },
            "maxItems": 12,
        },
        "amounts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["label", "value", "evidence"],
                "additionalProperties": False,
            },
            "maxItems": 8,
        },
        "documents": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["name", "evidence"],
                "additionalProperties": False,
            },
            "maxItems": 8,
        },
        "missing_facts": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 5,
        },
        "clarification_questions": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 3,
        },
        "ready_for_analysis": {"type": "boolean"},
        "search_query": {"type": "string"},
    },
    "required": [
        "input_type",
        "topic",
        "summary",
        "user_goal",
        "help_types",
        "parties",
        "events",
        "amounts",
        "documents",
        "missing_facts",
        "clarification_questions",
        "ready_for_analysis",
        "search_query",
    ],
    "additionalProperties": False,
}


class CaseIntakeService:
    def __init__(self, ai_service: OfflineAIService):
        self.ai_service = ai_service

    def understand(
        self,
        case_description: str,
        event_date: str = "",
        current_message: str = "",
    ) -> Dict:
        text = self._clean_input(case_description)
        if not text:
            raise ValueError("Olukorra kirjeldus ei tohi olla tühi.")
        if len(text) > MAX_INPUT_CHARS:
            raise ValueError(
                f"Kirjeldus võib olla kuni {MAX_INPUT_CHARS:,} tähemärki.".replace(",", " ")
            )

        evidence_text = self._user_evidence_text(text)

        if self._is_fragment(evidence_text):
            result = self._fragment_result(evidence_text)
            self._apply_turn_decision(
                result,
                current_message=current_message or evidence_text,
                evidence_text=evidence_text,
                full_text=text,
            )
            result["input_length"] = len(text)
            result["used_ai"] = False
            return result

        if self._is_direct_question(evidence_text):
            result = self._fallback_result(evidence_text)
            self._augment_from_original_text(result, evidence_text)
            result["summary"] = self._grounded_summary(result, evidence_text)
            result["search_query"] = self._grounded_search_query(result, evidence_text)
            self._apply_turn_decision(
                result,
                current_message=current_message or evidence_text,
                evidence_text=evidence_text,
                full_text=text,
            )
            result["analysis_context"] = self._build_analysis_context(result)
            result["input_length"] = len(text)
            result["used_ai"] = False
            return result

        chunks = self._split_chunks(text)
        try:
            partials = [self._understand_chunk(chunk, event_date) for chunk in chunks]
            result = self._merge_results(partials, evidence_text)
            result["used_ai"] = True
        except Exception as exc:
            logger.warning("Juhtumi mõistmise mudelikiht ebaõnnestus: %s", exc)
            result = self._fallback_result(evidence_text)
            result["used_ai"] = False

        self._apply_high_value_clarifications(result, evidence_text, text)
        self._remove_answered_clarifications(result, text)
        self._apply_turn_decision(
            result,
            current_message=current_message or evidence_text,
            evidence_text=evidence_text,
            full_text=text,
        )
        result["input_length"] = len(text)
        return result

    def _understand_chunk(self, text: str, event_date: str) -> Dict:
        raw = self.ai_service.generate_structured(
            self._build_prompt(text, event_date), CASE_INTAKE_SCHEMA
        )
        payload = self._extract_json(raw)
        if not isinstance(payload, dict):
            raise ValueError("Juhtumi mõistmise vastus ei olnud JSON objekt.")
        return self._validate_payload(payload, self._user_evidence_text(text))

    def _build_prompt(self, text: str, event_date: str) -> str:
        date_line = f"Kasutaja lisatud sündmuse kuupäev: {event_date}\n" if event_date else ""
        return f"""Sa oled ÕigusAI juhtumi vastuvõtu abiline.

Sinu ülesanne EI OLE anda õiguslikku hinnangut ega nimetada seadusi. Muuda
kasutaja vabas vormis tekst kontrollitavaks juhtumikirjelduseks.

{date_line}KASUTAJA TEKST:
{text}

REEGLID:
1. Erista juhtunu, osapooled, kuupäevad, summad, dokumendid ja soovitud abi.
2. Ära mõtle välja nime, rolli, sündmust, dokumenti, kuupäeva ega summat.
3. Iga parties, events, amounts ja documents kirje evidence peab olema täpne katkematu tsitaat kasutaja tekstist.
4. Kasutaja arvamus või kahtlus esita kahtlusena, mitte tõendatud faktina.
5. summary peab olema lühike, neutraalne ja tavakeelne.
6. search_query peab kirjeldama juhtumi põhiküsimust lühidalt, ilma uusi asjaolusid lisamata.
7. clarification_questions loendisse lisa ainult küsimus, mille vastus võib õiguslikku hinnangut või allika valikut muuta.
8. Küsi korraga kõige rohkem kolm lihtsat ja grammatiliselt korrektset küsimust.
   Ära kasuta juriidilist žargooni. Kui kasutaja kirjeldab enda olukorda,
   pöördu tema poole sinavormis, mitte ära räägi temast kolmandas isikus.
9. Kui kasutaja küsimus on juba piisavalt selge, jäta clarification_questions tühjaks ja märgi ready_for_analysis=true.
10. help_types näitab, millist abi kasutaja tõenäoliselt soovib.
11. Ära küsi kasutaja ega teise eraisiku nime, isikukoodi, aadressi, telefoni,
    e-posti, kontonumbrit ega muud isikut tuvastavat infot. Kasuta osapoolte
    rolle, näiteks „kasutaja”, „ametnik” või „üürileandja”.
12. Ära korda küsimust, millele kasutaja on juba vastanud, ka siis mitte, kui
    suudad selle teiste sõnadega ümber sõnastada. Vastus „ma ei tea” loeb
    vastuseks.

Tagasta ainult skeemile vastav JSON.
"""

    def _validate_payload(self, payload: Dict, original_text: str) -> Dict:
        input_type = str(payload.get("input_type", "narrative"))
        if input_type not in {"fragment", "question", "narrative", "document_dump"}:
            input_type = "narrative"
        raw_questions = self._string_list(
            payload.get("clarification_questions"), 3, 220
        )
        safe_questions = self._questions(raw_questions)
        only_unsafe_questions = bool(raw_questions) and not safe_questions

        result = {
            "input_type": input_type,
            "topic": self._limited(payload.get("topic"), 140),
            "summary": self._limited(payload.get("summary"), 900),
            "user_goal": self._limited(payload.get("user_goal"), 260),
            "help_types": self._allowed_help_types(payload.get("help_types")),
            "parties": self._validated_evidence_items(
                payload.get("parties"), original_text, ("role", "label", "evidence"), 8
            ),
            "events": self._validated_evidence_items(
                payload.get("events"), original_text, ("date", "actor", "action", "evidence"), 12
            ),
            "amounts": self._validated_evidence_items(
                payload.get("amounts"), original_text, ("label", "value", "evidence"), 8
            ),
            "documents": self._validated_evidence_items(
                payload.get("documents"), original_text, ("name", "evidence"), 8
            ),
            "missing_facts": self._string_list(payload.get("missing_facts"), 5, 180),
            "clarification_questions": safe_questions,
            "ready_for_analysis": bool(payload.get("ready_for_analysis", False)),
            "search_query": self._limited(payload.get("search_query"), 700),
        }

        if not result["topic"]:
            result["topic"] = self._limited(original_text, 140)
        if not result["summary"] or self._adds_new_quantity(result["summary"], original_text):
            result["summary"] = self._limited(original_text, 700)
        if not result["user_goal"]:
            result["user_goal"] = self._infer_goal(original_text)
        result["help_types"] = self._unique_strings(
            self._infer_help_types(original_text) + result["help_types"]
        )[:3]
        if not result["search_query"] or self._adds_new_quantity(
            result["search_query"], original_text
        ):
            result["search_query"] = self._limited(original_text, 700)

        self._augment_from_original_text(result, original_text)
        result["summary"] = self._grounded_summary(result, original_text)
        result["search_query"] = self._grounded_search_query(result, original_text)

        # Selge küsimuse või abisoovi korral saab anda tingimusliku esmase
        # vastuse ka siis, kui mõni detail puudub. Ära sunni kasutajat läbima
        # tarbetut küsimustikku.
        if self._has_clear_request(original_text):
            result["ready_for_analysis"] = True
            result["clarification_questions"] = []

        if not result["ready_for_analysis"] and not result["clarification_questions"]:
            if only_unsafe_questions:
                # Isikut tuvastav detail ei ole üldise õigusliku selgituse
                # eeltingimus. Jätka olemasoleva info põhjal, ära asenda
                # blokeeritud küsimust uue tarbetu küsimustikuga.
                result["ready_for_analysis"] = True
            else:
                result["clarification_questions"] = self._generic_questions()

        result["analysis_context"] = self._build_analysis_context(result)
        return result

    def _merge_results(self, partials: List[Dict], original_text: str) -> Dict:
        if len(partials) == 1:
            return partials[0]

        result = {
            "input_type": "document_dump" if len(partials) > 2 else "narrative",
            "topic": " / ".join(self._unique_strings(p["topic"] for p in partials))[:220],
            "summary": " ".join(self._unique_strings(p["summary"] for p in partials))[:1600],
            "user_goal": " / ".join(
                self._unique_strings(p["user_goal"] for p in partials)
            )[:400],
            "help_types": self._unique_strings(
                value for p in partials for value in p["help_types"]
            )[:3],
            "parties": self._unique_dicts(
                item for p in partials for item in p["parties"]
            )[:8],
            "events": self._unique_dicts(
                item for p in partials for item in p["events"]
            )[:12],
            "amounts": self._unique_dicts(
                item for p in partials for item in p["amounts"]
            )[:8],
            "documents": self._unique_dicts(
                item for p in partials for item in p["documents"]
            )[:8],
            "missing_facts": self._unique_strings(
                value for p in partials for value in p["missing_facts"]
            )[:5],
            "clarification_questions": self._unique_strings(
                value for p in partials for value in p["clarification_questions"]
            )[:3],
            "ready_for_analysis": any(p["ready_for_analysis"] for p in partials),
            "search_query": " ".join(
                self._unique_strings(p["search_query"] for p in partials)
            )[:1000],
        }
        if not result["summary"]:
            result["summary"] = self._limited(original_text, 1200)
        if not result["search_query"]:
            result["search_query"] = self._limited(original_text, 1000)
        result["search_query"] = self._grounded_search_query(result, original_text)
        result["analysis_context"] = self._build_analysis_context(result)
        return result

    def _fragment_result(self, text: str) -> Dict:
        # The surrounding sentence already supplies its own punctuation. A
        # fragment such as "Koondamine ." must not produce duplicate marks.
        topic = re.sub(r"\s+([,.;:!?])", r"\1", self._limited(text, 140))
        topic = topic.rstrip(" .,:;!?") or self._limited(text, 140)
        result = {
            "input_type": "fragment",
            "topic": topic,
            "summary": f"Kirjutasid teemaks „{topic}“.",
            "user_goal": "Abi eesmärk vajab täpsustamist.",
            "help_types": ["general_information"],
            "parties": [],
            "events": [],
            "amounts": [],
            "documents": [],
            "missing_facts": ["mis juhtus", "kes on osapooled", "millist abi vajad"],
            "clarification_questions": self._generic_questions(),
            "ready_for_analysis": False,
            "search_query": topic,
        }
        result["analysis_context"] = self._build_analysis_context(result)
        return result

    def _fallback_result(self, text: str) -> Dict:
        result = {
            "input_type": "question" if "?" in text else "narrative",
            "topic": self._limited(text, 140),
            "summary": self._limited(text, 900),
            "user_goal": self._infer_goal(text),
            "help_types": self._infer_help_types(text),
            "parties": [],
            "events": [],
            "amounts": [],
            "documents": [],
            "missing_facts": [],
            "clarification_questions": [],
            "ready_for_analysis": True,
            "search_query": self._limited(text, 700),
        }
        result["analysis_context"] = self._build_analysis_context(result)
        return result

    def _split_chunks(self, text: str) -> List[str]:
        if len(text) <= CHUNK_CHARS:
            return [text]

        chunks: List[str] = []
        remainder = text
        while remainder and len(chunks) < MAX_CHUNKS:
            if len(remainder) <= CHUNK_CHARS:
                chunks.append(remainder)
                break
            cut = remainder.rfind("\n", 0, CHUNK_CHARS)
            if cut < CHUNK_CHARS // 2:
                cut = remainder.rfind(". ", 0, CHUNK_CHARS)
            if cut < CHUNK_CHARS // 2:
                cut = CHUNK_CHARS
            chunks.append(remainder[:cut].strip())
            remainder = remainder[cut:].strip()
        if remainder:
            chunks[-1] = (chunks[-1] + "\n" + remainder)[: CHUNK_CHARS * 2]
        return [chunk for chunk in chunks if chunk]

    def _validated_evidence_items(
        self, items, original_text: str, fields: tuple, maximum: int
    ) -> List[Dict]:
        if not isinstance(items, list):
            return []
        validated: List[Dict] = []
        normalized_original = self._normalize_for_match(original_text)
        for item in items[:maximum]:
            if not isinstance(item, dict):
                continue
            evidence = self._limited(item.get("evidence"), 320)
            if len(evidence) < 2:
                continue
            if self._normalize_for_match(evidence) not in normalized_original:
                continue
            cleaned = {field: self._limited(item.get(field), 260) for field in fields}
            if evidence and any(value for key, value in cleaned.items() if key != "evidence"):
                validated.append(cleaned)
        return validated

    def _build_analysis_context(self, result: Dict) -> str:
        lines = [f"Olukorra kokkuvõte: {result['summary']}"]
        if result.get("turn_summary"):
            lines.append(f"Kasutaja viimase sõnumi mõte: {result['turn_summary']}")
        if result["user_goal"]:
            lines.append(f"Kasutaja soovitud abi: {result['user_goal']}")
        if result.get("current_intents"):
            lines.append(
                "Viimase sõnumi eesmärgid: "
                + ", ".join(result["current_intents"])
            )
        if result.get("answer_requirements"):
            lines.append("Vastus peab käsitlema:")
            lines.extend(
                f"- {requirement}"
                for requirement in result["answer_requirements"]
            )
        if result["parties"]:
            lines.append(
                "Osapooled: "
                + "; ".join(
                    f"{item.get('label') or item.get('role')} ({item.get('role')})"
                    for item in result["parties"]
                )
            )
        if result["events"]:
            lines.append("Sündmused:")
            for item in result["events"]:
                date = item.get("date") or "kuupäev teadmata"
                actor = item.get("actor") or "osapool"
                lines.append(f"- {date}: {actor} – {item.get('action')}")
        if result["amounts"]:
            lines.append(
                "Summad: "
                + "; ".join(
                    f"{item.get('label')}: {item.get('value')}" for item in result["amounts"]
                )
            )
        if result["documents"]:
            lines.append(
                "Dokumendid: "
                + ", ".join(item.get("name", "") for item in result["documents"])
            )
        return "\n".join(lines)[:5000]

    def _apply_turn_decision(
        self,
        result: Dict,
        *,
        current_message: str,
        evidence_text: str,
        full_text: str,
    ) -> None:
        """Let the latest turn, not stale history, choose the next action."""
        decision = ConversationTurnPlanner.decide(
            current_message=current_message,
            case_text=full_text,
            existing_help_types=result.get("help_types", []),
        )
        result["current_intents"] = list(decision.current_intents)
        result["help_types"] = self._unique_strings(
            [*decision.current_intents, *result.get("help_types", [])]
        )[:5]
        result["turn_summary"] = decision.turn_summary
        result["decision_reason"] = decision.decision_reason
        result["answer_requirements"] = list(decision.answer_requirements)

        if decision.clarification_question:
            result["next_action"] = "clarify"
            result["ready_for_analysis"] = False
            result["clarification_questions"] = [decision.clarification_question]
            result["missing_facts"] = self._unique_strings(
                [*decision.missing_facts, *result.get("missing_facts", [])]
            )[:5]
        elif decision.clarification_answered:
            # The user has answered the one combined planner question, including
            # with "Ma ei tea".  Do not let the extraction model replace it with
            # another lower-priority detail question on the same turn.
            result["next_action"] = "analyze"
            result["ready_for_analysis"] = True
            result["clarification_questions"] = []
        elif result.get("clarification_questions") and not result.get("ready_for_analysis"):
            # Preserve an earlier high-value case clarification, for example the
            # document question for a newly described fine.
            result["next_action"] = "clarify"
        else:
            result["next_action"] = "analyze"
            result["ready_for_analysis"] = True
            result["clarification_questions"] = []

        # The focused query carries only stable background anchors plus the
        # latest request.  It intentionally omits UI-authored questions and old
        # generic document words that could hijack retrieval.
        if current_message.strip():
            result["search_query"] = decision.search_query or self._grounded_search_query(
                result, evidence_text
            )
        result["analysis_context"] = self._build_analysis_context(result)

    def _augment_from_original_text(self, result: Dict, original_text: str) -> None:
        """Fill obvious roles, events, quantities and documents deterministically."""
        role_pattern = re.compile(
            r"\b(?:üürileandja|üürnik|tööandja|töötaja|ostja|müüja|tarbija|"
            r"kaupleja|politsei|kohtutäitur|ametnik|abikaasa|vanem|laps|naaber)\b",
            re.IGNORECASE,
        )
        existing_roles = {item.get("role", "").casefold() for item in result["parties"]}
        for match in role_pattern.finditer(original_text):
            evidence = match.group(0)
            role = evidence.casefold()
            if role in existing_roles:
                continue
            result["parties"].append({
                "role": role,
                "label": evidence,
                "evidence": evidence,
            })
            existing_roles.add(role)
            if len(result["parties"]) >= 8:
                break

        quantity_pattern = re.compile(
            r"\b(?:\d+(?:[.,]\d+)?|ühe|kahe|kolme|nelja|viie|kuue|seitsme|"
            r"kaheksa|üheksa|kümne)\s*(?:€|eurot?|kuud?|kuu|päeva|päev|"
            r"nädalat?|aastat?|protsenti|%)\b",
            re.IGNORECASE,
        )
        existing_amounts = {item.get("evidence", "").casefold() for item in result["amounts"]}
        for match in quantity_pattern.finditer(original_text):
            evidence = match.group(0)
            if evidence.casefold() in existing_amounts:
                continue
            result["amounts"].append({
                "label": "tekstis nimetatud suurus või ajavahemik",
                "value": evidence,
                "evidence": evidence,
            })
            existing_amounts.add(evidence.casefold())
            if len(result["amounts"]) >= 8:
                break

        document_pattern = re.compile(
            r"\b(?:tööleping\w*|üürileping\w*|leping\w*|arve\w*|otsus\w*|"
            r"teade\w*|avaldus\w*|protokoll\w*|e-kiri\w*|kirja\w*)\b",
            re.IGNORECASE,
        )
        existing_documents = {
            item.get("evidence", "").casefold() for item in result["documents"]
        }
        for match in document_pattern.finditer(original_text):
            evidence = match.group(0)
            if evidence.casefold() in existing_documents:
                continue
            result["documents"].append({"name": evidence, "evidence": evidence})
            existing_documents.add(evidence.casefold())
            if len(result["documents"]) >= 8:
                break

        if not result["events"]:
            action_pattern = re.compile(
                r"\b(?:teatas|ütles|küsib|küsis|nõuab|nõudis|valland\w*|"
                r"koond\w*|maks\w*|ost\w*|müü\w*|keeld\w*|andis|võttis|"
                r"lõpet\w*|tõst\w*|kahjust\w*|arest\w*|trahv\w*)\b",
                re.IGNORECASE,
            )
            sentences = re.split(r"(?<=[.!?])\s+", original_text)
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence or not action_pattern.search(sentence):
                    continue
                actor = result["parties"][0]["role"] if result["parties"] else "osapool"
                result["events"].append({
                    "date": "teadmata",
                    "actor": actor,
                    "action": sentence.rstrip(".!?"),
                    "evidence": sentence,
                })
                if len(result["events"]) >= 4:
                    break

    def _grounded_summary(self, result: Dict, original_text: str) -> str:
        """Build the visible summary only from verbatim user-text evidence."""
        event_evidence = self._unique_strings(
            item.get("evidence", "") for item in result.get("events", [])
        )
        if event_evidence:
            joined = " ".join(value.rstrip() for value in event_evidence[:4])
            return self._limited(f"Kirjelduse järgi: {joined}", 900)
        return self._limited(original_text, 900)

    def _grounded_search_query(self, result: Dict, original_text: str) -> str:
        """Keep short inputs intact; distil long inputs only from grounded fields."""
        if len(original_text) <= 3000:
            return self._limited(original_text, 2000)

        parts = [result.get("topic", "")]
        parts.extend(item.get("evidence", "") for item in result.get("events", [])[:6])
        parts.extend(item.get("evidence", "") for item in result.get("amounts", [])[:4])
        parts.extend(item.get("evidence", "") for item in result.get("documents", [])[:4])
        grounded = " ".join(self._unique_strings(parts))
        return self._limited(grounded or original_text, 2000)

    def _apply_high_value_clarifications(
        self,
        result: Dict,
        evidence_text: str,
        full_text: str,
    ) -> None:
        """Override generic readiness when one missing fact changes the answer.

        The local model may consider a polite request such as "selgita mu õigusi"
        clear even though the underlying event is still ambiguous.  A claimed
        fine is one such case: the document type, stated offence and receipt date
        determine both the applicable procedure and any next step.
        """
        lowered = evidence_text.casefold()
        personal_fine_event = self._has_personal_fine_event(lowered)
        if not personal_fine_event:
            return

        fine_details_present = re.search(
            r"\b(?:trahviotsus\w*|trahvitea\w*|väärteoprotokoll\w*|"
            r"kiirmenetluse\s+otsus\w*|rikkumis\w*|paragrahv\w*|"
            r"kirjalik\w*\s+otsus\w*|otsust\s+ei\s+saanud|"
            r"dokumenti\s+ei\s+saanud|suuline\w*\s+trahv\w*)\b",
            lowered,
        )
        fine_details_answered = self._fine_clarification_was_answered(full_text)
        if fine_details_present or fine_details_answered:
            needs_decision_maker = (
                "abipolitsei" in lowered
                and self._fine_document_was_received(evidence_text)
                and not self._fine_decision_maker_is_known(evidence_text)
                and not self._decision_maker_clarification_was_answered(full_text)
            )
            if needs_decision_maker:
                question = (
                    "Kes on trahviteatel „kohtuvälise menetleja” ja „otsuse teinud "
                    "ametnikuna” kirjas ning milline seaduse paragrahv on rikkumise "
                    "juures märgitud?"
                )
                result["ready_for_analysis"] = False
                result["clarification_questions"] = [question]
                result["missing_facts"] = self._unique_strings(
                    [
                        "trahviteatele märgitud menetleja, otsuse tegija ja rikkumise paragrahv",
                        *result.get("missing_facts", []),
                    ]
                )[:5]
                return

            # The user may legitimately answer "I do not know". After the
            # focused questions have been answered, continue with a conditional
            # explanation instead of rephrasing the same question indefinitely.
            if fine_details_answered or self._decision_maker_clarification_was_answered(full_text):
                result["ready_for_analysis"] = True
                result["clarification_questions"] = []
            return

        question = (
            "Kas said trahviotsuse või trahviteate, mis rikkumine sinna märgiti "
            "ja millal said selle kätte?"
        )
        result["ready_for_analysis"] = False
        result["clarification_questions"] = [question]
        result["missing_facts"] = self._unique_strings(
            [
                "trahvidokumendi liik, otsusele märgitud rikkumine ja kättesaamise aeg",
                *result.get("missing_facts", []),
            ]
        )[:5]

    @staticmethod
    def _has_personal_fine_event(text: str) -> bool:
        """Recognize ordinary fine descriptions even with reasons between words."""
        lowered = str(text or "").casefold()
        direct_patterns = (
            r"\btrahvis\w*\b",
            r"\btrahviti\w*\b",
            r"\bsain\s+(?:ma\s+)?(?:[^.!?\n]{0,90}\s+)?trahvi\b",
            r"\b(?:määras|tegi|kirjutas)\w*\b\s+(?:mulle|minule)\b"
            r"[^.!?\n]{0,100}\btrahvi\b",
            r"\b(?:määras|tegi|kirjutas)\w*\b\s+(?:mulle\s+)?trahvi\b",
        )
        if any(re.search(pattern, lowered) for pattern in direct_patterns):
            return True
        return bool(re.search(
            r"\babipolitsei\w*\b[^.!?\n]{0,140}\btrahv\w*\b",
            lowered,
        ))

    @staticmethod
    def _fine_clarification_was_answered(full_text: str) -> bool:
        for match in re.finditer(
            r"ÕigusAI küsimus:\s*(.*?)\nKasutaja vastus:\s*(.*?)"
            r"(?=\nÕigusAI küsimus:|\Z)",
            full_text or "",
            flags=re.IGNORECASE | re.DOTALL,
        ):
            question, answer = match.groups()
            if "trahvi" in question.casefold() and answer.strip():
                return True
        return False

    @staticmethod
    def _decision_maker_clarification_was_answered(full_text: str) -> bool:
        for match in re.finditer(
            r"ÕigusAI küsimus:\s*(.*?)\nKasutaja vastus:\s*(.*?)"
            r"(?=\nÕigusAI küsimus:|\Z)",
            full_text or "",
            flags=re.IGNORECASE | re.DOTALL,
        ):
            question, answer = match.groups()
            lowered_question = question.casefold()
            if (
                "kohtuvälise menetleja" in lowered_question
                and "otsuse teinud" in lowered_question
                and answer.strip()
            ):
                return True
        return False

    @staticmethod
    def _fine_document_was_received(evidence_text: str) -> bool:
        lowered = evidence_text.casefold()
        if re.search(
            r"\b(?:dokumenti|otsust|trahviteadet|trahviotsust)\s+"
            r"(?:mulle\s+)?ei\s+(?:antud|saanud)\b",
            lowered,
        ):
            return False
        return bool(re.search(
            r"\b(?:sain|anti|väljastati|toimetati)\b.{0,40}"
            r"\b(?:trahvitea\w*|trahviotsus\w*|kiirmenetluse\s+otsus\w*)\b",
            lowered,
        ))

    @staticmethod
    def _fine_decision_maker_is_known(evidence_text: str) -> bool:
        return bool(re.search(
            r"\b(?:kohtuväline\s+menetleja|otsuse\s+tegi|otsuse\s+teinud|"
            r"menetlejaks|politsei-\s*ja\s*piirivalveamet|\bppa\b)\b",
            evidence_text.casefold(),
        ))

    @classmethod
    def _user_evidence_text(cls, value: str) -> str:
        """Remove UI-authored questions while retaining only the user's answers."""
        lines: List[str] = []
        for line in str(value or "").splitlines():
            stripped = line.strip()
            if stripped.casefold() == "kasutaja täpsustused:":
                continue
            if stripped.casefold().startswith("õigusai küsimus:"):
                continue
            if stripped.casefold().startswith("kasutaja vastus:"):
                stripped = stripped.split(":", 1)[1].strip()
            if stripped:
                lines.append(stripped)
        return cls._clean_input("\n".join(lines))

    @staticmethod
    def _extract_json(text: str):
        decoder = json.JSONDecoder()
        for index, char in enumerate(text or ""):
            if char != "{":
                continue
            try:
                payload, _ = decoder.raw_decode(text[index:])
                return payload
            except json.JSONDecodeError:
                continue
        return None

    @staticmethod
    def _clean(value) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @staticmethod
    def _clean_input(value) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @classmethod
    def _limited(cls, value, maximum: int) -> str:
        text = cls._clean(value)
        return text[:maximum].rstrip()

    @staticmethod
    def _normalize_for_match(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().casefold()

    @classmethod
    def _is_fragment(cls, text: str) -> bool:
        words = re.findall(r"[A-Za-zÕÄÖÜõäöü]{2,}", text)
        return len(words) <= 2 and "?" not in text and len(text) <= 100

    @staticmethod
    def _is_direct_question(text: str) -> bool:
        words = re.findall(r"[A-Za-zÕÄÖÜõäöü]{2,}", text)
        if "?" not in text or len(words) > 28:
            return False
        return bool(re.match(
            r"^\s*(?:kas|kes|mida|millal|milline|millised|mitu|kui|kuidas|kuhu|"
            r"miks|mille|millistel)\b",
            text,
            flags=re.IGNORECASE,
        ))

    @staticmethod
    def _generic_questions() -> List[str]:
        return [
            "Mis täpselt juhtus või mida teine osapool tegi?",
            "Kes on olukorra osapooled?",
            "Kas soovid õiguste selgitust, järgmisi samme või dokumendi koostamist?",
        ]

    @staticmethod
    def _allowed_help_types(values) -> List[str]:
        allowed = {
            "rights_explanation",
            "next_steps",
            "deadline",
            "amount_or_claim",
            "document_help",
            "challenge_decision",
            "missed_deadline",
            "payment_plan",
            "find_authority",
            "general_information",
        }
        if not isinstance(values, list):
            return []
        return [str(value) for value in values if str(value) in allowed][:5]

    @classmethod
    def _string_list(cls, values, maximum: int, max_length: int) -> List[str]:
        if not isinstance(values, list):
            return []
        return cls._unique_strings(cls._limited(value, max_length) for value in values)[:maximum]

    @classmethod
    def _questions(cls, values) -> List[str]:
        questions = cls._string_list(values, 3, 220)
        safe = [
            cls._polish_question(question) for question in questions
            if not cls._requests_personal_identifier(question)
        ]
        return [question if question.endswith("?") else question + "?" for question in safe]

    @classmethod
    def _polish_question(cls, value: str) -> str:
        """Fix small recurring local-model language errors before UI output."""
        question = re.sub(r"\s+([,.;:!?])", r"\1", cls._clean(value))
        question = re.sub(
            r"^Kas töötaja on teadlik,\s*millist konkreetset vigu teda "
            r"süüdistatakse\??$",
            "Kas tööandja ütles, millises konkreetses rikkumises ta sind süüdistab?",
            question,
            flags=re.IGNORECASE,
        )
        question = re.sub(
            r"^Kui kaua kestis töösuhkus enne seda sündmust\??$",
            "Kui kaua olid enne vallandamist selle tööandja juures töötanud?",
            question,
            flags=re.IGNORECASE,
        )
        question = re.sub(
            r"\bmillist konkreetset vigu\b",
            "millistes konkreetsetes vigades",
            question,
            flags=re.IGNORECASE,
        )
        return question

    @staticmethod
    def _requests_personal_identifier(question: str) -> bool:
        lowered = str(question or "").casefold()
        direct_identifiers = (
            "isikukood",
            "telefoninumber",
            "telefoninumbri",
            "e-posti aadress",
            "emaili aadress",
            "kodune aadress",
            "elukoha aadress",
            "kontonumber",
            "pangakonto",
            "dokumendi number",
        )
        if any(value in lowered for value in direct_identifiers):
            return True
        return bool(re.search(
            r"\b(?:sinu|kasutaja|eraisiku|isiku|kliendi)\b.{0,45}"
            r"\b(?:täpne\s+)?(?:ees-\s*ja\s+perekonna)?nimi\b",
            lowered,
        ))

    @classmethod
    def _remove_answered_clarifications(cls, result: Dict, full_text: str) -> None:
        """Do not ask an answered question again with slightly different wording."""
        answered_pairs = cls._conversation_answers(full_text)
        questions = result.get("clarification_questions", [])
        if not answered_pairs or not questions:
            return

        remaining = [
            question
            for question in questions
            if not any(
                answer.strip() and cls._questions_are_similar(question, asked)
                for asked, answer in answered_pairs
            )
        ]
        if len(remaining) == len(questions):
            return
        result["clarification_questions"] = remaining
        if not remaining:
            result["ready_for_analysis"] = True

    @staticmethod
    def _conversation_answers(full_text: str) -> List[tuple]:
        return [
            (question.strip(), answer.strip())
            for question, answer in re.findall(
                r"ÕigusAI küsimus:\s*(.*?)\nKasutaja vastus:\s*(.*?)"
                r"(?=\nÕigusAI küsimus:|\Z)",
                full_text or "",
                flags=re.IGNORECASE | re.DOTALL,
            )
            if question.strip()
        ]

    @classmethod
    def _questions_are_similar(cls, first: str, second: str) -> bool:
        if cls._normalize_for_match(first) == cls._normalize_for_match(second):
            return True
        first_terms = cls._question_terms(first)
        second_terms = cls._question_terms(second)
        if not first_terms or not second_terms:
            return False
        shared = len(first_terms & second_terms)
        return shared >= 2 and shared / min(len(first_terms), len(second_terms)) >= 0.64

    @staticmethod
    def _question_terms(value: str) -> set:
        stopwords = {
            "aga", "ega", "et", "ja", "kas", "kes", "kui", "mida", "milline",
            "millised", "mis", "ning", "on", "oli", "oled", "sa", "see", "sinu",
            "või",
        }
        return {
            word
            for word in re.findall(r"[a-zõäöüšž]{3,}", str(value or "").casefold())
            if word not in stopwords
        }

    @staticmethod
    def _unique_strings(values) -> List[str]:
        seen = set()
        result = []
        for value in values:
            value = str(value or "").strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                result.append(value)
        return result

    @staticmethod
    def _unique_dicts(values) -> List[Dict]:
        seen = set()
        result = []
        for value in values:
            key = json.dumps(value, ensure_ascii=False, sort_keys=True).casefold()
            if key not in seen:
                seen.add(key)
                result.append(value)
        return result

    @staticmethod
    def _quantity_tokens(value: str) -> set:
        pattern = re.compile(
            r"(?<!\w)(?:\d+(?:[.,]\d+)?%?|üks|ühe|kaks|kahe|kolm|kolme|"
            r"neli|nelja|viis|viie|kuus|kuue|seitse|seitsme|kaheksa|"
            r"üheksa|kümme|kümne|sada|saja|tuhat|tuhande)(?!\w)",
            re.IGNORECASE,
        )
        return {token.casefold() for token in pattern.findall(value or "")}

    @classmethod
    def _adds_new_quantity(cls, generated: str, original: str) -> bool:
        return not cls._quantity_tokens(generated).issubset(cls._quantity_tokens(original))

    @staticmethod
    def _infer_goal(text: str) -> str:
        lowered = text.casefold()
        if any(term in lowered for term in (
            "järelmaks", "osamaks", "maksegraaf", "osade kaupa", "tasuda ositi"
        )):
            return "Soovib teada tähtaja või otsuse vaidlustamist ja ositi tasumise võimalust."
        if any(term in lowered for term in (
            "vaidlusta", "vaie", "kaeb", "ei nõustu", "ei ole nõus"
        )):
            return "Soovib teada otsuse või trahvi vaidlustamise võimalusi."
        if any(term in lowered for term in ("kiri", "avaldus", "vastus", "dokument")):
            return "Soovib abi dokumendi või vastuse koostamisel."
        if any(term in lowered for term in ("tähtaeg", "kaua", "millal")):
            return "Soovib teada olulist tähtaega."
        if any(term in lowered for term in ("mida teha", "kuhu pöörd", "kuidas edasi")):
            return "Soovib praktilisi järgmisi samme."
        return "Soovib olukorra ja oma võimaluste selgitust."

    @staticmethod
    def _infer_help_type(text: str) -> str:
        return CaseIntakeService._infer_help_types(text)[0]

    @staticmethod
    def _infer_help_types(text: str) -> List[str]:
        lowered = text.casefold()
        types: List[str] = []
        if (
            any(term in lowered for term in ("tähtaeg", "kaebetäht", "vaide tähtaeg"))
            and any(term in lowered for term in ("möödas", "ületatud", "hiljaks", "ennista"))
        ):
            types.append("missed_deadline")
        if any(term in lowered for term in (
            "vaidlusta", "vaie", "kaeb", "ei nõustu", "ei ole nõus"
        )):
            types.append("challenge_decision")
        if any(term in lowered for term in (
            "järelmaks", "osamaks", "maksegraaf", "osade kaupa", "tasuda ositi"
        )):
            types.append("payment_plan")
        if any(term in lowered for term in ("kiri", "avaldus", "vastus", "dokument")):
            types.append("document_help")
        if any(term in lowered for term in ("tähtaeg", "kaua", "millal")):
            types.append("deadline")
        if any(term in lowered for term in (
            "mida teha", "mida peaksin", "mida ma peaksin", "kuhu pöörd", "kuidas edasi"
        )):
            types.append("next_steps")
        if any(term in lowered for term in (
            "kas ", "kas see", "lubatud", "õigus", "soovin teada", "tahan teada"
        )):
            types.append("rights_explanation")
        return CaseIntakeService._unique_strings(types or ["rights_explanation"])[:5]

    @staticmethod
    def _has_clear_request(text: str) -> bool:
        lowered = text.casefold()
        return "?" in text or any(term in lowered for term in (
            "soovin teada",
            "tahan teada",
            "palun selgita",
            "mida teha",
            "mida peaksin",
            "mida ma peaksin",
            "kuidas edasi",
            "kuhu pöörduda",
        ))
