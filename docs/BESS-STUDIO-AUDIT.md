# BESS Investment Studio – fullständig granskning

**Datum:** 2026-10-06 · **Granskad version:** `main` @ `db67354` (studio-koden senast ändrad i `0551f7e`) · **Ingen kod har ändrats.**

Granskat: `site/bess-studio.html`, `site/assets/bess-studio.js`, `src/geo/dispatch_engine.py`, `src/geo/market.py`, `src/geo/build_static.py`, datafilerna i `site/data/bess-map/` samt den publicerade sidan (testkörd i webbläsare, 1440 px och 375 px).

---

## 1. Slutsats

**Studion är inte redo att visas för kunder som underlag för investeringsbeslut.** Den ser färdig ut och laddar utan fel, men:

- Tre vanliga reglagekombinationer ger uppenbart orimliga resultat på skärmen (avsnitt 2, A1–A3).
- Intäktsmotorn är en heuristik med overifierade konstanter, och de "historiska perioderna" är inte de perioder etiketterna anger (A4–A6).
- Platsrankingen har betydligt lägre upplösning än gränssnittet påstår (A7–A8).
- Kalkylen är en enkel payback utan NPV/IRR, degradering eller fullständig OPEX (A10).

Som **screeningverktyg per elområde** med ärliga etiketter är grunden bra: spotdata, SvK-hämtningen, kartlagren och känslighetsanalysens konstruktion håller. Vägen dit beskrivs i avsnitt 8.

**Om du måste demonstrera innan något är åtgärdat:** desktop ≥ 1280 px, 50 MW / 100 MWh, SE3 eller SE4, "Senaste 12 månaderna", 0 extremår, fristående. Undvik extremår, datasetbyte, 2030, hybrid, strategi, ytterlägen på reglagen och körschema-modalen.

---

## 2. Blockerande fel (A)

### A1. Orimlig dimensionering ger toppresultat
Testat på live-sidan, SE4:

| Inmatning | Visas | Kommentar |
|---|---|---|
| 300 MW / 10 MWh | **2,4 år**, badge "30.00 C (0.0h)" | Två minuters lagring värderas som 1h-batteri |
| 5 MW / 600 MWh | "999,0 år", EBITDA 0, **platspoäng 83 "Värd förfrågan (Topp)"** | Poängen är frikopplad från lönsamheten |

Orsak: `bess-studio.js:426` klämmer varaktigheten till 1–4 h för intäkten, medan CAPEX följer verklig MWh. Reglagen saknar spärr.

### A2. aFRR är alltid 0
`dispatch_engine.py:108` – `alloc_afrr` tilldelas aldrig. Alla 72 kombinationer i `dispatch_backtest.json` har `afrr_rev = 0`. Ändå visas raden "aFRR Kapacitet 0,0 MSEK", kolumnen "aFRR Ned" (alltid tom), valet "2030" (multiplicerar noll) och metodtexten säger att aFRR "beräknas utifrån faktiska marginalpriser".

### A3. "Extremår" är inte extrema – i norr försämrar de kalkylen
2022-epoken använder påhittade mFRR-priser (10/8 €/MW, `dispatch_engine.py:300-305`). Resultat i SE1, 50 MW/100 MWh:

| Extremår | Återbetalning |
|---|---|
| 0 | 10,5 år |
| 1 | 10,6 år |
| 2 | 10,7 år |

Datasetet "Extremår 2022 (Historisk peak)" ger 11,4 år mot 10,6 år för senaste 12 månaderna. I tornadon hamnar "2 extremår" i den röda pessimistkolumnen. Hjälptexten utlovar "upp till 238 000 €/MW/år i SE4"; modellen ger 129 096 (2h).

### A4. De historiska perioderna är inte de som etiketterna anger

| Etikett i UI | Vad motorn faktiskt kör |
|---|---|
| Senaste 12 månaderna | 2025-07-18 → 2026-10-06, 10 695 h (≈ 14,6 mån, inkl. morgondagens timmar) |
| 3 år (2023–2026) | 2023–2025 |
| 5 år (2021–2026 inkl. krisår) | 2022–2025 (4 år; 2021 saknas helt i `data/actuals`) |
| 10 år (2015–2026, 350k+ timmar) | 2016–2019 + 2022–2025 (8 år) |

