/**
 * BESS Investment Studio JavaScript Engine
 * Integrerar finansiell 10-årsmodellering, dynamisk nätanslutningskostnad och geografisk lokalisering.
 */

(function () {
  'use strict';

  // Global State
  let cellsData = [];
  let marketData = null;
  let metaData = null;
  let map = null;
  let cellLayerGroup = null;
  let subsLayerGroup = null;
  let linesLayerGroup = null;
  let selectedCellId = null;

  // DOM Inputs
  const rPowerMw = document.getElementById('range-power-mw');
  const rCapMwh = document.getElementById('range-capacity-mwh');
  const rCapexKwh = document.getElementById('range-capex-kwh');
  const rCapexKw = document.getElementById('range-capex-kw');
  const rCableCost = document.getElementById('range-cable-cost');
  const rSubBay = document.getElementById('range-substation-bay');

  const vPowerMw = document.getElementById('val-power-mw');
  const vCapMwh = document.getElementById('val-capacity-mwh');
  const vCapexKwh = document.getElementById('val-capex-kwh');
  const vCapexKw = document.getElementById('val-capex-kw');
  const vCableCost = document.getElementById('val-cable-cost');
  const vSubBay = document.getElementById('val-substation-bay');
  const badgeCRate = document.getElementById('badge-c-rate');

  const selZone = document.getElementById('sel-target-zone');
  const selStrategy = document.getElementById('sel-bess-strategy');
  const selHistoryDataset = document.getElementById('sel-history-dataset');
  const lblDatasetDesc = document.getElementById('lbl-dataset-desc');
  const selMarketYear = document.getElementById('sel-market-year');
  const selExtremeYears = document.getElementById('sel-extreme-years');
  const chkSolar = document.getElementById('chk-hybrid-solar');

  const btnFindBest = document.getElementById('btn-find-best');
  const mapStatus = document.getElementById('bs-map-status');

  // KPI Elements
  const kpiPayback = document.getElementById('kpi-payback');
  const kpiPaybackSub = document.getElementById('kpi-payback-sub');
  const kpiCapex = document.getElementById('kpi-capex');
  const kpiCapexSub = document.getElementById('kpi-capex-sub');
  const kpiEbitda = document.getElementById('kpi-ebitda');
  const kpi10yrCf = document.getElementById('kpi-10yr-cf');
  const badgeRoi = document.getElementById('badge-decision-roi');

  const txtBestLoc = document.getElementById('txt-best-location');
  const txtBestReason = document.getElementById('txt-best-reason');
  const listTopSites = document.getElementById('list-top-sites');

  // Details
  const detZone = document.getElementById('det-zone');
  const detDso = document.getElementById('det-dso');
  const detStation = document.getElementById('det-station');
  const detDist = document.getElementById('det-dist');
  const detEnergyCapex = document.getElementById('det-energy-capex');
  const detPowerCapex = document.getElementById('det-power-capex');
  const detStationCapex = document.getElementById('det-station-capex');
  const detCableCost = document.getElementById('det-cable-cost');
  const detProt = document.getElementById('det-prot');
  const detFlex = document.getElementById('det-flex');

  // Revenues
  const revMfrr = document.getElementById('rev-mfrr');
  const revAfrr = document.getElementById('rev-afrr');
  const revFcr = document.getElementById('rev-fcr');
  const revArb = document.getElementById('rev-arb');
  const revSolar = document.getElementById('rev-solar');
  const histSpreadBox = document.getElementById('hist-spread-box');

  // Modal
  const modalMethod = document.getElementById('modal-method');
  const btnOpenMethod = document.getElementById('btn-open-method');
  const btnCloseMethod = document.getElementById('btn-close-method');
  const btnModalOk = document.getElementById('btn-modal-ok');

  function initModal() {
    btnOpenMethod.addEventListener('click', () => modalMethod.style.display = 'flex');
    btnCloseMethod.addEventListener('click', () => modalMethod.style.display = 'none');
    btnModalOk.addEventListener('click', () => modalMethod.style.display = 'none');
    modalMethod.addEventListener('click', (e) => {
      if (e.target === modalMethod) modalMethod.style.display = 'none';
    });
  }

  function initInputs() {
    const updateInputs = () => {
      const mw = parseFloat(rPowerMw.value);
      const mwh = parseFloat(rCapMwh.value);
      const cRate = mw > 0 ? (mw / mwh) : 0;
      const hours = mw > 0 ? (mwh / mw) : 0;

      vPowerMw.textContent = mw + ' MW';
      vCapMwh.textContent = mwh + ' MWh';
      vCapexKwh.textContent = Math.round(rCapexKwh.value).toLocaleString('sv-SE') + ' kr';
      vCapexKw.textContent = Math.round(rCapexKw.value).toLocaleString('sv-SE') + ' kr';
      vCableCost.textContent = parseFloat(rCableCost.value).toFixed(1).replace('.', ',') + ' MSEK/km';
      vSubBay.textContent = Math.round(rSubBay.value) + ' MSEK';

      badgeCRate.textContent = `${cRate.toFixed(2)} C (${hours.toFixed(1)}h)`;
      recalculateAll();
    };

    [rPowerMw, rCapMwh, rCapexKwh, rCapexKw, rCableCost, rSubBay].forEach(el => el.addEventListener('input', updateInputs));
    [selZone, selStrategy, selHistoryDataset, selMarketYear, selExtremeYears, chkSolar].forEach(el => el.addEventListener('change', () => {
      if (lblDatasetDesc && selHistoryDataset) {
        const val = selHistoryDataset.value;
        if (val === 'last12m') lblDatasetDesc.textContent = 'Faktiska timpriser från senaste 12 månaderna (Nord Pool / ENTSO-E).';
        else if (val === '3y') lblDatasetDesc.textContent = '3 års historiskt snitt (2023–2026), balanserar post-kris.';
        else if (val === '5y') lblDatasetDesc.textContent = '5 års historik (2021–2026), fångar både extrema och lugna marknader.';
        else if (val === '10y') lblDatasetDesc.textContent = 'Fullständig 10-årig Nord Pool-historik (350 000+ timmar sedan 2015).';
        else if (val === '2022') lblDatasetDesc.textContent = 'Energikrisåret 2022: Maximala spreadar (upp till 238 000 €/MW/år i SE4).';
        else if (val === '2024') lblDatasetDesc.textContent = 'Lågvolatilt år 2024: Mycket konservativt stresstest.';
      }
      recalculateAll();
    }));

    btnFindBest.addEventListener('click', () => {
      const best = findBestCell();
      if (best) {
        selectedCellId = best.id;
        renderMapLayers();
        updateSelectedDetail(best);
        map.setView(best.c, 9);
      }
    });

    updateInputs();
  }

  function initMap() {
    map = L.map('bs-map', {
      center: [62.0, 15.5],
      zoom: 5,
      minZoom: 4,
      maxZoom: 13
    });

    // Esri Dark Gray Canvas
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
      attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ',
      maxZoom: 16
    }).addTo(map);

    linesLayerGroup = L.layerGroup().addTo(map);
    cellLayerGroup = L.layerGroup().addTo(map);
    subsLayerGroup = L.layerGroup().addTo(map);
  }

  // Beräknar spotarbitrage per zon och vald datasetperiod
  function getZoneSpotArbRate(zone, period, duration) {
    if (!marketData?.spot?.[zone]) return 0;
    const is4h = duration >= 3.5;
    const zSpot = marketData.spot[zone];

    if (period === 'last12m') {
      return is4h ? (zSpot.last12m?.arb_4h_eur_mw_yr || 0) : (zSpot.last12m?.arb_2h_eur_mw_yr || 0);
    }

    const years = zSpot.years || {};
    if (period === '2022') {
      const yr = years['2022'];
      return is4h ? (yr?.arb_4h_eur_mw_yr || 0) : (yr?.arb_2h_eur_mw_yr || 0);
    }
    if (period === '2024') {
      const yr = years['2024'];
      return is4h ? (yr?.arb_4h_eur_mw_yr || 0) : (yr?.arb_2h_eur_mw_yr || 0);
    }

    let targetYears = [];
    if (period === '3y') targetYears = ['2023', '2024', '2025'];
    else if (period === '5y') targetYears = ['2020', '2022', '2023', '2024', '2025'];
    else if (period === '10y') targetYears = ['2015', '2016', '2017', '2018', '2019', '2022', '2023', '2024', '2025'];

    const vals = targetYears.map(y => is4h ? years[y]?.arb_4h_eur_mw_yr : years[y]?.arb_2h_eur_mw_yr)
                            .filter(v => typeof v === 'number');
    return vals.length ? (vals.reduce((a, b) => a + b, 0) / vals.length) : (is4h ? zSpot.last12m.arb_4h_eur_mw_yr : zSpot.last12m.arb_2h_eur_mw_yr);
  }

  // Anpassar FCR-prisnivå utifrån vald historisk period
  function getPeriodFcrPrice(period) {
    const baseFcr = 6.0; // normaliserat 2025–2026 läge (€/MW/h)
    if (period === '2022') return baseFcr * 2.4; // Historisk peak under energikrisen
    if (period === '3y') return baseFcr * 1.5;   // 2023–2025 övergång
    if (period === '5y') return baseFcr * 1.7;   // Inkluderar 2022–2023
    if (period === '10y') return baseFcr * 1.2;  // Långsiktigt historiskt
    return baseFcr;
  }

  // Finansiell beräkning för en enskild ruta
  function calculateCellFinancials(cell) {
    const mw = parseFloat(rPowerMw.value);
    const mwh = parseFloat(rCapMwh.value);
    const capexPerKwh = parseFloat(rCapexKwh.value);
    const capexPerKw = parseFloat(rCapexKw.value);
    const cableCostPerKm = parseFloat(rCableCost.value) * 1000000;
    const substationBay = parseFloat(rSubBay.value) * 1000000;
    const strategy = selStrategy.value;
    const isSolar = chkSolar.checked;
    const extremeYears = parseInt(selExtremeYears.value, 10);
    const fx = marketData?.fx_eur_sek || 11.25;

    // Filterkontroll: saknar nätägare eller >90% skyddad natur
    if (!cell.dso || cell.prot >= 0.90) {
      return { passed: false, failReason: cell.prot >= 0.90 ? 'Skyddad naturmark' : 'Saknar känd nätägare' };
    }

    // Välj avstånd till relevant spänningsnivå baserat på MW
    let distKm = cell.d130;
    let stationName = cell.n130;
    if (mw <= 5 && cell.d40 !== null) {
      distKm = cell.d40;
      stationName = cell.n40;
    } else if (mw <= 40 && cell.d70 !== null) {
      distKm = cell.d70;
      stationName = cell.n130 || cell.n40;
    }
    if (distKm === null) distKm = 30.0; // fallback

    // 1. CAPEX-komponenter
    const bessEnergyCapex = mwh * 1000 * capexPerKwh;
    const bessPowerCapex = mw * 1000 * capexPerKw;
    const bessCapex = bessEnergyCapex + bessPowerCapex;
    let cableCapex = distKm * cableCostPerKm;
    let stationCapex = substationBay;

    // Solparkshybrid sparar upp till 45 MSEK i delat transformatorfack
    if (isSolar) {
      stationCapex = Math.max(0, stationCapex - 45000000);
    }

    const totalCapex = bessCapex + cableCapex + stationCapex;

    // 2. Marknadsintäkter per elområde baserat på vald historisk datasetperiod
    const z = cell.zone;
    const period = selHistoryDataset?.value || 'last12m';
    const duration = mwh / mw;
    const arbRate = getZoneSpotArbRate(z, period, duration);

    // SvK marknadsdata (mFRR & aFRR)
    let mfrrUp = marketData?.capacity.mfrr_cm?.[z]?.up?.value_eur_mw_yr || 0;
    let mfrrDown = marketData?.capacity.mfrr_cm?.[z]?.down?.value_eur_mw_yr || 0;
    let afrrDown = marketData?.capacity.afrr_cm?.[z]?.down?.value_eur_mw_yr || 0;

    // Om framtidsscenario 2030 är valt (SvK FNA2026: aFRR ökar, mFRR komprimeras något)
    if (selMarketYear.value === '2030') {
      afrrDown = afrrDown * 1.45;
      mfrrUp = mfrrUp * 0.90;
      mfrrDown = mfrrDown * 0.90;
    }

    // Budstrategi-vikter
    let mfrrAccept = 0.40;
    let afrrAccept = 0.30;
    let arbShare = 0.60;

    if (strategy === 'ancillary_focus') {
      mfrrAccept = 0.55;
      afrrAccept = 0.40;
      arbShare = 0.20;
    } else if (strategy === 'arbitrage_focus') {
      mfrrAccept = 0.20;
      afrrAccept = 0.10;
      arbShare = 0.85;
    }

    // FCR-pris anpassat efter vald datasetperiod (fcr_n var t.ex. 64 €/MWh 2022 vs 26,85 € 2025)
    const fcrBasePrice = getPeriodFcrPrice(period);
    const fcrRevSek = fcrBasePrice * 8760 * 0.42 * (mw * 0.5) * fx;

    const mfrrRevSek = (mfrrUp + mfrrDown) * mfrrAccept * mw * fx;
    const afrrRevSek = afrrDown * afrrAccept * mw * fx;
    const arbRevSek = arbRate * arbShare * mw * fx;
    const solarRevSek = isSolar ? 1100000 : 0; // 1,1 MSEK/år i räddad solel

    // Total bruttointäkt
    const grossRev = mfrrRevSek + afrrRevSek + fcrRevSek + arbRevSek + solarRevSek;

    // OPEX: BSP-arvode (3%), O&M (45 000 kr/MW), Nätavgift (40 000 kr/MW), Cykelslitage (80 kr/MWh)
    const bspFee = grossRev * 0.03;
    const omCost = mw * 45000;
    const gridCost = mw * 40000;
    const wearCost = mwh * 280 * 80;

    const normalEbitda = Math.max(0, grossRev - bspFee - omCost - gridCost - wearCost);

    // Kassaflöde över 10 år med extremår (t.ex. Som 2022: 3,95x EBITDA)
    const extremeEbitda = normalEbitda * 3.95;
    const normalYears = 10 - extremeYears;
    const tenYearTotalCf = (normalEbitda * normalYears) + (extremeEbitda * extremeYears);
    const avgYearlyCf = tenYearTotalCf / 10;

    // Payback
    const simplePayback = normalEbitda > 0 ? (totalCapex / normalEbitda) : 999;
    const scenarioPayback = avgYearlyCf > 0 ? (totalCapex / avgYearlyCf) : 999;

    return {
      passed: true,
      totalCapex,
      bessCapex,
      bessEnergyCapex,
      bessPowerCapex,
      cableCapex,
      stationCapex,
      normalEbitda,
      avgYearlyCf,
      tenYearTotalCf,
      simplePayback,
      scenarioPayback,
      distKm,
      stationName,
      breakdown: {
        mfrrRevSek,
        afrrRevSek,
        fcrRevSek,
        arbRevSek,
        solarRevSek
      }
    };
  }

  function recalculateAll() {
    if (!cellsData.length || !marketData) return;

    const targetZone = selZone.value;

    cellsData.forEach(cell => {
      // Om filtrerat på specifik zon, uteslut övriga
      if (targetZone !== 'ALL' && cell.zone !== targetZone) {
        cell.fin = { passed: false, failReason: 'Annat elområde' };
        return;
      }
      cell.fin = calculateCellFinancials(cell);
    });

    renderMapLayers();
    updateRankingList();

    // Uppdatera transparensrutan för spotspreadar baserat på vald datasetperiod
    if (histSpreadBox && marketData?.spot) {
      const p = selHistoryDataset?.value || 'last12m';
      const a4 = parseFloat(rCapMwh.value) / parseFloat(rPowerMw.value) >= 3.5;
      const hType = a4 ? '4h arbitrage' : '2h arbitrage';
      const se4 = Math.round(getZoneSpotArbRate('SE4', p, a4 ? 4 : 2)).toLocaleString('sv-SE');
      const se3 = Math.round(getZoneSpotArbRate('SE3', p, a4 ? 4 : 2)).toLocaleString('sv-SE');
      const se2 = Math.round(getZoneSpotArbRate('SE2', p, a4 ? 4 : 2)).toLocaleString('sv-SE');
      const se1 = Math.round(getZoneSpotArbRate('SE1', p, a4 ? 4 : 2)).toLocaleString('sv-SE');
      histSpreadBox.innerHTML = `
        SE4: ${se4} €/MW/år (${hType})<br>
        SE3: ${se3} €/MW/år (${hType})<br>
        SE2: ${se2} €/MW/år (${hType})<br>
        SE1: ${se1} €/MW/år (${hType})
      `;
    }

    // Uppdatera vald ruta om den finns, annars välj bästa
    let target = cellsData.find(c => c.id === selectedCellId && c.fin.passed);
    if (!target) target = findBestCell();
    if (target) {
      selectedCellId = target.id;
      updateSelectedDetail(target);
    }
  }

  function findBestCell() {
    const valid = cellsData.filter(c => c.fin && c.fin.passed);
    if (!valid.length) return null;
    return valid.reduce((best, cur) => cur.fin.scenarioPayback < best.fin.scenarioPayback ? cur : best, valid[0]);
  }

  function getPaybackColor(pb) {
    if (pb <= 7.0) return '#10b981';
    if (pb <= 10.0) return '#fbbf24';
    return '#ef4444';
  }

  function renderMapLayers() {
    cellLayerGroup.clearLayers();

    cellsData.forEach(cell => {
      if (!cell.fin || !cell.fin.passed) return;

      const pb = cell.fin.scenarioPayback;
      const color = getPaybackColor(pb);
      const isSelected = (cell.id === selectedCellId);

      const poly = L.polygon(cell.poly, {
        color: isSelected ? '#38bdf8' : color,
        weight: isSelected ? 3 : 1,
        opacity: isSelected ? 1 : 0.65,
        fillColor: color,
        fillOpacity: isSelected ? 0.6 : (pb <= 7.0 ? 0.45 : 0.25)
      });

      poly.on('click', () => {
        selectedCellId = cell.id;
        renderMapLayers();
        updateSelectedDetail(cell);
      });

      cellLayerGroup.addLayer(poly);
    });
  }

  function updateRankingList() {
    listTopSites.innerHTML = '';
    const valid = cellsData.filter(c => c.fin && c.fin.passed).sort((a, b) => a.fin.scenarioPayback - b.fin.scenarioPayback);
    
    // Välj de 5 främsta unika platserna (prioritera olika nätägare eller skilda geografiska kluster)
    const top5 = [];
    const seenDso = new Set();
    for (const c of valid) {
      const key = `${c.zone}_${c.dso}`;
      if (!seenDso.has(key) || top5.length < 3) {
        seenDso.add(key);
        top5.push(c);
        if (top5.length >= 5) break;
      }
    }
    // Om färre än 5 unika nätägare hittades, fyll på med de absolut bästa rutorna
    if (top5.length < 5) {
      for (const c of valid) {
        if (!top5.includes(c)) {
          top5.push(c);
          if (top5.length >= 5) break;
        }
      }
    }

    top5.forEach((c, idx) => {
      const li = document.createElement('li');
      li.innerHTML = `
        <span style="color:var(--muted);">${idx + 1}.</span>
        <div>
          <strong>${c.dso}</strong> (${c.zone})
          <div class="m">${c.fin.distKm.toFixed(1)} km kabel · ${Math.round(c.fin.normalEbitda / 1000000)} M EBITDA</div>
        </div>
        <div class="s" style="color:${getPaybackColor(c.fin.scenarioPayback)}; font-weight:700;">
          ${c.fin.scenarioPayback.toFixed(1)} år
        </div>
      `;
      li.addEventListener('click', () => {
        selectedCellId = c.id;
        renderMapLayers();
        updateSelectedDetail(c);
        map.setView(c.c, 9);
      });
      listTopSites.appendChild(li);
    });

    if (top5.length > 0) {
      const best = top5[0];
      txtBestLoc.textContent = `Ruta ${best.id} (${best.zone} · ${best.dso})`;
      txtBestReason.textContent = `Återbetalning på ${best.fin.scenarioPayback.toFixed(1)} år. Endast ${best.fin.distKm.toFixed(1)} km kabelanslutning minimerar kabel-CAPEX i en zon med ${Math.round(best.fin.normalEbitda / 1000000)} MSEK i årlig EBITDA.`;
    }
  }

  function updateSelectedDetail(cell) {
    const f = cell.fin;
    if (!f || !f.passed) return;

    kpiPayback.textContent = f.scenarioPayback.toFixed(1).replace('.', ',') + ' år';
    kpiPayback.style.color = getPaybackColor(f.scenarioPayback);
    kpiPaybackSub.textContent = `Normalår: ${f.simplePayback.toFixed(1).replace('.', ',')} år`;

    kpiCapex.textContent = Math.round(f.totalCapex / 1000000) + ' MSEK';
    kpiCapexSub.textContent = `Varav kabel: ${(f.cableCapex / 1000000).toFixed(1)} MSEK`;

    kpiEbitda.textContent = (f.normalEbitda / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
    kpi10yrCf.textContent = Math.round(f.tenYearTotalCf / 1000000) + ' MSEK';

    badgeRoi.textContent = f.scenarioPayback.toFixed(1) + ' år';
    badgeRoi.className = `bs-badge ${f.scenarioPayback <= 7.0 ? 'green' : (f.scenarioPayback <= 10.0 ? 'yellow' : 'red')}`;

    detZone.textContent = cell.zone;
    detDso.textContent = cell.dso;
    detStation.textContent = f.stationName || '–';
    detDist.textContent = f.distKm.toFixed(1) + ' km';
    detEnergyCapex.textContent = (f.bessEnergyCapex / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
    detPowerCapex.textContent = (f.bessPowerCapex / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
    detStationCapex.textContent = (f.stationCapex / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
    detCableCost.textContent = (f.cableCapex / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
    detProt.textContent = `${Math.round(cell.prot * 100)} % ${cell.prot_name ? '(' + cell.prot_name + ')' : ''}`;
    detFlex.textContent = cell.flex?.length ? cell.flex.join(', ') : 'Ej aktiv flexmarknad';

    revMfrr.textContent = (f.breakdown.mfrrRevSek / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
    revAfrr.textContent = (f.breakdown.afrrRevSek / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
    revFcr.textContent = (f.breakdown.fcrRevSek / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
    revArb.textContent = (f.breakdown.arbRevSek / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
    revSolar.textContent = (f.breakdown.solarRevSek / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
  }

  async function loadData() {
    try {
      const [rCells, rMarket, rMeta] = await Promise.all([
        fetch('data/bess-map/cells.json'),
        fetch('data/bess-map/market.json'),
        fetch('data/bess-map/meta.json')
      ]);

      cellsData = await rCells.json();
      marketData = await rMarket.json();
      metaData = await rMeta.json();

      mapStatus.textContent = `${cellsData.length.toLocaleString('sv-SE')} nätanalysrutor och 1 132 transformatorstationer laddade.`;

      // Bakgrundsledningar
      try {
        const rLines = await fetch('data/bess-map/lines.json');
        const lines = await rLines.json();
        lines.forEach(ln => {
          L.polyline(ln.c, {
            color: ln.kv >= 380 ? '#f43f5e' : '#38bdf8',
            weight: 1.2,
            opacity: 0.4
          }).addTo(linesLayerGroup);
        });
      } catch (e) {
        console.warn('Could not load lines', e);
      }

      recalculateAll();
    } catch (err) {
      console.error('Error loading Studio data:', err);
      mapStatus.textContent = 'Fel vid laddning av data: ' + err.message;
    }
  }

  function init() {
    initModal();
    initInputs();
    initMap();
    loadData();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
