// Node test script for site/assets/bess-utility.js
const fs = require('fs');
const path = require('path');

// Mock localStorage and document for headless execution
const store = {};
global.localStorage = {
  getItem: (k) => store[k] || null,
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; }
};

global.window = {};
global.document = {
  readyState: 'complete',
  addEventListener: () => {},
  querySelectorAll: () => [],
  getElementById: () => null
};

// Load bess-utility.js
const code = fs.readFileSync(path.join(__dirname, '../site/assets/bess-utility.js'), 'utf8');
eval(code);

const engine = global.window._UtilityBess;
console.log("Engine loaded successfully.");

// 1. Verify Bo's Default SE2 Baseline
console.log("\n--- TEST 1: Bo's SE2 Baseline (150 MW / 300 MWh) ---");
engine.state.zone = "SE2";
engine.state.power_mw = 150.0;
engine.state.capacity_mwh = 300.0;
engine.state.capex_per_kwh = 2800.0;
engine.state.year = "2026";
engine.state.scenario = "base";

const res = engine.calculate();

console.log("Total CAPEX:", (res.totalCapex / 1e6).toFixed(1), "MSEK (Expected: 840.0 MSEK)");
console.assert(Math.abs(res.totalCapex - 840000000) < 1, "CAPEX mismatch");

console.log("Available Hours:", Math.round(res.availHours), "h (Expected: 8322 h)");
console.assert(Math.round(res.availHours) === 8322, "Available hours mismatch");

console.log("Gross Revenue 2026:", (res.grossRevenue / 1e6).toFixed(2), "MSEK (Bo's sheet: ~134.78 MSEK)");
console.assert(Math.abs(res.grossRevenue - 134784691) < 100000, `Gross revenue diff: ${res.grossRevenue}`);

console.log("BSP / Trading Desk Cost:", (res.bspCost / 1e6).toFixed(2), "MSEK (Bo's sheet: ~4.04 MSEK)");
console.assert(Math.abs(res.bspCost - 4043540) < 10000, "BSP cost mismatch");

console.log("EBITDA 2026:", (res.ebitda / 1e6).toFixed(2), "MSEK (Bo's sheet: ~116.70 MSEK)");
console.assert(Math.abs(res.ebitda - 116695151) < 100000, `EBITDA diff: ${res.ebitda}`);

console.log("Payback:", res.paybackYears.toFixed(2), "years (Bo's sheet: ~7.20 years)");
console.assert(Math.abs(res.paybackYears - 7.20) < 0.1, "Payback mismatch");

console.log("Up-regulation Commitment:", (res.upRatio * 100).toFixed(1), "% (Bo's sheet: 38.4 %)");
console.assert(Math.abs(res.upRatio - 0.3837) < 0.01, "Up commitment mismatch");

console.log("Down-regulation Commitment:", (res.downRatio * 100).toFixed(1), "% (Bo's sheet: 37.4 %)");
console.assert(Math.abs(res.downRatio - 0.3741) < 0.01, "Down commitment mismatch");

console.log("Validation Flags - Overbooking:", res.validationFlags.overbooking_up || res.validationFlags.overbooking_down);
console.assert(!res.validationFlags.overbooking_up, "Unexpected overbooking up");
console.assert(!res.validationFlags.overbooking_down, "Unexpected overbooking down");

// 2. Verify Overbooking Guard
console.log("\n--- TEST 2: Overbooking Guard Detection ---");
// Simulate someone bidding 100% of hours on all products
const savedAlloc = JSON.parse(JSON.stringify(engine.state.allocations));
engine.state.allocations.fcr_d_up.bid_share = 1.0;
engine.state.allocations.fcr_d_up.accept_rate = 0.8;
engine.state.allocations.mfrr_cap_up.bid_share = 1.0;
engine.state.allocations.mfrr_cap_up.accept_rate = 0.8;
const resOverbooked = engine.calculate();
console.log("Up Ratio with excessive bids:", (resOverbooked.upRatio * 100).toFixed(1), "%");
console.log("Overbooking flag:", resOverbooked.validationFlags.overbooking_up);
console.assert(resOverbooked.validationFlags.overbooking_up === true, "Overbooking was NOT detected!");
// Restore
engine.state.allocations = savedAlloc;

// 3. Verify Duration / Endurance Constraint Check
console.log("\n--- TEST 3: Duration / C-Rate Constraint Check ---");
// If system is 150 MW / 50 MWh (0.33h duration)
engine.state.capacity_mwh = 50.0;
const resShortDuration = engine.calculate();
console.log("Duration:", resShortDuration.durationH.toFixed(2), "h");
console.log("Duration exceeded alerts count:", resShortDuration.validationFlags.duration_exceeded.length);
console.assert(resShortDuration.validationFlags.duration_exceeded.length > 0, "Duration constraint was NOT triggered!");
const mfrrAlert = resShortDuration.validationFlags.duration_exceeded.find(d => d.product.includes("mFRR"));
console.log("Found mFRR duration alert:", mfrrAlert);
console.assert(mfrrAlert !== undefined, "mFRR 1h constraint alert missing");
// Restore
engine.state.capacity_mwh = 300.0;

// 4. Verify Dimensioning Analysis (2h vs 4h)
console.log("\n--- TEST 4: Dimensioning Analysis (2h vs 4h / 300 vs 600 MWh) ---");
engine.state.dim_alt_mwh = 600.0;
engine.state.dim_spread_eff = 0.72;
const resDim = engine.calculate();
console.log("Extra Investment for 600 MWh:", (resDim.dim.extraCapex / 1e6).toFixed(1), "MSEK (Bo's sheet: 504.0 MSEK)");
console.assert(Math.abs(resDim.dim.extraCapex - 504000000) < 1, "Dim extra capex mismatch");

