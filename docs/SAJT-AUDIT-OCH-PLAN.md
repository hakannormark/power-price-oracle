# Granskning av resten av sajten, och plan

**Datum:** 2026-10-06 · **Granskat:** `main` @ `db67354` plus mina ocommittade BESS Studio-ändringar · **Inget är ändrat i den här omgången.**

Omfattning: prognossidorna (start, långtid, träffsäkerhet, modeller, metod, API), hembatterisidorna, prognosmodellerna med träning och poängsättning, samt driften runt dem. BESS Studio är granskad separat i [BESS-STUDIO-AUDIT.md](BESS-STUDIO-AUDIT.md).

---

## 1. Slutsats

**Prognosdelen är i grunden välbyggd och ärlig.** Poängsättningen är korrekt (publicerade timmar räknas inte, en körning per lucka, parade jämförelser), startsidan fungerar och texterna skiljer backtest från skarp drift.

**Tre saker är trasiga i drift just nu:**

1. Hembatteriets data byggs inte. Alla fyra elområden ger exakt samma kalkyl.
2. Samma fel hindrar marknadsdata till BESS Studio från att uppdateras.
3. Prognosloggen växer mot GitHubs filgräns. En veckofil är redan 61 MB.

**Den nya standardmodellen är lovande men otillräckligt belagd på lång sikt.** LightGBM-modellen halverar felet dygn 1–2. Men på dygn 3–7 är den blandning som nu är standard sämre än LightGBM ensam på jämförbara timmar, och modellen tränades på perfekt väder och har inte tränats om sedan den 27 september.

**Störst potential för träffsäkerheten** ligger i att träna om LightGBM på riktiga väderprognoser, löpande och med fler indata. Det är fas C i planen.

---

## 2. Akuta fel

### F1. Hembatteriets data byggs inte – elområdesvalet är verkningslöst
`status.json` visar i varje körning: `"bess": {"ok": false, "error": "unsupported operand type(s) for *: 'float' and 'dict'"}`.

Orsak: [pipeline.py:278](../src/pipeline.py:278) skickar hela växelkursobjektet i stället för kursen. Den publicerade `bess.json` är från en manuell körning 4 oktober som saknade prisdata, så varje elområde har `"No actuals for zone"` och reservvärden.

Följd på sidan, verifierat i webbläsaren:

| Elområde | Visat årsvärde | Visad återbetalning | Verklig dygnsspread |
|---|---|---|---|
| SE1 | 9 613 kr | 3,1 år | 0,50 kr/kWh |
| SE2 | 9 613 kr | 3,1 år | – |
| SE3 | 9 613 kr | 3,1 år | 0,91 kr/kWh |
| SE4 | 9 613 kr | 3,1 år | 1,26 kr/kWh |

Kalkylen räknar med reservvärdet 1,15 kr/kWh i spread överallt, alltså ungefär SE4-nivå. För SE1 överskattas arbitraget med mer än det dubbla. Sidan visar samtidigt "Snitt-dygnsspread: 0.0 öre/kWh" och "Arbitragekapacitet: 0 kr/kWh/år", och det "11-åriga historiska backtestet" är tomt.

Jag körde bygget lokalt med rätt indata utan att skriva något: det fungerar och ger olika värden per zon.

### F2. Marknadsdata till BESS Studio uppdateras inte
Uppdateringen ligger i samma `try`-block efter den rad som kraschar ([pipeline.py:283](../src/pipeline.py:283)) och nås aldrig. Den har dessutom samma fel: rättas bara F1 skrivs ett objekt som växelkurs och Studion slutar räkna. De två måste rättas ihop.

### F3. Prognosloggen närmar sig GitHubs gräns
| Vecka | Filstorlek |
|---|---|
| W38 | 36 MB |
| W39 | 37 MB |
| W40 | **61 MB** |
| W41 (pågår) | 29 MB |

GitHub varnar vid 50 MB och vägrar ta emot filer över 100 MB. Tio modeller loggas nu mot fem i september, och varje push-körning loggar en full uppsättning. En vecka med mycket utvecklingsarbete kan spränga gränsen, och då stannar hela uppdateringen. Repot är 365 MB och växer med ungefär 35 MB i veckan.

