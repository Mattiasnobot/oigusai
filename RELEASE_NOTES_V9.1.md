# ÕigusAI V9.1 – kontrollitud kohalik õigusabi tööruum

Kuupäev: 11.08.2026

## V8.2

- Kihiline vastus: lühivastus, põhjendus, tegevusplaan, teadmata asjaolud,
  kiireloomulisus ja kindluse selgitus.
- Versioonitud ja kasutaja parandatav juhtumikaart.
- Kuupäevade ning kiireloomulisuse tuvastamine ilma leitud kuupäeva automaatselt
  õiguslikuks tähtajaks muutmata.

## V8.3

- Dokumentide kuupäevad, summad, viited, olulised katkendid ja ajajoon.
- Iga väljaloetud dokumendifakt säilitab faili, lehe, märgivahemiku ja täpse
  tõendikatkendi.
- Vastuse, selgitustaotluse, vaide ja nõudekirja ohutud kavandid.
- Puuduvad andmed jäävad kavandis nähtavateks nurksulgudega väljadeks.

## V9.0

- Seitsmeastmeline analüüsirada: juhtumi mõistmine, dokumenditõendid,
  õigusallikate otsing, kohalik analüüs, viidete kontroll, tõendikatkendite
  kontroll ja lihtsa vastuse koostamine.
- Tööliini jälg ei säilita kasutaja teksti.
- Kihilise vastuse õiguslik põhjendus koostatakse ainult kontrollitud väidetest.
- Töölepingu ülesütlemise vormiküsimuse sisuline katvuskontroll nõuab, et vastus
  käsitleks nii TLS § 95 vorminõuet kui ka selle rikkumise tagajärge.

## V9.1

- Lokaalne kvaliteedipaneel aadressil `/admin`.
- Koondmõõdikud: päringud, kontrollitasemed, fallback'i osakaal, analüüsi
  latentsus, tagasiside ja tööjärjekord.
- 200 juhtumi töövoohindaja `scripts/evaluate_workflow.py`.
- V8.1 ligipääsukood, koormuspiirangud, mälupõhine privaatsus ja HTTPS-i hoiatus
  jäid alles.

## Kontrollitud lõppseis

- 153/153 automaattesti läbitud.
- 200/200 töövoo turvakontrolli läbitud.
- Õigusallikate retrieval säilitas V6.1 baasjoone 184/200.
- Töölauavaade, 390 × 844 mobiilivaade, juhtumikaardi muutmine,
  dokumendikavand ja `/admin` kvaliteedipaneel kontrolliti päris brauseris.

## Turvapiir

ÕigusAI ei esita leitud kuupäeva automaatselt õigusliku tähtajana, ei mõtle
dokumendikavandisse puuduvaid andmeid ega kasuta mudeli loodud allikat.
Kasutajaandmeid ei kirjutata püsivasse andmebaasi. Avaliku interneti jaoks on
endiselt vaja HTTPS-i või krüpteeritud tunnelit.
