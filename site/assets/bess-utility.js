// Large-Scale Utility BESS Valuation Controller
// Based on Svenska kraftnät Balancing Market Outlook 2030 (2026 update), 
// empirical market outcomes 2019-2025, and ENTSO-E spot data.

(function () {
  "use strict";

  const STORAGE_KEY = "ppo_utility_bess_state_v1";

  // Built-in defaults in case JSON is not fetched immediately
  const DEFAULT_DATA = {
    fx_rate: 11.30,
    requirements: {
      fcr_d_up: { name: "FCR-D upp", req_2026: 542, req_2030: 542, endurance: 0.333 },
      fcr_d_down: { name: "FCR-D ned", req_2026: 524, req_2030: 524, endurance: 0.333 },
      fcr_n: { name: "FCR-N (symmetrisk)", req_2026: 226, req_2030: 226, endurance: 1.0 },
      afrr_up: { name: "aFRR upp", req_2026: 100, req_2030: 235, endurance: 1.0 },
      afrr_down: { name: "aFRR ned", req_2026: 125, req_2030: 235, endurance: 1.0 },
      mfrr_cap_up: { name: "mFRR kapacitet upp", req_2026: 1400, req_2030: 1400, endurance: 1.0 },
      mfrr_cap_down: { name: "mFRR kapacitet ned", req_2026: 1150, req_2030: 1150, endurance: 1.0 },
      ffr: { name: "FFR (maj–sep)", req_2026: 113, req_2030: 113, endurance: 0.0083 }
    },
    zones: {
      SE1: { name: "SE1 (Luleå / Norrbotten)", mean_spot: 188.8, spread_2h: 220, spread_4h: 190, peak_p: 320, trough_p: 100, mfrr_mult: 0.90 },
      SE2: { name: "SE2 (Sundsvall / Norra Mellansverige)", mean_spot: 186.8, spread_2h: 210, spread_4h: 151, peak_p: 300, trough_p: 90, mfrr_mult: 0.95 },
      SE3: { name: "SE3 (Stockholm / Södra Mellansverige)", mean_spot: 522.5, spread_2h: 500, spread_4h: 435, peak_p: 650, trough_p: 150, mfrr_mult: 1.15 },
      SE4: { name: "SE4 (Malmö / Sydsverige)", mean_spot: 682.8, spread_2h: 700, spread_4h: 609, peak_p: 850, trough_p: 150, mfrr_mult: 1.30 }
    },
    prices: {
      "2026": {
        fcr_d_up: { low: 4, base: 6, high: 10 },
        fcr_d_down: { low: 3, base: 6, high: 12 },
        fcr_n: { low: 18, base: 26, high: 38 },
        afrr_up: { low: 12, base: 18, high: 28 },
        afrr_down: { low: 13, base: 19, high: 30 },
        mfrr_cap_up: { low: 22, base: 33, high: 48 },
        mfrr_cap_down: { low: 18, base: 28, high: 42 },
        ffr: { low: 7, base: 11, high: 20 },
        mfrr_margin: { low: 100, base: 200, high: 400 },
        mfrr_mwh_mw: { low: 40, base: 80, high: 160 }
      },
      "2031": {
        fcr_d_up: { low: 4, base: 8, high: 16 },
        fcr_d_down: { low: 3, base: 7, high: 15 },
        fcr_n: { low: 12, base: 20, high: 34 },
        afrr_up: { low: 15, base: 24, high: 40 },
        afrr_down: { low: 16, base: 26, high: 42 },
        mfrr_cap_up: { low: 15, base: 25, high: 42 },
        mfrr_cap_down: { low: 13, base: 22, high: 38 },
        ffr: { low: 6, base: 12, high: 25 },
        mfrr_margin: { low: 150, base: 300, high: 600 },
        mfrr_mwh_mw: { low: 60, base: 120, high: 240 }
      }
    }
  };

  const state = {
    zone: "SE2",
    presetId: "bo_se2_150mw",
    year: "2026",
    scenario: "base", // low, base, high
    
    // Core Battery Specs
    power_mw: 150.0,
    capacity_mwh: 300.0,
    capex_per_kwh: 2800.0,
    power_capex_share: 0.40,
    om_sek_per_mw_year: 45000.0,
    fixed_fees_sek_per_mw_year: 40000.0,
    cycle_cost_sek_mwh: 80.0,
    bsp_share: 0.03,
    bsp_floor_sek: 3000000.0,
    roundtrip_eff: 0.88,
    tech_availability: 0.95,
    dod: 0.90,
    annual_degradation: 0.02,
    discount_rate: 0.07,
    arbitrage_cycles: 200,

    // Product Bids & Allocations
    allocations: {
      fcr_d_up: { bid_mw: 100.0, bid_share: 1.0, accept_rate: 0.42 },
      fcr_d_down: { bid_mw: 100.0, bid_share: 1.0, accept_rate: 0.42 },
      fcr_n: { bid_mw: 30.0, bid_share: 0.60, accept_rate: 0.24 },
      afrr_up: { bid_mw: 0.0, bid_share: 0.0, accept_rate: 0.0 },
      afrr_down: { bid_mw: 0.0, bid_share: 0.0, accept_rate: 0.0 },
      mfrr_cap_up: { bid_mw: 150.0, bid_share: 0.50, accept_rate: 0.17 },
      mfrr_cap_down: { bid_mw: 150.0, bid_share: 0.50, accept_rate: 0.17 },
      ffr: { bid_mw: 20.0, bid_share: 0.42, accept_rate: 0.18 }
    },

    // Arbitrage Prices Overrides (null means use zone defaults)
    peak_price_override: null,
    trough_price_override: null,

    // Deep-dive modules state
    dim_alt_mwh: 600.0,
    dim_spread_eff: 0.72,
    
    hybrid_solar_mw: 40.0,
    hybrid_solar_curtailment_share: 0.18,
    hybrid_battery_absorb_share: 0.55,
    hybrid_value_lift_sek: 350.0,
    hybrid_saved_conn_sek: 45000000.0,

    extreme_type: "som_2022", // svagt, bas, som_2022, som_2023
    extreme_count_of_10: 1,

    // VPP Comparison
    vpp_villa_kw: 10.0,
    vpp_villa_kwh: 20.0,
    vpp_gross_kwh_cost: 4500.0,
    vpp_aggregator_cut: 0.20,
    vpp_include_tax_deduction: true
  };

  let remoteData = DEFAULT_DATA;

  function fmtKr(n) {
    if (n === null || n === undefined || isNaN(n)) return "— kr";
    return Math.round(n).toLocaleString("sv-SE") + " kr";
  }

  function fmtMSEK(n) {
    if (n === null || n === undefined || isNaN(n)) return "— MSEK";
    return (n / 1000000).toFixed(1).replace(".", ",") + " MSEK";
  }

  function fmtPct(n) {
    if (n === null || n === undefined || isNaN(n)) return "— %";
    return (n * 100).toFixed(1).replace(".", ",") + " %";
  }

  function fmtYears(n) {
    if (n === null || n === undefined || isNaN(n) || !isFinite(n) || n <= 0) return "> 30 år";
    if (n > 30) return "> 30 år";
    return n.toFixed(1).replace(".", ",") + " år";
  }

  // Preset definitions
  const PRESET_MAP = {
    bo_se2_150mw: {
      zone: "SE2",
      power_mw: 150.0,
      capacity_mwh: 300.0,
      capex_per_kwh: 2800.0,
      power_capex_share: 0.40,
      om_sek_per_mw_year: 45000.0,
      fixed_fees_sek_per_mw_year: 40000.0,
      cycle_cost_sek_mwh: 80.0,
      bsp_share: 0.03,
      bsp_floor_sek: 3000000.0,
      roundtrip_eff: 0.88,
      tech_availability: 0.95,
      dod: 0.90,
      annual_degradation: 0.02,
      discount_rate: 0.07,
      arbitrage_cycles: 200,
      allocations: {
        fcr_d_up: { bid_mw: 100.0, bid_share: 1.0, accept_rate: 0.42 },
        fcr_d_down: { bid_mw: 100.0, bid_share: 1.0, accept_rate: 0.42 },
        fcr_n: { bid_mw: 30.0, bid_share: 0.60, accept_rate: 0.24 },
        afrr_up: { bid_mw: 0.0, bid_share: 0.0, accept_rate: 0.0 },
        afrr_down: { bid_mw: 0.0, bid_share: 0.0, accept_rate: 0.0 },
        mfrr_cap_up: { bid_mw: 150.0, bid_share: 0.50, accept_rate: 0.17 },
        mfrr_cap_down: { bid_mw: 150.0, bid_share: 0.50, accept_rate: 0.17 },
        ffr: { bid_mw: 20.0, bid_share: 0.42, accept_rate: 0.18 }
      }
    },
    regional_se3_20mw: {
      zone: "SE3",
      power_mw: 20.0,
      capacity_mwh: 40.0,
      capex_per_kwh: 3200.0,
      power_capex_share: 0.38,
      om_sek_per_mw_year: 50000.0,
      fixed_fees_sek_per_mw_year: 45000.0,
      cycle_cost_sek_mwh: 80.0,
      bsp_share: 0.04,
      bsp_floor_sek: 1000000.0,
      roundtrip_eff: 0.88,
      tech_availability: 0.95,
      dod: 0.90,
      annual_degradation: 0.02,
      discount_rate: 0.07,
      arbitrage_cycles: 250,
      allocations: {
        fcr_d_up: { bid_mw: 15.0, bid_share: 1.0, accept_rate: 0.45 },
        fcr_d_down: { bid_mw: 15.0, bid_share: 1.0, accept_rate: 0.45 },
        fcr_n: { bid_mw: 10.0, bid_share: 0.50, accept_rate: 0.25 },
        afrr_up: { bid_mw: 5.0, bid_share: 0.30, accept_rate: 0.25 },
        afrr_down: { bid_mw: 5.0, bid_share: 0.30, accept_rate: 0.25 },
        mfrr_cap_up: { bid_mw: 20.0, bid_share: 0.50, accept_rate: 0.22 },
        mfrr_cap_down: { bid_mw: 20.0, bid_share: 0.50, accept_rate: 0.22 },
        ffr: { bid_mw: 5.0, bid_share: 0.42, accept_rate: 0.20 }
      }
    },
    industrial_se4_10mw: {
      zone: "SE4",
      power_mw: 10.0,
      capacity_mwh: 20.0,
      capex_per_kwh: 3400.0,
      power_capex_share: 0.35,
      om_sek_per_mw_year: 55000.0,
      fixed_fees_sek_per_mw_year: 50000.0,
      cycle_cost_sek_mwh: 80.0,
      bsp_share: 0.05,
      bsp_floor_sek: 600000.0,
      roundtrip_eff: 0.88,
      tech_availability: 0.95,
      dod: 0.90,
      annual_degradation: 0.02,
      discount_rate: 0.07,
      arbitrage_cycles: 280,
      allocations: {
        fcr_d_up: { bid_mw: 8.0, bid_share: 0.90, accept_rate: 0.50 },
        fcr_d_down: { bid_mw: 8.0, bid_share: 0.90, accept_rate: 0.50 },
        fcr_n: { bid_mw: 5.0, bid_share: 0.40, accept_rate: 0.25 },
        afrr_up: { bid_mw: 5.0, bid_share: 0.40, accept_rate: 0.30 },
        afrr_down: { bid_mw: 5.0, bid_share: 0.40, accept_rate: 0.30 },
        mfrr_cap_up: { bid_mw: 10.0, bid_share: 0.60, accept_rate: 0.25 },
        mfrr_cap_down: { bid_mw: 10.0, bid_share: 0.60, accept_rate: 0.25 },
        ffr: { bid_mw: 3.0, bid_share: 0.42, accept_rate: 0.22 }
      }
    },
    long_duration_50mw_4h: {
      zone: "SE3",
      power_mw: 50.0,
      capacity_mwh: 200.0,
      capex_per_kwh: 2240.0,
      power_capex_share: 0.25,
      om_sek_per_mw_year: 40000.0,
      fixed_fees_sek_per_mw_year: 35000.0,
      cycle_cost_sek_mwh: 80.0,
      bsp_share: 0.03,
      bsp_floor_sek: 2000000.0,
      roundtrip_eff: 0.88,
      tech_availability: 0.95,
      dod: 0.90,
      annual_degradation: 0.02,
      discount_rate: 0.07,
      arbitrage_cycles: 260,
      allocations: {
        fcr_d_up: { bid_mw: 40.0, bid_share: 1.0, accept_rate: 0.42 },
        fcr_d_down: { bid_mw: 40.0, bid_share: 1.0, accept_rate: 0.42 },
        fcr_n: { bid_mw: 20.0, bid_share: 0.60, accept_rate: 0.24 },
        afrr_up: { bid_mw: 15.0, bid_share: 0.50, accept_rate: 0.25 },
        afrr_down: { bid_mw: 15.0, bid_share: 0.50, accept_rate: 0.25 },
        mfrr_cap_up: { bid_mw: 50.0, bid_share: 0.50, accept_rate: 0.20 },
        mfrr_cap_down: { bid_mw: 50.0, bid_share: 0.50, accept_rate: 0.20 },
        ffr: { bid_mw: 10.0, bid_share: 0.42, accept_rate: 0.18 }
      }
    }
  };

  function loadState() {
    try {
      const ppoZone = localStorage.getItem("ppo.zone");
      if (ppoZone && ["SE1", "SE2", "SE3", "SE4"].includes(ppoZone)) {
        state.zone = ppoZone;
      }
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const saved = JSON.parse(raw);
        Object.assign(state, saved);
      }
    } catch (e) {
      console.warn("Could not load utility BESS state:", e);
    }
  }

  function saveState() {
    try {
      localStorage.setItem("ppo.zone", state.zone);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) {
      console.warn("Could not save utility BESS state:", e);
    }
  }

  // Financial PV helper
  function pvAnnuity(cashflow, rate, years) {
    if (rate === 0) return cashflow * years;
    return cashflow * (1 - Math.pow(1 + rate, -years)) / rate;
  }

  // Main Calculation Engine
  function calculate() {
    const fx = remoteData.fx_rate || 11.30;
    const zInfo = (remoteData.zones && remoteData.zones[state.zone]) || DEFAULT_DATA.zones[state.zone];
    const pricesYear = (remoteData.prices && remoteData.prices[state.year]) || DEFAULT_DATA.prices[state.year];
    const reqs = remoteData.requirements || DEFAULT_DATA.requirements;

    // 1. Technical Availability & Capacity
    const availHours = 8760 * state.tech_availability;
    const durationH = state.capacity_mwh > 0 && state.power_mw > 0 ? (state.capacity_mwh / state.power_mw) : 0;
    const usableMwhBase = state.capacity_mwh * state.dod;
    const degFactor5y = Math.pow(1 - state.annual_degradation, 5);
    const usableMwh = state.year === "2031" ? (usableMwhBase * degFactor5y) : usableMwhBase;
    const effectivePowerMw = state.year === "2031" ? (state.power_mw * degFactor5y) : state.power_mw;

    // 2. Investment & CAPEX split
    const totalCapex = state.capex_per_kwh * state.capacity_mwh * 1000;
    const powerCapex = totalCapex * state.power_capex_share;
    const energyCapex = totalCapex * (1 - state.power_capex_share);
    const energyCostPerKwh = (state.capacity_mwh > 0) ? (energyCapex / (state.capacity_mwh * 1000)) : 0;

    // 3. Products Validation & Allocation Calculations
    const productResults = {};
    let upCommitmentHours = 0;
    let downCommitmentHours = 0;
    let totalCapRevenueSek = 0;
    const validationFlags = {
      overbooking_up: false,
      overbooking_down: false,
      duration_exceeded: [],
      market_impact_high: []
    };

    const productKeys = ["fcr_d_up", "fcr_d_down", "fcr_n", "afrr_up", "afrr_down", "mfrr_cap_up", "mfrr_cap_down", "ffr"];
    
    productKeys.forEach(key => {
      const alloc = state.allocations[key] || { bid_mw: 0, bid_share: 0, accept_rate: 0 };
      const req = reqs[key] || { name: key, req_2026: 500, endurance: 1.0 };
      const svkReq = state.year === "2031" ? (req.req_2030 || req.req_2026) : req.req_2026;
      
      // Scaling for 2031 degradation if desired
      let bidMw = alloc.bid_mw;
      if (state.year === "2031" && bidMw > effectivePowerMw) {
        bidMw = effectivePowerMw;
      }

      // Duration & Energy Constraint Check
      const maxBidMwEnergy = (req.endurance > 0) ? (usableMwh / req.endurance) : state.power_mw;
      const isDurationExceeded = bidMw > maxBidMwEnergy;
      if (isDurationExceeded) {
        validationFlags.duration_exceeded.push({
          product: req.name,
          bid_mw: bidMw,
          max_allowed_mw: Math.round(maxBidMwEnergy),
          endurance_h: req.endurance
        });
      }

      // Market Share Check
      const marketShare = svkReq > 0 ? (bidMw / svkReq) : 0;
      let marketStatus = "ok"; // ok (<10%), notice (10-20%), price_maker (>20%)
      if (marketShare > 0.20) {
        marketStatus = "price_maker";
        validationFlags.market_impact_high.push({
          product: req.name,
          share: marketShare,
          svkReq: svkReq
        });
      } else if (marketShare > 0.10) {
        marketStatus = "notice";
      }

      // Hours won (based on technical availability, bid share and acceptance)
      const hoursWon = availHours * alloc.bid_share * alloc.accept_rate;

      // Price & Revenue (harmonic with Bo's sheet which applies tech availability)
      const pEntry = pricesYear[key] || { low: 5, base: 10, high: 15 };
      let unitPriceEur = pEntry[state.scenario] || pEntry.base || 10;
      
      // Local zone multiplier for mFRR if applicable
      if (key.startsWith("mfrr") && state.zone !== "SE2") {
        unitPriceEur *= (zInfo.mfrr_mult || 1.0);
      }

      const unitPriceSek = unitPriceEur * fx;
      // Bo's model applies technical availability factor (0.95) to capacity settlements
      const revenueSek = bidMw * hoursWon * unitPriceSek * state.tech_availability;
      totalCapRevenueSek += revenueSek;

      // Track commitments
      if (key === "fcr_d_up" || key === "afrr_up" || key === "mfrr_cap_up" || key === "ffr") {
        upCommitmentHours += (bidMw * hoursWon);
      } else if (key === "fcr_d_down" || key === "afrr_down" || key === "mfrr_cap_down") {
        downCommitmentHours += (bidMw * hoursWon);
      } else if (key === "fcr_n") {
        // Symmetric - binds both directions
        upCommitmentHours += (bidMw * hoursWon);
        downCommitmentHours += (bidMw * hoursWon);
      }

      productResults[key] = {
        name: req.name,
        bid_mw: bidMw,
        hours_won: hoursWon,
        price_eur: unitPriceEur,
        price_sek: unitPriceSek,
        revenue_sek: revenueSek,
        market_share: marketShare,
        market_status: marketStatus,
        max_bid_mw: maxBidMwEnergy,
        is_duration_exceeded: isDurationExceeded
      };
    });

    // Check capacity overbooking (denominator is total calendar MW*hours 8760*MW)
    const totalCalendarCapacity = state.power_mw * 8760;
    const upRatio = totalCalendarCapacity > 0 ? (upCommitmentHours / totalCalendarCapacity) : 0;
    const downRatio = totalCalendarCapacity > 0 ? (downCommitmentHours / totalCalendarCapacity) : 0;
    if (upRatio > 1.001) validationFlags.overbooking_up = true;
    if (downRatio > 1.001) validationFlags.overbooking_down = true;

    // 4. mFRR Energy Activation (Marginal over spot via MARI)
    const mfrrBiddedMw = state.allocations.mfrr_cap_up.bid_mw;
    const mfrrMarginSek = pricesYear.mfrr_margin[state.scenario] || 200;
    const mfrrMwhPerMw = pricesYear.mfrr_mwh_mw[state.scenario] || 80;
    const mfrrEnergyRevSek = mfrrBiddedMw * mfrrMwhPerMw * mfrrMarginSek;

    // 5. Day-ahead Spot Arbitrage
    const peakPrice = state.peak_price_override !== null ? state.peak_price_override : (zInfo.peak_p || 300);
    const troughPrice = state.trough_price_override !== null ? state.trough_price_override : (zInfo.trough_p || 90);
    const dailySpread = peakPrice - troughPrice;
    
    const energySoldPerCycle = usableMwh;
    const totalSoldMwhYear = energySoldPerCycle * state.arbitrage_cycles;
    const totalBoughtMwhYear = (state.roundtrip_eff > 0) ? (totalSoldMwhYear / state.roundtrip_eff) : totalSoldMwhYear;

    const grossArbitrageRevenue = totalSoldMwhYear * peakPrice;
    const arbitrageCostOfCharging = totalBoughtMwhYear * troughPrice;
    const arbitrageCycleWear = totalSoldMwhYear * state.cycle_cost_sek_mwh;
    const netArbitrageSek = grossArbitrageRevenue - arbitrageCostOfCharging - arbitrageCycleWear;

    // Arbitrage viability check (break-even spread)
    const breakEvenSpread = (state.cycle_cost_sek_mwh / state.roundtrip_eff) + troughPrice * ((1 / state.roundtrip_eff) - 1);
    const isArbitrageUnprofitable = dailySpread < breakEvenSpread;

    // 6. Total Stack, Costs & Financial Returns
    const grossRevenue = totalCapRevenueSek + mfrrEnergyRevSek + Math.max(0, netArbitrageSek);
    const bspCost = Math.max(grossRevenue * state.bsp_share, state.bsp_floor_sek);
    const netRevenueOwner = grossRevenue - bspCost;

    const omCost = state.power_mw * state.om_sek_per_mw_year;
    const fixedFeesCost = state.power_mw * state.fixed_fees_sek_per_mw_year;
    
    // Cycle wear from reserve delivery (60 cycles in 2026, 80 cycles in 2031 from FCR-N/mFRR)
    const reserveEquivCycles = state.year === "2031" ? 80 : 60;
    const reserveWearCost = reserveEquivCycles * usableMwh * state.cycle_cost_sek_mwh;

    const totalOpCosts = bspCost + omCost + fixedFeesCost + reserveWearCost;
    const ebitda = grossRevenue - totalOpCosts;
    const paybackYears = ebitda > 0 ? (totalCapex / ebitda) : Infinity;

    const netPerMw = state.power_mw > 0 ? (netRevenueOwner / state.power_mw) : 0;
    const ebitdaPerMw = state.power_mw > 0 ? (ebitda / state.power_mw) : 0;
    const netPerMwh = state.capacity_mwh > 0 ? (netRevenueOwner / state.capacity_mwh) : 0;

    const npv10y = pvAnnuity(ebitda, state.discount_rate, 10);
    const netNpv = npv10y - totalCapex;

    // 7. Dimensioning Deep-dive (2h vs 4h)
    const dimAltMwh = state.dim_alt_mwh || (state.capacity_mwh * 2);
    const dimAltDuration = state.power_mw > 0 ? (dimAltMwh / state.power_mw) : 4;
    const dimAltCapexTotal = powerCapex + (energyCostPerKwh * dimAltMwh * 1000);
    const dimExtraCapex = dimAltCapexTotal - totalCapex;

    // Extra energy only benefits mFRR energy & arbitrage (reserves are power-capped at 1-2h)
    const dimAltMfrrEnergyRev = mfrrEnergyRevSek * (dimAltMwh / state.capacity_mwh);
    
    // In 4h arbitrage, spread compresses (e.g. 156 kr vs 210 kr in SE2)
    const dimAltSoldMwh = (dimAltMwh * state.dod) * state.arbitrage_cycles;
    const dimAltBoughtMwh = (state.roundtrip_eff > 0) ? (dimAltSoldMwh / state.roundtrip_eff) : dimAltSoldMwh;
    // Spread efficiency compresses peak selling price and increases trough buying price
    const pBuy4h = troughPrice * 1.1111111; // 90 -> 100 in SE2
    const spread4h = dailySpread * (state.dim_spread_eff ? (state.dim_spread_eff * (156 / (210 * 0.72))) : 0.742857);
    const pSell4h = pBuy4h + spread4h;
    const dimAltArbitrageGross = (dimAltSoldMwh * pSell4h) - (dimAltBoughtMwh * pBuy4h) - (dimAltSoldMwh * state.cycle_cost_sek_mwh);
    const dimAltArbitrageNet = Math.max(0, dimAltArbitrageGross);

    const dimAltGrossRevenue = totalCapRevenueSek + dimAltMfrrEnergyRev + dimAltArbitrageNet;
    const dimAltBsp = Math.max(dimAltGrossRevenue * state.bsp_share, state.bsp_floor_sek);
    const dimAltReserveWear = reserveWearCost * (dimAltMwh / state.capacity_mwh);
    const dimAltEbitda = dimAltGrossRevenue - (dimAltBsp + omCost + fixedFeesCost + dimAltReserveWear);
    
    const dimExtraEbitda = dimAltEbitda - ebitda;
    const dimMarginalPayback = dimExtraEbitda > 0 ? (dimExtraCapex / dimExtraEbitda) : Infinity;
    const dimRequiredCellCostForParity = (ebitda > 0 && totalCapex > 0 && (dimAltMwh - state.capacity_mwh) > 0)
      ? (dimExtraEbitda * (totalCapex / ebitda)) / ((dimAltMwh - state.capacity_mwh) * 1000)
      : 0;

    // 8. Hybrid Solar Deep-dive
    const solMw = state.hybrid_solar_mw;
    const solAnnualMwh = solMw * 1000;
    const solCurtailmentMwh = solAnnualMwh * state.hybrid_solar_curtailment_share;
    const solMovedMwh = solCurtailmentMwh * state.hybrid_battery_absorb_share;
    const solRevenueLift = solMovedMwh * state.hybrid_value_lift_sek;
    const solAddedWear = solMovedMwh * state.cycle_cost_sek_mwh;
    const solNetBenefit = solRevenueLift - solAddedWear;

    // 9. Extreme Years Deep-dive
    const opFixedCosts = omCost + fixedFeesCost + reserveWearCost; // 14 046 000 in baseline
    const extremeOutcomes = {
      svagt: {
        label: "Svakt år (negativt extremår)",
        cap_sek: totalCapRevenueSek * 0.55,
        mfrr_energy_sek: mfrrEnergyRevSek * 0.55,
        arbitrage_sek: netArbitrageSek * 0.60,
      },
      bas: {
        label: "Basår (normalmarknad)",
        cap_sek: totalCapRevenueSek,
        mfrr_energy_sek: mfrrEnergyRevSek,
        arbitrage_sek: netArbitrageSek,
      },
      som_2022: {
        label: "Som 2022 (gaskris & kärnkraftsstopp)",
        cap_sek: 466979033.36 * (state.power_mw / 150),
        mfrr_energy_sek: 3840000 * (state.power_mw / 150),
        arbitrage_sek: 18753954.55 * (state.capacity_mwh / 300),
      },
      som_2023: {
        label: "Som 2023 (FCR-D upprampning)",
        cap_sek: 507474059.43 * (state.power_mw / 150),
        mfrr_energy_sek: 3360000 * (state.power_mw / 150),
        arbitrage_sek: 12396681.82 * (state.capacity_mwh / 300),
      }
    };
    
    // Calculate EBITDA for selected extreme year
    const activeExtreme = extremeOutcomes[state.extreme_type] || extremeOutcomes.bas;
    activeExtreme.gross_sek = activeExtreme.cap_sek + activeExtreme.mfrr_energy_sek + Math.max(0, activeExtreme.arbitrage_sek);
    const extBsp = Math.max(activeExtreme.gross_sek * state.bsp_share, state.bsp_floor_sek);
    activeExtreme.ebitda = activeExtreme.gross_sek - (extBsp + opFixedCosts);
    activeExtreme.mult = (ebitda > 0) ? (activeExtreme.ebitda / ebitda) : 1;
    activeExtreme.payback = activeExtreme.ebitda > 0 ? (totalCapex / activeExtreme.ebitda) : Infinity;

    // 10-year cashflows with 0, 1, 2 extreme years
    const cashflow10y_0ext = ebitda * 10;
    const cashflow10y_1ext = (ebitda * 9) + activeExtreme.ebitda;
    const cashflow10y_2ext = (ebitda * 8) + (activeExtreme.ebitda * 2);

    // 10. Head-to-Head: Utility BESS vs Home Battery Cluster (VPP)
    const vppVillaCount = Math.round((state.power_mw * 1000) / state.vpp_villa_kw); // e.g. 15 000 villas
    const vppGrossCapexPerVilla = state.vpp_villa_kwh * state.vpp_gross_kwh_cost; // e.g. 90 000 kr
    const vppTotalGrossCapex = vppVillaCount * vppGrossCapexPerVilla; // e.g. 1 350 MSEK
    
    // Green Tech deduction (50% up to 100 000 kr per 2-owner household)
    const vppDeductionPerVilla = state.vpp_include_tax_deduction 
      ? Math.min(vppGrossCapexPerVilla * 0.485, 100000)
      : 0;
    const vppTotalSubsidies = vppVillaCount * vppDeductionPerVilla;
    const vppNetCapexHouseholds = vppTotalGrossCapex - vppTotalSubsidies;

    // VPP Aggregator cut vs Utility BSP desk
    const vppAggregatorFee = grossRevenue * state.vpp_aggregator_cut;
    const vppNetRevenuesToHomes = grossRevenue - vppAggregatorFee;
    const utilityNetRevenuesToOwner = grossRevenue - bspCost;

    return {
      availHours,
      durationH,
      usableMwh,
      totalCapex,
      powerCapex,
      energyCapex,
      energyCostPerKwh,
      productResults,
      upRatio,
      downRatio,
      totalCapRevenueSek,
      mfrrEnergyRevSek,
      peakPrice,
      troughPrice,
      dailySpread,
      breakEvenSpread,
      isArbitrageUnprofitable,
      netArbitrageSek,
      grossRevenue,
      bspCost,
      netRevenueOwner,
      omCost,
      fixedFeesCost,
      reserveWearCost,
      totalOpCosts,
      ebitda,
      paybackYears,
      netPerMw,
      ebitdaPerMw,
      netPerMwh,
      npv10y,
      netNpv,
      validationFlags,
      dim: {
        altMwh: dimAltMwh,
        altDuration: dimAltDuration,
        altCapexTotal: dimAltCapexTotal,
        extraCapex: dimExtraCapex,
        altEbitda: dimAltEbitda,
        extraEbitda: dimExtraEbitda,
        marginalPayback: dimMarginalPayback,
        requiredCellCost: dimRequiredCellCostForParity
      },
      hybrid: {
        solMw,
        solAnnualMwh,
        solMovedMwh,
        solRevenueLift,
        solAddedWear,
        solNetBenefit,
        savedConnSek: state.hybrid_saved_conn_sek
      },
      extreme: {
        active: activeExtreme,
        cashflow0: cashflow10y_0ext,
        cashflow1: cashflow10y_1ext,
        cashflow2: cashflow10y_2ext
      },
      vpp: {
        villaCount: vppVillaCount,
        grossCapexTotal: vppTotalGrossCapex,
        subsidiesTotal: vppTotalSubsidies,
        netCapexTotal: vppNetCapexHouseholds,
        aggregatorFeeTotal: vppAggregatorFee,
        netHomesTotal: vppNetRevenuesToHomes,
        utilityBspCost: bspCost,
        utilityNetOwner: utilityNetRevenuesToOwner
      }
    };
  }

  // DOM Rendering
  function render() {
    const res = calculate();

    // 1. Zone Pills
    document.querySelectorAll(".bess-zone-btn").forEach(btn => {
      const z = btn.getAttribute("data-zone");
      btn.classList.toggle("active", z === state.zone);
    });

    // Zone summary info
    const zInfoEl = document.getElementById("bess-zone-info-text");
    if (zInfoEl) {
      const zData = (remoteData.zones && remoteData.zones[state.zone]) || DEFAULT_DATA.zones[state.zone];
      zInfoEl.textContent = `${zData.name}: Årsmedel spot ${zData.mean_spot} SEK/MWh. Typisk dygnsspread 2h är ca ${zData.spread_2h} SEK/MWh. ${zData.description}`;
    }

    // 2. Preset Select
    const presetSelect = document.getElementById("bess-preset-select");
    if (presetSelect && presetSelect.value !== state.presetId) {
      presetSelect.value = state.presetId;
    }

    // 3. Inputs & Sliders
    setVal("input-power-mw", state.power_mw);
    setVal("slider-power-mw", state.power_mw);
    setText("val-power-mw", state.power_mw + " MW");

    setVal("input-cap-mwh", state.capacity_mwh);
    setVal("slider-cap-mwh", state.capacity_mwh);
    setText("val-cap-mwh", state.capacity_mwh + " MWh");

    setText("val-duration-h", res.durationH.toFixed(1) + " h");

    setVal("input-capex-kwh", state.capex_per_kwh);
    setVal("slider-capex-kwh", state.capex_per_kwh);
    setText("val-capex-kwh", state.capex_per_kwh.toLocaleString("sv-SE") + " kr/kWh");

    setVal("input-power-capex-share", state.power_capex_share * 100);
    setVal("slider-power-capex-share", state.power_capex_share * 100);
    setText("val-power-capex-share", (state.power_capex_share * 100).toFixed(0) + " %");

    setVal("input-om-mw", state.om_sek_per_mw_year);
    setVal("input-fixed-mw", state.fixed_fees_sek_per_mw_year);
    setVal("input-cycle-cost", state.cycle_cost_sek_mwh);
    setVal("input-bsp-share", state.bsp_share * 100);
    setVal("input-bsp-floor", state.bsp_floor_sek / 1000000);
    setVal("input-avail", state.tech_availability * 100);
    setVal("input-roundtrip", state.roundtrip_eff * 100);
    setVal("input-discount-rate", state.discount_rate * 100);
    setVal("input-arbitrage-cycles", state.arbitrage_cycles);

    // Scenario buttons
    document.querySelectorAll(".bess-scenario-btn").forEach(btn => {
      btn.classList.toggle("active", btn.getAttribute("data-scenario") === state.scenario);
    });
    document.querySelectorAll(".bess-year-btn").forEach(btn => {
      btn.classList.toggle("active", btn.getAttribute("data-year") === state.year);
    });

    // 4. Overbooking Gauges & Status
    const upGauge = document.getElementById("gauge-up-commitment");
    const upPctText = document.getElementById("text-up-commitment");
    if (upGauge && upPctText) {
      const pct = Math.min(100, res.upRatio * 100);
      upGauge.style.width = pct + "%";
      upGauge.className = res.validationFlags.overbooking_up ? "gauge-bar danger" : (pct > 85 ? "gauge-bar warn" : "gauge-bar success");
      upPctText.textContent = (res.upRatio * 100).toFixed(1) + " %";
    }

    const downGauge = document.getElementById("gauge-down-commitment");
    const downPctText = document.getElementById("text-down-commitment");
    if (downGauge && downPctText) {
      const pct = Math.min(100, res.downRatio * 100);
      downGauge.style.width = pct + "%";
      downGauge.className = res.validationFlags.overbooking_down ? "gauge-bar danger" : (pct > 85 ? "gauge-bar warn" : "gauge-bar success");
      downPctText.textContent = (res.downRatio * 100).toFixed(1) + " %";
    }

    // Validation Alert Box
    const alertBox = document.getElementById("bess-validation-alerts");
    if (alertBox) {
      let alertsHtml = "";
      if (res.validationFlags.overbooking_up || res.validationFlags.overbooking_down) {
        alertsHtml += `<div class="bess-alert danger">
          <strong>⛔ OTILLÅTEN FYSISK ÖVERBOKNING:</strong> Du säljer samma effekt flera gånger under året! 
          ${res.validationFlags.overbooking_up ? `Uppreglering är bokad till ${(res.upRatio * 100).toFixed(0)} % (>100 %). ` : ""}
          ${res.validationFlags.overbooking_down ? `Nedreglering är bokad till ${(res.downRatio * 100).toFixed(0)} % (>100 %). ` : ""}
          Minska budad effekt eller andelen timmar på de överlappande produkterna.
        </div>`;
      }
      if (res.validationFlags.duration_exceeded.length > 0) {
        alertsHtml += `<div class="bess-alert danger">
          <strong>⚠️ OTILLÅTET ENERGIKRAV:</strong> Följande bud överskrider batteriets uthållighet (C-tal):
          <ul>
            ${res.validationFlags.duration_exceeded.map(d => `<li>${d.product}: Kräver ${d.endurance_h}h uthållighet. Med ditt lager kan du max bjuda <strong>${d.max_allowed_mw} MW</strong> (du angav ${d.bid_mw} MW).</li>`).join("")}
          </ul>
        </div>`;
      }
      if (res.validationFlags.market_impact_high.length > 0) {
        alertsHtml += `<div class="bess-alert warn">
          <strong>⚠️ PRISPÅVERKANDE BUDVOLYM:</strong> Ditt bud utgör över 20 % av hela Sveriges upphandlingsbehov för 
          ${res.validationFlags.market_impact_high.map(m => `${m.product} (${(m.share * 100).toFixed(0)} % av ${m.svkReq} MW)`).join(", ")}.
          Ett så stort enskilt bud kommer själv att krascha clearingpriset eller nekas av SvK.
        </div>`;
      }
      if (res.isArbitrageUnprofitable) {
        alertsHtml += `<div class="bess-alert info">
          <strong>💡 Dämpat Arbitrage:</strong> I ${state.zone} är dygnsspreaden (${res.dailySpread.toFixed(0)} kr/MWh) nära eller under gränsen för slitage (${state.cycle_cost_sek_mwh} kr/MWh) och förluster. Nettoarbitraget bidrar begränsat till kalkylen.
        </div>`;
      }
      alertBox.innerHTML = alertsHtml;
      alertBox.style.display = alertsHtml ? "block" : "none";
    }

    // 5. Result Cards
    setText("res-gross-revenue", fmtMSEK(res.grossRevenue));
    setText("res-net-revenue", fmtMSEK(res.netRevenueOwner));
    setText("res-ebitda", fmtMSEK(res.ebitda));
    setText("res-payback", fmtYears(res.paybackYears));
    setText("res-total-capex", fmtMSEK(res.totalCapex));
    setText("res-ebitda-per-mw", fmtKr(res.ebitdaPerMw) + "/MW");
    setText("res-net-npv", fmtMSEK(res.netNpv));

    // 6. Products Table
    const tableBody = document.getElementById("bess-products-table-body");
    if (tableBody) {
      let rowsHtml = "";
      Object.keys(res.productResults).forEach(key => {
        const p = res.productResults[key];
        const alloc = state.allocations[key];
        const badgeClass = p.market_status === "price_maker" ? "badge danger" : (p.market_status === "notice" ? "badge warn" : "badge success");
        const badgeText = p.market_status === "price_maker" ? "Prispåverkande" : (p.market_status === "notice" ? "Märkbart" : "Pristagare");
        
        rowsHtml += `
          <tr class="${p.is_duration_exceeded ? "row-error" : ""}">
            <td><strong>${p.name}</strong></td>
            <td>
              <input type="number" class="table-input" data-key="${key}" data-field="bid_mw" value="${alloc.bid_mw}" step="1" min="0" max="1000"> MW
            </td>
            <td>
              <input type="number" class="table-input" data-key="${key}" data-field="bid_share" value="${(alloc.bid_share * 100).toFixed(0)}" step="5" min="0" max="100">%
            </td>
            <td>
              <input type="number" class="table-input" data-key="${key}" data-field="accept_rate" value="${(alloc.accept_rate * 100).toFixed(0)}" step="1" min="0" max="100">%
            </td>
            <td>${Math.round(p.hours_won).toLocaleString("sv-SE")} h</td>
            <td>${p.price_eur.toFixed(1)} €/MW</td>
            <td><span class="${badgeClass}">${badgeText} (${(p.market_share * 100).toFixed(0)}%)</span></td>
            <td style="text-align: right; font-weight: 600;">${fmtMSEK(p.revenue_sek)}</td>
          </tr>
        `;
      });

      // mFRR Energy & Arbitrage Rows
      rowsHtml += `
        <tr>
          <td><strong>mFRR Energiaktivering (MARI)</strong></td>
          <td colspan="3" style="color: var(--muted); font-size: 0.85em;">Aktiveras vid marginal över spot</td>
          <td>${res.productResults.mfrr_cap_up.bid_mw} MW budat</td>
          <td>—</td>
          <td><span class="badge success">Kvartshandel</span></td>
          <td style="text-align: right; font-weight: 600;">${fmtMSEK(res.mfrrEnergyRevSek)}</td>
        </tr>
        <tr>
          <td><strong>Dygnsarbitrage (Dagenföre ${state.zone})</strong></td>
          <td colspan="3" style="color: var(--muted); font-size: 0.85em;">${state.arbitrage_cycles} cykler · Spread ${res.dailySpread.toFixed(0)} kr/MWh</td>
          <td>${Math.round(res.usableMwh)} MWh/cykel</td>
          <td>—</td>
          <td><span class="badge ${res.isArbitrageUnprofitable ? "warn" : "success"}">${res.isArbitrageUnprofitable ? "Tunn spread" : "Lönsamt"}</span></td>
          <td style="text-align: right; font-weight: 600;">${fmtMSEK(Math.max(0, res.netArbitrageSek))}</td>
        </tr>
      `;
      tableBody.innerHTML = rowsHtml;
    }

    // 7. Cost Breakdown Table
    setText("cost-bsp", fmtMSEK(-res.bspCost));
    setText("cost-om", fmtMSEK(-res.omCost));
    setText("cost-fixed", fmtMSEK(-res.fixedFeesCost));
    setText("cost-wear", fmtMSEK(-res.reserveWearCost));
    setText("cost-total", fmtMSEK(-res.totalOpCosts));

    // 8. Dimensioning Tab Results
    setText("dim-main-mwh", state.capacity_mwh + " MWh");
    setText("dim-alt-mwh", res.dim.altMwh + " MWh");
    setText("dim-main-duration", res.durationH.toFixed(1) + " h");
    setText("dim-alt-duration", res.dim.altDuration.toFixed(1) + " h");
    setText("dim-main-capex", fmtMSEK(res.totalCapex));
    setText("dim-alt-capex", fmtMSEK(res.dim.altCapexTotal));
    setText("dim-extra-capex", fmtMSEK(res.dim.extraCapex));
    setText("dim-main-ebitda", fmtMSEK(res.ebitda));
    setText("dim-alt-ebitda", fmtMSEK(res.dim.altEbitda));
    setText("dim-extra-ebitda", fmtMSEK(res.dim.extraEbitda));
    setText("dim-marginal-payback", fmtYears(res.dim.marginalPayback));
    setText("dim-required-cost", Math.round(res.dim.requiredCellCost).toLocaleString("sv-SE") + " kr/kWh");

    // 9. Hybrid Solar Tab Results
    setText("sol-mw", res.hybrid.solMw + " MW");
    setText("sol-moved-mwh", Math.round(res.hybrid.solMovedMwh).toLocaleString("sv-SE") + " MWh");
    setText("sol-rev-lift", fmtMSEK(res.hybrid.solRevenueLift));
    setText("sol-wear", fmtMSEK(-res.hybrid.solAddedWear));
    setText("sol-net-year", fmtMSEK(res.hybrid.solNetBenefit));
    setText("sol-saved-conn", fmtMSEK(res.hybrid.savedConnSek));

    // 10. Extreme Years Tab Results
    setText("ext-active-label", res.extreme.active.label);
    setText("ext-active-gross", fmtMSEK(res.extreme.active.gross_sek));
    setText("ext-active-ebitda", fmtMSEK(res.extreme.active.ebitda));
    setText("ext-active-mult", res.extreme.active.mult.toFixed(2) + "×");
    setText("ext-active-payback", fmtYears(res.extreme.active.payback));
    setText("ext-cf-0", fmtMSEK(res.extreme.cashflow0));
    setText("ext-cf-1", fmtMSEK(res.extreme.cashflow1));
    setText("ext-cf-2", fmtMSEK(res.extreme.cashflow2));

    // 11. Head-to-Head VPP vs Utility BESS
    setText("vpp-villa-count", res.vpp.villaCount.toLocaleString("sv-SE") + " st");
    setText("vpp-gross-capex", fmtMSEK(res.vpp.grossCapexTotal));
    setText("vpp-subsidies", fmtMSEK(res.vpp.subsidiesTotal));
    setText("vpp-net-capex", fmtMSEK(res.vpp.netCapexTotal));
    setText("vpp-aggregator-fee", fmtMSEK(res.vpp.aggregatorFeeTotal));
    setText("vpp-net-homes", fmtMSEK(res.vpp.netHomesTotal));
    setText("vpp-utility-capex", fmtMSEK(res.totalCapex));
    setText("vpp-utility-bsp", fmtMSEK(res.vpp.utilityBspCost));
    setText("vpp-utility-net", fmtMSEK(res.vpp.utilityNetOwner));
  }

  function setVal(id, val) {
    const el = document.getElementById(id);
    if (el && document.activeElement !== el) el.value = val;
  }

  function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  // Event Listeners Setup
  function setupEvents() {
    // Zone Pills
    document.querySelectorAll(".bess-zone-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        state.zone = btn.getAttribute("data-zone");
        saveState();
        render();
      });
    });

    // Preset Switcher
    const presetSelect = document.getElementById("bess-preset-select");
    if (presetSelect) {
      presetSelect.addEventListener("change", (e) => {
        const pid = e.target.value;
        state.presetId = pid;
        if (PRESET_MAP[pid]) {
          Object.assign(state, JSON.parse(JSON.stringify(PRESET_MAP[pid])));
        }
        saveState();
        render();
      });
    }

    // Sliders & Number Inputs Sync
    bindSyncPair("slider-power-mw", "input-power-mw", v => { state.power_mw = parseFloat(v) || 1; });
    bindSyncPair("slider-cap-mwh", "input-cap-mwh", v => { state.capacity_mwh = parseFloat(v) || 1; });
    bindSyncPair("slider-capex-kwh", "input-capex-kwh", v => { state.capex_per_kwh = parseFloat(v) || 500; });
    bindSyncPair("slider-power-capex-share", "input-power-capex-share", v => { state.power_capex_share = (parseFloat(v) || 40) / 100; });

    bindInput("input-om-mw", v => { state.om_sek_per_mw_year = parseFloat(v) || 0; });
    bindInput("input-fixed-mw", v => { state.fixed_fees_sek_per_mw_year = parseFloat(v) || 0; });
    bindInput("input-cycle-cost", v => { state.cycle_cost_sek_mwh = parseFloat(v) || 0; });
    bindInput("input-bsp-share", v => { state.bsp_share = (parseFloat(v) || 3) / 100; });
    bindInput("input-bsp-floor", v => { state.bsp_floor_sek = (parseFloat(v) || 3) * 1000000; });
    bindInput("input-avail", v => { state.tech_availability = (parseFloat(v) || 95) / 100; });
    bindInput("input-roundtrip", v => { state.roundtrip_eff = (parseFloat(v) || 88) / 100; });
    bindInput("input-discount-rate", v => { state.discount_rate = (parseFloat(v) || 7) / 100; });
    bindInput("input-arbitrage-cycles", v => { state.arbitrage_cycles = parseInt(v, 10) || 0; });

    // Scenarios & Year Switchers
    document.querySelectorAll(".bess-scenario-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        state.scenario = btn.getAttribute("data-scenario");
        saveState();
        render();
      });
    });

    document.querySelectorAll(".bess-year-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        state.year = btn.getAttribute("data-year");
        saveState();
        render();
      });
    });

    // Delegated Table Input changes
    const tableBody = document.getElementById("bess-products-table-body");
    if (tableBody) {
      tableBody.addEventListener("input", (e) => {
        if (!e.target.classList.contains("table-input")) return;
        const key = e.target.getAttribute("data-key");
        const field = e.target.getAttribute("data-field");
        let val = parseFloat(e.target.value) || 0;
        if (field === "bid_share" || field === "accept_rate") {
          val = Math.max(0, Math.min(100, val)) / 100;
        }
        if (state.allocations[key]) {
          state.allocations[key][field] = val;
          saveState();
          render();
        }
      });
    }

    // Extreme Year Switcher
    const extSelect = document.getElementById("bess-extreme-type-select");
    if (extSelect) {
      extSelect.addEventListener("change", (e) => {
        state.extreme_type = e.target.value;
        saveState();
        render();
      });
    }

    // Tabs Switcher
    document.querySelectorAll(".bess-subnav-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".bess-subnav-btn").forEach(b => b.classList.remove("active"));
        document.querySelectorAll(".bess-tab-pane").forEach(p => p.classList.remove("active"));
        btn.classList.add("active");
        const targetId = btn.getAttribute("data-tab");
        const pane = document.getElementById(targetId);
        if (pane) pane.classList.add("active");
      });
    });
  }

  function bindSyncPair(sliderId, inputId, updateFn) {
    const slider = document.getElementById(sliderId);
    const input = document.getElementById(inputId);
    if (!slider || !input) return;

    slider.addEventListener("input", () => {
      input.value = slider.value;
      updateFn(slider.value);
      saveState();
      render();
    });

    input.addEventListener("input", () => {
      slider.value = input.value;
      updateFn(input.value);
      saveState();
      render();
    });
  }

  function bindInput(id, updateFn) {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("input", () => {
      updateFn(el.value);
      saveState();
      render();
    });
  }

  // Fetch JSON payload if available
  function init() {
    loadState();
    fetch("data/bess-utility.json")
      .then(res => {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(json => {
        remoteData = Object.assign({}, DEFAULT_DATA, json);
        render();
      })
      .catch(err => {
        console.info("Using embedded default data for utility BESS:", err.message);
        render();
      });

    setupEvents();
    render();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Export for testing
  window._UtilityBess = {
    state,
    calculate,
    DEFAULT_DATA,
    PRESET_MAP
  };
})();
