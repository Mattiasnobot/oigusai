"""Understand the latest user turn and choose the next conversational action.

The case intake service extracts facts from the whole conversation.  This module
has a different responsibility: it keeps the latest user message separate from
the background, detects every current help request and decides whether one
legally decisive clarification is needed before retrieval.

The rules are deterministic on purpose.  A local model may enrich the case
summary, but it may not silently drop one of the user's questions or let an old
word in the conversation override the latest request.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Sequence


@dataclass(frozen=True)
class TurnDecision:
    current_intents: tuple[str, ...]
    next_action: str
    decision_reason: str
    answer_requirements: tuple[str, ...]
    missing_facts: tuple[str, ...]
    clarification_question: str
    search_query: str
    turn_summary: str
    clarification_answered: bool = False


class ConversationTurnPlanner:
    """Create an inspectable action decision for one user turn."""

    _INTENT_PATTERNS: Sequence[tuple[str, tuple[str, ...]]] = (
        (
            "missed_deadline",
            (
                r"\b(?:kaebe|kaebuse|vaide|vaidlustamise)?\s*tahta\w*\b.{0,55}\b(?:moodas|uletatud|hiljaks)\b",
                r"\b(?:moodas|uletatud|hiljaks)\b.{0,55}\btahta\w*\b",
                r"\btahta\w*\s+ennist\w*\b",
                r"\bennist\w*\s+tahta\w*\b",
            ),
        ),
        (
            "challenge_decision",
            (
                r"\bvaidlust\w*\b",
                r"\bkaeb\w*\b",
                r"\bvaie\w*\b",
                r"\bei\s+(?:ole\s+)?noustu\b",
                r"\bei\s+ole\s+nous\b",
            ),
        ),
        (
            "payment_plan",
            (
                r"\bjarelmaks\w*\b",
                r"\bosamak\w*\b",
                r"\bmaksegraaf\w*\b",
                r"\bosade\s+kaupa\b",
                r"\b(?:maks\w*|tasu\w*)\b.{0,35}\bositi\b",
                r"\bositi\b.{0,35}\b(?:maks\w*|tasu\w*)\b",
            ),
        ),
        (
            "deadline",
            (
                r"\btahta\w*\b",
                r"\bmillal\b",
                r"\bkui\s+kaua\b",
            ),
        ),
        (
            "document_help",
            (
                r"\b(?:koosta|kirjuta|vormista)\w*\b.{0,45}\b(?:kaebus|vaie|avaldus|kiri|vastus|dokument)\w*\b",
                r"\babi\b.{0,35}\b(?:kaebuse|vaide|avalduse|kirja|vastuse|dokumendi)\b",
            ),
        ),
        (
            "find_authority",
            (
                r"\bkuhu\s+poord\w*\b",
                r"\bkellele\s+(?:esitada|saata)\w*\b",
                r"\bmis\s+asutus\w*\b",
            ),
        ),
        (
            "next_steps",
            (
                r"\bmida\s+(?:ma\s+)?peaksin\w*\b",
                r"\bmida\s+teha\b",
                r"\bkuidas\s+edasi\b",
            ),
        ),
        (
            "amount_or_claim",
            (
                r"\bkui\s+suur\b",
                r"\bsumma\b",
                r"\bnoue\w*\b",
            ),
        ),
    )

    _INTENT_REQUIREMENTS: Dict[str, str] = {
        "missed_deadline": "kas ja millistel tingimustel saab möödunud tähtaega ennistada",
        "challenge_decision": "milline on selle otsuse õige vaidlustamise viis",
        "payment_plan": "kas rahatrahvi saab tasuda ositi ja kellele taotlus esitada",
        "deadline": "milline tähtaeg kohaldub ja millest seda arvestatakse",
        "document_help": "milline dokument tuleb koostada ja mida see peab sisaldama",
        "find_authority": "millise asutuse või kohtu poole pöörduda",
        "next_steps": "millised on kasutaja järgmised praktilised sammud",
        "amount_or_claim": "kuidas tekstis nimetatud summat või nõuet käsitleda",
        "rights_explanation": "millised õigused ja võimalused kasutajal kirjeldatud olukorras on",
    }

    @classmethod
    def decide(
        cls,
        current_message: str,
        case_text: str,
        existing_help_types: Sequence[str] = (),
    ) -> TurnDecision:
        raw_context = str(case_text or "")
        context = cls._clean(raw_context)
        answered_planner_question = cls._planner_question_was_answered(
            raw_context,
            current_message=current_message,
        )
        current = cls._clean(current_message) or cls._latest_user_text(case_text)
        if answered_planner_question:
            # "Ma ei tea" answers the clarification, not the user's legal
            # question.  Carry the immediately preceding substantive request
            # into intent detection, retrieval and answer requirements.
            current = cls._request_before_latest_planner_question(raw_context) or current
        intents = cls.detect_intents(current)
        if not intents:
            intents = [
                value for value in existing_help_types
                if value in cls._INTENT_REQUIREMENTS
            ][:3]
        if not intents:
            intents = ["rights_explanation"]

        fine_context = cls.is_fine_context(context)
        exact_document = cls._exact_fine_document(context)
        has_receipt_date = cls._has_receipt_date(context)
        has_enforcement_status = cls._has_enforcement_status(context)
        amount_to_confirm = cls._large_fine_amount(current) if fine_context else ""
        missing: List[str] = []
        needs_procedure_context = fine_context and any(
            value in intents
            for value in ("missed_deadline", "challenge_decision", "payment_plan")
        )
        if needs_procedure_context and not exact_document:
            missing.append("dokumendi täpne pealkiri")
        if fine_context and "missed_deadline" in intents and not has_receipt_date:
            missing.append("dokumendi kättesaamise või kättesaadavaks tegemise kuupäev")
        if fine_context and any(
            value in intents for value in ("missed_deadline", "payment_plan")
        ) and not has_enforcement_status:
            missing.append("kas rahatrahv on juba kohtutäituri või muu täitja käes")
        if amount_to_confirm:
            missing.append(f"summa {amount_to_confirm} kinnitus")

        question = ""
        next_action = "analyze"
        reason = "Uus sõnum sisaldab piisavalt infot kontrollitud allikate otsimiseks."
        if missing and not answered_planner_question:
            next_action = "clarify"
            question = cls._build_clarification(
                missing,
                amount_to_confirm=amount_to_confirm,
            )
            reason = (
                "Puuduv asjaolu muudab vaidlustamise või tasumise menetlusteed, "
                "seega on enne otsingut vaja üht täpsustust."
            )

        requirements = tuple(
            cls._INTENT_REQUIREMENTS[value]
            for value in intents
            if value in cls._INTENT_REQUIREMENTS
        )
        search_query = cls._build_search_query(
            current=current,
            context=context,
            intents=intents,
            exact_document=exact_document,
            fine_context=fine_context,
        )
        return TurnDecision(
            current_intents=tuple(intents),
            next_action=next_action,
            decision_reason=reason,
            answer_requirements=requirements,
            missing_facts=tuple(missing),
            clarification_question=question,
            search_query=search_query,
            turn_summary=cls._turn_summary(intents),
            clarification_answered=answered_planner_question,
        )

    @classmethod
    def detect_intents(cls, text: str) -> List[str]:
        normalized = cls._normalize(text)
        intents: List[str] = []
        for name, patterns in cls._INTENT_PATTERNS:
            if any(re.search(pattern, normalized) for pattern in patterns):
                intents.append(name)

        # A missed appeal deadline is also a request to challenge the decision.
        if "missed_deadline" in intents and "challenge_decision" not in intents:
            intents.insert(1, "challenge_decision")
        # Keep the more precise missed-deadline intent instead of a duplicate
        # generic deadline badge.
        if "missed_deadline" in intents and "deadline" in intents:
            intents.remove("deadline")
        if not intents and "?" in str(text or ""):
            intents.append("rights_explanation")
        return cls._unique(intents)[:5]

    @staticmethod
    def is_fine_context(text: str) -> bool:
        normalized = ConversationTurnPlanner._normalize(text)
        return bool(re.search(
            r"\b(?:trahv\w*|rahatrahv\w*|vaarte\w*|kiirmenetl\w*|luhimenetl\w*)\b",
            normalized,
        ))

    @classmethod
    def _build_search_query(
        cls,
        *,
        current: str,
        context: str,
        intents: Sequence[str],
        exact_document: str,
        fine_context: bool,
    ) -> str:
        parts: List[str] = []
        normalized_context = cls._normalize(context)
        if "abipolitsei" in normalized_context:
            parts.append("abipolitseiniku pädevus")
        if fine_context:
            parts.append("rahatrahv väärteomenetlus")
        if exact_document:
            parts.append(exact_document)
        parts.append(current)
        if "missed_deadline" in intents:
            parts.append("kaebuse tähtaja ennistamise taotlus tähtaja möödumine")
        elif "challenge_decision" in intents:
            parts.append("otsuse vaidlustamine kaebus")
        if "payment_plan" in intents:
            parts.append("rahatrahvi tasumine ositi osamaksed täitmisele pööramine")
        return " ".join(cls._unique(parts))[:2000].strip()

    @staticmethod
    def _build_clarification(missing: Sequence[str], *, amount_to_confirm: str) -> str:
        parts: List[str] = []
        if "dokumendi täpne pealkiri" in missing:
            parts.append("mis on dokumendi täpne pealkiri")
        if any("kättesaamise" in value for value in missing):
            parts.append("millal said selle kätte")
        if any("kohtutäituri" in value for value in missing):
            parts.append("kas see on juba kohtutäituri või muu täitja käes")
        question = "Palun vaata dokumendilt, " + ", ".join(parts)
        if amount_to_confirm:
            question += f", ning kinnita, kas summa on tõesti {amount_to_confirm}"
        return question.rstrip(" ,.") + "?"

    @classmethod
    def _turn_summary(cls, intents: Sequence[str]) -> str:
        intent_set = set(intents)
        if {"missed_deadline", "payment_plan"}.issubset(intent_set):
            return (
                "Soovid teada, kas möödunud kaebetähtajast hoolimata saab otsust "
                "veel vaidlustada ja kas rahatrahvi saab tasuda osade kaupa."
            )
        labels = {
            "missed_deadline": "möödunud tähtaja võimalikku ennistamist",
            "challenge_decision": "otsuse vaidlustamist",
            "payment_plan": "osade kaupa tasumise võimalust",
            "deadline": "kohalduvat tähtaega",
            "document_help": "dokumendi koostamist",
            "find_authority": "õiget asutust",
            "next_steps": "järgmisi samme",
            "amount_or_claim": "nimetatud summat või nõuet",
            "rights_explanation": "oma õigusi ja võimalusi",
        }
        values = cls._unique(labels[value] for value in intents if value in labels)
        if not values:
            return "Soovid oma olukorra kohta kontrollitud selgitust."
        if len(values) == 1:
            return f"Soovid teada {values[0]}."
        return f"Soovid teada {', '.join(values[:-1])} ja {values[-1]}."

    @classmethod
    def _exact_fine_document(cls, text: str) -> str:
        normalized = cls._normalize(text)
        patterns = (
            (r"\bkiirmenetluse\s+otsus\w*\b", "kiirmenetluse otsus"),
            (r"\bluhimenetluse\s+otsus\w*\b", "lühimenetluse otsus"),
            (r"\buldmenetluses?\s+(?:tehtud\s+)?otsus\w*\b", "üldmenetluses tehtud otsus"),
            (r"\bhoiatustrahv\w*\b", "hoiatustrahvi trahviteade"),
        )
        for pattern, label in patterns:
            if re.search(pattern, normalized):
                return label
        return ""

    @classmethod
    def _has_receipt_date(cls, text: str) -> bool:
        normalized = cls._normalize(text)
        date = (
            r"(?:\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b|"
            r"\b\d{4}-\d{2}-\d{2}\b|"
            r"\b\d{1,2}\s+(?:jaanuar|veebruar|marts|aprill|mai|juuni|juuli|"
            r"august|september|oktoober|november|detsember)\w*\b)"
        )
        return bool(re.search(
            rf"\b(?:sain|katte|kattesaam\w*|toimetat\w*|saabus\w*)\b.{{0,70}}{date}|"
            rf"{date}.{{0,70}}\b(?:sain|katte|kattesaam\w*|toimetat\w*)\b",
            normalized,
        ))

    @classmethod
    def _has_enforcement_status(cls, text: str) -> bool:
        normalized = cls._normalize(text)
        return bool(re.search(
            r"\b(?:kohtutaitur\w*|taitemenetl\w*|taitmisele\s+poor\w*|"
            r"maksu\s+ja\s+tolliamet|\bmta\b|inkasso\w*)\b",
            normalized,
        ))

    @staticmethod
    def _large_fine_amount(text: str) -> str:
        for match in re.finditer(
            r"(?<!\d)(\d{1,7}(?:[.,]\d{1,2})?)\s*(?:€|eurot?|eur)\b",
            str(text or ""),
            flags=re.IGNORECASE,
        ):
            raw = match.group(1).replace(" ", "").replace(",", ".")
            try:
                value = float(raw)
            except ValueError:
                continue
            if value >= 1000:
                return match.group(0).strip()
        return ""

    @classmethod
    def _planner_question_was_answered(
        cls,
        text: str,
        *,
        current_message: str = "",
    ) -> bool:
        pattern = re.compile(
            r"ÕigusAI küsimus:\s*(.*?)\nKasutaja vastus:\s*(.*?)"
            r"(?=\nÕigusAI küsimus:|\Z)",
            flags=re.IGNORECASE | re.DOTALL,
        )
        matches = pattern.findall(text or "")
        if not matches:
            return False
        question, answer = matches[-1]
        normalized_question = cls._normalize(question)
        normalized_answer = cls._normalize(answer)
        normalized_current = cls._normalize(current_message)
        if normalized_current and normalized_answer != normalized_current:
            return False
        return bool(
            "dokumendi tapne pealkiri" in normalized_question
            and "kohtutaituri" in normalized_question
            and normalized_answer
        )

    @classmethod
    def _request_before_latest_planner_question(cls, text: str) -> str:
        question_matches = list(re.finditer(
            r"ÕigusAI küsimus:\s*.*?(?=\nKasutaja vastus:)",
            str(text or ""),
            flags=re.IGNORECASE | re.DOTALL,
        ))
        if not question_matches:
            return ""
        prefix = str(text or "")[:question_matches[-1].start()]
        prior_answers = re.findall(
            r"Kasutaja vastus:\s*(.*?)"
            r"(?=\nÕigusAI küsimus:|\Z)",
            prefix,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if prior_answers:
            return cls._clean(prior_answers[-1])
        initial = re.split(
            r"\nKasutaja täpsustused:\s*",
            prefix,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        return cls._clean(initial)

    @staticmethod
    def _latest_user_text(text: str) -> str:
        matches = re.findall(
            r"Kasutaja vastus:\s*(.*?)"
            r"(?=\nÕigusAI küsimus:|\Z)",
            str(text or ""),
            flags=re.IGNORECASE | re.DOTALL,
        )
        if matches:
            return ConversationTurnPlanner._clean(matches[-1])
        return ConversationTurnPlanner._clean(text)

    @staticmethod
    def _clean(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @staticmethod
    def _normalize(value: str) -> str:
        text = str(value or "").casefold().translate(
            str.maketrans({"ä": "a", "ö": "o", "ü": "u", "õ": "o", "š": "s", "ž": "z"})
        )
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z0-9]+", " ", text).strip()

    @staticmethod
    def _unique(values) -> List[str]:
        seen = set()
        result: List[str] = []
        for value in values:
            value = str(value or "").strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                result.append(value)
        return result
