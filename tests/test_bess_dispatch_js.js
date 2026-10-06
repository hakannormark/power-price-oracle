/**
 * The browser calculator (site/assets/bess-dispatch.js) against the Python
 * valuation (src/bess/dispatch.py), on the published data.
 * Run: node tests/test_bess_dispatch_js.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const B = require('../site/assets/bess-dispatch.js');

const read = (f) => JSON.parse(fs.readFileSync(path.join(__dirname, '../site/data', f), 'utf8'));
const hourly = read('bess-hourly.json');
const bess = read('bess.json');
let checks = 0;
const near = (a, b, tol, msg) => { checks++; assert.ok(Math.abs(a - b) <= tol, `${msg}: js ${a.toFixed(1)} vs python ${b}`); };

assert.strictEqual(hourly.hours, hourly.base.length);
assert.strictEqual(hourly.constants.levels, B.LEVELS, 'both sides use the same grid');

// Every published offer, both strategies, every zone: the browser must reproduce Python.
for (const zone of Object.keys(bess.zones)) {
  for (const offer of bess.zones[zone].offers) {
    for (const strategy of ['mixed', 'energy_only']) {
      const py = offer.annual_dispatch[strategy];
      const js = B.valueBattery(hourly, {
        zone, loadKwh: 8000, pvKwp: hourly.constants.case_house_pv_kwp, usableKwh: offer.usable_kwh,
        powerKw: offer.battery_max_power_kw, roundTripEff: offer.round_trip_eff, strategy,
      });
      const tag = `${zone} ${offer.id} ${strategy}`;
      // The published prices are rounded to four decimals and the shapes to one; allow 1 %.
      near(js.solarSek, py.solar_savings_sek, Math.max(15, 0.01 * py.solar_savings_sek), `${tag} solar`);
      near(js.arbitrageSek, py.arbitrage_profit_sek, Math.max(15, 0.015 * py.arbitrage_profit_sek), `${tag} arbitrage`);
    }
  }
}

// Properties of the valuation itself.
const base = { zone: 'SE4', loadKwh: 8000, pvKwp: 10, usableKwh: 13.5, powerKw: 10, roundTripEff: 0.9, strategy: 'energy_only' };
const v = B.valueBattery(hourly, base);
checks++; assert.ok(v.solarSek > 0 && v.arbitrageSek > 0, 'a battery with solar is worth something');
checks++; assert.ok(v.throughputKwh / 15 < 366, `at most about a cycle a day (${(v.throughputKwh / 15).toFixed(0)})`);

const noBattery = B.valueBattery(hourly, Object.assign({}, base, { usableKwh: 0 }));
checks++; assert.strictEqual(noBattery.solarSek + noBattery.arbitrageSek, 0, 'no battery, no value');

const noSolar = B.valueBattery(hourly, Object.assign({}, base, { pvKwp: 0 }));
checks++; assert.strictEqual(Math.round(noSolar.solarSek), 0, 'no solar, no solar value');
checks++; assert.ok(noSolar.arbitrageSek > 0, 'but arbitrage on its own load remains');

const bigger = B.valueBattery(hourly, Object.assign({}, base, { usableKwh: 27 }));
checks++; assert.ok(bigger.solarSek + bigger.arbitrageSek >= v.solarSek + v.arbitrageSek, 'more storage is never worth less');
checks++; assert.ok(bigger.solarSek + bigger.arbitrageSek < 2 * (v.solarSek + v.arbitrageSek), 'and twice the storage is worth less than twice as much');

const reserved = B.valueBattery(hourly, Object.assign({}, base, { strategy: 'mixed' }));
checks++; assert.ok(reserved.solarSek + reserved.arbitrageSek < v.solarSek + v.arbitrageSek, 'reserving room for frequency response costs energy value');

for (const zone of ['SE1', 'SE4']) {
  const z = B.valueBattery(hourly, Object.assign({}, base, { zone }));
  checks++; assert.ok(z.solarSek + z.arbitrageSek > 0, zone);
}
const se1 = B.valueBattery(hourly, Object.assign({}, base, { zone: 'SE1' }));
checks++; assert.ok(se1.solarSek + se1.arbitrageSek < v.solarSek + v.arbitrageSek, 'SE1 is worth less than SE4');

// The bill can only fall, and never by more than the bill itself plus what export could earn.
checks++; assert.ok(v.solarSek + v.arbitrageSek < v.baselineBillSek + 8500 * 2, 'value is bounded by the bill');

// The quarters are the schedule's own and add up to the year.
near(v.quarters.reduce((s, q) => s + q.solarSek, 0), v.solarSek, 1, 'solar by quarter adds up');
near(v.quarters.reduce((s, q) => s + q.arbitrageSek, 0), v.arbitrageSek, 1, 'arbitrage by quarter adds up');
checks++; assert.ok(v.quarters[1].solarSek + v.quarters[2].solarSek > 3 * (v.quarters[0].solarSek + v.quarters[3].solarSek) / 2, 'stored sun is a spring and summer thing');

// Sun differs by zone, and it is the zone's own.
const yields = Object.fromEntries(['SE1', 'SE2', 'SE3', 'SE4'].map((z) => [z, B.yieldPerKwp(hourly, z)]));
checks++; assert.ok(yields.SE1 < yields.SE2 && yields.SE2 < yields.SE3 && yields.SE3 < yields.SE4, `yield rises southwards: ${JSON.stringify(yields)}`);
checks++; assert.ok(yields.SE1 > 650 && yields.SE4 < 1150, 'and stays within what Swedish installations report');
const winter = (zone) => {
  const s = B.seriesFor(hourly, zone);
  const start = new Date(hourly.from).getTime();
  let w = 0; let all = 0;
  s.pv.forEach((p, t) => { const m = new Date(start + t * 3600e3).getUTCMonth(); all += p; if (m === 11 || m === 0) w += p; });
  return w / all;
};
checks++; assert.ok(winter('SE1') < winter('SE4'), 'Luleå gets a smaller share of its sun in midwinter than Malmö');
const days = (zone) => {
  const s = B.seriesFor(hourly, zone);
  const sums = [];
  for (let d = 150; d < 240; d++) { let x = 0; for (let h = 0; h < 24; h++) x += s.pv[d * 24 + h] || 0; sums.push(x); }
  return Math.min(...sums) / Math.max(...sums);
};
checks++; assert.ok(days('SE3') < 0.5, 'summer days differ: there are overcast ones');

// A roof facing east and west yields less than one facing south, in every zone.
for (const z of ['SE1', 'SE2', 'SE3', 'SE4']) {
  const ratio = B.yieldPerKwp(hourly, z, 'eastwest') / B.yieldPerKwp(hourly, z, 'south');
  checks++; assert.ok(ratio > 0.65 && ratio < 0.9, `${z} east-west against south: ${ratio}`);
}

// The load: the annual figure is kept, heating puts more of it in winter, and
// more so in the north.
const winterShare = (zone, heatingShare) => {
  const s = B.seriesFor(hourly, zone);
  const load = B.composeLoad(s, hourly.constants, 8000, heatingShare);
  const start = new Date(hourly.from).getTime();
  let w = 0; let all = 0;
  load.forEach((v, t) => { const m = new Date(start + t * 3600e3).getUTCMonth(); all += v; if (m === 11 || m === 0 || m === 1) w += v; });
  near(all, 8000 * load.length / 8760, 0.5, `${zone} annual load is kept`);
  return w / all;
};
checks++; assert.ok(winterShare('SE1', null) > winterShare('SE4', null), 'the north heats more of its year');
checks++; assert.ok(winterShare('SE4', 0) < winterShare('SE4', null), 'without electric heating the load is flatter');
near(winterShare('SE4', 0), winterShare('SE1', 0), 0.002, 'and then it is the same in every zone');

// The extreme year is 2022 as it was, valued for the same system.
const ext = B.valueBattery(hourly, Object.assign({}, base, { extreme: true }));
checks++; assert.ok(ext && hourly.extreme.year === 2022, 'the extreme year is published');
checks++; assert.ok(ext.arbitrageSek > v.arbitrageSek, 'arbitrage was worth more in 2022 in SE4');
for (const zone of Object.keys(bess.zones)) {
  const offer = bess.zones[zone].offers[0];
  const py = bess.zones[zone].backtest.years['2022'].offers[offer.id].mixed;
  const js = B.valueBattery(hourly, {
    zone, loadKwh: 8000, pvKwp: hourly.constants.case_house_pv_kwp, usableKwh: offer.usable_kwh,
    powerKw: offer.battery_max_power_kw, roundTripEff: offer.round_trip_eff, strategy: 'mixed', extreme: true,
  });
  near(js.solarSek + js.arbitrageSek, py.solar_savings_sek + py.arbitrage_profit_sek, Math.max(30, 0.015 * (py.solar_savings_sek + py.arbitrage_profit_sek)), `${zone} extreme year against the Python back-test`);
}

// The weak year is 2020 as it was, and it is weaker than the last twelve months.
checks++; assert.ok(hourly.weak && hourly.weak.year === 2020, 'the weak year is published');
for (const zone of Object.keys(bess.zones)) {
  const offer = bess.zones[zone].offers[0];
  const py = bess.zones[zone].backtest.years['2020'].offers[offer.id].mixed;
  const o = {
    zone, loadKwh: 8000, pvKwp: hourly.constants.case_house_pv_kwp, usableKwh: offer.usable_kwh,
    powerKw: offer.battery_max_power_kw, roundTripEff: offer.round_trip_eff, strategy: 'mixed',
  };
  const weak = B.valueBattery(hourly, Object.assign({ period: 'weak' }, o));
  const now = B.valueBattery(hourly, o);
  near(weak.solarSek + weak.arbitrageSek, py.solar_savings_sek + py.arbitrage_profit_sek, Math.max(30, 0.015 * (py.solar_savings_sek + py.arbitrage_profit_sek)), `${zone} weak year against the Python back-test`);
  checks++; assert.ok(weak.solarSek + weak.arbitrageSek < now.solarSek + now.arbitrageSek, `${zone}: 2020 was worth less than the last year`);
}

console.log(`bess-dispatch: ${checks} checks passed`);
