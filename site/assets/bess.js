// BESS & Home Battery Valuation Controller

(function () {
  const state = {
    data: null,
    numOwners: 2,
    strategy: "mixed",
    scenario: "nordic_frequency",
    selectedOfferId: "solis_dyness_15",
  };

  function fmtKr(val) {
    if (val === null || val === undefined || isNaN(val)) return "—";
    return Math.round(val).toLocaleString("sv-SE") + " kr";
  }

  function fmtYears(val) {
    if (val === null || val === undefined || isNaN(val)) return "—";
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

  function bindControls() {
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
  }

  function getOfferLifecycle(offer) {
    const key = `${state.scenario}_${state.numOwners === 1 ? "1_owner" : "2_owners"}`;
    const altKey = `${state.scenario}_2_owners`;
    return (offer.lifecycle && (offer.lifecycle[key] || offer.lifecycle[altKey])) || {};
  }

  function getOfferRevenue(offer) {
    const disp = (offer.annual_dispatch && offer.annual_dispatch[state.strategy]) || {};
    return disp.total_annual_value_sek || 0;
  }

  function renderAll() {
    if (!state.data) return;
    renderOffersTable();
    renderOfferDetail();
    renderBacktest();
  }

  function renderOffersTable() {
    const tbody = document.getElementById("bess-table-body");
    if (!tbody) return;

    const offers = state.data.offers || [];
    let html = "";

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
    tbody.querySelectorAll("tr").forEach((tr) => {
      tr.addEventListener("click", () => {
        state.selectedOfferId = tr.getAttribute("data-offer-id");
        renderAll();
      });
    });
  }

  function renderOfferDetail() {
    const detailBox = document.getElementById("bess-detail-box");
    if (!detailBox) return;

    const offer = (state.data.offers || []).find((o) => o.id === state.selectedOfferId) || state.data.offers[0];
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
          <h3>Detaljanalys: ${offer.name}</h3>
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
            <div style="font-size: 0.85em; color: var(--muted);">Årsvärde (basår)</div>
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

        <h4>15-årigt kassaflöde (inkl. degradering & extremår)</h4>
        <div style="overflow-x: auto; margin-top: 0.75rem;">
          <table class="data-table" style="width: 100%; font-size: 0.9em;">
            <thead>
              <tr>
                <th>År</th>
                <th>Solelbesparing</th>
                <th>Spotarbitrage</th>
                <th>Stödtjänster</th>
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
      </div>
    `;
  }

  function renderBacktest() {
    const btContainer = document.getElementById("bess-backtest-box");
    if (!btContainer || !state.data.backtest || !state.data.backtest.years) return;

    const yearsData = state.data.backtest.years;
    let rows = "";

    Object.keys(yearsData).sort().forEach((year) => {
      const yData = yearsData[year];
      const solis = (yData.offers && yData.offers["solis_dyness_15"]) || {};
      const solisMixed = solis.mixed || {};
      const sig = (yData.offers && yData.offers["sigenergy_18"]) || {};
      const sigMixed = sig.mixed || {};

      rows += `
        <tr>
          <td><strong>${year}</strong></td>
          <td>${(yData.mean_spot_sek_kwh * 100).toFixed(1)} öre/kWh</td>
          <td>${(yData.min_spot_sek_kwh * 100).toFixed(1)} / ${(yData.max_spot_sek_kwh * 100).toFixed(1)} öre</td>
          <td>${fmtKr(solisMixed.solar_savings_sek)}</td>
          <td>${fmtKr(solisMixed.arbitrage_profit_sek)}</td>
          <td style="color: #4ade80; font-weight: 600;">${fmtKr(solisMixed.total_value_sek)}</td>
          <td style="color: #38bdf8; font-weight: 600;">${fmtKr(sigMixed.total_value_sek)}</td>
        </tr>
      `;
    });

    btContainer.innerHTML = `
      <div style="overflow-x: auto; margin-top: 1rem;">
        <table class="data-table" style="width: 100%; font-size: 0.9em;">
          <thead>
            <tr>
              <th>År</th>
              <th>Snittpris SE4</th>
              <th>Min / Max spot</th>
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
    `;
  }

  document.addEventListener("DOMContentLoaded", init);
})();
