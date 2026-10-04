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
    if (val === null || val === undefined || isNaN(val) || !isFinite(val)) return "—";
    return val.toFixed(1).replace(".", ",") + " år";
  }

  function init() {
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
      btn.addEventListener("click", () => {
        zonePills.forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        state.selectedZone = btn.getAttribute("data-zone") || "SE4";
        renderAll();
      });
    });

    // 2. Era pills (scoped to #bess-era-pills)
    const eraPills = document.querySelectorAll("#bess-era-pills .zone-pill");
    eraPills.forEach((btn) => {
      btn.addEventListener("click", () => {
        eraPills.forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        state.selectedEra = btn.getAttribute("data-era") || "modern";
        renderBacktest();
      });
    });

    // 3. Custom system inputs (sliders + number inputs sync)
    function sync(sliderId, numId, stateKey) {
      const sl = document.getElementById(sliderId);
      const nm = document.getElementById(numId);
      if (!sl || !nm) return;

      sl.addEventListener("input", (e) => {
        const val = parseFloat(e.target.value);
        nm.value = val;
        state.custom[stateKey] = val;
        renderAll();
      });

      nm.addEventListener("input", (e) => {
        let val = parseFloat(e.target.value);
        if (isNaN(val)) return;
        sl.value = val;
        state.custom[stateKey] = val;
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
      ownersSelect.addEventListener("change", (e) => {
        state.numOwners = parseInt(e.target.value, 10);
        renderAll();
      });
    }

    const stratSelect = document.getElementById("bess-strategy");
    if (stratSelect) {
      stratSelect.addEventListener("change", (e) => {
        state.strategy = e.target.value;
        const fcrGroup = document.getElementById("bess-fcr-group");
        if (fcrGroup) {
          fcrGroup.style.opacity = state.strategy === "energy_only" ? "0.4" : "1.0";
        }
        renderAll();
      });
    }

    const scenSelect = document.getElementById("bess-scenario");
    if (scenSelect) {
      scenSelect.addEventListener("change", (e) => {
        state.scenario = e.target.value;
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
      const biddableKw = Math.min(c.p_kw, c.cap_kwh * (cRate >= 0.5 ? 0.8 : cRate * 1.6));
      ancillaryRevSek = Math.round(biddableKw * c.fcr_rate_month * 12);
    }

    const totalAnnualValue = solarSavingsSek + arbitrageProfitSek + ancillaryRevSek;
    const simplePayback = totalAnnualValue > 0 ? (netPrice / totalAnnualValue) : null;

    // 15-year lifecycle forecast (forward-looking Years 1 to 15)
    const cashFlows = [];
    let cumCash = -netPrice;
    let npv = -netPrice;
    let npv10 = -netPrice;
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

      cumCash += yrTotal;

      const df = 1.0 / Math.pow(1.05, yr);
      npv += yrTotal * df;
      if (yr <= 10) npv10 += yrTotal * df;

      if (discPayback === null && npv >= 0) {
        discPayback = yr;
      }

      cashFlows.push({
        year: yr,
        solar_savings: yrSolar,
        arbitrage_profit: yrArb,
        ancillary_revenue: yrAncillary,
        total_revenue: yrTotal,
        capacity_retention: deg,
        cumulative_cash_flow: cumCash,
      });
    }

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
    renderCustomSystem();
    renderOffersTable();
    renderOfferDetail();
    renderBacktest();
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
      const cRateText = res.cRate >= 0.5 
        ? `C-tal: ${res.cRate.toFixed(2)} C (Godkänd för full stödtjänstbudning)`
        : `C-tal: ${res.cRate.toFixed(2)} C (Mindre än 0,5C: batteriet begränsar stödtjänstbudet till ${Math.round(c.cap_kwh * 0.5)} kW)`;
      cRateHint.textContent = cRateText;
      cRateHint.style.color = res.cRate >= 0.5 ? "var(--faint)" : "#f87171";
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
        cfHtml += `
          <tr>
            <td><strong>År ${cf.year}</strong></td>
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
    cashFlows.slice(0, 15).forEach((cf) => {
      const cumColor = cf.cumulative_cash_flow >= 0 ? "#4ade80" : "#f87171";
      cashFlowRows += `
        <tr>
          <td>År ${cf.year}</td>
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

  function renderBacktest() {
    const btContainer = document.getElementById("bess-backtest-box");
    const btTitle = document.getElementById("bess-backtest-title");
    if (!btContainer) return;

    const zd = getZoneData();
    const btData = (zd && zd.backtest) || (state.data && state.data.backtest) || {};
    const yearsData = btData.years || {};
    const erasSummary = btData.eras_summary || {};

    let eraLabel = "Moderna eran (2022–2026)";
    if (state.selectedEra === "classic") eraLabel = "Gamla eran (2015–2020)";
    else if (state.selectedEra === "all") eraLabel = "Hela historiken (2015–2026)";

    if (btTitle) {
      btTitle.textContent = `4. Historiskt backtest i ${state.selectedZone}: Faktiskt utfall (${eraLabel})`;
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
      const solis = (yData.offers && yData.offers["solis_dyness_15"]) || {};
      const solisMixed = solis.mixed || {};
      const sig = (yData.offers && yData.offers["sigenergy_18"]) || {};
      const sigMixed = sig.mixed || {};

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
          <td>${fmtKr(solisMixed.solar_savings_sek)}</td>
          <td>${fmtKr(solisMixed.arbitrage_profit_sek)}</td>
          <td style="color: #4ade80; font-weight: 600;">${fmtKr(solisMixed.total_value_sek)}</td>
          <td style="color: #38bdf8; font-weight: 600;">${fmtKr(sigMixed.total_value_sek)}</td>
        </tr>
      `;
    });

    // Era summary metrics
    const classicSummary = erasSummary.classic || {};
    const modernSummary = erasSummary.modern || {};

    let summaryBlock = `
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; margin-top: 1.25rem;">
        <div style="background: rgba(148, 163, 184, 0.05); border: 1px solid var(--border); border-radius: 8px; padding: 1rem;">
          <div style="font-weight: 600; color: #94a3b8; font-size: 0.95em; margin-bottom: 0.5rem;">Gamla eran (2015–2020) Snitt:</div>
          <div style="font-size: 0.88em; color: var(--muted); line-height: 1.6;">
            • Snittspot: <strong>${classicSummary.mean_spot_sek_kwh ? (classicSummary.mean_spot_sek_kwh * 100).toFixed(1) : "36,7"} öre/kWh</strong><br>
            • Dygnsspread: <strong>${classicSummary.mean_daily_spread_sek_kwh ? (classicSummary.mean_daily_spread_sek_kwh * 100).toFixed(1) : "21,5"} öre/kWh</strong><br>
            • Solis arbitrage: <strong>${classicSummary.solis_mean_arbitrage_sek ? fmtKr(classicSummary.solis_mean_arbitrage_sek) : "467 kr"}/år</strong><br>
            • Slutsats: <em>För låg volatilitet. Batteri olönsamt (payback &gt;40 år).</em>
          </div>
        </div>

        <div style="background: rgba(56, 189, 248, 0.06); border: 1px solid rgba(56, 189, 248, 0.25); border-radius: 8px; padding: 1rem;">
          <div style="font-weight: 600; color: #38bdf8; font-size: 0.95em; margin-bottom: 0.5rem;">Moderna eran (2022–2026) Snitt:</div>
          <div style="font-size: 0.88em; color: var(--text); line-height: 1.6;">
            • Snittspot: <strong>${modernSummary.mean_spot_sek_kwh ? (modernSummary.mean_spot_sek_kwh * 100).toFixed(1) : "94,3"} öre/kWh</strong><br>
            • Dygnsspread: <strong>${modernSummary.mean_daily_spread_sek_kwh ? (modernSummary.mean_daily_spread_sek_kwh * 100).toFixed(1) : "125,4"} öre/kWh</strong><br>
            • Solis arbitrage: <strong>${modernSummary.solis_mean_arbitrage_sek ? fmtKr(modernSummary.solis_mean_arbitrage_sek) : "3 596 kr"}/år</strong><br>
            • Slutsats: <em>7–10× högre arbitragevärde. Batteri når 5–8 års återbetalningstid.</em>
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
              <th>Solel (Solis)</th>
              <th>Arbitrage (Solis)</th>
              <th>Summa Solis 15 kWh</th>
              <th>Summa Sigenergy 18 kWh</th>
            </tr>
          </thead>
          <tbody>
            ${rows}
          </tbody>
        </table>
      </div>
      ${summaryBlock}
    `;
  }

  document.addEventListener("DOMContentLoaded", init);
})();
