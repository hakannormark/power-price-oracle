# Genomförda ändringar efter granskningen av sajten

**Datum:** 2026-10-06 · **Underlag:** [SAJT-AUDIT-OCH-PLAN.md](SAJT-AUDIT-OCH-PLAN.md) · **BESS Studio:** [BESS-STUDIO-CHANGES.md](BESS-STUDIO-CHANGES.md)

Beslut som följdes: fas A genomförs, hybriden behålls som standardmodell tills mätningen säger annat, leverantörsjämförelsen anonymiseras, och BESS Studio driftsätts tillsammans med resten.

---

## 1. Vad som är gjort, fas för fas

| Fas | Innehåll | Status |
|---|---|---|
| A | Akuta rättningar | Klar |
| B | Mätning och befordringsregel | Klar |
| C | LightGBM v3 | Klar som kandidat, inte standard |
| D | Kalibrera osäkerhetsbandet | Gjort för v3; standardmodellens band väntar på mer data |
| E | Långtidsprognosen mot terminsmarknaden | Kan inte göras före 1 november |
| F | Hembatteriets kalkylmotor | Data, varning, format och typfall klara; två metodfrågor kvar |

---

## 2. Fas A – akuta rättningar

**Hembatteriets data (F1).** Pipelinen skickade hela växelkursposten där en kurs väntades, och steget kraschade i varje körning sedan 4 oktober. Rättat, med test, och datafilen är ombyggd från riktiga priser.

| Elområde | Före | Efter |
|---|---|---|
| SE1 | 9 613 kr/år, 3,1 år | 6 798 kr/år, 4,9 år |
| SE2 | 9 613 kr/år, 3,1 år | 6 876 kr/år, 4,8 år |
| SE3 | 9 613 kr/år, 3,1 år | 8 704 kr/år, 3,5 år |
| SE4 | 9 613 kr/år, 3,1 år | 10 126 kr/år, 2,9 år |

**BESS Studios marknadsdata (F2).** Låg i samma `try`-block och kördes aldrig. Stegen är nu oberoende av varandra.

**Prognosloggen (F3).** En veckofil hade nått 61 MB mot GitHubs gräns på 100.
- Loggen delas nu per dygn i stället för per vecka. En körning skriver om högst en fil på ungefär 5 MB.
- Bara den första körningen i varje tidslucka loggas. Det är den enda som poängsättningen någonsin har använt.
- Äldre veckofiler läses som förut.

**Inaktuella data (F4).** Vattenmagasin och grannpriser hämtas nu dagligen av underhållsflödet. Startsidan skriver "var fyllda till … (mätvecka från …)" i stället för att ange en gammal mätning som nuläge.

**Texter och sidor.**
- Startsidans rubrik säger inte längre "Lugnt läge" när rutan under anger bortfall.
- Ingen sida rullar i sidled på mobil.
- Modellbeskrivningar som skarpa data motsade är rättade, och metodsidan beskriver den nuvarande standardmodellen.
- Alla sidor har versionsnyckel på skripten, så ingen får gammal kod med ny data.

---

## 3. Fas B – mätningen

**Standardmodellens definition.** Hybriden ändrades 2 oktober under samma namn. Prognoser den ställde ut dessförinnan räknas inte längre som den nuvarande modellens. Träffsäkerhetssidan säger det.

Följden är att standardmodellen i dag bara har **fyra leveransdygn** av ren mätning.

**Befordringsregeln.** En modell får bli standard först efter:
- minst 21 gemensamma leveransdygn med standardmodellen,
- hela 90-procentsintervallet på skillnaden i medelfel under noll totalt,
- och inte säkert sämre på någon enskild horisont.

Intervallet räknas genom att dra om hela leveransdygn, eftersom timmarna inom ett dygn inte är oberoende.

**Ny tabell på träffsäkerhetssidan:** "Kandidater mot standardmodellen", med skillnad, intervall och utfall per modell. Just nu står alla som "För tidigt".

---

## 4. Fas C – LightGBM v3

Loggas som kandidat från nästa körning. Den är **inte** standard.

