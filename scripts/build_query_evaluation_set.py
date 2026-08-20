#!/usr/bin/env python3
"""Build the audited ÕigusAI 200-query retrieval evaluation set.

Every positive label points to a section that exists in the local trusted
Riigi Teataja corpus. The builder validates IDs, domains, duplicates and the
fixed development/holdout/challenge split before writing JSON artifacts.
"""
from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = PROJECT_ROOT / "eval"
LAWS_FILE = PROJECT_ROOT / "data" / "laws.json"


# Six independently worded, section-labelled cases for each core domain.
# The first four cases per domain are development data; the last two are holdout.
CORE_CASES = {
    "TLS": [
        ("Kas tööleping peab olema kirjalik?", ["TLS_4"], ["title", "contract_form"], "easy"),
        ("Kui pikk võib töötaja katseaeg olla?", ["TLS_10B1"], ["natural", "probation"], "easy"),
        ("Kui sageli peab tööandja töötasu maksma?", ["TLS_33"], ["natural", "wages"], "medium"),
        ("Kas täistööaeg tähendab tavaliselt 40 tundi seitsme päeva jooksul?", ["TLS_43"], ["natural", "working_time"], "medium"),
        ("Mitu tundi järjest peab kahe tööpäeva vahel puhkamiseks jääma?", ["TLS_51"], ["natural", "rest_time"], "medium"),
        ("Tööandja ütles, et töömahu vähenemise tõttu mind koondatakse. Millised reeglid kehtivad?", ["TLS_89"], ["scenario", "redundancy"], "hard"),
    ],
    "VOS": [
        ("Kui suurt tagatisraha võib eluruumi üürileandja küsida?", ["VOS_308"], ["natural", "deposit"], "easy"),
        ("Kui palju varem peab üürileandja üüri tõstmisest teatama?", ["VOS_299"], ["natural", "rent_increase"], "medium"),
        ("Millal võib üürileandja maksmata üüri tõttu lepingu erakorraliselt lõpetada?", ["VOS_316"], ["scenario", "lease_termination"], "hard"),
        ("Millal loetakse ostetud asi lepingutingimustele mittevastavaks?", ["VOS_217"], ["title", "defective_goods"], "medium"),
        ("Kas internetist ostetud kauba võib 14 päeva jooksul põhjust ütlemata tagastada?", ["VOS_56"], ["natural", "distance_contract"], "medium"),
        ("Millises ulatuses peab lepingut rikkunud pool tekitatud kahju hüvitama?", ["VOS_127"], ["natural", "damages"], "hard"),
    ],
    "KARS": [
        ("Kas enda kaitsmiseks ründajale kahju tekitamine võib olla hädakaitse?", ["KARS_28"], ["scenario", "self_defence"], "medium"),
        ("Millal on sõnumiga tapmise ähvardamine kuritegu?", ["KARS_120"], ["natural", "threat"], "medium"),
        ("Kas teisele inimesele valu tekitav löömine on kehaline väärkohtlemine?", ["KARS_121"], ["natural", "assault"], "easy"),
        ("Mida käsitatakse vargusena?", ["KARS_199"], ["title", "theft"], "easy"),
        ("Millal on vale ettekujutuse loomisega raha saamine kelmus?", ["KARS_209"], ["natural", "fraud"], "medium"),
        ("Kui vana peab inimene olema, et olla karistusõiguslikult süüvõimeline?", ["KARS_33"], ["natural", "criminal_capacity"], "medium"),
    ],
    "PKS": [
        ("Millal võib kohus abielu lahutada pöördumatult lõppenud abielusuhete tõttu?", ["PKS_67"], ["natural", "divorce"], "medium"),
        ("Kuidas peab lahus elav vanem tavaliselt lapsele elatist andma?", ["PKS_100"], ["scenario", "maintenance"], "medium"),
        ("Mida hõlmab vanema hooldusõigus?", ["PKS_116"], ["title", "custody"], "easy"),
        ("Kas lapsel on õigus suhelda mõlema vanemaga?", ["PKS_143"], ["natural", "contact_right"], "easy"),
        ("Millistel üldtingimustel on lapse lapsendamine lubatud?", ["PKS_147"], ["natural", "adoption"], "medium"),
        ("Kuidas jagatakse abikaasade ühisvara varaühisuse lõppemisel?", ["PKS_37"], ["natural", "marital_property"], "hard"),
    ],
    "PS": [
        ("Millised nõuded kehtivad põhiõiguste piiramisele?", ["PS_11"], ["title", "fundamental_rights"], "medium"),
        ("Kas kõiki tuleb seaduse ees võrdselt kohelda?", ["PS_12"], ["natural", "equality"], "easy"),
        ("Kas õiguste rikkumise korral on õigus kohtusse pöörduda?", ["PS_15"], ["natural", "court_access"], "easy"),
        ("Millal võib riigiasutus inimese eraellu sekkuda?", ["PS_26"], ["scenario", "privacy"], "medium"),
        ("Kas riik võib omaniku nõusolekuta tema vara võõrandada?", ["PS_32"], ["scenario", "property"], "hard"),
        ("Kas Eestis on tsensuur lubatud ja kuidas võib sõnavabadust piirata?", ["PS_45"], ["natural", "expression"], "medium"),
    ],
    "HMS": [
        ("Kuidas võib haldusorgan otsuse inimesele kätte toimetada?", ["HMS_25"], ["natural", "service"], "medium"),
        ("Kas ametiasutus peab enne minu õigusi kahjustavat otsust mind ära kuulama?", ["HMS_40"], ["scenario", "hearing"], "medium"),
        ("Mida peab kirjaliku haldusakti põhjendus sisaldama?", ["HMS_56"], ["title", "reasoning"], "easy"),
        ("Kellel on õigus haldusakti või toimingu peale vaie esitada?", ["HMS_71"], ["natural", "administrative_challenge"], "medium"),
        ("Kui pikk on tavaliselt vaide esitamise tähtaeg?", ["HMS_75"], ["natural", "challenge_deadline"], "easy"),
        ("Mida peab haldusorgan arvestama haldusakti kehtetuks tunnistamisel?", ["HMS_64"], ["natural", "revocation"], "hard"),
    ],
    "HKMS": [
        ("Milliseid nõudeid saab halduskohtule esitatavas kaebuses esitada?", ["HKMS_37"], ["title", "complaint_types"], "medium"),
        ("Kas halduskohtusse võib minna ainult oma õiguse kaitseks?", ["HKMS_44"], ["natural", "standing"], "medium"),
        ("Kui kaua on aega haldusakti tühistamiseks kohtusse kaevata?", ["HKMS_46"], ["natural", "complaint_deadline"], "easy"),
        ("Millal saab halduskohtult taotleda esialgset õiguskaitset?", ["HKMS_249"], ["scenario", "interim_relief"], "hard"),
        ("Kui pikk on haldusasjas apellatsioonkaebuse esitamise tähtaeg?", ["HKMS_181"], ["natural", "appeal_deadline"], "medium"),
        ("Kui pikk on haldusasjas kassatsioonkaebuse tähtaeg?", ["HKMS_212"], ["natural", "cassation_deadline"], "medium"),
    ],
    "TSMS": [
        ("Mida peab tsiviilasja hagiavaldus sisaldama?", ["TSMS_363"], ["title", "statement_of_claim"], "easy"),
        ("Kes peab tsiviilkohtus oma väiteid tõendama?", ["TSMS_230"], ["natural", "burden_of_proof"], "medium"),
        ("Kas kohus võib teha tagaseljaotsuse, kui kostja hagile ei vasta?", ["TSMS_407"], ["scenario", "default_judgment"], "medium"),
        ("Kui kaua on aega maakohtu otsuse peale apellatsiooni esitada?", ["TSMS_632"], ["natural", "appeal_deadline"], "easy"),
        ("Kuidas jagatakse kohtukulud, kui hagi rahuldatakse ainult osaliselt?", ["TSMS_163"], ["scenario", "costs"], "hard"),
        ("Kuidas kontrollitakse, millisele kohtule tsiviilasi allub?", ["TSMS_75"], ["natural", "jurisdiction"], "medium"),
    ],
    "VTMS": [
        ("Millised õigused on väärteomenetluses menetlusalusel isikul?", ["VTMS_19"], ["title", "procedural_rights"], "easy"),
        ("Millistel alustel ja kui kauaks võib väärteos kahtlustatava kinni pidada?", ["VTMS_44"], ["natural", "detention"], "medium"),
        ("Millal võib kohtuväline menetleja kasutada kiirmenetlust?", ["VTMS_55"], ["natural", "expedited_procedure"], "medium"),
        ("Mida peab väärteoprotokoll sisaldama?", ["VTMS_69"], ["title", "protocol"], "easy"),
        ("Millal ja mis tähtaja jooksul saab kohtuvälise menetleja otsuse maakohtusse kaevata?", ["VTMS_114"], ["scenario", "court_challenge"], "hard"),
        ("Kuidas saab lühimenetluse otsust vaidlustada?", ["VTMS_54B11"], ["natural", "short_procedure"], "medium"),
    ],
    "LS": [
        ("Kuhu tohib asulas auto parkida?", ["LS_20"], ["natural", "parking"], "medium"),
        ("Millal peab autos turvavöö kinnitatud olema?", ["LS_30"], ["natural", "seatbelt"], "easy"),
        ("Mida peab juht sõidukiiruse valimisel arvestama?", ["LS_50"], ["title", "speed"], "medium"),
        ("Millal loetakse mootorsõidukijuht alkoholijoobes olevaks?", ["LS_69"], ["natural", "alcohol"], "hard"),
        ("Mida peab juht pärast liiklusõnnetust tegema?", ["LS_169"], ["scenario", "traffic_accident"], "medium"),
        ("Kas inimene võib juhtida autot ilma vastava kategooria juhtimisõiguseta?", ["LS_90"], ["natural", "driving_right"], "easy"),
    ],
    "KORS": [
        ("Kuidas peab korrakaitseorgan valima kõige vähem kahjustava meetme?", ["KORS_7"], ["natural", "proportionality"], "medium"),
        ("Millal võib politsei inimese peatada ja teda küsitleda?", ["KORS_30"], ["scenario", "questioning"], "medium"),
        ("Millal võib politsei inimese isikusamasuse tuvastada?", ["KORS_32"], ["natural", "identity_check"], "medium"),
        ("Millistel alustel võib politsei inimese kinni pidada?", ["KORS_46"], ["scenario", "detention"], "hard"),
        ("Millal võib politsei omaniku nõusolekuta eluruumi siseneda?", ["KORS_50"], ["scenario", "entry"], "hard"),
        ("Mida tähendab korrakaitseseaduses vahetu sund?", ["KORS_74"], ["title", "direct_coercion"], "easy"),
    ],
    "ABIPOLS": [
        ("Milline on abipolitseiniku pädevus avaliku korra kaitsmisel?", ["ABIPOLS_3"], ["title", "competence"], "easy"),
        ("Millistele nõuetele peab abipolitseinikuks soovija vastama?", ["ABIPOLS_4"], ["natural", "eligibility"], "medium"),
        ("Milliseid korrakaitseseaduse meetmeid võib abipolitseinik rakendada?", ["ABIPOLS_16"], ["natural", "measures"], "hard"),
        ("Millal võib abipolitseinik kasutada füüsilist jõudu või erivahendit?", ["ABIPOLS_28"], ["scenario", "direct_coercion"], "hard"),
        ("Kas abipolitseinik võib kasutada tulirelva või elektrišokirelva?", ["ABIPOLS_35"], ["natural", "firearm"], "hard"),
        ("Kas abipolitseinik võib politsei ülesandel iseseisvalt liiklusjärelevalvet teha?", ["ABIPOLS_3"], ["scenario", "traffic_supervision"], "medium"),
    ],
    "IKS": [
        ("Millistel tingimustel võib isikuandmeid teadusuuringuks nõusolekuta töödelda?", ["IKS_6"], ["natural", "research"], "hard"),
        ("Kui vana laps võib ise nõustuda oma andmete töötlemisega veebiteenuses?", ["IKS_8"], ["scenario", "child_consent"], "medium"),
        ("Millal kohaldatakse õiguskaitseasutuse isikuandmete töötlemise erireegleid?", ["IKS_12"], ["title", "law_enforcement_data"], "hard"),
        ("Millal võib õiguskaitseasutus töödelda eriliiki isikuandmeid?", ["IKS_20"], ["natural", "special_categories"], "hard"),
        ("Millal peab inimest tema isikuandmetega seotud rikkumisest teavitama?", ["IKS_45"], ["scenario", "data_breach"], "medium"),
        ("Milline asutus teeb isikuandmete töötlemise üle riiklikku järelevalvet?", ["IKS_56"], ["natural", "supervision"], "easy"),
    ],
    "AVTS": [
        ("Millised asutused ja isikud on avaliku teabe seaduse järgi teabevaldajad?", ["AVTS_5"], ["title", "information_holder"], "medium"),
        ("Kui kiiresti peab ametiasutus teabenõude täitma?", ["AVTS_18"], ["natural", "request_deadline"], "easy"),
        ("Millal võib teabevaldaja teabenõude täitmisest keelduda?", ["AVTS_23"], ["natural", "refusal"], "medium"),
        ("Millistel alustel tunnistatakse teave asutusesiseseks kasutamiseks?", ["AVTS_35"], ["title", "access_restriction"], "hard"),
        ("Kas piiranguga dokumendist peab avalikustama selle osa, millele piirang ei kehti?", ["AVTS_38"], ["scenario", "partial_access"], "hard"),
        ("Millist asutuse valduses olevat teavet peab veebis avalikustama?", ["AVTS_28"], ["natural", "proactive_disclosure"], "medium"),
    ],
    "TKS": [
        ("Millist teavet peab kaupleja tarbijale enne ostu andma?", ["TKS_4"], ["natural", "consumer_information"], "medium"),
        ("Millal loetakse kaup tarbijakaitseseaduse järgi puudusega kaubaks?", ["TKS_12"], ["title", "defective_goods"], "easy"),
        ("Millise ametiasutuse poole saab tarbija õiguste rikkumisega pöörduda?", ["TKS_21"], ["natural", "consumer_authority"], "easy"),
        ("Milliseid vaidlusi lahendab tarbijavaidluste komisjon?", ["TKS_40"], ["natural", "consumer_dispute"], "medium"),
        ("Mida peab sisaldama tarbijavaidluste komisjonile esitatav avaldus?", ["TKS_46"], ["scenario", "consumer_application"], "hard"),
        ("Mida tähendab kauplemisvõte ja milliseid õigusi annab ebaaus võte tarbijale?", ["TKS_13"], ["natural", "unfair_practice"], "hard"),
    ],
    "KRTS": [
        ("Mitu häält annab üks korteriomand korteriühistu üldkoosolekul?", ["KRTS_22"], ["natural", "voting"], "easy"),
        ("Milliseid tegevusi loetakse korteriomandi tavapäraseks valitsemiseks?", ["KRTS_35"], ["title", "ordinary_management"], "medium"),
        ("Kuidas jagatakse korteriühistu majandamiskulud korteriomanike vahel?", ["KRTS_40"], ["natural", "management_costs"], "medium"),
        ("Millal võivad korteriomanikud nõuda probleemse omaniku korteri võõrandamist?", ["KRTS_32"], ["scenario", "forced_sale"], "hard"),
        ("Millised õigused on korteriomanikul eriomandi ja kaasomandi kasutamisel?", ["KRTS_30"], ["natural", "owner_rights"], "medium"),
        ("Kas korteriomanikud peavad omavahelistes suhetes arvestama üksteise õigustatud huve?", ["KRTS_12"], ["natural", "good_faith"], "easy"),
    ],
    "AOS": [
        ("Mida tähendab omavoliline valdus?", ["AOS_40"], ["title", "possession"], "easy"),
        ("Mida saab valdaja nõuda, kui tema valdust rikutakse?", ["AOS_44"], ["natural", "possession_protection"], "medium"),
        ("Kas omanik saab nõuda tema omandiõiguse rikkumise lõpetamist?", ["AOS_89"], ["natural", "ownership_protection"], "medium"),
        ("Kas teeta kinnistu omanik võib nõuda juurdepääsu üle naabri maa?", ["AOS_156"], ["scenario", "necessary_access"], "hard"),
        ("Kelle kanda jäävad kahe kinnistu ühise piirirajatise korrashoiukulud?", ["AOS_151"], ["scenario", "boundary_structure"], "medium"),
        ("Mida tähendab reaalservituut?", ["AOS_172"], ["title", "servitude"], "easy"),
    ],
    "TMS": [
        ("Millal loetakse täitemenetlus võlgniku suhtes alanuks?", ["TMS_24"], ["natural", "enforcement_notice"], "medium"),
        ("Millal võib kohtutäitur lõpetada täitemenetluse nõude aegumise tõttu?", ["TMS_48B1"], ["scenario", "limitation"], "hard"),
        ("Kui suur osa inimese kuusissetulekust peab üldjuhul arestimata jääma?", ["TMS_132"], ["natural", "protected_income"], "medium"),
        ("Kui kiiresti peab kohtutäitur vabastama pangakontol aresti alt kaitstud sissetuleku?", ["TMS_133"], ["scenario", "account_release"], "hard"),
        ("Kuidas saab kohtutäituri kaebuse kohta tehtud otsuse maakohtusse edasi kaevata?", ["TMS_218"], ["natural", "bailiff_challenge"], "hard"),
        ("Kuidas müüb kohtutäitur arestitud vallasasju avalikul enampakkumisel?", ["TMS_78"], ["natural", "auction"], "medium"),
    ],
    "MKS": [
        ("Millist maksukohustuslase teavet peab maksuhaldur saladuses hoidma?", ["MKS_26"], ["natural", "tax_secrecy"], "medium"),
        ("Mida peab maksuhalduri vastutusotsus sisaldama?", ["MKS_96"], ["title", "liability_decision"], "hard"),
        ("Millistel juhtudel maksusumma määramise aegumine peatub?", ["MKS_99"], ["natural", "limitation"], "hard"),
        ("Kui suur on maksuvõlalt arvestatav päevane intressimäär?", ["MKS_117"], ["natural", "interest"], "medium"),
        ("Kas maksuhalduri otsuse või tegevusetuse peale saab vaide esitada?", ["MKS_137"], ["scenario", "tax_challenge"], "medium"),
        ("Mis on maksukontrolli eesmärk?", ["MKS_55B1"], ["title", "tax_audit"], "easy"),
    ],
    "ATS": [
        ("Kui pikk võib ametniku katseaeg olla?", ["ATS_24"], ["natural", "probation"], "easy"),
        ("Millised on ametniku üldised teenistuskohustused?", ["ATS_51"], ["title", "official_duties"], "medium"),
        ("Millistest osadest koosneb ametniku palk?", ["ATS_61"], ["natural", "salary"], "medium"),
        ("Mida loetakse ametniku distsiplinaarsüüteoks?", ["ATS_69"], ["title", "discipline"], "easy"),
        ("Kui palju peab ametnik omal soovil teenistusest lahkumisest ette teatama?", ["ATS_87"], ["natural", "resignation"], "medium"),
        ("Millal võib ametniku teenistusest koondamise tõttu vabastada?", ["ATS_90"], ["scenario", "redundancy"], "hard"),
    ],
}


