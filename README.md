# ÕigusAI v0.9.1

ÕigusAI on Eesti-keelne lokaalse AI-ga õigusanalüüsi prototüüp. Põhireegel jääb samaks:

**NO SOURCE → NO LEGAL CLAIM**

## V9.1: kontrollitud tööliin ja kvaliteedipaneel

V9.1 ühendab V8.2–V9.1 arendused üheks auditeeritavaks lokaalseks tööruumiks:

- kasutaja saab avada ja parandada versioonitud juhtumikaarti;
- vastus on kihiline: lühivastus, põhjendus, järgmised sammud, teadmata asjaolud,
  kiireloomulisus ja vastuse kindluse selgitus;
- leitud kuupäeva ei esitata automaatselt õigusliku tähtajana;
- dokumendist leitakse täpsete faili-, lehe- ja märgiviidetega kuupäevad, summad,
  õigusviited, olulised katkendid ja ajajoon;
- kinnitatud faktidest saab koostada vastuse, selgitustaotluse, vaide või
  nõudekirja kavandi; puuduva info kohad jäävad nähtavateks väljadeks;
- analüüs läbib seitse nähtavat etappi juhtumi mõistmisest tõendite ja vastuse
  lõppkontrollini;
- `/admin` kvaliteedipaneel kuvab ainult koondmõõdikuid ega säilita kasutaja
  küsimusi, dokumente, IP-aadresse ega vestluse ID-sid;
- `scripts/evaluate_workflow.py` kontrollib kogu deterministlikku turvapiiri
  olemasoleva 200 auditeeritud päringu peal.
- sisulise katvuse kontroll ei luba töölepingu suulise ülesütlemise küsimusele
  tagastada pelgalt üldist vallandamise alust: vastus peab käsitlema TLS § 95
  vorminõuet ja selle rikkumise tagajärge.

Kvaliteedipaneel: `http://127.0.0.1:8000/admin`

V9.1 ei lisa kasutajakontosid ega püsivat andmebaasi. Juhtumid, juhtumikaardid,
dokumendid, kavandid, tagasiside ja mõõdikud jäävad jätkuvalt lokaalse protsessi
mällu ning aeguvad või kaovad teenuse sulgemisel.

## V8.1: töökindel kohalik piloot

V8.1 muudab vestluse turvalisemaks ja mugavamaks päris kasutajatega proovimiseks:

- valikuline jagatud ligipääsukood kaitseb veebiliidest ja API päringuid;
- päringupiirang ja piiratud tööjärjekord väldivad kohaliku mudeli ülekoormamist;
- pooleliolevad juhtumid aeguvad vaikimisi 120 minuti järel;
- vastuse saab kopeerida või brauseri kaudu PDF-ina salvestada;
- kasutaja saab anda anonüümse „Jah / Mitte päris” hinnangu; küsimust ega dokumenti
  tagasiside juurde ei salvestata;
- `/health` näitab lisaks mudelitele ka järjekorra, ligipääsukaitse ja aktiivsete
  ajutiste juhtumite olekut;
- brauserivastustele lisatakse kaitsvad turvapäised.

Ligipääsukood ei asenda HTTPS-i. Teenuse avamisel avalikku internetti kasuta
krüpteeritud tunnelit või HTTPS-i pöördproksit; paljas ruuteri pordisuunamine ei
ole dokumentide ega isikuandmete jaoks piisavalt turvaline.

## V8: juhtumid ja dokumendid

V8 lubab lisada samasse lokaalsesse vestlusesse PDF-, DOCX-, TXT-, PNG- ja
JPG-faile. Faili sisu hoitakse ainult rakenduse mälus ning see kustutatakse
„Uus vestlus” vajutamisel, teenuse sulgemisel või tegevusetuse järel automaatselt.
Toorfaili kettale ei salvestata.

- tekst seotakse faili, lehekülje ja täpse märgivahemikuga;
- skannitud PDF-id ja pildid loetakse kohaliku `llama3.2-vision` mudeliga;
- OCR-transkriptsioon on kasutajale selgelt eristatav ning vajab originaalilt
  nimede, kuupäevade ja summade ülekontrolli;
- õiguse otsing kasutab nii kasutaja küsimust kui asjakohaseid dokumendikatkendeid;
- dokumendi ja seaduse võrdluse juures kontrollitakse eraldi mõlemad sisendid.

## V7: kontrollitud tõendid

