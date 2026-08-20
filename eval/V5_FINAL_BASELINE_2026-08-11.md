# ÕigusAI v5 lõppbaasjoon — 2026-08-11

## Otsus

V5 päringumõistmise development-komplekt on lõpetatud ja sõnastik külmutatud.
Holdout'i ebaõnnestumisi ei lisatud pärast lõpphindamist sõnastikureeglitena.
Need jäävad v6 semantilise retrieval'i mõõdetavaks lähteülesandeks.

Hindamine kasutab 22 287 kohaliku Riigi Teataja korpuse sätet ja
`LEGAL_MAX_RESULTS=5` seadistust.

## Lõpptulemus

| Mõõdik | Tulemus |
|---|---:|
| Koondtabamus | 174/200 (87,0%) |
| Oodatud käitumise täpsus | 200/200 (100,0%) |
| Vähemalt ühe õige valdkonna Recall@5 | 164/170 (96,5%) |
| Kõigi nõutud valdkondade Recall@5 | 21/30 (70,0%) |
| Vähemalt ühe õige paragrahvi Recall@5 | 127/140 (90,7%) |
| Kõigi paragrahvirühmade Recall@5 | 17/30 (56,7%) |
| Korpuse puudujäägid | 0/200 (0,0%) |

Koondtabamus on kolme eraldi jooksu summa. Development ja challenge läbivad
täielikult; holdout jääb nähtavalt madalamaks ning seda ei peideta koondnumbri
taha.

## Splitid

| Split | Koondtabamus | Olulisemad mõõdikud |
|---|---:|---|
| Development | 120/120 (100,0%) | domain-any 110/110; domain-all 15/15; section-any 95/95; section-groups 15/15 |
| Holdout | 34/60 (56,7%) | domain-any 54/60; domain-all 6/15; section-any 32/45; section-groups 2/15 |
| Challenge | 20/20 (100,0%) | historical 5/5; no-source 15/15 |

## Muutus sama päeva algseisust

| Mõõdik | Enne | Pärast | Muutus |
|---|---:|---:|---:|
| Koondtabamus | 148/200 (74,0%) | 174/200 (87,0%) | +13,0 pp |
| Domain-any Recall@5 | 159/170 (93,5%) | 164/170 (96,5%) | +3,0 pp |
| Domain-all Recall@5 | 22/30 (73,3%) | 21/30 (70,0%) | −3,3 pp |
| Section-any Recall@5 | 101/140 (72,1%) | 127/140 (90,7%) | +18,6 pp |
| Section-group Recall@5 | 17/30 (56,7%) | 17/30 (56,7%) | muutuseta |

Domain-all väike langus on dokumenteeritud, mitte peidetud: liiga üldine
`kohtutäitur` → sissetuleku aresti reegel eemaldati. See parandas konkreetsete
täitemenetluse sätete valikut, kuid ei lahendanud semantiliselt kahe valdkonna
parafraase.

## V6 vastuvõtukriteeriumid

V6 lexical + embeddings + fusion peab kasutama sama külmutatud holdout'i ja:

1. säilitama expected-behaviour täpsuse 100% ning challenge tulemuse 20/20;
2. tõstma holdout koondtabamuse üle 34/60;
3. tõstma holdout section-group Recall@5 üle 2/15;
4. mitte langetama holdout section-any Recall@5 alla 32/45;
5. tagastama jätkuvalt ainult kohaliku korpuse päris sätteid.

## Kontrollid

- 200 märgistatud päringut on korpuse vastu valideeritud.
- 83/83 automaattesti läbivad.
- Hindajal on `--case-id` kiire regressioonirežiim.
- V5 turvapiir `NO SOURCE → NO LEGAL CLAIM` jääb muutmata.
