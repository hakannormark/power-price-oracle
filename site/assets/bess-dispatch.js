/**
 * Hembatteriets värdering: billigaste körschemat för ett batteri i ett hushåll.
 *
 * Samma algoritm som src/bess/optimal.py och src/bess/dispatch.py. Batteriets
 * värde är elräkningen utan batteri minus elräkningen med, och sollagring och
 * prisarbitrage delar på ett batteri och en uppsättning cykler.
 * tests/test_bess_dispatch_js.js kontrollerar att den här filen och Python-koden
 * ger samma tal.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.BessDispatch = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const LEVELS = 49;
  const INFEASIBLE = 1e12;

  function billWithoutBattery(net, imp, exp, group, byGroup) {
    let bill = 0;
    for (let t = 0; t < net.length; t++) {
      const cost = net[t] > 0 ? net[t] * imp[t] : net[t] * exp[t];
      bill += cost;
      if (byGroup) byGroup[group[t]] += cost;
    }
    return bill;
  }

  /**
   * Billigaste schemat. net = förbrukning minus solel per timme (kWh),
   * imp/exp = köp- och säljpris (kr/kWh). Returnerar räkningen och flödena.
   */
  function optimalSchedule(net, imp, exp, windowKwh, maxPowerKw, roundTripEff, allowGridCharging, wearHurdle, group, byGroup) {
    const n = net.length;
    if (!(windowKwh > 0) || !(maxPowerKw > 0) || n === 0) {
      return { cost: billWithoutBattery(net, imp, exp, group, byGroup), charged: 0, chargedSolar: 0, discharged: 0 };
    }
    const S = LEVELS;
    const eta = Math.sqrt(roundTripEff);
    const step = windowKwh / (S - 1);
    // ac[i*S+j]: vad batteriet tar från (+) eller ger (−) huset när lagret går från nivå i till j.
    const ac = new Float64Array(S * S);
    const penalty = new Float64Array(S * S);
    for (let i = 0; i < S; i++) {
      for (let j = 0; j < S; j++) {
        const delta = (j - i) * step;
        const a = delta > 0 ? delta / eta : delta * eta;
        ac[i * S + j] = a;
        penalty[i * S + j] = Math.abs(a) <= maxPowerKw + 1e-9 ? (delta < 0 ? -a * wearHurdle : 0) : INFEASIBLE;
      }
    }

    let value = new Float64Array(S);
    let next = new Float64Array(S);
    const choice = new Uint8Array(n * S);
    for (let t = n - 1; t >= 0; t--) {
      const nt = net[t];
      const it = imp[t];
      const et = exp[t];
      const surplus = nt < 0 ? -nt : 0;
      for (let i = 0; i < S; i++) {
        let best = Infinity;
        let bestJ = i;
        const row = i * S;
        for (let j = 0; j < S; j++) {
          const p = penalty[row + j];
          if (p >= INFEASIBLE) continue;
          const a = ac[row + j];
          if (!allowGridCharging && j > i && a > surplus + 1e-9) continue;
          const flow = nt + a;
          const cost = (flow > 0 ? flow * it : flow * et) + p + value[j];
          if (cost < best) { best = cost; bestJ = j; }
        }
        next[i] = best;
        choice[t * S + i] = bestJ;
      }
      const swap = value; value = next; next = swap;
    }

    let state = 0;
    let bill = 0;
    let charged = 0;
    let chargedSolar = 0;
    let discharged = 0;
    for (let t = 0; t < n; t++) {
      const j = choice[t * S + state];
      const a = ac[state * S + j];
      const flow = net[t] + a;
      const cost = flow > 0 ? flow * imp[t] : flow * exp[t];
      bill += cost;
      if (byGroup) byGroup[group[t]] += cost;
      if (a > 0) {
        charged += a;
        chargedSolar += Math.min(a, net[t] < 0 ? -net[t] : 0);
      } else {
        discharged -= a;
      }
      state = j;
    }
    return { cost: bill, charged, chargedSolar, discharged };
  }

  /**
   * Timserierna för ett elområde. period: undefined = det senaste året,
   * 'extreme' = 2022, 'weak' = 2020. orientation: 'south' eller 'eastwest'.
   */
  function seriesFor(hourly, zone, period, orientation) {
    const src = period === true ? hourly.extreme : (period ? hourly[period] : hourly);
    const pv = src && src.pv && src.pv[orientation || 'south'];
    if (!src || !src.spot || !src.spot[zone] || !pv || !pv[zone] || !src.deg || !src.deg[zone]) return null;
    return { spot: src.spot[zone], pv: pv[zone], base: Array.isArray(src.base) ? src.base : src.base[zone], deg: src.deg[zone] };
  }

  /** Solproduktion per installerad kWp och år i elområdet, ur uppmätt instrålning i panelens plan. */
  function yieldPerKwp(hourly, zone, orientation) {
    const s = seriesFor(hourly, zone, null, orientation);
    if (!s) return 0;
    let sum = 0;
    for (let t = 0; t < s.pv.length; t++) sum += s.pv[t];
    return sum / 1000 * 8760 / s.pv.length;
  }

  /**
   * Förbrukningen timme för timme (kWh). Hushållsel följer klockan; uppvärmning
   * följer elområdets uppmätta temperatur. Samma formel som profiles.compose_load.
   */
  function composeLoad(series, constants, annualKwh, heatingShare) {
    const n = series.base.length;
    const h = heatingShare == null ? constants.heating_share : heatingShare;
    const raw = new Float64Array(n);
    let baseSum = 0;
    for (let t = 0; t < n; t++) baseSum += series.base[t];
    const baseMean = baseSum / n;
    let sum = 0;
    for (let t = 0; t < n; t++) {
      raw[t] = (1 - h) * series.base[t] / baseMean + h * (series.deg[t] / 10) / constants.reference_mean_degrees;
      sum += raw[t];
    }
    const scale = annualKwh * (n / 8760) / sum;
    for (let t = 0; t < n; t++) raw[t] *= scale;
    return raw;
  }

  /**
   * Årsvärdet av ett batteri i ett hushåll.
   *
   * hourly: innehållet i data/bess-hourly.json. o: { zone, loadKwh, pvKwp,
   * usableKwh, powerKw, roundTripEff, strategy, period, orientation,
   * heatingShare }. Sol och temperatur är elområdets uppmätta, timme för timme.
   * Med period 'extreme' eller 'weak' räknas samma anläggning mot 2022
   * respektive 2020.
   */
  function valueBattery(hourly, o) {
    const k = hourly.constants;
    const period = o.period || (o.extreme ? 'extreme' : null);
    const series = seriesFor(hourly, o.zone, period, o.orientation);
    if (!series) return null;
    const spot = series.spot;
    const n = spot.length;
    const yearShare = n / 8760;
    const net = new Float64Array(n);
    const imp = new Float64Array(n);
    const exp = new Float64Array(n);
    const load = composeLoad(series, k, o.loadKwh, o.heatingShare);
    const pvKwp = o.pvKwp || 0;
    for (let t = 0; t < n; t++) {
      net[t] = load[t] - series.pv[t] / 1000 * pvKwp;
      imp[t] = spot[t] * k.spot_vat + k.import_adders_sek;
      exp[t] = spot[t] + k.export_adder_sek;
    }
    const reserve = (k.fcr_window_reserve || {})[o.strategy] || 0;
    const windowKwh = o.usableKwh * (1 - reserve);
    // The bill per calendar quarter, so that the seasonal split is the schedule's own.
    const quarter = new Uint8Array(n);
    const start = period ? null : new Date(hourly.from).getTime();
    for (let t = 0; t < n; t++) {
      quarter[t] = start === null ? Math.min(3, Math.floor(t / (n / 4))) : Math.floor(new Date(start + t * 3600e3).getUTCMonth() / 3);
    }
    const qBase = new Float64Array(4);
    const qSolar = new Float64Array(4);
    const qFull = new Float64Array(4);
    const baseline = billWithoutBattery(net, imp, exp, quarter, qBase);
    const solarOnly = optimalSchedule(net, imp, exp, windowKwh, o.powerKw, o.roundTripEff, false, k.wear_hurdle_sek_per_kwh, quarter, qSolar);
    const full = o.strategy === 'fcr_priority'
      ? solarOnly
      : optimalSchedule(net, imp, exp, windowKwh, o.powerKw, o.roundTripEff, true, k.wear_hurdle_sek_per_kwh, quarter, qFull);
    const toYear = 1 / yearShare;
    const quarters = [0, 1, 2, 3].map((q) => ({
      solarSek: (qBase[q] - qSolar[q]) * k.realisation * toYear,
      arbitrageSek: (o.strategy === 'fcr_priority' ? 0 : qSolar[q] - qFull[q]) * k.realisation * toYear,
    }));
    return {
      quarters,
      solarSek: Math.max(0, baseline - solarOnly.cost) * k.realisation * toYear,
      arbitrageSek: Math.max(0, solarOnly.cost - full.cost) * k.realisation * toYear,
      storedSolarKwh: full.chargedSolar * toYear,
      gridChargedKwh: (full.charged - full.chargedSolar) * toYear,
      throughputKwh: full.charged * toYear,
      baselineBillSek: baseline * toYear,
    };
  }

  return { LEVELS, billWithoutBattery, optimalSchedule, valueBattery, yieldPerKwp, seriesFor, composeLoad };
});
