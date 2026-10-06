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

  function billWithoutBattery(net, imp, exp) {
    let bill = 0;
    for (let t = 0; t < net.length; t++) bill += net[t] > 0 ? net[t] * imp[t] : net[t] * exp[t];
    return bill;
  }

  /**
   * Billigaste schemat. net = förbrukning minus solel per timme (kWh),
   * imp/exp = köp- och säljpris (kr/kWh). Returnerar räkningen och flödena.
   */
  function optimalSchedule(net, imp, exp, windowKwh, maxPowerKw, roundTripEff, allowGridCharging, wearHurdle) {
    const n = net.length;
    if (!(windowKwh > 0) || !(maxPowerKw > 0) || n === 0) {
      return { cost: billWithoutBattery(net, imp, exp), charged: 0, chargedSolar: 0, discharged: 0 };
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
      bill += flow > 0 ? flow * imp[t] : flow * exp[t];
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

  /** Timserierna för ett elområde: det senaste året, eller extremåret (2022) om extreme är sant. */
  function seriesFor(hourly, zone, extreme) {
    const src = extreme ? hourly.extreme : hourly;
    if (!src || !src.spot || !src.spot[zone] || !src.pv_per_kwp || !src.pv_per_kwp[zone]) return null;
    return { spot: src.spot[zone], pv: src.pv_per_kwp[zone], load: src.w_load };
  }

  /** Solproduktion per installerad kWp och år i elområdet, ur uppmätt instrålning. */
  function yieldPerKwp(hourly, zone) {
    const s = seriesFor(hourly, zone, false);
    if (!s) return 0;
    let sum = 0;
    for (let t = 0; t < s.pv.length; t++) sum += s.pv[t];
    return sum * 8760 / s.pv.length;
  }

  /**
   * Årsvärdet av ett batteri i ett hushåll.
   *
   * hourly: innehållet i data/bess-hourly.json. o: { zone, loadKwh, pvKwp,
   * usableKwh, powerKw, roundTripEff, strategy, extreme }. pvKwp är installerad
   * effekt vid standardfaktorn; solen är elområdets uppmätta, timme för timme.
   * Med extreme räknas samma anläggning mot 2022 års priser och sol.
   */
  function valueBattery(hourly, o) {
    const k = hourly.constants;
    const series = seriesFor(hourly, o.zone, !!o.extreme);
    if (!series) return null;
    const spot = series.spot;
    const n = spot.length;
    const yearShare = n / 8760;
    const net = new Float64Array(n);
    const imp = new Float64Array(n);
    const exp = new Float64Array(n);
    const loadScale = o.loadKwh * yearShare / 1e6;
    const pvKwp = o.pvKwp || 0;
    for (let t = 0; t < n; t++) {
      net[t] = series.load[t] * loadScale - series.pv[t] * pvKwp;
      imp[t] = spot[t] * k.spot_vat + k.import_adders_sek;
      exp[t] = spot[t] + k.export_adder_sek;
    }
    const reserve = (k.fcr_window_reserve || {})[o.strategy] || 0;
    const windowKwh = o.usableKwh * (1 - reserve);
    const baseline = billWithoutBattery(net, imp, exp);
    const solarOnly = optimalSchedule(net, imp, exp, windowKwh, o.powerKw, o.roundTripEff, false, k.wear_hurdle_sek_per_kwh);
    const full = o.strategy === 'fcr_priority'
      ? solarOnly
      : optimalSchedule(net, imp, exp, windowKwh, o.powerKw, o.roundTripEff, true, k.wear_hurdle_sek_per_kwh);
    const toYear = 1 / yearShare;
    return {
      solarSek: Math.max(0, baseline - solarOnly.cost) * k.realisation * toYear,
      arbitrageSek: Math.max(0, solarOnly.cost - full.cost) * k.realisation * toYear,
      storedSolarKwh: full.chargedSolar * toYear,
      gridChargedKwh: (full.charged - full.chargedSolar) * toYear,
      throughputKwh: full.charged * toYear,
      baselineBillSek: baseline * toYear,
    };
  }

  return { LEVELS, billWithoutBattery, optimalSchedule, valueBattery, yieldPerKwp, seriesFor };
});