# Two independent phrasings for each multi-domain scenario. The first is
# development data and the second is frozen holdout data.
CROSS_CASES = [
    (
        "Internetist ostetud telefon on vigane ja pood keeldub seda tagasi võtmast. Millised reeglid kohalduvad?",
        "Veebipoest saabunud puudusega kaup ei tööta ning müüja ei nõustu kaebust lahendama. Kuhu pöörduda?",
        [["VOS_56", "VOS_217"], ["TKS_12", "TKS_46"]],
        ["VOS", "TKS"],
        ["consumer", "distance_contract", "defective_goods"],
    ),
    (
        "Abipolitseinik kontrollis mu isikut ja pidas mind kinni. Milliseid volitusi ta võis kasutada?",
        "Kas abi politsei tohib dokumenti küsida ning inimese politseiniku korraldusel kinni võtta?",
        [["ABIPOLS_16"], ["KORS_32", "KORS_46"]],
        ["ABIPOLS", "KORS"],
        ["police", "identity_check", "detention"],
    ),
    (
        "Abipolitseinik kasutas minu vastu jõudu. Millised abipolitseiniku ja vahetu sunni reeglid kehtivad?",
        "Millal võib abipolitseinik politseiniku korraldusel füüsilist jõudu kasutada?",
        [["ABIPOLS_28", "ABIPOLS_35"], ["KORS_74"]],
        ["ABIPOLS", "KORS"],
        ["police", "direct_coercion"],
    ),
    (
        "Esitasin ametiasutuse otsuse peale vaide, kuid tahan nüüd halduskohtusse minna. Millised tähtajad kehtivad?",
        "Kuidas vaidlustada haldusakti esmalt vaidemenetluses ja seejärel kohtus?",
        [["HMS_71", "HMS_75"], ["HKMS_46"]],
        ["HMS", "HKMS"],
        ["administrative", "challenge", "deadline"],
    ),
    (
        "Maksuameti otsus rikub minu õigusi ja tahan selle kohtus vaidlustada. Milline vaide- ja kaebekord kehtib?",
        "Kas maksuotsuse peale tuleb esitada vaie ning kui kaua on hiljem aega halduskohtusse pöörduda?",
        [["MKS_137"], ["HKMS_46"]],
        ["MKS", "HKMS"],
        ["tax", "administrative", "challenge"],
    ),
    (
        "Sain valesti parkimise eest väärteootsuse ja tahan selle maakohtus vaidlustada.",
        "Auto parkimise eest määratud trahv tundub vale. Milline parkimisreegel ja kaebetähtaeg kohalduvad?",
        [["LS_20"], ["VTMS_114"]],
        ["LS", "VTMS"],
        ["traffic", "parking", "misdemeanour"],
    ),
    (
        "Teine juht põhjustas liiklusõnnetuse ja mu auto sai kahjustada. Mida peab juht tegema ja kuidas kahju hüvitatakse?",
        "Pärast avariid lahkus süüdlane ning mulle jäi remondiarve. Millised liiklus- ja kahjuhüvitamise reeglid kohalduvad?",
        [["LS_169"], ["VOS_127"]],
        ["LS", "VOS"],
        ["traffic_accident", "damages"],
    ),
    (
        "Korteriühistu sai kohtuotsuse probleemse omaniku korteri võõrandamiseks ja kohtutäitur alustas müüki.",
        "Millised reeglid kohalduvad, kui korteriühistu nõuab korteri müümist ning lahendit täidab kohtutäitur?",
        [["KRTS_32"], ["TMS_24", "TMS_78"]],
        ["KRTS", "TMS"],
        ["apartment", "forced_sale", "enforcement"],
    ),
    (
        "Minu kinnistul puudub teeühendus ja tahan kohtult juurdepääsu üle naabri maa.",
        "Naaber ei luba teeta kinnistule läbipääsu. Milline materiaalõigus ja mida peab hagiavaldus sisaldama?",
        [["AOS_156"], ["TSMS_363"]],
        ["AOS", "TSMS"],
        ["property", "necessary_access", "civil_procedure"],
    ),
    (
        "Ametnikule määrati distsiplinaarkaristus ilma teda enne ära kuulamata ja otsust põhjendamata.",
        "Kas teenistuskohustuse rikkumise eest karistamisel peab ametiasutus ametniku vastuväited ära kuulama?",
        [["ATS_69"], ["HMS_40", "HMS_56"]],
        ["ATS", "HMS"],
        ["public_service", "discipline", "hearing"],
    ),
    (
        "Politsei pidas mind ohu tõrjumiseks kinni. Kuidas seostuvad kinnipidamise alus ja põhiseaduslik vabadus?",
        "Millal võib korrakaitse eesmärgil inimese vabadust piirata?",
        [["KORS_46"], ["PS_20"]],
        ["KORS", "PS"],
        ["police", "detention", "fundamental_rights"],
    ),
    (
        "Omavalitsus tahab minu kinnistu sundvõõrandada, kuid otsuses puudub selge põhjendus.",
        "Millised põhiõiguse ja haldusakti põhjendamise nõuded kehtivad vara sundvõõrandamisel?",
        [["PS_32"], ["HMS_56"]],
        ["PS", "HMS"],
        ["property", "expropriation", "reasoning"],
    ),
    (
        "Teine vanem ei luba mul lapsega suhelda ja tahan kohtult suhtlemiskorda.",
        "Kuidas saab lahus elav vanem taotleda kohtult lapsega suhtlemise korra määramist?",
        [["PKS_143"], ["TSMS_550"]],
        ["PKS", "TSMS"],
        ["family", "contact_right", "civil_procedure"],
    ),
    (
        "Lapse elatis on välja mõistetud, kuid teine vanem ei maksa ja kohtutäitur arestib tema palka.",
        "Kuidas seostuvad lapse elatise maksmine ja võlgniku sissetuleku arestimise piirid?",
        [["PKS_100"], ["TMS_132", "TMS_133"]],
        ["PKS", "TMS"],
        ["maintenance", "enforcement", "protected_income"],
    ),
    (
        "Kaupleja kasutas eksitavat müügivõtet ja tekitas mulle rahalist kahju.",
        "Kas ebaausa reklaami tõttu kahju saanud tarbija saab nõuda kahju hüvitamist?",
        [["TKS_13"], ["VOS_127"]],
        ["TKS", "VOS"],
        ["consumer", "unfair_practice", "damages"],
    ),
]