Dessutom är det **bara spotpriset som är historiskt**. mFRR är i alla epoker dagens årsmedel (okt 2025–okt 2026) som ett platt pris alla timmar. FCR är en hårdkodad siffra som blandar produkter: 6,06 (FCR-D upp 2025) för 12 mån och 10 år, 26,85 (FCR-N 2025) för 3/5 år, 64,66 (FCR-N 2022), 10,51 (FCR-D upp 2024). "10 års backtest" är alltså gamla spotpriser med 2026 års stödtjänstpriser.

### A5. Stödtjänstintäkten vilar på overifierade konstanter
`dispatch_engine.py:128-190`:
- 40 % "acceptgrad" i båda riktningar, 42 % för FCR.
- 8 % av kontrakterad effekt aktiveras varje timme, ersatt med `max(spot, 1,5 × kapacitetspris)`.
- Nedaktivering laddar batteriet **gratis**; energin säljs sedan på spot.
- Regelstyrd heuristik, inte optimering (metadata säger "LP/heuristic").

Symptom på att motorn inte är stabil: mer lagring ger ibland mindre intäkt (SE1 2022: 1h 70 231 mot 2h 58 749 €/MW/år; SE4 mFRR 12 mån: 2h 64 073 mot 4h 55 336). Gränssnittet interpolerar linjärt mellan dessa. Det finns inga tester för motorn och ingen kalibrering mot verkliga anläggningars utfall.

### A6. Marknadsdjupet är nästan verkningslöst
300 MW i SE4 får 119 MSEK/år i mFRR. Enligt egna `market.json` upphandlas i snitt 163,9 MW mFRR upp i SE4 och hela marknaden betalade ca 35 M€ (≈ 400 MSEK) på ett år. En anläggning större än marknaden får alltså 30 % av den. Vid 50 MW (30 % av volymen) är avdraget 5 %. Volymerna är hårdkodade i `bess-studio.js:448`, gäller bara uppreglering, och FCR saknar avdrag helt.

Större risk som inte modelleras alls: prisfall vid mättnad. Era egna data visar FCR-D upp 38,36 → 6,06 €/MW/h mellan 2023 och 2025. Kalkylen antar att dagens mFRR-nivå består oförändrad i tio år.

### A7. Naturskyddsfiltret är med stor sannolikhet trunkerat
`meta.json` anger 500 inlästa skyddade områden. `build_static.py:247-279` begär 1 000 per sida och slutar när en sida är kortare – servern verkar ge högst 500. Inga nationalparker finns i resultatet och bara 2 av 4 612 rutor exkluderas. Tooltippen nämner dessutom Natura 2000, som inte läses in. Verifiera mot WFS-tjänsten.

### A8. Platsprecisionen är överdriven
- Avståndet mäts från hela 10 × 10 km-rutan till närmaste station. Ligger en station i rutan blir det 0 km → golv 0,25 km. 501 rutor ligger på golvet; i SE4 har 80 av 418 rutor identisk CAPEX.
- Intäkten är densamma för alla rutor i ett elområde. Rankingen inom en zon avgörs därför av en gissad nättariff (fyra nivåer efter namnmatchning, `bess-studio.js:585-593`). Topp 5 är i praktiken godtycklig bland lika.
- För 38 % av rutorna är "närmaste 130 kV-station" egentligen en 220–400 kV transmissionsstation.
- "Nätägare" är lokalnätskoncessionären; 50 MW ansluts normalt till regionnätet.
- Ledig kapacitet finns inte. `build_static.py` säger det uttryckligen i sin docstring, men UI:t säger "Räknar ut exakt var anläggningen betalar sig snabbast" och "godkända rutan".