V7 lisab API vastusele struktureeritud `claims` loendi. Iga kuvatav väide on
seotud ühe või mitme kontrollitud allikakatkendiga. API piir kontrollib katkendi
olemasolu uuesti vahetult enne vastuse väljastamist ja ebaõnnestub suletult.

Usaldustasemed on teadlikult eraldi:

- `EVIDENCE_VERIFIED` – seaduseväite täpne tõendikatkend kontrolliti korpusest;
- `DOCUMENT_TEXT_VERIFIED` – katkend leiti PDF/DOCX/TXT tekstikihist;
- `OCR_REVIEW_REQUIRED` – katkend leiti kohaliku OCR-i transkriptsioonist ja
  vajab originaalilt ülekontrolli;
- `INPUTS_VERIFIED` – dokumendi ja seaduse võrdluse mõlemad sisendid on
  kontrollitud, kuid see ei muuda järeldust automaatselt lõplikult õigeks;
- `SOURCE_ONLY_FALLBACK` – mudeli tõrke korral tagastatud deterministlik
  kontrollitud allikate kokkuvõte.

## V6.2: vestluspõhine juhtumi vastuvõtt

V6.2 muudab senise vormi üheks loomulikuks vestluseks, muutmata V6.1
kontrollitud otsingu- ja viitekihti.

- kasutaja kirjutab ühte püsivasse sõnumivälja;
- sisend võib olla üks sõna, täpne küsimus, pikk lugu või kleebitud tekst;
- ÕigusAI küsib vajadusel ainult ühe otsustava küsimuse korraga;
- kasutaja võib alati paluda vastata olemasoleva info põhjal;
- järjestikused vastused, täiendused ja parandused koondatakse üheks
  struktureeritud juhtumikirjelduseks;
- analüüs, järgmised sammud ja avatavad kontrollitud allikad ilmuvad samasse
  vestlusesse;
- „Uus vestlus” puhastab brauseris hoitava juhtumi oleku;
- vestlust ei salvestata eraldi serveripoolsesse sessiooni.

Varasem vormipõhine mall säilib failis `templates/index.html` tagasipöördumise
võimalusena. Rakendus kuvab V6.2 malli `templates/chat.html`.

## V6.1: kohalik kandidaatide reranker

V6.1 lisab V6 hübriidotsingu järele mitmekeelse
`BAAI/bge-reranker-v2-m3` ristkodeerija. Mudel võrdleb kasutaja küsimust ja
juba leitud paragrahvi teksti otse ning parandab kandidaatide lõppjärjestust.

Turvapiir jääb muutumatuks:

- reranker saab ainult kontrollsummaga kinnitatud korpusekirjed;
- ta ei saa luua uut seaduse ID-d, allikat ega teksti;
- mitmeosalise küsimuse osaküsimuste paremad tulemused põimitakse järjestusse;
- laadimis-, mälu- või inferentsivea korral jääb V6 järjestus automaatselt alles;
- mudel töötab lokaalselt ja kasutaja küsimust ei saadeta välisesse teenusesse.

Windowsi NVIDIA seadistuse soovituslik paigaldus ja ühekordne soojendus:

```powershell
python -m pip install torch==2.12.1 --index-url https://download.pytorch.org/whl/cu130
python -m pip install "transformers>=4.45,<5"
python scripts/warmup_reranker.py
```

Mudel on umbes 2,3 GB. Esimene päring pärast rakenduse käivitamist laadib mudeli
lokaalselt GPU-le; järgmised päringud kasutavad juba mälus olevat mudelit.

## V6: kohalik hübriidotsing

V6 ühendab senise V5 sõnapõhise otsingu kohaliku semantilise otsinguga:

- V5 lexical/IDF järjestus;
- Ollama `bge-m3` mitmekeelne embedding;
- lokaalne LanceDB täpne vektoriotsing;
- kaalutud reciprocal-rank fusion (RRF);
- automaatne V5 fallback, kui mudel või indeks pole saadaval;
- korpuse kontrollsumma, mudeli ja skeemiga versioonitud indeks.

Embedding ja LanceDB mõjutavad ainult kirjete järjestust. Vastuses kasutatav
seadusetekst loetakse alati kontrollitud `data/laws.json` korpusest ning iga
vektoritulemuse `content_hash` peab korpuse kirjega kattuma.

Esimesel kasutusel tõmba embedding-mudel ja ehita indeks:

```powershell
ollama pull bge-m3
python scripts/build_vector_index.py
```

