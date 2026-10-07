# BESS Investment Studio – genomförda ändringar

**Datum:** 2026-10-06 · **Utgångsläge:** `main` @ `db67354` · **Status:** allt ligger ocommittat i arbetskatalogen. Inget är pushat eller publicerat.

Granskningen som låg till grund finns i [BESS-STUDIO-AUDIT.md](BESS-STUDIO-AUDIT.md). Hänvisningar som A3 och C1 nedan pekar dit.

Lokal förhandsvisning: `http://localhost:8765/bess-studio.html` (servern startas via `.claude/launch.json`).

---

## 1. Sammanfattning

- **Intäktsmodellen är omskriven från grunden.** Den är nu en riktig optimering mot faktiska timpriser på åtta marknader, hämtade från Svenska kraftnät.
- **Extremåren är omdefinierade och bevisat korrekta** i samtliga 3 500 testade kombinationer (avsnitt 3).
- **En investeringskalkyl med NPV och IRR** är tillagd, dold tills man klickar fram den.
- **Naturskyddsdatat är komplett:** 10 773 områden mot tidigare 500.
- **Gränssnittet är ombyggt:** orimliga inmatningar går inte längre att göra, texterna stämmer med modellen, och sidan fungerar på mobil.
- **Tester:** 56 507 kontroller av kalkylmodellen, 10 tester av optimeringen, hela Python-sviten (187) går igenom.

Resultat med förvalda antaganden (50 MW / 100 MWh, senaste 12 månaderna, inga extremår):

| Elområde | Bruttointäkt | Driftnetto | Återbetalning | Extremår ger |
|---|---|---|---|---|
| SE1 | 118 k€/MW | 56,4 MSEK | 4,8 år | +2 % |
| SE2 | 117 k€/MW | 55,8 MSEK | 4,8 år | +3 % |
| SE3 | 126 k€/MW | 60,1 MSEK | 4,4 år | +22 % |
| SE4 | 119 k€/MW | 55,8 MSEK | 4,8 år | +28 % |

