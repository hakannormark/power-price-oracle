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

assert.strictEqual(hourly.hours, hourly.w_load.length);
assert.strictEqual(hourly.constants.levels, B.LEVELS, 'both sides use the same grid');

// Every published offer, both strategies, every zone: the browser must reproduce Python.
for (const zone of Object.keys(bess.zones)) {
  for (const offer of bess.zones[zone].offers) {
    for (const strategy of ['mixed', 'energy_only']) {
      const py = offer.annual_dispatch[strategy];
      const js = B.valueBattery(hourly, {
        zone, loadKwh: 8000, pvKwh: 7000, usableKwh: offer.usable_kwh,
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
const base = { zone: 'SE4', loadKwh: 8000, pvKwh: 8500, usableKwh: 13.5, powerKw: 10, roundTripEff: 0.9, strategy: 'energy_only' };
const v = B.valueBattery(hourly, base);
checks++; assert.ok(v.solarSek > 0 && v.arbitrageSek > 0, 'a battery with solar is worth something');
checks++; assert.ok(v.throughputKwh / 15 < 366, `at most about a cycle a day (${(v.throughputKwh / 15).toFixed(0)})`);

const noBattery = B.valueBattery(hourly, Object.assign({}, base, { usableKwh: 0 }));
checks++; assert.strictEqual(noBattery.solarSek + noBattery.arbitrageSek, 0, 'no battery, no value');

const noSolar = B.valueBattery(hourly, Object.assign({}, base, { pvKwh: 0 }));
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

console.log(`bess-dispatch: ${checks} checks passed`);