# Colloquial, misspelled and compound-heavy retrieval cases. The first 15 are
# development data; the last five are holdout.
HARD_LANGUAGE_CASES = [
    ("Toandia ei maksa mulle palla õigel ajal", ["TLS_33"], ["TLS"], ["typo", "colloquial", "wages"]),
    ("Kas uuri tagatis raha voib olla kolme kuu jagu?", ["VOS_308"], ["VOS"], ["typo", "compound", "deposit"]),
    ("Naabrimees pani oma aia minu maa peale", ["AOS_89", "AOS_151"], ["AOS"], ["colloquial", "property"]),
    ("Abipoliteinik võttis mu kinni, kas tal oli selleks õigus?", ["ABIPOLS_16"], ["ABIPOLS"], ["typo", "detention"]),
    ("Kohtutaitur võttis pangast kogu palga ara", ["TMS_132", "TMS_133"], ["TMS"], ["typo", "colloquial", "protected_income"]),
    ("Kas mendid võivad niisama mu nime ja dokumenti küsida?", ["KORS_32"], ["KORS"], ["colloquial", "identity_check"]),
    ("Netipoest ostetu tahaks tagasi saata, 14 paeva pole tais", ["VOS_56"], ["VOS"], ["typo", "distance_contract"]),
    ("Mu andmed läksid lekkesse ja keegi ei teavitanud", ["IKS_45"], ["IKS"], ["colloquial", "data_breach"]),
    ("Korteri uhistu remont on mega kallis ja hääletus kahtlane", ["KRTS_22", "KRTS_35", "KRTS_40"], ["KRTS"], ["compound", "colloquial", "apartment"]),
    ("Mind lasti lahti sest tood pole", ["TLS_89"], ["TLS"], ["colloquial", "inflection", "redundancy"]),
    ("Maksuameti otsus tundub vale, tahan vastu vaielda", ["MKS_137"], ["MKS"], ["colloquial", "tax_challenge"]),
    ("Amet ei kuulanud mind enne otsuse tegemist üldse", ["HMS_40"], ["HMS"], ["colloquial", "hearing"]),
    ("Tahan riigilt dokumenti näha, kaua nad vastata võivad?", ["AVTS_18"], ["AVTS"], ["colloquial", "public_information"]),
    ("Lapse isa ei maksa sentigi", ["PKS_100"], ["PKS"], ["colloquial", "maintenance"]),
    ("Eks ei lase mul last näha", ["PKS_143"], ["PKS"], ["colloquial", "contact_right"]),
    ("Sain parkimis trahvi ja tahan selle kohtusse anda", ["LS_20", "VTMS_114"], ["LS", "VTMS"], ["compound", "colloquial", "parking"]),
    ("Pood muus mulle vigast asja ja ei tee midagi", ["TKS_12", "VOS_217"], ["TKS", "VOS"], ["typo", "colloquial", "defective_goods"]),
    ("Keegi lõi mind ja ütles et see polnud midagi", ["KARS_121"], ["KARS"], ["colloquial", "assault"]),
    ("Mind ähvardatakse sõnumites ära tappa", ["KARS_120"], ["KARS"], ["colloquial", "threat"]),
    ("Mu krundilt pole teele pääsu, ainult üle naabri maa", ["AOS_156"], ["AOS"], ["colloquial", "necessary_access"]),
]