### A9. Ingen friskrivning, inget datadatum, påhittade startvärden
- `market.json` innehåller en bra disclaimer som aldrig visas. `meta.json` laddas men används inte. Ingenstans står när data hämtades.
- HTML:en har hårdkodade exempelvärden (Ruta 380_6130, 6,8 år, 316 MSEK, 485 MSEK). De syns tills data laddats – och **ligger kvar som om de vore resultat om laddningen misslyckas**.
- Om `dispatch_backtest.json` inte laddas växlar sidan tyst till en annan formelmodell (`bess-studio.js:556-579`) med andra siffror.

### A10. Kalkylen räcker inte för investeringsbeslut
- Enda måttet är odiskonterad payback, och den kallas "ROI" på tio ställen. ROI är ett avkastningsmått, inte en tid.
- Saknas: NPV, IRR, kalkylränta, degradering, cellbyte/augmentation, skatt, belåning, inflation, restvärde, livslängd över 10 år.
- "EBITDA" innehåller cykelslitage (ingen kassakostnad) men saknar försäkring, arrende, fastighetsskatt, egenförbrukning, energibaserade nätavgifter och obalanskostnad. Optimerararvodet är 3 %. EBITDA-marginalen blir 86 %.
- Slitaget är låst till 280 cykler/år medan körschemat visar 365.
- `Math.max(0, …)` döljer förluster: negativ EBITDA visas som 0.

---

## 3. Metodfrågor av medelhög vikt (B)

| # | Fynd | Plats |
|---|---|---|
| B1 | Budstrategi är fasta multiplikatorer (×1,20 / ×0,35 osv.), inte simulerad. Båda alternativen är alltid sämre än "Samoptimerad". | `bess-studio.js:535-539` |
| B2 | "2030" = mFRR −10 %, utan källa. Kravtabellen i `bess-utility.json` anger oförändrad mFRR-volym. | `:540` |
| B3 | Hybrid: fasta belopp oavsett batteristorlek och plats. Etiketten säger "Sparar 45/70 MSEK" men besparingen kapas vid fackkostnaden – verklig effekt 35 MSEK. Ingen kontroll att en park finns; delad anslutning begränsar inte effekten. | `:505-516` |
| B4 | Platspoäng: 35 % vikt på ett avstånd som är en rutartefakt, 10 % på flexmarknader (binärt, cirkel-approximation). Poängen ändras med MW. Hårdkodade normaliseringstal. | `:624-634` |
| B5 | Spänningsval: ≤ 40 MW använder avstånd till 70 kV-klass men namnet från 130 kV-fältet (inget `n70` i datat). Klasserna är "≥", så "40 kV lokalnät" kan vara en 130 kV-station. Samma fackkostnad för alla nivåer. | `:478-486` |
| B6 | Kabelkostnaden har en dold faktor 1,30; visat avstånd är utan den. | `:498` |
| B7 | `market.json` uppdateras varje timme i pipelinen, `dispatch_backtest.json` byggs för hand och saknar tidsstämpel. De glider isär. | `pipeline.py:283` |
| B8 | Känslighet: "Anläggningsskala" säger "oförändrade marknadspriser" men djupavdraget slår till. Båda utfallen kan vara sämre än basfallet och ändå stå under grönt "Optimistiskt". De viktigaste antagandena (acceptgrad, prisnivå stödtjänster, degradering) saknas. | `:1552-1601` |
| B9 | "Representativ vintervecka (timme 300–468)" är 30 juli–6 augusti 2025 för 12-månadersvalet och januari 2016 för 10 år. SoC ändras utan synlig orsak eftersom mFRR ned och aktiveringar inte visas. | `dispatch_engine.py:193` |

---

## 4. Buggar och användbarhet (C) – verifierade på live-sidan

