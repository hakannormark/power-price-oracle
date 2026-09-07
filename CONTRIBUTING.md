# Att läsa och ändra i det här projektet

Skriven för dig som ska granska koden utan att ha varit med när den byggdes.
Den förklarar inte vad varje fil gör — det står i filernas egna docstrings — utan
vad som är lätt att missförstå och vilka misstag som redan har gjorts här.

## Var du börjar läsa

Fyra filer räcker för att förstå helheten:

| Fil | Varför den är central |
| --- | --- |
| `src/timeutil.py` | `is_official_known()` är projektets viktigaste tio rader. Allt annat hänger på den. |
| `src/models/registry.py` | Vilka modeller som finns, vilken som är standard, och mätvärdena som avgjorde det. |
| `src/pipeline.py` | Hela körningen i ordning, med numrerade steg. |
| `src/research/backtest.py` | Hur modellval avgörs. Läs docstringen — den beskriver två fel som riggen själv har haft. |

Kör `python -m src.diagnose` för att se vad varje extern källa faktiskt levererar
just nu. Det besvarar frågor som dokumentation inte kan.

## Den enda regel som allt vilar på

**Ett pris som börsen redan publicerat är inte en prognos.**

Day-ahead-auktionen publicerar morgondagens priser omkring 12:45. En prognos
utfärdad efter den tidpunkten, för morgondagens timmar, är en avskrift — inte en
gissning. Den får aldrig räknas som modellens träffsäkerhet.

Regeln bor i `is_official_known()` och tillämpas i `evaluate/score.py`.

Den här regeln har jag brutit mot en gång, och det kostade ett felaktigt
modellbyte i produktion: forskningsriggen i `src/research/backtest.py` byggdes
helt utan filtret. Två tredjedelar av det första prognosdygnet poängsattes då mot
priser som redan var offentliga, vilket smickrade varje modell som lutar sig mot
färska priser. En sådan modell befordrades till standard och visade sig vara näst
sämst på riktiga data. **Om du lägger till en utvärdering någonstans: kontrollera
att filtret finns med.**

## Prognosloggen är produktionsdata

`data/forecasts.jsonl` är protokollet som träffsäkerhetssidan poängsätter. Varje
rad där behandlas som något sajten faktiskt har publicerat.

Därför skriver `python -m src.pipeline` **inte** till den lokalt. Registrering
sker bara när `GITHUB_ACTIONS` är satt, eller om du uttryckligen ger `--record`.

Det skyddet finns för att det saknades: 7 % av loggen visade sig vara lokala
testkörningar, varav flera på demodata och trasiga mellanlägen, och de
poängsattes som riktiga prognoser. Det gav en falsk modelljämförelse som ledde
till fel beslut. Rör inte den filen för hand.

## Jämför bara modeller på samma fönster

En ny modell har färre rader i loggen än de gamla, eftersom den funnits kortare
tid. Att läsa av träffsäkerhetstabellen rakt av jämför då olika tidsperioder och
olika väder.

Filtrera till det gemensamma fönstret innan du drar slutsatser. Att inte göra det
gav vid ett tillfälle en modell som såg dubbelt så dålig ut som den var.

## Hur ett modellval avgörs

1. Formulera hypotesen så den kan falsifieras.
2. Kör `python -m src.research.backtest` och lägg till kandidaten i `CANDIDATES`.
3. Koefficienter anpassas bara på kvartal äldre än det de tillämpas på.
4. Vinner den inte tydligt — skeppa den inte.
5. Skeppa som **ny modell**, inte som ändring av en befintlig. Referensen måste
   ligga still för att jämförelsen ska betyda något.
6. Byt standardmodell först när skarp poängsättning på gemensamt fönster håller
   med.

Tolv hypoteser har prövats i projektet. Två har hållit. Listan över de förkastade
står på metodsidan och i commit-meddelandena — de är avsiktligt bevarade, för att
den som inte vet vad som redan testats testar det igen.

## Sådant som ser ut som buggar men inte är det

**Priserna är i EUR/MWh, aldrig i öre.** Öre/kWh är en *valutaomräkning* via
ECB:s dagskurs, inte bara en flyttad decimal. Formeln i den ursprungliga specen
(`ore = eur_mwh / 10`) ger eurocent och är fel. Se `config.ore_per_kwh`.

**Osäkerhetsbandet är brett och asymmetriskt.** Cirka 120 EUR/MWh, med nedsidan
begränsad och uppsidan två till tre gånger nivån. Det är kalibrerat mot utfall —
det tidigare, smalare bandet täckte 39,7 % medan det påstod 80. Se
`models/band.py`.

**`ensemble` är sämre än en av sina beståndsdelar.** Den ligger kvar som mätt
jämförelse. Att ta bort förlorande kandidater är hur man slutar märka när något
blir bättre igen.

**Magasinnivåer och avbrott hämtas men används inte av någon modell.** Båda är
testade och gav ingen förbättring. De visas som fakta i drivkraftstexten, vilket
är en annan sak än att räkna på dem.

**Fritextfält från externa API:er tolkas inte, de citeras.** Svenska kraftnäts
driftinfo och Nord Pools orsakstexter går rakt ut som citat. Att sammanfatta dem
vore att hitta på.

## Sådant som är osäkert och bör granskas hårdare

- Backtestet matar modellerna med ERA5-reanalys, alltså perfekt väder i
  efterhand. Den väderdrivna delen av varje resultat är därför optimistisk.
- Koefficientanpassningen är girig: en term i taget på ett grovt rutnät. En
  riktig samoptimering kan slå de handsatta väderkoefficienterna.
- Skarp poängsättning bygger ännu på få dygn. Allt som sägs om modellordning är
  preliminärt tills fönstret är veckor långt.
- Mobilvyn är aldrig visuellt granskad.
- Bränslekoderna från Nord Pool är avlästa ur data, inte hämtade ur en kodlista.
  De är verifierade mot kända anläggningar men kan vara ofullständiga.

## Köra lokalt

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
echo "ENTSOE_TOKEN=..." > .env          # gitignorerad
python -m src.pipeline                  # skriver site/ och api/, rör inte loggen
python -m unittest discover -s tests -t .
python -m http.server 8000 --directory site
```

Utan token kör allt ändå, men i demoläge med syntetiska priser som är märkta
`source: "demo"` genom hela API:t.