HISTORICAL_CASES = [
    ("HIST-01", "Kas 2018. aastal pidi tööleping olema kirjalik?", "2018-06-01", ["TLS"], ["TLS_4"]),
    ("HIST-02", "Kui suur võis eluruumi tagatisraha olla 2019. aastal?", "2019-03-15", ["VOS"], ["VOS_308"]),
    ("HIST-03", "Milline oli 2020. aastal vaide esitamise tähtaeg?", "2020-01-10", ["HMS"], ["HMS_75"]),
    ("HIST-04", "Kui kaua sai 2017. aastal haldusakti kohtus vaidlustada?", "2017-09-01", ["HKMS"], ["HKMS_46"]),
    ("HIST-05", "Millised olid lapse elatise maksmise reeglid 2021. aastal?", "2021-05-20", ["PKS"], ["PKS_100"]),
    ("HIST-06", "Kui palju võis kohtutäitur 2019. aastal palgast arestida?", "2019-11-01", ["TMS"], ["TMS_132"]),
    ("HIST-07", "Milline oli maksuvõla intressimäär 2020. aastal?", "2020-08-10", ["MKS"], ["MKS_117"]),
    ("HIST-08", "Kas 2018. aastal pidi autos turvavöö kinnitama?", "2018-02-01", ["LS"], ["LS_30"]),
    ("HIST-09", "Millised olid abipolitseiniku volitused 2016. aastal?", "2016-07-01", ["ABIPOLS"], ["ABIPOLS_16"]),
    ("HIST-10", "Kui kiiresti tuli 2022. aastal teabenõudele vastata?", "2022-04-05", ["AVTS"], ["AVTS_18"]),
    ("HIST-11", "Kas 2019. aastal võis lapse andmeid veebiteenuses tema nõusolekul töödelda?", "2019-06-01", ["IKS"], ["IKS_8"]),
    ("HIST-12", "Kuidas jagati korteriühistu kulusid 2020. aastal?", "2020-10-01", ["KRTS"], ["KRTS_40"]),
    ("HIST-13", "Milline oli 2018. aastal apellatsioonitähtaeg tsiviilasjas?", "2018-05-01", ["TSMS"], ["TSMS_632"]),
    ("HIST-14", "Millal oli 2017. aastal lubatud inimese korrakaitseline kinnipidamine?", "2017-01-15", ["KORS"], ["KORS_46"]),
    ("HIST-15", "Millised olid ametniku koondamise reeglid 2015. aastal?", "2015-09-01", ["ATS"], ["ATS_90"]),
]


