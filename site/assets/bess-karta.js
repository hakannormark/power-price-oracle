/**
 * BESS Screening Karta Sverige
 * Interaktiv screening av batterilager på 10x10 km rutnät.
 * Väger samman elområdesintäkter, anslutningsavstånd, nätägare, naturskydd och lokal flex.
 */

(function () {
  'use strict';

  // Viktprofiler (normaliserade vikter 0-1)
  const PROFILES = {
    balanced: {
      name: 'Balanserad',
      weights: { conn: 0.35, ancillary: 0.30, arb: 0.20, flex: 0.10, land: 0.05 },
      desc: 'Jämn fördelning mellan elmarknadsintäkt, stödtjänster och anslutningsnärhet.'
    },
    capacity: {
      name: 'Kapacitets-BESS (Stödtjänster)',
      weights: { conn: 0.35, ancillary: 0.45, arb: 0.10, flex: 0.05, land: 0.05 },
      desc: 'Fokuserar på mFRR- och aFRR-kapacitetsmarknaderna och anslutning.'
    },
    arbitrage: {
      name: 'Arbitrage / Hybrid sol-vind',
      weights: { conn: 0.30, ancillary: 0.15, arb: 0.45, flex: 0.05, land: 0.05 },
      desc: 'Prioriterar spotprisvolatilitet och dygnsspread för laddning/urladdning.'
    },
    flex: {
      name: 'Lokal flexibilitet',
      weights: { conn: 0.30, ancillary: 0.20, arb: 0.15, flex: 0.30, land: 0.05 },
      desc: 'Premierar områden med aktiva lokala flexmarknader (sthlmflex, Effekthandel Väst etc).'
    }
  };

  // State
  let cellsData = [];
  let marketData = null;
  let metaData = null;
  let flexData = [];
  let map = null;

  // Layer groups
  let cellLayerGroup = null;
  let zoneLayerGroup = null;
  let linesLayerGroup = null;
  let subsLayerGroup = null;
  let concLayerGroup = null;
  let flexLayerGroup = null;

  let selectedCellId = null;

  // DOM Elements
  const elSize = document.getElementById('bm-size');
  const elProfile = document.getElementById('bm-profile');
  const elRadius = document.getElementById('bm-radius');
  const elRadiusVal = document.getElementById('bm-radius-val');
  const elDuration = document.getElementById('bm-duration');
  const elMw = document.getElementById('bm-mw');

  const elCapMfrr = document.getElementById('cap-mfrr');
  const elCapMfrrVal = document.getElementById('cap-mfrr-val');
  const elCapAfrr = document.getElementById('cap-afrr');
  const elCapAfrrVal = document.getElementById('cap-afrr-val');
  const elCapArb = document.getElementById('cap-arb');
  const elCapArbVal = document.getElementById('cap-arb-val');
  const elCapStack = document.getElementById('cap-stack');
  const elCapStackVal = document.getElementById('cap-stack-val');

  const elTopList = document.getElementById('bm-top');
  const elStats = document.getElementById('bm-stats');
  const elDetail = document.getElementById('bm-detail');
  const elZones = document.getElementById('bm-zones');
  const elHistory = document.getElementById('bm-history');
  const elWeightsTable = document.getElementById('bm-weights');
  const elSourcesTable = document.getElementById('bm-sources');
  const elMapSources = document.getElementById('bm-map-sources');

  // Layer toggles
  const lyrCells = document.getElementById('lyr-cells');
  const lyrZones = document.getElementById('lyr-zones');
  const lyrLines = document.getElementById('lyr-lines');
  const lyrSubs = document.getElementById('lyr-subs');
  const lyrConc = document.getElementById('lyr-conc');
  const lyrFlex = document.getElementById('lyr-flex');
  const lyrFailed = document.getElementById('lyr-failed');

  function initSliders() {
    elRadius.addEventListener('input', () => {
      elRadiusVal.textContent = elRadius.value + ' km';
      updateScores();
    });
    elCapMfrr.addEventListener('input', () => {
      elCapMfrrVal.textContent = Math.round(elCapMfrr.value * 100) + ' %';
      updateScores();
    });
    elCapAfrr.addEventListener('input', () => {
      elCapAfrrVal.textContent = Math.round(elCapAfrr.value * 100) + ' %';
      updateScores();
    });
    elCapArb.addEventListener('input', () => {
      elCapArbVal.textContent = Math.round(elCapArb.value * 100) + ' %';
      updateScores();
    });
    elCapStack.addEventListener('input', () => {
      elCapStackVal.textContent = Math.round(elCapStack.value * 100) + ' %';
      updateScores();
    });

    elSize.addEventListener('change', updateScores);
    elProfile.addEventListener('change', () => {
      renderWeightsTable();
      updateScores();
    });
    elDuration.addEventListener('change', () => {
      document.getElementById('bm-hist-dur').textContent = elDuration.value + ' h';
      renderZoneCards();
      renderHistoryTable();
      updateScores();
    });
    elMw.addEventListener('input', () => {
      renderZoneCards();
      if (selectedCellId) showCellDetail(selectedCellId);
    });

    elCapMfrrVal.textContent = Math.round(elCapMfrr.value * 100) + ' %';
    elCapAfrrVal.textContent = Math.round(elCapAfrr.value * 100) + ' %';
    elCapArbVal.textContent = Math.round(elCapArb.value * 100) + ' %';
    elCapStackVal.textContent = Math.round(elCapStack.value * 100) + ' %';
    elRadiusVal.textContent = elRadius.value + ' km';
  }

  function initMap() {
    map = L.map('bm-map', {
      center: [62.0, 15.5],
      zoom: 5,
      minZoom: 4,
      maxZoom: 13
    });

    // Standard open tiles without API keys
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> bidragsgivare',
      subdomains: 'abc',
      maxZoom: 18
    }).addTo(map);

    zoneLayerGroup = L.layerGroup().addTo(map);
    linesLayerGroup = L.layerGroup().addTo(map);
    cellLayerGroup = L.layerGroup().addTo(map);
    flexLayerGroup = L.layerGroup().addTo(map);
    subsLayerGroup = L.layerGroup();
    concLayerGroup = L.layerGroup();

    // Setup toggle listeners
    lyrCells.addEventListener('change', () => lyrCells.checked ? map.addLayer(cellLayerGroup) : map.removeLayer(cellLayerGroup));
    lyrZones.addEventListener('change', () => lyrZones.checked ? map.addLayer(zoneLayerGroup) : map.removeLayer(zoneLayerGroup));
    lyrLines.addEventListener('change', () => lyrLines.checked ? map.addLayer(linesLayerGroup) : map.removeLayer(linesLayerGroup));
    lyrSubs.addEventListener('change', () => lyrSubs.checked ? map.addLayer(subsLayerGroup) : map.removeLayer(subsLayerGroup));
    lyrConc.addEventListener('change', () => lyrConc.checked ? map.addLayer(concLayerGroup) : map.removeLayer(concLayerGroup));
    lyrFlex.addEventListener('change', () => lyrFlex.checked ? map.addLayer(flexLayerGroup) : map.removeLayer(flexLayerGroup));
    lyrFailed.addEventListener('change', updateScores);

    // Legend
    const legend = L.control({ position: 'bottomright' });
    legend.onAdd = function () {
      const div = L.DomUtil.create('div', 'bm-legend');
      div.innerHTML = `
        <strong>Screeningpoäng</strong><br>
        <i style="background:#10b981"></i> &ge; 0,70 Värd förfrågan<br>
        <i style="background:#fbbf24"></i> 0,40 &ndash; 0,69 Möjlig<br>
        <i style="background:#64748b"></i> &lt; 0,40 Svag<br>
        <i style="background:#ef4444; opacity:0.6"></i> Exkluderad (filter)
      `;
      return div;
    };
    legend.addTo(map);
  }

  // Räkna ut poäng för alla celler
  function computeCellScores() {
    const size = elSize.value; // 'local', 'region', 'tso'
    const profileKey = elProfile.value;
    const weights = PROFILES[profileKey].weights;
    const maxDist = parseFloat(elRadius.value);
    const durHours = parseInt(elDuration.value, 10);

    const capMfrr = parseFloat(elCapMfrr.value);
    const capAfrr = parseFloat(elCapAfrr.value);
    const capArb = parseFloat(elCapArb.value);
    const capStack = parseFloat(elCapStack.value);

    // Hitta maxvärden för normalisering per zon
    const zoneMarket = {};
    const zones = ['SE1', 'SE2', 'SE3', 'SE4'];
    let maxAncillaryTotal = 0;
    let maxArbTotal = 0;

    zones.forEach(z => {
      const spot = marketData.spot[z] ? marketData.spot[z].last12m : null;
      const arbRaw = spot ? (durHours === 4 ? spot.arb_4h_eur_mw_yr : spot.arb_2h_eur_mw_yr) : 0;
      const arbValue = arbRaw * capArb;

      // Stödtjänster: mFRR upp+ned och aFRR upp+ned
      const mfrrUp = (marketData.capacity.mfrr_cm?.[z]?.up?.value_eur_mw_yr || 0);
      const mfrrDown = (marketData.capacity.mfrr_cm?.[z]?.down?.value_eur_mw_yr || 0);
      const mfrrTotal = (mfrrUp + mfrrDown) * capMfrr;

      const afrrUp = (marketData.capacity.afrr_cm?.[z]?.up?.value_eur_mw_yr || 0);
      const afrrDown = (marketData.capacity.afrr_cm?.[z]?.down?.value_eur_mw_yr || 0);
      const afrrTotal = (afrrUp + afrrDown) * capAfrr;

      const bestAncillary = Math.max(mfrrTotal, afrrTotal);

      // Total intäktsstack (bästa strömmen + restandel av andra)
      const primary = Math.max(bestAncillary, arbValue);
      const secondary = Math.min(bestAncillary, arbValue) * capStack;
      const stackEstimate = primary + secondary;

      zoneMarket[z] = {
        arbValue,
        bestAncillary,
        stackEstimate
      };

      if (bestAncillary > maxAncillaryTotal) maxAncillaryTotal = bestAncillary;
      if (arbValue > maxArbTotal) maxArbTotal = arbValue;
    });

    cellsData.forEach(cell => {
      // 1. Hårda filter
      let failReason = null;

      // Filter: Känd nätägare
      if (!cell.dso) {
        failReason = 'Ingen känd nätkoncession/ägare i området';
      }

      // Filter: Spänningskrav och avstånd till station
      let dist = null;
      let nearestName = '';
      if (size === 'local') {
        dist = cell.d40;
        nearestName = cell.n40;
        if (dist === null || dist > maxDist) {
          failReason = `Ingen station ≥ 40 kV inom ${maxDist} km (närmaste: ${dist !== null ? dist + ' km' : 'saknas'})`;
        }
      } else if (size === 'region') {
        dist = cell.d70 !== null ? cell.d70 : cell.d130;
        nearestName = cell.n130 || cell.n40;
        if (dist === null || dist > maxDist) {
          failReason = `Ingen station ≥ 70 kV inom ${maxDist} km (närmaste: ${dist !== null ? dist + ' km' : 'saknas'})`;
        }
      } else { // tso (>40 MW)
        dist = cell.d130;
        nearestName = cell.n130;
        if (dist === null || dist > maxDist) {
          failReason = `Ingen station ≥ 130 kV inom ${maxDist} km (närmaste: ${dist !== null ? dist + ' km' : 'saknas'})`;
        }
      }

      // Filter: Strikt skyddad mark (>= 90%)
      if (!failReason && cell.prot >= 0.90) {
        failReason = `Exkluderad naturmark: ${Math.round(cell.prot * 100)} % strikt skydd (${cell.prot_name || 'naturreservat/nationalpark'})`;
      }

      cell.failReason = failReason;
      cell.passed = !failReason;

      if (!cell.passed) {
        cell.score = 0;
        cell.subScores = { conn: 0, ancillary: 0, arb: 0, flex: 0, land: 0 };
        return;
      }

      // 2. Delpoäng 0-1
      // Anslutning: 1 vid 0 km, 0 vid maxDist, plus bonus för stationskluster vid 130 kV
      let sConn = Math.max(0, 1 - (dist / maxDist));
      if (cell.k130_15 && cell.k130_15 >= 2) {
        sConn = Math.min(1.0, sConn + 0.10); // Redundansbonus
      }

      // Stödtjänster (zon-nivå)
      const zm = zoneMarket[cell.zone] || { bestAncillary: 0, arbValue: 0, stackEstimate: 0 };
      const sAncillary = maxAncillaryTotal > 0 ? (zm.bestAncillary / maxAncillaryTotal) : 0;

      // Arbitrage (zon-nivå)
      const sArb = maxArbTotal > 0 ? (zm.arbValue / maxArbTotal) : 0;

      // Lokal flexmarknad (1 om träff, annars 0)
      const sFlex = (cell.flex && cell.flex.length > 0) ? 1.0 : 0.0;

      // Markrisk (1 minus skyddad markandel och halva vattenskyddet)
      const sLand = Math.max(0, 1 - cell.prot - (cell.water * 0.5));

      // Sammanvägd poäng
      const total = (
        sConn * weights.conn +
        sAncillary * weights.ancillary +
        sArb * weights.arb +
        sFlex * weights.flex +
        sLand * weights.land
      );

      cell.score = Math.round(total * 100) / 100;
      cell.subScores = {
        conn: Math.round(sConn * 100) / 100,
        ancillary: Math.round(sAncillary * 100) / 100,
        arb: Math.round(sArb * 100) / 100,
        flex: Math.round(sFlex * 100) / 100,
        land: Math.round(sLand * 100) / 100
      };
      cell.stackEstimate = zm.stackEstimate;
      cell.nearestStationText = nearestName;
      cell.usedDist = dist;
    });
  }

  function getScoreColor(score, passed) {
    if (!passed) return '#ef4444';
    if (score >= 0.70) return '#10b981';
    if (score >= 0.40) return '#fbbf24';
    return '#64748b';
  }

  function renderCellsLayer() {
    cellLayerGroup.clearLayers();
    const showFailed = lyrFailed.checked;

    cellsData.forEach(cell => {
      if (!cell.passed && !showFailed) return;

      const color = getScoreColor(cell.score, cell.passed);
      const isSelected = (cell.id === selectedCellId);

      const poly = L.polygon(cell.poly, {
        color: isSelected ? '#38bdf8' : color,
        weight: isSelected ? 3 : (cell.passed ? 1 : 0.5),
        opacity: isSelected ? 1 : (cell.passed ? 0.7 : 0.3),
        fillColor: color,
        fillOpacity: isSelected ? 0.6 : (cell.passed ? (cell.score >= 0.7 ? 0.45 : 0.28) : 0.12)
      });

      poly.on('click', () => {
        selectedCellId = cell.id;
        renderCellsLayer();
        showCellDetail(cell.id);
      });

      cellLayerGroup.addLayer(poly);
    });
  }

  function renderTopList() {
    elTopList.innerHTML = '';
    const passedCells = cellsData.filter(c => c.passed).sort((a, b) => b.score - a.score);

    elStats.textContent = `${passedCells.length} av ${cellsData.length} rutor klarade filtren (${cellsData.length - passedCells.length} föll bort).`;

    const top15 = passedCells.slice(0, 15);
    top15.forEach((c, idx) => {
      const li = document.createElement('li');
      li.innerHTML = `
        <span style="color:var(--muted);">${idx + 1}.</span>
        <div>
          <strong>${c.dso || 'Okänd nätägare'}</strong> (${c.zone})
          <div class="m">${c.usedDist !== null ? c.usedDist + ' km till station' : ''} ${c.flex?.length ? '· Flexmarknad' : ''}</div>
        </div>
        <div class="s">${c.score.toFixed(2)}</div>
      `;
      li.addEventListener('click', () => {
        selectedCellId = c.id;
        renderCellsLayer();
        showCellDetail(c.id);
        map.setView(c.c, 9);
      });
      elTopList.appendChild(li);
    });
  }

  function showCellDetail(cellId) {
    const c = cellsData.find(x => x.id === cellId);
    if (!c) return;

    const mw = parseFloat(elMw.value) || 20;
    const fx = marketData.fx_eur_sek || 11.25;

    let html = `
      <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:0.75rem;">
        <div>
          <h3 style="margin:0;">Ruta ${c.id} (${c.zone})</h3>
          <span style="font-size:0.85em; color:var(--muted);">${c.dso || 'Okänd nätägare'}</span>
        </div>
        <div>
    `;

    if (c.passed) {
      const vClass = c.score >= 0.7 ? 'hi' : (c.score >= 0.4 ? 'mid' : 'lo');
      const vLabel = c.score >= 0.7 ? 'Värd förfrågan' : (c.score >= 0.4 ? 'Möjlig' : 'Svag');
      html += `<span class="bm-verdict ${vClass}">${c.score.toFixed(2)} · ${vLabel}</span>`;
    } else {
      html += `<span class="bm-verdict fail">Exkluderad</span>`;
    }

    html += `</div></div>`;

    if (!c.passed) {
      html += `
        <div style="background:rgba(239,68,68,0.12); border:1px solid #ef4444; border-radius:6px; padding:0.75rem; font-size:0.85em; color:#fca5a5; margin-bottom:0.75rem;">
          <strong>Filterorsak:</strong> ${c.failReason}
        </div>
      `;
    }

    // Delpoäng
    if (c.passed) {
      const pKey = elProfile.value;
      const w = PROFILES[pKey].weights;
      html += `
        <div style="margin-bottom:1rem;">
          <div style="font-size:0.8em; color:var(--muted); font-weight:600; margin-bottom:0.25rem;">DELPOÄNG (VIKT):</div>
          <div class="bm-sub"><span>Anslutningsnärhet</span><div class="bm-bar"><span style="width:${c.subScores.conn * 100}%;"></span></div><span class="w">${c.subScores.conn}</span></div>
          <div class="bm-sub"><span>Stödtjänstvärde</span><div class="bm-bar"><span style="width:${c.subScores.ancillary * 100}%;"></span></div><span class="w">${c.subScores.ancillary}</span></div>
          <div class="bm-sub"><span>Spotarbitrage</span><div class="bm-bar"><span style="width:${c.subScores.arb * 100}%;"></span></div><span class="w">${c.subScores.arb}</span></div>
          <div class="bm-sub"><span>Lokal flex</span><div class="bm-bar"><span style="width:${c.subScores.flex * 100}%;"></span></div><span class="w">${c.subScores.flex}</span></div>
          <div class="bm-sub"><span>Markförutsättning</span><div class="bm-bar"><span style="width:${c.subScores.land * 100}%;"></span></div><span class="w">${c.subScores.land}</span></div>
        </div>
      `;

      // Intäktsindikation
      const annEur = (c.stackEstimate || 0) * mw;
      const annMsek = (annEur * fx) / 1000000;
      html += `
        <div style="background:rgba(56,189,248,0.06); border:1px solid rgba(56,189,248,0.25); border-radius:8px; padding:0.75rem; margin-bottom:1rem;">
          <div style="font-size:0.8em; color:#38bdf8; font-weight:600;">INDIKATIV ÅRSINTÄKT (${mw} MW):</div>
          <div style="font-size:1.35em; font-weight:700; font-family:var(--mono); color:#fff; margin:0.2rem 0;">
            ${annMsek.toFixed(1)} MSEK/år
          </div>
          <div class="bm-note">~${Math.round(annEur).toLocaleString('sv-SE')} €/år vid antagen träffgrad & stackning. Ej en garanti.</div>
        </div>
      `;
    }

    // Fakta om rutan
    html += `
      <dl class="bm-kv">
        <dt>Elområde</dt><dd>${c.zone}</dd>
        <dt>Huvudsaklig nätägare</dt><dd>${c.dso || 'Okänd'} (${Math.round((c.dso_share || 1) * 100)} %)</dd>
        <dt>Koncessions-ID</dt><dd>${c.conc || '–'}</dd>
        <dt>Närmaste station</dt><dd>${c.nearestStationText || 'Ingen'}</dd>
        <dt>Avstånd station</dt><dd>${c.usedDist !== null ? c.usedDist + ' km' : '–'}</dd>
        <dt>130 kV stationer &le; 15 km</dt><dd>${c.k130_15 || 0} st</dd>
        <dt>Skyddad natur</dt><dd>${Math.round(c.prot * 100)} % ${c.prot_name ? '(' + c.prot_name + ')' : ''}</dd>
        <dt>Vattenskyddsområde</dt><dd>${Math.round(c.water * 100)} %</dd>
        <dt>Lokal flexmarknad</dt><dd>${c.flex && c.flex.length ? c.flex.join(', ') : 'Nej'}</dd>
      </dl>
    `;

    elDetail.innerHTML = html;
  }

  function updateScores() {
    computeCellScores();
    renderCellsLayer();
    renderTopList();
    if (selectedCellId) showCellDetail(selectedCellId);
  }

  function renderZoneCards() {
    if (!marketData) return;
    elZones.innerHTML = '';
    const durHours = parseInt(elDuration.value, 10);
    const fx = marketData.fx_eur_sek || 11.25;
    const mw = parseFloat(elMw.value) || 20;

    ['SE1', 'SE2', 'SE3', 'SE4'].forEach(z => {
      const spot = marketData.spot[z]?.last12m;
      const arb = spot ? (durHours === 4 ? spot.arb_4h_eur_mw_yr : spot.arb_2h_eur_mw_yr) : 0;
      const mfrrUp = marketData.capacity.mfrr_cm?.[z]?.up?.value_eur_mw_yr || 0;
      const mfrrDown = marketData.capacity.mfrr_cm?.[z]?.down?.value_eur_mw_yr || 0;
      const afrrDown = marketData.capacity.afrr_cm?.[z]?.down?.value_eur_mw_yr || 0;

      const div = document.createElement('div');
      div.className = 'bm-zone';
      div.innerHTML = `
        <h4><span>${z}</span><span class="big">${spot ? Math.round(spot.mean) : '–'} <small style="font-size:0.55em; color:var(--muted);">EUR/MWh</small></span></h4>
        <dl class="bm-kv">
          <dt>Dygnsspread (snitt)</dt><dd>${spot ? spot.daily_spread_mean.toFixed(1) : '–'} EUR</dd>
          <dt>Dygnsspread (P90)</dt><dd>${spot ? spot.daily_spread_p90.toFixed(1) : '–'} EUR</dd>
          <dt>Dygnsarbitrage (${durHours}h)</dt><dd>${Math.round(arb).toLocaleString('sv-SE')} €/MW/år</dd>
          <dt>mFRR upp-kapacitet</dt><dd>${Math.round(mfrrUp).toLocaleString('sv-SE')} €/MW/år</dd>
          <dt>mFRR ned-kapacitet</dt><dd>${Math.round(mfrrDown).toLocaleString('sv-SE')} €/MW/år</dd>
          <dt>aFRR ned-kapacitet</dt><dd>${Math.round(afrrDown).toLocaleString('sv-SE')} €/MW/år</dd>
          <dt>Negativa timmar</dt><dd>${spot ? (spot.neg_share * 100).toFixed(1) : '–'} %</dd>
        </dl>
      `;
      elZones.appendChild(div);
    });
  }

  function renderHistoryTable() {
    if (!marketData) return;
    const durHours = parseInt(elDuration.value, 10);
    const zones = ['SE1', 'SE2', 'SE3', 'SE4'];
    const years = ['2015', '2016', '2017', '2018', '2019', '2020', '2022', '2023', '2024', '2025', '2026'];

    let html = `
      <table class="bm-table">
        <thead>
          <tr>
            <th>År</th>
            <th>SE1</th>
            <th>SE2</th>
            <th>SE3</th>
            <th>SE4</th>
            <th>SE4 / SE2 multipel</th>
          </tr>
        </thead>
        <tbody>
    `;

    years.forEach(y => {
      const v1 = marketData.spot.SE1?.years?.[y]?.[durHours === 4 ? 'arb_4h_eur_mw_yr' : 'arb_2h_eur_mw_yr'] || 0;
      const v2 = marketData.spot.SE2?.years?.[y]?.[durHours === 4 ? 'arb_4h_eur_mw_yr' : 'arb_2h_eur_mw_yr'] || 0;
      const v3 = marketData.spot.SE3?.years?.[y]?.[durHours === 4 ? 'arb_4h_eur_mw_yr' : 'arb_2h_eur_mw_yr'] || 0;
      const v4 = marketData.spot.SE4?.years?.[y]?.[durHours === 4 ? 'arb_4h_eur_mw_yr' : 'arb_2h_eur_mw_yr'] || 0;
      const isPartial = marketData.spot.SE1?.years?.[y]?.partial;
      const mult = v2 > 0 ? (v4 / v2).toFixed(2) + 'x' : '–';

      html += `
        <tr>
          <td><strong>${y}${isPartial ? '*' : ''}</strong></td>
          <td style="font-family:var(--mono);">${Math.round(v1).toLocaleString('sv-SE')} €</td>
          <td style="font-family:var(--mono);">${Math.round(v2).toLocaleString('sv-SE')} €</td>
          <td style="font-family:var(--mono);">${Math.round(v3).toLocaleString('sv-SE')} €</td>
          <td style="font-family:var(--mono); font-weight:700; color:#38bdf8;">${Math.round(v4).toLocaleString('sv-SE')} €</td>
          <td style="font-family:var(--mono); color:#fbbf24;">${mult}</td>
        </tr>
      `;
    });

    html += `</tbody></table>`;
    elHistory.innerHTML = html;
  }

  function renderWeightsTable() {
    const cur = elProfile.value;
    let html = `
      <thead>
        <tr>
          <th>Profil</th>
          <th>Anslutning (0,35)</th>
          <th>Stödtjänster (0,30)</th>
          <th>Arbitrage (0,20)</th>
          <th>Lokal flex (0,10)</th>
          <th>Markrisk (0,05)</th>
        </tr>
      </thead>
      <tbody>
    `;

    Object.keys(PROFILES).forEach(k => {
      const p = PROFILES[k];
      const isSel = (k === cur);
      html += `
        <tr style="${isSel ? 'background:rgba(56,189,248,0.08); font-weight:600;' : ''}">
          <td>${p.name}${isSel ? ' (aktiv)' : ''}</td>
          <td>${Math.round(p.weights.conn * 100)} %</td>
          <td>${Math.round(p.weights.ancillary * 100)} %</td>
          <td>${Math.round(p.weights.arb * 100)} %</td>
          <td>${Math.round(p.weights.flex * 100)} %</td>
          <td>${Math.round(p.weights.land * 100)} %</td>
        </tr>
      `;
    });

    html += `</tbody>`;
    elWeightsTable.innerHTML = html;
  }

  function renderSourcesTable() {
    if (!metaData) return;
    let html = `
      <thead>
        <tr>
          <th>Lager / Data</th>
          <th>Källa</th>
          <th>Senast hämtad</th>
          <th>Gäller från</th>
          <th>Kommentar</th>
        </tr>
      </thead>
      <tbody>
    `;

    const lyrs = metaData.layers || {};
    Object.keys(lyrs).forEach(k => {
      const l = lyrs[k];
      html += `
        <tr>
          <td><strong>${k}</strong></td>
          <td>${l.url ? `<a href="${l.url}" target="_blank" rel="noopener">${l.source}</a>` : l.source}</td>
          <td style="font-family:var(--mono);">${l.retrieved_at ? l.retrieved_at.slice(0, 10) : '–'}</td>
          <td style="font-family:var(--mono);">${l.valid_from ? l.valid_from.slice(0, 10) : '–'}</td>
          <td class="bm-note">${l.note || ''}</td>
        </tr>
      `;
    });

    // Marknadsdata
    if (marketData) {
      if (marketData.spot_source) {
        html += `
          <tr>
            <td><strong>spot_day_ahead</strong></td>
            <td>${marketData.spot_source.source}</td>
            <td style="font-family:var(--mono);">${marketData.spot_source.retrieved_at.slice(0, 10)}</td>
            <td style="font-family:var(--mono);">${marketData.spot_source.valid_from ? marketData.spot_source.valid_from.slice(0, 10) : '2015-01-01'}</td>
            <td class="bm-note">Nord Pool / ENTSO-E timpriser</td>
          </tr>
        `;
      }
      if (marketData.capacity_sources) {
        Object.keys(marketData.capacity_sources).forEach(ck => {
          const cs = marketData.capacity_sources[ck];
          html += `
            <tr>
              <td><strong>${ck}</strong></td>
              <td><a href="${cs.url}" target="_blank" rel="noopener">${cs.source}</a></td>
              <td style="font-family:var(--mono);">${cs.retrieved_at ? cs.retrieved_at.slice(0, 10) : '–'}</td>
              <td style="font-family:var(--mono);">${cs.valid_from ? cs.valid_from.slice(0, 10) : '–'}</td>
              <td class="bm-note">${cs.records} observationer senaste 365 dagarna</td>
            </tr>
          `;
        });
      }
    }

    html += `</tbody>`;
    elSourcesTable.innerHTML = html;

    elMapSources.innerHTML = `Dataunderlag: Ei lokalnätskoncession (${metaData.layers.concessions?.valid_from?.slice(0, 10) || '2025-11-04'}), OSM kraftinfrastruktur (${metaData.layers.substations?.count || 1132} stationer &ge; 40 kV), SvK mFRR/aFRR kapacitet (senaste 12 mån), Naturvårdsregistret skyddsområden.`;
  }

  // Ladda bakgrundslager (zoner, ledningar, stationer, flex)
  async function loadBackgroundLayers() {
    // 1. Zoner
    try {
      const res = await fetch('data/bess-map/zones.json');
      const zJson = await res.json();
      L.geoJSON(zJson, {
        style: function (feat) {
          const z = feat.properties.zone;
          const colors = { SE1: '#60a5fa', SE2: '#22d3ee', SE3: '#fbbf24', SE4: '#fb7185' };
          return {
            color: colors[z] || '#ffffff',
            weight: 2,
            dashArray: '4, 4',
            fillOpacity: 0.03
          };
        }
      }).addTo(zoneLayerGroup);
    } catch (e) {
      console.warn('Could not load zones.json', e);
    }

    // 2. Ledningar
    try {
      const res = await fetch('data/bess-map/lines.json');
      const lines = await res.json();
      lines.forEach(ln => {
        const color = ln.kv >= 380 ? '#f43f5e' : (ln.kv >= 220 ? '#fb923c' : '#38bdf8');
        L.polyline(ln.c, {
          color: color,
          weight: ln.kv >= 380 ? 2 : 1.2,
          opacity: 0.55
        }).addTo(linesLayerGroup);
      });
    } catch (e) {
      console.warn('Could not load lines.json', e);
    }

    // 3. Stationer
    try {
      const res = await fetch('data/bess-map/substations.json');
      const subs = await res.json();
      subs.forEach(s => {
        // [lat, lon, kv, name, operator]
        const circle = L.circleMarker([s[0], s[1]], {
          radius: s[2] >= 220 ? 5 : 3.5,
          color: s[2] >= 220 ? '#fb923c' : '#38bdf8',
          fillColor: '#ffffff',
          fillOpacity: 0.8,
          weight: 1
        });
        circle.bindPopup(`<strong>${s[3] || 'Transformatorstation'}</strong><br>${s[2]} kV<br>${s[4] || ''}`);
        circle.addTo(subsLayerGroup);
      });
    } catch (e) {
      console.warn('Could not load substations.json', e);
    }

    // 4. Flexmarknader
    try {
      const res = await fetch('data/bess-map/flex.json');
      flexData = await res.json();
      flexData.forEach(f => {
        const c = L.circle([f.lat, f.lon], {
          radius: f.radius_km * 1000,
          color: '#34d399',
          fillColor: '#34d399',
          fillOpacity: 0.12,
          weight: 1.5,
          dashArray: '3, 4'
        });
        c.bindPopup(`<strong>Lokal flexmarknad: ${f.name}</strong><br>${f.area}<br>${f.operator}<br><small>${f.note}</small>`);
        c.addTo(flexLayerGroup);
      });
    } catch (e) {
      console.warn('Could not load flex.json', e);
    }
  }

  // Huvudstart
  async function init() {
    initSliders();
    initMap();
    renderWeightsTable();

    try {
      const [resCells, resMarket, resMeta] = await Promise.all([
        fetch('data/bess-map/cells.json'),
        fetch('data/bess-map/market.json'),
        fetch('data/bess-map/meta.json')
      ]);

      cellsData = await resCells.json();
      marketData = await resMarket.json();
      metaData = await resMeta.json();

      renderZoneCards();
      renderHistoryTable();
      renderSourcesTable();

      await loadBackgroundLayers();
      updateScores();

      // Välj topp-rutan automatiskt i detaljfönstret
      const first = cellsData.find(c => c.passed && c.score >= 0.7) || cellsData[0];
      if (first) {
        selectedCellId = first.id;
        showCellDetail(first.id);
      }
    } catch (err) {
      console.error('Failed loading BESS map data:', err);
      elDetail.innerHTML = `<div class="bm-warn">Fel vid laddning av kartdata: ${err.message}</div>`;
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