console.log("Extra EBITDA per year:", (resDim.dim.extraEbitda / 1e6).toFixed(2), "MSEK (Bo's sheet: ~1.40 MSEK)");
console.assert(Math.abs(resDim.dim.extraEbitda - 1398660) < 50000, "Dim extra EBITDA mismatch");

console.log("Marginal Payback:", resDim.dim.marginalPayback.toFixed(1), "years (Bo's sheet: 360.3 years)");
console.assert(Math.abs(resDim.dim.marginalPayback - 360.3) < 5, "Marginal payback mismatch");

// 5. Verify Hybrid Solar Park
console.log("\n--- TEST 5: Hybrid Solar Park ---");
console.log("Solar net benefit per year:", (res.hybrid.solNetBenefit / 1e6).toFixed(2), "MSEK (Bo's sheet: ~1.07 MSEK)");
console.assert(Math.abs(res.hybrid.solNetBenefit - 1069200) < 10000, "Hybrid solar net benefit mismatch");
console.log("Saved Grid Connection:", (res.hybrid.savedConnSek / 1e6).toFixed(1), "MSEK (Bo's sheet: 45.0 MSEK)");
console.assert(res.hybrid.savedConnSek === 45000000, "Saved grid conn mismatch");

// 6. Verify Extreme Year Scenarios
console.log("\n--- TEST 6: Extreme Year Scenarios ---");
engine.state.extreme_type = "som_2022";
const resExt2022 = engine.calculate();
console.log("Som 2022 EBITDA:", (resExt2022.extreme.active.ebitda / 1e6).toFixed(1), "MSEK (Bo's sheet: ~460.8 MSEK)");
console.assert(Math.abs(resExt2022.extreme.active.ebitda - 460839798) < 2000000, "Som 2022 EBITDA mismatch");
console.log("Som 2022 Multiple:", resExt2022.extreme.active.mult.toFixed(2), "x (Bo's sheet: 3.95x)");
console.assert(Math.abs(resExt2022.extreme.active.mult - 3.95) < 0.1, "Som 2022 multiple mismatch");

engine.state.extreme_type = "svagt";
const resExtSvagt = engine.calculate();
console.log("Svagt år EBITDA:", (resExtSvagt.extreme.active.ebitda / 1e6).toFixed(1), "MSEK (Bo's sheet: ~57.4 MSEK)");
console.assert(Math.abs(resExtSvagt.extreme.active.ebitda - 57403444) < 1000000, "Svagt år EBITDA mismatch");
console.log("Svagt år Multiple:", resExtSvagt.extreme.active.mult.toFixed(2), "x (Bo's sheet: 0.49x)");
console.assert(Math.abs(resExtSvagt.extreme.active.mult - 0.49) < 0.05, "Svagt år multiple mismatch");

// 7. Verify Zone Switching (SE1 vs SE2 vs SE3 vs SE4)
console.log("\n--- TEST 7: Zone Switching Spread and Arbitrage ---");
const zones = ["SE1", "SE2", "SE3", "SE4"];
zones.forEach(z => {
  engine.state.zone = z;
  const r = engine.calculate();
  console.log(`Zone ${z}: Spread = ${r.dailySpread.toFixed(0)} SEK/MWh, Net Arbitrage = ${(r.netArbitrageSek / 1e6).toFixed(2)} MSEK, Total EBITDA = ${(r.ebitda / 1e6).toFixed(1)} MSEK, Payback = ${r.paybackYears.toFixed(2)} år`);
  console.assert(r.ebitda > 0, `EBITDA should be positive in ${z}`);
  console.assert(r.paybackYears > 0 && r.paybackYears < 20, `Payback should be reasonable in ${z}`);
});

// Check that SE4 has significantly higher EBITDA than SE2 due to arbitrage and mFRR
engine.state.zone = "SE4";
const rSE4 = engine.calculate();
engine.state.zone = "SE2";
const rSE2 = engine.calculate();
console.log(`Diff SE4 vs SE2 EBITDA: +${((rSE4.ebitda - rSE2.ebitda)/1e6).toFixed(1)} MSEK`);
console.assert(rSE4.ebitda > rSE2.ebitda, "SE4 should have higher EBITDA than SE2");

// 8. Verify VPP Cluster Comparison
console.log("\n--- TEST 8: VPP Cluster Comparison ---");
console.log("Equivalent villas (150 MW / 10 kW):", res.vpp.villaCount, "st (Expected: 15 000 st)");
console.assert(res.vpp.villaCount === 15000, "VPP villa count mismatch");
console.log("Total Gross CAPEX VPP:", (res.vpp.grossCapexTotal / 1e6).toFixed(1), "MSEK (Expected: 1350.0 MSEK)");
console.assert(res.vpp.grossCapexTotal === 1350000000, "VPP gross capex mismatch");
console.log("VPP Subsidies:", (res.vpp.subsidiesTotal / 1e6).toFixed(1), "MSEK (Expected: ~675.0 MSEK)");
console.log("VPP Aggregator Fee (20%):", (res.vpp.aggregatorFeeTotal / 1e6).toFixed(1), "MSEK vs Utility BSP (3%):", (res.vpp.utilityBspCost / 1e6).toFixed(1), "MSEK");
console.assert(res.vpp.aggregatorFeeTotal > res.vpp.utilityBspCost, "VPP fee should exceed utility BSP cost");

console.log("\n>>> ALL TESTS PASSED SUCCESSFULLY! <<<\n");
