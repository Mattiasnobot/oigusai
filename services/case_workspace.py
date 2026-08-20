"""V8.2 case-card and urgency helpers.

The workspace never invents legal facts.  Every structured fact is copied from
the intake result or from an exact user/document excerpt.  Deadline candidates
are dates that were observed in the supplied material, not calculated legal
deadlines.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Dict, Iterable, List, Optional


DATE_PATTERNS = (
    re.compile(r"\b(?P<day>0?[1-9]|[12]\d|3[01])[.](?P<month>0?[1-9]|1[0-2])[.](?P<year>20\d{2})\b"),
    re.compile(r"\b(?P<year>20\d{2})-(?P<month>0[1-9]|1[0-2])-(?P<day>0[1-9]|[12]\d|3[01])\b"),
)

URGENT_TERMS = {
    "tähtaeg": "Kirjelduses mainiti tähtaega.",
    "kohtukutse": "Kirjelduses mainiti kohtukutset.",
    "arest": "Kirjelduses mainiti aresti või kinnipidamist.",
    "väljatõst": "Kirjelduses mainiti võimalikku väljatõstmist.",
    "valland": "Kirjelduses mainiti töösuhte kiiret lõppemist.",
    "vaidlusta": "Kasutaja soovib otsust vaidlustada.",
}

IMMEDIATE_SAFETY_TERMS = {
    "ähvard": "Kirjelduses võib olla vahetu turvarisk.",
    "vägivald": "Kirjelduses võib olla vahetu turvarisk.",
    "peks": "Kirjelduses võib olla vahetu turvarisk.",
    "tapab": "Kirjelduses võib olla vahetu turvarisk.",
}


def _clean(value: object, limit: int = 2000) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _unique_strings(values: Iterable[object], limit: int = 20) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        clean = _clean(value, 600)
        key = clean.casefold()
        if clean and key not in seen:
            result.append(clean)
            seen.add(key)
        if len(result) >= limit:
            break
    return result


def _validated_items(values: object, fields: tuple[str, ...], limit: int) -> List[Dict]:
    if not isinstance(values, list):
        return []
    result: List[Dict] = []
    seen = set()
    for raw in values:
        if not isinstance(raw, dict):
            continue
        item = {field: _clean(raw.get(field), 600) for field in fields}
        if not any(item.values()):
            continue
        key = tuple(item.values())
        if key in seen:
            continue
        result.append(item)
        seen.add(key)
        if len(result) >= limit:
            break
    return result


class CaseCardBuilder:
    """Build and safely revise a user-editable card from intake facts."""

    EDITABLE_FIELDS = {
        "title",
        "summary",
        "user_goal",
        "parties",
        "events",
        "amounts",
        "documents",
        "missing_facts",
    }

    def from_intake(self, intake: Dict, previous: Optional[Dict] = None) -> Dict:
        previous = deepcopy(previous or {})
        revision = max(0, int(previous.get("revision") or 0)) + 1
        topic = _clean(intake.get("topic"), 120)
        user_confirmed = previous.get("source") == "user_confirmed"
        card = {
            "revision": revision,
            "title": topic or _clean(previous.get("title"), 120) or "Uus juhtum",
            "summary": _clean(
                previous.get("summary") if user_confirmed else intake.get("summary"),
                2400,
            ),
            "user_goal": _clean(
                previous.get("user_goal") if user_confirmed else intake.get("user_goal"),
                1200,
            ),
            "help_types": _unique_strings(intake.get("help_types") or [], 12),
            "parties": _validated_items(
                previous.get("parties") if user_confirmed else intake.get("parties"),
                ("role", "label", "evidence"), 20
            ),
            "events": _validated_items(
                previous.get("events") if user_confirmed else intake.get("events"),
                ("date", "actor", "action", "evidence"), 40
            ),
            "amounts": _validated_items(
                previous.get("amounts") if user_confirmed else intake.get("amounts"),
                ("label", "value", "evidence"), 20
            ),
            "documents": _validated_items(
                previous.get("documents") if user_confirmed else intake.get("documents"),
                ("name", "evidence"), 20
            ),
            "missing_facts": _unique_strings(intake.get("missing_facts") or [], 20),
            "source": "user_confirmed" if user_confirmed else "user_intake",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        return card

    def revise(self, current: Dict, changes: Dict, expected_revision: int) -> Dict:
        current = deepcopy(current or {})
        actual_revision = int(current.get("revision") or 0)
        if int(expected_revision) != actual_revision:
            raise ValueError(
                "Juhtumikaart on vahepeal muutunud. Ava kaart uuesti ja proovi siis parandada."
            )
        unknown = set(changes).difference(self.EDITABLE_FIELDS)
        if unknown:
            raise ValueError("Juhtumikaardi tundmatuid välju ei salvestatud.")

        if "title" in changes:
            current["title"] = _clean(changes["title"], 120) or "Uus juhtum"
        if "summary" in changes:
            current["summary"] = _clean(changes["summary"], 2400)
        if "user_goal" in changes:
            current["user_goal"] = _clean(changes["user_goal"], 1200)
        if "parties" in changes:
            current["parties"] = _validated_items(
                changes["parties"], ("role", "label", "evidence"), 20
            )
        if "events" in changes:
            current["events"] = _validated_items(
                changes["events"], ("date", "actor", "action", "evidence"), 40
            )
        if "amounts" in changes:
            current["amounts"] = _validated_items(
                changes["amounts"], ("label", "value", "evidence"), 20
            )
        if "documents" in changes:
            current["documents"] = _validated_items(
                changes["documents"], ("name", "evidence"), 20
            )
        if "missing_facts" in changes:
            current["missing_facts"] = _unique_strings(changes["missing_facts"], 20)

        current["revision"] = actual_revision + 1
        current["source"] = "user_confirmed"
        current["updated_at"] = datetime.now(timezone.utc).isoformat()
        return current


class UrgencyAnalyzer:
    """Detect urgency signals without asserting a legal deadline."""

    def analyze(
        self,
        text: str,
        *,
        event_date: str = "",
        document_spans: Optional[Iterable[Dict]] = None,
        today: Optional[date] = None,
    ) -> Dict:
        today = today or date.today()
        materials = [{"text": str(text or ""), "source": "user", "source_id": "USER"}]
        if event_date:
            materials.append({"text": event_date, "source": "user_date", "source_id": "EVENT_DATE"})
        for span in list(document_spans or [])[:20]:
            materials.append({
                "text": str(span.get("text") or ""),
                "source": "document",
                "source_id": str(span.get("span_id") or ""),
                "page": span.get("page"),
            })

        joined = "\n".join(item["text"] for item in materials).casefold()
        reasons = []
        level = "normal"
        for term, reason in URGENT_TERMS.items():
            if term in joined:
                reasons.append(reason)
                level = "attention"
        for term, reason in IMMEDIATE_SAFETY_TERMS.items():
            if term in joined:
                reasons.append(reason)
                level = "immediate"

        if "tähtaeg" in joined and any(word in joined for word in ("möödas", "ület", "hiljaks")):
            reasons.append("Kasutaja kirjeldab võimalikku möödunud tähtaega.")
            level = "urgent" if level != "immediate" else level

        dates = []
        seen = set()
        for material in materials:
            for parsed in self._dates(material["text"]):
                key = (parsed.isoformat(), material["source_id"])
                if key in seen:
                    continue
                seen.add(key)
                days = (parsed - today).days
                dates.append({
                    "date": parsed.isoformat(),
                    "days_from_today": days,
                    "source": material["source"],
                    "source_id": material["source_id"],
                    "page": material.get("page"),
                    "status": "observed_not_legal_deadline",
                })
                if 0 <= days <= 7 and "tähtaeg" in joined and level == "normal":
                    level = "attention"
                    reasons.append("Kirjelduses on järgmise seitsme päeva kuupäev ja tähtaja märksõna.")

        questions = []
        if any(term in joined for term in ("vaidlusta", "kaeb", "tähtaeg", "otsus")):
            if not any(term in joined for term in ("kätte sain", "kätte saanud", "kättetoimet")):
                questions.append("Millal said otsuse või dokumendi kätte?")
            questions.append("Mis on dokumendi täpne pealkiri ja sellel märgitud vaidlustamisjuhis?")

        return {
            "level": level,
            "reasons": _unique_strings(reasons, 10),
            "observed_dates": dates[:20],
            "questions": _unique_strings(questions, 6),
            "legal_deadline_confirmed": False,
            "notice": (
                "Leitud kuupäevad pärinevad kasutaja tekstist või dokumendist. "
                "Neid ei käsitleta automaatselt õigusliku tähtajana."
            ),
        }

    @staticmethod
    def _dates(text: str) -> List[date]:
        result = []
        for pattern in DATE_PATTERNS:
            for match in pattern.finditer(str(text or "")):
                try:
                    result.append(date(
                        int(match.group("year")),
                        int(match.group("month")),
                        int(match.group("day")),
                    ))
                except ValueError:
                    continue
        return result
