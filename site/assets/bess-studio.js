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
  let dispatchData = null;
  let map = null;
  let cellLayerGroup = null;
  let subsLayerGroup = null;
  let linesLayerGroup = null;
  let concLayerGroup = null;
  let flexLayerGroup = null;
  let selectedCellId = null;

  // Layer Checkboxes
  const lyrCells = document.getElementById('bs-lyr-cells');
  const lyrLines = document.getElementById('bs-lyr-lines');
  const lyrSubs = document.getElementById('bs-lyr-subs');
  const lyrConc = document.getElementById('bs-lyr-conc');
  const lyrFlex = document.getElementById('bs-lyr-flex');
  const lyrFailed = document.getElementById('bs-lyr-failed');

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

  // Dispatch Inspection Modal
  const modalDispatch = document.getElementById('modal-dispatch');
  const btnInspectDispatch = document.getElementById('btn-inspect-dispatch');
  const btnCloseDispatch = document.getElementById('btn-close-dispatch');
  const btnModalDispatchOk = document.getElementById('btn-modal-dispatch-ok');
  const dispKpiEpoch = document.getElementById('disp-kpi-epoch');
  const dispKpiCycles = document.getElementById('disp-kpi-cycles');
  const dispKpiRevenue = document.getElementById('disp-kpi-revenue');
  const dispTableBody = document.getElementById('disp-table-body');

  function initModal() {
    btnOpenMethod.addEventListener('click', () => modalMethod.style.display = 'flex');
    btnCloseMethod.addEventListener('click', () => modalMethod.style.display = 'none');
    btnModalOk.addEventListener('click', () => modalMethod.style.display = 'none');
    modalMethod.addEventListener('click', (e) => {
      if (e.target === modalMethod) modalMethod.style.display = 'none';
    });

    if (btnInspectDispatch) {
      btnInspectDispatch.addEventListener('click', openDispatchModal);
    }
    if (btnCloseDispatch) {
      btnCloseDispatch.addEventListener('click', () => modalDispatch.style.display = 'none');
    }
    if (btnModalDispatchOk) {
      btnModalDispatchOk.addEventListener('click', () => modalDispatch.style.display = 'none');
    }
    if (modalDispatch) {
      modalDispatch.addEventListener('click', (e) => {
        if (e.target === modalDispatch) modalDispatch.style.display = 'none';
      });
    }
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
    flexLayerGroup = L.layerGroup().addTo(map);
    concLayerGroup = L.layerGroup(); // Avstängt per default

    // Koppla av/på-växling för kartlager
    if (lyrCells) lyrCells.addEventListener('change', () => lyrCells.checked ? map.addLayer(cellLayerGroup) : map.removeLayer(cellLayerGroup));
    if (lyrLines) lyrLines.addEventListener('change', () => lyrLines.checked ? map.addLayer(linesLayerGroup) : map.removeLayer(linesLayerGroup));
    if (lyrSubs) lyrSubs.addEventListener('change', () => lyrSubs.checked ? map.addLayer(subsLayerGroup) : map.removeLayer(subsLayerGroup));
    if (lyrConc) lyrConc.addEventListener('change', () => lyrConc.checked ? map.addLayer(concLayerGroup) : map.removeLayer(concLayerGroup));
    if (lyrFlex) lyrFlex.addEventListener('change', () => lyrFlex.checked ? map.addLayer(flexLayerGroup) : map.removeLayer(flexLayerGroup));
    if (lyrFailed) lyrFailed.addEventListener('change', renderMapLayers);
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

    // 1. CAPEX-komponenter (med 1,3x realistisk kabeldragningsfaktor för terräng & vägsträckning)
    const bessEnergyCapex = mwh * 1000 * capexPerKwh;
    const bessPowerCapex = mw * 1000 * capexPerKw;
    const bessCapex = bessEnergyCapex + bessPowerCapex;
    const routingFactor = 1.30; // Realistisk schakt- och terrängsträckning
    let cableCapex = distKm * routingFactor * cableCostPerKm;
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
    const dKey = duration >= 3.0 ? '4.0h' : (duration <= 1.5 ? '1.0h' : '2.0h');

    let mfrrRevSek = 0.0;
    let afrrRevSek = 0.0;
    let fcrRevSek = 0.0;
    let arbRevSek = 0.0;

    // Om kronologisk 8 760h dispatch-matris finns laddad: använd exakt simultan dispatch
    const epochDispatch = dispatchData?.epochs?.[period]?.[z]?.[dKey];
    if (epochDispatch) {
      arbRevSek = epochDispatch.spot_rev_eur_mw_yr * mw * fx;
      mfrrRevSek = epochDispatch.mfrr_rev_eur_mw_yr * mw * fx;
      afrrRevSek = epochDispatch.afrr_rev_eur_mw_yr * mw * fx;
      fcrRevSek = epochDispatch.fcr_rev_eur_mw_yr * mw * fx;

      // Strategijustering om användaren valt renodlat fokus
      if (strategy === 'ancillary_focus') {
        mfrrRevSek *= 1.20;
        afrrRevSek *= 1.15;
        arbRevSek *= 0.35;
      } else if (strategy === 'arbitrage_focus') {
        arbRevSek *= 1.35;
        mfrrRevSek *= 0.40;
        afrrRevSek *= 0.30;
      }
    } else {
      // Fallback till formelbaserad modell om dispatchData inte laddats än
      const arbRate = getZoneSpotArbRate(z, period, duration);
      let mfrrUp = marketData?.capacity.mfrr_cm?.[z]?.up?.value_eur_mw_yr || 0;
      let mfrrDown = marketData?.capacity.mfrr_cm?.[z]?.down?.value_eur_mw_yr || 0;
      let afrrDown = marketData?.capacity.afrr_cm?.[z]?.down?.value_eur_mw_yr || 0;

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

      const fcrBasePrice = getPeriodFcrPrice(period);
      fcrRevSek = fcrBasePrice * 8760 * 0.42 * (mw * 0.5) * fx;
      mfrrRevSek = (mfrrUp + mfrrDown) * mfrrAccept * mw * fx;
      afrrRevSek = afrrDown * afrrAccept * mw * fx;
      arbRevSek = arbRate * arbShare * mw * fx;
    }

    // Framtidsutsikt 2030 (SvK FNA2026: aFRR ökar, mFRR komprimeras något)
    if (selMarketYear.value === '2030') {
      afrrRevSek *= 1.45;
      mfrrRevSek *= 0.90;
    }

    const solarRevSek = isSolar ? 1100000 : 0; // 1,1 MSEK/år i räddad solel
    const grossRev = mfrrRevSek + afrrRevSek + fcrRevSek + arbRevSek + solarRevSek;

    // OPEX: Differentierad nätavgift baserat på DSO-kategori (35–55 kkr/MW beroende på nätägare)
    let dsoTariffRate = 40000;
    const dsoName = (cell.dso || '').toLowerCase();
    if (dsoName.includes('vattenfall') || dsoName.includes('ellevio')) {
      dsoTariffRate = 46000; // Stora regionnätsägare med fastare effekttariff
    } else if (dsoName.includes('e.on')) {
      dsoTariffRate = 48000;
    } else if (dsoName.includes('skellefteå') || dsoName.includes('luleå') || dsoName.includes('umeå')) {
      dsoTariffRate = 34000; // Norrländska kommunala nät med lägre nätkostnad
    }

    const bspFee = grossRev * 0.03;
    const omCost = mw * 45000;
    const gridCost = mw * dsoTariffRate;
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
    const showFailed = lyrFailed ? lyrFailed.checked : false;

    cellsData.forEach(cell => {
      if (!cell.fin) return;
      if (!cell.fin.passed && !showFailed) return;

      const isPassed = cell.fin.passed;
      const pb = isPassed ? cell.fin.scenarioPayback : 999;
      const color = isPassed ? getPaybackColor(pb) : '#64748b';
      const isSelected = (cell.id === selectedCellId);

      const poly = L.polygon(cell.poly, {
        color: isSelected ? '#38bdf8' : (isPassed ? color : '#ef4444'),
        weight: isSelected ? 3 : 1,
        opacity: isSelected ? 1 : (isPassed ? 0.65 : 0.4),
        fillColor: color,
        fillOpacity: isSelected ? 0.6 : (isPassed ? (pb <= 7.0 ? 0.45 : 0.25) : 0.12)
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

  function openDispatchModal() {
    if (!modalDispatch) return;
    const cell = cellsData.find(c => c.id === selectedCellId) || cellsData[0];
    const z = cell?.zone || 'SE4';
    const period = selHistoryDataset?.value || 'last12m';
    const mw = parseFloat(rPowerMw.value);
    const mwh = parseFloat(rCapMwh.value);
    const duration = mw > 0 ? (mwh / mw) : 2.0;
    const dKey = duration >= 3.0 ? '4.0h' : (duration <= 1.5 ? '1.0h' : '2.0h');

    const epochDisp = dispatchData?.epochs?.[period]?.[z]?.[dKey];

    if (dispKpiEpoch) {
      const periodNames = {
        'last12m': 'Senaste 12 månaderna (Aktuell marknad)',
        '3y': '3 års snitt (2023–2026)',
        '5y': '5 års snitt (2021–2026)',
        '10y': '10 års snitt (Nord Pool 2015–2026)',
        '2022': 'Extremåret 2022 (Energikrisen)',
        '2024': 'Konservativt år 2024'
      };
      dispKpiEpoch.textContent = `${periodNames[period] || period} · ${z} (${dKey})`;
    }

    if (epochDisp) {
      if (dispKpiCycles) dispKpiCycles.textContent = `${epochDisp.equivalent_cycles} cykler/år`;
      if (dispKpiRevenue) dispKpiRevenue.textContent = `${Math.round(epochDisp.total_rev_eur_mw_yr).toLocaleString('sv-SE')} €/MW/år`;

      if (dispTableBody) {
        dispTableBody.innerHTML = '';
        const week = epochDisp.sample_week || [];
        // Visa första 48 timmarna av sample week i tabellen
        week.slice(0, 48).forEach((row, i) => {
          const tr = document.createElement('tr');
          tr.style.borderBottom = '1px solid rgba(255,255,255,0.05)';
          tr.innerHTML = `
            <td style="padding:6px 8px; text-align:left; color:var(--muted);">T+${i + 1}h</td>
            <td style="padding:6px 8px; font-weight:600;">${row.spot.toFixed(1)}</td>
            <td style="padding:6px 8px; color:#38bdf8;">${row.soc_pct.toFixed(0)} %</td>
            <td style="padding:6px 8px; color:#34d399;">${row.spot_charge_mw > 0 ? (row.spot_charge_mw * mw).toFixed(1) + ' MW' : '–'}</td>
            <td style="padding:6px 8px; color:#fbbf24;">${row.spot_discharge_mw > 0 ? (row.spot_discharge_mw * mw).toFixed(1) + ' MW' : '–'}</td>
            <td style="padding:6px 8px; color:#38bdf8;">${row.mfrr_up_mw > 0 ? (row.mfrr_up_mw * mw).toFixed(1) + ' MW' : '–'}</td>
            <td style="padding:6px 8px; color:#a855f7;">${row.afrr_down_mw > 0 ? (row.afrr_down_mw * mw).toFixed(1) + ' MW' : '–'}</td>
          `;
          dispTableBody.appendChild(tr);
        });
      }
    }

    modalDispatch.style.display = 'flex';
  }

  async function loadData() {
    try {
      const [rCells, rMarket, rMeta, rDisp] = await Promise.all([
        fetch('data/bess-map/cells.json'),
        fetch('data/bess-map/market.json'),
        fetch('data/bess-map/meta.json'),
        fetch('data/bess-map/dispatch_backtest.json').catch(() => null)
      ]);

      cellsData = await rCells.json();
      marketData = await rMarket.json();
      metaData = await rMeta.json();
      if (rDisp && rDisp.ok) {
        dispatchData = await rDisp.json();
      }

      mapStatus.textContent = `${cellsData.length.toLocaleString('sv-SE')} nätanalysrutor och 1 132 transformatorstationer laddade.`;

      // 1. Högspänningsledningar
      try {
        const rLines = await fetch('data/bess-map/lines.json');
        const lines = await rLines.json();
        lines.forEach(ln => {
          L.polyline(ln.c, {
            color: ln.kv >= 380 ? '#f43f5e' : (ln.kv >= 220 ? '#fb923c' : '#38bdf8'),
            weight: 1.2,
            opacity: 0.45
          }).addTo(linesLayerGroup);
        });
      } catch (e) {
        console.warn('Could not load lines', e);
      }

      // 2. Transformatorstationer (>= 40 kV)
      try {
        const rSubs = await fetch('data/bess-map/substations.json');
        const subs = await rSubs.json();
        subs.forEach(s => {
          const circle = L.circleMarker([s[0], s[1]], {
            radius: s[2] >= 220 ? 4.5 : 3.2,
            color: s[2] >= 220 ? '#fb923c' : '#38bdf8',
            fillColor: '#ffffff',
            fillOpacity: 0.85,
            weight: 1
          });
          circle.bindPopup(`<strong>${s[3] || 'Transformatorstation'}</strong><br>${s[2]} kV<br>${s[4] || ''}`);
          circle.addTo(subsLayerGroup);
        });
      } catch (e) {
        console.warn('Could not load substations', e);
      }

      // 3. Lokala flexmarknader
      try {
        const rFlex = await fetch('data/bess-map/flex.json');
        const flexList = await rFlex.json();
        flexList.forEach(f => {
          const c = L.circle([f.lat, f.lon], {
            radius: f.radius_km * 1000,
            color: '#34d399',
            fillColor: '#34d399',
            fillOpacity: 0.12,
            weight: 1.5,
            dashArray: '3, 4'
          });
          c.bindPopup(`<strong>Lokal flexmarknad: ${f.name}</strong><br>${f.area}<br>${f.operator}`);
          c.addTo(flexLayerGroup);
        });
      } catch (e) {
        console.warn('Could not load flex', e);
      }

      // 4. Områdeskoncessioner (Ei DSO-polygoner)
      try {
        const rConc = await fetch('data/bess-map/concessions.json');
        const concGeo = await rConc.json();
        L.geoJSON(concGeo, {
          style: {
            color: '#a855f7',
            weight: 1.5,
            dashArray: '2, 3',
            fillColor: '#a855f7',
            fillOpacity: 0.08
          },
          onEachFeature: (feat, layer) => {
            const p = feat.properties || {};
            layer.bindPopup(`<strong>Nätkoncession: ${p.owner || 'Okänd'}</strong><br>ID: ${p.id || '–'}`);
          }
        }).addTo(concLayerGroup);
      } catch (e) {
        console.warn('Could not load concessions', e);
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
