from __future__ import annotations

ACT_ID = "106032026003"
TITLE = "Avaliku teabe seadus"


def rt_xml(
    act_id: str = ACT_ID,
    *,
    title: str = TITLE,
    act_type: str = "seadus",
    rt_part: str = "I",
    valid_from: str = "2026-03-16+02:00",
    valid_to: str = "2026-09-30+03:00",
    section_number: str = "95",
    section_text: str = "Ülesütlemisavaldus peab olema kirjalikku taasesitamist võimaldavas vormis.",
) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<akt>
  <metaandmed>
    <globaalID>{act_id}</globaalID>
    <aktinimi><nimi><pealkiri>{title}</pealkiri></nimi></aktinimi>
    <valjaandja>Riigikogu</valjaandja>
    <dokumentLiik>{act_type}</dokumentLiik>
    <tekstiliik>terviktekst</tekstiliik>
    <kehtivus><kehtivuseAlgus>{valid_from}</kehtivuseAlgus><kehtivuseLopp>{valid_to}</kehtivuseLopp></kehtivus>
    <avaldamismarge><RTosa>{rt_part}</RTosa><avaldamineKuupaev>2026-03-06</avaldamineKuupaev><RTartikkel>3</RTartikkel></avaldamismarge>
  </metaandmed>
  <sisu>
    <paragrahv id="para{section_number.casefold()}">
      <paragrahvNr>§ {section_number}</paragrahvNr>
      <paragrahviPealkiri>Kontrollsäte</paragrahviPealkiri>
      <loige><sisuTekst>{section_text}</sisuTekst></loige>
    </paragrahv>
    <paragrahv id="para16"><paragrahvNr>§ 16</paragrahvNr><loige><sisuTekst>Teine piisavalt pikk kontrollsäte ametliku XML kontrollimiseks.</sisuTekst></loige></paragrahv>
  </sisu>
</akt>""".encode("utf-8")


def search_payload(*ids: str) -> bytes:
    return ("<tulemused>" + "".join(f"<akt><globaalID>{value}</globaalID></akt>" for value in ids) + "</tulemused>").encode("utf-8")


def search_fetcher(payload: bytes, *, final_url_override: str | None = None):
    def fetch(url, timeout, user_agent):
        return payload, final_url_override or url
    return fetch


def xml_fetcher(factory=None):
    def fetch(url, timeout, user_agent):
        act_id = url.rstrip("/").split("/")[-2]
        data = factory(act_id) if factory else rt_xml(act_id)
        return data, url
    return fetch