NO_SOURCE_CASES = [
    ("NOSRC-01", "Kuidas küpsetada kardemonisaiu nii, et need jääksid pehmed?"),
    ("NOSRC-02", "Miks mu toataime lehed kollaseks lähevad?"),
    ("NOSRC-03", "Kirjuta mulle neljarealine luuletus sügisest."),
    ("NOSRC-04", "Kui palju valku võiksin pärast trenni süüa?"),
    ("NOSRC-05", "Kuidas parandada jalgratta logisevat käiguvahetajat?"),
    ("NOSRC-06", "Mis ilm võiks nädalavahetusel matkamiseks sobida?"),
    ("NOSRC-07", "Aita lahendada võrrand 3x + 7 = 22."),
    ("NOSRC-08", "Milline objektiiv sobib paremini öiseks pildistamiseks?"),
    ("NOSRC-09", "Kuidas õpetada koerale käsklust lama?"),
    ("NOSRC-10", "Soovita rahulikku muusikat keskendumiseks."),
    ("NOSRC-11", "Kuidas teha arvutis failidest varukoopia?"),
    ("NOSRC-12", "Mida tähendab inglise keeles sõna serendipity?"),
    ("NOSRC-13", "Kuidas planeerida kolmepäevast rattamatka?"),
    ("NOSRC-14", "Millist mulda vajavad tomatitaimed rõdul?"),
    ("NOSRC-15", "Miks kohv mõnikord liiga hapu maitseb?"),
]


