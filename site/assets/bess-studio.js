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
  const selHybridRenewable = document.getElementById('sel-hybrid-renewable');
  const lblHybridSaving = document.getElementById('lbl-hybrid-saving');

  const btnFindBest = document.getElementById('btn-find-best');
  const btnPrevSite = document.getElementById('btn-prev-site');
  const btnNextSite = document.getElementById('btn-next-site');
  const txtStepperInfo = document.getElementById('txt-stepper-info');
  const btnZoomAll = document.getElementById('btn-zoom-all');
  const mapStatus = document.getElementById('bs-map-status');

  // KPI Elements
  const kpiPayback = document.getElementById('kpi-payback');
  const kpiPaybackSub = document.getElementById('kpi-payback-sub');
  const kpiCapex = document.getElementById('kpi-capex');
  const kpiCapexSub = document.getElementById('kpi-capex-sub');
  const kpiEbitda = document.getElementById('kpi-ebitda');
  const kpi10yrCf = document.getElementById('kpi-10yr-cf');
  const badgeRoi = document.getElementById('badge-decision-roi');
  const titleSelectedSite = document.getElementById('title-selected-site');

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

  // Scores
  const badgeSiteScore = document.getElementById('badge-site-score');
  const badgeSiteScoreTop = document.getElementById('badge-site-score-top');
  const selMapColorMode = document.getElementById('sel-map-color-mode');
  const legendScoreWrap = document.getElementById('legend-score-wrap');
  const legendPaybackWrap = document.getElementById('legend-payback-wrap');
  const barScoreConn = document.getElementById('bar-score-conn');
  const valScoreConn = document.getElementById('val-score-conn');
  const barScoreAnc = document.getElementById('bar-score-anc');
  const valScoreAnc = document.getElementById('val-score-anc');
  const barScoreArb = document.getElementById('bar-score-arb');
  const valScoreArb = document.getElementById('val-score-arb');
  const barScoreFlex = document.getElementById('bar-score-flex');
  const valScoreFlex = document.getElementById('val-score-flex');
  const barScoreLand = document.getElementById('bar-score-land');
  const valScoreLand = document.getElementById('val-score-land');

  // Comparison
  const btnToggleCompare = document.getElementById('btn-toggle-compare');
  const boxCompareTray = document.getElementById('box-compare-tray');
  const compareChipsContainer = document.getElementById('compare-chips-container');
  const btnShowCompareModal = document.getElementById('btn-show-compare-modal');
  const btnClearCompare = document.getElementById('btn-clear-compare');
  const modalCompare = document.getElementById('modal-compare');
  const btnCloseCompare = document.getElementById('btn-close-compare');
  const btnModalCompareOk = document.getElementById('btn-modal-compare-ok');
  const tableCompare = document.getElementById('table-compare');
  const btnOpenCompare = document.getElementById('btn-open-compare');
  const txtCompareCount = document.getElementById('txt-compare-count');

  // Sensitivity & Transparency
  const txtSensBaseline = document.getElementById('txt-sens-baseline');
  const sensTornadoContainer = document.getElementById('sens-tornado-container');
  const histYearTabs = document.getElementById('hist-year-tabs');
  const histTransparencyContent = document.getElementById('hist-transparency-content');

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
  const dispAllocTableBody = document.getElementById('disp-alloc-table-body');
  const boxAllocSummary = document.getElementById('box-alloc-summary');

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
    let recalcTimer = null;

    // Snabb uppdatering av reglagens etiketter och badge direkt vid drag (0 ms fördröjning)
    const updateLabelsOnly = () => {
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
    };

    const triggerRecalculate = () => {
      if (recalcTimer) {
        clearTimeout(recalcTimer);
        recalcTimer = null;
      }
      recalculateAll();
    };

    const scheduleDebouncedRecalculate = () => {
      updateLabelsOnly();
      if (recalcTimer) clearTimeout(recalcTimer);
      // Debounce på 180 ms under pågående drag så att reglaget flyter silkeslent
      recalcTimer = setTimeout(() => {
        recalculateAll();
        recalcTimer = null;
      }, 180);
    };

    // 'input' uppdaterar siffrorna blixtsnabbt direkt och debouncar omräkningen
    [rPowerMw, rCapMwh, rCapexKwh, rCapexKw, rCableCost, rSubBay].forEach(el => {
      el.addEventListener('input', scheduleDebouncedRecalculate);
      // 'change' triggas direkt när användaren släpper musen / fingret från reglaget
      el.addEventListener('change', triggerRecalculate);
    });

    [selZone, selStrategy, selHistoryDataset, selMarketYear, selExtremeYears, selHybridRenewable].forEach(el => el?.addEventListener('change', () => {
      if (lblDatasetDesc && selHistoryDataset) {
        const val = selHistoryDataset.value;
        if (val === 'last12m') lblDatasetDesc.textContent = 'Faktiska timpriser från senaste 12 månaderna (Nord Pool / ENTSO-E).';
        else if (val === '3y') lblDatasetDesc.textContent = '3 års historiskt snitt (2023–2026), balanserar post-kris.';
        else if (val === '5y') lblDatasetDesc.textContent = '5 års historik (2021–2026), fångar både extrema och lugna marknader.';
        else if (val === '10y') lblDatasetDesc.textContent = 'Fullständig 10-årig Nord Pool-historik (350 000+ timmar sedan 2015).';
        else if (val === '2022') lblDatasetDesc.textContent = 'Energikrisåret 2022: Maximala spreadar (upp till 238 000 €/MW/år i SE4).';
        else if (val === '2024') lblDatasetDesc.textContent = 'Lågvolatilt år 2024: Mycket konservativt stresstest.';
      }
      if (lblHybridSaving && selHybridRenewable) {
        const hVal = selHybridRenewable.value;
        if (hVal === 'solar_20') lblHybridSaving.textContent = 'Sparar 20 MSEK';
        else if (hVal === 'solar_50') lblHybridSaving.textContent = 'Sparar 45 MSEK';
        else if (hVal === 'wind_100') lblHybridSaving.textContent = 'Sparar 70 MSEK';
        else lblHybridSaving.textContent = 'Ingen delad anslutning';
      }
      recalculateAll();
    }));

    btnFindBest.addEventListener('click', () => {
      const bounds = map ? map.getBounds() : null;
      const bestInView = findBestCell(bounds);
      if (bestInView) {
        activateSite(bestInView, -1);
      } else if (currentTopSites.length > 0) {
        activateSite(currentTopSites[0], 0);
      }
    });

    if (btnPrevSite) {
      btnPrevSite.addEventListener('click', () => {
        if (currentTopSites.length > 0 && currentStepperIndex > 0) {
          activateSite(currentTopSites[currentStepperIndex - 1], currentStepperIndex - 1);
        }
      });
    }

    if (btnNextSite) {
      btnNextSite.addEventListener('click', () => {
        if (currentTopSites.length > 0 && currentStepperIndex < currentTopSites.length - 1) {
          activateSite(currentTopSites[currentStepperIndex + 1], currentStepperIndex + 1);
        }
      });
    }

    if (btnZoomAll) {
      btnZoomAll.addEventListener('click', () => {
        zoomAllHotspots();
      });
    }

    // Compare Events
    if (btnToggleCompare) {
      btnToggleCompare.addEventListener('click', () => {
        if (selectedCellId) toggleCompareSite(selectedCellId);
      });
    }
    if (btnOpenCompare) btnOpenCompare.addEventListener('click', renderCompareModal);
    if (btnShowCompareModal) btnShowCompareModal.addEventListener('click', renderCompareModal);
    if (btnCloseCompare) btnCloseCompare.addEventListener('click', () => modalCompare.style.display = 'none');
    if (btnModalCompareOk) btnModalCompareOk.addEventListener('click', () => modalCompare.style.display = 'none');
    if (btnClearCompare) {
      btnClearCompare.addEventListener('click', () => {
        compareCellIds.clear();
        updateCompareTray();
        updateCompareButtonText(selectedCellId);
      });
    }
    if (modalCompare) {
      modalCompare.addEventListener('click', (e) => {
        if (e.target === modalCompare) modalCompare.style.display = 'none';
      });
    }

    // Map color mode event
    if (selMapColorMode) {
      selMapColorMode.addEventListener('change', () => {
        const isScore = (selMapColorMode.value === 'score');
        if (legendScoreWrap) legendScoreWrap.style.display = isScore ? 'inline' : 'none';
        if (legendPaybackWrap) legendPaybackWrap.style.display = isScore ? 'none' : 'inline';
        renderMapLayers();
      });
    }

    // Top zone tabs
    const topZoneTabs = document.getElementById('top-zone-tabs');
    if (topZoneTabs) {
      topZoneTabs.addEventListener('click', (e) => {
        const btn = e.target.closest('button');
        if (btn && btn.dataset.zone) {
          topZoneTabs.querySelectorAll('button').forEach(b => b.classList.toggle('active', b === btn));
          selectedTopZone = btn.dataset.zone;
          updateRankingList();
        }
      });
    }

    // Transparency Tab Events
    if (histYearTabs) {
      histYearTabs.addEventListener('click', (e) => {
        const btn = e.target.closest('button');
        if (btn && btn.dataset.year) {
          renderDynamicTransparency(btn.dataset.year);
        }
      });
    }

    updateLabelsOnly();
    triggerRecalculate();
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

    // Auto-pan kartan när popup öppnas och när fält fälls ut så den aldrig hamnar utanför synfältet
    map.on('popupopen', (e) => {
      const popupEl = e.popup?.getElement();
      if (!popupEl) return;
      
      // Lyssna på när användaren klickar på fällbara fält (<details>)
      const detailsList = popupEl.querySelectorAll('details');
      detailsList.forEach(det => {
        det.addEventListener('toggle', () => {
          setTimeout(() => {
            if (e.popup && e.popup.isOpen()) {
              e.popup.update();
              // Säkerställ att popupen förblir synlig i kartan
              const px = map.project(e.popup.getLatLng());
              px.y -= 30; // Justera något uppåt vid expansion
              map.panTo(map.unproject(px), { animate: true, duration: 0.25 });
            }
          }, 50);
        });
      });
    });
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

  // Hjälpfunktion för att interpolera 8760h backtest-resultat linjärt över varaktighet (1h, 2h, 4h)
  function getZoneInterpolatedDispatch(periodKey, zone, duration) {
    const zEp = dispatchData?.epochs?.[periodKey]?.[zone];
    if (!zEp) return null;
    const d = Math.min(4, Math.max(1, duration));
    const lo = d > 2 ? 2 : 1;
    const hi = d > 2 ? 4 : 2;
    const a = zEp[`${lo}.0h`];
    const b = zEp[`${hi}.0h`];
    if (!a || !b) return null;
    const t = (d - lo) / (hi - lo);
    const mix = (k) => a[k] + (b[k] - a[k]) * t;
    return {
      spot_rev_eur_mw_yr: mix('spot_rev_eur_mw_yr'),
      mfrr_rev_eur_mw_yr: mix('mfrr_rev_eur_mw_yr'),
      afrr_rev_eur_mw_yr: mix('afrr_rev_eur_mw_yr'),
      fcr_rev_eur_mw_yr: mix('fcr_rev_eur_mw_yr'),
      equivalent_cycles: Math.round(mix('equivalent_cycles') * 10) / 10,
      total_rev_eur_mw_yr: mix('total_rev_eur_mw_yr')
    };
  }

  // Beräknar marknadsdjup och prisvolatilitetsavdrag (cannibalization guard).
  // Om batteriets effekt överstiger 15 % av elområdets genomsnittliga mFRR-upphandling
  // appliceras en gradvis elastisk prisrabatt.
  function getMarketDepthDiscount(mw, zone) {
    const volumes = { SE1: 115.8, SE2: 112.8, SE3: 556.1, SE4: 163.9 };
    const mkt = volumes[zone] || 200;
    const share = mw / mkt;
    if (share <= 0.15) return 1.0;
    return Math.max(0.55, 1.0 - (share - 0.15) * 0.35);
  }

  // Finansiell beräkning för en enskild ruta
  // `ov` låter känslighetsanalysen (tornado) köra EXAKT samma beräkning med ändrade
  // parametrar, så att basfall och känslighetsstaplar aldrig kan glida isär.
  function calculateCellFinancials(cell, ov = {}) {
    const mw = ov.mw ?? parseFloat(rPowerMw.value);
    const mwh = ov.mwh ?? parseFloat(rCapMwh.value);
    const capexPerKwh = parseFloat(rCapexKwh.value) * (ov.batteryCapexMult ?? 1);
    const capexPerKw = parseFloat(rCapexKw.value) * (ov.batteryCapexMult ?? 1);
    const cableCostPerKm = parseFloat(rCableCost.value) * 1000000 * (ov.cableMult ?? 1);
    const substationBay = parseFloat(rSubBay.value) * 1000000 * (ov.subBayMult ?? 1);
    const strategy = selStrategy.value;
    const hybridChoice = selHybridRenewable ? selHybridRenewable.value : 'none';
    const extremeYears = ov.extYears ?? parseInt(selExtremeYears.value, 10);
    const revMult = ov.revMult ?? 1;
    const omMult = ov.omMult ?? 1;
    const fx = marketData?.fx_eur_sek || 11.25;

    // Filterkontroll: saknar nätägare eller >90% skyddad natur
    if (!cell.dso || cell.prot >= 0.90) {
      return { passed: false, failReason: cell.prot >= 0.90 ? 'Skyddad naturmark' : 'Saknar känd nätägare' };
    }

    // Välj avstånd till relevant spänningsnivå baserat på MW
    let rawDistKm = cell.d130;
    let stationName = cell.n130 ? `${cell.n130} (130 kV-station)` : '130 kV regionnätsstation';
    if (mw <= 5 && cell.d40 !== null) {
      rawDistKm = cell.d40;
      stationName = cell.n40 ? `${cell.n40} (40 kV lokalnät)` : '40 kV fördelningsstation';
    } else if (mw <= 40 && cell.d70 !== null) {
      rawDistKm = cell.d70;
      stationName = cell.n130 ? `${cell.n130} (70 kV regionnät)` : '70 kV regionnätsstation';
    }
    if (rawDistKm === null) rawDistKm = 30.0; // fallback

    // Realistiskt minimiavstånd: Även om stationen ligger i samma ruta/grannfastighet
    // krävs minst ~250 m kabel (internt ställverk, säkerhetszon & anslutningspunkt).
    const MIN_STATION_CABLE_KM = 0.25;
    const distKm = Math.max(MIN_STATION_CABLE_KM, rawDistKm);

    // 1. CAPEX-komponenter (med 1,3x realistisk kabeldragningsfaktor för terräng & vägsträckning)
    const bessEnergyCapex = mwh * 1000 * capexPerKwh;
    const bessPowerCapex = mw * 1000 * capexPerKw;
    const bessCapex = bessEnergyCapex + bessPowerCapex;
    const routingFactor = 1.30; // Realistisk schakt- och terrängsträckning
    let cableCapex = distKm * routingFactor * cableCostPerKm;
    let stationCapex = substationBay;

    // Samlokalisering (Hybrid med förnybart) sparar delat transformatorfack
    let hybridCapexSaving = 0;
    let hybridAnnualRevSek = 0;
    if (hybridChoice === 'solar_20') {
      hybridCapexSaving = 20000000; // Sparar 20 MSEK
      hybridAnnualRevSek = 600000;   // 0,6 MSEK/år i räddad solel
    } else if (hybridChoice === 'solar_50') {
      hybridCapexSaving = 45000000; // Sparar 45 MSEK (delat 130 kV-fack)
      hybridAnnualRevSek = 1400000;  // 1,4 MSEK/år i räddad solel
    } else if (hybridChoice === 'wind_100') {
      hybridCapexSaving = 70000000; // Sparar 70 MSEK vid storskalig vind/sol
      hybridAnnualRevSek = 2800000;  // 2,8 MSEK/år i räddad energi
    }

    stationCapex = Math.max(0, stationCapex - hybridCapexSaving);

    const totalCapex = bessCapex + cableCapex + stationCapex;

    // 2. Marknadsintäkter per elområde baserat på vald historisk datasetperiod
    const z = cell.zone;
    const period = selHistoryDataset?.value || 'last12m';
    const duration = mwh / mw;
    const is2030 = selMarketYear.value === '2030';
    const depthDiscount = getMarketDepthDiscount(mw, z);

    // Omvandlar en dispatch-epok till SEK/år för hela anläggningen inkl. strategi, 2030, revMult och marknadsdjup
    const revenueFromEpoch = (ep) => {
      let r = {
        arb: ep.spot_rev_eur_mw_yr * mw * fx,
        mfrr: ep.mfrr_rev_eur_mw_yr * mw * fx * depthDiscount,
        afrr: ep.afrr_rev_eur_mw_yr * mw * fx * depthDiscount,
        fcr: ep.fcr_rev_eur_mw_yr * mw * fx
      };
      if (strategy === 'ancillary_focus') {
        r.mfrr *= 1.20; r.afrr *= 1.15; r.arb *= 0.35;
      } else if (strategy === 'arbitrage_focus') {
        r.arb *= 1.35; r.mfrr *= 0.40; r.afrr *= 0.30;
      }
      if (is2030) { r.afrr *= 1.45; r.mfrr *= 0.90; }
      return {
        arb: r.arb * revMult, mfrr: r.mfrr * revMult,
        afrr: r.afrr * revMult, fcr: r.fcr * revMult
      };
    };

    let mfrrRevSek = 0.0;
    let afrrRevSek = 0.0;
    let fcrRevSek = 0.0;
    let arbRevSek = 0.0;

    const baseEpoch = getZoneInterpolatedDispatch(period, z, duration);
    if (baseEpoch) {
      const r = revenueFromEpoch(baseEpoch);
      arbRevSek = r.arb; mfrrRevSek = r.mfrr; afrrRevSek = r.afrr; fcrRevSek = r.fcr;
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
        mfrrAccept = 0.55; afrrAccept = 0.40; arbShare = 0.20;
      } else if (strategy === 'arbitrage_focus') {
        mfrrAccept = 0.20; afrrAccept = 0.10; arbShare = 0.85;
      }

      const fcrBasePrice = getPeriodFcrPrice(period);
      fcrRevSek = fcrBasePrice * 8760 * 0.42 * (mw * 0.5) * fx * revMult;
      mfrrRevSek = (mfrrUp + mfrrDown) * mfrrAccept * mw * fx * revMult * depthDiscount;
      afrrRevSek = afrrDown * afrrAccept * mw * fx * revMult * depthDiscount;
      arbRevSek = arbRate * arbShare * mw * fx * revMult;
      if (is2030) { afrrRevSek *= 1.45; mfrrRevSek *= 0.90; }
    }

    const solarRevSek = hybridAnnualRevSek * revMult;
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

    const omCost = mw * 45000 * omMult;
    const gridCost = mw * dsoTariffRate;
    const wearCost = mwh * 280 * 80;
    const ebitdaOf = (gross) => Math.max(0, gross * (1 - 0.03) - omCost - gridCost - wearCost); // 3 % BSP-arvode

    const normalEbitda = ebitdaOf(grossRev);

    // Extremår: intäkten i ett extremår hämtas från backtestets 2022-epok för SAMMA zon och
    // varaktighet (inte en fast multiplikator). Om användaren redan valt 2022 som dataset är
    // basen redan ett extremår och kvoten blir 1.
    let extGrossRatio = 1.0;
    if (period !== '2022' && baseEpoch) {
      const extEpoch = getZoneInterpolatedDispatch('2022', z, duration);
      if (extEpoch) {
        const er = revenueFromEpoch(extEpoch);
        const extGross = er.arb + er.mfrr + er.afrr + er.fcr + solarRevSek;
        extGrossRatio = grossRev > 0 ? extGross / grossRev : 1.0;
      }
    }
    const extremeEbitda = ebitdaOf(grossRev * extGrossRatio);
    const normalYears = 10 - extremeYears;
    const tenYearTotalCf = (normalEbitda * normalYears) + (extremeEbitda * extremeYears);
    const avgYearlyCf = tenYearTotalCf / 10;

    // Payback (förenklad: investering / genomsnittligt årligt kassaflöde, utan diskontering)
    const simplePayback = normalEbitda > 0 ? (totalCapex / normalEbitda) : 999;
    const scenarioPayback = avgYearlyCf > 0 ? (totalCapex / avgYearlyCf) : 999;

    // Beräkna flerdimensionell platspoäng (0–100) & delbetyg
    const sConn = Math.max(0, Math.min(1.0, 1 - (distKm / 15.0))); // 1.0 vid 0 km, 0 vid 15 km
    const maxZAnc = 220000; // Ref för topp-stödtjänster (SE4 mFRR)
    const sAnc = Math.max(0, Math.min(1.0, (mfrrRevSek + afrrRevSek) / (mw * fx * 0.5 * maxZAnc)));
    const maxZArb = 135000; // Ref för topp-arbitrage
    const sArb = Math.max(0, Math.min(1.0, arbRevSek / (mw * fx * 0.85 * maxZArb)));
    const sFlex = (cell.flex && cell.flex.length > 0) ? 1.0 : 0.2;
    const sLand = Math.max(0, 1 - (cell.prot || 0) - ((cell.water || 0) * 0.5));

    // Sammanvägd poäng (0–100)
    const rawScore = (sConn * 0.35) + (sAnc * 0.25) + (sArb * 0.15) + (sFlex * 0.10) + (sLand * 0.15);
    const score100 = Math.round(rawScore * 100);

    let verdict = 'Svag kandidat';
    let verdictClass = 'red';
    if (score100 >= 75) {
      verdict = 'Värd förfrågan (Topp)';
      verdictClass = 'green';
    } else if (score100 >= 55) {
      verdict = 'Möjlig kandidat';
      verdictClass = 'yellow';
    }

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
      depthDiscount,
      score: score100,
      verdict,
      verdictClass,
      subScores: {
        conn: Math.round(sConn * 100) / 100,
        anc: Math.round(sAnc * 100) / 100,
        arb: Math.round(sArb * 100) / 100,
        flex: Math.round(sFlex * 100) / 100,
        land: Math.round(sLand * 100) / 100
      },
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
      const a4 = parseFloat(rCapMwh.value) / parseFloat(rPowerMw.value) >= 3.0;
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

  function findBestCell(bounds = null) {
    let valid = cellsData.filter(c => c.fin && c.fin.passed);
    if (bounds) {
      const inBounds = valid.filter(c => {
        if (!c.c || !c.c.length) return false;
        return bounds.contains(L.latLng(c.c[0], c.c[1]));
      });
      if (inBounds.length > 0) valid = inBounds;
    }
    if (!valid.length) return null;
    return valid.reduce((best, cur) => {
      const d = cur.fin.scenarioPayback - best.fin.scenarioPayback;
      if (d < -0.05) return cur;
      if (Math.abs(d) <= 0.05 && cur.fin.score > best.fin.score) return cur;
      return best;
    }, valid[0]);
  }

  function getPaybackColor(pb) {
    if (pb <= 7.0) return '#10b981';
    if (pb <= 10.0) return '#fbbf24';
    return '#ef4444';
  }

  function getScoreColor(score) {
    if (score >= 75) return '#10b981';
    if (score >= 55) return '#fbbf24';
    return '#ef4444';
  }

  let cellPolygonMap = new Map();

  function renderMapLayers() {
    cellLayerGroup.clearLayers();
    cellPolygonMap.clear();
    const showFailed = lyrFailed ? lyrFailed.checked : false;
    const colorMode = selMapColorMode ? selMapColorMode.value : 'payback';

    cellsData.forEach(cell => {
      if (!cell.fin) return;
      if (!cell.fin.passed && !showFailed) return;

      const isPassed = cell.fin.passed;
      const pb = isPassed ? cell.fin.scenarioPayback : 999;
      const score = isPassed ? (cell.fin.score || 0) : 0;
      
      let color = '#64748b';
      if (isPassed) {
        color = (colorMode === 'score') ? getScoreColor(score) : getPaybackColor(pb);
      }
      const isSelected = (cell.id === selectedCellId);

      const poly = L.polygon(cell.poly, {
        color: isSelected ? '#38bdf8' : (isPassed ? color : '#ef4444'),
        weight: isSelected ? 3 : 1,
        opacity: isSelected ? 1 : (isPassed ? 0.65 : 0.4),
        fillColor: color,
        fillOpacity: isSelected ? 0.6 : (isPassed ? (score >= 75 || pb <= 7.0 ? 0.45 : 0.25) : 0.12)
      });

      const popupHtml = buildCellPopupContent(cell);
      poly.bindPopup(popupHtml, {
        maxWidth: 320,
        className: 'bess-cell-popup',
        autoPan: true,
        autoPanPaddingTopLeft: L.point(30, 30),
        autoPanPaddingBottomRight: L.point(30, 30)
      });

      if (isPassed) {
        poly.bindTooltip(`
          <strong>Ruta ${cell.id} (${cell.zone})</strong><br>
          Poäng: <strong style="color:${getScoreColor(cell.fin.score)};">${cell.fin.score}/100</strong> (${cell.fin.verdict})<br>
          ROI: <strong style="color:${getPaybackColor(cell.fin.scenarioPayback)};">${cell.fin.scenarioPayback.toFixed(1).replace('.', ',')} år</strong> · ${cell.dso || 'Okänd'}
        `, { sticky: true, opacity: 0.95 });
      } else {
        poly.bindTooltip(`<strong>Ruta ${cell.id} (${cell.zone})</strong><br><span style="color:#f87171;">Exkluderad</span>: ${cell.fin.failReason || 'Ej godkänd'}`, { sticky: true, opacity: 0.95 });
      }

      poly.on('click', () => {
        selectedCellId = cell.id;
        renderMapLayers();
        updateSelectedDetail(cell);
        // Öppna popupen för den klickade rutan
        const activePoly = cellPolygonMap.get(cell.id);
        if (activePoly) activePoly.openPopup();
      });

      cellLayerGroup.addLayer(poly);
      cellPolygonMap.set(cell.id, poly);
    });
  }

  let currentTopSites = [];
  let currentStepperIndex = 0;

  let selectedTopZone = 'ALL';
  const txtTopListTitle = document.getElementById('txt-top-list-title');

  function updateRankingList() {
    listTopSites.innerHTML = '';
    
    // Filtrera på vald zon om angiven
    let candidates = cellsData.filter(c => c.fin && c.fin.passed);
    if (selectedTopZone !== 'ALL') {
      candidates = candidates.filter(c => c.zone === selectedTopZone);
    }
    // Många rutor delar exakt samma återbetalningstid (samma zon, kabelavstånd på golvet 0,25 km).
    // Vid lika (±0,05 år) avgör platspoängen, därefter minst skyddad mark – annars blir
    // "bästa ruta" bara den som råkar stå först i datafilen.
    const valid = candidates.sort((a, b) => {
      const d = a.fin.scenarioPayback - b.fin.scenarioPayback;
      if (Math.abs(d) > 0.05) return d;
      if (b.fin.score !== a.fin.score) return b.fin.score - a.fin.score;
      return (a.prot || 0) - (b.prot || 0);
    });
    
    if (txtTopListTitle) {
      txtTopListTitle.textContent = selectedTopZone === 'ALL' 
        ? '🏆 Topp 5 Platser i Sverige' 
        : `🏆 Topp 5 Platser i ${selectedTopZone}`;
    }

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

    currentTopSites = top5;
    updateStepperDisplay();

    top5.forEach((c, idx) => {
      const isSelected = (c.id === selectedCellId);
      const li = document.createElement('li');
      li.className = 'bs-top-item';
      if (isSelected) {
        li.style.borderColor = '#38bdf8';
        li.style.background = 'rgba(56, 189, 248, 0.12)';
      }
      li.innerHTML = `
        <div class="bs-top-rank">${idx + 1}</div>
        <div class="bs-top-main">
          <div class="bs-top-head">
            <span class="bs-top-name" title="${c.dso}">${c.dso}</span>
            <span class="bs-badge ${c.fin.verdictClass}" style="font-size:0.7em; padding:1px 5px;">${c.fin.score}p</span>
          </div>
          <div class="bs-top-sub">
            <span style="color:#38bdf8; font-weight:600;">${c.zone}</span> · ${c.fin.distKm.toFixed(1)} km till station · Ruta ${c.id}
          </div>
        </div>
        <div class="bs-top-right">
          <div class="bs-top-roi" style="color:${getPaybackColor(c.fin.scenarioPayback)};">
            ${c.fin.scenarioPayback.toFixed(1)} år
          </div>
          <div class="bs-top-ebitda">
            ${Math.round(c.fin.normalEbitda / 1000000)} M/år
          </div>
        </div>
      `;
      li.addEventListener('click', () => {
        activateSite(c, idx);
      });
      listTopSites.appendChild(li);
    });

    if (top5.length > 0 && txtBestLoc && txtBestReason) {
      const best = top5[0];
      const distStr = best.fin.distKm <= 0.3 ? 'ca 0,3 km (direktanslutning)' : `${best.fin.distKm.toFixed(1)} km`;
      txtBestLoc.textContent = `Ruta ${best.id} (${best.zone} · ${best.dso}) · ${best.fin.score}/100`;
      txtBestReason.textContent = `Återbetalning på ${best.fin.scenarioPayback.toFixed(1)} år. ${distStr} kabelanslutning minimerar kabel-CAPEX i en zon med ${Math.round(best.fin.normalEbitda / 1000000)} MSEK i årlig EBITDA.`;
    }
  }

  function activateSite(cell, index = -1) {
    if (!cell) return;
    selectedCellId = cell.id;
    if (index >= 0) {
      currentStepperIndex = index;
    } else {
      const foundIdx = currentTopSites.findIndex(c => c.id === cell.id);
      currentStepperIndex = foundIdx >= 0 ? foundIdx : 0;
    }
    updateStepperDisplay();
    renderMapLayers();
    updateSelectedDetail(cell);
    map.flyTo(cell.c, 9, { duration: 1.0 });
    setTimeout(() => {
      const poly = cellPolygonMap.get(cell.id);
      if (poly) poly.openPopup();
    }, 450);
  }

  function updateStepperDisplay() {
    if (!txtStepperInfo) return;
    const total = currentTopSites.length;
    if (total === 0) {
      txtStepperInfo.textContent = 'Inga träffar';
      if (btnPrevSite) btnPrevSite.disabled = true;
      if (btnNextSite) btnNextSite.disabled = true;
      return;
    }

    if (currentStepperIndex < 0) currentStepperIndex = 0;
    if (currentStepperIndex >= total) currentStepperIndex = total - 1;

    const currentCell = currentTopSites[currentStepperIndex];
    txtStepperInfo.textContent = `#${currentStepperIndex + 1} av ${total} · ${currentCell.dso || currentCell.zone}`;
    if (btnPrevSite) btnPrevSite.disabled = (currentStepperIndex <= 0);
    if (btnNextSite) btnNextSite.disabled = (currentStepperIndex >= total - 1);
  }

  function zoomAllHotspots() {
    if (!currentTopSites.length) {
      map.setView([62.0, 15.5], 5);
      return;
    }
    // Samla koordinater för alla topplatser
    const latlngs = currentTopSites.map(c => c.c);
    const bounds = L.latLngBounds(latlngs);
    map.fitBounds(bounds, { padding: [60, 60], maxZoom: 7, animate: true });
    // Markera bästa
    if (currentTopSites[0]) {
      selectedCellId = currentTopSites[0].id;
      renderMapLayers();
      updateSelectedDetail(currentTopSites[0]);
    }
  }

  function buildCellPopupContent(cell) {
    const f = cell.fin;
    if (!f || !f.passed) {
      return `
        <div style="font-family:var(--sans); font-size:12px; line-height:1.4; min-width:210px;">
          <strong style="color:#f87171; font-size:13px;">Ruta ${cell.id} (${cell.zone})</strong><br>
          <span style="color:#94a3b8;">${cell.dso || 'Okänd nätägare'}</span><br>
          <div style="margin-top:6px; padding:4px 8px; background:rgba(239,68,68,0.15); border:1px solid #ef4444; border-radius:4px; color:#fca5a5;">
            <strong>Exkluderad:</strong> ${f?.failReason || 'Ej tillgänglig'}
          </div>
        </div>
      `;
    }

    const pbColor = getPaybackColor(f.scenarioPayback);
    const msekEbitda = (f.normalEbitda / 1000000).toFixed(1).replace('.', ',');
    const msekCapex = Math.round(f.totalCapex / 1000000);
    const msekCable = (f.cableCapex / 1000000).toFixed(1).replace('.', ',');

    const z = cell.zone;
    const period = selHistoryDataset?.value || 'last12m';
    const mw = parseFloat(rPowerMw.value);
    const mwh = parseFloat(rCapMwh.value);
    const duration = mw > 0 ? (mwh / mw) : 2.0;
    const is4h = duration >= 3.5;

    // Spot-historik per år
    const zSpot = marketData?.spot?.[z];
    const spotLast12mSpread = zSpot?.last12m?.daily_spread_mean ? Math.round(zSpot.last12m.daily_spread_mean) : '–';
    const spotLast12mMean = zSpot?.last12m?.mean ? Math.round(zSpot.last12m.mean) : '–';
    const spotLast12mArb = zSpot?.last12m ? Math.round(is4h ? zSpot.last12m.arb_4h_eur_mw_yr : zSpot.last12m.arb_2h_eur_mw_yr).toLocaleString('sv-SE') : '–';

    const years = zSpot?.years || {};
    const yr2022Arb = years['2022'] ? Math.round(is4h ? years['2022'].arb_4h_eur_mw_yr : years['2022'].arb_2h_eur_mw_yr).toLocaleString('sv-SE') : '–';
    const yr2023Arb = years['2023'] ? Math.round(is4h ? years['2023'].arb_4h_eur_mw_yr : years['2023'].arb_2h_eur_mw_yr).toLocaleString('sv-SE') : '–';
    const yr2024Arb = years['2024'] ? Math.round(is4h ? years['2024'].arb_4h_eur_mw_yr : years['2024'].arb_2h_eur_mw_yr).toLocaleString('sv-SE') : '–';
    const yr2025Arb = years['2025'] ? Math.round(is4h ? years['2025'].arb_4h_eur_mw_yr : years['2025'].arb_2h_eur_mw_yr).toLocaleString('sv-SE') : '–';

    // Intäktsandelar
    const mfrrMsek = (f.breakdown.mfrrRevSek / 1000000).toFixed(1).replace('.', ',');
    const afrrMsek = (f.breakdown.afrrRevSek / 1000000).toFixed(1).replace('.', ',');
    const fcrMsek = (f.breakdown.fcrRevSek / 1000000).toFixed(1).replace('.', ',');
    const arbMsek = (f.breakdown.arbRevSek / 1000000).toFixed(1).replace('.', ',');

    // SvK Stödtjänstpriser i zonen
    const mfrrCm = marketData?.capacity?.mfrr_cm?.[z];
    const mfrrUpEur = mfrrCm?.up?.price_mean ? mfrrCm.up.price_mean.toFixed(1) : '–';
    const mfrrDownEur = mfrrCm?.down?.price_mean ? mfrrCm.down.price_mean.toFixed(1) : '–';

    return `
      <div style="font-family:var(--sans); font-size:12px; line-height:1.45; min-width:280px; max-width:320px; color:#f1f5f9;">
        <!-- Header -->
        <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:6px; border-bottom:1px solid rgba(255,255,255,0.12); padding-bottom:5px;">
          <div>
            <strong style="font-size:13px; color:#fff;">Ruta ${cell.id}</strong>
            <span style="font-size:11px; color:#94a3b8; margin-left:4px;">(${cell.zone})</span>
          </div>
          <span style="font-weight:700; color:${pbColor}; font-size:13px;">${f.scenarioPayback.toFixed(1)} år ROI</span>
        </div>

        <!-- Basdata -->
        <div style="color:#cbd5e1; font-size:11.5px;"><strong>Nätägare:</strong> <span style="color:#fff;">${cell.dso || 'Okänd'}</span></div>
        <div style="color:#cbd5e1; font-size:11.5px;"><strong>Station:</strong> <span style="color:#fff;">${f.stationName || '–'}</span> (${f.distKm.toFixed(1)} km)</div>

        <!-- Huvud-KPI:er & Score -->
        <div style="margin-top:6px; padding:6px 8px; background:rgba(255,255,255,0.04); border-radius:6px; border:1px solid rgba(255,255,255,0.08);">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:3px;">
            <span style="font-size:11px; color:#94a3b8;">Platspoäng:</span>
            <span class="bs-badge ${f.verdictClass}" style="padding:1px 6px; font-size:10.5px;">${f.score}/100 · ${f.verdict}</span>
          </div>
          <div style="display:flex; justify-content:space-between;"><span>Årlig EBITDA:</span><strong style="color:#38bdf8;">${msekEbitda} MSEK</strong></div>
          <div style="display:flex; justify-content:space-between; margin-top:2px;"><span>Total CAPEX:</span><strong style="color:#fff;">${msekCapex} MSEK</strong></div>
          <div style="display:flex; justify-content:space-between; font-size:11px; color:#94a3b8; margin-top:1px;"><span>Varav kabelanslutning:</span><span>${msekCable} MSEK</span></div>
        </div>

        <div style="margin-top:5px; display:flex; gap:4px;">
          <button type="button" onclick="window.bessToggleCompare('${cell.id}')" style="flex:1; padding:4px 6px; background:rgba(56,189,248,0.12); color:#38bdf8; border:1px solid rgba(56,189,248,0.3); border-radius:5px; font-weight:600; font-size:11px; cursor:pointer;">
            ⚖️ Jämför ruta
          </button>
        </div>

        <!-- Expanderbar: Intäktsfördelning -->
        <details style="margin-top:6px; background:rgba(15,23,42,0.6); border:1px solid rgba(255,255,255,0.08); border-radius:6px; padding:4px 8px;">
          <summary style="cursor:pointer; font-weight:600; font-size:11px; color:#38bdf8; outline:none; user-select:none; display:flex; justify-content:space-between; align-items:center;">
            <span>📊 Intäkter per marknad (${mw} MW)</span>
            <span style="font-size:10px; color:var(--muted);">▼</span>
          </summary>
          <div style="margin-top:5px; font-size:11px; border-top:1px dashed rgba(255,255,255,0.1); padding-top:4px;">
            <div style="display:flex; justify-content:space-between; color:#cbd5e1;"><span>mFRR Kapacitet:</span><strong style="color:#fff;">${mfrrMsek} MSEK</strong></div>
            <div style="display:flex; justify-content:space-between; color:#cbd5e1;"><span>aFRR Kapacitet:</span><strong style="color:#fff;">${afrrMsek} MSEK</strong></div>
            <div style="display:flex; justify-content:space-between; color:#cbd5e1;"><span>FCR Reserver:</span><strong style="color:#fff;">${fcrMsek} MSEK</strong></div>
            <div style="display:flex; justify-content:space-between; color:#cbd5e1;"><span>Spotarbitrage:</span><strong style="color:#fff;">${arbMsek} MSEK</strong></div>
            <div style="font-size:10px; color:#94a3b8; margin-top:3px;">Snitt SvK mFRR: Upp ${mfrrUpEur} €/MWh · Ned ${mfrrDownEur} €/MWh</div>
          </div>
        </details>

        <!-- Expanderbar: Historiska Spreads & Priser (2015-2026) -->
        <details style="margin-top:5px; background:rgba(15,23,42,0.6); border:1px solid rgba(255,255,255,0.08); border-radius:6px; padding:4px 8px;">
          <summary style="cursor:pointer; font-weight:600; font-size:11px; color:#38bdf8; outline:none; user-select:none; display:flex; justify-content:space-between; align-items:center;">
            <span>📈 Historisk Spot &amp; Spread (${z})</span>
            <span style="font-size:10px; color:var(--muted);">▼</span>
          </summary>
          <div style="margin-top:5px; font-size:11px; border-top:1px dashed rgba(255,255,255,0.1); padding-top:4px;">
            <div style="display:flex; justify-content:space-between;"><span>Senaste 12m dygnsspread:</span><strong style="color:#fbbf24;">${spotLast12mSpread} €/MWh</strong></div>
            <div style="display:flex; justify-content:space-between; margin-top:1px;"><span>Senaste 12m snittpris:</span><span>${spotLast12mMean} €/MWh</span></div>
            <div style="margin-top:4px; font-weight:600; color:#94a3b8; font-size:10.5px;">Teoretiskt arbitrage (${duration}h):</div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:2px 8px; font-size:10.5px; margin-top:2px;">
              <div>• 12m: <strong>${spotLast12mArb} €</strong></div>
              <div>• 2025: <strong>${yr2025Arb} €</strong></div>
              <div>• 2024: <strong>${yr2024Arb} €</strong></div>
              <div>• 2023: <strong>${yr2023Arb} €</strong></div>
              <div>• 2022 (kris): <strong style="color:#f87171;">${yr2022Arb} €</strong></div>
            </div>
          </div>
        </details>

        <!-- Omvärld & Flex (visas endast vid faktisk konflikt eller aktiv flex) -->
        ${(cell.prot >= 0.02 || (cell.flex && cell.flex.length)) ? `
          <div style="margin-top:6px; font-size:11px; color:#94a3b8;">
            ${cell.prot >= 0.02 ? `<span style="color:#fbbf24;">⚠️ ${Math.round(cell.prot * 100)} % natur- &amp; markskydd</span>` : ''}
            ${(cell.prot >= 0.02 && cell.flex?.length) ? '<br>' : ''}
            ${cell.flex?.length ? `<span style="color:#34d399; font-weight:600;">⚡ Flexmarknad: ${cell.flex.join(', ')}</span>` : ''}
          </div>
        ` : ''}

        <!-- Körschema-knapp -->
        <button type="button" onclick="window.bessOpenDispatchModal()" style="width:100%; margin-top:8px; padding:5px 8px; background:linear-gradient(135deg, #0284c7, #38bdf8); color:#0b1220; border:none; border-radius:5px; font-weight:700; font-size:11px; cursor:pointer;">
          ⏱️ Timvis Batteristyrning &amp; Körschema
        </button>
      </div>
    `;
  }

  function updateSelectedDetail(cell) {
    const f = cell.fin;
    if (!f || !f.passed) return;

    if (titleSelectedSite) {
      titleSelectedSite.textContent = `📊 Vald Plats: Ruta ${cell.id} (${cell.zone} · ${cell.dso})`;
    }

    kpiPayback.textContent = f.scenarioPayback.toFixed(1).replace('.', ',') + ' år';
    kpiPayback.style.color = getPaybackColor(f.scenarioPayback);
    kpiPaybackSub.textContent = `Normalår: ${f.simplePayback.toFixed(1).replace('.', ',')} år`;

    kpiCapex.textContent = Math.round(f.totalCapex / 1000000) + ' MSEK';
    kpiCapexSub.textContent = `Varav kabel: ${(f.cableCapex / 1000000).toFixed(1)} MSEK`;

    kpiEbitda.textContent = (f.normalEbitda / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
    kpi10yrCf.textContent = Math.round(f.tenYearTotalCf / 1000000) + ' MSEK';

    badgeRoi.textContent = `ROI: ${f.scenarioPayback.toFixed(1).replace('.', ',')} år`;
    badgeRoi.className = `bs-badge ${f.scenarioPayback <= 7.0 ? 'green' : (f.scenarioPayback <= 10.0 ? 'yellow' : 'red')}`;

    detZone.textContent = cell.zone;
    detDso.textContent = cell.dso;
    detStation.textContent = f.stationName || '–';
    detDist.textContent = f.distKm.toFixed(1) + ' km';
    detEnergyCapex.textContent = (f.bessEnergyCapex / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
    detPowerCapex.textContent = (f.bessPowerCapex / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
    detStationCapex.textContent = (f.stationCapex / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
    detCableCost.textContent = (f.cableCapex / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
    if (detProt) {
      const protPct = Math.round(cell.prot * 100);
      if (cell.prot_name) {
        detProt.innerHTML = `<span>${protPct} %</span><div style="font-size:0.75em; color:var(--muted); font-weight:normal; line-height:1.2; margin-top:2px;" title="${cell.prot_name}">${cell.prot_name}</div>`;
      } else {
        detProt.textContent = `${protPct} %`;
      }
    }
    detFlex.textContent = cell.flex?.length ? cell.flex.join(', ') : 'Ej aktiv flexmarknad';

    revMfrr.textContent = (f.breakdown.mfrrRevSek / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
    revAfrr.textContent = (f.breakdown.afrrRevSek / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
    revFcr.textContent = (f.breakdown.fcrRevSek / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
    revArb.textContent = (f.breakdown.arbRevSek / 1000000).toFixed(1).replace('.', ',') + ' MSEK';
    revSolar.textContent = (f.breakdown.solarRevSek / 1000000).toFixed(1).replace('.', ',') + ' MSEK';

    // Platspoäng & Delbetyg i vänsterkortet
    if (badgeSiteScore) {
      badgeSiteScore.textContent = `${f.score} / 100 · ${f.verdict}`;
      badgeSiteScore.className = `bs-badge ${f.verdictClass}`;
    }
    if (badgeSiteScoreTop) {
      badgeSiteScoreTop.textContent = `Poäng: ${f.score}/100`;
      badgeSiteScoreTop.className = `bs-badge ${f.verdictClass}`;
    }
    if (barScoreConn && f.subScores) {
      barScoreConn.style.width = `${f.subScores.conn * 100}%`;
      valScoreConn.textContent = f.subScores.conn.toFixed(2);
      barScoreAnc.style.width = `${f.subScores.anc * 100}%`;
      valScoreAnc.textContent = f.subScores.anc.toFixed(2);
      barScoreArb.style.width = `${f.subScores.arb * 100}%`;
      valScoreArb.textContent = f.subScores.arb.toFixed(2);
      barScoreFlex.style.width = `${f.subScores.flex * 100}%`;
      valScoreFlex.textContent = f.subScores.flex.toFixed(2);
      barScoreLand.style.width = `${f.subScores.land * 100}%`;
      valScoreLand.textContent = f.subScores.land.toFixed(2);
    }

    // Uppdatera jämförelseknappens text
    updateCompareButtonText(cell.id);

    // Kompakt sammanställning av marknadsallokering (under Intäkt per år)
    if (boxAllocSummary) {
      const mw = parseFloat(rPowerMw.value);
      const mwh = parseFloat(rCapMwh.value);
      const is4h = (mwh / mw) >= 3.0;
      const b = f.breakdown;
      const mfrrM = (b.mfrrRevSek / 1000000).toFixed(1).replace('.', ',');
      const afrrM = (b.afrrRevSek / 1000000).toFixed(1).replace('.', ',');
      const fcrM = (b.fcrRevSek / 1000000).toFixed(1).replace('.', ',');
      const arbM = (b.arbRevSek / 1000000).toFixed(1).replace('.', ',');
      const solarM = (b.solarRevSek / 1000000).toFixed(1).replace('.', ',');

      boxAllocSummary.innerHTML = `
        <div style="display:flex; justify-content:space-between; margin-bottom:2px;">
          <span>• mFRR Kapacitet:</span>
          <strong style="color:#38bdf8; font-family:var(--mono);">${mw} MW (${mfrrM} MSEK)</strong>
        </div>
        ${b.afrrRevSek > 0 ? `
        <div style="display:flex; justify-content:space-between; margin-bottom:2px;">
          <span>• aFRR Kapacitet:</span>
          <strong style="color:#a855f7; font-family:var(--mono);">${(mw * 0.4).toFixed(0)} MW (${afrrM} MSEK)</strong>
        </div>
        ` : ''}
        ${b.fcrRevSek > 0 ? `
        <div style="display:flex; justify-content:space-between; margin-bottom:2px;">
          <span>• FCR Reserver:</span>
          <strong style="color:#e2e8f0; font-family:var(--mono);">${(mw * 0.5).toFixed(0)} MW (${fcrM} MSEK)</strong>
        </div>
        ` : ''}
        <div style="display:flex; justify-content:space-between; margin-bottom:2px;">
          <span>• Dygnsarbitrage (${is4h ? '4h' : '2h'}):</span>
          <strong style="color:#fbbf24; font-family:var(--mono);">${mwh} MWh (${arbM} MSEK)</strong>
        </div>
        ${b.solarRevSek > 0 ? `
        <div style="display:flex; justify-content:space-between; margin-bottom:2px;">
          <span>• Hybrid Solel/Curtailment:</span>
          <strong style="color:#34d399; font-family:var(--mono);">${solarM} MSEK</strong>
        </div>
        ` : ''}
        ${f.depthDiscount < 0.99 ? `
        <div style="margin-top:4px; padding:3px 6px; background:rgba(251, 191, 36, 0.1); border:1px solid rgba(251, 191, 36, 0.25); border-radius:4px; font-size:0.88em; color:#fbbf24;">
          ⚠️ <strong>Marknadsdjup (${cell.zone}):</strong> Anläggningen (${mw} MW) utgör en betydande andel av elområdets mFRR-upphandling. Intäkterna har justerats ned med ${(Math.round((1 - f.depthDiscount) * 100))} % för priselasticitet/kannibalisering.
        </div>
        ` : ''}
        <div style="margin-top:4px; font-size:0.92em; color:var(--muted); border-top:1px dashed rgba(255,255,255,0.06); padding-top:3px;">
          💡 För fullständig tim- och prisallokering, klicka på <em>⏱️ Timvis körschema</em> nedan.
        </div>
      `;
    }

    // Uppdatera Känslighets- / Riskanalys
    renderSensitivityAnalysis(cell);
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

    const interpDisp = getZoneInterpolatedDispatch(period, z, duration);
    const epochDisp = interpDisp || dispatchData?.epochs?.[period]?.[z]?.[dKey];

    const dispActiveYearTitle = document.getElementById('disp-active-year-title');
    if (dispActiveYearTitle) {
      const pFullNames = {
        'last12m': `Senaste 12 månaderna rullande (Faktisk marknad i ${z})`,
        '3y': `3-års historiskt genomsnitt (2023–2026 i ${z})`,
        '5y': `5-års historiskt genomsnitt (2021–2026 inkl. krisår i ${z})`,
        '10y': `10-års fullständigt historiskt genomsnitt (2015–2026 i ${z})`,
        '2022': `Extremåret 2022 – Energikrisen (Maximal volatilitet i ${z})`,
        '2024': `Konservativt basår 2024 (Lägre volatilitet i ${z})`
      };
      dispActiveYearTitle.textContent = pFullNames[period] || period;
    }

    if (dispKpiEpoch) {
      const periodNames = {
        'last12m': 'Senaste 12 månaderna (Aktuell marknad)',
        '3y': '3 års snitt (2023–2026)',
        '5y': '5 års snitt (2021–2026)',
        '10y': '10 års snitt (Nord Pool 2015–2026)',
        '2022': 'Extremåret 2022 (Energikrisen)',
        '2024': 'Konservativt år 2024'
      };
      dispKpiEpoch.textContent = `${periodNames[period] || period} · ${z} (${duration.toFixed(1)}h interpolerad)`;
    }

    if (epochDisp) {
      if (dispKpiCycles) dispKpiCycles.textContent = `${epochDisp.equivalent_cycles} cykler/år`;
      if (dispKpiRevenue) dispKpiRevenue.textContent = `${Math.round(epochDisp.total_rev_eur_mw_yr).toLocaleString('sv-SE')} €/MW/år`;

      const dispAllocTableTitle = document.getElementById('disp-alloc-table-title');
      if (dispAllocTableTitle) {
        if (period === 'last12m') {
          dispAllocTableTitle.innerHTML = `📊 Årsallokering (8 760 timmar): Senaste 12 månadernas marknad i ${z}`;
        } else if (period === '10y') {
          dispAllocTableTitle.innerHTML = `📊 Genomsnittligt Normalår (8 760 h/år) baserat på 10-års historik (2015–2026 i ${z})`;
        } else if (period === '5y') {
          dispAllocTableTitle.innerHTML = `📊 Genomsnittligt Normalår (8 760 h/år) baserat på 5-års historik (2021–2026 i ${z})`;
        } else if (period === '3y') {
          dispAllocTableTitle.innerHTML = `📊 Genomsnittligt Normalår (8 760 h/år) baserat på 3-års historik (2023–2026 i ${z})`;
        } else if (period === '2022') {
          dispAllocTableTitle.innerHTML = `📊 Årsallokering (8 760 timmar): Extremåret 2022 (Energikrisen i ${z})`;
        } else {
          dispAllocTableTitle.innerHTML = `📊 Årsallokering (8 760 timmar): Konservativt basår 2024 i ${z}`;
        }
      }

      // Rendera Årsallokeringstabellen (Effekt & Timmar)
      if (dispAllocTableBody && cell.fin) {
        const f = cell.fin;
        const b = f.breakdown;
        const mfrrRevM = (b.mfrrRevSek / 1000000).toFixed(1).replace('.', ',');
        const afrrRevM = (b.afrrRevSek / 1000000).toFixed(1).replace('.', ',');
        const fcrRevM = (b.fcrRevSek / 1000000).toFixed(1).replace('.', ',');
        const arbRevM = (b.arbRevSek / 1000000).toFixed(1).replace('.', ',');
        const solarRevM = (b.solarRevSek / 1000000).toFixed(1).replace('.', ',');
        const grossTotM = ((b.mfrrRevSek + b.afrrRevSek + b.fcrRevSek + b.arbRevSek + b.solarRevSek) / 1000000).toFixed(1).replace('.', ',');
        const ebitdaM = (f.normalEbitda / 1000000).toFixed(1).replace('.', ',');

        dispAllocTableBody.innerHTML = `
          <tr style="border-bottom:1px solid rgba(255,255,255,0.05);">
            <td style="padding:6px 10px; text-align:left; font-family:var(--sans);"><strong style="color:#38bdf8;">mFRR Kapacitetsmarknad</strong></td>
            <td style="padding:6px 10px; text-align:right;">${mw} MW</td>
            <td style="padding:6px 10px; text-align:right;">Upp/Ned</td>
            <td style="padding:6px 10px; text-align:right;">Marginell</td>
            <td style="padding:6px 10px; text-align:right; color:#38bdf8;">Prioriterad</td>
            <td style="padding:6px 10px; text-align:right;">${Math.round(epochDisp.mfrr_rev_eur_mw_yr).toLocaleString('sv-SE')} €/MW</td>
            <td style="padding:6px 10px; text-align:right; font-weight:700; color:#38bdf8;">${mfrrRevM} MSEK</td>
          </tr>
          ${epochDisp.afrr_rev_eur_mw_yr > 0 ? `
          <tr style="border-bottom:1px solid rgba(255,255,255,0.05);">
            <td style="padding:6px 10px; text-align:left; font-family:var(--sans);"><strong style="color:#a855f7;">aFRR Kapacitet</strong></td>
            <td style="padding:6px 10px; text-align:right;">${(mw * 0.4).toFixed(0)} MW</td>
            <td style="padding:6px 10px; text-align:right;">Nedreglering</td>
            <td style="padding:6px 10px; text-align:right;">Marginell</td>
            <td style="padding:6px 10px; text-align:right; color:#a855f7;">Delårsdel</td>
            <td style="padding:6px 10px; text-align:right;">${Math.round(epochDisp.afrr_rev_eur_mw_yr).toLocaleString('sv-SE')} €/MW</td>
            <td style="padding:6px 10px; text-align:right; font-weight:700; color:#a855f7;">${afrrRevM} MSEK</td>
          </tr>
          ` : ''}
          ${epochDisp.fcr_rev_eur_mw_yr > 0 ? `
          <tr style="border-bottom:1px solid rgba(255,255,255,0.05);">
            <td style="padding:6px 10px; text-align:left; font-family:var(--sans);"><strong style="color:#e2e8f0;">FCR Reserver (FCR-D/N)</strong></td>
            <td style="padding:6px 10px; text-align:right;">${(mw * 0.5).toFixed(0)} MW</td>
            <td style="padding:6px 10px; text-align:right;">Samordnad</td>
            <td style="padding:6px 10px; text-align:right;">Pay-as-bid</td>
            <td style="padding:6px 10px; text-align:right; color:#e2e8f0;">Frekvensbunden</td>
            <td style="padding:6px 10px; text-align:right;">${Math.round(epochDisp.fcr_rev_eur_mw_yr).toLocaleString('sv-SE')} €/MW</td>
            <td style="padding:6px 10px; text-align:right; font-weight:700;">${fcrRevM} MSEK</td>
          </tr>
          ` : ''}
          <tr style="border-bottom:1px solid rgba(255,255,255,0.05); background:rgba(255,255,255,0.02);">
            <td style="padding:6px 10px; text-align:left; font-family:var(--sans);"><strong style="color:#fbbf24;">Dygnsarbitrage (Spot ${z})</strong></td>
            <td style="padding:6px 10px; text-align:right;">${mw} MW (${mwh} MWh)</td>
            <td style="padding:6px 10px; text-align:right;">100 %</td>
            <td style="padding:6px 10px; text-align:right;">Day-Ahead</td>
            <td style="padding:6px 10px; text-align:right; color:#fbbf24;">${epochDisp.equivalent_cycles} cykler</td>
            <td style="padding:6px 10px; text-align:right;">${Math.round(epochDisp.spot_rev_eur_mw_yr).toLocaleString('sv-SE')} €/MW</td>
            <td style="padding:6px 10px; text-align:right; font-weight:700; color:#fbbf24;">${arbRevM} MSEK</td>
          </tr>
          ${b.solarRevSek > 0 ? `
          <tr style="border-bottom:1px solid rgba(255,255,255,0.05);">
            <td style="padding:6px 10px; text-align:left; font-family:var(--sans);"><strong style="color:#34d399;">Hybrid Samlokalisering</strong></td>
            <td style="padding:6px 10px; text-align:right;">Curtailment</td>
            <td style="padding:6px 10px; text-align:right;">Delad park</td>
            <td style="padding:6px 10px; text-align:right;">Räddad el</td>
            <td style="padding:6px 10px; text-align:right; color:#34d399;">Säsongsbunden</td>
            <td style="padding:6px 10px; text-align:right;">—</td>
            <td style="padding:6px 10px; text-align:right; font-weight:700; color:#34d399;">${solarRevM} MSEK</td>
          </tr>
          ` : ''}
          <tr style="font-weight:700; background:rgba(56,189,248,0.06); border-top:1px solid rgba(56,189,248,0.3);">
            <td style="padding:7px 10px; text-align:left; font-family:var(--sans); color:#fff;">Total Bruttointäkt</td>
            <td style="padding:7px 10px; text-align:right;">${mw} MW</td>
            <td colspan="4" style="padding:7px 10px; text-align:right; color:var(--muted); font-size:0.9em; font-family:var(--sans);">Innan driftskostnader &amp; avgifter</td>
            <td style="padding:7px 10px; text-align:right; color:#38bdf8; font-size:1.05em;">${grossTotM} MSEK</td>
          </tr>
          <tr style="font-weight:700; background:rgba(16,185,129,0.08); border-top:1px solid rgba(16,185,129,0.2);">
            <td style="padding:7px 10px; text-align:left; font-family:var(--sans); color:#34d399;">Årlig EBITDA (Netto)</td>
            <td style="padding:7px 10px; text-align:right;">${mw} MW</td>
            <td colspan="4" style="padding:7px 10px; text-align:right; color:var(--muted); font-size:0.85em; font-family:var(--sans);">Efter BSP (3 %), O&amp;M, effekttariff &amp; cykelslitage</td>
            <td style="padding:7px 10px; text-align:right; color:#34d399; font-size:1.1em;">${ebitdaM} MSEK</td>
          </tr>
        `;
      }

      if (dispTableBody) {
        dispTableBody.innerHTML = '';
        const discreteEpoch = dispatchData?.epochs?.[period]?.[z]?.[dKey];
        const week = epochDisp.sample_week || discreteEpoch?.sample_week || [];
        week.forEach((row, i) => {
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

  // --- JÄMFÖRELSEFUNKTIONER (COMPARE SITES) ---
  let compareCellIds = new Set();

  function toggleCompareSite(cellId) {
    if (compareCellIds.has(cellId)) {
      compareCellIds.delete(cellId);
    } else {
      if (compareCellIds.size >= 5) {
        alert('Du kan maximalt jämföra 5 platser samtidigt.');
        return;
      }
      compareCellIds.add(cellId);
    }
    updateCompareTray();
    updateCompareButtonText(selectedCellId);
  }

  function updateCompareButtonText(cellId) {
    if (!btnToggleCompare) return;
    if (compareCellIds.has(cellId)) {
      btnToggleCompare.innerHTML = '<span>✔️ Tillagd i jämförelse</span>';
      btnToggleCompare.style.background = 'rgba(16,185,129,0.15)';
      btnToggleCompare.style.borderColor = 'rgba(16,185,129,0.4)';
      btnToggleCompare.style.color = '#34d399';
    } else {
      btnToggleCompare.innerHTML = '<span>➕ Lägg till för jämförelse</span>';
      btnToggleCompare.style.background = 'rgba(255,255,255,0.05)';
      btnToggleCompare.style.borderColor = 'var(--border)';
      btnToggleCompare.style.color = '#e2e8f0';
    }
  }

  function updateCompareTray() {
    const count = compareCellIds.size;
    if (txtCompareCount) txtCompareCount.textContent = count;
    if (btnOpenCompare) {
      btnOpenCompare.style.display = 'inline-flex';
      if (count > 0) {
        btnOpenCompare.style.background = 'rgba(56, 189, 248, 0.2)';
        btnOpenCompare.style.borderColor = '#38bdf8';
        btnOpenCompare.style.color = '#fff';
      } else {
        btnOpenCompare.style.background = 'rgba(255, 255, 255, 0.05)';
        btnOpenCompare.style.borderColor = 'var(--border)';
        btnOpenCompare.style.color = '#cbd5e1';
      }
    }

    if (!boxCompareTray) return;
    if (count === 0) {
      boxCompareTray.style.display = 'none';
      return;
    }

    boxCompareTray.style.display = 'flex';
    if (compareChipsContainer) {
      compareChipsContainer.innerHTML = '';
      compareCellIds.forEach(id => {
        const c = cellsData.find(x => x.id === id);
        const chip = document.createElement('div');
        chip.className = 'bs-compare-chip';
        chip.innerHTML = `
          <span>${c ? `${c.dso || c.id} (${c.zone})` : id}</span>
          <span class="del" title="Ta bort">&times;</span>
        `;
        chip.querySelector('.del').addEventListener('click', (e) => {
          e.stopPropagation();
          compareCellIds.delete(id);
          updateCompareTray();
          updateCompareButtonText(selectedCellId);
        });
        chip.addEventListener('click', () => {
          if (c) activateSite(c, -1);
        });
        compareChipsContainer.appendChild(chip);
      });
    }
  }

  function renderCompareModal() {
    if (!tableCompare || !modalCompare) return;

    // Om inga rutor valts ännu och användaren trycker på Jämför, lägg automatiskt till vald plats
    if (compareCellIds.size === 0 && selectedCellId) {
      compareCellIds.add(selectedCellId);
      updateCompareTray();
      updateCompareButtonText(selectedCellId);
    }

    const selectedCells = cellsData.filter(c => compareCellIds.has(c.id) && c.fin && c.fin.passed);
    if (!selectedCells.length) {
      tableCompare.innerHTML = `
        <tr>
          <td style="text-align:center; padding:2rem 1rem; color:var(--muted);">
            <div style="font-size:1.1em; font-weight:600; color:#fff; margin-bottom:0.5rem;">Inga rutor har valts för jämförelse än</div>
            <p style="margin:0 0 1rem; font-size:0.9em;">Klicka på <strong>"➕ Lägg till för jämförelse"</strong> i vänsterpanelen eller på en ruta i kartan för att ställa flera rutor sida vid sida.</p>
            ${selectedCellId ? `<button type="button" class="bs-btn-auto" onclick="window.bessToggleCompare('${selectedCellId}'); window.bessRenderCompareModal();" style="display:inline-flex; width:auto; padding:0.5rem 1rem;">➕ Lägg till nuvarande vald ruta (${selectedCellId})</button>` : ''}
          </td>
        </tr>
      `;
      modalCompare.style.display = 'flex';
      return;
    }

    let thead = '<thead><tr><th style="text-align:left;">Nyckeltal / Parameter</th>';
    selectedCells.forEach(c => {
      thead += `<th style="text-align:right;"><strong style="color:#fff;">${c.dso || c.id}</strong><br><small style="color:#38bdf8;">${c.zone} · Ruta ${c.id}</small></th>`;
    });
    thead += '</tr></thead>';

    const rows = [
      { label: '🏆 Platspoäng (0–100)', get: c => `<span class="bs-badge ${c.fin.verdictClass}">${c.fin.score}/100</span>` },
      { label: '⏱️ Återbetalningstid (ROI)', get: c => `<strong style="color:${getPaybackColor(c.fin.scenarioPayback)}; font-family:var(--mono);">${c.fin.scenarioPayback.toFixed(1).replace('.', ',')} år</strong>` },
      { label: 'Normalår (Utan kris)', get: c => `${c.fin.simplePayback.toFixed(1).replace('.', ',')} år` },
      { label: 'Årlig EBITDA', get: c => `<strong style="color:#38bdf8; font-family:var(--mono);">${(c.fin.normalEbitda / 1000000).toFixed(1).replace('.', ',')} MSEK</strong>` },
      { label: '10-års Kassaflöde', get: c => `${Math.round(c.fin.tenYearTotalCf / 1000000)} MSEK` },
      { label: 'Total CAPEX', get: c => `<strong style="font-family:var(--mono);">${Math.round(c.fin.totalCapex / 1000000)} MSEK</strong>` },
      { label: 'Kabelinvestering', get: c => `${(c.fin.cableCapex / 1000000).toFixed(1).replace('.', ',')} MSEK (${c.fin.distKm.toFixed(1)} km)` },
      { label: 'Närmaste Station', get: c => `${c.fin.stationName || '–'}` },
      { label: 'mFRR Kapacitetsintäkt', get: c => `${(c.fin.breakdown.mfrrRevSek / 1000000).toFixed(1).replace('.', ',')} MSEK` },
      { label: 'Spotarbitrage', get: c => `${(c.fin.breakdown.arbRevSek / 1000000).toFixed(1).replace('.', ',')} MSEK` },
      { label: 'Skyddad natur', get: c => `${Math.round(c.prot * 100)} %` },
      { label: 'Lokal flexmarknad', get: c => c.flex?.length ? `<span style="color:#34d399; font-weight:600;">${c.flex.join(', ')}</span>` : '<span style="color:var(--faint);">Nej</span>' }
    ];

    let tbody = '<tbody>';
    rows.forEach(r => {
      tbody += `<tr><td style="text-align:left; color:var(--muted); font-weight:500;">${r.label}</td>`;
      selectedCells.forEach(c => {
        tbody += `<td style="text-align:right;">${r.get(c)}</td>`;
      });
      tbody += '</tr>';
    });
    tbody += '</tbody>';

    tableCompare.innerHTML = thead + tbody;
    modalCompare.style.display = 'flex';
  }

  // --- KÄNSLIGHETS- & RISKANALYS (SENSITIVITY TORNADO) ---
  function renderSensitivityAnalysis(cell) {
    if (!sensTornadoContainer || !cell || !cell.fin) return;
    const basePb = cell.fin.scenarioPayback;
    if (txtSensBaseline) {
      txtSensBaseline.textContent = `Basfall för Ruta ${cell.id} (${cell.dso}): ${basePb.toFixed(1).replace('.', ',')} år ROI`;
    }

    const mw = parseFloat(rPowerMw.value);
    const mwh = parseFloat(rCapMwh.value);
    const extremeYears = parseInt(selExtremeYears.value, 10);
    const period = selHistoryDataset?.value || 'last12m';

    // Känsligheten körs genom exakt samma motor som basfallet (calculateCellFinancials),
    // så staplarna är alltid konsistenta med de siffror som visas i beslutsrutan.
    const calcPb = (ov = {}) => {
      const r = calculateCellFinancials(cell, ov);
      return r && r.passed ? r.scenarioPayback : 99.0;
    };

    const extUp = extremeYears >= 2 ? extremeYears + 1 : 2;
    const extNote = period === '2022'
      ? 'Ingen effekt: valt dataset är redan 2022 (extremåret ingår i basen)'
      : `${extremeYears === 0 ? '0 st (bas)' : '0 st'} vs ${extUp} st extremår (intäkt som 2022 i samma zon)`;

    const drivers = [
      {
        name: 'Stödtjänst- & Spotintäkter',
        unit: '±20 % på samtliga marknadsintäkter',
        upDiff: calcPb({ revMult: 1.20 }) - basePb,
        downDiff: calcPb({ revMult: 0.80 }) - basePb
      },
      {
        name: 'Extremår under 10-årsperioden',
        unit: extNote,
        upDiff: calcPb({ extYears: extUp }) - basePb,
        downDiff: calcPb({ extYears: 0 }) - basePb
      },
      {
        name: 'Anläggningsskala (MW & MWh tillsammans)',
        unit: `${Math.round(mw * 1.5)} MW / ${Math.round(mwh * 1.5)} MWh vs ${Math.round(mw * 0.6)} MW / ${Math.round(mwh * 0.6)} MWh. Obs: antar oförändrade marknadspriser`,
        upDiff: calcPb({ mw: mw * 1.5, mwh: mwh * 1.5 }) - basePb,
        downDiff: calcPb({ mw: mw * 0.6, mwh: mwh * 0.6 }) - basePb
      },
      {
        name: 'Batterikapacitet / Varaktighet (MWh)',
        unit: `${(mwh * 0.7).toFixed(0)} MWh (${((mwh * 0.7) / mw).toFixed(1)} h) vs ${(mwh * 1.5).toFixed(0)} MWh (${((mwh * 1.5) / mw).toFixed(1)} h) vid oförändrad effekt`,
        upDiff: calcPb({ mwh: mwh * 0.70 }) - basePb,
        downDiff: calcPb({ mwh: mwh * 1.50 }) - basePb
      },
      {
        name: 'Batteri-CAPEX (kr/kWh & kr/kW)',
        unit: '±20 % cell- & PCS-hårdvarukostnad',
        upDiff: calcPb({ batteryCapexMult: 0.80 }) - basePb,
        downDiff: calcPb({ batteryCapexMult: 1.20 }) - basePb
      },
      {
        name: 'Nätkabel & Markschakt',
        unit: '±30 % schakt- & förläggningskostnad',
        upDiff: calcPb({ cableMult: 0.70 }) - basePb,
        downDiff: calcPb({ cableMult: 1.30 }) - basePb
      },
      {
        name: 'Nätstationsfack (130 kV)',
        unit: '±30 % ställverks- & reläinvestering',
        upDiff: calcPb({ subBayMult: 0.70 }) - basePb,
        downDiff: calcPb({ subBayMult: 1.30 }) - basePb
      },
      {
        name: 'O&M / Servicekostnader',
        unit: '±25 % löpande drift- & underhållskostnad',
        upDiff: calcPb({ omMult: 0.75 }) - basePb,
        downDiff: calcPb({ omMult: 1.25 }) - basePb
      }
    ];

    // Stapelbredd skalas mot den största effekten i diagrammet (tidigare hård gräns på 3 år
    // vilket gjorde att stora effekter klipptes och såg lika stora ut).
    const maxAbs = Math.max(0.01, ...drivers.map(d => Math.max(Math.abs(d.upDiff), Math.abs(d.downDiff))));

    let html = `
      <div style="display:grid; grid-template-columns: 210px 1fr 1fr 140px; gap:0.5rem; font-size:0.75em; text-transform:uppercase; color:var(--muted); font-weight:700; margin-bottom:0.5rem; padding-bottom:0.35rem; border-bottom:1px solid rgba(255,255,255,0.08);">
        <div>Parameter &amp; Känslighetsfaktor</div>
        <div style="text-align:right; color:#34d399;">Optimistiskt utfall (Snabbare ROI)</div>
        <div style="color:#f87171;">Pessimistiskt / Stresstest (Längre ROI)</div>
        <div style="text-align:right;">Utfallsintervall</div>
      </div>
    `;

    // Sortera störst påverkan överst (en riktig tornado)
    drivers.sort((a, b) => {
      const sa = Math.abs(Math.min(a.upDiff, a.downDiff)) + Math.abs(Math.max(a.upDiff, a.downDiff));
      const sb = Math.abs(Math.min(b.upDiff, b.downDiff)) + Math.abs(Math.max(b.upDiff, b.downDiff));
      return sb - sa;
    });

    drivers.forEach(d => {
      const best = Math.min(d.upDiff, d.downDiff);
      const worst = Math.max(d.upDiff, d.downDiff);
      const bestYears = (basePb + best).toFixed(1).replace('.', ',');
      const worstYears = (basePb + worst).toFixed(1).replace('.', ',');

      const leftWidthPct = Math.min(100, Math.round((Math.abs(Math.min(0, best)) / maxAbs) * 100));
      const rightWidthPct = Math.min(100, Math.round((Math.abs(Math.max(0, worst)) / maxAbs) * 100));

      html += `
        <div class="bs-sens-row">
          <div>
            <strong style="color:#fff;">${d.name}</strong><br>
            <span style="color:var(--muted); font-size:0.9em;">${d.unit}</span>
          </div>

          <div style="display:flex; justify-content:flex-end; align-items:center;">
            <div style="display:flex; align-items:center; width:100%; justify-content:flex-end;">
              <span style="font-family:var(--mono); color:#34d399; font-weight:600; margin-right:6px; font-size:0.9em;">${bestYears} år</span>
              <div style="width:120px; height:12px; background:rgba(255,255,255,0.04); border-radius:3px; display:flex; justify-content:flex-end; overflow:hidden;">
                <div style="width:${leftWidthPct}%; background:linear-gradient(90deg, #10b981, #34d399); height:100%;"></div>
              </div>
            </div>
          </div>

          <div style="display:flex; justify-content:flex-start; align-items:center;">
            <div style="display:flex; align-items:center; width:100%; justify-content:flex-start;">
              <div style="width:120px; height:12px; background:rgba(255,255,255,0.04); border-radius:3px; overflow:hidden;">
                <div style="width:${rightWidthPct}%; background:linear-gradient(90deg, #ef4444, #f87171); height:100%;"></div>
              </div>
              <span style="font-family:var(--mono); color:#f87171; font-weight:600; margin-left:6px; font-size:0.9em;">${worstYears} år</span>
            </div>
          </div>

          <div style="text-align:right;">
            <div style="font-family:var(--mono); font-weight:700; color:#38bdf8; font-size:0.92em;">${bestYears} – ${worstYears} år</div>
          </div>
        </div>
      `;
    });

    sensTornadoContainer.innerHTML = html;
  }

  // --- DYNAMISK DATATRANSPARENS (ALLA ÅR 2015-2026) ---
  function renderDynamicTransparency(activeTab = 'summary') {
    if (!histTransparencyContent || !marketData?.spot) return;

    if (histYearTabs) {
      histYearTabs.querySelectorAll('button').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.year === activeTab);
      });
    }

    const zones = ['SE1', 'SE2', 'SE3', 'SE4'];
    const mw = parseFloat(rPowerMw.value);
    const mwh = parseFloat(rCapMwh.value);
    const is4h = (mwh / mw) >= 3.0;

    if (activeTab === 'summary') {
      let tableHtml = `
        <div style="overflow-x:auto;">
          <table class="bs-table" style="font-size:0.82em;">
            <thead>
              <tr>
                <th style="text-align:left;">År / Period</th>
                <th style="text-align:left;">Status &amp; Marknadsklimat</th>
                <th style="text-align:right;">SE4 Dygnsspread</th>
                <th style="text-align:right;">SE4 Arbitrage</th>
                <th style="text-align:right;">SE3 Arbitrage</th>
                <th style="text-align:right;">SE2 Arbitrage</th>
                <th style="text-align:right;">SE1 Arbitrage</th>
              </tr>
            </thead>
            <tbody>
      `;

      const allYears = ['2026', '2025', '2024', '2023', '2022', '2020', '2019', '2018', '2017', '2016', '2015'];
      const yearMeta = {
        '2026': 'Pågående helår med hög volatilitet',
        '2025': 'Konsoliderat marknadsläge',
        '2024': 'Mycket lugnt lågvolatilt stresstestår',
        '2023': 'Post-kris med stabiliserade priser',
        '2022': '⚡ Historiskt extremår (Energikrisen)',
        '2020': 'Extremt blött lågprisår',
        '2019': 'Normalår pre-pandemi',
        '2018': 'Torrår med tidiga sommarspreadar',
        '2017': 'Historisk basreferens',
        '2016': 'Historisk basreferens',
        '2015': 'Startår för 10-årsanalysen'
      };

      allYears.forEach(y => {
        const se4Y = marketData.spot['SE4']?.years?.[y];
        const se3Y = marketData.spot['SE3']?.years?.[y];
        const se2Y = marketData.spot['SE2']?.years?.[y];
        const se1Y = marketData.spot['SE1']?.years?.[y];
        if (!se4Y) return;

        const spd = se4Y.daily_spread_mean ? Math.round(se4Y.daily_spread_mean) + ' €/MWh' : '–';
        const a4 = se4Y ? Math.round(is4h ? se4Y.arb_4h_eur_mw_yr : se4Y.arb_2h_eur_mw_yr).toLocaleString('sv-SE') + ' €' : '–';
        const a3 = se3Y ? Math.round(is4h ? se3Y.arb_4h_eur_mw_yr : se3Y.arb_2h_eur_mw_yr).toLocaleString('sv-SE') + ' €' : '–';
        const a2 = se2Y ? Math.round(is4h ? se2Y.arb_4h_eur_mw_yr : se2Y.arb_2h_eur_mw_yr).toLocaleString('sv-SE') + ' €' : '–';
        const a1 = se1Y ? Math.round(is4h ? se1Y.arb_4h_eur_mw_yr : se1Y.arb_2h_eur_mw_yr).toLocaleString('sv-SE') + ' €' : '–';

        const isExt = y === '2022';
        tableHtml += `
          <tr style="${isExt ? 'background:rgba(239,68,68,0.08); font-weight:600;' : ''}">
            <td style="text-align:left; color:#fff; font-weight:700;">${y}</td>
            <td style="text-align:left; color:var(--muted);">${yearMeta[y] || 'Historisk observation'}</td>
            <td style="text-align:right; font-family:var(--mono); color:#fbbf24;">${spd}</td>
            <td style="text-align:right; font-family:var(--mono); color:#38bdf8;">${a4}</td>
            <td style="text-align:right; font-family:var(--mono);">${a3}</td>
            <td style="text-align:right; font-family:var(--mono);">${a2}</td>
            <td style="text-align:right; font-family:var(--mono);">${a1}</td>
          </tr>
        `;
      });

      tableHtml += `</tbody></table></div>`;
      histTransparencyContent.innerHTML = tableHtml;
    } else {
      // Detaljvy för valt enskilt år
      const yrKey = activeTab;
      let gridHtml = `<div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:1rem;">`;

      zones.forEach(z => {
        const zObj = marketData.spot[z];
        const data = yrKey === 'last12m' ? zObj?.last12m : zObj?.years?.[yrKey];
        if (!data) return;

        const arbVal = is4h ? data.arb_4h_eur_mw_yr : data.arb_2h_eur_mw_yr;
        gridHtml += `
          <div style="background:rgba(255,255,255,0.02); border:1px solid var(--border); border-radius:8px; padding:1rem;">
            <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:0.5rem; border-bottom:1px solid rgba(255,255,255,0.06); padding-bottom:0.35rem;">
              <strong style="color:#38bdf8; font-size:1.1em;">${z}</strong>
              <span style="font-size:0.75em; color:var(--muted);">${data.hours ? data.hours.toLocaleString('sv-SE') : 8760} timmar</span>
            </div>
            <div style="font-size:0.84em; line-height:1.6;">
              <div style="display:flex; justify-content:space-between;"><span>Dygnsspread:</span><strong style="color:#fbbf24; font-family:var(--mono);">${Math.round(data.daily_spread_mean || 0)} €/MWh</strong></div>
              <div style="display:flex; justify-content:space-between;"><span>Snittpris:</span><span style="font-family:var(--mono);">${data.mean?.toFixed(1) || '–'} €/MWh</span></div>
              <div style="display:flex; justify-content:space-between;"><span>Negativa timmar:</span><span style="font-family:var(--mono);">${Math.round((data.neg_share || 0) * 100)} %</span></div>
              <div style="display:flex; justify-content:space-between; margin-top:4px; padding-top:4px; border-top:1px dashed rgba(255,255,255,0.08);">
                <span>Årsarbitrage:</span><strong style="color:#34d399; font-family:var(--mono);">${Math.round(arbVal || 0).toLocaleString('sv-SE')} €/MW</strong>
              </div>
            </div>
          </div>
        `;
      });
      gridHtml += `</div>`;
      histTransparencyContent.innerHTML = gridHtml;
    }
  }

  // Global helper för popups och knappar
  window.bessToggleCompare = toggleCompareSite;
  window.bessRenderCompareModal = renderCompareModal;
  window.bessTogglePopupSection = function(id) {
    const el = document.getElementById(id);
    const arrow = document.getElementById(id + '-arrow');
    if (!el) return;
    const isHidden = (el.style.display === 'none' || !el.style.display);
    el.style.display = isHidden ? 'block' : 'none';
    if (arrow) arrow.textContent = isHidden ? '▲' : '▼';
  };

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
    window.bessOpenDispatchModal = openDispatchModal;
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