| # | Fynd |
|---|---|
| C1 | **"Full Datatransparens" är tom vid sidladdning** – renderas först när man klickar en flik. Visar bara spot trots rubriken om SvK:s kapacitetsmarknader; flikar finns bara för 2025, 2024, 2023, 2022, 2018. |
| C2 | Stationsnamn dubbleras: "namnlös (130 kV) (130 kV-station)", "Hedenlunda (400 kV) (130 kV-station)". 56 % av rutorna har "namnlös". |
| C3 | Flexmarknader visas som interna id:n: "eon-skane", "hassleholm", "ehv". |
| C4 | Topplistan ser felsorterad ut: plats 1–2 har 5,4 år, plats 3–5 har 5,3 år. Den förvalda "bästa" rutan är plats 4. Stegaren visar "#1 av 5 · E.ON" när Trelleborg är vald. |
| C5 | "10-års kassaflöde – Inkl. 1 extremår" är statisk text oavsett val (`bess-studio.html:734`). |
| C6 | Blandad talformatering: "0.3 km", "Varav kabel: 1.5 MSEK", "5.4 år", "0.50 C (2.0h)", "365.5 cykler", "1501 MSEK". Udda varaktigheter skrivs ut oavrundade i popupen (`:1074`). |
| C7 | Jämförelse: byter man zon ligger chipsen kvar men modalen säger "Inga rutor har valts". `alert()` vid max 5. Två ingångar (knapp + flytande list). |
| C8 | Modaler stängs inte med Esc, saknar fokushantering, bakgrunden scrollar. |
| C9 | **Mobil (375 px) är trasig:** sidan blir 565 px bred, jämförelselisten täcker innehåll, kartan hamnar 3 700 px ned. Under 1080 px hamnar kartan under alla reglage. |
| C10 | Varje omräkning tar ca 0,5 s (4 612 polygoner med popup byggs om, även vid klick). Sidan hämtar ca 6 MB JSON; `dispatch_backtest.json` är 3,5 MB varav det mesta är 72 exempelveckor. |
| C11 | Cache-nyckeln `?v=20261005a` höjdes inte när JS ändrades 6 okt. Kunder kan få gammal kod med ny data. |
| C12 | Körschemats tabell: kolumnerna Budandel/Acceptgrad/Vinnande timmar är fyllda med ord ("Marginell", "Prioriterad", "Delårsdel", "Frekvensbunden"). "Snittpris" visar årsintäkt. KPI 104 381 €/MW/år stämmer inte med tabellens MSEK. Effekterna "40 %" och "50 %" för aFRR/FCR är påhittade (`:1192, :1198`). |
| C13 | "Hela Sverige (Sök bästa zon)" hoppar inte till bästa ruta; kartan zoomar inte till vald zon. |
| C14 | Raderna "Skyddad natur" och "Lokal flex" ligger i rutan "Intäkt per år". |

---

## 5. Texter och påståenden

- **"Bos tekniska intäktsmodell"** i metodmodalen – intern referens.
- Metodmodalen har två avsnitt nummer 3, beskriver knappar som inte finns ("🇸🇪 Hela Sverige", "🎯 Bästa plats inom vald zon") och en modell som inte är den som körs ("en cykel per dygn", "faktiska marginalpriser", "BSP 3–5 %").
- Överord: "exakt", "Full integration", "Fysisk Drift 8 760 timmar", "350 000 timobservationer" (modellen använder 8 år), "peer-reviewad forskning" (en källa är ett arXiv-preprint, och modellen implementerar inte metoderna).
- Referenser: Xia m.fl. (arXiv:2609.03767) och Kittel, Roth & Schill (Nature Communications 2026) finns; titeln på den senare är avkortad och artikelnumret är okontrollerat. **Wang m.fl. (2026), Sustainability 18(17) hittade jag inte** – kontrollera före visning.
- Varumärket "Batterikalkyl" länkar till hembatterisidan. Blandning av svenska och engelska, många emojier.
- Kartbotten från Esri (attribution "DeLorme, NAVTEQ" är föråldrad; kontrollera villkor för kommersiell användning). OSM-attribution finns bara i sidfoten. Leaflet och typsnitt hämtas från externa CDN:er – demo kräver nät.

---

## 6. Överflödigt

- Död kod: `btnZoomAll`/`zoomAllHotspots`, `txtBestLoc`, `txtBestReason`, `histSpreadBox`, `bessTogglePopupSection`, `metaData`.
- aFRR-rader, 2030-val och strategival – ta bort tills de bygger på något.
- Samma intäktssiffror visas tre gånger (intäktsrutan, "timmar & marknadsallokering", körschemat). Platspoängen visas två gånger i samma kort.
- Flexmarknad som poängfaktor.