Kiiruse võrdlus ja dense-rankide diagnostika:

```powershell
python scripts/benchmark_hybrid.py
python scripts/diagnose_hybrid.py --id-prefix CROSS-
```

Indeksit pole vaja rakenduse iga käivituse ajal uuesti ehitada. Kui korpus,
mudel või indeksi skeem muutub, keeldub rakendus aegunud indeksist ning jätkab
ohutult V5 otsinguga kuni uue indeksi ehitamiseni.

## Juhitud ja kasutajasõbralik sisend

V6 lisab enne õigusotsingut juhtumi vastuvõtukihi. Kasutaja võib sisestada ühe
sõna, täpse küsimuse, vabas vormis loo või pika teksti. Süsteem:

- eristab sisendi tüübi;
- loeb välja osapooled, sündmused, summad, dokumendid ja soovitud abi;
- säilitab väljaloetud faktide juures kasutaja algteksti tõendi;
- küsib kuni kolm lihtsat täpsustust ainult ebapiisava sisendi korral;
- laseb kasutajal alati jätkata olemasoleva info põhjal;
- jagab pika teksti enne õigusotsingut hallatavateks osadeks;
- koostab otsingusisendi ainult kasutaja algtekstist ja kontrollitud väljadest;
- kuvab vastuse lühivastuse, järgmiste sammude ja avatavate allikate plokkidena.

Uus API otspunkt `POST /intake` tagastab struktureeritud juhtumi. `POST /analyze`
toetab lisaks algtekstile valikulisi `search_query` ja `case_context` välju.

V5 lisab selle ette uue, eraldi otsingukihi: kasutaja loomulikku eestikeelset sisendit võib retrieval'i jaoks konservatiivselt laiendada, kuid ükski fuzzy-parandus, sünonüüm või liitsõna **ei muutu ise õigusallikaks**. AI saab endiselt kasutada ainult päriselt korpusest leitud Riigi Teataja kirjeid.

## V5: loomuliku päringu mõistmine

Uus `services/query_understanding.py` ehitab korpuse metaandmetest ja aliastest õigusterminite sõnavara ning toetab:

- kirjavigade konservatiivset fuzzy-match'i;
- eestikeelsete käändelõppude retrieval-only variante;
- lahku kirjutatud liitsõnade tuvastamist (`abi politseinik` → `abipolitseinik`);
- õigusakti/domain'i vihjeid rankingule;
- vana V5 skoori ja IDF/pealkirjaskoori reciprocal-rank fusion'it;
- mitme valdkonna tulemuste hajutamist;
- auditeeritavaid `sections`-vihjeid täpsete olukorramustrite jaoks;
- läbipaistvat API/UI teadet, kui päringut otsingu jaoks laiendati;
- `.env` kaudu häälestatavaid lävendeid;
- query golden-set evaluation runner'it.

Näide:

```text
Kasutaja: "Kas abipoliteinuku võib mind kinni pidada?"

V5 retrieval-tõlgendus:
abipoliteinuku → abipolitseiniku
possible domain: ABIPOLS

Seejärel otsitakse päris korpusest.
Ainult leitud paragrahvid lähevad LLM-i konteksti.
```

UI võib näidata kasutajale:

```text
Otsingu tõlgendus
Otsing laiendas võimalikku kirjaviga „abipoliteinuku” → „abipolitseiniku”.
```

See on teadlikult retrieval'i selgitus, mitte õiguslik järeldus.

## Kaustastruktuur

```text
oigusai/
├─ main.py
├─ config.py
├─ requirements.txt
├─ .env.example
├─ data/
│  ├─ laws.json
│  └─ rt_registry.json
├─ eval/
│  ├─ query_cases.json
│  ├─ query_cases_development.json
│  ├─ query_cases_holdout.json
│  ├─ query_cases_challenge.json
│  └─ V91_WORKFLOW_BASELINE_2026-08-11.json
├─ scripts/
│  ├─ import_riigiteataja.py
│  ├─ build_query_evaluation_set.py
│  ├─ evaluate_queries.py
│  └─ evaluate_workflow.py
├─ services/
│  ├─ analysis_pipeline.py
│  ├─ case_intake.py
│  ├─ case_workspace.py
│  ├─ document_insights.py
│  ├─ documents.py
│  ├─ feedback.py
│  ├─ matters.py
│  ├─ metrics.py
│  ├─ query_understanding.py
│  ├─ legal_search.py
│  ├─ offline_ai.py
│  ├─ runtime_guard.py
│  └─ riigiteataja.py
├─ verifiers/
│  └─ source_verifier.py
├─ templates/
│  ├─ admin.html
│  ├─ chat.html
│  └─ index.html
└─ tests/
```