**Vad som är nytt mot v2**
- Tränad på arkiverade väderprognoser med rätt ålder (Open-Meteo Previous Runs, från januari 2024).
- Framförhållningen är en egen variabel, 1–7 dygn. v2 hade två lägen.
- Rå temperatur, vind och instrålning i stället för index. Träning och drift räknade indexen olika.
- Tyskt och danskt väder som egna variabler, gaspris, utsläppsrätter, prisskillnad mellan elområden.
- Elområdet som kategori.
- En enda funktion bygger indata för både träning och drift.
- Tränas om varje vecka i underhållsflödet, och metadata anger när och på vad.

**Test: omträning före var och en av de sex senaste månaderna**

| Framförhållning | v3 | Tränad på färskaste vädret | Dämpad nivå | Säsongsnaiv |
|---|---|---|---|---|
| 1 dygn | 25,3 | 25,0 | 33,4 | 36,4 |
| 3 dygn | 26,9 | 26,2 | 33,4 | 36,4 |
| 5 dygn | 29,2 | 28,4 | 33,4 | 36,4 |
| 7 dygn | 30,7 | 30,1 | 33,4 | 36,4 |
| Alla | **28,1** | 27,4 | 33,4 | 36,4 |

Medelfel i EUR/MWh, 120 960 poängsatta timmar.

**Tre saker som ska sägas rakt ut:**

1. **Huvudhypotesen höll inte.** Att träna på väderprognoser av rätt ålder var inte bättre än att träna på det färskaste vädret – 28,1 mot 27,4. Metodsidan säger det. Kandidatens värde ligger i de övriga ändringarna.
2. **Den ligger för lågt.** Snedvridningen är −13 EUR/MWh i testet, störst i SE4 (−17). En del är väntat för en medianprognos av priser med toppar uppåt, men det är mer än så.
3. **Bandet var för smalt:** 68 % mot målet 80. Det vidgas nu med det uppmätta gapet per framförhållning, nästan bara uppåt (22–31 EUR/MWh).

Testet är gjort på arkiverade prognoser, en gång per dygn före auktionen. Det säger att modellen är värd att logga, inte att den är bättre än v2. Det avgör den skarpa poängsättningen.

**Ett fel jag hittade i min egen kod.** Testet för att indata aldrig läser okända priser slog larm: dygnsmedlet grupperade alla timmar till samma dygn på grund av tidsstämplarnas upplösning. Rättat före första träningen, och testet ligger kvar.

---

## 5. Fas F – hembatteriet

**Gjort**
- Riktiga data per elområde (se fas A).
- Röd varning överst om prisunderlaget saknas, i stället för tysta schablonvärden.
- Decimalkomma i visade tal.
- Sex namngivna leverantörer är ersatta med typfall A–F. Pris och villkor är kvar som exempel.

**De två metodfrågorna – rättade 2026-10-07**

Båda visade sig vara verkliga fel, och större än jag trodde.

1. **Batteriet räknades två gånger.** Värdet av solel och värdet av arbitrage räknades fram var för sig och lades ihop: 180 cykler solel plus 280 cykler arbitrage, alltså 460 om året på ett batteri. Det fanns en timvis simulering, men det var inte dess flöden som värderades.
2. **Lagrad solel värderades till hela köppriset.** Den sålda elen hade också gett spotpriset, så det riktiga värdet är köppriset på kvällen minus säljpriset mitt på dagen.

Nu räknas ett enda körschema: det billigaste sättet att köra batteriet timme för timme mot elområdets priser. Batteriets värde är elräkningen utan batteri minus elräkningen med. Sol och arbitrage delar på samma batteri, och arbitragedelen är vad nätladdning tillför utöver solen.

Typfall A (15 kWh, 10 kW), energidelen före stödtjänster:

| Elområde | Före | Efter | Värde per lagrad kWh solel |
|---|---|---|---|
| SE1 | 5 051 kr | 1 941 kr | 0,64 kr |
| SE3 | – | 3 297 kr | 0,99 kr |
| SE4 | 7 956 kr | 4 533 kr | 1,38 kr |

Sidan angav tidigare 1,65–1,80 kr per lagrad kWh.

Återbetalningstid för förvald anläggning i kalkylatorn (15 kWh, 65 000 kr brutto, med stödtjänster):

| Elområde | Efter fas A | Nu |
|---|---|---|
| SE1 | 4,9 år | 8,2 år |
| SE3 | 3,5 år | 5,6 år |
| SE4 | 2,9 år | 4,5 år |