def make_case(
    case_id: str,
    query: str,
    split: str,
    *,
    behavior: str = "retrieve",
    domains_any: list[str] | None = None,
    domains_all: list[str] | None = None,
    sections_any: list[str] | None = None,
    section_groups: list[list[str]] | None = None,
    tags: list[str] | None = None,
    difficulty: str = "medium",
    event_date: str = "",
) -> dict:
    result = {
        "id": case_id,
        "query": query,
        "split": split,
        "expected_behavior": behavior,
        "expected_domains": sorted(set(domains_any or domains_all or [])),
        "expected_domains_all": sorted(set(domains_all or [])),
        "expected_sections_any": list(dict.fromkeys(sections_any or [])),
        "expected_section_groups": section_groups or [],
        "tags": list(dict.fromkeys(tags or [])),
        "difficulty": difficulty,
    }
    if event_date:
        result["event_date"] = event_date
    return result


def build_cases() -> list[dict]:
    cases: list[dict] = []

    for domain, rows in CORE_CASES.items():
        for index, (query, sections, tags, difficulty) in enumerate(rows, start=1):
            split = "development" if index <= 4 else "holdout"
            cases.append(make_case(
                f"{domain}-CORE-{index:02d}",
                query,
                split,
                domains_any=[domain],
                sections_any=sections,
                tags=["core", *tags],
                difficulty=difficulty,
            ))

    for index, (development_query, holdout_query, groups, domains, tags) in enumerate(
        CROSS_CASES, start=1
    ):
        cases.append(make_case(
            f"CROSS-{index:02d}A",
            development_query,
            "development",
            domains_all=domains,
            section_groups=groups,
            tags=["cross_domain", *tags],
            difficulty="hard",
        ))
        cases.append(make_case(
            f"CROSS-{index:02d}B",
            holdout_query,
            "holdout",
            domains_all=domains,
            section_groups=groups,
            tags=["cross_domain", "paraphrase", *tags],
            difficulty="hard",
        ))

    for index, (query, sections, domains, tags) in enumerate(HARD_LANGUAGE_CASES, start=1):
        split = "development" if index <= 15 else "holdout"
        cases.append(make_case(
            f"LANG-{index:02d}",
            query,
            split,
            domains_any=domains,
            sections_any=sections,
            tags=["hard_language", *tags],
            difficulty="hard",
        ))

    for index, (case_id, query, event_date, domains, sections) in enumerate(
        HISTORICAL_CASES, start=1
    ):
        split = "development" if index <= 10 else "challenge"
        cases.append(make_case(
            case_id,
            query,
            split,
            behavior="historical_unavailable",
            domains_any=domains,
            sections_any=sections,
            tags=["historical", "fail_closed"],
            difficulty="hard",
            event_date=event_date,
        ))

    for case_id, query in NO_SOURCE_CASES:
        cases.append(make_case(
            case_id,
            query,
            "challenge",
            behavior="no_result",
            tags=["out_of_scope", "fail_closed"],
            difficulty="hard",
        ))

    return cases


