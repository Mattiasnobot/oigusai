# ÕigusAI v6 hübriidotsingu baasjoon — 2026-08-11

## Otsus

V6 lexical + embeddings + LanceDB + RRF haru on rakendatud ja töötab lokaalselt.
See parandab märgatavalt ühe valdkonna ja ühe paragrahvi leidmist ning säilitab
fail-closed käitumise. Mitme paragrahvirühma täpne leidmine ei paranenud; selle
jaoks on järgmine põhjendatud alametapp lokaalne reranker.

V6 on sobiv hübriidotsingu baasversioon, kuid rerankeri etappi ei loeta
lõpetatuks.

## Indeks ja käituskeskkond

| Näitaja | Tulemus |
|---|---:|
| Kohaliku Riigi Teataja korpuse sätted | 22 287 |
| Embedding-mudel | `bge-m3` Ollama kaudu |
| Embeddingu mõõde | 1024 |
| LanceDB read | 22 287 |
| Täisindeksi ehitusaeg | 28,1 min |
| Korpuse vaste | SHA-256 sõrmejäljega kontrollitud |

Indeksi manifest seob omavahel skeemiversiooni, korpuse sõrmejälje,
embedding-mudeli, tekstipiirangu, vektori mõõtme ja ridade arvu. Puuduv või
aegunud indeks ei peata rakendust, vaid jätab aktiivseks täpselt V5 leksikaalse
fallback'i.

## Lõpptulemus 200 külmutatud päringul

| Mõõdik | V5 | V6 | Muutus |
|---|---:|---:|---:|
| Koondtabamus | 174/200 (87,0%) | 183/200 (91,5%) | +4,5 pp |
| Oodatud käitumise täpsus | 200/200 | 200/200 | sama |
| Domain-any Recall@5 | 164/170 (96,5%) | 170/170 (100,0%) | +3,5 pp |
| Domain-all Recall@5 | 21/30 (70,0%) | 26/30 (86,7%) | +16,7 pp |
| Section-any Recall@5 | 127/140 (90,7%) | 136/140 (97,1%) | +6,4 pp |
| Section-group Recall@5 | 17/30 (56,7%) | 17/30 (56,7%) | muutuseta |
| Korpuse puudujäägid | 0/200 | 0/200 | sama |

## Splitid ja ohutus

| Split | V5 | V6 |
|---|---:|---:|
| Development | 120/120 | 120/120 |
| Holdout | 34/60 | 43/60 |
| Challenge | 20/20 | 20/20 |

Challenge sisaldab 15 no-source ja 5 ajaloolise õigusseisu juhtumit. Kõik 20
läbivad. Kõik tagastatud tulemused on algse kohaliku korpuse kirjed ning dense
tulemuse `content_hash` peab vastama samale korpusekirjele.

## Kiirus

| Režiim | p50 | p95 | max |
|---|---:|---:|---:|
| V5 fallback, 200 päringut | 476,6 ms | 1 234,9 ms | 1 869,8 ms |
| V6 hybrid, 200 päringut | 956,0 ms | 1 784,4 ms | 2 953,0 ms |

Pöördindeksiga leksikaalne kandidaatide eelfilter säilitas V5 tulemused täpselt,
kuid vähendas leksikaalse otsingu p95 alla 1,3 sekundi. V6 p95 jääb alla seatud
2 sekundi eesmärgi.

## Automaattestid

- 90/90 üksus- ja integratsioonitesti läbivad.
- Testitud on puuduv ja aegunud indeks, vale `content_hash`, embeddingu vigane
  vastus, dense-haru rike, täpne V5 fallback ja health-väljad.
- `/health` näitab `hybrid_ready=true`, `vector_rows=22287` ning
  `embedding_dimension=1024`.

## Teadlikult avatud töö

1. Section-group mõõdik jäi 17/30 peale; üldine domain-diversity ja klausli-dense
   multi-query parandasid valdkonna katvust, kuid ei asenda paragrahvitaseme
   reranker'it.
2. Enne tootmiskvaliteedi väidet tuleb luua uus, pärast v6 valmimist külmutatud
   väline holdout. Praegust holdout'i kasutati v6 diagnostikas ning selle
   tulemusi ei tohi enam käsitleda täiesti pimedana.
3. Reranker peab järjestama ainult juba kontrollitud korpuse kandidaate ja ei
   tohi ise muutuda õigusallikaks.

