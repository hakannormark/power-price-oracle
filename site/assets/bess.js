// BESS & Home Battery Valuation Controller

(function () {
  const state = {
    data: null,
    selectedZone: "SE4",
    numOwners: 2,
    strategy: "mixed",
    scenario: "nordic_frequency",
    selectedOfferId: "solis_dyness_15",
    showCustomCf: false,
    selectedEra: "modern",
    backtestOfferId: "custom",
    backtestCompareOfferId: "solis_dyness_15",

    // Custom system settings
    custom: {
      cap_kwh: 15,
      p_kw: 10,
      load_kwh: 8000,
      pv_kwp: 10,
      gross_price: 65000,
      fcr_rate_month: 25,
    },
  };

  function fmtKr(val) {
    if (val === null || val === undefined || isNaN(val)) return "— kr";
    return Math.round(val).toLocaleString("sv-SE") + " kr";
  }

  function fmtYears(val) {
    if (val === null || val === undefined || isNaN(val) || !isFinite(val)) return "> 15 år";
    if (val > 15) return "> 15 år";
    return val.toFixed(1).replace(".", ",") + " år";
  }

  const STORAGE_KEY = "bess_user_state_v1";

  function loadState() {
    try {
      const ppoZone = localStorage.getItem("ppo.zone");
      if (ppoZone && ["SE1", "SE2", "SE3", "SE4"].includes(ppoZone)) {
        state.selectedZone = ppoZone;
      }

      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const saved = JSON.parse(raw);
        if (saved.selectedZone && ["SE1", "SE2", "SE3", "SE4"].includes(saved.selectedZone)) {
          state.selectedZone = saved.selectedZone;
        }
        if (saved.numOwners !== undefined) state.numOwners = parseInt(saved.numOwners, 10);
        if (saved.strategy) state.strategy = saved.strategy;
        if (saved.scenario) state.scenario = saved.scenario;
        if (saved.selectedEra) state.selectedEra = saved.selectedEra;
        if (saved.selectedOfferId) state.selectedOfferId = saved.selectedOfferId;
        if (saved.backtestOfferId) state.backtestOfferId = saved.backtestOfferId;
        if (saved.backtestCompareOfferId !== undefined) state.backtestCompareOfferId = saved.backtestCompareOfferId;
        if (saved.showCustomCf !== undefined) state.showCustomCf = !!saved.showCustomCf;
        if (saved.custom && typeof saved.custom === "object") {
          state.custom = Object.assign({}, state.custom, saved.custom);
        }
      }
    } catch (e) {
      console.warn("Could not load BESS state from localStorage:", e);
    }
  }

  function saveState() {
    try {
      localStorage.setItem("ppo.zone", state.selectedZone);

      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        selectedZone: state.selectedZone,
        numOwners: state.numOwners,
        strategy: state.strategy,
        scenario: state.scenario,
        selectedEra: state.selectedEra,
        selectedOfferId: state.selectedOfferId,
        backtestOfferId: state.backtestOfferId,
        backtestCompareOfferId: state.backtestCompareOfferId,
        showCustomCf: state.showCustomCf,
        custom: state.custom,
      }));
    } catch (e) {
      console.warn("Could not save BESS state to localStorage:", e);
    }
  }

  function init() {
    loadState();
    fetch("data/bess.json")
      .then((r) => {
        if (!r.ok) throw new Error("Could not load data/bess.json");
        return r.json();
      })
      .then((payload) => {
        state.data = payload;
        bindControls();
        renderAll();
      })
      .catch((err) => {
        console.error("BESS data error:", err);
        const container = document.getElementById("bess-app");
        if (container) {
          container.innerHTML = `<div class="card"><p class="sub">Kunde inte läsa in batterikalkylen: ${err.message}</p></div>`;
        }
      });
  }

  function getZoneData() {
    if (!state.data) return null;
    if (state.data.zones && state.data.zones[state.selectedZone]) {
      return state.data.zones[state.selectedZone];
    }
    return {
      name: state.selectedZone,
      stats: {
        mean_spot_sek_kwh: 0.85,
        mean_daily_spread_sek_kwh: 1.15,
        arbitrage_yield_sek_per_kwh: 230,
        solar_avoided_cost_sek_kwh: 1.65,
      },
      offers: state.data.offers || [],
      backtest: state.data.backtest || {},
    };
  }

  function bindControls() {
    // 1. Zone pills (scoped to #bess-zone-pills)
    const zonePills = document.querySelectorAll("#bess-zone-pills .zone-pill");
    zonePills.forEach((btn) => {
      const z = btn.getAttribute("data-zone");
      if (z === state.selectedZone) {
        btn.classList.add("active");
      } else {
        btn.classList.remove("active");
      }
      btn.addEventListener("click", () => {
        zonePills.forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        state.selectedZone = btn.getAttribute("data-zone") || "SE4";
        saveState();
        renderAll();
      });
    });

    // 2. Era pills (scoped to #bess-era-pills)
    const eraPills = document.querySelectorAll("#bess-era-pills .zone-pill");
    eraPills.forEach((btn) => {
      const era = btn.getAttribute("data-era");
      if (era === state.selectedEra) {
        btn.classList.add("active");
      } else {
        btn.classList.remove("active");
      }
      btn.addEventListener("click", () => {
        eraPills.forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        state.selectedEra = btn.getAttribute("data-era") || "modern";
        saveState();
        renderBacktest();
      });
    });

    // 3. Custom system inputs (sliders + number inputs sync)
    function sync(sliderId, numId, stateKey) {
      const sl = document.getElementById(sliderId);
      const nm = document.getElementById(numId);
      if (!sl || !nm) return;

      if (state.custom[stateKey] !== undefined) {
        sl.value = state.custom[stateKey];
        nm.value = state.custom[stateKey];
      }

      sl.addEventListener("input", (e) => {
        const val = parseFloat(e.target.value);
        nm.value = val;
        state.custom[stateKey] = val;
        saveState();
        renderAll();
      });

      nm.addEventListener("input", (e) => {
        let val = parseFloat(e.target.value);
        if (isNaN(val)) return;
        sl.value = val;
        state.custom[stateKey] = val;
        saveState();
        renderAll();
      });
    }

    sync("bess-cap-slider", "bess-cap-num", "cap_kwh");
    sync("bess-p-slider", "bess-p-num", "p_kw");
    sync("bess-load-slider", "bess-load-num", "load_kwh");
    sync("bess-pv-slider", "bess-pv-num", "pv_kwp");
    sync("bess-cost-slider", "bess-cost-num", "gross_price");
    sync("bess-fcr-slider", "bess-fcr-num", "fcr_rate_month");

    // 4. Dropdowns
    const ownersSelect = document.getElementById("bess-owners");
    if (ownersSelect) {
      if (state.numOwners !== undefined) ownersSelect.value = String(state.numOwners);
      ownersSelect.addEventListener("change", (e) => {
        state.numOwners = parseInt(e.target.value, 10);
        saveState();
        renderAll();
      });
    }

    const stratSelect = document.getElementById("bess-strategy");
    if (stratSelect) {
      if (state.strategy) stratSelect.value = state.strategy;
      const fcrGroup = document.getElementById("bess-fcr-group");
      if (fcrGroup) {
        fcrGroup.style.opacity = state.strategy === "energy_only" ? "0.4" : "1.0";
      }
      stratSelect.addEventListener("change", (e) => {
        state.strategy = e.target.value;
        if (fcrGroup) {
          fcrGroup.style.opacity = state.strategy === "energy_only" ? "0.4" : "1.0";
        }
        saveState();
        renderAll();
      });
    }

    const scenSelect = document.getElementById("bess-scenario");
    if (scenSelect) {
      if (state.scenario) scenSelect.value = state.scenario;
      scenSelect.addEventListener("change", (e) => {
        state.scenario = e.target.value;
        saveState();
        renderAll();
      });
    }

    // 5. Toggle custom cash flow
    const toggleCfBtn = document.getElementById("bess-custom-toggle-cf");
    if (toggleCfBtn) {
      toggleCfBtn.addEventListener("click", () => {
        state.showCustomCf = !state.showCustomCf;
        const box = document.getElementById("bess-custom-cf-box");
        if (box) {
          box.style.display = state.showCustomCf ? "block" : "none";
        }
        toggleCfBtn.textContent = state.showCustomCf
          ? "Dölj 15-årig Framtidsprognos ▲"
          : "Visa 15-årig Framtidsprognos för Kassaflöde (År 1–15 framåt) ▼";
      });
    }

    // 6. Backtest selectors
    const btOfferSelect = document.getElementById("bess-backtest-offer");
    if (btOfferSelect) {
      if (state.backtestOfferId) btOfferSelect.value = state.backtestOfferId;
      btOfferSelect.addEventListener("change", (e) => {
        state.backtestOfferId = e.target.value;
        saveState();
        renderBacktest();
      });
    }

    const btCompareSelect = document.getElementById("bess-backtest-compare");
    if (btCompareSelect) {
      if (state.backtestCompareOfferId) btCompareSelect.value = state.backtestCompareOfferId;
      btCompareSelect.addEventListener("change", (e) => {
        state.backtestCompareOfferId = e.target.value;
        saveState();
        renderBacktest();
      });
    }

    const btStratSelect = document.getElementById("bess-backtest-strategy");
    if (btStratSelect) {
      if (state.strategy) btStratSelect.value = state.strategy;
      btStratSelect.addEventListener("change", (e) => {
        state.strategy = e.target.value;
        const mainStrat = document.getElementById("bess-strategy");
        if (mainStrat) mainStrat.value = state.strategy;
        saveState();
        renderAll();
      });
    }
  }

  // Calculate dynamic custom battery system
  function computeCustomSystem() {
    const c = state.custom;
    const zd = getZoneData();
    const stats = (zd && zd.stats) || {};

    const dailySpread = stats.mean_daily_spread_sek_kwh || 1.15;
    const solarAvoidedCost = stats.solar_avoided_cost_sek_kwh || 1.65;

    // Green tech deduction
    const maxDeduction = state.numOwners === 1 ? 50000 : 100000;
    const theoreticalDeduction = c.gross_price * 0.485;
    const actualDeduction = Math.min(theoreticalDeduction, maxDeduction);
    const netPrice = c.gross_price - actualDeduction;
    const deductionLost = Math.max(0, theoreticalDeduction - maxDeduction);

    // Usable capacity & C-rate
    const usableKwh = c.cap_kwh * 0.90;
    const cRate = c.cap_kwh > 0 ? (c.p_kw / c.cap_kwh) : 0;

    // Solar self-consumption
    let storedSolarKwh = 0;
    let solarSavingsSek = 0;
    if (c.pv_kwp > 0) {
      const annualPv = c.pv_kwp * 850;
      // In high-load households (25k - 60k kWh), daytime base load absorbs more direct solar
      const daytimeLoadFrac = c.load_kwh >= 25000 ? 0.45 : 0.35;
      const directPv = Math.min(annualPv * 0.40, c.load_kwh * daytimeLoadFrac);
      const surplusPv = Math.max(0, annualPv - directPv);
      const nightLoad = c.load_kwh * 0.45;
      storedSolarKwh = Math.min(surplusPv, usableKwh * 180, nightLoad);
      solarSavingsSek = Math.round(storedSolarKwh * solarAvoidedCost);
    }

    // Spot arbitrage
    // Limit per cycle: battery usable capacity or inverter power * window (approx 3.5h)
    const maxCycleKwh = Math.min(usableKwh * 0.85, c.p_kw * 3.5);
    const spreadMargin = Math.max(0, (dailySpread * 0.88) - 0.15);
    const arbitrageKwh = Math.round(maxCycleKwh * 280);
    const arbitrageProfitSek = Math.round(maxCycleKwh * spreadMargin * 280);

    // Ancillary services (FCR-D)
    let ancillaryRevSek = 0;
    if (state.strategy === "mixed") {
      if (c.p_kw >= 3) {
        // Aggregator requirement: at least 3 kW continuous power
        // Biddable power is limited by inverter capacity and battery continuous discharge capability (with 10% SOC buffer)
        const maxDischargeKw = Math.min(c.p_kw, c.cap_kwh * 0.9);
        const biddableKw = Math.round(maxDischargeKw * 0.9);
        ancillaryRevSek = Math.round(biddableKw * c.fcr_rate_month * 12);
      }
    }

    const totalAnnualValue = solarSavingsSek + arbitrageProfitSek + ancillaryRevSek;

    // 15-year lifecycle forecast (forward-looking Years 1 to 15)
    const cashFlows = [];
    let cumCash = -netPrice;
    let npv = -netPrice;
    let npv10 = -netPrice;
    let dynamicPayback = null;
    let discPayback = null;

    for (let yr = 1; yr <= 15; yr++) {
      const deg = Math.max(0.65, 1.0 - (yr - 1) * 0.018); // 1.8% annual degradation
      let shock = 1.0;
      if (state.scenario === "nordic_frequency") {
        if (yr === 3 || yr === 11) shock = 2.0;      // Positive extreme shock (gas/dry)
        else if (yr === 7) shock = 0.6;              // Wet negative shock
      }

      // Ancillary services degrade with battery SoH AND market saturation/cannibalization
      let yrAncillary = 0;
      if (state.strategy === "mixed") {
        let saturationDecay = 1.0;
        if (state.scenario === "nordic_frequency") {
          // Moderate market saturation: 5% erosion per year after year 1 down to 40% floor
          saturationDecay = Math.max(0.40, Math.pow(0.95, yr - 1));
        } else if (state.scenario === "cannibalization") {
          // Rapid cannibalization: 12% erosion per year down to 25% floor
          saturationDecay = Math.max(0.25, Math.pow(0.88, yr - 1));
        }
        yrAncillary = Math.round(ancillaryRevSek * deg * saturationDecay * (shock > 1 ? 1.2 : (shock < 1 ? 0.8 : 1.0)));
      }

      const yrSolar = Math.round(solarSavingsSek * deg * (shock > 1 ? (1 + (shock - 1) * 0.2) : shock));
      const yrArb = Math.round(arbitrageProfitSek * deg * shock);
      const yrTotal = yrSolar + yrArb + yrAncillary;

      const prevCumCash = cumCash;
      cumCash += yrTotal;

      if (dynamicPayback === null && cumCash >= 0 && yrTotal > 0) {
        const unrecovered = -prevCumCash;
        dynamicPayback = (yr - 1) + Math.max(0, Math.min(1, unrecovered / yrTotal));
      }

      const prevNpv = npv;
      const df = 1.0 / Math.pow(1.05, yr);
      const discountedYr = yrTotal * df;
      npv += discountedYr;
      if (yr <= 10) npv10 += discountedYr;

      if (discPayback === null && npv >= 0 && discountedYr > 0) {
        const unrecoveredNpv = -prevNpv;
        discPayback = (yr - 1) + Math.max(0, Math.min(1, unrecoveredNpv / discountedYr));
      }

      cashFlows.push({
        year: yr,
        solar_savings: yrSolar,
        arbitrage_profit: yrArb,
        ancillary_revenue: yrAncillary,
        total_revenue: yrTotal,
        capacity_retention: deg,
        prev_cumulative_cash_flow: prevCumCash,
        cumulative_cash_flow: cumCash,
      });
    }

    const simplePayback = dynamicPayback !== null ? dynamicPayback : (totalAnnualValue > 0 ? (netPrice / totalAnnualValue) : null);

    return {
      netPrice,
      actualDeduction,
      deductionLost,
      usableKwh,
      cRate,
      storedSolarKwh,
      solarSavingsSek,
      arbitrageKwh,
      arbitrageProfitSek,
      ancillaryRevSek,
      totalAnnualValue,
      simplePayback,
      discPayback: discPayback || (simplePayback ? simplePayback * 1.3 : null),
      npv15y: Math.round(npv),
      npv10y: Math.round(npv10),
      cashFlows,
    };
  }

  function getOfferLifecycle(offer) {
    const key = `${state.scenario}_${state.numOwners === 1 ? "1_owner" : "2_owners"}`;
    const altKey = `${state.scenario}_2_owners`;
    return (offer.lifecycle && (offer.lifecycle[key] || offer.lifecycle[altKey])) || {};
  }

  function renderAll() {
    if (!state.data) return;
    renderZoneInfo();
    renderActiveConfigSummary();
    renderCustomSystem();
    renderOffersTable();
    renderOfferDetail();
    renderBacktest();
  }

  function renderActiveConfigSummary() {
    const summaryEl = document.getElementById("bess-active-config-summary");
    if (!summaryEl) return;
    const c = state.custom;
    const res = computeCustomSystem();
    summaryEl.innerHTML = `
      <span style="color: #38bdf8; font-weight: 700;">${c.cap_kwh} kWh</span> batteri · 
      <span style="color: #38bdf8; font-weight: 700;">${c.p_kw} kW</span> växelriktare (${res.cRate.toFixed(2)} C) · 
      <span style="color: #38bdf8; font-weight: 700;">${c.load_kwh.toLocaleString("sv-SE")} kWh/år</span> förbrukning · 
      <span style="color: #38bdf8; font-weight: 700;">${c.pv_kwp} kWp</span> solceller · 
      <span style="color: #38bdf8; font-weight: 700;">${fmtKr(c.gross_price)}</span> brutto · 
      Elområde <span style="color: #38bdf8; font-weight: 700;">${state.selectedZone}</span>
    `;
  }

  function getYearStatusInfo(year, scenario, cumCash, prevCumCash) {
    let badges = [];
    let rowStyle = "";

    // 1. Financial milestone: Break-even
    const isBreakEven = prevCumCash < 0 && cumCash >= 0;
    if (isBreakEven) {
      badges.push(`<span style="background: rgba(74, 222, 128, 0.2); color: #4ade80; border: 1px solid rgba(74, 222, 128, 0.4); padding: 2px 7px; border-radius: 4px; font-weight: 600; font-size: 0.82em;">🎉 Break-even</span>`);
    }

    // 2. Scenario shocks
    if (scenario === "nordic_frequency") {
      if (year === 3) {
        badges.push(`<span style="background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); padding: 2px 7px; border-radius: 4px; font-weight: 600; font-size: 0.82em;">🔥 Positivt extremår (2,3×)</span>`);
        rowStyle = "background: rgba(245, 158, 11, 0.08);";
      } else if (year === 7) {
        badges.push(`<span style="background: rgba(56, 189, 248, 0.2); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.4); padding: 2px 7px; border-radius: 4px; font-weight: 600; font-size: 0.82em;">🌧️ Negativt extremår (0,6×)</span>`);
        rowStyle = "background: rgba(56, 189, 248, 0.08);";
      } else if (year === 11) {
        badges.push(`<span style="background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); padding: 2px 7px; border-radius: 4px; font-weight: 600; font-size: 0.82em;">🔥 Positivt extremår (2,3×)</span>`);
        rowStyle = "background: rgba(245, 158, 11, 0.08);";
      }
    } else if (scenario === "cannibalization") {
      if (year === 3 || year === 6 || year === 9) {
        badges.push(`<span style="background: rgba(248, 113, 113, 0.15); color: #f87171; border: 1px solid rgba(248, 113, 113, 0.3); padding: 2px 7px; border-radius: 4px; font-size: 0.82em;">⚠️ FCR-D mättnad</span>`);
      }
    }

    // 3. Technical & lifecycle milestones
    if (year === 1 && badges.length === 0) {
      badges.push(`<span style="color: var(--muted); font-size: 0.82em;">Driftsättning (Basår)</span>`);
    } else if (year === 10) {
      badges.push(`<span style="background: rgba(148, 163, 184, 0.15); color: #cbd5e1; border: 1px solid rgba(148, 163, 184, 0.3); padding: 2px 7px; border-radius: 4px; font-size: 0.82em;">🛡️ Slut på garanti (år 10)</span>`);
    } else if (year === 15) {
      badges.push(`<span style="color: var(--muted); font-size: 0.82em;">🏁 Slut på kalkyl (år 15)</span>`);
    }

    if (badges.length === 0) {
      badges.push(`<span style="color: var(--muted); font-size: 0.82em;">Normalt basår</span>`);
    }

    return {
      badgeHtml: badges.join(" "),
      rowStyle,
    };
  }

  function renderZoneInfo() {
    const zd = getZoneData();
    const infoBox = document.getElementById("bess-zone-info");
    if (!infoBox || !zd) return;

    const stats = zd.stats || {};
    infoBox.innerHTML = `
      <strong>Valt elområde: ${zd.name}</strong> · 
      Snittspot: <strong>${(stats.mean_spot_sek_kwh * 100).toFixed(1)} öre/kWh</strong> · 
      Snitt-dygnsspread: <strong>${(stats.mean_daily_spread_sek_kwh * 100).toFixed(1)} öre/kWh</strong> · 
      Värde av lagrad solel: <strong>${stats.solar_avoided_cost_sek_kwh ? stats.solar_avoided_cost_sek_kwh.toFixed(2) : "1,65"} kr/kWh</strong> · 
      Arbitragekapacitet: <strong>${fmtKr(stats.arbitrage_yield_sek_per_kwh)}/kWh/år</strong>
    `;
  }

  function renderCustomSystem() {
    const res = computeCustomSystem();
    const c = state.custom;

    // Update value labels
    const capLabel = document.getElementById("bess-cap-val");
    if (capLabel) capLabel.textContent = `${c.cap_kwh} kWh`;
    const capHint = document.getElementById("bess-cap-hint");
    if (capHint) capHint.textContent = `Användbar kapacitet (90 % DoD): ${res.usableKwh.toFixed(1)} kWh`;

    const pLabel = document.getElementById("bess-p-val");
    if (pLabel) pLabel.textContent = `${c.p_kw} kW`;
    const cRateHint = document.getElementById("bess-c-rate-hint");
    if (cRateHint) {
      let msg = "";
      let color = "var(--faint)";
      if (c.p_kw < 3) {
        msg = `C-tal: ${res.cRate.toFixed(2)} C ⚠️ Under 3 kW: Aggregatorer kräver normalt minst 3 kW för att delta i stödtjänster.`;
        color = "#f87171";
      } else if (res.cRate > 1.2) {
        const minDuration = Math.round(60 / res.cRate);
        msg = `C-tal: ${res.cRate.toFixed(2)} C ⚡ Hög urladdningstakt: Batteriet töms på ca ${minDuration} minuter. Kontrollera att BMS tillåter denna urladdningsström.`;
        color = "#facc15";
      } else {
        const enduranceH = (1 / res.cRate).toFixed(1);
        msg = `C-tal: ${res.cRate.toFixed(2)} C ✅ Hög uthållighet: ca ${enduranceH} timmar vid maxeffekt (godkänd för SvK:s 20-minuterskrav på FCR-D).`;
        color = "var(--faint)";
      }
      cRateHint.textContent = msg;
      cRateHint.style.color = color;
    }

    const loadLabel = document.getElementById("bess-load-val");
    if (loadLabel) loadLabel.textContent = `${c.load_kwh.toLocaleString("sv-SE")} kWh`;

    const pvLabel = document.getElementById("bess-pv-val");
    if (pvLabel) pvLabel.textContent = `${c.pv_kwp} kWp`;
    const pvHint = document.getElementById("bess-pv-hint");
    if (pvHint) {
      pvHint.textContent = c.pv_kwp > 0
        ? `Årsproduktion: ca ${(c.pv_kwp * 700).toLocaleString("sv-SE")} kWh · Lagrad solel: ca ${res.storedSolarKwh.toLocaleString("sv-SE")} kWh/år`
        : `Inga solceller: Batteriet kör ren nätarbitrage och stödtjänster`;
    }

    const costLabel = document.getElementById("bess-cost-val");
    if (costLabel) costLabel.textContent = fmtKr(c.gross_price);

    const fcrLabel = document.getElementById("bess-fcr-val");
    if (fcrLabel) fcrLabel.textContent = `${c.fcr_rate_month} kr/kW/mån`;

    // KPI Results
    const netBox = document.getElementById("bess-res-net");
    if (netBox) netBox.textContent = fmtKr(res.netPrice);
    const dedBox = document.getElementById("bess-res-deduction");
    if (dedBox) {
      if (res.deductionLost > 0) {
        dedBox.innerHTML = `<span style="color: #f87171;">Tak sprängt! Tappar ${fmtKr(res.deductionLost)} i avdrag</span>`;
      } else {
        dedBox.textContent = `Grön teknik: -${fmtKr(res.actualDeduction)}`;
      }
    }

    const revBox = document.getElementById("bess-res-rev");
    if (revBox) revBox.textContent = `${fmtKr(res.totalAnnualValue)}/år`;
    const revSplit = document.getElementById("bess-res-rev-split");
    if (revSplit) {
      revSplit.textContent = `Sol: ${fmtKr(res.solarSavingsSek)} · Arb: ${fmtKr(res.arbitrageProfitSek)} · FCR: ${fmtKr(res.ancillaryRevSek)}`;
    }

    const pbBox = document.getElementById("bess-res-payback");
    if (pbBox) pbBox.textContent = fmtYears(res.simplePayback);
    const pbDisc = document.getElementById("bess-res-payback-disc");
    if (pbDisc) pbDisc.textContent = `Diskonterad: ${fmtYears(res.discPayback)} (5 % kalkylränta)`;

    const npvBox = document.getElementById("bess-res-npv");
    if (npvBox) {
      npvBox.textContent = fmtKr(res.npv15y);
      npvBox.style.color = res.npv15y >= 0 ? "#4ade80" : "#f87171";
    }
    const npv10Box = document.getElementById("bess-res-npv-10");
    if (npv10Box) npv10Box.textContent = `10-års NPV: ${fmtKr(res.npv10y)}`;

    // Render custom cash flow rows
    const cfTbody = document.getElementById("bess-custom-cf-body");
    if (cfTbody) {
      let cfHtml = "";
      res.cashFlows.forEach((cf) => {
        const cumColor = cf.cumulative_cash_flow >= 0 ? "#4ade80" : "#f87171";
        const status = getYearStatusInfo(cf.year, state.scenario, cf.cumulative_cash_flow, cf.prev_cumulative_cash_flow);
        cfHtml += `
          <tr style="${status.rowStyle}">
            <td><strong>År ${cf.year}</strong></td>
            <td>${status.badgeHtml}</td>
            <td>${fmtKr(cf.solar_savings)}</td>
            <td>${fmtKr(cf.arbitrage_profit)}</td>
            <td>${fmtKr(cf.ancillary_revenue)}</td>
            <td><strong>${fmtKr(cf.total_revenue)}</strong></td>
            <td>${(cf.capacity_retention * 100).toFixed(1)} %</td>
            <td style="color: ${cumColor}; font-weight: 600;">${fmtKr(cf.cumulative_cash_flow)}</td>
          </tr>
        `;
      });
      cfTbody.innerHTML = cfHtml;
    }
  }

  function renderOffersTable() {
    const tbody = document.getElementById("bess-table-body");
    if (!tbody) return;

    const zd = getZoneData();
    const offers = (zd && zd.offers) || (state.data && state.data.offers) || [];
    let html = "";

    // Insert user custom configuration as reference row
    const customRes = computeCustomSystem();
    const c = state.custom;
    html += `
      <tr style="background: rgba(56, 189, 248, 0.08); border-left: 3px solid #38bdf8;">
        <td style="font-weight: 700; color: #38bdf8;">Ditt val</td>
        <td>
          <strong style="color: #38bdf8;">Anpassad anläggning</strong><br>
          <small style="color: var(--muted);">${c.pv_kwp} kWp sol · ${c.load_kwh} kWh förbrukning</small>
        </td>
        <td>${c.cap_kwh} kWh<br><small style="color: var(--muted);">${c.p_kw} kW (${customRes.cRate.toFixed(2)} C)</small></td>
        <td>${fmtKr(c.gross_price)}</td>
        <td>
          <strong>${fmtKr(customRes.netPrice)}</strong>
          ${customRes.deductionLost > 0 ? `<br><small style="color: #f87171;">Tak spräckt (+${fmtKr(customRes.deductionLost)})</small>` : ""}
        </td>
        <td style="color: #38bdf8; font-weight: 700;">${fmtKr(customRes.totalAnnualValue)}/år</td>
        <td><strong style="color: #4ade80; font-size: 1.05em;">${fmtYears(customRes.simplePayback)}</strong></td>
        <td>10–15 år</td>
        <td><span class="badge" style="background: rgba(56, 189, 248, 0.2); color: #38bdf8;">Valfritt</span></td>
      </tr>
    `;

    offers.forEach((o) => {
      const net = state.numOwners === 1 ? o.net_price_1_owner : o.net_price_2_owners;
      const lost = state.numOwners === 1 ? o.deduction_lost_1_owner : 0;
      const disp = (o.annual_dispatch && o.annual_dispatch[state.strategy]) || {};
      const rev = disp.total_annual_value_sek || 0;
      const lc = getOfferLifecycle(o);
      const isSelected = o.id === state.selectedOfferId;

      let lockBadge = `<span class="badge" style="background: rgba(34, 197, 94, 0.2); color: #4ade80;">Fritt</span>`;
      if (o.lock_in_level === "moderate") {
        lockBadge = `<span class="badge" style="background: rgba(234, 179, 8, 0.2); color: #facc15;">Måttlig</span>`;
      } else if (o.lock_in_level === "severe") {
        lockBadge = `<span class="badge" style="background: rgba(239, 68, 68, 0.2); color: #f87171;">Stark</span>`;
      }

      const paybackText = fmtYears(lc.payback_years);
      const rowClass = isSelected ? "bess-row-selected" : "";

      html += `
        <tr class="${rowClass}" data-offer-id="${o.id}" style="cursor: pointer;">
          <td style="font-weight: 600;">#${o.rank}</td>
          <td>
            <strong>${o.name}</strong><br>
            <small style="color: var(--muted);">${o.hardware}</small>
          </td>
          <td>${o.capacity_kwh} kWh<br><small style="color: var(--muted);">${o.battery_max_power_kw} kW (${o.c_rate} C)</small></td>
          <td>${fmtKr(o.gross_price)}</td>
          <td>
            <strong>${fmtKr(net)}</strong>
            ${lost > 0 ? `<br><small style="color: #f87171;">Tak spräckt (+${fmtKr(lost)})</small>` : ""}
          </td>
          <td style="color: #38bdf8; font-weight: 600;">${fmtKr(rev)}/år</td>
          <td><strong style="color: #4ade80; font-size: 1.05em;">${paybackText}</strong></td>
          <td>${o.warranty_years} år</td>
          <td>${lockBadge}</td>
        </tr>
      `;
    });

    tbody.innerHTML = html;

    // Row selection
    tbody.querySelectorAll("tr[data-offer-id]").forEach((tr) => {
      tr.addEventListener("click", () => {
        state.selectedOfferId = tr.getAttribute("data-offer-id");
        saveState();
        renderAll();
      });
    });
  }

  function renderOfferDetail() {
    const detailBox = document.getElementById("bess-detail-box");
    if (!detailBox) return;

    const zd = getZoneData();
    const offers = (zd && zd.offers) || (state.data && state.data.offers) || [];
    const offer = offers.find((o) => o.id === state.selectedOfferId) || offers[0];
    if (!offer) return;

    const net = state.numOwners === 1 ? offer.net_price_1_owner : offer.net_price_2_owners;
    const lost = state.numOwners === 1 ? offer.deduction_lost_1_owner : 0;
    const disp = (offer.annual_dispatch && offer.annual_dispatch[state.strategy]) || {};
    const lc = getOfferLifecycle(offer);
    const cashFlows = lc.cash_flows || [];

    let cashFlowRows = "";
    let prevCum = -net;
    cashFlows.slice(0, 15).forEach((cf) => {
      const cumColor = cf.cumulative_cash_flow >= 0 ? "#4ade80" : "#f87171";
      const status = getYearStatusInfo(cf.year, state.scenario, cf.cumulative_cash_flow, prevCum);
      prevCum = cf.cumulative_cash_flow;
      cashFlowRows += `
        <tr style="${status.rowStyle}">
          <td><strong>År ${cf.year}</strong></td>
          <td>${status.badgeHtml}</td>
          <td>${fmtKr(cf.solar_savings)}</td>
          <td>${fmtKr(cf.arbitrage_profit)}</td>
          <td>${fmtKr(cf.ancillary_revenue)}</td>
          <td><strong>${fmtKr(cf.total_revenue)}</strong></td>
          <td>${(cf.capacity_retention * 100).toFixed(1)} %</td>
          <td style="color: ${cumColor}; font-weight: 600;">${fmtKr(cf.cumulative_cash_flow)}</td>
        </tr>
      `;
    });

    detailBox.innerHTML = `
      <div class="card" style="margin-top: 1.5rem;">
        <div class="section-title">
          <h3>Detaljanalys: ${offer.name} (${state.selectedZone})</h3>
          <span class="badge" style="background: rgba(56, 189, 248, 0.2); color: #38bdf8;">Rank #${offer.rank}</span>
        </div>
        
        <p class="sub" style="margin-bottom: 1.2rem;">${offer.notes_sv}</p>

        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 1.5rem;">
          <div style="background: var(--bg-card); padding: 1rem; border-radius: 8px; border: 1px solid var(--border);">
            <div style="font-size: 0.85em; color: var(--muted);">Nettoinvestering (${state.numOwners} ägare)</div>
            <div style="font-size: 1.4em; font-weight: 700; color: #fff;">${fmtKr(net)}</div>
            ${lost > 0 ? `<div style="font-size: 0.8em; color: #f87171;">Tappad reduktion: ${fmtKr(lost)}</div>` : `<div style="font-size: 0.8em; color: #4ade80;">Fullt grönt avdrag</div>`}
          </div>

          <div style="background: var(--bg-card); padding: 1rem; border-radius: 8px; border: 1px solid var(--border);">
            <div style="font-size: 0.85em; color: var(--muted);">Årsvärde i ${state.selectedZone}</div>
            <div style="font-size: 1.4em; font-weight: 700; color: #38bdf8;">${fmtKr(disp.total_annual_value_sek)}/år</div>
            <div style="font-size: 0.8em; color: var(--muted);">Sol: ${fmtKr(disp.solar_savings_sek)} · Arb: ${fmtKr(disp.arbitrage_profit_sek)} · FCR: ${fmtKr(disp.ancillary_revenue_sek)}</div>
          </div>

          <div style="background: var(--bg-card); padding: 1rem; border-radius: 8px; border: 1px solid var(--border);">
            <div style="font-size: 0.85em; color: var(--muted);">Återbetalningstid (Payback)</div>
            <div style="font-size: 1.4em; font-weight: 700; color: #4ade80;">${fmtYears(lc.payback_years)}</div>
            <div style="font-size: 0.8em; color: var(--muted);">Diskonterad: ${fmtYears(lc.discounted_payback_years)} (5 % ränta)</div>
          </div>

          <div style="background: var(--bg-card); padding: 1rem; border-radius: 8px; border: 1px solid var(--border);">
            <div style="font-size: 0.85em; color: var(--muted);">15-års Nettonuvärde (NPV)</div>
            <div style="font-size: 1.4em; font-weight: 700; color: ${lc.npv_15y >= 0 ? '#4ade80' : '#f87171'};">${fmtKr(lc.npv_15y)}</div>
            <div style="font-size: 0.8em; color: var(--muted);">10-års NPV: ${fmtKr(lc.npv_10y)}</div>
          </div>
        </div>

        <div style="background: rgba(255, 255, 255, 0.03); padding: 1rem; border-radius: 8px; margin-bottom: 1.5rem; font-size: 0.9em;">
          <strong>Tekniska villkor & Inlåsning:</strong><br>
          • <strong>Växelriktare / C-tal:</strong> ${offer.inverter_kw} kW märkeffekt, men batteriets maxeffekt är ${offer.battery_max_power_kw} kW (${offer.c_rate} C).<br>
          • <strong>Garanti:</strong> ${offer.warranty_years} år${offer.warranty_cycles ? ` eller ${offer.warranty_cycles} cykler` : ""}.<br>
          • <strong>Inlåsning:</strong> ${offer.lock_in_desc}<br>
          • <strong>Ö-drift & Utomhus:</strong> Ö-drift: ${offer.islanding === "yes" ? "Integrerad" : offer.islanding === "option" ? "Tillval mot kostnad" : "Kräver extern brytare"}. Utomhusplacering: ${offer.outdoor_placement ? "Ja (IP65/IP66)" : "Nej (kräver frostfritt)"}.
        </div>

        <h4>15-årig Framtidsprognos för Kassaflöde (Framåt i tiden: År 1–15)</h4>
        <div style="background: rgba(56, 189, 248, 0.04); border: 1px solid rgba(56, 189, 248, 0.2); border-radius: 6px; padding: 0.75rem 1rem; margin-top: 0.5rem; font-size: 0.85em; color: var(--muted); line-height: 1.5;">
          <strong>Simulering framåt i tiden:</strong> Denna kalkyl simulerar förväntat framtida kassaflöde över 15 år med -1,8 %/år batterislitage, kalkylränta (5 %) och successiv priserosion på stödtjänstmarknaden. <em>(Jämför med Sektion 4 som visar verkliga historiska kalenderår bakåt i tiden.)</em>
        </div>

        <div style="overflow-x: auto; margin-top: 0.75rem;">
          <table class="data-table" style="width: 100%; font-size: 0.9em;">
            <thead>
              <tr>
                <th>År (Framåt)</th>
                <th>Händelse / Status</th>
                <th>Solelbesparing</th>
                <th>Spotarbitrage</th>
                <th>Stödtjänster (FCR-D)*</th>
                <th>Totalt kassaflöde</th>
                <th>Kapacitet</th>
                <th>Ackumulerat netto</th>
              </tr>
            </thead>
            <tbody>
              ${cashFlowRows}
            </tbody>
          </table>
        </div>
        <p style="font-size: 0.8em; color: var(--muted); margin-top: 0.5rem;">
          * Stödtjänstintäkter sjunker gradvis över 15 år på grund av minskad batterikapacitet (SoH) och marknadsmättnad på frekvensmarknaden.
        </p>
      </div>
    `;
  }

  function getSystemBacktestYear(sysId, yData) {
    if (!yData) return { solar: 0, arb: 0, ancillary: 0, total: 0, name: "Okänt", shortName: "Okänt" };
    const strat = state.strategy === "energy_only" ? "energy_only" : "mixed";

    if (sysId === "custom") {
      const c = state.custom;
      const solis = (yData.offers && yData.offers["solis_dyness_15"] && yData.offers["solis_dyness_15"][strat]) || {};
      const solisSolar = solis.solar_savings_sek || 0;
      const solisArb = solis.arbitrage_profit_sek || 0;
      const solisAnc = solis.ancillary_revenue_sek || 0;

      // 1. Spot arbitrage scaling based on usable capacity & max power:
      // Solis baseline: 15 kWh, 10 kW -> min(15 * 0.9 * 0.70, 10 * 2.5) = 9.45 kWh
      const customArbCap = Math.min(c.cap_kwh * 0.90 * 0.70, c.p_kw * 2.5);
      const arbRatio = customArbCap / 9.45;
      const arb = Math.round(solisArb * arbRatio);

      // 2. Solar PV self-consumption scaling:
      let solar = 0;
      if (c.pv_kwp > 0) {
        const annualPv = c.pv_kwp * 850;
        const daytimeLoadFrac = c.load_kwh >= 25000 ? 0.45 : 0.35;
        const directPv = Math.min(annualPv * 0.40, c.load_kwh * daytimeLoadFrac);
        const surplusPv = Math.max(0, annualPv - directPv);
        const nightLoad = c.load_kwh * 0.45;
        const storedSolarKwh = Math.min(surplusPv, c.cap_kwh * 0.90 * 180, nightLoad);
        // Solis baseline: 10 kWp, 8000 kWh load, 15 kWh battery -> min(5100, 2430, 3600) = 2430 kWh
        const solarRatio = storedSolarKwh / 2430;
        solar = Math.round(solisSolar * solarRatio);
      }

      // 3. Ancillary (FCR-D) services:
      let ancillary = 0;
      if (strat === "mixed" && c.p_kw >= 3) {
        // Solis baseline: 10 kW, 9 kW biddable
        const customBiddable = Math.min(c.p_kw, c.cap_kwh * 0.9) * 0.9;
        const rateRatio = (c.fcr_rate_month || 25) / 25;
        ancillary = Math.round(solisAnc * (customBiddable / 9.0) * rateRatio);
      }

      const total = solar + arb + ancillary;
      return {
        name: `Ditt val (${c.cap_kwh} kWh / ${c.p_kw} kW)`,
        shortName: `Ditt val`,
        solar,
        arb,
        ancillary,
        total,
      };
    }

    // Commercial offer lookup from backtest dataset
    const zd = getZoneData();
    const offersList = (zd && zd.offers) || (state.data && state.data.offers) || [];
    const offerMeta = offersList.find((o) => o.id === sysId) || { name: sysId };

    const offData = (yData.offers && yData.offers[sysId] && yData.offers[sysId][strat]) || {};
    const solar = Math.round(offData.solar_savings_sek || 0);
    const arb = Math.round(offData.arbitrage_profit_sek || 0);
    const ancillary = Math.round(offData.ancillary_revenue_sek || 0);
    const total = Math.round(offData.total_value_sek || (solar + arb + ancillary));

    return {
      name: offerMeta.name,
      shortName: offerMeta.name.split(" ")[0] || offerMeta.name,
      solar,
      arb,
      ancillary,
      total,
    };
  }

  function renderBacktest() {
    const btContainer = document.getElementById("bess-backtest-box");
    const btTitle = document.getElementById("bess-backtest-title");
    if (!btContainer) return;

    const zd = getZoneData();
    const btData = (zd && zd.backtest) || (state.data && state.data.backtest) || {};
    const yearsData = btData.years || {};

    let eraLabel = "Moderna eran (2022–2026)";
    if (state.selectedEra === "classic") eraLabel = "Gamla eran (2015–2020)";
    else if (state.selectedEra === "all") eraLabel = "Hela historiken (2015–2026)";

    const primarySysId = state.backtestOfferId || "custom";
    const compareSysId = state.backtestCompareOfferId || "solis_dyness_15";
    const hasCompare = compareSysId !== "none" && compareSysId !== primarySysId;

    // Sample data to retrieve readable system names
    const sampleYearKey = Object.keys(yearsData)[0] || "2024";
    const sampleYData = yearsData[sampleYearKey] || {};
    const primarySample = getSystemBacktestYear(primarySysId, sampleYData);
    const compareSample = hasCompare ? getSystemBacktestYear(compareSysId, sampleYData) : null;

    if (btTitle) {
      btTitle.textContent = `3. Historiskt backtest i ${state.selectedZone}: ${primarySample.name} (${eraLabel})`;
    }

    // Sync backtest selectors in the DOM
    const btOfferSelect = document.getElementById("bess-backtest-offer");
    if (btOfferSelect) {
      const customOpt = btOfferSelect.querySelector('option[value="custom"]');
      if (customOpt) {
        customOpt.textContent = `⭐ Ditt val: Egen anläggning (${state.custom.cap_kwh} kWh / ${state.custom.p_kw} kW)`;
      }
      btOfferSelect.value = primarySysId;
    }

    const btCompareSelect = document.getElementById("bess-backtest-compare");
    if (btCompareSelect) {
      const customCompOpt = btCompareSelect.querySelector('option[value="custom"]');
      if (customCompOpt) {
        customCompOpt.textContent = `Ditt val: Egen anläggning (${state.custom.cap_kwh} kWh / ${state.custom.p_kw} kW)`;
      }
      btCompareSelect.value = compareSysId;
    }

    const btStratSelect = document.getElementById("bess-backtest-strategy");
    if (btStratSelect) {
      btStratSelect.value = state.strategy;
    }

    let allYearKeys = Object.keys(yearsData).sort((a, b) => parseInt(a, 10) - parseInt(b, 10));
    let filteredYears = allYearKeys;
    if (state.selectedEra === "modern") {
      filteredYears = allYearKeys.filter((y) => parseInt(y, 10) >= 2022);
    } else if (state.selectedEra === "classic") {
      filteredYears = allYearKeys.filter((y) => parseInt(y, 10) <= 2020);
    }

    let rows = "";
    filteredYears.forEach((year) => {
      const yData = yearsData[year];
      const prim = getSystemBacktestYear(primarySysId, yData);
      const comp = hasCompare ? getSystemBacktestYear(compareSysId, yData) : null;

      const yNum = parseInt(year, 10);
      let eraBadge = "";
      if (yNum <= 2020) {
        eraBadge = `<span class="badge" style="background: rgba(148, 163, 184, 0.15); color: #94a3b8;">Gamla eran</span>`;
      } else if (yNum === 2022) {
        eraBadge = `<span class="badge" style="background: rgba(250, 204, 21, 0.2); color: #facc15;">Energikris</span>`;
      } else {
        eraBadge = `<span class="badge" style="background: rgba(56, 189, 248, 0.2); color: #38bdf8;">Moderna eran</span>`;
      }

      const spreadStr = yData.mean_daily_spread_sek_kwh 
        ? `${(yData.mean_daily_spread_sek_kwh * 100).toFixed(1)} öre`
        : "—";
      const negHoursStr = (yData.neg_hours !== undefined) ? `${yData.neg_hours} h` : "—";

      rows += `
        <tr ${yNum === 2022 ? 'style="background: rgba(250, 204, 21, 0.08);"' : (yNum <= 2020 ? 'style="opacity: 0.85;"' : '')}>
          <td><strong>${year}</strong></td>
          <td>${eraBadge}</td>
          <td>${(yData.mean_spot_sek_kwh * 100).toFixed(1)} öre/kWh</td>
          <td style="color: #38bdf8; font-weight: 600;">${spreadStr}</td>
          <td>${negHoursStr}</td>
          <td>${fmtKr(prim.solar)}</td>
          <td>${fmtKr(prim.arb)}</td>
          ${state.strategy === "mixed" ? `<td>${fmtKr(prim.ancillary)}</td>` : ""}
          <td style="color: #4ade80; font-weight: 700;">${fmtKr(prim.total)}</td>
          ${hasCompare ? `<td style="color: #38bdf8; font-weight: 600;">${fmtKr(comp.total)}</td>` : ""}
        </tr>
      `;
    });

    // Dynamic era metrics for the selected primary system
    const classicYears = allYearKeys.filter((y) => parseInt(y, 10) <= 2020);
    const modernYears = allYearKeys.filter((y) => parseInt(y, 10) >= 2022);

    function calcEraMetrics(yearList) {
      if (!yearList || yearList.length === 0) return { meanSpot: 0, meanSpread: 0, meanArb: 0, meanTot: 0 };
      let sumSpot = 0, sumSpread = 0, sumArb = 0, sumTot = 0;
      yearList.forEach((yk) => {
        const yd = yearsData[yk];
        sumSpot += yd.mean_spot_sek_kwh || 0;
        sumSpread += yd.mean_daily_spread_sek_kwh || 0;
        const p = getSystemBacktestYear(primarySysId, yd);
        sumArb += p.arb;
        sumTot += p.total;
      });
      const n = yearList.length;
      return {
        meanSpot: sumSpot / n,
        meanSpread: sumSpread / n,
        meanArb: Math.round(sumArb / n),
        meanTot: Math.round(sumTot / n),
      };
    }

    const classicM = calcEraMetrics(classicYears);
    const modernM = calcEraMetrics(modernYears);
    const mult = classicM.meanTot > 0 ? (modernM.meanTot / classicM.meanTot).toFixed(1) : "—";

    let summaryBlock = `
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; margin-top: 1.25rem;">
        <div style="background: rgba(148, 163, 184, 0.05); border: 1px solid var(--border); border-radius: 8px; padding: 1rem;">
          <div style="font-weight: 600; color: #94a3b8; font-size: 0.95em; margin-bottom: 0.5rem;">Gamla eran (2015–2020) Snitt för ${primarySample.shortName}:</div>
          <div style="font-size: 0.88em; color: var(--muted); line-height: 1.6;">
            • Snittspot: <strong>${(classicM.meanSpot * 100).toFixed(1)} öre/kWh</strong><br>
            • Dygnsspread: <strong>${(classicM.meanSpread * 100).toFixed(1)} öre/kWh</strong><br>
            • Spotarbitrage: <strong>${fmtKr(classicM.meanArb)}/år</strong><br>
            • Totalt årsvärde: <strong>${fmtKr(classicM.meanTot)}/år</strong><br>
            • Slutsats: <em>För låg volatilitet. Batteri olönsamt (payback &gt;40 år).</em>
          </div>
        </div>

        <div style="background: rgba(56, 189, 248, 0.06); border: 1px solid rgba(56, 189, 248, 0.25); border-radius: 8px; padding: 1rem;">
          <div style="font-weight: 600; color: #38bdf8; font-size: 0.95em; margin-bottom: 0.5rem;">Moderna eran (2022–2026) Snitt för ${primarySample.shortName}:</div>
          <div style="font-size: 0.88em; color: var(--text); line-height: 1.6;">
            • Snittspot: <strong>${(modernM.meanSpot * 100).toFixed(1)} öre/kWh</strong><br>
            • Dygnsspread: <strong>${(modernM.meanSpread * 100).toFixed(1)} öre/kWh</strong><br>
            • Spotarbitrage: <strong>${fmtKr(modernM.meanArb)}/år</strong><br>
            • Totalt årsvärde: <strong>${fmtKr(modernM.meanTot)}/år</strong><br>
            • Slutsats: <em>${mult}× högre årsvärde i moderna eran! Batteri når 4–7 års återbetalningstid.</em>
          </div>
        </div>
      </div>
    `;

    btContainer.innerHTML = `
      <div style="overflow-x: auto; margin-top: 1rem;">
        <table class="data-table" style="width: 100%; font-size: 0.9em;">
          <thead>
            <tr>
              <th>År</th>
              <th>Marknadsepok</th>
              <th>Snittspot</th>
              <th>Dygnsspread</th>
              <th>Neg. timmar</th>
              <th>Solel (${primarySample.shortName})</th>
              <th>Arbitrage (${primarySample.shortName})</th>
              ${state.strategy === "mixed" ? `<th>Stödtjänster (${primarySample.shortName})</th>` : ""}
              <th style="color: #4ade80;">Totalt årsvärde: ${primarySample.shortName}</th>
              ${hasCompare ? `<th style="color: #38bdf8;">Totalt årsvärde: ${compareSample.shortName}</th>` : ""}
            </tr>
          </thead>
          <tbody>
            ${rows}
          </tbody>
        </table>
      </div>
      <p style="font-size: 0.8em; color: var(--muted); margin-top: 0.5rem; margin-bottom: 1rem;">
        * <strong>Totalt årsvärde (kr/år):</strong> Summan av årets ekonomiska nytta för det enskilda året = Solel (sparad nätel) + Spotarbitrage (vinst från dygnsspreadar) + Stödtjänster (ersättning via aggregator). Siffran visar utfallet för respektive år (ej ackumulerat).
      </p>
      ${summaryBlock}
    `;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
