/* Power Price Oracle — frontend controller.
   All data is static JSON next to the page: fetches are relative to site/. */

const BASE = document.documentElement.dataset.base || "";

const ZONE_COLORS = { SE1: "#60a5fa", SE2: "#22d3ee", SE3: "#fbbf24", SE4: "#fb7185" };
const ZONES = ["SE1", "SE2", "SE3", "SE4"];
const STORE = { unit: "ppo.unit", zone: "ppo.zone", overlay: "ppo.overlay" };

const state = {
  unit: readStore(STORE.unit, "ore"),
  zone: readStore(STORE.zone, "SE3"),
  overlay: readStore(STORE.overlay, "0") === "1",
  overview: null,
  zoneData: {},
};

/* ------------------------------------------------------------ utilities */

function readStore(key, fallback) {
  try {
    return window.localStorage.getItem(key) ?? fallback;
  } catch (err) {
    return fallback;
  }
}

function writeStore(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (err) {
    /* private mode — preferences simply do not persist */
  }
}

async function loadJSON(path) {
  const response = await fetch(`${BASE}${path}`, { cache: "no-cache" });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

function toDisplay(eurMwh) {
  if (eurMwh === null || eurMwh === undefined) return null;
  return state.unit === "ore" ? eurMwh / 10 : eurMwh;
}

function unitLabel() {
  return state.unit === "ore" ? "öre/kWh" : "EUR/MWh";
}

function fmt(value, decimals = 1) {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  return value.toLocaleString("sv-SE", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function fmtPrice(eurMwh, decimals = 1) {
  const value = toDisplay(eurMwh);
  return value === null ? "–" : fmt(value, decimals);
}

function fmtTime(iso) {
  const date = new Date(iso);
  return date.toLocaleTimeString("sv-SE", { hour: "2-digit", minute: "2-digit" });
}

function fmtDayTime(iso) {
  const date = new Date(iso);
  const day = date.toLocaleDateString("sv-SE", { weekday: "short", day: "numeric", month: "short" });
  return `${day} ${fmtTime(iso)}`;
}

function fmtStamp(iso) {
  if (!iso) return "–";
  return new Date(iso).toLocaleString("sv-SE", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function el(id) {
  return document.getElementById(id);
}

function bucketLabel(bucket) {
  return bucket.replace("-", "–").replace("h", " h");
}

/* ------------------------------------------------------------ chrome */

function renderBanners(overview) {
  const banner = el("degraded-banner");
  if (!banner) return;
  if (overview.demo) {
    banner.className = "banner warn";
    banner.innerHTML =
      "<strong>Ingen ENTSO-E-nyckel — visar inte officiella priser.</strong> " +
      "Sajten kör på syntetiska demodata så att gränssnittet går att bedöma. " +
      "Sätt hemligheten <code>ENTSOE_TOKEN</code> för riktiga Nord Pool-priser.";
    banner.hidden = false;
  } else if (overview.degraded) {
    banner.className = "banner warn";
    banner.innerHTML =
      "<strong>Degraderad körning.</strong> En datakälla svarade inte, så delar av " +
      "prognosen kan komma från en tidigare körning. Se <a href='api.html'>status</a>.";
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }
}

function renderFooterMeta(overview) {
  document.querySelectorAll("[data-updated]").forEach((node) => {
    node.textContent = fmtStamp(overview.generated_at);
  });
  document.querySelectorAll("[data-next-update]").forEach((node) => {
    node.textContent = fmtStamp(overview.next_expected_update_utc);
  });
  document.querySelectorAll("[data-repo-url]").forEach((node) => {
    node.setAttribute("href", overview.repo_url);
  });
}

function bindUnitToggle(onChange) {
  document.querySelectorAll("[data-unit-toggle] button").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.unit === state.unit));
    button.addEventListener("click", () => {
      state.unit = button.dataset.unit;
      writeStore(STORE.unit, state.unit);
      document.querySelectorAll("[data-unit-toggle] button").forEach((other) => {
        other.setAttribute("aria-pressed", String(other.dataset.unit === state.unit));
      });
      document.querySelectorAll("[data-unit-label]").forEach((node) => {
        node.textContent = unitLabel();
      });
      onChange();
    });
  });
  document.querySelectorAll("[data-unit-label]").forEach((node) => {
    node.textContent = unitLabel();
  });
}

/* ------------------------------------------------------------ tiles */

function sparkline(values, color) {
  const points = values.filter((v) => v !== null && v !== undefined);
  if (points.length < 2) return "";
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const width = 100;
  const height = 30;
  const step = width / (values.length - 1);

  let path = "";
  values.forEach((value, index) => {
    if (value === null || value === undefined) return;
    const x = (index * step).toFixed(2);
    const y = (height - 3 - ((value - min) / span) * (height - 6)).toFixed(2);
    path += `${path ? "L" : "M"}${x},${y}`;
  });

  return `<svg class="tile-spark" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">
    <path d="${path}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round" opacity="0.9"/>
  </svg>`;
}

function renderTiles(overview, onSelect) {
  const host = el("zone-tiles");
  if (!host) return;
  host.innerHTML = overview.zones
    .map((tile) => {
      const color = ZONE_COLORS[tile.zone];
      const current = tile.current;
      const eur = current ? current.eur_mwh : null;
      const sourceChip = current
        ? current.source === "official"
          ? '<span class="chip official">Officiellt</span>'
          : current.source === "demo"
            ? '<span class="chip demo">Demo</span>'
            : '<span class="chip forecast">Prognos</span>'
        : "";
      const big = state.unit === "ore" ? fmtPrice(eur, 1) : fmtPrice(eur, 1);
      const alt =
        eur === null
          ? ""
          : state.unit === "ore"
            ? `${fmt(eur, 1)} EUR/MWh`
            : `${fmt(eur / 10, 1)} öre/kWh`;

      return `<button type="button" class="tile" data-zone="${tile.zone}"
          style="--zone:${color}" aria-pressed="${tile.zone === state.zone}">
        <span class="tile-head">
          <span class="tile-zone">${tile.zone}</span>
          <span class="tile-city">${tile.name}</span>
          ${sourceChip}
        </span>
        <span class="tile-price">
          <span class="big">${big}</span>
          <span class="unit" data-unit-label>${unitLabel()}</span>
        </span>
        <span class="tile-alt">${alt}</span>
        ${sparkline(tile.spark || [], color)}
      </button>`;
    })
    .join("");

  host.querySelectorAll(".tile").forEach((button) => {
    button.addEventListener("click", () => onSelect(button.dataset.zone));
  });
}

function markSelectedTile() {
  document.querySelectorAll("#zone-tiles .tile").forEach((tile) => {
    tile.setAttribute("aria-pressed", String(tile.dataset.zone === state.zone));
  });
}

/* ------------------------------------------------------------ drivers */

function renderDrivers(zoneData) {
  const host = el("drivers");
  if (!host) return;
  const drivers = zoneData.drivers || {};
  const features = drivers.features || {};
  const anomalyKey = `temp_anomaly_${zoneData.zone.toLowerCase()}_c`;

  const chips = [
    { label: "Vindindex lokalt", value: features.wind_index_local, decimals: 2, pivot: 1 },
    { label: "Vindindex norr", value: features.wind_index_north, decimals: 2, pivot: 1 },
    { label: "Vindindex söder", value: features.wind_index_south, decimals: 2, pivot: 1 },
    { label: "Temp.avvikelse", value: features[anomalyKey], decimals: 1, pivot: 0, suffix: " °C" },
    { label: "Solindex dagtid", value: features.solar_index_daytime, decimals: 2, pivot: 0 },
    { label: "SE4 − SE2", value: features.spread_proxy_se4_se2, decimals: 1, pivot: 0, suffix: " EUR/MWh" },
  ];

  host.innerHTML = `
    <div class="section-title">
      <h2>Varför ser det ut så här?</h2>
      <span class="chip">${drivers.regime_label_sv || "–"}</span>
    </div>
    <p style="font-size:1.05rem">${drivers.headline_sv || ""}</p>
    <ul class="bullets">${(drivers.bullets_sv || []).map((b) => `<li>${b}</li>`).join("")}</ul>
    <div class="feature-chips">
      ${chips
        .map((chip) => {
          if (chip.value === null || chip.value === undefined) return "";
          const direction =
            chip.value > chip.pivot + 0.01 ? "up" : chip.value < chip.pivot - 0.01 ? "down" : "";
          return `<div class="feature-chip ${direction}">
            <span class="label">${chip.label}</span>
            <span class="value">${fmt(chip.value, chip.decimals)}${chip.suffix || ""}</span>
          </div>`;
        })
        .join("")}
    </div>`;
}

/* ------------------------------------------------------------ next hours */

function dayKey(iso) {
  return iso.slice(0, 10);
}

function dayHeading(key) {
  const label = new Date(key).toLocaleDateString("sv-SE", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
  const today = new Date();
  const diff = Math.round((new Date(key) - new Date(dayKey(today.toISOString()))) / 86400000);
  if (diff === 0) return `I dag · ${label}`;
  if (diff === 1) return `I morgon · ${label}`;
  return label.charAt(0).toUpperCase() + label.slice(1);
}

function sourceChip(source) {
  if (source === "official") return '<span class="chip official">Officiellt</span>';
  if (source === "demo") return '<span class="chip demo">Demo</span>';
  return '<span class="chip forecast">Prognos</span>';
}

function renderNextHours(zoneData) {
  const host = el("next-hours");
  if (!host) return;
  const rows = zoneData.next_hours || [];
  if (!rows.length) {
    host.innerHTML = '<p class="loading">Ingen prognos tillgänglig.</p>';
    return;
  }

  // One disclosure per delivery day. The first two open, so the page reads at
  // rest without burying today's prices under seven days of rows.
  const days = [];
  rows.forEach((row) => {
    const key = dayKey(row.ts);
    const last = days[days.length - 1];
    if (last && last.key === key) last.rows.push(row);
    else days.push({ key, rows: [row] });
  });

  host.innerHTML = days
    .map((day, index) => {
      const values = day.rows.map((r) => r.eur_mwh).filter((v) => v !== null);
      const low = values.length ? Math.min(...values) : null;
      const high = values.length ? Math.max(...values) : null;
      const settled = day.rows.filter((r) => r.source !== "forecast").length;
      const dayChip =
        settled === day.rows.length
          ? sourceChip(day.rows[0].source)
          : settled > 0
            ? '<span class="chip">Delvis officiellt</span>'
            : '<span class="chip forecast">Prognos</span>';

      const body = day.rows
        .map(
          (row) => `<tr>
            <td>${fmtTime(row.ts)}</td>
            <td class="num">${fmtPrice(row.eur_mwh)}</td>
            <td class="num">${
              row.p10 === null || row.p10 === undefined
                ? '<span class="muted">–</span>'
                : `${fmtPrice(row.p10)} – ${fmtPrice(row.p90)}`
            }</td>
            <td>${sourceChip(row.source)}</td>
          </tr>`
        )
        .join("");

      return `<details class="day"${index < 2 ? " open" : ""}>
        <summary>
          <span class="day-name">${dayHeading(day.key)}</span>
          <span class="day-range">${fmtPrice(low)} – ${fmtPrice(high)}
            <span data-unit-label>${unitLabel()}</span></span>
          ${dayChip}
        </summary>
        <div class="table-scroll"><table>
          <thead><tr>
            <th>Tid</th><th><span data-unit-label>${unitLabel()}</span></th>
            <th>Band p10–p90</th><th>Källa</th>
          </tr></thead>
          <tbody>${body}</tbody>
        </table></div>
      </details>`;
    })
    .join("");
}

/* ------------------------------------------------------------ accuracy */

function accuracyRows(metrics, modelId) {
  const buckets = Object.keys(metrics || {});
  return buckets.map((bucket) => ({ bucket, stats: metrics[bucket] }));
}

function renderAccuracySnapshot(zoneData) {
  const host = el("accuracy-snapshot");
  if (!host) return;
  const accuracy = zoneData.accuracy || {};
  const metrics = (accuracy.metrics || {})[accuracy.default_model || "ensemble"] || {};
  const usable = Object.values(metrics).filter((m) => m && m.enough_data);

  if (!usable.length) {
    host.innerHTML = `<p class="loading">För lite data ännu — träffsäkerhet visas när minst
      ${accuracy.min_samples || 24} utfall finns per horisont. Prognoser sparas vid varje körning
      och jämförs med Nord Pools utfall efterhand.</p>`;
    return;
  }
  window.PPOCharts.renderSnapshot(host, accuracy, state.unit);
}

/* ------------------------------------------------------------ index page */

async function initIndex() {
  const overview = await loadJSON("data/overview.json");
  state.overview = overview;
  if (!ZONES.includes(state.zone)) state.zone = "SE3";

  renderBanners(overview);
  renderFooterMeta(overview);
  const blurb = el("blurb");
  if (blurb) blurb.textContent = overview.blurb_sv || "";

  const overlayBox = el("overlay-models");
  if (overlayBox) {
    overlayBox.checked = state.overlay;
    overlayBox.addEventListener("change", () => {
      state.overlay = overlayBox.checked;
      writeStore(STORE.overlay, state.overlay ? "1" : "0");
      drawZone();
    });
  }

  renderTiles(overview, selectZone);
  bindUnitToggle(() => {
    renderTiles(overview, selectZone);
    markSelectedTile();
    drawZone();
  });

  await selectZone(state.zone);
}

async function ensureZoneData(zone) {
  if (!state.zoneData[zone]) {
    state.zoneData[zone] = await loadJSON(`data/${zone.toLowerCase()}.json`);
  }
  return state.zoneData[zone];
}

async function selectZone(zone) {
  state.zone = zone;
  writeStore(STORE.zone, zone);
  markSelectedTile();
  await drawZone();
}

async function drawZone() {
  const zoneData = await ensureZoneData(state.zone);
  const heading = el("chart-zone-name");
  if (heading) heading.textContent = `${zoneData.zone} · ${zoneData.zone_name}`;

  window.PPOCharts.renderMain(el("main-chart"), zoneData, {
    unit: state.unit,
    overlay: state.overlay,
    defaultModel: zoneData.default_model,
  });
  renderDrivers(zoneData);
  renderNextHours(zoneData);
  renderAccuracySnapshot(zoneData);
}

/* ------------------------------------------------------------ accuracy page */

async function initAccuracy() {
  const [overview, accuracy] = await Promise.all([
    loadJSON("data/overview.json"),
    loadJSON("data/accuracy.json"),
  ]);
  state.overview = overview;
  renderBanners(overview);
  renderFooterMeta(overview);

  const windowNote = el("accuracy-window");
  if (windowNote) {
    windowNote.textContent = `${accuracy.window_days} dygn · ${accuracy.scored_points.toLocaleString(
      "sv-SE"
    )} bedömda prognospunkter`;
  }

  const zoneSelect = el("acc-zone");
  const modelSelect = el("acc-model");
  zoneSelect.innerHTML =
    '<option value="ALL">Alla elområden</option>' +
    ZONES.map((z) => `<option value="${z}">${z}</option>`).join("");
  zoneSelect.value = ZONES.includes(state.zone) ? state.zone : "ALL";
  modelSelect.innerHTML = accuracy.models
    .map((m) => `<option value="${m}">${m}</option>`)
    .join("");
  modelSelect.value = accuracy.models.includes("ensemble") ? "ensemble" : accuracy.models[0];

  const draw = () => drawAccuracy(accuracy, zoneSelect.value, modelSelect.value);
  zoneSelect.addEventListener("change", draw);
  modelSelect.addEventListener("change", draw);
  bindUnitToggle(draw);
  draw();

  await drawHistory();
  zoneSelect.addEventListener("change", drawHistory);
}

function drawAccuracy(accuracy, zone, modelId) {
  const metrics = zone === "ALL" ? accuracy.overall : accuracy.zones[zone] || {};
  const table = accuracy.table[zone] || {};

  const host = el("accuracy-table");
  const rows = accuracyRows(metrics[modelId], modelId);
  if (!rows.length) {
    host.innerHTML = `<p class="loading">För lite data ännu för ${modelId} i ${
      zone === "ALL" ? "hela landet" : zone
    }.</p>`;
  } else {
    const body = rows
      .map(({ bucket, stats }) => {
        if (!stats.enough_data) {
          return `<tr><td>${bucketLabel(bucket)}</td><td class="num">${stats.n}</td>
            <td colspan="5" class="empty">för lite data</td></tr>`;
        }
        const skill = stats.skill_vs_naive;
        return `<tr>
          <td>${bucketLabel(bucket)}</td>
          <td class="num">${stats.n}</td>
          <td class="num">${fmtPrice(stats.mae)}</td>
          <td class="num">${fmtPrice(stats.rmse)}</td>
          <td class="num">${fmtPrice(stats.bias)}</td>
          <td class="num">${stats.coverage80 === null ? "–" : fmt(stats.coverage80 * 100, 0) + " %"}</td>
          <td class="num ${skill > 0 ? "best" : ""}">${skill === null || skill === undefined ? "–" : fmt(skill * 100, 0) + " %"}</td>
        </tr>`;
      })
      .join("");
    host.innerHTML = `<div class="table-scroll"><table>
      <thead><tr>
        <th>Horisont</th><th>n</th><th>MAE <span data-unit-label>${unitLabel()}</span></th>
        <th>RMSE</th><th>Bias</th><th>Täckning p10–p90</th><th>Skill vs naiv</th>
      </tr></thead><tbody>${body}</tbody></table></div>`;
  }

  window.PPOCharts.renderMaeBars(el("mae-chart"), table, accuracy.models, state.unit);
  window.PPOCharts.renderSkill(el("skill-chart"), metrics, accuracy.models, accuracy.reference_model);
}

async function drawHistory() {
  const host = el("history-chart");
  if (!host) return;
  const zoneSelect = el("acc-zone");
  const zone = zoneSelect && ZONES.includes(zoneSelect.value) ? zoneSelect.value : "SE3";
  const zoneData = await ensureZoneData(zone);

  const leadSelect = el("hist-lead");
  if (leadSelect && !leadSelect.options.length) {
    const leads = zoneData.history_lead_times_h || [24];
    leadSelect.innerHTML = leads
      .map((h) => `<option value="${h}">${h} h innan (dygn ${h / 24})</option>`)
      .join("");
    leadSelect.value = String(zoneData.history_default_lead_h || leads[0]);
    leadSelect.addEventListener("change", drawHistory);
  }
  const lead = leadSelect ? Number(leadSelect.value) : 24;

  const label = el("history-zone-name");
  if (label) label.textContent = `${zoneData.zone} · ${zoneData.zone_name}`;
  window.PPOCharts.renderHistory(host, zoneData, state.unit, lead);
}

/* ------------------------------------------------------------ models page */

async function initModels() {
  const [overview, models] = await Promise.all([
    loadJSON("data/overview.json"),
    loadJSON("data/models.json"),
  ]);
  renderBanners(overview);
  renderFooterMeta(overview);

  el("model-cards").innerHTML = models.models
    .map((model) => {
      const badges = [];
      if (model.is_default) badges.push('<span class="chip forecast">Standard</span>');
      if (model.is_reference) badges.push('<span class="chip">Referens</span>');
      if (model.derived) badges.push('<span class="chip">Härledd</span>');
      badges.push(
        model.quantiles
          ? '<span class="chip">p10–p90</span>'
          : '<span class="chip">endast punkt</span>'
      );
      return `<article class="card model-card">
        <div class="badges">${badges.join("")}</div>
        <h3>${model.name_sv}</h3>
        <span class="id">${model.id}</span>
        <p>${model.description_sv}</p>
      </article>`;
    })
    .join("");
}

/* ------------------------------------------------------------ static pages */

async function initStatic() {
  try {
    const overview = await loadJSON("data/overview.json");
    renderBanners(overview);
    renderFooterMeta(overview);
    const status = el("status-block");
    if (status) {
      status.innerHTML = `<div class="table-scroll"><table>
        <thead><tr><th>Källa</th><th>Status</th><th>Detalj</th></tr></thead>
        <tbody>${Object.entries(overview.sources)
          .map(
            ([name, info]) => `<tr>
              <td>${name}</td>
              <td>${info.ok ? '<span class="chip forecast">ok</span>' : '<span class="chip demo">fel</span>'}</td>
              <td class="muted">${info.error || info.rows || info.note || "–"}</td>
            </tr>`
          )
          .join("")}</tbody></table></div>`;
    }
  } catch (err) {
    console.warn("Kunde inte läsa status", err);
  }
}

/* ------------------------------------------------------------ boot */

const PAGES = {
  index: initIndex,
  accuracy: initAccuracy,
  models: initModels,
  static: initStatic,
};

function boot(page) {
  const init = PAGES[page] || initStatic;
  return init().catch((err) => {
    console.error(err);
    document.querySelectorAll(".loading").forEach((node) => {
      node.textContent = "Kunde inte läsa data. Kör pipelinen och ladda om sidan.";
    });
  });
}

// Exposed so a single-file preview build can drive the same code paths.
window.PPOApp = { boot, state };

document.addEventListener("DOMContentLoaded", () => {
  boot(document.body.dataset.page || "static");
});
