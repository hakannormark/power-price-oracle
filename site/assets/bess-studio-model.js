/**
 * BESS Investment Studio – beräkningsmodell.
 *
 * Rena funktioner utan DOM, så att samma kod körs i webbläsaren och i
 * tests/test_bess_studio_model.js. All intäktsdata kommer från
 * data/bess-map/dispatch_backtest.json (src/geo/dispatch_engine.py).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.BessModel = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const EXTREME_YEAR = '2022';
  // Samma konvention som hembatterikalkylen (batteri-extremar.html): ett positivt
  // extremår är minst 2,0 × normalt spotvärde, ett negativt 0,6 ×. Det negativa är
  // ett stressantagande: sedan 2015 har inget helt år legat under 0,76 × snittet 2023–2025.
  const POSITIVE_THRESHOLD = 2.0;
  const NEGATIVE_FACTOR = 0.6;

  const DEFAULTS = {
    // Marknad
    marketSharePct: 5,       // högsta andel av varje stödtjänsts upphandlade volym per timme
    realizationPct: 75,      // andel av den teoretiska (perfekt optimerade) intäkten som realiseras
    // Drift
    feePct: 5,               // optimerare / BSP, andel av bruttointäkt
    omPerKw: 45,             // kr/kW/år
    gridPerKw: 40,           // kr/kW/år, fast effektavgift
    otherPctCapex: 0.5,      // försäkring, arrende, administration, % av CAPEX per år
    wearPerMwh: 80,          // kr per urladdad MWh (avsättning i förenklad kalkyl)
    // Investeringskalkyl
    lifeYears: 15,
    waccPct: 8,
    inflationPct: 2,
    degradationPct: 2,
    augmentYear: 8,
    augmentPctOfBattery: 20,
    ancTrendPct: -5,
    spotTrendPct: 0,
    taxPct: 20.6,
    depreciationYears: 10,
    gearingPct: 0,
    interestPct: 6,
    tenorYears: 10,
    residualPct: 0,
  };

  // ------------------------------------------------------------ interpolation
  function bracket(grid, x) {
    if (x <= grid[0]) return [0, 0, 0];
    const last = grid.length - 1;
    if (x >= grid[last]) return [last, last, 0];
    for (let i = 0; i < last; i++) {
      if (x <= grid[i + 1]) return [i, i + 1, (x - grid[i]) / (grid[i + 1] - grid[i])];
    }
    return [last, last, 0];
  }

  const lerp = (a, b, t) => a + (b - a) * t;
  const lerpArr = (a, b, t) => a.map((v, i) => lerp(v, b[i], t));

  /**
   * Samoptimerat utfall per MW för en period, zon, varaktighet (h) och marknadstak rho (andel / MW).
   * table väljer körning: 'coopt' (periodens egna priser) eller 'coopt_extreme' (2022 års spot).
   */
  function stackAt(data, period, zone, durationH, rho, table) {
    const source = data[table || 'coopt'];
    const zoneData = source && source[period] && source[period][zone];
    if (!zoneData) return null;
    const [d0, d1, dt] = bracket(data.durations, durationH);
    const [r0, r1, rt] = bracket(data.rho_grid, rho);
    const at = (di) => {
      const e = zoneData[String(data.durations[di])];
      const zeros = e.rev[r0].map(() => 0);
      return {
        rev: lerpArr(e.rev[r0], e.rev[r1], rt),
        mw: e.mw ? lerpArr(e.mw[r0], e.mw[r1], rt) : zeros,
        hrs: e.hrs ? lerpArr(e.hrs[r0], e.hrs[r1], rt) : zeros,
        cycles: lerp(e.cycles[r0], e.cycles[r1], rt),
      };
    };
    const a = at(d0);
    const b = at(d1);
    return {
      rev: lerpArr(a.rev, b.rev, dt),
      mw: lerpArr(a.mw, b.mw, dt),
      hrs: lerpArr(a.hrs, b.hrs, dt),
      cycles: lerp(a.cycles, b.cycles, dt),
    };
  }

  /** Ren arbitrageintäkt (EUR/MW/år) för ett prisår eller 'last12m'. */
  function spotOnly(data, zone, durationH, key) {
    const [d0, d1, dt] = bracket(data.durations, durationH);
    const pick = (di) => {
      const e = data.spot_years[zone][String(data.durations[di])][key];
      return e ? e.rev : null;
    };
    const a = pick(d0);
    const b = pick(d1);
    if (a === null || b === null) return null;
    return lerp(a, b, dt);
  }

  function spotOnlyMean(data, zone, durationH, keys) {
    const vals = keys.map((k) => spotOnly(data, zone, durationH, k)).filter((v) => v !== null);
    return vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : null;
  }

  // ------------------------------------------------------------------ datasets
  /** Valbara dataset, härledda ur vad datafilen faktiskt innehåller. */
  function datasets(data) {
    const years = data.full_years.map(String);
    const lastFull = years[years.length - 1];
    const out = [];
    const fmt = (iso) => iso.slice(0, 10);
    const p = data.periods;
    out.push({
      id: 'last12m', kind: 'actual', period: 'last12m',
      label: `Senaste 12 månaderna (${fmt(p.last12m.from)} – ${fmt(p.last12m.to)})`,
      note: 'Faktiska timpriser på alla marknader: spot, mFRR, aFRR och FCR.',
    });
    ['2025', '2024'].forEach((y) => {
      if (p[y]) {
        out.push({
          id: y, kind: 'actual', period: y, label: `Kalenderåret ${y} (faktiskt utfall)`,
          note: `Faktiska timpriser ${y} på alla marknader.` + (y === '2024' ? ' mFRR-kapacitetsmarknaden var ny och täcker inte alla timmar.' : ''),
        });
      }
    });
    const range = (n) => years.slice(-n);
    [3, 5, 10].forEach((n) => {
      if (years.length >= n) {
        const ys = range(n);
        out.push({
          id: `spot${n}`, kind: 'spot', years: ys,
          label: `Spotpriser som snittet ${ys[0]}–${lastFull} (${n} år)`,
          note: `Spotdelen räknas om till snittet av ${n} hela år. Stödtjänster som senaste 12 månaderna – de marknaderna saknar längre historik.`,
        });
      }
    });
    if (years.includes(EXTREME_YEAR)) {
      out.push({
        id: 'spot2022', kind: 'spot', years: [EXTREME_YEAR],
        label: 'Spotpriser som 2022 (energikrisen)',
        note: 'Spotdelen räknas om till 2022 års priser. Stödtjänster som senaste 12 månaderna.',
      });
    }
    return out;
  }

  // ------------------------------------------------------------------ revenue
  /**
   * Årsintäkt för en anläggning i ett elområde.
   *
   * Normalår  = stödtjänster + spotdel, båda ur den samoptimerade körningen för basperioden.
   *             För ett spotscenario skalas spotdelen med kvoten mellan ren arbitrageintäkt
   *             i scenariots prisår och i basperioden.
   * Positivt extremår = en egen samoptimering med 2022 års spotpriser mot basperiodens
   *             stödtjänstpriser. Batteriet flyttar då från reserverna till arbitrage, så
   *             spotdelen växer mer och stödtjänstdelen krymper något. Att bara skala
   *             normalårets spotdel underskattade effekten. Stödtjänstdelen kan dessutom
   *             multipliceras (ancillaryExtremeMult) för att pröva ett år där även
   *             reservpriserna stiger. Aldrig lägre än normalåret (extremeFloorApplied).
   * Negativt extremår = samma stödtjänster, spotdelen × 0,6 (NEGATIVE_FACTOR).
   * Prisunderlaget "som 2022" använder extremårskörningen som normalår.
   */
  function siteRevenue(data, o) {
    const ds = o.dataset;
    const basePeriod = ds.kind === 'actual' ? ds.period : 'last12m';
    const share = Math.max(0, o.marketSharePct) / 100;
    const rho = o.mw > 0 ? share / o.mw : 0;
    const baseStack = stackAt(data, basePeriod, o.zone, o.durationH, rho);
    if (!baseStack) return null;
    const extStack = stackAt(data, basePeriod, o.zone, o.durationH, rho, 'coopt_extreme');
    const extremeIsBase = ds.kind === 'spot' && ds.years.length === 1 && ds.years[0] === EXTREME_YEAR;
    const stack = extremeIsBase && extStack ? Object.assign({}, baseStack, { rev: extStack.rev, cycles: extStack.cycles }) : baseStack;
    const realization = o.realizationPct / 100;
    const k = o.mw * o.fx * realization * (o.revMult === undefined ? 1 : o.revMult);
    // Reserve prices in the extreme year: a number the user chose, or else the
    // measured relation between each product's price and the spot price.
    const measured = o.ancillaryExtremeMult === undefined || o.ancillaryExtremeMult === null || o.ancillaryExtremeMult === 'measured';
    const ancMult = measured ? 1 : Math.max(0, o.ancillaryExtremeMult);
    const comove = measured && data.reserve_comovement && data.reserve_comovement.factors
      && data.reserve_comovement.factors[basePeriod] && data.reserve_comovement.factors[basePeriod][o.zone];

    const arbBase = spotOnly(data, o.zone, o.durationH, basePeriod);
    const arbScenario = ds.kind === 'spot' ? spotOnlyMean(data, o.zone, o.durationH, ds.years) : arbBase;
    const arbExtreme = spotOnly(data, o.zone, o.durationH, EXTREME_YEAR);
    // The 2022 data set is the extreme run itself; no further scaling of its spot part.
    const spotFactor = extremeIsBase && extStack ? 1 : (arbBase > 0 && arbScenario !== null ? arbScenario / arbBase : 1);
    const arbRatio = arbBase > 0 && arbExtreme !== null ? arbExtreme / arbBase : 1;

    const products = data.products;
    const perProduct = {};
    products.forEach((p, i) => {
      // With 2022 as the price data set every year is the extreme year, reserves included.
      const reserve = extremeIsBase && extStack ? (comove ? (comove[p] > 0 ? comove[p] : 1) : ancMult) : 1;
      perProduct[p] = stack.rev[i] * k * (i === 0 ? spotFactor : reserve);
    });
    const spot = perProduct.spot;
    const anc = products.slice(1).reduce((s, p) => s + perProduct[p], 0);

    // Extreme year: its own optimisation where the file has one, else the old scaling.
    let spotExtreme;
    let ancExtremeBase;
    let ancExtremeUnchanged = null;
    if (extStack) {
      spotExtreme = extStack.rev[0] * k;
      ancExtremeBase = extStack.rev.slice(1).reduce((s, v, i) => {
        const f = comove && comove[products[i + 1]];
        return s + v * (f > 0 ? f : 1);
      }, 0) * k;
      ancExtremeUnchanged = extStack.rev.slice(1).reduce((s, v) => s + v, 0) * k;
    } else {
      spotExtreme = baseStack.rev[0] * k * arbRatio;
      ancExtremeBase = anc;
    }
    let ancExtreme = ancExtremeBase * ancMult;
    let extremeFloorApplied = false;
    if (extremeIsBase) {
      // Every year is already 2022; an "extreme year" is just another of the same.
      spotExtreme = spot;
      ancExtreme = anc;
    } else if (spotExtreme + ancExtreme < (spot + anc) * (1 - 1e-9)) {
      extremeFloorApplied = true;
      spotExtreme = spot;
      ancExtreme = anc;
    }
    const spotNegative = spot * NEGATIVE_FACTOR;

    const market = (data.market[basePeriod] || {})[o.zone] || {};
    const sold = {};
    products.forEach((p, i) => {
      const mwSold = baseStack.mw[i] * o.mw;
      const vol = market[p] && market[p].volume_mean_mw;
      sold[p] = {
        mw: mwSold,
        hoursShare: baseStack.hrs[i],
        marketShare: i > 0 && vol > 0 ? mwSold / vol : null,
        avgPrice: i > 0 && baseStack.mw[i] > 0 ? baseStack.rev[i] / (baseStack.mw[i] * 8760) : null,
      };
    });

    // 2022 as it actually was for a battery, for reference only.
    const actual = data.actual_2022 ? stackAt({ durations: data.durations, rho_grid: data.rho_grid, x: { y: data.actual_2022 } }, 'y', o.zone, o.durationH, rho, 'x') : null;

    const gross = spot + anc;
    return {
      basePeriod, rho, perProduct, spot, anc, gross,
      spotExtreme, ancExtreme,
      grossExtreme: spotExtreme + ancExtreme,
      spotNegative,
      grossNegative: spotNegative + anc,
      spotFactor, extremeFloorApplied, extremeIsBase,
      // What the measured relation did to the reserves of the extreme year (1 = unchanged prices).
      ancExtremeFactor: ancExtremeUnchanged > 0 && !extremeIsBase && !extremeFloorApplied ? ancExtreme / ancExtremeUnchanged : 1,
      ancExtremeMeasured: !!comove,
      reoptimised: !!extStack,
      // 2022 års rena arbitrage i förhållande till normalårets, att jämföra med gränsen 2,0 ×.
      extremeRatio: spotFactor > 0 ? arbRatio / (extremeIsBase ? arbRatio : spotFactor) : 1,
      extremeMeetsThreshold: !extremeIsBase && spotFactor > 0 && arbRatio / spotFactor >= POSITIVE_THRESHOLD,
      // Hela intäkten 2022 som den var, mot normalåret (utan stödtjänstmultiplikator).
      actual2022Gross: actual ? actual.rev.reduce((s, v) => s + v, 0) * k : null,
      cycles: stack.cycles,
      theoreticalPerMwEur: stack.rev.reduce((s, v, i) => s + v * (i === 0 ? spotFactor : 1), 0),
      sold,
    };
  }

  // -------------------------------------------------------------------- capex
  function capex(o) {
    const mwh = o.mw * o.durationH;
    const energy = mwh * 1000 * o.capexPerKwh;
    const power = o.mw * 1000 * o.capexPerKw;
    const cable = o.cableKm * o.routingFactor * o.cablePerKmMsek * 1e6;
    const bay = o.bayMsek * 1e6;
    return { mwh, energy, power, cable, bay, battery: energy + power, total: energy + power + cable + bay };
  }

  // -------------------------------------------------- förenklad kalkyl (10 år)
  function opex(o, cap, gross, cycles) {
    const fee = gross * o.feePct / 100;
    const om = o.mw * 1000 * o.omPerKw;
    const grid = o.mw * 1000 * o.gridPerKw;
    const other = cap.total * o.otherPctCapex / 100;
    const wear = cycles * cap.mwh * o.wearPerMwh;
    return { fee, om, grid, other, wear, fixed: om + grid + other, total: fee + om + grid + other + wear };
  }

  /**
   * Förenklad kalkyl: tio år, varav `extremeYears` med 2022 års spotpriser och
   * `negativeYears` med 0,6 × normalårets spotdel. Resten är normalår.
   * Odiskonterad återbetalningstid = investering / genomsnittligt årligt driftnetto.
   * Negativt driftnetto visas som negativt; återbetalningstiden är då null.
   */
  function simpleFinancials(o, rev) {
    const cap = capex(o);
    const nExt = rev.extremeIsBase ? 0 : Math.max(0, Math.min(10, o.extremeYears || 0));
    const nNeg = Math.max(0, Math.min(10 - nExt, o.negativeYears || 0));
    const grossNegative = rev.grossNegative === undefined ? rev.gross : rev.grossNegative;
    const oxNormal = opex(o, cap, rev.gross, rev.cycles);
    const oxExtreme = opex(o, cap, rev.grossExtreme, rev.cycles);
    const oxNegative = opex(o, cap, grossNegative, rev.cycles);
    const netNormal = rev.gross - oxNormal.total;
    const netExtreme = rev.grossExtreme - oxExtreme.total;
    const netNegative = grossNegative - oxNegative.total;
    const tenYear = netNormal * (10 - nExt - nNeg) + netExtreme * nExt + netNegative * nNeg;
    const avg = tenYear / 10;
    return {
      capex: cap, opexNormal: oxNormal, opexExtreme: oxExtreme, opexNegative: oxNegative,
      netNormal, netExtreme, netNegative, tenYear, avg, extremeYearsUsed: nExt, negativeYearsUsed: nNeg,
      paybackNormal: netNormal > 0 ? cap.total / netNormal : null,
      payback: avg > 0 ? cap.total / avg : null,
    };
  }

  // ------------------------------------------------------- investeringskalkyl
  function npv(rate, flows) {
    return flows.reduce((s, cf, t) => s + cf / Math.pow(1 + rate, t), 0);
  }

  /** Internränta med bisektion. null om kassaflödet aldrig byter tecken eller roten ligger utanför [-0,99; 10]. */
  function irr(flows) {
    let lo = -0.99;
    let hi = 10;
    let fLo = npv(lo, flows);
    const fHi = npv(hi, flows);
    if (!isFinite(fLo) || !isFinite(fHi) || fLo * fHi > 0) return null;
    for (let i = 0; i < 200; i++) {
      const mid = (lo + hi) / 2;
      const fMid = npv(mid, flows);
      if (Math.abs(fMid) < 1e-6 * Math.max(1, Math.abs(flows[0]))) return mid;
      if (fLo * fMid <= 0) hi = mid; else { lo = mid; fLo = fMid; }
    }
    return (lo + hi) / 2;
  }

  /** Vilka år (1..life) som är extremår om användaren inte anger något: jämnt utspridda. */
  function defaultExtremeYearPositions(count, life) {
    const out = [];
    for (let i = 1; i <= count; i++) out.push(Math.max(1, Math.min(life, Math.round((i * life) / (count + 1)))));
    return Array.from(new Set(out));
  }

  /** Förvalda negativa extremår: mitt emellan de positiva, aldrig samma år som ett positivt. */
  function defaultNegativeYearPositions(count, life, taken) {
    const used = new Set(taken || []);
    const out = [];
    for (let i = 1; i <= count; i++) {
      let y = Math.max(1, Math.min(life, Math.round(((i - 0.55) * life) / count)));
      let guard = 0;
      while (used.has(y) && guard++ < life) y = (y % life) + 1;
      if (!used.has(y)) { out.push(y); used.add(y); }
    }
    return out.sort((a, b) => a - b);
  }

  /**
   * Nominell kassaflödesmodell år för år.
   *
   *  intäkt_t   = [spot_t + stödtjänster × (1+trend)^(t-1) × kapacitetsfaktor_anc] × (1+inflation)^(t-1)
   *               spot_t = spot (extremårets eller det negativa extremårets spot de åren)
   *                        × (1+spottrend)^(t-1) × kapacitetsfaktor. Står ett år i båda
   *                        listorna räknas det som positivt extremår.
   *  kapacitet  = (1 − degradering)^(år sedan start eller senaste cellkomplettering);
   *               slår fullt på spotdelen och till hälften på stödtjänsterna (effektprodukter).
   *  EBITDA     = intäkt − arvode − fasta driftkostnader (O&M, nät, övrigt; inflationsuppräknade)
   *  skatt      = skattesats × (EBITDA − avskrivning [− ränta]) med förlustavdrag som rullas framåt
   *  FCFF       = EBITDA − skatt (utan ränteavdrag) − cellkomplettering; år 0 = −CAPEX; restvärde sista året
   *  FCFE       = EBITDA − skatt − cellkomplettering − ränta − amortering; år 0 = −eget kapital
   *
   * Cykelslitage dras inte som kostnad här – det representeras av degradering och cellkomplettering.
   */
  function investmentModel(p) {
    const N = Math.max(1, Math.round(p.lifeYears));
    const infl = p.inflationPct / 100;
    const deg = p.degradationPct / 100;
    const gAnc = p.ancTrendPct / 100;
    const gSpot = p.spotTrendPct / 100;
    const tax = p.taxPct / 100;
    const wacc = p.waccPct / 100;
    const extSet = new Set((p.extremeYearList || []).filter((y) => y >= 1 && y <= N));
    const negSet = new Set((p.negativeYearList || []).filter((y) => y >= 1 && y <= N && !extSet.has(y)));
    const spotNeg = p.spotNegative === undefined ? p.spot : p.spotNegative;
    const augYear = p.augmentYear >= 1 && p.augmentYear < N ? Math.round(p.augmentYear) : 0;
    const augCost = augYear ? p.batteryEnergyCapex * p.augmentPctOfBattery / 100 : 0;
    const depYears = Math.max(1, Math.round(p.depreciationYears));

    const debt0 = p.capexTotal * p.gearingPct / 100;
    const r = p.interestPct / 100;
    const tenor = Math.max(1, Math.min(N, Math.round(p.tenorYears)));
    const annuity = debt0 > 0 ? (r > 0 ? debt0 * r / (1 - Math.pow(1 + r, -tenor)) : debt0 / tenor) : 0;

    const rows = [];
    const fcff = [-p.capexTotal];
    const fcfe = [-(p.capexTotal - debt0)];
    let debt = debt0;
    let lossU = 0;
    let lossL = 0;
    let sinceRefresh = 0;
    let minDscr = null;
    let cumDisc = -p.capexTotal;
    let cum = -p.capexTotal;
    let discPayback = null;
    let payback = null;

    for (let t = 1; t <= N; t++) {
      const cap = Math.pow(1 - deg, sinceRefresh);
      const price = Math.pow(1 + infl, t - 1);
      const isExt = extSet.has(t);
      const isNeg = negSet.has(t);
      const spot = (isExt ? p.spotExtreme : isNeg ? spotNeg : p.spot) * Math.pow(1 + gSpot, t - 1) * cap * price;
      const ancYear = isExt && p.ancExtreme !== undefined ? p.ancExtreme : p.anc;
      const anc = ancYear * Math.pow(1 + gAnc, t - 1) * (1 - 0.5 * (1 - cap)) * price;
      const revenue = spot + anc;
      const fee = revenue * p.feePct / 100;
      const fixed = p.fixedOpex * price;
      const ebitda = revenue - fee - fixed;

      let depreciation = t <= depYears ? p.capexTotal / depYears : 0;
      if (augYear && t > augYear) depreciation += augCost / Math.max(1, N - augYear);
      const augmentation = t === augYear ? augCost : 0;

      const interest = t <= tenor ? debt * r : 0;
      const principal = t <= tenor ? Math.min(debt, annuity - interest) : 0;
      debt -= principal;

      const ebit = ebitda - depreciation;
      let taxU = 0;
      if (ebit + lossU > 0) { taxU = (ebit + lossU) * tax; lossU = 0; } else lossU += ebit;
      const ebt = ebit - interest;
      let taxL = 0;
      if (ebt + lossL > 0) { taxL = (ebt + lossL) * tax; lossL = 0; } else lossL += ebt;

      const residual = t === N ? p.capexTotal * p.residualPct / 100 : 0;
      const cfU = ebitda - taxU - augmentation + residual;
      const cfL = ebitda - taxL - augmentation - interest - principal + residual;
      fcff.push(cfU);
      fcfe.push(cfL);

      if (interest + principal > 0) {
        const dscr = (ebitda - taxL) / (interest + principal);
        minDscr = minDscr === null ? dscr : Math.min(minDscr, dscr);
      }
      const disc = cfU / Math.pow(1 + wacc, t);
      if (discPayback === null && cumDisc < 0 && cumDisc + disc >= 0) discPayback = t - 1 + (-cumDisc) / disc;
      if (payback === null && cum < 0 && cum + cfU >= 0) payback = t - 1 + (-cum) / cfU;
      cumDisc += disc;
      cum += cfU;

      rows.push({
        year: t, extreme: isExt, negative: isNeg, capacity: cap, spot, anc, revenue, fee, fixed, ebitda, depreciation,
        interest, principal, tax: taxU, taxLevered: taxL, augmentation, residual, fcff: cfU, fcfe: cfL,
        cumulative: cum, cumulativeDiscounted: cumDisc,
      });
      sinceRefresh = t === augYear ? 0 : sinceRefresh + 1;
    }

    return {
      rows, fcff, fcfe,
      npv: npv(wacc, fcff),
      irr: irr(fcff),
      equityIrr: debt0 > 0 ? irr(fcfe) : null,
      payback, discountedPayback: discPayback, minDscr,
      debt: debt0, equity: p.capexTotal - debt0,
      totalRevenue: rows.reduce((s, x) => s + x.revenue, 0),
      totalEbitda: rows.reduce((s, x) => s + x.ebitda, 0),
    };
  }

  // ---------------------------------------------------------------------- geo
  /** Avstånd i km mellan två punkter (lat, lon i grader). */
  function distanceKm(lat1, lon1, lat2, lon2) {
    const toRad = Math.PI / 180;
    const dLat = (lat2 - lat1) * toRad;
    const dLon = (lon2 - lon1) * toRad;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * toRad) * Math.cos(lat2 * toRad) * Math.sin(dLon / 2) ** 2;
    return 6371 * 2 * Math.asin(Math.sqrt(a));
  }

  /** Anslutningsklass: regionnät (100–199 kV) för större anläggningar, annars även 40–99 kV. */
  function stationClassFor(mw) {
    return mw > 10 ? 'regional' : 'any';
  }

  function isCandidate(kv, cls) {
    if (kv >= 200) return false;           // transmissionsnät: ingen normal anslutningspunkt för en BESS
    return cls === 'regional' ? kv >= 100 : kv >= 40;
  }

  /** Närmaste anslutningsbara station. stations = [[lat, lon, kV, namn, ägare], ...]. */
  function nearestStation(lat, lon, stations, cls) {
    let best = null;
    let bestD = Infinity;
    const cosLat = Math.cos(lat * Math.PI / 180);
    for (let i = 0; i < stations.length; i++) {
      const s = stations[i];
      if (!isCandidate(s[2], cls)) continue;
      const dy = (s[0] - lat) * 111.2;
      const dx = (s[1] - lon) * 111.2 * cosLat;
      const d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = s; }
    }
    if (!best) return null;
    return { km: distanceKm(lat, lon, best[0], best[1]), kv: best[2], name: best[3] || '', operator: best[4] || '', lat: best[0], lon: best[1] };
  }

  // -------------------------------------------------------------------- score
  /** Platspoäng 0–100: nätnärhet 40 %, zonens marknad 40 %, mark och miljö 20 %. */
  function siteScore(o) {
    const conn = Math.max(0, Math.min(1, 1 - o.cableKm / 15));
    const market = Math.max(0, Math.min(1, o.zoneGrossPerMw / o.bestZoneGrossPerMw));
    const land = Math.max(0, 1 - (o.prot || 0) - 0.5 * (o.water || 0));
    const score = Math.round(100 * (0.40 * conn + 0.40 * market + 0.20 * land));
    return { score, conn, market, land };
  }

  return {
    DEFAULTS, EXTREME_YEAR, POSITIVE_THRESHOLD, NEGATIVE_FACTOR,
    bracket, stackAt, spotOnly, spotOnlyMean, datasets, siteRevenue,
    capex, opex, simpleFinancials,
    npv, irr, defaultExtremeYearPositions, defaultNegativeYearPositions, investmentModel,
    distanceKm, stationClassFor, isCandidate, nearestStation, siteScore,
  };
});