### F4. Vattenmagasin och grannpriser är inaktuella
- Magasinsdata slutar 2026-08-24 och hämtades senast 4 september. Startsidan skriver ändå "Vattenmagasinen är fyllda till 71 %" som nuläge.
- Grannländernas priser slutar 5 september.

Båda hämtas bara av en manuell uppgift.

---

## 3. Prognosmodellerna

### Nuläge, skarpt mätt
Parad jämförelse mot den säsongsnaiva referensen, alla elområden, ur `accuracy.json`:

| Modell | Dygn 1 | Dygn 2 | Dygn 4 | Dygn 7 | Mätt sedan |
|---|---|---|---|---|---|
| Dämpad väderskalad | +4 % | +14 % | +16 % | +16 % | 4 sep |
| Färsk nivå | +21 % | +28 % | +16 % | +16 % | 4 sep |
| LightGBM v1 | +45 % | +49 % | +32 % | +18 % | 17 sep |
| **Residual-LightGBM (v2)** | **+47 %** | **+47 %** | **+36 %** | **+21 %** | 27 sep |
| Horisonthybrid (standard) | +47 % | +47 % | +28 % | +7 % | 27 sep |

På de 1 156 leveranstimmar som alla modeller och horisonter har prognosticerat (medelfel, EUR/MWh):

| Horisont | Dämpad | LightGBM v2 | Hybrid (standard) |
|---|---|---|---|
| 0–24 h | 44,3 | 15,4 | 15,4 |
| 48–72 h | 42,0 | 22,4 | 23,6 |
| 72–96 h | 42,4 | 23,1 | 29,6 |
| 120–144 h | 43,0 | 34,2 | 39,9 |
| 144–168 h | 41,8 | 34,3 | 43,8 |

### Fynd

**M1. Standardmodellens definition ändrades under samma namn.** Hybriden var fram till 2 oktober 100 % marknadsjusterad nivå efter 72 timmar; sedan dess är den 60 % LightGBM. Dess poäng på dygn 4–7 är alltså mest den gamla modellens. Det finns i praktiken ingen skarp mätning av den nuvarande standardmodellen på lång horisont.

**M2. Blandningen ser sämre ut än LightGBM ensam på dygn 3–7.** Se tabellen ovan. Vikten 60/40 är satt för hand. Underlaget är tunt (nio dagar), så det är en varningssignal och inget bevis.

**M3. Befordrad efter fem dagar.** Hybriden blev standard 2 oktober på data från 27 september. Det är samma mönster som när den färska nivån befordrades för tidigt. Belägget för dygn 1–2 är starkt; för dygn 3–7 fanns nästan inget.

**M4. LightGBM tränades på perfekt väder.** [train_lightgbm_v2.py](../src/models/train_lightgbm_v2.py) använder ERA5-arkivet. Skarpt får modellen en väderprognos som blir sämre med horisonten. Den har alltså aldrig sett den osäkerhet den möter, vilket bör förklara en del av tappet från +47 % till +21 %.

**M5. Horisonten har bara två värden i träningen.** Varje timme läggs in två gånger: som horisont 12 med gårdagens pris, och som horisont 96 utan. Skarpt matas 0–168. Modellen kan inte lära sig hur osäkerheten växer.

**M6. Ingen omträning, ingen validering.** Modellfilerna är från 27 september och tränas inte om i CI. Träningen har ingen valideringsdel, ingen tidig stoppning och ingen walk-forward-mätning. `meta_v2.json` saknar träningsdatum och dataperiod. Modellerna finns inte heller i `research/backtest.py`.

**M7. Osäkerhetsbandet är för smalt på lång sikt.** Mål 80 %:

| Horisont | LightGBM v2 | Hybrid |
|---|---|---|
| 0–24 h | 83 % | 83 % |
| 96–120 h | 75 % | 91 % |
| 144–168 h | 54 % | 64 % |

**M8. Systematisk snedvridning per elområde.** LightGBM v2 ligger 18 EUR/MWh för lågt i SE4 redan dygn 1 och 34 för lågt dygn 4. I SE3 ligger den 17–32 för högt dygn 4–7. Det tyder på att kopplingen till kontinenten saknas i indata.

**M9. Data som samlas men inte används av LightGBM:**

