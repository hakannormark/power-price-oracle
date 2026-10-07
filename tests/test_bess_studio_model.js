/**
 * Tests for site/assets/bess-studio-model.js against the published dispatch data.
 * Run: node tests/test_bess_studio_model.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const M = require('../site/assets/bess-studio-model.js');
const data = JSON.parse(fs.readFileSync(path.join(__dirname, '../site/data/bess-map/dispatch_backtest.json'), 'utf8'));

const ZONES = ['SE1', 'SE2', 'SE3', 'SE4'];
const DURS = [1, 1.5, 2, 3, 4];
const MWS = [5, 20, 50, 150, 300];
const SHARES = [0, 2, 5, 10, 30];
const FX = 11.25;
const EPS = 1e-6;
let checks = 0;
const ok = (cond, msg) => { checks++; assert.ok(cond, msg); };
const near = (a, b, msg, tol = 1e-6) => { checks++; assert.ok(Math.abs(a - b) <= tol * Math.max(1, Math.abs(a), Math.abs(b)), `${msg}: ${a} vs ${b}`); };

const dsets = M.datasets(data);
const baseInputs = (over = {}) => Object.assign({
  mw: 50, durationH: 2, capexPerKwh: 1800, capexPerKw: 1000, cableKm: 2, routingFactor: 1.3,
  cablePerKmMsek: 4.5, bayMsek: 35, extremeYears: 1,
}, M.DEFAULTS, over);

// ---------------------------------------------------------------- data file
ok(data.rho_grid[0] === 0, 'rho grid starts at 0 (arbitrage only)');
ok(data.full_years.includes(2022), '2022 is a full year in the data');
ok(data.periods.last12m.hours === 8760, 'last 12 months is exactly 8 760 hours');
ok(dsets.length >= 5 && dsets[0].id === 'last12m', 'datasets derived from the file');

// ------------------------------------------ extreme year: 2022 really is the extreme
ZONES.forEach((z) => {
  data.durations.forEach((d) => {
    const years = data.spot_years[z][String(d)];
    const best = Object.keys(years).reduce((a, b) => (years[a].rev >= years[b].rev ? a : b));
    ok(best === '2022', `${z} ${d}h: 2022 is the highest arbitrage year (found ${best})`);
    ok(years['2022'].rev > years.last12m.rev, `${z} ${d}h: 2022 arbitrage exceeds the last 12 months`);
  });
});

// ------------------------- extreme year never below a normal year, in any combination
let floorHits = 0;
let ceilingHits = 0;
ZONES.forEach((zone) => DURS.forEach((durationH) => MWS.forEach((mw) => SHARES.forEach((marketSharePct) => {
  dsets.forEach((dataset) => {
    const rev = M.siteRevenue(data, { zone, mw, durationH, dataset, marketSharePct, realizationPct: 75, fx: FX });
    const tag = `${zone} ${durationH}h ${mw}MW ${marketSharePct}% ${dataset.id}`;
    ok(rev.gross >= 0 && isFinite(rev.gross), `${tag}: gross is finite`);
    ok(rev.grossExtreme >= rev.gross - EPS, `${tag}: extreme year ${rev.grossExtreme} >= normal year ${rev.gross}`);
    near(rev.grossExtreme, rev.spotExtreme + rev.ancExtreme, `${tag}: extreme year is its own spot and ancillary parts`);
    ok(rev.reoptimised, `${tag}: the extreme year comes from its own optimisation`);
    if (!rev.extremeIsBase) {
      // Re-optimised against 2022's spreads the battery leans on arbitrage: more spot, no more reserves.
      ok(rev.spotExtreme >= rev.spot - EPS, `${tag}: extreme year has at least the normal year's spot revenue`);
      // With unchanged reserve prices the battery leans on arbitrage: no more reserves than a normal year.
      const flat = M.siteRevenue(data, { zone, mw, durationH, dataset, marketSharePct, realizationPct: 75, fx: FX, ancillaryExtremeMult: 1 });
      ok(flat.ancExtreme <= flat.anc + Math.max(1, 0.01 * flat.anc), `${tag}: unchanged prices give no more ancillary revenue than a normal year`);
      ok(flat.ancExtremeFactor === 1 || flat.extremeFloorApplied, `${tag}: and the factor says unchanged`);
      // The default follows the measured relation: upward reserves dearer, never by more than the cap.
      ok(rev.ancExtremeMeasured, `${tag}: the measured relation is in the file`);
      if (!rev.extremeFloorApplied && !flat.extremeFloorApplied) {
        ok(rev.ancExtremeFactor > 0.85 && rev.ancExtremeFactor < 3.0001, `${tag}: measured factor ${rev.ancExtremeFactor} is within range`);
        ok(rev.spotExtreme === flat.spotExtreme, `${tag}: the relation does not touch the spot part`);
      }
    }
    if (rev.extremeFloorApplied) floorHits++;
    // Negative extreme year: its own optimisation against 2020's spot prices.
    ok(rev.weakReoptimised, `${tag}: the negative year comes from its own optimisation`);
    near(rev.grossNegative, rev.spotNegative + rev.ancNegative, `${tag}: negative year is its own spot and ancillary parts`);
    if (rev.negativeCeilingApplied) ceilingHits++;
    ok(rev.grossNegative <= rev.gross + EPS && rev.gross <= rev.grossExtreme + EPS, `${tag}: negative <= normal <= extreme`);
    ok(rev.extremeRatio > 0 && isFinite(rev.extremeRatio), `${tag}: extreme ratio is a number`);
    ok(rev.extremeMeetsThreshold === (rev.extremeRatio >= M.POSITIVE_THRESHOLD), `${tag}: threshold flag`);
    {
      const f0 = M.simpleFinancials(baseInputs({ mw, durationH, extremeYears: 1, negativeYears: 0, marketSharePct }), rev);
      const f1 = M.simpleFinancials(baseInputs({ mw, durationH, extremeYears: 1, negativeYears: 1, marketSharePct }), rev);
      near(f1.tenYear, f0.tenYear + (f1.netNegative - f1.netNormal), `${tag}: a negative year replaces one normal year`);
      ok(f1.netNegative <= f1.netNormal + EPS, `${tag}: and it is never better than a normal year`);
      ok(f1.tenYear <= f0.tenYear + EPS, `${tag}: a negative year never raises the ten-year cash flow`);
      if (f1.payback !== null && f0.payback !== null) ok(f1.payback >= f0.payback - EPS, `${tag}: nor shortens the payback`);
      ok(f1.negativeYearsUsed === 1 && f0.negativeYearsUsed === 0, `${tag}: negative years used`);
    }
    if (dataset.id === 'spot2022') {
      ok(rev.extremeIsBase, `${tag}: 2022 dataset is flagged as already extreme`);
      near(rev.grossExtreme, rev.gross, `${tag}: extreme year equals the base when the base is 2022`);
    } else {
      ok(!rev.extremeIsBase, `${tag}: not flagged as extreme base`);
      if (rev.spot > 1) ok(rev.grossExtreme > rev.gross, `${tag}: extreme year strictly higher`);
    }

    // Simple model: more extreme years can only shorten the payback.
    const fins = [0, 1, 2].map((n) => M.simpleFinancials(baseInputs({ mw, durationH, extremeYears: n, marketSharePct }), rev));
    const nExt = (n) => (rev.extremeIsBase ? 0 : n);
    [0, 1, 2].forEach((n) => {
      const f = fins[n];
      near(f.tenYear, 10 * f.netNormal + nExt(n) * (f.netExtreme - f.netNormal), `${tag}: ten-year identity n=${n}`);
      ok(f.extremeYearsUsed === nExt(n), `${tag}: extreme years used n=${n}`);
    });
    near(fins[1].netExtreme - fins[1].netNormal, (rev.grossExtreme - rev.gross) * (1 - M.DEFAULTS.feePct / 100),
      `${tag}: extreme year adds its extra gross less the fee, nothing else`);
    {
      // A reserve-price multiplier above one can only add to the extreme year.
      const up = M.siteRevenue(data, { zone, mw, durationH, dataset, marketSharePct, realizationPct: 75, fx: FX, ancillaryExtremeMult: 2 });
      const one = M.siteRevenue(data, { zone, mw, durationH, dataset, marketSharePct, realizationPct: 75, fx: FX, ancillaryExtremeMult: 1 });
      ok(up.grossExtreme >= one.grossExtreme - EPS, `${tag}: higher reserve prices in the extreme year never lower it`);
      if (!rev.extremeIsBase) near(up.gross, rev.gross, `${tag}: and leave the normal year alone`);
      if (!rev.extremeIsBase && !up.extremeFloorApplied) near(up.ancExtreme, 2 * one.ancExtreme, `${tag}: the multiplier acts on the ancillary part`);
    }
    ok(fins[1].tenYear >= fins[0].tenYear - EPS && fins[2].tenYear >= fins[1].tenYear - EPS, `${tag}: ten-year cash flow rises with extreme years`);
    if (fins[0].payback !== null) {
      ok(fins[1].payback !== null && fins[1].payback <= fins[0].payback + EPS, `${tag}: payback 1 extreme <= 0 extreme`);
      ok(fins[2].payback <= fins[1].payback + EPS, `${tag}: payback 2 extreme <= 1 extreme`);
    }
    near(fins[0].payback === null ? 0 : fins[0].payback, fins[0].paybackNormal === null ? 0 : fins[0].paybackNormal, `${tag}: no extreme years = normal-year payback`);
  });
}))));
// Against the last twelve months 2020 is weaker for every plant; the ceiling is for other base periods.
dsets.filter((x) => x.id === 'last12m').forEach((dataset) => ZONES.forEach((zone) => [1, 2, 4].forEach((durationH) => [10, 50, 200].forEach((mw) => {
  const r = M.siteRevenue(data, { zone, mw, durationH, dataset, marketSharePct: 5, realizationPct: 75, fx: FX });
  ok(!r.negativeCeilingApplied && r.grossNegative < r.gross, `${zone} ${durationH}h ${mw}MW: 2020 is below the last twelve months`);
  ok(r.spotNegative < 0.6 * r.spot, `${zone} ${durationH}h ${mw}MW: and its arbitrage is well under the old 0.6 rule`);
}))));
ok(floorHits === 0, `the extreme-year floor never had to act (${floorHits} hits): 2022 is above every scenario`);

// The re-optimised extreme year is worth more than scaling the spot part was, where spot matters.
{
  const rev = M.siteRevenue(data, { zone: 'SE4', mw: 50, durationH: 2, dataset: dsets[0], marketSharePct: 5, realizationPct: 100, fx: 1 });
  const scaled = rev.anc + rev.spot * M.spotOnly(data, 'SE4', 2, '2022') / M.spotOnly(data, 'SE4', 2, 'last12m');
  ok(rev.grossExtreme > scaled, 'SE4: re-optimising beats scaling the spot part');
  ok(rev.actual2022Gross > 2 * rev.gross, 'SE4: 2022 as it was paid more than twice a normal year');
  const y2022 = M.siteRevenue(data, { zone: 'SE4', mw: 50, durationH: 2, dataset: dsets.find((x) => x.id === 'spot2022'), marketSharePct: 5, realizationPct: 100, fx: 1 });
  near(y2022.gross, rev.grossExtreme, 'the 2022 price data set is the extreme year itself');
}

// --------------------------------------------------------------- market depth
ZONES.forEach((zone) => DURS.forEach((durationH) => {
  const ds = dsets[0];
  let prevPerMw = Infinity;
  MWS.forEach((mw) => {
    const rev = M.siteRevenue(data, { zone, mw, durationH, dataset: ds, marketSharePct: 5, realizationPct: 100, fx: 1 });
    const perMw = rev.gross / mw;
    ok(perMw <= prevPerMw + 1e-3, `${zone} ${durationH}h: revenue per MW does not rise with size (${mw} MW)`);
    prevPerMw = perMw;
    Object.keys(rev.sold).forEach((p) => {
      const s = rev.sold[p];
      if (s.marketShare !== null) ok(s.marketShare <= 0.05 * 1.35, `${zone} ${durationH}h ${mw}MW ${p}: sold share ${s.marketShare.toFixed(3)} respects the cap`);
    });
  });
  let prev = -Infinity;
  SHARES.forEach((share) => {
    const rev = M.siteRevenue(data, { zone, mw: 50, durationH, dataset: ds, marketSharePct: share, realizationPct: 100, fx: 1 });
    ok(rev.gross >= prev - 1e-3, `${zone} ${durationH}h: gross does not fall when the market cap is raised (${share} %)`);
    prev = rev.gross;
  });
  const zero = M.siteRevenue(data, { zone, mw: 50, durationH, dataset: ds, marketSharePct: 0, realizationPct: 100, fx: 1 });
  near(zero.anc, 0, `${zone} ${durationH}h: no market share means no ancillary revenue`);
  near(zero.gross / 50, M.spotOnly(data, zone, durationH, 'last12m'), `${zone} ${durationH}h: and exactly the arbitrage-only result`, 1e-4);
}));

// Longer duration never earns less per MW.
ZONES.forEach((zone) => {
  let prev = -Infinity;
  [1, 2, 4].forEach((durationH) => {
    const rev = M.siteRevenue(data, { zone, mw: 50, durationH, dataset: dsets[0], marketSharePct: 5, realizationPct: 100, fx: 1 });
    ok(rev.gross >= prev, `${zone}: gross per MW does not fall with duration (${durationH} h)`);
    prev = rev.gross;
  });
});

// Realisation is a plain haircut.
{
  const a = M.siteRevenue(data, { zone: 'SE3', mw: 50, durationH: 2, dataset: dsets[0], marketSharePct: 5, realizationPct: 100, fx: FX });
  const b = M.siteRevenue(data, { zone: 'SE3', mw: 50, durationH: 2, dataset: dsets[0], marketSharePct: 5, realizationPct: 60, fx: FX });
  near(b.gross, 0.6 * a.gross, 'realisation scales gross');
  near(b.grossExtreme, 0.6 * a.grossExtreme, 'realisation scales the extreme year too');
}

// ------------------------------------------------------------ capex and opex
{
  const c = M.capex(baseInputs());
  near(c.energy, 100 * 1000 * 1800, 'energy capex');
  near(c.power, 50 * 1000 * 1000, 'power capex');
  near(c.cable, 2 * 1.3 * 4.5e6, 'cable capex');
  near(c.total, c.energy + c.power + c.cable + 35e6, 'total capex');
  const rev = { gross: 60e6, grossExtreme: 90e6, cycles: 300, extremeIsBase: false };
  const f = M.simpleFinancials(baseInputs({ extremeYears: 0 }), rev);
  near(f.opexNormal.fee, 3e6, 'fee');
  near(f.opexNormal.wear, 300 * 100 * 80, 'wear');
  near(f.netNormal, 60e6 - 3e6 - 50e3 * 45 - 50e3 * 40 - c.total * 0.005 - 2.4e6, 'net');
  const loss = M.simpleFinancials(baseInputs({ extremeYears: 0 }), { gross: 1e6, grossExtreme: 1e6, cycles: 300, extremeIsBase: false });
  ok(loss.netNormal < 0 && loss.payback === null, 'a loss is shown as a loss, with no payback');
}

// ------------------------------------------------------ investment calculation
near(M.npv(0.1, [-100, 110]), 0, 'npv');
near(M.irr([-100, 110]), 0.1, 'irr one period', 1e-5);
near(M.irr([-1000, 300, 300, 300, 300, 300]), 0.152382, 'irr annuity', 1e-4);
ok(M.irr([-100, -10, -10]) === null, 'irr undefined without a sign change');
assert.deepStrictEqual(M.defaultExtremeYearPositions(1, 15), [8]); checks++;
assert.deepStrictEqual(M.defaultExtremeYearPositions(2, 15), [5, 10]); checks++;
assert.deepStrictEqual(M.defaultExtremeYearPositions(0, 15), []); checks++;
assert.deepStrictEqual(M.defaultNegativeYearPositions(1, 15, []), [7]); checks++;
assert.deepStrictEqual(M.defaultNegativeYearPositions(1, 15, [7]), [8]); checks++;
assert.deepStrictEqual(M.defaultNegativeYearPositions(0, 15, [5, 10]), []); checks++;
ok(M.simpleFinancials(baseInputs({ extremeYears: 8, negativeYears: 5 }), { gross: 60e6, grossExtreme: 90e6, grossNegative: 50e6, cycles: 300, extremeIsBase: false }).negativeYearsUsed === 2, 'extreme and negative years never exceed ten together');

const inv = (over = {}) => M.investmentModel(Object.assign({
  capexTotal: 1000, batteryEnergyCapex: 600, spot: 100, spotExtreme: 300, anc: 100, fixedOpex: 20,
  extremeYearList: [],
}, M.DEFAULTS, {
  inflationPct: 0, degradationPct: 0, augmentYear: 0, ancTrendPct: 0, spotTrendPct: 0, taxPct: 0, feePct: 0,
  lifeYears: 10, waccPct: 8, gearingPct: 0, residualPct: 0,
}, over));

{
  const m = inv();
  ok(m.rows.length === 10 && m.fcff.length === 11, 'one row per year');
  m.rows.forEach((r) => near(r.fcff, 180, 'flat case: FCFF = EBITDA'));
  near(m.npv, -1000 + 180 * (1 - Math.pow(1.08, -10)) / 0.08, 'flat case NPV is an annuity');
  near(m.payback, 1000 / 180, 'flat case payback');
  ok(m.equityIrr === null && m.minDscr === null, 'no debt, no equity IRR or DSCR');

  const e1 = inv({ extremeYearList: [5] });
  near(e1.rows[4].revenue - m.rows[4].revenue, 200, 'extreme year replaces the spot part only');
  const e1anc = inv({ extremeYearList: [5], ancExtreme: 80 });
  near(e1anc.rows[4].revenue - m.rows[4].revenue, 180, 'and the ancillary part when the extreme year has its own');
  near(e1anc.rows[5].revenue, m.rows[5].revenue, 'only in that year');
  ok(e1.rows[4].extreme && !e1.rows[3].extreme, 'extreme flag on the right year');
  near(e1.npv - m.npv, 200 / Math.pow(1.08, 5), 'extreme year adds its discounted spot uplift');
  ok(inv({ extremeYearList: [2] }).npv > e1.npv, 'an earlier extreme year is worth more');
  ok(inv({ extremeYearList: [2, 7] }).npv > inv({ extremeYearList: [2] }).npv, 'two extreme years beat one');
  near(inv({ extremeYearList: [99, 0] }).npv, m.npv, 'extreme years outside the life are ignored');
  ok(inv({ spotExtreme: 100, extremeYearList: [5] }).npv === m.npv, 'an extreme year equal to a normal year changes nothing');

  const n1 = inv({ spotNegative: 60, negativeYearList: [7] });
  near(n1.rows[6].revenue - m.rows[6].revenue, -40, 'negative year lowers the spot part only');
  ok(n1.rows[6].negative && !n1.rows[6].extreme && !n1.rows[5].negative, 'negative flag on the right year');
  near(n1.npv - m.npv, -40 / Math.pow(1.08, 7), 'negative year removes its discounted spot shortfall');
  const n2 = inv({ spotNegative: 60, ancNegative: 80, negativeYearList: [7] });
  near(n2.rows[6].anc - n1.rows[6].anc, 80 - m.rows[6].anc, 'the negative year takes its own ancillary level');
  const both = inv({ spotNegative: 60, negativeYearList: [5], extremeYearList: [5] });
  ok(both.rows[4].extreme && !both.rows[4].negative, 'a year in both lists counts as a positive extreme year');
  near(both.npv, e1.npv, 'and is valued as one');
  near(inv({ negativeYearList: [7] }).npv, m.npv, 'without a negative spot level a negative year is a normal year');

  const deg = inv({ degradationPct: 2 });
  near(deg.rows[1].spot, 100 * 0.98, 'degradation hits spot fully');
  near(deg.rows[1].anc, 100 * (1 - 0.5 * 0.02), 'and ancillary by half');
  const aug = inv({ degradationPct: 2, augmentYear: 5, augmentPctOfBattery: 20 });
  // Five years at 2 % have cost 9.6 % of the capacity; that is what is bought, not the 20 % allowed.
  const lost = 1 - Math.pow(0.98, 5);
  near(aug.rows[4].augmentation, 600 * lost, 'augmentation buys back what was lost, no more');
  near(aug.rows[4].capacity, Math.pow(0.98, 4), 'the year of the purchase still runs on the worn battery');
  near(aug.rows[5].capacity, 1, 'capacity restored the year after');
  ok(aug.rows[6].capacity < 1, 'and degrades again');
  // A purchase smaller than the loss restores only that much.
  const small = inv({ degradationPct: 2, augmentYear: 5, augmentPctOfBattery: 5 });
  near(small.rows[4].augmentation, 600 * 0.05, 'a small purchase costs what it is');
  near(small.rows[5].capacity, Math.pow(0.98, 5) + 0.05, 'and brings back five points, not all of it');
  ok(small.rows[5].capacity < 1, 'so the battery is not as new');

  const trend = inv({ ancTrendPct: -5, inflationPct: 2 });
  near(trend.rows[2].anc, 100 * 0.95 * 0.95 * 1.02 * 1.02, 'ancillary trend and inflation compound');
  near(trend.rows[2].fixed, 20 * 1.02 * 1.02, 'fixed costs follow inflation');

  const taxed = inv({ taxPct: 20, depreciationYears: 10 });
  near(taxed.rows[0].tax, (180 - 100) * 0.2, 'tax on EBITDA less depreciation');
  const carry = inv({ taxPct: 20, depreciationYears: 2 });
  near(carry.rows[0].tax, 0, 'no tax in a loss year');
  near(carry.rows[2].tax, Math.max(0, 180 - 640) * 0.2, 'loss carried forward');
  near(carry.rows[5].tax, (180 * 6 - 1000) * 0.2, 'tax once the losses are used up');

  const lev = inv({ gearingPct: 60, interestPct: 6, tenorYears: 10 });
  near(lev.debt, 600, 'debt');
  near(lev.rows.reduce((s, r) => s + r.principal, 0), 600, 'principal repaid in full');
  near(lev.fcfe[0], -400, 'equity outlay');
  ok(lev.equityIrr > lev.irr, 'positive leverage raises the equity IRR');
  near(lev.minDscr, 180 / (600 * 0.06 / (1 - Math.pow(1.06, -10))), 'DSCR');
  near(lev.npv, m.npv, 'project NPV does not depend on financing');

  near(inv({ residualPct: 10 }).rows[9].fcff, 280, 'residual value in the last year');
  ok(inv({ spot: 5, anc: 5 }).irr === null || inv({ spot: 5, anc: 5 }).irr < 0, 'a losing project has no positive IRR');
}

// ----------------------------------------------------------------------- geo
{
  const stations = [[59.0, 18.0, 400, 'Stamnät', 'SvK'], [59.05, 18.0, 132, 'Region', 'X'], [59.01, 18.0, 45, 'Lokal', 'Y']];
  const big = M.nearestStation(59.0, 18.0, stations, M.stationClassFor(50));
  ok(big.name === 'Region', 'a 50 MW plant connects to the regional station, not the 400 kV one');
  near(big.km, 5.56, 'distance', 0.01);
  ok(M.nearestStation(59.0, 18.0, stations, M.stationClassFor(5)).name === 'Lokal', 'a 5 MW plant may use 45 kV');
  ok(M.nearestStation(59, 18, [[59, 18, 400, 'T', '']], 'regional') === null, 'no candidate, no station');
  const s = M.siteScore({ cableKm: 0, zoneGrossPerMw: 1, bestZoneGrossPerMw: 1, prot: 0, water: 0 });
  ok(s.score === 100, 'score tops out at 100');
  ok(M.siteScore({ cableKm: 30, zoneGrossPerMw: 0, bestZoneGrossPerMw: 1, prot: 1, water: 1 }).score === 0, 'and bottoms at 0');
}

console.log(`bess-studio-model: ${checks} checks passed`);
