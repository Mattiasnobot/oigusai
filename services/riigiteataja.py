"""
ÕigusAI - Riigi Teataja integratsioon (Variant B)

- Laeb päris seaduste tekstid riigiteataja.ee XML-kujul.
- Puhverdab XML-i lokaalselt (cache/rt/), et mitte koormata RT serverit
  ja et rakendus oleks kiire.
- Jagab akti paragrahvide kaupa kirjeteks, mida legal_search.py tunneb:
  {"id": "VOS_123", "title": ..., "text": ..., "source": ...}
"""
import json
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

import requests

from config import Settings, load_settings

logger = logging.getLogger(__name__)

class RiigiTeatajaService:
    def __init__(
        self,
        cache_ttl_days: int = None,
        timeout: int = None,
        settings: Settings = None,
    ):
        cfg = settings or load_settings()
        ttl_days = cfg.rt_cache_ttl_days if cache_ttl_days is None else cache_ttl_days
        self.cache_ttl = timedelta(days=ttl_days)
        self.timeout = cfg.rt_timeout if timeout is None else timeout
        self.cache_dir = cfg.rt_service_cache_dir
        self.registry_file = cfg.rt_registry_file
        self.user_agent = cfg.rt_user_agent
        self.request_delay = cfg.rt_request_delay
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Register
    # ------------------------------------------------------------------
    def load_registry(self) -> List[Dict]:
        """Loeb data/rt_registry.json – milliseid seadusi toetame."""
        if not self.registry_file.exists():
            logger.warning("data/rt_registry.json puudub – RT andmeid ei laeta.")
            return []

        try:
            raw = json.loads(self.registry_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("rt_registry.json on vigane: %s", exc)
            return []

        acts = []
        for item in raw:
            url = str(item.get("url", "")).strip()
            # Jäta vahele kirjed, mille URL on veel asendamata
            if not url or "..." in url or "asenda" in url.lower():
                logger.warning("Akt %s: URL puudub/asendamata, jätan vahele.", item.get("prefix"))
                continue
            acts.append(item)
        return acts

    # ------------------------------------------------------------------
    # Allalaadimine + puhver
    # ------------------------------------------------------------------
    def _fetch_xml(self, act: Dict) -> str:
        cache_file = self.cache_dir / f"{act['prefix']}.xml"

        # Värske puhver -> ei lähe üldse veebi
        if cache_file.exists():
            age = datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)
            if age < self.cache_ttl:
                return cache_file.read_text(encoding="utf-8")

        url = act["url"]
        candidates = [url] if url.endswith(".xml") else [url + ".xml", url]

        last_error = None
        for candidate in candidates:
            try:
                resp = requests.get(
                    candidate,
                    headers={"User-Agent": self.user_agent},
                    timeout=self.timeout,
                )
                if resp.status_code == 200 and resp.text.lstrip().startswith("<"):
                    cache_file.write_text(resp.text, encoding="utf-8")
                    return resp.text
                last_error = f"{candidate} -> HTTP {resp.status_code}"
            except requests.RequestException as exc:
                last_error = f"{candidate} -> {exc}"
            time.sleep(self.request_delay)  # viisakas paus

        raise RuntimeError(f"RT XML laadimine ebaõnnestus ({act['prefix']}): {last_error}")

    # ------------------------------------------------------------------
    # Parsimine
    # ------------------------------------------------------------------
    @staticmethod
    def _tag(elem) -> str:
        return elem.tag.split("}")[-1] if isinstance(elem.tag, str) else ""

    def parse_act(self, xml_text: str, act: Dict) -> List[Dict]:
        """Jagab akti §-kaupa seaduse-kirjeteks."""
        root = ET.fromstring(xml_text.encode("utf-8"))
        laws = []

        for elem in root.iter():
            if self._tag(elem) != "paragrahv":
                continue

            number = ""
            for child in elem.iter():
                if self._tag(child) == "number":
                    number = "".join(c for c in (child.text or "") if c.isdigit())
                    break
            if not number:
                continue

            text = " ".join("".join(elem.itertext()).split())
            text = text.replace(f"§ {number}", "", 1).strip()

            laws.append({
                "id": f"{act['prefix']}_{number}",
                "title": f"{act['name']} § {number}",
                "text": text[:3000],
                "source": f"Riigi Teataja: {act['name']} • {act['url']}",
            })

        if not laws:
            logger.warning("Akt %s: XML-is ei leitud ühtegi paragrahvi.", act["prefix"])

        return laws

    def load_laws(self) -> List[Dict]:
        """Laeb kõik registris olevad seadused (puhvrist või veebist)."""
        laws = []
        for act in self.load_registry():
            try:
                laws.extend(self.parse_act(self._fetch_xml(act), act))
            except Exception as exc:
                logger.error("Akti %s laadimine ebaõnnestus: %s", act.get("prefix"), exc)
        return laws