| Data | Historik | Används i dag |
|---|---|---|
| ENTSO-E:s last- och vindprognos (residuallast) | byggs upp sedan sep | bara i de handbyggda modellerna |
| Gaspris (TTF) och utsläppsrätter | fem år, dagligen | bara i långtidsprognosen |
| Väder i Köpenhamn och Hamburg | fyra år | bara inbakat i "vind söder" |
| Grannländernas priser | 2022–, men inaktuella | nej |
| Kärnkraft ur drift (MW) | 2024– | bara som förklaring |
| Terminspriser | sedan 10 sep | provmodellen `market_scaled` |

**M10. Elområdet matas som ett tal 0–3**, inte som kategori. Träden behandlar det som en ordnad skala.

**M11. Inaktuella påståenden om modellerna:**
- Modellsidan och `registry.py` säger att den färska nivån är sämst skarpt. Den är nu klart bättre än den dämpade på dygn 1–2 (+21 % mot +4 %).
- LightGBM v2 sägs "eliminera nivåbiasen på dag 4–7". Skarpt är biasen +12 EUR/MWh dygn 7 och +32 i SE3.
- Hybridens docstring beskriver den gamla definitionen.
- Metodsidan heter fortfarande "Vad v1 faktiskt gör" och beskriver den dämpade modellen som standard. LightGBM nämns inte.

### Vad jag föreslår för modellerna
Se fas B–D i planen. Allt är hypoteser tills de är mätta skarpt. De återvändsgränder som redan är uppmätta (grannpriser, avbrott och magasin som nivåtermer, anpassade väderkoefficienter, dygnsprofil) gällde de handbyggda modellerna. Som indata till en trädmodell är de en annan fråga, men de ska överleva rullande träningsfönster innan de tros om något.

---

## 4. Prognossidorna

| # | Fynd |
|---|---|
| P1 | Startsidans rubrik säger "Lugnt läge i elsystemet" samtidigt som rutan under säger "Tre kärnkraftsblock är ur drift – det stramar åt SE3", prisskillnaden SE4 − SE2 är 91 EUR/MWh och SE3 går från 3 till 158 öre under dygnet. |
| P2 | Alla sidor rullar i sidled på mobil: sidan blir 407 px bred i en 375 px-vy. Huvudmenyns tre flikar får inte plats. |
| P3 | Magasinsläget anges olika: "13 procentenheter under det normala" på startsidan, "8 % under" på långtidssidan. Kan vara olika mått, men det framgår inte, och underlaget är sex veckor gammalt (F4). |
| P4 | Långtidssidan: terminsmarknaden prissätter november 13,5 EUR/MWh över vår prognos. Sidan säger det öppet, men modellen saknar fortfarande kärnkraftsstoppen. |
| P5 | Träffsäkerhetssidan visar standardmodellens tabell utan att säga att definitionen byttes 2 oktober (M1). |
| P6 | API-sidan är korrekt och hänvisar konsekvent till `default_model`. Inga fel funna. |

---

## 5. Hembatterisidorna

| # | Fynd |
|---|---|
| H1 | Elområdesvalet är verkningslöst och backtestet tomt (F1). Sidorna påstår ändå "simulerade mot det valda elområdets faktiska spotpriser". |
| H2 | När data saknas faller kalkylen tyst tillbaka på inbyggda värden. Inget säger användaren att siffrorna inte är beräknade. |
| H3 | Sex namngivna leverantörer rangordnas med pris, "inlåsning: stark" och tekniska omdömen. Jag hittade ingen källa eller datum per uppgift i `offers.py`. Det är en risk om sidan visas för kunder. |
| H4 | Decimalpunkt i stället för komma på flera ställen: "85.0 öre", "0.67 C", "13.5 kWh". |
| H5 | Extremårsdefinitionen stöds bara delvis av data (redovisat tidigare). Torkåret 2018 anges som positivt extremår men uppfyller inte definitionen. |
| H6 | Återbetalningstiden 3,1 år vilar på tre antaganden jag inte har kunnat pröva förrän F1 är rättat: stödtjänstersättning 25 kr/kW/mån, 2 430 kWh lagrad solel och spread. |