---

## 7. Saknas

**För investeringsbeslut**
- NPV, IRR, kalkylränta, livslängd, degradering, augmentation, skatt, belåning; kassaflöde år för år.
- Synlig och redigerbar OPEX och teknik: arvode, O&M, nättariff, verkningsgrad, tillgänglighet.
- Prisscenarier för stödtjänster (mättnad). Sajtens egen långtidsprognos (`langtid.html`) används inte alls.
- Fler intäktsströmmar: intradag, mFRR energiaktivering, FFR (finns i kravtabellen, inte i modellen).

**För kundmötet**
- "Min plats": klicka en punkt eller sök adress/kommun och ange eget avstånd och anslutningskostnad. Kunder har oftast redan en tomt.
- Export (PDF/CSV), delbar länk, sparade scenarier.
- Sifferinmatning och förval (1h/2h/4h) – i dag bara reglage i 5 MW-steg.
- Datadatum och källa per siffra, friskrivning, länk till nätägarnas kapacitetskartor.

**För förvaltning**
- Tester för dispatchmotorn och studio-JS (i dag finns inga). Dispatchbygget i pipelinen.

---

## 8. Rekommendationer i prioritetsordning

**Steg 0 – före första kundvisning (små ändringar)**
1. Spärra varaktigheten till 1–4 h och koppla reglagen (A1).
2. Dölj aFRR, 2030, strategi och hybrid, eller rätta dem (A2, B1–B3).
3. Ta bort extremårsfunktionen tills 2022-epoken bygger på riktiga priser (A3).
4. Rätta periodetiketterna till vad som faktiskt körs och skriv "historisk spot, dagens stödtjänstpriser" (A4).
5. Byt "ROI" mot "återbetalningstid" och "exakt" mot "screening"; visa disclaimer och datadatum (A8–A10).
6. Ersätt hårdkodade startvärden med "–" och visa ett tydligt fel om data saknas (A9).
7. Rätta C1–C6, C11 och "Bos".
8. Kontrollera Wang-referensen.

**Steg 1 – metodens trovärdighet**
1. Bygg om dispatchen som en riktig optimering med timvisa mFRR/aFRR/FCR-priser från SvK, definierade epoker, och kalibrera mot ett känt utfall. Skriv tester.
2. Marknadsdjup och mättnadsscenarier från data (A6).
3. Finansmodell med NPV/IRR, degradering och fullständig OPEX (A10).
4. Rätta naturskyddsinläsningen (A7).
5. Gör om platsdelen: zonresultat som huvudsak, rutorna som filter, plus "min plats"-läge (A8).

**Steg 2 – produkt**
Export och delning, sök, mobil layout, prestanda (uppdatera stil i stället för att bygga om lagren; flytta exempelveckorna till egen fil), tillgänglighet.

---

## 9. Det som är bra

- Spotdatat är komplett och kronologiskt; `market.py` aggregerar SvK-data försiktigt (timmedel först, ingen uppskalning av tunna marknader) och är ärlig i sina kommentarer.
- Kartlagren har källa, hämtdatum och giltighet i `meta.json`.
- Känslighetsanalysen kör samma funktion som basfallet, så den kan inte glida isär.
- CAPEX-uppdelningen i energi och effekt är rätt tänkt.
- Sidan laddar utan konsolfel; kärnflödet (välj ruta → KPI → jämför) fungerar på desktop.

---

## 10. Avgränsningar i granskningen

- Testad i en Chromium-baserad webbläsare; inte Safari/Firefox eller långsamt nät.
- Python-testerna kördes inte (pytest saknas lokalt). `tests/test_bess_utility_js.js` gick igenom men täcker inte studion.
- Marknadspriserna i `market.json` är inte kontrollerade mot SvK:s källa, och intäktsnivåerna är inte jämförda med något externt index.
- A7 bygger på metadata och frånvaron av nationalparker, inte på ett anrop mot WFS-tjänsten.