## 1. Virtuaalkeskkond

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 2. Loo `.env`

```powershell
Copy-Item .env.example .env
```

Põhiseaded sinu praeguse Qwen 9B setup'i jaoks:

```env
APP_HOST=127.0.0.1
APP_PORT=8000
APP_RELOAD=true
LOG_LEVEL=INFO
APP_ACCESS_CODE=
APP_RATE_LIMIT_PER_MINUTE=30
APP_UPLOAD_LIMIT_PER_MINUTE=6
APP_MAX_CONCURRENT_WORK=1
APP_MAX_QUEUED_WORK=8
APP_QUEUE_TIMEOUT=360
MATTER_TTL_MINUTES=120

OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen3.5:9b-q4_K_M
OLLAMA_TIMEOUT=300
OLLAMA_TEMPERATURE=0.1
OLLAMA_TOP_P=0.9
OLLAMA_NUM_CTX=8192
OLLAMA_NUM_PREDICT=1536
OLLAMA_THINK=false
OLLAMA_KEEP_ALIVE=10m
OLLAMA_CITATION_RETRIES=2
ALLOW_MOCK_ANALYSIS=false

LEGAL_DATA_FILE=data/laws.json
LEGAL_MIN_SCORE=6
LEGAL_MAX_RESULTS=5
LEGAL_RELATIVE_THRESHOLD=0.6
ALLOW_LIVE_RT_FALLBACK=false

QUERY_UNDERSTANDING_ENABLED=true
QUERY_LEXICON_FILE=data/query_lexicon.json
QUERY_FUZZY_THRESHOLD=0.82
QUERY_FUZZY_MAX_MATCHES=1
QUERY_FUZZY_MIN_TOKEN_LENGTH=5
QUERY_MAX_EXPANDED_TERMS=16
QUERY_COMPOUND_ENABLED=true
QUERY_DOMAIN_HINT_BONUS=2
QUERY_CURATED_DOMAIN_HINT_BONUS=18

HYBRID_RETRIEVAL_ENABLED=true
VECTOR_INDEX_DIR=data/lancedb
EMBEDDING_MODEL=bge-m3
EMBEDDING_TIMEOUT=180
EMBEDDING_BATCH_SIZE=64
EMBEDDING_KEEP_ALIVE=30m
EMBEDDING_MAX_CHARS=6000
HYBRID_DENSE_CANDIDATES=60
HYBRID_RRF_K=20
HYBRID_LEXICAL_WEIGHT=1.0
HYBRID_DENSE_WEIGHT=1.0
HYBRID_DIVERSITY_WEIGHT=0.75
HYBRID_MULTI_QUERY_ENABLED=true
HYBRID_MAX_QUERY_VARIANTS=3

RERANKER_ENABLED=true
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_DEVICE=auto
RERANKER_CANDIDATES=20
RERANKER_BATCH_SIZE=8
RERANKER_MAX_LENGTH=512
RERANKER_MAX_CHARS=5000
RERANKER_WEIGHT=2.0
```

### V5 query seaded

- `QUERY_UNDERSTANDING_ENABLED` – lülitab uue kihi sisse/välja.
- `QUERY_LEXICON_FILE` – auditeeritavad Eesti loomuliku keele väljendid ja retrieval-laiendused.
- `QUERY_FUZZY_THRESHOLD` – minimaalne sarnasusskoor. Kõrgem = konservatiivsem.
- `QUERY_FUZZY_MAX_MATCHES` – mitu kandidaati ühe sõna kohta võib retrieval'ile lisada.
- `QUERY_FUZZY_MIN_TOKEN_LENGTH` – lühikesi sõnu automaatselt ei parandata.
- `QUERY_MAX_EXPANDED_TERMS` – kaitse liigse query expansion'i eest.
- `QUERY_COMPOUND_ENABLED` – proovib tuntud liitsõnu kokku kirjutada.
- `QUERY_DOMAIN_HINT_BONUS` – väike ranking-boonus üheselt tuvastatud õigusaktile.
- `QUERY_CURATED_DOMAIN_HINT_BONUS` – tugevam boonus auditeeritava lexicon'i täpsele valdkonnavastele; seda rakendatakse ainult juba tekstiliselt sobivale paragrahvile.

