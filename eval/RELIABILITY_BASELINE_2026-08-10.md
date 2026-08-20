# OigusAI reliability baseline — 2026-08-10

## Tulemus

v0.5.2 kasutab 22 287 paragrahviga korpusel hübriidset leksikaalset otsingut,
auditeeritavaid valdkonna- ja sättevihjeid ning kontrollitud source-only
fallback'i. Koondtulemus arvutati development-, holdout- ja challenge-jooksude
summana.

| Mõõdik | v0.5.1 | v0.5.2 | Muutus |
|---|---:|---:|---:|
| Koondtabamus | 94/200 (47,0%) | 146/200 (73,0%) | +26,0 pp |
| Oodatud käitumise täpsus | 185/200 (92,5%) | 200/200 (100,0%) | +7,5 pp |
| Vähemalt ühe õige valdkonna Recall@5 | 142/170 (83,5%) | 159/170 (93,5%) | +10,0 pp |
| Kõigi nõutud valdkondade Recall@5 | 8/30 (26,7%) | 22/30 (73,3%) | +46,6 pp |
| Vähemalt ühe õige paragrahvi Recall@5 | 79/140 (56,4%) | 100/140 (71,4%) | +15,0 pp |
| Kõigi paragrahvirühmade Recall@5 | 0/30 (0,0%) | 16/30 (53,3%) | +53,3 pp |
| Corpus-gap rate | 0/200 | 0/200 | muutuseta |

## Splitid

| Split | v0.5.1 | v0.5.2 |
|---|---:|---:|
| Development | 64/120 (53,3%) | 94/120 (78,3%) |
| Holdout | 25/60 (41,7%) | 32/60 (53,3%) |
| Challenge | 5/20 (25,0%) | 20/20 (100,0%) |

Development-komplektis olid kõnekeelsed juhtumid 15/15 ning kõiki nõutud
valdkondi ja paragrahvirühmi sisaldavad mitme seaduse juhtumid 15/15. Holdout
näitab samas ausalt, et täpsete sätete üldistamine uutele parafraasidele vajab
veel tööd: section-group tulemus oli 1/15.

Esimene külmutatud holdout-jooks paljastas seitse liiga range relevantsusvärava
tõttu vastuseta jäänud õiguspäringut. Pärast seda lisati üldised õigusterminid
(mitte päringulaused ega holdout'i sättevihjed) ning lõplik holdout käivitati
uuesti. Seetõttu on siin esitatud lõplik holdout regressioonitulemus, mitte
rangelt puutumata uurimistulemus. Algne v0.5.1 võrdlus jääb muutmata.

## Töökindluse kaitsed

- 52/52 automaattesti läbivad.
- Vana 32 päringu V5 regressioonikomplekt: 32/32.
- Kõik 15 ajaloolise õigusseisu juhtumit keelduvad ilma redaktsiooniandmeteta.
- Kõik 15 õigusvälist päringut tagastavad `no_result`.
- Kõik 170 tänapäevast õiguspäringut leiavad vähemalt ühe korpuseallika.
- Ollama ühenduse-, timeout- või viitekontrolli vea korral tagastab API
  `SOURCE_ONLY_FALLBACK` vastuse, mis sisaldab verifier'iga kontrollitud sätteid.

Fallback ei ole lõplik materiaalõiguslik järeldus. See tagab, et tehniline
mudelitõrge ei muutu kasutaja jaoks tühjaks veateateks ning et kuvatud väited
jäävad olemasolevate Riigi Teataja kirjete piiresse.
