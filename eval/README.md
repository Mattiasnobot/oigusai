# OigusAI query evaluation set

See kataloog sisaldab 200 käsitsi koostatud ja korpuse vastu valideeritud
eestikeelset päringut. Komplekt mõõdab allika leidmist, mitte lõpliku
õigusnõuande sisulist õigsust.

V6.1 lisab pärast 200 juhtumi komplektiga häälestamist eraldi külmutatud
post-tuning kontrolli `query_cases_v61_frozen_holdout.json`. Selle 30 juhtumi
SHA-256, külmutamise kord ja esimene tulemus on failis
`V61_FROZEN_HOLDOUT_2026-08-11.md`. Seda komplekti ei kasutata rankingukaalude,
lexicon'i ega lävendite muutmiseks.

## Jaotus

- `query_cases_development.json`: 120 juhtumit igapäevaseks arenduseks;
- `query_cases_holdout.json`: 60 juhtumit lõppkontrolliks;
- `query_cases_challenge.json`: 20 fail-closed ja ajaloolise õiguse juhtumit;
- `query_cases.json`: kõik 200 juhtumit koos;
- `query_cases_v5_baseline.json`: varasem 32 päringu regressioonikomplekt.

Holdout-päringuid ei kasutata sõnavara, skooride ega lävendite häälestamiseks.
Uue versiooni puhul tehakse esmalt arenduskomplekti analüüs, külmutatakse
muudatused ja alles seejärel käivitatakse holdout üks kord.

## Katvus

Komplekt hõlmab 20 põhidomeeni (töö-, lepingu-, karistus-, perekonna-, põhi-,
haldus-, kohtu-, liiklus-, korrakaitse-, andmekaitse-, avaliku teabe-, tarbija-,
korteri-, asja-, täite-, maksu- ja avaliku teenistuse õigus), 15 kahe valdkonna
juhtumit, 20 kõnekeelset või kirjavigadega päringut, 15 ajaloolise kuupäeva
juhtumit ning 15 ilma õigusallikata või väljaspool ulatust päringut.

## Juhtumi skeem

- `expected_behavior`: `retrieve`, `no_result` või `historical_unavailable`;
- `expected_domains`: vähemalt üks neist valdkondadest peab tulemustes olema;
- `expected_domains_all`: kõik loetletud valdkonnad peavad tulemustes olema;
- `expected_sections_any`: vähemalt üks loetletud paragrahv peab leiduma;
- `expected_section_groups`: igast rühmast peab leiduma vähemalt üks paragrahv;
- `split`, `tags`, `difficulty`: raporti lõiked;
- `event_date`: ajaloolise õigusseisu kontrolli kuupäev.

Builder kontrollib juhtumite arvu, ID-de ja päringute unikaalsust, splittide
suurust ning seda, et iga positiivne paragrahv kuulub märgitud valdkonda ja on
`data/laws.json` korpuses päriselt olemas.

## Käivitamine

```powershell
python scripts/build_query_evaluation_set.py
python scripts/evaluate_queries.py --cases eval/query_cases_development.json --show-failures
python scripts/evaluate_queries.py --cases eval/query_cases_holdout.json
python scripts/evaluate_queries.py --cases eval/query_cases_challenge.json
```

Ühe või mitme parandatud juhtumi kiireks kontrolliks võib `--case-id` võtit
korrata. Juhtumid käivitatakse andmestiku algses järjekorras ning tundmatu ID
korral hindamine katkeb veaga:

```powershell
python scripts/evaluate_queries.py --cases eval/query_cases_development.json `
  --case-id VOS-CORE-01 --case-id KARS-CORE-04 --show-failures
```

Hindaja tagastab nullist erineva väljumiskoodi, kui vähemalt üks juhtum ei läbi
kõiki talle rakenduvaid käitumis-, valdkonna- ja paragrahvikontrolle. See teeb
hindamise sobivaks CI regressiooniväravaks pärast kokkulepitud lävendi lisamist.