Lexicon'i kirje võib lisaks `forms`, `expand` ja `domains` väljadele sisaldada
`sections` loendit. Sättevihje aktiveerub ainult täpse auditeeritud vormi
leidmisel, iga ID peab kohalikus korpuses olemas olema ja vastama kirje
valdkonnale. Vihje mõjutab üksnes päris Riigi Teataja kirjete järjestust.

Soovitus: ära langeta `QUERY_FUZZY_THRESHOLD` agressiivselt. Õigusotsingus on vale parandus ohtlikum kui üks leidmata sünonüüm.

## 3. Korpus

Kui sul on v4.1-st juba töötav `data/laws.json` (näiteks 22 034 paragrahvi), **kopeeri see V5 `data/laws.json` asemele**. Uut importi pole vaja ainult V5 query understanding'u kasutamiseks.

Vajadusel import:

```powershell
python scripts/import_riigiteataja.py
```

## 4. Ollama

```powershell
ollama pull qwen3.5:9b-q4_K_M
ollama pull bge-m3
ollama pull llama3.2-vision
```

Kontroll:

```powershell
ollama ps
```

## 5. Käivita

Esitluseks kõige lihtsam: tee topeltklõps failil `START_OIGUSAI.cmd`. See
kontrollib korpuse ja mudelid, käivitab teenuse, teeb ühe soojenduspäringu ning
avab brauseri.

Kui `.env` failis on `APP_ACCESS_CODE`, küsib veebiliides seda esimesel avamisel
ning hoiab koodi ainult selle brauserikaardi sessioonis. Otse API kasutamisel
lisa sama väärtus päisesse `X-OigusAI-Access-Code`.

Käsitsi käivitamine:

```powershell
python main.py
```

Ava:

```text
http://127.0.0.1:8000/
```

Health endpoint näitab korpuse, kohalike mudelite, dokumenditöötluse,
query-understanding'u, hübriidotsingu, rerankeri, tööjärjekorra ja ajutiste
juhtumite valmisolekut:

```text
http://127.0.0.1:8000/health
```

`ready_for_demo=true` tähendab, et kontrollitud õiguskorpus ja põhianalüüsi
mudel on saadaval. OCR-i valmisolekut näitab eraldi `ocr_model_ready`.

V6 väljad `hybrid_enabled`, `hybrid_ready`, `embedding_model`,
`embedding_dimension`, `vector_rows` ja `hybrid_error` näitavad, kas semantiline
haru töötab. Rakenduse üldolek võib olla `ok` ka siis, kui `hybrid_ready=false`,
sest sellisel juhul töötab kontrollitud V5 fallback.

V6.1 väljad `reranker_enabled`, `reranker_loaded`, `reranker_ready`,
`reranker_model`, `reranker_device`, `reranker_candidates` ja
`reranker_error` näitavad kohaliku lõppjärjestaja olekut. Enne esimest päringut
on `reranker_loaded=false`; pärast edukat esimest päringut peab see olema
`reranker_ready=true`.

11.08.2026 mõõdetud V6.1 baasjoon on dokumenteeritud failis
`eval/V61_RERANKER_BASELINE_2026-08-11.md`: 184/200 koondtabamust, challenge
20/20, paragrahvirühmad 18/30 ja retrieval'i p95 2,42 sekundit. Pärast
seadistuse külmutamist koostatud uus 30 päringu järelkontroll andis 29/30.

## 6. Testid

```powershell
python -m unittest discover -s tests -v
```

V9.1 väljalaske testikomplektis on 153 automaattesti. Need katavad muu hulgas
ligipääsukoodi, päringupiiranguid, tööjärjekorda, ajutiste juhtumite aegumist,
anonüümset tagasisidet, dokumenditöötlust, juhtumikaardi versioonikonflikte,
kuupäevade ohutut käsitlemist, dokumendikavandeid, seitsmeetapilist tööliini,
koondmõõdikute privaatsuspiiri ja kontrollitud tõendiväiteid.

V5 lisab eraldi testid:

- `abipoliteinuku` → `abipolitseiniku`;
- lahku kirjutatud liitsõna;
- valepositiivse fuzzy match'i vältimine;
- query-understanding väljalülitamine;
- kontroll, et fuzzy expansion aitab retrieval'i, kuid tulemuseks jääb päris korpuse seadusekirje.

## 7. Query evaluation