def normalize_query(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def validate_cases(cases: list[dict], laws: list[dict]) -> None:
    by_id = {str(law["id"]).upper(): law for law in laws}
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Evaluation case IDs are not unique")

    normalized_queries = [normalize_query(case["query"]) for case in cases]
    duplicates = [value for value, count in Counter(normalized_queries).items() if count > 1]
    if duplicates:
        raise ValueError(f"Duplicate normalized queries: {duplicates}")

    for case in cases:
        behavior = case["expected_behavior"]
        if behavior not in {"retrieve", "no_result", "historical_unavailable"}:
            raise ValueError(f"{case['id']}: unsupported behavior {behavior}")
        if case["split"] not in {"development", "holdout", "challenge"}:
            raise ValueError(f"{case['id']}: invalid split")
        if behavior == "retrieve" and not (
            case["expected_sections_any"] or case["expected_section_groups"]
        ):
            raise ValueError(f"{case['id']}: retrieval case has no section labels")

        labelled_sections = list(case["expected_sections_any"])
        for group in case["expected_section_groups"]:
            if not group:
                raise ValueError(f"{case['id']}: empty section group")
            labelled_sections.extend(group)

        expected_domains = {
            value.upper()
            for value in case["expected_domains"] + case["expected_domains_all"]
        }
        for section_id in labelled_sections:
            normalized_id = section_id.upper()
            law = by_id.get(normalized_id)
            if law is None:
                raise ValueError(f"{case['id']}: missing corpus section {section_id}")
            actual_domain = str(law["domain"]).upper()
            if expected_domains and actual_domain not in expected_domains:
                raise ValueError(
                    f"{case['id']}: {section_id} belongs to {actual_domain}, "
                    f"not {sorted(expected_domains)}"
                )

    expected_counts = {
        "all": 200,
        "development": 120,
        "holdout": 60,
        "challenge": 20,
    }
    actual_counts = Counter(case["split"] for case in cases)
    actual_counts["all"] = len(cases)
    if any(actual_counts[key] != value for key, value in expected_counts.items()):
        raise ValueError(
            f"Unexpected split counts: {dict(actual_counts)}; expected {expected_counts}"
        )


def write_json(path: Path, payload: list[dict]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    laws = json.loads(LAWS_FILE.read_text(encoding="utf-8"))
    cases = build_cases()
    validate_cases(cases, laws)

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    current = EVAL_DIR / "query_cases.json"
    baseline = EVAL_DIR / "query_cases_v5_baseline.json"
    if current.exists() and not baseline.exists():
        shutil.copy2(current, baseline)

    write_json(current, cases)
    for split in ("development", "holdout", "challenge"):
        write_json(
            EVAL_DIR / f"query_cases_{split}.json",
            [case for case in cases if case["split"] == split],
        )

    counts = Counter(case["split"] for case in cases)
    behaviors = Counter(case["expected_behavior"] for case in cases)
    print(f"Written {len(cases)} cases to {current}")
    print("Splits:", dict(sorted(counts.items())))
    print("Behaviors:", dict(sorted(behaviors.items())))
    print(f"Preserved V5 baseline: {baseline}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
