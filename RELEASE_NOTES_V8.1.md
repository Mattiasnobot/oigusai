# ÕigusAI V8.1 – töökindel kohalik piloot

Kuupäev: 11.08.2026

## Mis muutus

- Vestlus ja API toetavad valikulist jagatud ligipääsukoodi.
- Kohaliku AI tööd läbivad piiratud järjekorra; liigse koormuse korral saab
  kasutaja arusaadava teate ega jää lõputult ootama.
- Päringutele ja dokumendi üleslaadimisele kehtivad eraldi kiiruspiirangud.
- Mälus hoitavad juhtumid ja dokumendid aeguvad vaikimisi 120 minutiga.
- Kontrollitud vastust saab kopeerida või brauseri printimisvaate kaudu PDF-ina
  salvestada.
- Kasutaja saab anda anonüümse kasulikkuse hinnangu. Hinnanguga ei salvestata
  küsimust, vastust ega dokumente.
- `/health` näitab ligipääsukaitse, tööjärjekorra, ajutiste juhtumite ja
  tagasiside olekut.
- Lisatud on kaitsvad brauseripäised ning avaliku HTTP kasutuse kohta selge
  HTTPS-i hoiatus.

## Kontrollitud seis

- 135/135 automaattesti läbitud.
- 200 päringu hindamiskomplekt: 184/200 ehk 92,0%.
- Oodatud käitumise otsus: 200/200 ehk 100,0%.
- Õige õigusvaldkond Recall@5: 170/170 ehk 100,0%.
- Õige paragrahv Recall@5: 136/140 ehk 97,1%.
- Challenge-komplekt: 20/20.
- Veebis kontrollitud: vale ja õige ligipääsukood, küsimuse esitamine,
  kontrollitud vastus, allikad, kopeerimine ja anonüümne tagasiside.

## Käivitamine

1. Tee topeltklõps failil `START_OIGUSAI.cmd`.
2. Ava `http://127.0.0.1:8000/`.
3. Kui `APP_ACCESS_CODE` on `.env` failis määratud, sisesta sama kood
   veebiliidese avamisel.

## Oluline piirang

Ligipääsukood ei krüpteeri liiklust. Teenust ei tohi tundlike dokumentidega
avalikku internetti avada ainult ruuteri pordisuunamise kaudu. Väliseks piloodiks
kasuta HTTPS-i pöördproksit või krüpteeritud tunnelit.

ÕigusAI annab kontrollitud allikatel põhineva esmase selgituse, mitte lõpliku
õigusnõu.