Auditeeritud hindamiskomplekt sisaldab 200 loomulikus eesti keeles päringut. Kõik
positiivsed sildid viitavad kohalikus usaldatud Riigi Teataja korpuses olemas
olevatele paragrahvidele.

Komplekti taastamine ja valideerimine:

```powershell
python scripts/build_query_evaluation_set.py
```

Arenduskomplekti jooks:

```powershell
python scripts/evaluate_queries.py --cases eval/query_cases_development.json --show-failures
```

Kontroll- ja challenge-komplekti jooksud:

```powershell
python scripts/evaluate_queries.py --cases eval/query_cases_holdout.json
python scripts/evaluate_queries.py --cases eval/query_cases_challenge.json
```

Kõigi 200 päringu ühine raport:

```powershell
python scripts/evaluate_queries.py --show-failures
```

V9.1 kogu töövoo turvapiiri kontroll:

```powershell
python scripts/evaluate_workflow.py
```

11.08.2026 V9.1 baasjoon läbis 200/200 juhtumil sisendi kokkuvõtte,
juhtumikaardi, tähtajaohutuse ja isikukoodi mitteküsimise kontrollid. Retrieval'i
tulemus jäi varasema V6.1 baasjoonega võrdseks: 184/200. Masinloetav raport on
failis `eval/V91_WORKFLOW_BASELINE_2026-08-11.json`.

Runner mõõdab:

- oodatud käitumise täpsust (`retrieve`, `no_result`, `historical_unavailable`);
- vähemalt ühe õige valdkonna ja kõigi nõutud valdkondade Recall@5;
- vähemalt ühe õige paragrahvi ja kõigi nõutud paragrahvirühmade Recall@5;
- corpus-gap rate'i;
- koondtulemust split'i ja auditeeritavate märksõnade kaupa.

Jaotus on 120 arendus-, 60 holdout- ja 20 challenge-juhtumit. Holdout-komplekti
ei kasutata lexicon'i, lävendite ega rankingu häälestamiseks; see käivitatakse
alles pärast arenduskomplekti muudatuste külmutamist. Täpsem metoodika ja skeem
on failis `eval/README.md`. Algne 32 päringu V5 komplekt on säilitatud failis
`eval/query_cases_v5_baseline.json`. Esimese 200 päringu jooksu tulemused ja
peamised järeldused on failis `eval/BASELINE_2026-08-10.md`.

Töökindluse järeltesti tulemused on failis
`eval/RELIABILITY_BASELINE_2026-08-10.md`.

## Olulised turvapiirangud

`APP_ACCESS_CODE` on mõeldud kohaliku või usaldatud piloodi lihtsaks
ligipääsupiiriks. HTTP kaudu avalikku internetti saadetuna ei ole kood ega
kasutaja sisu krüpteeritud. Avaliku ligipääsu jaoks on nõutav HTTPS või
krüpteeritud tunnel; samuti tuleb valida vähemalt 8 märgi pikkune juhuslik kood.

`CITATIONS_VERIFIED` tähendab, et kasutatud `[ID]` viited olid mudelile ette
antud kontrollitud allikate hulgas ja lõplik viitevorming läbis kontrolli.
`EVIDENCE_VERIFIED` on rangem: ka mudeli valitud tõendikatkend leiti vastava
allika tekstist ning väitesse ei lubatud tõendis puuduvat uut arvu. See ei ole
siiski täielik materiaalõigusliku järelduse tõestus.

Kui kohalik mudel ei vasta või selle väljund ei läbi viitekontrolli, tagastab
API veateate asemel deterministliku kontrollitud sätete kokkuvõtte staatusega
`SOURCE_ONLY_FALLBACK`. See kokkuvõte ei esita automaatselt tuletatud lõplikku
õiguslikku järeldust, kuid kasutaja saab alati kätte leitud allikad ja nende
põhisisu.

V5 query-understanding ei muuda seda piiri lõdvemaks:

```text
fuzzy / compound / morphology
        ↓
aitab allika LEIDA

Riigi Teataja korpuse kirje
        ↓
saab olla õigusväite ALLIKAS
```

V8 dokumendivõrdluse staatus `INPUTS_VERIFIED` kinnitab teadlikult ainult seda,
et järelduses kasutatud seaduse- ja dokumendikatkendid on päriselt olemas.
ÕigusAI väljund jääb esmaseks selgituseks, mitte lõplikuks õigusnõuks.