**Avgränsning:** kalkylmotorn i `bess.js` (1 443 rader) har jag inte gått igenom rad för rad. Den granskningen blir meningsfull först när riktiga data finns på plats.

---

## 6. Plan

Ingenting görs förrän du har godkänt. Varje fas kan godkännas för sig.

### Fas A – Akuta rättningar
Små ändringar med låg risk.

1. Rätta växelkursen i pipelinen (F1 + F2 ihop) och lägg till ett test som kör hela steget.
2. Låt hembatterisidan säga ifrån när data saknas i stället för att visa reservvärden (H2).
3. Begränsa prognosloggen: logga bara första körningen per tidslucka, eller dela filerna per dygn (F3).
4. Lägg magasin och grannpriser i den schemalagda körningen, och visa datum på startsidan (F4).
5. Rätta inaktuella texter om modellerna (M11) och rubriken på startsidan (P1).
6. Rätta mobilmenyn (P2) och decimalformatet (H4).

### Fas B – Mätningen först
Utan detta går det inte att veta om fas C hjälper.

1. Ge varje modelldefinition ett eget id. Den nuvarande hybriden får ett nytt, så att dess poäng börjar om.
2. Inför en befordringsregel: minst tre veckors skarp data, parad jämförelse per horisont och elområde.
3. Visa osäkerheten i träffsäkerhetstalen, räknad per leveransdygn.
4. Logga `lightgbm_v2` ensam och hybriden sida vid sida som kandidater till standard (M2).

### Fas C – LightGBM v3
Här ligger den stora möjliga vinsten. Varje steg mäts för sig med rullande fönster, och ingenting blir standard utan fas B:s regel.

| Steg | Åtgärd | Förväntad effekt |
|---|---|---|
| C1 | Träna på arkiverade **väderprognoser** med rätt framförhållning i stället för ERA5 (M4) | störst på dygn 3–7 |
| C2 | Riktig horisont: varje timme tränas vid flera framförhållningar, med exakt den information som fanns då (M5) | dygn 2–7, och bandet |
| C3 | Walk-forward-validering, tidig stoppning, veckovis omträning i CI med datum i metadata (M6) | håller modellen aktuell |
| C4 | Fler indata, ett i taget: tysk och dansk vind och sol var för sig, gas och utsläppsrätter, residuallast dygn 1–2, kärnkraft ur drift, prisskillnad mellan elområden (M8, M9) | främst SE3 och SE4 |
| C5 | Elområde som kategori, eller en modell per område (M10) | SE4:s snedvridning |

C1 kräver Open-Meteos tjänst för historiska prognoser. Den är gratis för icke-kommersiellt bruk, så villkoren behöver kontrolleras om sajten ska visas för kunder.

### Fas D – Osäkerhetsbandet
Kalibrera kvantilerna per horisont mot skarpa utfall (M7). Väntar tills fas C har gett en stabil modell.

### Fas E – Långtidsprognosen
Efter 1 november finns första skarpa månaden. Jämför då vår modell mot terminsmarknaden och avgör om marknadspriset ska vägas in (P4).

### Fas F – Hembatteriet
När fas A är klar: granska kalkylmotorn på riktigt, pröva antagandena i H6 mot data, och bestäm vad som ska gälla för leverantörsjämförelsen (H3).

---

## 7. Beslut jag behöver från dig

1. **Får jag börja med fas A?**
2. **Standardmodellen.** Jag rekommenderar att behålla hybriden som standard tills fas B har gett tre veckors ren mätning, och inte byta till LightGBM ensam på nio dagars underlag. Håller du med?
3. **Leverantörsjämförelsen (H3).** Behålla med källor och datum, anonymisera, eller ta bort?
4. **BESS Studio-ändringarna** ligger fortfarande ocommittade. Ska de in före eller tillsammans med fas A?

---

## 8. Inte granskat

- `bess.js`, `charts.js` och `app.js` rad för rad.
- Långtidsmodellens kod och förklaringsrutans logik (`explain/drivers.py`).
- Tillgänglighet och andra webbläsare än Chromium.
- Gaspriset 73,5 EUR/MWh och övriga marknadstal på långtidssidan mot extern källa.
- Home Assistant-integrationen, som ligger i ett eget repo.