Övrigt som följde med:
- Hushållet betalar moms på spotpriset vid köp; det saknades.
- Resultatet är 90 % av det teoretiskt bästa, eftersom en verklig styrning inte känner förbrukning och sol exakt i förväg. Det står på sidan.
- Kalkylatorn i webbläsaren kör samma algoritm som Python mot samma timpriser. Ett test jämför de två för alla typfall, elområden och driftstrategier; de skiljer mindre än 1 %.
- Stödtjänsterna är oförändrade: en ersättning per kW som användaren själv anger. De står nu för 40–60 % av årsvärdet, så det antagandet väger tyngst.

Påståendet att torkåret 2018 var ett positivt extremår är kvar, trots att det inte uppfyller definitionen i mina data.

**Sol per elområde och verkligt extremår – 2026-10-07**

1. **Solen är elområdets egen.** Tidigare användes en beräknad klar-himmel-kurva för Malmö i alla fyra elområden, skalad till samma årsproduktion. Nu hämtas uppmätt solinstrålning (ERA5 via Open-Meteo) timme för timme för varje elområdes ort, sedan 2015, till `data/bess/solar/`. Produktionen är instrålning × installerad effekt × 0,88 (systemverkningsgrad). Mulna dagar finns med.

   | Elområde | Produktion per kWp, senaste året |
   |---|---|
   | SE1 | 783 kWh |
   | SE2 | 891 kWh |
   | SE3 | 958 kWh |
   | SE4 | 1 032 kWh |

2. **Positivt extremår är 2022 som det var.** Faktorn 2,3 × på arbitrage är borta. År 3 och 11 räknas genom att köra samma anläggning mot 2022 års timpriser och sol i elområdet – i både Python och kalkylatorn.

Typfall A (15 kWh), blandad drift:

| Elområde | Energivärde normalår, före → nu | Arbitrage i extremår, före → nu | Återbetalning, före → nu |
|---|---|---|---|
| SE1 | 1 940 → 1 590 kr | 1 013 → 761 kr | 6,1 → 7,0 år |
| SE2 | 1 951 → 1 725 kr | 1 024 → 740 kr | 6,0 → 6,6 år |
| SE3 | 3 295 → 3 080 kr | 1 778 → 3 613 kr | 4,3 → 4,2 år |
| SE4 | 4 529 → 4 382 kr | 2 481 → 3 643 kr | 3,4 → 3,4 år |

Det som syns: 2022 var ett extremår i söder men knappt i norr. En fast faktor gav för mycket i SE1–SE2 och för lite i SE3–SE4.

Kvar som antaganden: negativt extremår är 0,6 × normalåret (inget år sedan 2015 har varit så svagt), stödtjänster × 1,2 / × 0,8 i extremår, och förbrukningsprofilen är densamma i alla elområden. Solen räknas på horisontell instrålning; panelens lutning och väderstreck ingår bara i faktorn 0,88, vilket ger något för lite vinterproduktion och något för mycket mitt på sommaren.

---

## 6. Inte gjort

- **Standardmodellens band (fas D).** Täckningen dygn 7 är för låg, men fyra dygn av ren mätning är för lite att kalibrera på.
- **Långtidsprognosen (fas E).** Första skarpa månaden är klar 1 november.
- **Fler indata till v3.** Residuallast, kärnkraft ur drift och terminspriser har för kort historik att träna på än. Grannländernas priser var inaktuella och är inte med.
- **Villkoren för Open-Meteo.** Tjänsten är gratis för icke-kommersiellt bruk. Om sajten ska användas kommersiellt behöver det kontrolleras; det gäller väderprognosen sajten redan använder lika mycket som den nya träningsdatan.
- **Underhållsflödets nya uppgifter är otestade.** Daglig hämtning av magasin och grannpriser, veckovis omträning och veckovis BESS-uppdatering går inte att köra härifrån. Kontrollera första körningarna under Actions.

---

## 7. Kontroll

- 229 Python-tester och 91 309 kontroller av BESS-modellen går igenom.
- Hela pipelinen är körd lokalt utan nätverk: hembatteriet, BESS-marknadsdata och v3 gav alla resultat.
- Sidorna är kontrollerade i en Chromium-webbläsare, på desktop och mobil.
