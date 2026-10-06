/**
 * BESS Investment Studio – gränssnitt.
 * All beräkning ligger i bess-studio-model.js (BessModel); den här filen läser
 * reglagen, ritar kartan och visar resultaten.
 */
(function () {
  'use strict';

  const M = window.BessModel;
  const $ = (id) => document.getElementById(id);
  const ZONES = ['SE1', 'SE2', 'SE3', 'SE4'];
  const ROUTING = 1.3;
  const MIN_KM = 0.25;
  const PROT_EXCLUDE = 0.5;
  const NO_STATION_KM = 30;
  const PRODUCT_LABELS = {
    spot: 'Spotarbitrage', mfrr_up: 'mFRR upp', mfrr_down: 'mFRR ned', afrr_up: 'aFRR upp', afrr_down: 'aFRR ned',
    fcr_n: 'FCR-N', fcr_d_up: 'FCR-D upp', fcr_d_down: 'FCR-D ned',
  };
  const PRODUCT_COLORS = {
    spot: '#fbbf24', mfrr_up: '#38bdf8', mfrr_down: '#0ea5e9', afrr_up: '#a855f7', afrr_down: '#c084fc',
    fcr_n: '#34d399', fcr_d_up: '#10b981', fcr_d_down: '#6ee7b7',
  };

  const S = {
    cells: [], cellById: new Map(), market: null, meta: null, disp: null, stations: [], datasets: [],
    flexNames: {}, selectedId: null, custom: null, compare: new Set(), topZone: 'ALL', topSites: [], negativeDirty: false,
    zoneRev: {}, bestGrossPerMw: 1, inp: null, ready: false, pickMode: false, extremeDirty: false,
    lastInvest: null, tab: 'spot', concLoaded: false,
  };
  let map = null;
  let layers = {};
  let customMarker = null;

  // ------------------------------------------------------------- formatting
  const num = (v, d = 0) => Number(v).toLocaleString('sv-SE', { minimumFractionDigits: d, maximumFractionDigits: d });
  const msek = (v, d = 1) => `${num(v / 1e6, d)} MSEK`;
  const pct = (v, d = 0) => `${num(v * 100, d)} %`;
  const yearsTxt = (v) => (v === null || !isFinite(v) ? 'Ingen återbetalning' : v > 50 ? 'över 50 år' : `${num(v, 1)} år`);
  const esc = (s) => String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const dateOnly = (iso) => (iso ? String(iso).slice(0, 10) : '–');

  const paybackColor = (pb) => (pb === null ? '#ef4444' : pb <= 7 ? '#10b981' : pb <= 10 ? '#fbbf24' : '#ef4444');
  const paybackClass = (pb) => (pb === null ? 'red' : pb <= 7 ? 'green' : pb <= 10 ? 'yellow' : 'red');
  const scoreColor = (s) => (s >= 75 ? '#10b981' : s >= 55 ? '#fbbf24' : '#ef4444');
  const scoreClass = (s) => (s >= 75 ? 'green' : s >= 55 ? 'yellow' : 'red');
  const scoreVerdict = (s) => (s >= 75 ? 'goda förutsättningar' : s >= 55 ? 'medelgoda förutsättningar' : 'svaga förutsättningar');

  // ------------------------------------------------------------------ inputs
  const RANGE_LABELS = [
    ['range-capex-kwh', 'val-capex-kwh', (v) => `${num(v)} kr`],
    ['range-capex-kw', 'val-capex-kw', (v) => `${num(v)} kr`],
    ['range-cable-cost', 'val-cable-cost', (v) => `${num(v, 1)} MSEK`],
    ['range-substation-bay', 'val-substation-bay', (v) => `${num(v)} MSEK`],
    ['range-share', 'val-share', (v) => `${num(v)} %`],
    ['range-realization', 'val-realization', (v) => `${num(v)} %`],
    ['range-anc-ext', 'val-anc-ext', (v) => `× ${num(v, 2)}`],
    ['range-fee', 'val-fee', (v) => `${num(v)} %`],
    ['range-om', 'val-om', (v) => `${num(v)} kr`],
    ['range-grid', 'val-grid', (v) => `${num(v)} kr`],
    ['range-other', 'val-other', (v) => `${num(v, 1)} %`],
    ['range-wear', 'val-wear', (v) => `${num(v)} kr`],
  ];
  const INVEST_FIELDS = {
    'inv-life': 'lifeYears', 'inv-wacc': 'waccPct', 'inv-inflation': 'inflationPct', 'inv-anc-trend': 'ancTrendPct',
    'inv-spot-trend': 'spotTrendPct', 'inv-degradation': 'degradationPct', 'inv-aug-year': 'augmentYear',
    'inv-aug-pct': 'augmentPctOfBattery', 'inv-tax': 'taxPct', 'inv-dep': 'depreciationYears',
    'inv-residual': 'residualPct', 'inv-gearing': 'gearingPct', 'inv-interest': 'interestPct', 'inv-tenor': 'tenorYears',
  };
  // Reglage som sparas i länken.
  const HASH_FIELDS = [
    'num-power-mw', 'num-duration', 'range-capex-kwh', 'range-capex-kw', 'range-cable-cost', 'range-substation-bay',
    'sel-dataset', 'sel-extreme-years', 'sel-negative-years', 'range-anc-ext', 'range-share', 'range-realization', 'range-fee', 'range-om', 'range-grid',
    'range-other', 'range-wear', 'inv-extreme-years', 'inv-negative-years',
  ].concat(Object.keys(INVEST_FIELDS));

  const fnum = (id, fallback = 0) => {
    const v = parseFloat(String($(id).value).replace(',', '.'));
    return isFinite(v) ? v : fallback;
  };
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  function readInputs() {
    const dsId = $('sel-dataset').value;
    return {
      mw: clamp(fnum('num-power-mw', 50), 5, 300),
      durationH: clamp(fnum('num-duration', 2), 1, 4),
      capexPerKwh: fnum('range-capex-kwh'),
      capexPerKw: fnum('range-capex-kw'),
      cablePerKmMsek: fnum('range-cable-cost'),
      bayMsek: fnum('range-substation-bay'),
      dataset: S.datasets.find((d) => d.id === dsId) || S.datasets[0],
      extremeYears: parseInt($('sel-extreme-years').value, 10) || 0,
      negativeYears: parseInt($('sel-negative-years').value, 10) || 0,
      marketSharePct: fnum('range-share'),
      ancillaryExtremeMult: fnum('range-anc-ext', 1),
      realizationPct: fnum('range-realization'),
      feePct: fnum('range-fee'),
      omPerKw: fnum('range-om'),
      gridPerKw: fnum('range-grid'),
      otherPctCapex: fnum('range-other'),
      wearPerMwh: fnum('range-wear'),
      routingFactor: ROUTING,
      fx: (S.market && S.market.fx_eur_sek) || 11.25,
    };
  }

  function updateLabels() {
    RANGE_LABELS.forEach(([rid, vid, f]) => { $(vid).textContent = f(fnum(rid)); });
    const mw = clamp(fnum('num-power-mw', 50), 5, 300);
    const h = clamp(fnum('num-duration', 2), 1, 4);
    $('badge-size').textContent = `${num(mw)} MW · ${num(mw * h)} MWh · ${num(h, 1)} h`;
  }

  // -------------------------------------------------------------- evaluation
  function zoneRevenue(inp, zone) {
    return M.siteRevenue(S.disp, {
      zone, mw: inp.mw, durationH: inp.durationH, dataset: inp.dataset, marketSharePct: inp.marketSharePct,
      realizationPct: inp.realizationPct, fx: inp.fx, revMult: inp.revMult,
      ancillaryExtremeMult: inp.ancillaryExtremeMult,
    });
  }

  function stationFor(site, mw) {
    return site.st[M.stationClassFor(mw)];
  }

  function exclusionReason(site) {
    if (!site.dso) return 'Ingen känd lokalnätskoncession';
    if ((site.prot || 0) >= PROT_EXCLUDE) return `${pct(site.prot)} formellt skyddad natur`;
    return null;
  }

  /** Full kalkyl för en plats. `rev` är zonens intäkt; `bestGross` behövs bara för poängen. */
  function evaluateSite(site, inp, rev, bestGross) {
    const reason = exclusionReason(site);
    if (reason) return { passed: false, reason };
    const st = stationFor(site, inp.mw);
    const rawKm = site.customKm !== undefined && site.customKm !== null ? site.customKm : (st ? st.km : NO_STATION_KM);
    const cableKm = Math.max(MIN_KM, rawKm);
    const o = Object.assign({}, inp, { cableKm });
    const fin = M.simpleFinancials(o, rev);
    const sc = M.siteScore({
      cableKm, zoneGrossPerMw: rev.gross / inp.mw, bestZoneGrossPerMw: bestGross || rev.gross / inp.mw,
      prot: site.prot, water: site.water,
    });
    return { passed: true, fin, rev, station: st, cableKm, score: sc.score, sub: sc };
  }

  /** Samma kalkyl med ändrade antaganden – används av känslighetsanalysen. */
  function evaluateWith(site, changes) {
    const inp = Object.assign({}, S.inp, changes);
    const rev = zoneRevenue(inp, site.zone);
    return evaluateSite(site, inp, rev, S.bestGrossPerMw);
  }

  function siteById(id) {
    if (id === 'custom') return S.custom;
    return S.cellById.get(id) || null;
  }
  const siteName = (site) => (site.id === 'custom' ? 'Egen plats' : `Ruta ${site.id}`);

  // ------------------------------------------------------------------ recalc
  function recalcAll() {
    if (!S.ready) return;
    updateLabels();
    S.inp = readInputs();
    S.zoneRev = {};
    ZONES.forEach((z) => { S.zoneRev[z] = zoneRevenue(S.inp, z); });
    S.bestGrossPerMw = Math.max(1, ...ZONES.map((z) => S.zoneRev[z].gross / S.inp.mw));

    S.cells.forEach((cell) => { cell.res = evaluateSite(cell, S.inp, S.zoneRev[cell.zone], S.bestGrossPerMw); });
    if (S.custom) S.custom.res = evaluateSite(S.custom, S.inp, S.zoneRev[S.custom.zone], S.bestGrossPerMw);

    let sel = siteById(S.selectedId);
    if (!sel || !sel.res.passed) sel = bestCell(null);
    if (sel) S.selectedId = sel.id;

    updateDatasetNotes();
    restyleCells();
    updateRanking();
    if (sel) { highlightSelection(); renderSelected(sel); }
    renderTransparency();
    saveHash();
  }

  let recalcTimer = null;
  function scheduleRecalc() {
    updateLabels();
    clearTimeout(recalcTimer);
    recalcTimer = setTimeout(recalcAll, 120);
  }

  const better = (a, b) => {
    const pa = a.res.fin.payback;
    const pb = b.res.fin.payback;
    if (pa === null && pb === null) return b.res.score - a.res.score;
    if (pa === null) return 1;
    if (pb === null) return -1;
    if (Math.abs(pa - pb) > 1e-9) return pa - pb;
    return b.res.score - a.res.score;
  };

  function bestCell(bounds) {
    let pool = S.cells.filter((c) => c.res && c.res.passed);
    if (bounds) pool = pool.filter((c) => bounds.contains(L.latLng(c.c[0], c.c[1])));
    if (!pool.length) return null;
    return pool.reduce((best, cur) => (better(cur, best) < 0 ? cur : best), pool[0]);
  }

  // --------------------------------------------------------------------- map
  function initMap() {
    map = L.map('bs-map', { center: [62.0, 15.5], zoom: 5, minZoom: 4, maxZoom: 13, preferCanvas: true });
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
      attribution: 'Bakgrund &copy; Esri, HERE, Garmin, &copy; OpenStreetMap-bidragsgivare · Nät &copy; OpenStreetMap (ODbL) · Koncessioner: Ei · Natur: Naturvårdsverket',
      maxZoom: 16,
    }).addTo(map);
    layers = {
      lines: L.layerGroup().addTo(map), cells: L.layerGroup().addTo(map), subs: L.layerGroup().addTo(map),
      flex: L.layerGroup().addTo(map), conc: L.layerGroup(),
    };
    const toggle = (id, key) => $(id).addEventListener('change', () => ($(id).checked ? map.addLayer(layers[key]) : map.removeLayer(layers[key])));
    toggle('bs-lyr-cells', 'cells');
    toggle('bs-lyr-lines', 'lines');
    toggle('bs-lyr-subs', 'subs');
    toggle('bs-lyr-flex', 'flex');
    $('bs-lyr-conc').addEventListener('change', async () => {
      if ($('bs-lyr-conc').checked) { await loadConcessions(); map.addLayer(layers.conc); } else map.removeLayer(layers.conc);
    });
    $('bs-lyr-failed').addEventListener('change', restyleCells);

    map.on('click', (e) => {
      if (!S.pickMode) return;
      map.closePopup();
      setCustomSite(e.latlng.lat, e.latlng.lng);
    });
  }

  function cellStyle(cell) {
    const r = cell.res;
    const selected = cell.id === S.selectedId;
    if (!r || !r.passed) return { color: '#64748b', weight: 1, opacity: 0.35, fillColor: '#64748b', fillOpacity: 0.12 };
    const byScore = $('sel-map-color-mode').value === 'score';
    const color = byScore ? scoreColor(r.score) : paybackColor(r.fin.payback);
    return {
      color: selected ? '#ffffff' : color, weight: selected ? 3 : 1, opacity: selected ? 1 : 0.6,
      fillColor: color, fillOpacity: selected ? 0.65 : 0.32,
    };
  }

  function buildCellLayer() {
    S.cells.forEach((cell) => {
      const poly = L.polygon(cell.poly, cellStyle(cell));
      poly.bindPopup(() => popupHtml(cell), { maxWidth: 320, className: 'bess-cell-popup', autoPanPadding: L.point(30, 30) });
      poly.bindTooltip(() => tooltipHtml(cell), { sticky: true, opacity: 0.95 });
      poly.on('click', (e) => {
        // En ruta med popup stoppar klicket innan det når kartan, så egen plats sätts här.
        if (S.pickMode) { map.closePopup(); setCustomSite(e.latlng.lat, e.latlng.lng); return; }
        if (!cell.res || !cell.res.passed) return;
        selectSite(cell, false);
      });
      cell.poly_ = poly;
      cell.onMap = false;
    });
  }

  function restyleCells() {
    const showFailed = $('bs-lyr-failed').checked;
    S.cells.forEach((cell) => {
      const visible = cell.res && (cell.res.passed || showFailed);
      if (visible && !cell.onMap) { layers.cells.addLayer(cell.poly_); cell.onMap = true; }
      if (!visible && cell.onMap) { layers.cells.removeLayer(cell.poly_); cell.onMap = false; }
      if (visible) cell.poly_.setStyle(cellStyle(cell));
    });
  }

  function highlightSelection() {
    S.cells.forEach((cell) => { if (cell.onMap && (cell.id === S.selectedId || cell.wasSelected)) cell.poly_.setStyle(cellStyle(cell)); cell.wasSelected = cell.id === S.selectedId; });
    const sel = S.cellById.get(S.selectedId);
    if (sel && sel.onMap) sel.poly_.bringToFront();
  }

  function tooltipHtml(cell) {
    const r = cell.res;
    if (!r || !r.passed) return `<strong>Ruta ${esc(cell.id)} (${cell.zone})</strong><br>Utesluten: ${esc(r ? r.reason : '')}`;
    return `<strong>Ruta ${esc(cell.id)} (${cell.zone})</strong><br>Återbetalning: <strong style="color:${paybackColor(r.fin.payback)};">${yearsTxt(r.fin.payback)}</strong><br>Platspoäng ${r.score}/100 · ${num(r.cableKm, 1)} km till station`;
  }

  function stationLabel(st) {
    if (!st) return 'Ingen station inom räckhåll i OpenStreetMap';
    return `${st.name ? esc(st.name) : 'Namnlös station'} · ${num(st.kv)} kV`;
  }

  function popupHtml(cell) {
    const r = cell.res;
    if (!r || !r.passed) {
      return `<div style="font-size:12px; min-width:210px;"><strong>Ruta ${esc(cell.id)} (${cell.zone})</strong><br><span style="color:#94a3b8;">${esc(cell.dso || 'Okänd nätägare')}</span><div style="margin-top:6px; padding:4px 8px; background:rgba(239,68,68,0.15); border:1px solid #ef4444; border-radius:4px; color:#fca5a5;">Utesluten: ${esc(r ? r.reason : '')}</div></div>`;
    }
    const f = r.fin;
    const row = (a, b) => `<div style="display:flex; justify-content:space-between; gap:10px;"><span style="color:#cbd5e1;">${a}</span><strong>${b}</strong></div>`;
    return `<div style="font-size:12px; line-height:1.5; min-width:250px; color:#f1f5f9;">
      <div style="display:flex; justify-content:space-between; align-items:baseline; border-bottom:1px solid rgba(255,255,255,0.12); padding-bottom:4px; margin-bottom:5px;">
        <strong style="font-size:13px;">Ruta ${esc(cell.id)} <span style="color:#94a3b8; font-weight:400;">(${cell.zone})</span></strong>
        <strong style="color:${paybackColor(f.payback)};">${yearsTxt(f.payback)}</strong>
      </div>
      ${row('Lokalnätsägare', esc(cell.dso))}
      ${row('Närmaste station', stationLabel(r.station))}
      ${row('Kabelavstånd', `${num(r.cableKm, 1)} km`)}
      ${row('Investering', msek(f.capex.total, 0))}
      ${row('Driftnetto per år', msek(f.netNormal))}
      ${row('Platspoäng', `${r.score}/100`)}
      ${(cell.prot || 0) >= 0.02 ? `<div style="color:#fbbf24; margin-top:4px;">${pct(cell.prot)} skyddad natur${cell.prot_name ? ` (${esc(cell.prot_name)})` : ''}</div>` : ''}
      <div style="display:flex; gap:4px; margin-top:7px;">
        <button type="button" data-act="compare" data-id="${esc(cell.id)}" class="bs-btn-subtle" style="flex:1; padding:4px 6px; font-size:11px;">${S.compare.has(cell.id) ? 'Ta bort ur jämförelse' : 'Jämför'}</button>
        <button type="button" data-act="dispatch" class="bs-btn-subtle" style="flex:1; padding:4px 6px; font-size:11px;">Marknadsfördelning</button>
      </div>
    </div>`;
  }

  function selectSite(site, fly) {
    if (!site) return;
    S.selectedId = site.id;
    highlightSelection();
    renderSelected(site);
    updateRanking();
    saveHash();
    if (fly) {
      map.flyTo(site.c, Math.max(map.getZoom(), 9), { duration: 0.8 });
      if (site.poly_) setTimeout(() => { if (S.selectedId === site.id && site.onMap) site.poly_.openPopup(); }, 900);
    }
  }

  // ------------------------------------------------------------- egen plats
  function nearestCell(lat, lon) {
    let best = null;
    let bestD = Infinity;
    S.cells.forEach((c) => {
      const d = (c.c[0] - lat) ** 2 + ((c.c[1] - lon) * Math.cos(lat * Math.PI / 180)) ** 2;
      if (d < bestD) { bestD = d; best = c; }
    });
    return best && M.distanceKm(lat, lon, best.c[0], best.c[1]) <= 8 ? best : null;
  }

  function setCustomSite(lat, lon, km) {
    const cell = nearestCell(lat, lon);
    if (!cell) {
      $('bs-map-status').textContent = 'Punkten ligger utanför de analyserade rutorna. Välj en plats på svensk mark.';
      return;
    }
    S.custom = {
      id: 'custom', c: [lat, lon], zone: cell.zone, dso: cell.dso, prot: cell.prot, prot_name: cell.prot_name,
      water: cell.water, flex: cell.flex, cellId: cell.id,
      st: { regional: M.nearestStation(lat, lon, S.stations, 'regional'), any: M.nearestStation(lat, lon, S.stations, 'any') },
      customKm: km === undefined ? null : km,
    };
    if (customMarker) map.removeLayer(customMarker);
    customMarker = L.marker([lat, lon], { icon: L.divIcon({ className: 'bs-custom-pin', iconSize: [16, 16] }), title: 'Egen plats' }).addTo(map);
    customMarker.on('click', () => selectSite(S.custom, false));
    setPickMode(false);
    S.custom.res = evaluateSite(S.custom, S.inp, S.zoneRev[S.custom.zone], S.bestGrossPerMw);
    $('bs-map-status').textContent = `Egen plats satt i ruta ${cell.id}. Mark- och nätägaruppgifter kommer från rutan.`;
    selectSite(S.custom, false);
  }

  function setPickMode(on) {
    S.pickMode = on;
    const b = $('btn-custom-site');
    b.setAttribute('aria-pressed', String(on));
    b.textContent = on ? 'Klicka i kartan …' : (S.custom ? 'Flytta egen plats' : 'Sätt egen plats');
    b.style.borderColor = on ? '#f472b6' : '';
    b.style.color = on ? '#f472b6' : '';
    $('bs-map').style.cursor = on ? 'crosshair' : '';
  }

  // ----------------------------------------------------------------- ranking
  function updateRanking() {
    const list = $('list-top-sites');
    list.innerHTML = '';
    let pool = S.cells.filter((c) => c.res && c.res.passed);
    if (S.topZone !== 'ALL') pool = pool.filter((c) => c.zone === S.topZone);
    pool.sort(better);
    $('txt-top-list-title').textContent = S.topZone === 'ALL' ? 'Kortast återbetalningstid i Sverige' : `Kortast återbetalningstid i ${S.topZone}`;

    // Högst två rutor per nätägare, så att listan inte blir fem grannrutor.
    const top = [];
    const perOwner = {};
    for (const c of pool) {
      const key = `${c.zone}|${c.dso}`;
      if ((perOwner[key] || 0) >= 2) continue;
      perOwner[key] = (perOwner[key] || 0) + 1;
      top.push(c);
      if (top.length >= 5) break;
    }
    S.topSites = top;

    top.forEach((c, idx) => {
      const f = c.res.fin;
      const li = document.createElement('li');
      li.className = 'bs-top-item';
      li.tabIndex = 0;
      if (c.id === S.selectedId) { li.style.borderColor = '#38bdf8'; li.style.background = 'rgba(56, 189, 248, 0.12)'; }
      li.innerHTML = `
        <div class="bs-top-rank">${idx + 1}</div>
        <div class="bs-top-main">
          <div class="bs-top-head"><span class="bs-top-name" title="${esc(c.dso)}">${esc(c.dso)}</span><span class="bs-badge ${scoreClass(c.res.score)}" style="font-size:0.7em; padding:1px 5px;">${c.res.score} p</span></div>
          <div class="bs-top-sub"><span style="color:#38bdf8; font-weight:600;">${c.zone}</span> · ${num(c.res.cableKm, 1)} km till station · ruta ${esc(c.id)}</div>
        </div>
        <div class="bs-top-right">
          <div class="bs-top-roi" style="color:${paybackColor(f.payback)};">${f.payback === null ? '–' : `${num(f.payback, 2)} år`}</div>
          <div class="bs-top-ebitda">${msek(f.netNormal)}/år</div>
        </div>`;
      const go = () => selectSite(c, true);
      li.addEventListener('click', go);
      li.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
      list.appendChild(li);
    });
    if (!top.length) list.innerHTML = '<li class="bs-note">Inga rutor uppfyller villkoren.</li>';
    updateStepper();
  }

  function updateStepper() {
    const idx = S.topSites.findIndex((c) => c.id === S.selectedId);
    const n = S.topSites.length;
    $('txt-stepper-info').textContent = idx >= 0 ? `Nr ${idx + 1} av ${n}` : (n ? 'Utanför topplistan' : 'Inga träffar');
    $('btn-prev-site').disabled = n === 0 || idx === 0;
    $('btn-next-site').disabled = n === 0 || idx === n - 1;
  }

  function step(dir) {
    const idx = S.topSites.findIndex((c) => c.id === S.selectedId);
    const next = idx < 0 ? 0 : clamp(idx + dir, 0, S.topSites.length - 1);
    if (S.topSites[next]) selectSite(S.topSites[next], true);
  }

  // ---------------------------------------------------------- selected detail
  function flexText(site) {
    if (!site.flex || !site.flex.length) return 'Ingen';
    return site.flex.map((id) => S.flexNames[id] || id).join(', ');
  }

  function renderSelected(site) {
    const r = site.res;
    if (!r || !r.passed) return;
    const f = r.fin;
    const rev = r.rev;
    const inp = S.inp;

    $('title-selected-site').textContent = `${siteName(site)} · ${site.zone} · ${site.dso}`;
    $('badge-site-score-top').textContent = `Poäng ${r.score}/100`;
    $('badge-site-score-top').className = `bs-badge ${scoreClass(r.score)}`;
    $('badge-payback').textContent = yearsTxt(f.payback);
    $('badge-payback').className = `bs-badge ${paybackClass(f.payback)}`;

    $('kpi-payback').textContent = yearsTxt(f.payback);
    $('kpi-payback').style.color = paybackColor(f.payback);
    const anyExt = f.extremeYearsUsed + f.negativeYearsUsed > 0;
    $('kpi-payback-sub').textContent = anyExt ? `Utan extremår: ${yearsTxt(f.paybackNormal)}` : 'Odiskonterad, tio lika år';
    $('kpi-capex').textContent = msek(f.capex.total, 0);
    $('kpi-capex-sub').textContent = `Varav anslutning: ${msek(f.capex.cable + f.capex.bay)}`;
    $('kpi-net').textContent = msek(f.netNormal);
    $('kpi-net').style.color = f.netNormal < 0 ? '#f87171' : '#38bdf8';
    $('kpi-10yr').textContent = msek(f.tenYear, 0);
    const parts = [];
    if (f.extremeYearsUsed > 0) parts.push(`${f.extremeYearsUsed} ${f.extremeYearsUsed === 1 ? 'positivt' : 'positiva'} à ${msek(f.netExtreme)}`);
    if (f.negativeYearsUsed > 0) parts.push(`${f.negativeYearsUsed} ${f.negativeYearsUsed === 1 ? 'negativt' : 'negativa'} à ${msek(f.netNegative)}`);
    $('kpi-10yr-sub').textContent = parts.length
      ? `Varav extremår: ${parts.join(', ')}`
      : (rev.extremeIsBase ? 'Alla år har 2022 års spotpriser' : 'Utan extremår');

    const isCustom = site.id === 'custom';
    $('box-custom-site').style.display = isCustom ? '' : 'none';
    if (isCustom) {
      const st = r.station;
      if (document.activeElement !== $('num-custom-km')) $('num-custom-km').value = num(r.cableKm, 1).replace(',', '.');
      $('txt-custom-note').textContent = `Punkt ${num(site.c[0], 4)}° N, ${num(site.c[1], 4)}° O. `
        + (st ? `Fågelvägen till ${st.name || 'namnlös station'} (${num(st.kv)} kV) är ${num(st.km, 1)} km. ` : '')
        + 'Ändra avståndet om du vet den verkliga anslutningspunkten.';
    }

    $('det-zone').textContent = site.zone;
    $('det-dso').textContent = site.dso;
    $('det-station').innerHTML = stationLabel(r.station);
    $('det-dist').textContent = `${num(r.cableKm, 1)} km${isCustom ? '' : ' (från rutans mitt)'}`;
    $('det-energy-capex').textContent = msek(f.capex.energy);
    $('det-power-capex').textContent = msek(f.capex.power);
    $('det-station-capex').textContent = msek(f.capex.bay);
    $('det-cable-cost').textContent = msek(f.capex.cable);
    $('det-capex-total').textContent = msek(f.capex.total);

    // Intäkter
    const rows = S.disp.products.map((p) => {
      const v = rev.perProduct[p];
      if (p !== 'spot' && v < 5e3) return '';
      return `<tr><td class="lbl">${PRODUCT_LABELS[p]}</td><td class="val" style="color:${PRODUCT_COLORS[p]};">${msek(v)}</td></tr>`;
    }).join('');
    let extra = `<tr><td class="lbl sum">Summa brutto</td><td class="val sum">${msek(rev.gross)}</td></tr>`;
    if (!rev.extremeIsBase) {
      const up = rev.gross > 0 ? (rev.grossExtreme / rev.gross - 1) : 0;
      extra += `<tr><td class="lbl">Positivt extremår (optimerat mot 2022 års spot)</td><td class="val" style="color:#fbbf24;">${msek(rev.grossExtreme)} · +${pct(up)}</td></tr>`;
    }
    const down = rev.gross > 0 ? (1 - rev.grossNegative / rev.gross) : 0;
    extra += `<tr><td class="lbl">Negativt extremår (0,6 × spot)</td><td class="val" style="color:#a78bfa;">${msek(rev.grossNegative)} · −${pct(down)}</td></tr>`;
    $('tbl-revenue').innerHTML = rows + extra;
    $('txt-rev-period').textContent = `${num(rev.gross / inp.mw / inp.fx / 1000, 0)} k€/MW`;
    const capped = S.disp.products.slice(1).filter((p) => rev.sold[p].marketShare !== null && rev.sold[p].marketShare >= 0.85 * inp.marketSharePct / 100 && rev.sold[p].mw > 0.05);
    $('txt-rev-note').textContent = `Teoretiskt tak ${num(rev.theoreticalPerMwEur / 1000, 0)} k€/MW/år, realiseringsgrad ${num(inp.realizationPct)} %. `
      + (capped.length ? `Marknadstaket på ${num(inp.marketSharePct)} % begränsar ${capped.map((p) => PRODUCT_LABELS[p]).join(', ')}.` : 'Inget marknadstak begränsar vid den här storleken.');

    // Driftkostnader
    const ox = f.opexNormal;
    const orow = (a, v, cls = '') => `<tr><td class="lbl ${cls}">${a}</td><td class="val ${cls}">${msek(v)}</td></tr>`;
    $('tbl-opex').innerHTML = orow(`Optimerare / BSP (${num(inp.feePct)} %)`, ox.fee) + orow('Drift och underhåll', ox.om) + orow('Nätavgift', ox.grid)
      + orow('Försäkring, arrende m.m.', ox.other) + orow(`Slitageavsättning (${num(rev.cycles)} cykler)`, ox.wear) + orow('Summa', ox.total, 'sum')
      + `<tr><td class="lbl sum">Driftnetto</td><td class="val sum" style="color:${f.netNormal < 0 ? '#f87171' : '#34d399'};">${msek(f.netNormal)}</td></tr>`;

    $('det-prot').innerHTML = `${pct(site.prot || 0)}${site.prot_name && site.prot > 0 ? `<div style="font-size:0.78em; color:var(--muted); font-weight:400;">${esc(site.prot_name)}</div>` : ''}`;
    $('det-water').textContent = pct(site.water || 0);
    $('det-flex').textContent = flexText(site);

    $('badge-site-score').textContent = `${r.score} / 100 · ${scoreVerdict(r.score)}`;
    $('badge-site-score').className = `bs-badge ${scoreClass(r.score)}`;
    [['conn', r.sub.conn], ['mkt', r.sub.market], ['land', r.sub.land]].forEach(([k, v]) => {
      $(`bar-score-${k}`).style.width = `${Math.round(v * 100)}%`;
      $(`val-score-${k}`).textContent = num(v, 2);
    });

    updateCompareButton();
    renderSensitivity(site);
    renderInvestment(site);
  }

  function updateDatasetNotes() {
    const inp = S.inp;
    $('lbl-dataset-desc').textContent = inp.dataset.note;
    const sel = $('sel-extreme-years');
    const isBase = inp.dataset.id === 'spot2022';
    sel.disabled = isBase;
    const ratios = ZONES.map((z) => S.zoneRev[z]).map((r) => (r.gross > 0 ? r.grossExtreme / r.gross - 1 : 0));
    const thr = num(M.POSITIVE_THRESHOLD, 1);
    const sel0 = S.zoneRev[(siteById(S.selectedId) || {}).zone] || S.zoneRev.SE3;
    const actualX = sel0 && sel0.actual2022Gross && sel0.gross > 0 ? sel0.actual2022Gross / sel0.gross : null;
    $('lbl-extreme-desc').textContent = isBase
      ? 'Prisunderlaget är redan 2022 – alla år är positiva extremår och valet har ingen effekt.'
      : `Batteriet optimeras om mot 2022 års spotpriser, med dagens stödtjänstpriser. Det höjer bruttointäkten med ${ZONES.map((z, i) => `${z} +${pct(ratios[i])}`).join(', ')}. 2022 års rena spotarbitrage var ${ZONES.map((z) => `${z} ${num(S.zoneRev[z].extremeRatio, 1)} ×`).join(', ')} normalårets (gräns för extremår: ${thr} ×).`;
    $('lbl-anc-ext-desc').textContent = 'Stödtjänstintäkten i ett positivt extremår, gånger den optimerade nivån. 1,00 betyder dagens priser. '
      + (actualX ? `Som jämförelse: 2022 som det faktiskt var – med den tidens FCR-priser, före batteriernas intåg – gav ${num(actualX, 1)} gånger dagens normalår för den här anläggningen.` : '');
    const drops = ZONES.map((z) => S.zoneRev[z]).map((r) => (r.gross > 0 ? 1 - r.grossNegative / r.gross : 0));
    $('lbl-negative-desc').textContent = `Spotdelen × ${num(M.NEGATIVE_FACTOR, 1)}, oförändrade stödtjänster. Sänker bruttointäkten med ${ZONES.map((z, i) => `${z} −${pct(drops[i])}`).join(', ')}. Ett stressantagande: lägsta helår sedan 2023 är 0,76 × snittet.`;
  }

  // ------------------------------------------------------- investeringskalkyl
  function investDefaults() {
    Object.entries(INVEST_FIELDS).forEach(([id, key]) => { $(id).value = M.DEFAULTS[key]; });
    S.extremeDirty = false;
    S.negativeDirty = false;
    syncExtremeYearField();
  }

  function syncExtremeYearField() {
    const life = Math.round(fnum('inv-life', M.DEFAULTS.lifeYears));
    if (!S.extremeDirty) {
      const n = parseInt($('sel-extreme-years').value, 10) || 0;
      $('inv-extreme-years').value = M.defaultExtremeYearPositions(n, life).join(', ');
    }
    if (!S.negativeDirty) {
      const n = parseInt($('sel-negative-years').value, 10) || 0;
      $('inv-negative-years').value = M.defaultNegativeYearPositions(n, life, yearList('inv-extreme-years')).join(', ');
    }
  }

  function yearList(id) {
    return String($(id).value).split(/[^0-9]+/).filter(Boolean).map((v) => parseInt(v, 10));
  }

  function readInvest(site) {
    const r = site.res;
    const p = {};
    Object.entries(INVEST_FIELDS).forEach(([id, key]) => { p[key] = fnum(id, M.DEFAULTS[key]); });
    p.lifeYears = clamp(Math.round(p.lifeYears), 5, 30);
    const list = yearList('inv-extreme-years');
    return Object.assign(p, {
      capexTotal: r.fin.capex.total, batteryEnergyCapex: r.fin.capex.energy,
      spot: r.rev.spot, spotExtreme: r.rev.spotExtreme, anc: r.rev.anc,
      fixedOpex: r.fin.opexNormal.fixed, feePct: S.inp.feePct,
      extremeYearList: r.rev.extremeIsBase ? [] : list,
      ancExtreme: r.rev.ancExtreme,
      spotNegative: r.rev.spotNegative, negativeYearList: yearList('inv-negative-years'),
    });
  }

  function renderInvestment(site) {
    if ($('sec-invest').hidden || !site || !site.res || !site.res.passed) return;
    const p = readInvest(site);
    const m = M.investmentModel(p);
    S.lastInvest = { p, m, site };
    const hasDebt = m.debt > 0;
    const kpi = (lbl, val, sub, color) => `<div class="bs-kpi"><div class="bs-kpi-lbl">${lbl}</div><div class="bs-kpi-val" style="font-size:1.2em;${color ? ` color:${color};` : ''}">${val}</div><div class="bs-kpi-sub">${sub}</div></div>`;
    const irrTxt = (v) => (v === null ? 'Ej definierad' : `${num(v * 100, 1)} %`);
    $('invest-kpis').innerHTML = kpi('Nuvärde (NPV)', msek(m.npv, 0), `vid ${num(p.waccPct, 1)} % kalkylränta`, m.npv >= 0 ? '#34d399' : '#f87171')
      + kpi('Internränta, projekt', irrTxt(m.irr), 'efter skatt, utan belåning', m.irr !== null && m.irr * 100 >= p.waccPct ? '#34d399' : '#f87171')
      + (hasDebt ? kpi('Internränta, eget kapital', irrTxt(m.equityIrr), `${num(p.gearingPct)} % belåning`) : '')
      + kpi('Diskonterad återbetalning', m.discountedPayback === null ? `Inte inom ${p.lifeYears} år` : `${num(m.discountedPayback, 1)} år`, `odiskonterad: ${m.payback === null ? 'inte inom livslängden' : `${num(m.payback, 1)} år`}`)
      + (hasDebt ? kpi('Lägsta skuldtäckningsgrad', m.minDscr === null ? '–' : num(m.minDscr, 2), 'DSCR, sämsta året', m.minDscr !== null && m.minDscr < 1.2 ? '#f87171' : undefined) : '')
      + kpi('EBITDA över livslängden', msek(m.totalEbitda, 0), `intäkter ${msek(m.totalRevenue, 0)}`);

    $('txt-invest-intro').textContent = `${siteName(site)} i ${site.zone}, ${num(S.inp.mw)} MW / ${num(S.inp.mw * S.inp.durationH)} MWh. Nominellt kassaflöde år för år, efter skatt. År 1 utgår från den förenklade kalkylen: spot ${msek(p.spot)}, stödtjänster ${msek(p.anc)}, fasta kostnader ${msek(p.fixedOpex)}.`;
    const ext = m.rows.filter((x) => x.extreme).map((x) => x.year);
    const neg = m.rows.filter((x) => x.negative).map((x) => x.year);
    $('txt-invest-note').textContent = (site.res.rev.extremeIsBase
      ? 'Prisunderlaget är 2022, så extremår läggs inte till. '
      : (ext.length ? `Positiva extremår år ${ext.join(' och ')}: spot ${msek(p.spotExtreme)} och stödtjänster ${msek(p.ancExtreme)}, mot ${msek(p.spot)} och ${msek(p.anc)} ett normalår (i år 1-priser). ` : 'Inga positiva extremår. '))
      + (neg.length ? `Negativa extremår år ${neg.join(' och ')}: spotdelen ${msek(p.spotNegative)}. ` : '')
      + 'Degraderingen slår fullt på spotdelen och till hälften på stödtjänsterna. Slitageavsättningen ingår inte här; den ersätts av degradering och cellkomplettering.';

    renderInvestChart(m, p);
    renderInvestTable(m, hasDebt);
  }

  function renderInvestChart(m, p) {
    const W = 900; const H = 250; const padL = 56; const padR = 12; const padT = 14; const padB = 26;
    const flows = m.fcff;
    const cum = [flows[0]].concat(m.rows.map((x) => x.cumulativeDiscounted));
    const lo = Math.min(0, ...flows, ...cum);
    const hi = Math.max(0, ...flows, ...cum);
    const y = (v) => padT + (hi - v) / (hi - lo || 1) * (H - padT - padB);
    const bw = (W - padL - padR) / flows.length;
    const ticks = [lo, lo / 2, 0, hi / 2, hi].filter((v, i, a) => a.indexOf(v) === i);
    let svg = `<svg viewBox="0 0 ${W} ${H}" style="width:100%; height:auto;" role="img" aria-label="Fritt kassaflöde per år och ackumulerat diskonterat kassaflöde">`;
    ticks.forEach((t) => {
      svg += `<line x1="${padL}" x2="${W - padR}" y1="${y(t)}" y2="${y(t)}" stroke="rgba(255,255,255,${t === 0 ? 0.35 : 0.08})"/>`;
      svg += `<text x="${padL - 6}" y="${y(t) + 4}" text-anchor="end" font-size="11" fill="#94a3b8">${num(t / 1e6, 0)}</text>`;
    });
    flows.forEach((v, t) => {
      const ext = t > 0 && m.rows[t - 1].extreme;
      const negYear = t > 0 && m.rows[t - 1].negative;
      const color = v < 0 ? '#f87171' : ext ? '#fbbf24' : negYear ? '#a78bfa' : '#38bdf8';
      const top = Math.min(y(v), y(0));
      svg += `<rect x="${padL + t * bw + bw * 0.18}" y="${top}" width="${bw * 0.64}" height="${Math.max(1, Math.abs(y(v) - y(0)))}" fill="${color}"><title>År ${t}: ${msek(v)}</title></rect>`;
      if (flows.length <= 21 || t % 2 === 0) svg += `<text x="${padL + t * bw + bw / 2}" y="${H - 8}" text-anchor="middle" font-size="11" fill="#94a3b8">${t}</text>`;
    });
    svg += `<polyline fill="none" stroke="#34d399" stroke-width="2" points="${cum.map((v, t) => `${padL + t * bw + bw / 2},${y(v)}`).join(' ')}"/>`;
    svg += '</svg>';
    $('invest-chart').innerHTML = `<div style="font-size:0.78em; color:var(--muted); margin-bottom:0.3rem;">MSEK per år.
      <span style="color:#38bdf8;">■</span> fritt kassaflöde &nbsp;<span style="color:#fbbf24;">■</span> positivt extremår &nbsp;<span style="color:#a78bfa;">■</span> negativt extremår &nbsp;<span style="color:#f87171;">■</span> negativt &nbsp;<span style="color:#34d399;">━</span> ackumulerat, diskonterat med ${num(p.waccPct, 1)} %</div>${svg}`;
  }

  function investColumns(hasDebt) {
    const cols = [
      ['År', (x) => x.year], ['Kapacitet', (x) => pct(x.capacity)], ['Spot', (x) => x.spot], ['Stödtjänster', (x) => x.anc],
      ['Intäkt', (x) => x.revenue], ['Arvode', (x) => -x.fee], ['Fasta kostnader', (x) => -x.fixed], ['EBITDA', (x) => x.ebitda],
      ['Avskrivning', (x) => -x.depreciation], ['Skatt', (x) => -x.tax], ['Cellkomplettering', (x) => -x.augmentation],
      ['Fritt kassaflöde', (x) => x.fcff], ['Ackumulerat, diskonterat', (x) => x.cumulativeDiscounted],
    ];
    if (hasDebt) cols.push(['Ränta', (x) => -x.interest], ['Amortering', (x) => -x.principal], ['Till eget kapital', (x) => x.fcfe]);
    return cols;
  }

  function renderInvestTable(m, hasDebt) {
    const cols = investColumns(hasDebt);
    const cell = (v) => (typeof v === 'string' ? v : (Math.abs(v) < 1 ? '–' : num(v / 1e6, 1)));
    let html = `<thead><tr>${cols.map((c) => `<th>${c[0]}</th>`).join('')}</tr></thead><tbody>`;
    html += `<tr><td>0</td>${cols.slice(1).map((c) => `<td>${c[0] === 'Fritt kassaflöde' || c[0] === 'Ackumulerat, diskonterat' ? num(m.fcff[0] / 1e6, 1) : (c[0] === 'Till eget kapital' ? num(m.fcfe[0] / 1e6, 1) : '')}</td>`).join('')}</tr>`;
    m.rows.forEach((x) => {
      const tag = x.extreme ? ' (positivt extremår)' : x.negative ? ' (negativt extremår)' : '';
      html += `<tr class="${x.extreme ? 'bs-ext' : x.negative ? 'bs-neg' : ''}">${cols.map((c, i) => `<td>${i === 0 ? `${x.year}${tag}` : cell(c[1](x))}</td>`).join('')}</tr>`;
    });
    $('invest-table').innerHTML = `${html}</tbody>`;
  }

  function downloadInvestCsv() {
    if (!S.lastInvest) return;
    const { m, p, site } = S.lastInvest;
    const cols = investColumns(m.debt > 0);
    const f = (v) => (typeof v === 'string' ? v.replace(/\u00a0/g, ' ') : String(Math.round(v) + 0));
    const lines = [
      `BESS Investment Studio;${siteName(site)};${site.zone};${S.inp.mw} MW;${S.inp.mw * S.inp.durationH} MWh`,
      `Prisunderlag;${S.inp.dataset.label}`,
      `NPV (SEK);${Math.round(m.npv)};IRR projekt;${m.irr === null ? '' : String((m.irr * 100).toFixed(2)).replace('.', ',')} %;Kalkylränta;${String(p.waccPct).replace('.', ',')} %`,
      '',
      cols.map((c) => c[0]).join(';') + ' (SEK)',
      ['0', '', '', '', '', '', '', '', '', '', '', Math.round(m.fcff[0]), Math.round(m.fcff[0])].concat(m.debt > 0 ? ['', '', Math.round(m.fcfe[0])] : []).join(';'),
    ];
    m.rows.forEach((x) => lines.push(cols.map((c) => f(c[1](x))).join(';')));
    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `bess-kassaflode-${site.zone}-${S.inp.mw}MW.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  function toggleInvest(open) {
    const sec = $('sec-invest');
    const show = open === undefined ? sec.hidden : open;
    sec.hidden = !show;
    const b = $('btn-toggle-invest');
    b.setAttribute('aria-expanded', String(show));
    b.textContent = show ? 'Dölj investeringskalkyl' : 'Visa investeringskalkyl (NPV och IRR)';
    if (show) {
      renderInvestment(siteById(S.selectedId));
      sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    saveHash();
  }

  // -------------------------------------------------------------- känslighet
  function renderSensitivity(site) {
    const base = site.res.fin.payback;
    $('txt-sens-baseline').textContent = `${siteName(site)}: ${yearsTxt(base)}`;
    const inp = S.inp;
    const isBase22 = site.res.rev.extremeIsBase;
    const pb = (changes) => {
      const r = evaluateWith(site, changes);
      return r.passed ? r.fin.payback : null;
    };
    const drivers = [
      { name: 'Intäkter, alla marknader', a: ['−20 %', { revMult: 0.8 }], b: ['+20 %', { revMult: 1.2 }] },
      { name: 'Realiseringsgrad', a: [`${num(Math.max(40, inp.realizationPct - 15))} %`, { realizationPct: Math.max(40, inp.realizationPct - 15) }], b: [`${num(Math.min(100, inp.realizationPct + 15))} %`, { realizationPct: Math.min(100, inp.realizationPct + 15) }] },
      { name: 'Andel av stödtjänstmarknaderna', a: [`${num(inp.marketSharePct / 2, 1)} %`, { marketSharePct: inp.marketSharePct / 2 }], b: [`${num(Math.min(30, inp.marketSharePct * 2))} %`, { marketSharePct: Math.min(30, inp.marketSharePct * 2) }] },
      { name: 'Positiva extremår under tio år', note: isBase22 ? 'ingen effekt: prisunderlaget är redan 2022' : '', a: ['0 år', { extremeYears: 0 }], b: ['2 år', { extremeYears: 2 }] },
      { name: 'Stödtjänster i positivt extremår', note: inp.extremeYears > 0 && !isBase22 ? '' : 'ingen effekt utan positiva extremår', a: ['× 1', { ancillaryExtremeMult: 1 }], b: ['× 3', { ancillaryExtremeMult: 3 }] },
      { name: 'Negativa extremår under tio år', a: ['0 år', { negativeYears: 0 }], b: ['2 år', { negativeYears: 2 }] },
      { name: 'Battericeller och kraftelektronik', a: ['+20 %', { capexPerKwh: inp.capexPerKwh * 1.2, capexPerKw: inp.capexPerKw * 1.2 }], b: ['−20 %', { capexPerKwh: inp.capexPerKwh * 0.8, capexPerKw: inp.capexPerKw * 0.8 }] },
      { name: 'Anslutning (kabel och stationsfack)', a: ['+30 %', { cablePerKmMsek: inp.cablePerKmMsek * 1.3, bayMsek: inp.bayMsek * 1.3 }], b: ['−30 %', { cablePerKmMsek: inp.cablePerKmMsek * 0.7, bayMsek: inp.bayMsek * 0.7 }] },
      { name: 'Fasta driftkostnader', a: ['+25 %', { omPerKw: inp.omPerKw * 1.25, gridPerKw: inp.gridPerKw * 1.25, otherPctCapex: inp.otherPctCapex * 1.25 }], b: ['−25 %', { omPerKw: inp.omPerKw * 0.75, gridPerKw: inp.gridPerKw * 0.75, otherPctCapex: inp.otherPctCapex * 0.75 }] },
      { name: 'Anläggningens storlek', note: 'samma varaktighet; större anläggning möter marknadstaket', a: [`${num(Math.max(5, inp.mw / 2))} MW`, { mw: Math.max(5, inp.mw / 2) }], b: [`${num(Math.min(300, inp.mw * 2))} MW`, { mw: Math.min(300, inp.mw * 2) }] },
      { name: 'Varaktighet', a: ['1 h', { durationH: 1 }], b: ['4 h', { durationH: 4 }] },
    ].map((d) => {
      const va = pb(d.a[1]);
      const vb = pb(d.b[1]);
      const val = (v) => (v === null ? 99 : v);
      const baseV = val(base);
      return Object.assign(d, { va, vb, da: val(va) - baseV, db: val(vb) - baseV, span: Math.abs(val(va) - val(vb)) });
    });
    drivers.sort((x, y) => y.span - x.span);
    const maxAbs = Math.max(0.01, ...drivers.map((d) => Math.max(Math.abs(d.da), Math.abs(d.db))));

    const chip = (label, v, delta) => {
      const w = Math.round(Math.abs(delta) / maxAbs * 100);
      const good = delta < -1e-9;
      const flat = Math.abs(delta) <= 1e-9;
      const color = flat ? '#64748b' : good ? '#34d399' : '#f87171';
      return `<div style="display:flex; align-items:center; gap:6px; margin:2px 0;">
        <span style="min-width:64px; color:var(--muted);">${label}</span>
        <div style="flex:1; height:10px; background:rgba(255,255,255,0.04); border-radius:3px; overflow:hidden; display:flex; ${good ? 'justify-content:flex-end;' : ''}"><div style="width:${w}%; background:${color};"></div></div>
        <span style="min-width:86px; text-align:right; font-family:var(--mono); color:${color}; font-weight:600;">${v === null ? 'ingen' : `${num(v, 1)} år`}</span>
      </div>`;
    };
    let html = '<div class="bs-sens-head"><div>Antagande</div><div>Kortare återbetalningstid</div><div>Längre återbetalningstid</div></div>';
    drivers.forEach((d) => {
      const items = [[d.a[0], d.va, d.da], [d.b[0], d.vb, d.db]].map((i) => (Math.abs(i[2]) <= 1e-9 ? [`${i[0]} (som nu)`, i[1], i[2]] : i));
      const left = items.filter((i) => i[2] < -1e-9).map((i) => chip(...i)).join('');
      const right = items.filter((i) => i[2] >= -1e-9).map((i) => chip(...i)).join('');
      html += `<div class="bs-sens-row"><div><strong style="color:#fff;">${d.name}</strong>${d.note ? `<br><span style="color:var(--muted); font-size:0.9em;">${d.note}</span>` : ''}</div><div>${left}</div><div>${right}</div></div>`;
    });
    $('sens-tornado-container').innerHTML = html;
  }

  // ---------------------------------------------------------------- underlag
  function renderTransparency() {
    const box = $('hist-transparency-content');
    if (!S.ready) return;
    document.querySelectorAll('#hist-year-tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === S.tab));
    const d = S.disp;
    const h = S.inp.durationH;

    if (S.tab === 'spot') {
      const keys = ['last12m'].concat(d.full_years.map(String).reverse());
      let html = `<p class="bs-note" style="margin-top:0;">Ren spotarbitrage för ett lager på ${num(h, 1)} h, EUR per MW och år. Optimerad vecka för vecka med perfekt förutseende – ett tak, inte en prognos. Kolumnen till höger är kvoten mot de senaste 12 månaderna, alltså den faktor spotdelen skalas med i ett spotscenario.</p>
        <div class="bs-scroll"><table class="bs-table" style="font-size:0.84em;"><thead><tr><th>Prisår</th>${ZONES.map((z) => `<th>${z}</th>`).join('')}<th>SE4, kvot mot 12 mån</th></tr></thead><tbody>`;
      keys.forEach((k) => {
        const base4 = M.spotOnly(d, 'SE4', h, 'last12m');
        const v4 = M.spotOnly(d, 'SE4', h, k);
        html += `<tr class="${k === M.EXTREME_YEAR ? 'bs-ext' : ''}"><td>${k === 'last12m' ? 'Senaste 12 månaderna' : k}${k === M.EXTREME_YEAR ? ' (extremår)' : ''}</td>`
          + ZONES.map((z) => `<td class="num">${num(M.spotOnly(d, z, h, k))}</td>`).join('')
          + `<td class="num">${num(v4 / base4, 2)}</td></tr>`;
      });
      box.innerHTML = `${html}</tbody></table></div>`;
    } else if (S.tab === 'anc') {
      const periods = Object.keys(d.periods).sort((a, b) => (a === 'last12m' ? -1 : b === 'last12m' ? 1 : Number(b) - Number(a)));
      let html = '<p class="bs-note" style="margin-top:0;">Kapacitetsmarknaderna per elområde: medelpris över alla timmar, medelvolym och vad 1 MW skulle ha fått om det antagits varje timme till marginalpris. FCR är en gemensam marknad för Sverige och östra Danmark och har samma värden i alla elområden.</p>';
      periods.forEach((pn) => {
        const span = d.periods[pn];
        html += `<h4 style="font-size:0.9em; margin:1rem 0 0.4rem; color:#38bdf8;">${pn === 'last12m' ? 'Senaste 12 månaderna' : pn} <span style="color:var(--muted); font-weight:400;">(${dateOnly(span.from)} – ${dateOnly(span.to)})</span></h4>
          <div class="bs-scroll"><table class="bs-table" style="font-size:0.82em;"><thead><tr><th>Marknad</th>${ZONES.map((z) => `<th>${z} pris</th><th>${z} volym</th>`).join('')}<th>SE4, tak per MW</th></tr></thead><tbody>`;
        d.products.slice(1).forEach((p) => {
          html += `<tr><td>${PRODUCT_LABELS[p]}</td>` + ZONES.map((z) => {
            const mk = d.market[pn][z][p];
            return `<td class="num">${num(mk.price_mean, 1)}</td><td class="num">${num(mk.volume_mean_mw)}</td>`;
          }).join('') + `<td class="num">${num(d.market[pn].SE4[p].value_eur_mw_yr)}</td></tr>`;
        });
        html += '</tbody></table></div>';
      });
      box.innerHTML = `${html}<p class="bs-note">Pris i EUR/MW per timme, volym i MW, tak i EUR per MW och år.</p>`;
    } else {
      const li = (name, text) => `<li style="margin-bottom:0.5rem;"><strong style="color:#fff;">${name}</strong><br>${text}</li>`;
      const ly = (S.meta && S.meta.layers) || {};
      const layer = (k) => (ly[k] ? `${esc(ly[k].source)}. Hämtad ${dateOnly(ly[k].retrieved_at)}${ly[k].count ? `, ${num(ly[k].count)} objekt` : ''}. ${esc(ly[k].note || '')}` : '–');
      box.innerHTML = `<ul style="font-size:0.85em; color:var(--muted); line-height:1.55; padding-left:1.1rem;">
        ${li('Spotpriser', esc(d.sources.spot))}
        ${li('mFRR', esc(d.sources.mfrr))}
        ${li('aFRR', esc(d.sources.afrr))}
        ${li('FCR', esc(d.sources.fcr))}
        ${li('Optimering', `${esc(d.model)}. Beräknad ${dateOnly(d.generated_at)}. Verkningsgrad ${pct(d.assumptions.rte)}, laddnivå ${pct(d.assumptions.soc_min)}–${pct(d.assumptions.soc_max)}. Ingår inte: ${d.assumptions.not_modelled.join(', ')}.`)}
        ${li('Växelkurs', `${num(S.inp.fx, 2)} SEK per EUR (ECB, uppdateras med prognosen).`)}
        ${li('Stationer', layer('substations'))}
        ${li('Ledningar', layer('lines'))}
        ${li('Lokalnätskoncessioner', layer('concessions'))}
        ${li('Skyddad natur', layer('protected'))}
        ${li('Flexmarknader', layer('flex'))}
      </ul>`;
    }
  }

  // ---------------------------------------------------------------- körschema
  function openDispatch() {
    const site = siteById(S.selectedId);
    if (!site || !site.res || !site.res.passed) return;
    const rev = site.res.rev;
    const inp = S.inp;
    const d = S.disp;
    const span = d.periods[rev.basePeriod];
    $('disp-intro').textContent = `${site.zone}, ${num(inp.mw)} MW / ${num(inp.mw * inp.durationH)} MWh. Stödtjänster och samoptimering bygger på ${dateOnly(span.from)} – ${dateOnly(span.to)}. `
      + (inp.dataset.kind === 'spot' ? `Spotdelen är omräknad till ${inp.dataset.label.toLowerCase()} (faktor ${num(rev.spotFactor, 2)}).` : 'Alla priser är faktiska timpriser för perioden.');
    $('disp-kpi-theory').textContent = `${num(rev.theoreticalPerMwEur)} €`;
    $('disp-kpi-real').textContent = `${num(rev.gross / inp.mw / inp.fx)} €`;
    $('disp-kpi-real-sub').textContent = `per MW och år, ${num(inp.realizationPct)} % realiseringsgrad = ${msek(rev.gross)}`;
    $('disp-kpi-cycles').textContent = num(rev.cycles);

    let body = '';
    d.products.forEach((p) => {
      const s = rev.sold[p];
      const v = rev.perProduct[p];
      body += `<tr><td style="color:${PRODUCT_COLORS[p]}; font-weight:600;">${PRODUCT_LABELS[p]}</td>
        <td>${p === 'spot' ? '–' : `${num(s.mw, 1)} MW`}</td>
        <td>${s.marketShare === null ? '–' : pct(s.marketShare, 1)}</td>
        <td>${pct(s.hoursShare)}</td>
        <td>${s.avgPrice === null ? '–' : `${num(s.avgPrice, 1)} €/MW/h`}</td>
        <td style="font-weight:700;">${msek(v)}</td></tr>`;
    });
    body += `<tr><td style="font-weight:700;">Summa brutto</td><td colspan="4" style="color:var(--muted);">efter ${num(inp.realizationPct)} % realiseringsgrad</td><td style="font-weight:700; color:#34d399;">${msek(rev.gross)}</td></tr>`;
    $('disp-alloc-table-body').innerHTML = body;

    const dk = String(d.durations.reduce((a, b) => (Math.abs(b - inp.durationH) < Math.abs(a - inp.durationH) ? b : a)));
    const wk = (d.sample_week[site.zone] || {})[dk];
    if (!wk) {
      $('disp-week-title').textContent = 'Exempelvecka saknas';
      $('disp-week-head').innerHTML = '';
      $('disp-table-body').innerHTML = '';
    } else {
      const start = new Date(wk.start);
      const refMw = Math.round((0.05 / d.sample_rho));
      $('disp-week-title').textContent = `Exempelvecka i ${site.zone}, från ${wk.start.slice(0, 10)}`;
      $('disp-week-note').textContent = `Optimerat schema för ett lager på ${dk} h i referensfallet (ungefär ${refMw} MW vid 5 % marknadsandel). Värdena är andel av installerad effekt och visar hur lagret växlar mellan marknaderna – inte din anläggnings exakta schema.`;
      const cols = d.sample_cols;
      const used = cols.map((c, i) => i < 2 || wk.rows.some((r) => r[i] > 0.005));
      const head = { spot: 'Spot €/MWh', soc_pct: 'Laddnivå', charge: 'Laddar', discharge: 'Laddar ur' };
      $('disp-week-head').innerHTML = '<th>Tid</th>' + cols.map((c, i) => (used[i] ? `<th>${head[c] || PRODUCT_LABELS[c]}</th>` : '')).join('');
      const fmtT = new Intl.DateTimeFormat('sv-SE', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Stockholm' });
      $('disp-table-body').innerHTML = wk.rows.map((r, i) => {
        const t = new Date(start.getTime() + i * 3600e3);
        return `<tr><td>${fmtT.format(t)}</td>` + r.map((v, j) => {
          if (!used[j]) return '';
          if (j === 0) return `<td>${num(v, 1)}</td>`;
          if (j === 1) return `<td style="color:#38bdf8;">${num(v)} %</td>`;
          return `<td style="color:${v > 0.005 ? (PRODUCT_COLORS[cols[j]] || (j === 2 ? '#34d399' : '#fbbf24')) : 'var(--faint)'};">${v > 0.005 ? `${num(v * 100)} %` : '–'}</td>`;
        }).join('') + '</tr>';
      }).join('');
    }
    openModal('modal-dispatch');
  }

  // --------------------------------------------------------------- jämförelse
  function toggleCompare(id) {
    const msg = $('txt-compare-msg');
    msg.textContent = '';
    if (S.compare.has(id)) S.compare.delete(id);
    else if (S.compare.size >= 5) { msg.textContent = 'Högst fem platser. Ta bort en först.'; $('box-compare-tray').style.display = 'flex'; return; }
    else S.compare.add(id);
    updateCompareTray();
    updateCompareButton();
  }

  function updateCompareButton() {
    const b = $('btn-toggle-compare');
    const on = S.compare.has(S.selectedId);
    b.textContent = on ? 'Ta bort ur jämförelse' : 'Lägg till i jämförelse';
    b.style.borderColor = on ? 'rgba(16,185,129,0.5)' : '';
    b.style.color = on ? '#34d399' : '';
  }

  function updateCompareTray() {
    $('txt-compare-count').textContent = S.compare.size;
    const tray = $('box-compare-tray');
    tray.style.display = S.compare.size ? 'flex' : 'none';
    const box = $('compare-chips-container');
    box.innerHTML = '';
    S.compare.forEach((id) => {
      const site = siteById(id);
      if (!site) { S.compare.delete(id); return; }
      const chip = document.createElement('div');
      chip.className = 'bs-compare-chip';
      chip.innerHTML = `<span style="cursor:pointer;">${esc(siteName(site))} (${site.zone})</span><button type="button" class="del" aria-label="Ta bort ${esc(siteName(site))}" style="background:none; border:none; padding:0 2px;">&times;</button>`;
      chip.querySelector('.del').addEventListener('click', (e) => { e.stopPropagation(); toggleCompare(id); });
      chip.firstChild.addEventListener('click', () => selectSite(site, true));
      box.appendChild(chip);
    });
  }

  function openCompare() {
    if (!S.compare.size && S.selectedId) { S.compare.add(S.selectedId); updateCompareTray(); updateCompareButton(); }
    const sites = Array.from(S.compare).map(siteById).filter((s) => s && s.res && s.res.passed);
    const t = $('table-compare');
    if (!sites.length) {
      t.innerHTML = '<tr><td style="padding:1.5rem; text-align:center; color:var(--muted);">Inga platser valda. Välj en ruta i kartan och klicka på "Lägg till i jämförelse".</td></tr>';
    } else {
      const rows = [
        ['Återbetalningstid', (s) => `<strong style="color:${paybackColor(s.res.fin.payback)};">${yearsTxt(s.res.fin.payback)}</strong>`],
        ['Utan extremår', (s) => yearsTxt(s.res.fin.paybackNormal)],
        ['Positivt extremår, driftnetto', (s) => msek(s.res.fin.netExtreme)],
        ['Negativt extremår, driftnetto', (s) => msek(s.res.fin.netNegative)],
        ['Investering', (s) => msek(s.res.fin.capex.total, 0)],
        ['varav kabel', (s) => `${msek(s.res.fin.capex.cable)} (${num(s.res.cableKm, 1)} km)`],
        ['Bruttointäkt per år', (s) => msek(s.res.rev.gross)],
        ['varav stödtjänster', (s) => msek(s.res.rev.anc)],
        ['varav spotarbitrage', (s) => msek(s.res.rev.spot)],
        ['Driftkostnader per år', (s) => msek(s.res.fin.opexNormal.total)],
        ['Driftnetto per år', (s) => `<strong>${msek(s.res.fin.netNormal)}</strong>`],
        ['Driftnetto 10 år', (s) => msek(s.res.fin.tenYear, 0)],
        ['Närmaste station', (s) => stationLabel(s.res.station)],
        ['Platspoäng', (s) => `<span class="bs-badge ${scoreClass(s.res.score)}">${s.res.score}/100</span>`],
        ['Skyddad natur', (s) => pct(s.prot || 0)],
        ['Vattenskydd', (s) => pct(s.water || 0)],
        ['Lokal flexmarknad', (s) => esc(flexText(s))],
      ];
      t.innerHTML = `<thead><tr><th>Nyckeltal</th>${sites.map((s) => `<th><strong style="color:#fff;">${esc(siteName(s))}</strong><br><small style="color:#38bdf8;">${s.zone} · ${esc(s.dso)}</small></th>`).join('')}</tr></thead><tbody>`
        + rows.map((r) => `<tr><td style="color:var(--muted);">${r[0]}</td>${sites.map((s) => `<td>${r[1](s)}</td>`).join('')}</tr>`).join('') + '</tbody>';
    }
    openModal('modal-compare');
  }

  // -------------------------------------------------------------------- modal
  let lastFocus = null;
  function openModal(id) {
    lastFocus = document.activeElement;
    const m = $(id);
    m.style.display = 'flex';
    document.body.style.overflow = 'hidden';
    const btn = m.querySelector('[data-close]');
    if (btn) btn.focus();
  }
  function closeModals() {
    let any = false;
    document.querySelectorAll('.bs-modal-backdrop').forEach((m) => { if (m.style.display === 'flex') any = true; m.style.display = 'none'; });
    document.body.style.overflow = '';
    if (any && lastFocus && lastFocus.focus) lastFocus.focus();
  }

  // --------------------------------------------------------------------- hash
  function saveHash() {
    if (!S.ready) return;
    const q = new URLSearchParams();
    HASH_FIELDS.forEach((id) => q.set(id, $(id).value));
    if (S.selectedId && S.selectedId !== 'custom') q.set('site', S.selectedId);
    if (S.custom) q.set('custom', `${S.custom.c[0].toFixed(5)},${S.custom.c[1].toFixed(5)}${S.custom.customKm !== null ? `,${S.custom.customKm}` : ''}`);
    if (S.selectedId === 'custom') q.set('site', 'custom');
    if (!$('sec-invest').hidden) q.set('invest', '1');
    history.replaceState(null, '', `#${q.toString()}`);
  }

  function loadHash() {
    const q = new URLSearchParams(location.hash.replace(/^#/, ''));
    if (![...q.keys()].length) return null;
    HASH_FIELDS.forEach((id) => {
      if (!q.has(id)) return;
      const el = $(id);
      if (el.tagName === 'SELECT') { if ([...el.options].some((o) => o.value === q.get(id))) el.value = q.get(id); } else el.value = q.get(id);
    });
    $('range-power-mw').value = $('num-power-mw').value;
    $('range-duration').value = $('num-duration').value;
    if (q.has('inv-extreme-years')) S.extremeDirty = true;
    if (q.has('inv-negative-years')) S.negativeDirty = true;
    return q;
  }

  // --------------------------------------------------------------------- data
  async function getJson(path, required) {
    try {
      const r = await fetch(path, { cache: 'no-cache' });
      if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
      return await r.json();
    } catch (err) {
      if (required) throw err;
      console.warn('Kunde inte ladda', path, err);
      return null;
    }
  }

  async function loadConcessions() {
    if (S.concLoaded) return;
    S.concLoaded = true;
    const geo = await getJson('data/bess-map/concessions.json', false);
    if (!geo) return;
    L.geoJSON(geo, {
      style: { color: '#a855f7', weight: 1.2, dashArray: '2, 3', fillColor: '#a855f7', fillOpacity: 0.06 },
      onEachFeature: (feat, layer) => layer.bindPopup(`<strong>Lokalnätskoncession</strong><br>${esc((feat.properties || {}).owner || 'Okänd')}`),
    }).addTo(layers.conc);
  }

  async function loadData() {
    const status = $('bs-map-status');
    try {
      const [cells, market, meta, disp, subs] = await Promise.all([
        getJson('data/bess-map/cells.json', true), getJson('data/bess-map/market.json', true),
        getJson('data/bess-map/meta.json', true), getJson('data/bess-map/dispatch_backtest.json', true),
        getJson('data/bess-map/substations.json', true),
      ]);
      if (!disp.coopt || !disp.spot_years) throw new Error('dispatch_backtest.json har fel format (förväntade version 2)');
      S.cells = cells; S.market = market; S.meta = meta; S.disp = disp; S.stations = subs;
      S.datasets = M.datasets(disp);

      const sel = $('sel-dataset');
      sel.innerHTML = S.datasets.map((d) => `<option value="${d.id}">${esc(d.label)}</option>`).join('');

      cells.forEach((c) => {
        S.cellById.set(c.id, c);
        c.st = { regional: M.nearestStation(c.c[0], c.c[1], subs, 'regional'), any: M.nearestStation(c.c[0], c.c[1], subs, 'any') };
      });

      subs.forEach((s) => {
        const trans = s[2] >= 200;
        const regional = s[2] >= 100 && !trans;
        L.circleMarker([s[0], s[1]], {
          radius: trans ? 4 : regional ? 3.2 : 2.4, color: trans ? '#fb923c' : '#38bdf8', fillColor: regional ? '#ffffff' : (trans ? '#fb923c' : '#94a3b8'),
          fillOpacity: 0.85, weight: 1,
        }).bindPopup(`<strong>${esc(s[3] || 'Namnlös station')}</strong><br>${num(s[2])} kV${s[4] ? `<br>${esc(s[4])}` : ''}${trans ? '<br><em>Stamnät – räknas inte som anslutningspunkt</em>' : ''}`).addTo(layers.subs);
      });
      $('lbl-lyr-subs').textContent = `Stationer (${num(subs.length)})`;

      const q = loadHash();
      investDefaultsIfEmpty();
      buildCellLayer();
      S.ready = true;

      $('bs-data-stamp').innerHTML = `<br>Marknadsdata till och med ${dateOnly(disp.periods.last12m.to)} · optimering beräknad ${dateOnly(disp.generated_at)} · kartlager ${dateOnly(meta.built_at)} · ${num(market.fx_eur_sek, 2)} SEK/EUR.`;
      status.textContent = `${num(cells.length)} analysrutor och ${num(subs.length)} stationer laddade.`;

      S.inp = readInputs();
      ZONES.forEach((z) => { S.zoneRev[z] = zoneRevenue(S.inp, z); });
      if (q && q.has('custom')) {
        const [la, lo, km] = q.get('custom').split(',').map(Number);
        if (isFinite(la) && isFinite(lo)) setCustomSite(la, lo, isFinite(km) ? km : undefined);
      }
      if (q && q.has('site')) S.selectedId = q.get('site');
      recalcAll();
      if (q && q.get('invest') === '1') toggleInvest(true);
      const sel0 = siteById(S.selectedId);
      if (q && sel0) map.setView(sel0.c, 8);

      // Sekundära lager: kartan fungerar utan dem.
      getJson('data/bess-map/lines.json', false).then((lines) => (lines || []).forEach((ln) => {
        L.polyline(ln.c, { color: ln.kv >= 380 ? '#f43f5e' : ln.kv >= 220 ? '#fb923c' : '#38bdf8', weight: 1.2, opacity: 0.45, interactive: false }).addTo(layers.lines);
      }));
      getJson('data/bess-map/flex.json', false).then((list) => {
        (list || []).forEach((f) => {
          S.flexNames[f.id] = f.name;
          L.circle([f.lat, f.lon], { radius: f.radius_km * 1000, color: '#34d399', fillColor: '#34d399', fillOpacity: 0.08, weight: 1.5, dashArray: '3, 4', interactive: false }).addTo(layers.flex);
        });
        const s = siteById(S.selectedId);
        if (s && s.res && s.res.passed) $('det-flex').textContent = flexText(s);
      });
    } catch (err) {
      console.error(err);
      status.textContent = 'Data kunde inte laddas.';
      $('bs-databar').innerHTML = `<strong>Data kunde inte laddas.</strong> Inga beräkningar visas. Ladda om sidan; kvarstår felet är datafilerna inte publicerade. <span style="font-family:var(--mono);">${esc(err.message)}</span>`;
      $('title-selected-site').textContent = 'Ingen data';
    }
  }

  function investDefaultsIfEmpty() {
    Object.entries(INVEST_FIELDS).forEach(([id, key]) => { if ($(id).value === '') $(id).value = M.DEFAULTS[key]; });
    syncExtremeYearField();
  }

  // ------------------------------------------------------------------- events
  function bindEvents() {
    const pair = (rangeId, numId, lo, hi) => {
      $(rangeId).addEventListener('input', () => { $(numId).value = $(rangeId).value; scheduleRecalc(); });
      $(numId).addEventListener('input', () => { const v = parseFloat($(numId).value); if (isFinite(v)) { $(rangeId).value = clamp(v, lo, hi); scheduleRecalc(); } });
      $(numId).addEventListener('change', () => { $(numId).value = clamp(fnum(numId, lo), lo, hi); $(rangeId).value = $(numId).value; recalcAll(); });
    };
    pair('range-power-mw', 'num-power-mw', 5, 300);
    pair('range-duration', 'num-duration', 1, 4);
    RANGE_LABELS.forEach(([rid]) => { $(rid).addEventListener('input', scheduleRecalc); $(rid).addEventListener('change', recalcAll); });
    $('sel-dataset').addEventListener('change', recalcAll);
    $('sel-extreme-years').addEventListener('change', () => { S.extremeDirty = false; syncExtremeYearField(); recalcAll(); });
    $('sel-negative-years').addEventListener('change', () => { S.negativeDirty = false; syncExtremeYearField(); recalcAll(); });
    $('sel-map-color-mode').addEventListener('change', () => {
      const byScore = $('sel-map-color-mode').value === 'score';
      $('legend-score-wrap').style.display = byScore ? 'inline' : 'none';
      $('legend-payback-wrap').style.display = byScore ? 'none' : 'inline';
      restyleCells();
    });

    $('btn-find-best').addEventListener('click', () => { const c = bestCell(map.getBounds()) || bestCell(null); if (c) selectSite(c, true); });
    $('btn-prev-site').addEventListener('click', () => step(-1));
    $('btn-next-site').addEventListener('click', () => step(1));
    $('btn-custom-site').addEventListener('click', () => setPickMode(!S.pickMode));
    $('num-custom-km').addEventListener('input', () => {
      if (!S.custom) return;
      const v = parseFloat($('num-custom-km').value);
      S.custom.customKm = isFinite(v) && v >= 0 ? v : null;
      S.custom.res = evaluateSite(S.custom, S.inp, S.zoneRev[S.custom.zone], S.bestGrossPerMw);
      if (S.selectedId === 'custom') renderSelected(S.custom);
      saveHash();
    });

    $('top-zone-tabs').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-zone]');
      if (!b) return;
      document.querySelectorAll('#top-zone-tabs button').forEach((x) => x.classList.toggle('active', x === b));
      S.topZone = b.dataset.zone;
      updateRanking();
    });
    $('hist-year-tabs').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-tab]');
      if (b) { S.tab = b.dataset.tab; renderTransparency(); }
    });

    $('btn-toggle-compare').addEventListener('click', () => { if (S.selectedId) toggleCompare(S.selectedId); });
    $('btn-open-compare').addEventListener('click', openCompare);
    $('btn-show-compare-modal').addEventListener('click', openCompare);
    $('btn-clear-compare').addEventListener('click', () => { S.compare.clear(); $('txt-compare-msg').textContent = ''; updateCompareTray(); updateCompareButton(); });
    $('btn-inspect-dispatch').addEventListener('click', openDispatch);
    $('btn-open-method').addEventListener('click', () => openModal('modal-method'));
    $('btn-toggle-invest').addEventListener('click', () => toggleInvest());
    $('btn-invest-reset').addEventListener('click', () => { investDefaults(); renderInvestment(siteById(S.selectedId)); saveHash(); });
    $('btn-invest-csv').addEventListener('click', downloadInvestCsv);
    $('btn-print').addEventListener('click', () => window.print());
    $('btn-copy-link').addEventListener('click', async () => {
      saveHash();
      const b = $('btn-copy-link');
      try { await navigator.clipboard.writeText(location.href); b.textContent = 'Länk kopierad'; } catch (e) { b.textContent = 'Kopiera adressen i adressfältet'; }
      setTimeout(() => { b.textContent = 'Kopiera länk till scenariot'; }, 2500);
    });

    Object.keys(INVEST_FIELDS).forEach((id) => $(id).addEventListener('input', () => {
      if (id === 'inv-life') syncExtremeYearField();
      renderInvestment(siteById(S.selectedId));
      saveHash();
    }));
    $('inv-extreme-years').addEventListener('input', () => { S.extremeDirty = true; syncExtremeYearField(); renderInvestment(siteById(S.selectedId)); saveHash(); });
    $('inv-negative-years').addEventListener('input', () => { S.negativeDirty = true; renderInvestment(siteById(S.selectedId)); saveHash(); });

    document.addEventListener('click', (e) => {
      if (e.target.closest('[data-close]') || e.target.classList.contains('bs-modal-backdrop')) { closeModals(); return; }
      const act = e.target.closest('[data-act]');
      if (!act) return;
      if (act.dataset.act === 'compare') { toggleCompare(act.dataset.id); map.closePopup(); }
      if (act.dataset.act === 'dispatch') openDispatch();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      if (S.pickMode) setPickMode(false);
      closeModals();
    });
  }

  function init() {
    updateLabels();
    bindEvents();
    initMap();
    loadData();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