Nivån ligger i linje med en publicerad indexuppskattning på 75–140 k€/MW/år för SE3/SE4 2025 ([European BESS Index](https://europeanbessindex.com/bess-revenue-europe)). Jag har inte kunnat bedöma den källans kvalitet, så se det som en rimlighetskontroll, inte en kalibrering.

---

## 2. Intäktsmodellen

### Nya data (hämtade själv, ingen nyckel behövs)

| Data | Källa | Täckning |
|---|---|---|
| mFRR kapacitetsmarknad, pris och volym per timme och elområde | SvK Data Service | 2023-10-17 → i dag |
| aFRR kapacitetsmarknad, dito | SvK Data Service | 2022-05-10 → i dag |
| FCR-N, FCR-D upp, FCR-D ned, pris och volym per timme | SvK Mimer | 2023-12 → i dag |
| Spotpriser 2020-10 → 2021-12 (saknades i `data/actuals`) | SvK Data Service | lagrade i `data/bess/spot_svk_2020_2021.json` |

Kontroll mot befintliga siffror: mFRR-medelpris och volym stämmer med `market.json`, och FCR-årsmedel 2024/2025 stämmer med tabellen i `bess-utility.json`.

Spotluckan fylldes i en egen fil i stället för i `data/actuals`, så att prognosmodellens träningsdata inte påverkas.

### Ny optimering ([src/geo/dispatch_engine.py](../src/geo/dispatch_engine.py))

Linjärprogrammering vecka för vecka, per MW:

- Samma megawatt säljs aldrig två gånger (effektgräns upp och ned).
- Energin bakom varje åtagande måste finnas i lagret: 1 timme för mFRR, aFRR och FCR-N, 20 minuter för FCR-D.
- **Marknadsdjup från data:** anläggningen kan varje timme sälja högst en vald andel av den volym SvK faktiskt upphandlade. Filen innehåller nio nivåer och sidan interpolerar.
- Pristagare till marginalpris; 88 % verkningsgrad; 5–95 % laddnivå.

Det gamla 0,40-antagandet, 8 %-aktiveringen, gratisladdningen och de påhittade 2022-priserna är borta (A5). aFRR finns nu med på riktigt (A2).

Två synliga reglage ersätter de dolda konstanterna:

| Reglage | Förval | Betydelse |
|---|---|---|
| Högsta andel av varje stödtjänstmarknad | 5 % | marknadsdjup |
| Realiseringsgrad | 75 % | avdrag från det teoretiska taket |

**Förvalen är mina antaganden, inte mätvärden.** De är valda så att nivån hamnar i det publicerade intervallet ovan. Ändra dem om du har bättre underlag.

### Prisunderlag som stämmer med etiketten (A4)

| Val | Innehåll |
|---|---|
| Senaste 12 månaderna | exakt 8 760 timmar, alla marknader faktiska |
| Kalenderåret 2025, 2024 | faktiskt utfall, alla marknader |
| Spotpriser som snittet 2023–2025 / 2021–2025 / 2016–2025 | spotdelen omräknad; stödtjänster som senaste 12 mån |
| Spotpriser som 2022 | dito |

Etiketterna genereras ur datafilen, så de kan inte glida isär från innehållet igen.

### Effekt av marknadsdjupet (A6)

| Anläggning i SE4 | Återbetalning |
|---|---|
| 5 MW / 20 MWh | 6,0 år |
| 50 MW / 100 MWh | 4,8 år |
| 300 MW / 300 MWh | 9,9 år |

Tidigare gav 300 MW / 10 MWh 2,4 år.

---

## 3. Extremår – särskild kontroll

### Definition

Ett extremår är ett år där **spotpriserna i elområdet ser ut som 2022**. Stödtjänstintäkten är oförändrad.

```
extremårets intäkt = stödtjänster + spotdel × (arbitrage 2022 ÷ arbitrage i basperioden)
```

Stödtjänsterna hålls oförändrade eftersom mFRR-kapacitetsmarknaden inte fanns 2022. Det är ett försiktigt antagande.

### Vad jag har verifierat

1. **2022 är det högsta arbitrageåret** i alla fyra elområden och för alla tre varaktigheter sedan 2015. Testas både i Python och JavaScript mot den publicerade filen.
2. **Ett extremår ger aldrig lägre intäkt än ett normalår.** Testat för 4 elområden × 5 varaktigheter × 5 storlekar × 5 marknadsandelar × 7 prisunderlag = 3 500 kombinationer.
3. **Fler extremår kan bara förkorta återbetalningstiden.** Samma 3 500 kombinationer, för 0, 1 och 2 extremår.
4. **Bara spotdelen ändras.** Skillnaden mellan extremår och normalår är exakt skillnaden i spotintäkt, minus arvodet på den.
5. **Tioårssumman går ihop:** 10 × normalår + n × (extremår − normalår).
6. **Prisunderlaget "2022"** gör alla år till extremår. Väljaren låses då och förklarar varför.
7. **I investeringskalkylen** lägger ett extremår till exakt sin diskonterade spotökning; ett tidigare extremår är värt mer än ett senare; år utanför livslängden ignoreras.
8. **I webbläsaren:** alla 4 elområden × 7 prisunderlag × 0/1/2 extremår genomklickade – driftnettot stiger och återbetalningstiden sjunker eller står still i samtliga fall.

Det finns en säkerhetsspärr i koden som hindrar extremåret från att hamna under normalåret. Testerna visar att den aldrig behövde ingripa.

### Skillnad mot tidigare

| | Före | Efter |
|---|---|---|
| SE1, 0 → 1 → 2 extremår | 10,5 → 10,6 → 10,7 år | 4,8 → 4,7 → 4,7 år |
| Extremårets effekt i SE1 | negativ | +2 % |
| Extremårets effekt i SE4 | byggd på påhittade priser | +28 % |

Att effekten är liten i norr är riktigt: där kommer nästan all intäkt från stödtjänster, och spotdelen är liten.

---

### Tillägg: negativa extremår och gränsen 2,0 ×

Hembatterisidan definierar ett positivt extremår som minst 2,0 × normalt värde och ett negativt som 0,6 ×. Studion följer nu samma konvention:

- **Negativa extremår** är tillagda: spotdelen × 0,6, oförändrade stödtjänster. Egen väljare (0–2 år) i den förenklade kalkylen, egna årtal i investeringskalkylen (förval år 7), egen rad i intäktstabellen och i känslighetsanalysen.
- **Kvoten mot 2,0 ×** visas under väljaren för positiva extremår, per elområde.
- Står ett år som både positivt och negativt räknas det som positivt.
- Testerna är utökade till 91 309 kontroller: negativt ≤ normalt ≤ positivt i alla 3 500 kombinationer, och ett negativt år kostar exakt 40 % av spotdelen minus arvodet.
- På hembatterisidan sade texten 2,0 × men koden räknade med 2,3 ×. Texten är rättad till 2,3 ×. Koden är oförändrad, så inga hembatterisiffror ändras. 2,0 är gränsen i definitionen; 2,3 är storleken som tillämpas och motsvarar 2022 mot snittet 2023–2025 (1,97–2,95 × över elområdena, medel 2,3).

### Tillägg 2026-10-07: extremåret vägde för lätt

Frågan var om skillnaden med och utan extremår inte borde vara större. Den borde det, av två skäl.

**1. Jag skalade bara spotdelen.** Normalårets spotintäkt multiplicerades med kvoten för 2022. Men ett batteri som ser 2022 års prisskillnader flyttar från reserverna till arbitrage. Nu optimeras batteriet om mot 2022 års spotpriser, med dagens stödtjänstpriser.

| Elområde | Skalad spotdel (förut) | Omoptimerat (nu) |
|---|---|---|
| SE1 | +2 % | +4 % |
| SE2 | +3 % | +5 % |
| SE3 | +22 % | +37 % |
| SE4 | +28 % | +37 % |

50 MW / 100 MWh, 5 % marknadsandel. Bruttointäkt i ett extremår mot ett normalår.

**2. Stödtjänsterna hölls oförändrade.** Det är ett antagande, och det går nu att ändra med ratten "Stödtjänster i positivt extremår". Som jämförelse är 2022 uträknat som det faktiskt var, med den tidens FCR-priser och utan mFRR-kapacitetsmarknad: omkring 3,6 gånger dagens normalår. Nästan allt kom från FCR-D på 63 EUR/MW/h mot omkring 5 i dag, på en marknad batterierna ännu inte hade fyllt. Det är en historisk uppgift och ingen prognos, så förvalet är kvar på × 1,00.

**Varför återbetalningstiden ändå rör sig lite.** Ett extremår är ett av tio år. Även +37 % på ett år höjer tioårssumman med knappt 4 %. I SE4 går återbetalningen från 4,8 till 4,6 år med ett extremår och till 4,4 med två. Med stödtjänster × 3 i extremåren blir det 3,5 år.

Prisunderlaget "Spotpriser som 2022" använder nu samma omoptimerade körning, så det stämmer med extremåret.

### Är definitionen berättigad?

Prövad mot spotarbitrage per år 2015–2025, som kvot mot snittet av de tre föregående åren:

| År | SE1 | SE2 | SE3 | SE4 |
|---|---|---|---|---|
| 2018 | 1,26 | 1,25 | 1,09 | 1,39 |
| 2019 | 0,77 | 0,77 | 0,82 | 1,06 |
| 2020 | 0,92 | 0,92 | **2,83** | **2,33** |
| 2021 | **2,17** | **2,19** | **4,21** | **3,70** |
| 2022 | **4,86** | **5,35** | **5,73** | **4,95** |
| 2023 | 1,11 | 1,03 | 0,65 | 0,79 |
| 2024 | 0,70 | 0,66 | *0,47* | 0,72 |
| 2025 | 0,82 | 0,80 | 0,83 | 0,83 |

**Slutsats: delvis.** Den håller som stresstestkonvention, inte som empirisk regel.

- **Positiva gränsen 2,0 × stöds.** 2021 och 2022 ligger över i alla elområden, 2020 i söder.
- **Men det var en händelse, inte tre.** Åren hänger ihop, och nivån gick aldrig tillbaka: arbitraget 2023–2025 är tre till sju gånger högre än 2015–2019. Det är ett regimskifte, inte svängningar kring ett stabilt normalläge.
- **Negativa gränsen 0,6 × saknar stöd för arbitrage.** Enda året under är SE3 2024, och det beror på att jämförelsen innehåller krisåren. Mot snittet 2023–2025 är lägsta året 0,76 ×.
- **Våtår sänker prisnivån, inte prisskillnaderna.** 2020 föll medelpriset i SE1 till 0,38 × men arbitraget låg på 0,92 ×, och i söder steg det. Definitionen säger "arbitrage- och energivärdet" som om de följdes åt. För solel stämmer 0,6; för ett batteri gör den det inte.
- **2018 uppfyller inte definitionen.** Hembatterisidan räknar torkåret 2018 som positivt extremår, men arbitraget låg på 1,1–1,4 × och medelpriset på 1,6 ×. Jag har inte ändrat den texten; den bygger på data från 2011 som jag inte har.
- **Frekvensen 1,5 + 0,5 per decennium går inte att skatta** ur elva år med en enda sammanhängande episod.
- **För ett storskaligt batteri är spot en mindre del.** Ett positivt extremår höjer hela intäkten med 2–28 %, ett negativt sänker den med 3–13 %. Den större risken, fallande stödtjänstpriser, täcks inte av definitionen utan av pristrenden i investeringskalkylen.

## 4. Investeringskalkylen (ny)

Dold som förval. Knappen **"Visa investeringskalkyl (NPV och IRR)"** under nyckeltalen fäller ut den. Läget sparas i länken.

**Ger:** nuvärde, internränta för projektet och för eget kapital, diskonterad och odiskonterad återbetalningstid, lägsta skuldtäckningsgrad, kassaflödesdiagram, tabell år för år och CSV-export.

**Ändringsbara antaganden (förval):**

| Antagande | Förval |
|---|---|
| Livslängd | 15 år |
| Kalkylränta, nominell | 8 % |
| Inflation | 2 %/år |
| Stödtjänstpriser, real trend | −5 %/år |
| Spotarbitrage, real trend | 0 %/år |
| Degradering | 2 %/år |
| Cellkomplettering | år 8, 20 % av cellkostnaden |
| Extremår infaller år | jämnt utspridda, t.ex. "5, 10" |
| Bolagsskatt | 20,6 % |
| Avskrivningstid | 10 år |
| Restvärde | 0 % |
| Belåningsgrad / ränta / löptid | 0 % / 6 % / 10 år |

Modellen räknar skatt med förlustavdrag som rullas framåt, och annuitetslån. Slitageavsättningen från den förenklade kalkylen ingår inte här – den ersätts av degradering och cellkomplettering, så inget räknas dubbelt.

Exempel (SE3, 50 MW / 100 MWh, förval): NPV 106 MSEK, IRR 14,8 %, diskonterad återbetalning 8,2 år – mot 4,4 år i den förenklade kalkylen.

---

## 5. Den förenklade kalkylen

Behållen, men rättad (A10):

- "ROI" är borta; det heter återbetalningstid.
- "EBITDA" heter nu driftnetto, och alla poster visas: arvode, drift och underhåll, nätavgift, försäkring/arrende, slitageavsättning.
- Driftkostnaderna är ändringsbara under "4. Driftkostnader".
- Slitaget följer cykeltalet ur optimeringen i stället för fasta 280.
- Förluster visas som förluster ("Ingen återbetalning", negativt driftnetto).
- Nättariffen gissas inte längre utifrån nätägarens namn.

**Ändrade förval:** optimerararvode 3 → 5 %, och en ny post för försäkring/arrende på 0,5 % av investeringen per år.

---

## 6. Karta och platsdata

- **Naturskydd (A7):** tjänsten returnerade 500 objekt per sida och ignorerade sidstorleken efter index 1 000. Läsaren är omskriven och kontrollerar nu antalet mot tjänstens egen uppgift.

  | | Före | Efter |
  |---|---|---|
  | Inlästa skyddade områden | 500 | 10 773 |
  | Rutor med skyddad natur | 290 | 2 920 |
  | Uteslutna rutor (≥ 50 % skyddad) | 2 | 469 |

- **Stationer (A8, B5):** avståndet räknas nu i webbläsaren från rutans mitt till närmaste *anslutningsbara* station. Stamnätsstationer (220–400 kV) räknas inte. Namn och spänning kommer från samma station som avståndet.
- **Egen plats (ny):** "Sätt egen plats" → klicka i kartan. Avståndet räknas exakt från punkten och kan skrivas över om du känner den verkliga anslutningspunkten.
- **Platspoängen** är förenklad till nätnärhet 40 %, marknad 40 %, mark 20 %, och texten säger att den beskriver förutsättningar, inte lönsamhet.
- **Topplistan** sorteras strikt på återbetalningstid och visar två decimaler.

---

## 7. Gränssnitt

| Fynd | Åtgärd |
|---|---|
| A1 orimliga storlekar | Effekt + varaktighet (1–4 h) i stället för MW + MWh; sifferfält bredvid reglagen |
| A9 påhittade startvärden | Ersatta med "–"; tydligt felmeddelande om data saknas |
| A9 ingen friskrivning | Fast ruta överst med friskrivning och datadatum |
| B1–B3 strategi, 2030, hybrid | Borttagna |
| C1 tom underlagssektion | Tre flikar: spotarbitrage per år, stödtjänstmarknader, källor och datum |
| C3 flex-id:n | Riktiga namn |
| C5 statisk extremårstext | Dynamisk |
| C6 talformat | En formaterare, svenskt format överallt |
| C7 jämförelse | Zonfiltret påverkar inte längre jämförelsen; ingen `alert()` |
| C8 modaler | Esc stänger, fokus återställs, bakgrunden låses |
| C9 mobil | Ingen sidledsrullning; kartan först |
| C10 prestanda | Omräkning 519 ms → 27 ms; `dispatch_backtest.json` 3,5 MB → 180 kB |
| C11 cache | Versionsnyckel höjd; data hämtas med `no-cache` |
| C12 körschemat | Riktiga kolumner; exempelvecka med datum (12–18 januari 2026) |
| Metodtexten | Omskriven efter den faktiska modellen; "Bos" och Wang-referensen borta |

**Nytt:** delbar länk (hela scenariot ligger i adressen), känslighetsanalys med nio antaganden, utskrift/PDF.

---

## 8. Ändrade och nya filer

| Fil | Ändring |
|---|---|
| `src/geo/dispatch_engine.py` | Omskriven: LP-optimering |
| `src/geo/svk_data.py` | Ny: hämtning från SvK |
| `src/geo/build_static.py` | Rättad naturskyddsläsning; `--protection-only` |
| `site/assets/bess-studio-model.js` | Ny: all kalkyl, testbar utan webbläsare |
| `site/assets/bess-studio.js` | Omskriven: gränssnitt |
| `site/bess-studio.html` | Ny sidkropp |
| `site/data/bess-map/dispatch_backtest.json` | Nytt format (v2) |
| `site/data/bess-map/cells.json`, `meta.json` | Nytt naturskydd |
| `data/bess/spot_svk_2020_2021.json` | Ny: spotpriser för luckan |
| `tests/test_bess_studio_model.js`, `tests/test_dispatch_engine.py` | Nya tester |
| `requirements-geo.txt` | + scipy |
| `.github/workflows/maintenance.yml` | Ny manuell uppgift `refresh-bess-dispatch` |
| `.claude/launch.json` | Ny: lokal förhandsvisning |

Uppdatera intäktsdata framöver:

```bash
python -m src.geo.dispatch_engine
```

---

## 9. Inte gjort, och kvarstående risker

**Inte gjort**
- **Stationer och ledningar är inte uppdaterade.** OpenStreetMap-tjänsten svarade inte (tre tidsgränser), så gårdagens data ligger kvar.
- **Adress- och ortssökning** i kartan. Kräver en extern söktjänst; jag ville inte lägga till ett sådant beroende utan att fråga.
- **Intradag, FFR och aktiveringsenergi** ingår inte i intäktsmodellen. Det står i metodtexten.
- **Kartbottens licens** (Esri) är inte utredd för kommersiell användning. Attributionen är uppdaterad.
- **Uppdateringen av intäktsdata är manuell.** CI-uppgiften finns men är otestad – jag kan inte köra GitHub Actions härifrån.
- **De gamla filerna** `bess-karta.js` och `bess-utility.js` ligger kvar oanvända av studion.

**Risker att känna till inför en kundvisning**
- Nivån styrs av två antaganden (5 % och 75 %). Var beredd att motivera dem, eller visa känslighetsanalysen.
- 2024 och 2025 ger mycket korta återbetalningstider (ned mot 2 år). Det är faktiska priser, men från en marknad som sedan mättats – använd dem för att visa trenden, inte som prognos.
- Avståndet räknas från rutans mitt. För en verklig tomt: använd "Sätt egen plats".
- Testat i en Chromium-webbläsare, inte Safari eller Firefox.
- `tests/test_new_models.py` kördes inte (kräver lightgbm) och berörs inte av ändringarna.


---

## Tillägg 2026-10-08: stödtjänster i positivt extremår

Fråga: i ett extremår borde väl mer än spotarbitraget öka?

**Kontrollräkning.** Spotarbitraget ökade redan: batteriet optimeras om mot 2022 års spotpriser och spotdelen blir två till åtta gånger större. Men stödtjänstpriserna hölls på dagens nivå, och eftersom stödtjänster är 70–95 % av intäkten för små och medelstora anläggningar blev hela extremåret bara 2–40 % bättre än ett normalår.

**Mätning.** Dagspris per produkt mot elområdets dagspris för spot, båda delade med kalendermånadens median, alla dagar sedan januari 2024 (`src/geo/reserve_comovement.py`):

| Produkt | Dyraste tiondelen spotdagar mot övriga | Elasticitet |
|---|---|---|
| mFRR upp | 2,7 × | 0,58 |
| FCR-D upp | 1,5 × | 0,36 |
| aFRR upp | 1,35 × | 0,15 |
| FCR-N | 1,2 × | 0,15 |
| mFRR ned | 0,96 × | −0,03 |
| aFRR ned | 0,85 × | −0,08 |
| FCR-D ned | 0,83 × | −0,17 |

**Ändring.** Som förval räknas varje produkts pris i extremåret om med (medelspot 2022 / medelspot i basperioden) ^ elasticitet. Valet "Oförändrade priser" ger den gamla uträkningen.

Extremår mot normalår, 2 timmars lager, 5 % marknadsandel:

| Anläggning | SE1 | SE3 | SE4 |
|---|---|---|---|
| 10 MW, förut | × 1,02 | × 1,21 | × 1,18 |
| 10 MW, nu | × 1,08 | × 1,43 | × 1,40 |
| 50 MW, förut | × 1,04 | × 1,37 | × 1,37 |
| 50 MW, nu | × 1,09 | × 1,58 | × 1,50 |
| 200 MW, förut | × 1,12 | × 1,80 | × 1,66 |
| 200 MW, nu | × 1,15 | × 1,91 | × 1,71 |

**Förbehåll.** Sambandet är uppmätt mellan dagar och tillämpas på ett helt år. 2022 som det faktiskt var gav omkring 3,3 × för 50 MW, men mest på grund av FCR-priser från en marknad som batterierna inte hade fyllt.

**Hembatteriet** är oförändrat: där är stödtjänstersättningen ett belopp per kW som användaren anger, och energidelen räknas redan mot 2022 års verkliga priser.


---

## Tillägg 2026-10-07: negativt extremår räknas mot 2020

Det finns ingen officiell definition av extremår för batteriintäkter. Gränserna 2,0 × och 0,6 × var en tumregel. Kalkylen använder nu ingen gräns eller faktor, utan två verkliga år: 2022 och 2020.

**Förut:** negativt extremår = stödtjänster oförändrade + spotdelen × 0,6.

**Nu:** batteriet optimeras om mot 2020 års spotpriser (`coopt_weak` i dispatch_backtest.json), på samma sätt som det positiva året mot 2022. Stödtjänstpriserna följer samma uppmätta samband med spotpriset, nedåt; med "Oförändrade priser" hålls de kvar.

Negativt extremår mot normalår, 2 timmars lager, 5 % marknadsandel:

| Anläggning | SE1 | SE3 | SE4 |
|---|---|---|---|
| 10 MW, förut → nu | 1,00 → 0,89 × | 0,98 → 0,73 × | 0,96 → 0,65 × |
| 50 MW, förut → nu | 0,97 → 0,86 × | 0,93 → 0,66 × | 0,87 → 0,62 × |
| 200 MW, förut → nu | 0,86 → 0,65 × | 0,79 → 0,51 × | 0,72 → 0,44 × |

Spotarbitraget 2020 var 0,2–0,3 gånger de senaste tolv månadernas, inte 0,6. Resten av skillnaden kommer av att uppreserverna blir billigare när spotpriset är lågt (mFRR upp × 0,5 i SE4 enligt sambandet).

Förbehåll: 2020 var också ett pandemiår. Och sambandet för reservpriserna är uppmätt mellan dagar men tillämpas på ett helt år.
