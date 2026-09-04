/* ECharts renderers. Every chart re-uses one instance per container so unit and
   zone switches redraw instead of leaking canvases. */

(function () {
  const COLORS = {
    accent: "#5eead4",
    band: "rgba(94, 234, 212, 0.18)",
    official: "#f8fafc",
    grid: "#1e2a44",
    muted: "#93a0b8",
    faint: "#6b7a94",
    models: ["#60a5fa", "#c084fc", "#fbbf24", "#fb7185", "#34d399"],
  };

  const instances = new WeakMap();

  function chartFor(node) {
    if (!node) return null;
    let chart = instances.get(node);
    if (!chart) {
      chart = window.echarts.init(node, null, { renderer: "canvas" });
      instances.set(node, chart);
      window.addEventListener("resize", () => chart.resize());
    }
    return chart;
  }

  function scale(value, unit) {
    if (value === null || value === undefined) return null;
    if (unit !== "ore") return value;
    // Mirrors toDisplay() in app.js: öre is a currency conversion from EUR.
    const rate = (window.PPOApp && window.PPOApp.state.fx) || null;
    return rate ? (value * rate) / 10 : null;
  }

  function unitLabel(unit) {
    return unit === "ore" ? "öre/kWh" : "EUR/MWh";
  }

  function num(value, decimals = 1) {
    if (value === null || value === undefined || Number.isNaN(value)) return "–";
    return value.toLocaleString("sv-SE", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  }

  function baseOptions() {
    return {
      backgroundColor: "transparent",
      textStyle: { fontFamily: '"Source Sans 3", "IBM Plex Sans", system-ui, sans-serif' },
      animationDuration: 320,
      grid: { left: 8, right: 12, top: 18, bottom: 8, containLabel: true },
      tooltip: {
        backgroundColor: "rgba(14, 22, 38, 0.96)",
        borderColor: "#2b3b5e",
        borderWidth: 1,
        textStyle: { color: "#e8eefc", fontSize: 12 },
        padding: [10, 12],
      },
    };
  }

  function axisCommon() {
    return {
      axisLine: { lineStyle: { color: COLORS.grid } },
      axisTick: { show: false },
      axisLabel: { color: COLORS.muted, fontSize: 11 },
      splitLine: { lineStyle: { color: COLORS.grid, opacity: 0.55 } },
    };
  }

  // Two ticks per day: the day name at midnight and a bare "12" at noon. Six-hour
  // ticks put a wide day name directly between an 18 and an 06 and they collided.
  function isLabelledHour(iso) {
    const hour = new Date(iso).getHours();
    return hour === 0 || hour === 12;
  }

  function hourLabel(iso) {
    const date = new Date(iso);
    if (date.getHours() === 0) {
      return `{b|${date.toLocaleDateString("sv-SE", { weekday: "short", day: "numeric" })}}`;
    }
    return "12";
  }

  /* ---------------------------------------------------------- main chart */

  function renderMain(node, zoneData, opts) {
    const chart = chartFor(node);
    if (!chart) return;
    const { unit, overlay, defaultModel } = opts;

    const series = zoneData.series || [];
    const categories = series.map((point) => point.ts);
    const actual = series.map((point) => scale(point.actual, unit));

    const def = (point) => point.models[defaultModel] || {};
    const p50 = series.map((point) => scale(def(point).p50, unit));
    const lower = series.map((point) => scale(def(point).p10, unit));
    const range = series.map((point, index) => {
      const high = scale(def(point).p90, unit);
      return high === null || lower[index] === null ? null : high - lower[index];
    });

    // Where fact stops and forecast begins.
    let lastOfficial = -1;
    series.forEach((point, index) => {
      if (point.actual !== null && point.actual !== undefined) lastOfficial = index;
    });

    const nowIso = new Date();
    let nowIndex = series.findIndex((point) => new Date(point.ts) >= nowIso);
    if (nowIndex < 0) nowIndex = series.length - 1;

    const otherModels = (zoneData.models || []).filter((id) => id !== defaultModel);

    const chartSeries = [
      {
        name: "p10",
        type: "line",
        stack: "band",
        data: lower,
        symbol: "none",
        lineStyle: { opacity: 0 },
        silent: true,
        tooltip: { show: false },
        z: 1,
      },
      {
        name: "Osäkerhetsband p10–p90",
        type: "line",
        stack: "band",
        data: range,
        symbol: "none",
        lineStyle: { opacity: 0 },
        areaStyle: { color: COLORS.band },
        silent: true,
        tooltip: { show: false },
        z: 1,
      },
      {
        name: "Prognos (ensemble)",
        type: "line",
        data: p50,
        symbol: "none",
        connectNulls: false,
        lineStyle: { color: COLORS.accent, width: 2.2 },
        itemStyle: { color: COLORS.accent },
        z: 4,
        markLine: {
          silent: true,
          symbol: "none",
          label: {
            formatter: "nu",
            color: COLORS.muted,
            fontSize: 10,
            position: "insideEndTop",
            rotate: 0,
            padding: [0, 0, 4, 0],
          },
          lineStyle: { color: COLORS.faint, type: "dashed", width: 1 },
          data: [{ xAxis: categories[nowIndex] }],
        },
        markArea:
          lastOfficial >= 0
            ? {
                silent: true,
                itemStyle: { color: "rgba(248, 250, 252, 0.045)" },
                label: {
                  show: true,
                  position: "insideTopLeft",
                  color: COLORS.faint,
                  fontSize: 10,
                  formatter: "officiellt publicerat",
                },
                data: [[{ xAxis: categories[0] }, { xAxis: categories[lastOfficial] }]],
              }
            : undefined,
      },
      {
        name: "Officiellt pris",
        type: "line",
        step: "end",
        data: actual,
        symbol: "none",
        connectNulls: false,
        lineStyle: { color: COLORS.official, width: 2 },
        itemStyle: { color: COLORS.official },
        z: 5,
      },
    ];

    if (overlay) {
      otherModels.forEach((id, index) => {
        chartSeries.push({
          name: id,
          type: "line",
          data: series.map((point) => scale((point.models[id] || {}).p50, unit)),
          symbol: "none",
          connectNulls: false,
          lineStyle: { color: COLORS.models[index % COLORS.models.length], width: 1.2, opacity: 0.85 },
          itemStyle: { color: COLORS.models[index % COLORS.models.length] },
          z: 3,
        });
      });
    }

    chart.setOption(
      Object.assign(baseOptions(), {
        tooltip: Object.assign(baseOptions().tooltip, {
          trigger: "axis",
          axisPointer: { type: "line", lineStyle: { color: COLORS.faint } },
          formatter(params) {
            if (!params.length) return "";
            const index = params[0].dataIndex;
            const point = series[index];
            const date = new Date(point.ts);
            const head = date.toLocaleString("sv-SE", {
              weekday: "short",
              day: "numeric",
              month: "short",
              hour: "2-digit",
              minute: "2-digit",
            });
            const lines = [`<div style="font-weight:600;margin-bottom:4px">${head}</div>`];

            if (point.actual !== null && point.actual !== undefined) {
              const tag = point.source === "demo" ? "Demo" : "Officiellt";
              lines.push(
                `<div><span style="color:${COLORS.official}">●</span> ${tag}: <b>${num(
                  scale(point.actual, unit)
                )}</b> ${unitLabel(unit)}</div>`
              );
            }
            (zoneData.models || []).forEach((id) => {
              const model = point.models[id];
              if (!model) return;
              const isDefault = id === defaultModel;
              const color = isDefault
                ? COLORS.accent
                : COLORS.models[otherModels.indexOf(id) % COLORS.models.length];
              const band =
                model.p10 === null || model.p90 === null
                  ? ""
                  : ` <span style="color:${COLORS.faint}">(${num(scale(model.p10, unit))}–${num(
                      scale(model.p90, unit)
                    )})</span>`;
              lines.push(
                `<div><span style="color:${color}">●</span> ${id}: ${num(
                  scale(model.p50, unit)
                )}${band}</div>`
              );
            });
            return lines.join("");
          },
        }),
        grid: { left: 8, right: 12, top: 40, bottom: 8, containLabel: true },
        xAxis: Object.assign(axisCommon(), {
          type: "category",
          data: categories,
          boundaryGap: false,
          splitLine: { show: false },
          axisLabel: {
            color: COLORS.muted,
            fontSize: 11,
            // Day names are wider than hour labels and used to collide with the
            // neighbouring 18:00 and 06:00 ticks; hideOverlap drops the loser.
            hideOverlap: true,
            interval: (index) => isLabelledHour(categories[index]),
            formatter: hourLabel,
            rich: { b: { color: "#e8eefc", fontWeight: 600, fontSize: 11, padding: [0, 4, 0, 4] } },
          },
        }),
        yAxis: Object.assign(axisCommon(), {
          type: "value",
          scale: true,
          name: unitLabel(unit),
          nameLocation: "end",
          nameTextStyle: { color: COLORS.faint, fontSize: 11, align: "left" },
          nameGap: 18,
        }),
        series: chartSeries,
      }),
      { notMerge: true }
    );
    chart.resize();
  }

  /* ---------------------------------------------------------- accuracy */

  function renderMaeBars(node, table, models, unit) {
    const chart = chartFor(node);
    if (!chart) return;
    const buckets = Object.keys(table);
    const hasData = buckets.some((bucket) =>
      models.some((model) => table[bucket] && table[bucket][model] !== null)
    );

    if (!hasData) {
      chart.clear();
      chart.setOption(
        Object.assign(baseOptions(), {
          title: {
            text: "För lite data ännu",
            subtext: "MAE per horisont visas när utfallen hunnit komma in.",
            left: "center",
            top: "middle",
            textStyle: { color: COLORS.faint, fontSize: 14, fontWeight: 500 },
            subtextStyle: { color: COLORS.faint, fontSize: 12 },
          },
        }),
        { notMerge: true }
      );
      return;
    }

    chart.setOption(
      Object.assign(baseOptions(), {
        tooltip: Object.assign(baseOptions().tooltip, { trigger: "axis", axisPointer: { type: "shadow" } }),
        legend: {
          data: models,
          top: 0,
          textStyle: { color: COLORS.muted, fontSize: 11 },
          itemWidth: 12,
          itemHeight: 8,
        },
        grid: { left: 8, right: 12, top: 34, bottom: 8, containLabel: true },
        xAxis: Object.assign(axisCommon(), {
          type: "category",
          data: buckets.map((b) => b.replace("-", "–")),
          splitLine: { show: false },
        }),
        yAxis: Object.assign(axisCommon(), {
          type: "value",
          name: `MAE ${unitLabel(unit)}`,
          nameTextStyle: { color: COLORS.faint, fontSize: 11, align: "left" },
        }),
        series: models.map((model, index) => ({
          name: model,
          type: "bar",
          data: buckets.map((bucket) => scale((table[bucket] || {})[model], unit)),
          itemStyle: {
            color: model === "ensemble" ? COLORS.accent : COLORS.models[index % COLORS.models.length],
            borderRadius: [3, 3, 0, 0],
          },
          barMaxWidth: 26,
        })),
      }),
      { notMerge: true }
    );
    chart.resize();
  }

  function renderSkill(node, metrics, models, referenceModel) {
    const chart = chartFor(node);
    if (!chart) return;
    const scored = models.filter((model) => model !== referenceModel);
    const buckets = new Set();
    scored.forEach((model) => Object.keys(metrics[model] || {}).forEach((b) => buckets.add(b)));
    const labels = Array.from(buckets);

    const hasData = scored.some((model) =>
      labels.some((bucket) => {
        const stats = (metrics[model] || {})[bucket];
        return stats && stats.enough_data && stats.skill_vs_naive !== null;
      })
    );

    if (!hasData) {
      chart.clear();
      chart.setOption(
        Object.assign(baseOptions(), {
          title: {
            text: "För lite data ännu",
            subtext: `Skill mäts mot ${referenceModel} när båda har utfall att jämföra.`,
            left: "center",
            top: "middle",
            textStyle: { color: COLORS.faint, fontSize: 14, fontWeight: 500 },
            subtextStyle: { color: COLORS.faint, fontSize: 12 },
          },
        }),
        { notMerge: true }
      );
      return;
    }

    chart.setOption(
      Object.assign(baseOptions(), {
        tooltip: Object.assign(baseOptions().tooltip, {
          trigger: "axis",
          valueFormatter: (value) => (value === null ? "–" : `${num(value, 0)} %`),
        }),
        legend: { data: scored, top: 0, textStyle: { color: COLORS.muted, fontSize: 11 }, itemWidth: 12, itemHeight: 8 },
        grid: { left: 8, right: 12, top: 34, bottom: 8, containLabel: true },
        xAxis: Object.assign(axisCommon(), {
          type: "category",
          data: labels.map((b) => b.replace("-", "–")),
          splitLine: { show: false },
        }),
        yAxis: Object.assign(axisCommon(), {
          type: "value",
          name: "Skill mot naiv (%)",
          nameTextStyle: { color: COLORS.faint, fontSize: 11, align: "left" },
        }),
        series: scored.map((model, index) => ({
          name: model,
          type: "bar",
          data: labels.map((bucket) => {
            const stats = (metrics[model] || {})[bucket];
            if (!stats || !stats.enough_data || stats.skill_vs_naive === null) return null;
            return stats.skill_vs_naive * 100;
          }),
          itemStyle: {
            color: model === "ensemble" ? COLORS.accent : COLORS.models[index % COLORS.models.length],
            borderRadius: [3, 3, 0, 0],
          },
          barMaxWidth: 26,
        })),
      }),
      { notMerge: true }
    );
    chart.resize();
  }

  function renderSnapshot(host, accuracy, unit) {
    host.innerHTML =
      '<div class="section-title"><h2>Träffsäkerhet just nu</h2>' +
      `<span class="meta">MAE per horisont · ${accuracy.window_days} dygn · ` +
      `<a href="traffsakerhet.html">se allt</a></span></div>` +
      '<div class="chart small" id="snapshot-chart"></div>';
    const table = accuracy.table || {};
    renderMaeBars(document.getElementById("snapshot-chart"), table, [accuracy.default_model || "ensemble"], unit);
  }

  /* ---------------------------------------------------------- history */

  function renderHistory(node, zoneData, unit, leadTime) {
    const chart = chartFor(node);
    if (!chart) return;
    const lead = String(leadTime || zoneData.history_default_lead_h || 24);
    const points = zoneData.history || [];
    if (!points.length) {
      chart.clear();
      chart.setOption(
        Object.assign(baseOptions(), {
          title: {
            text: "Ingen historik ännu",
            left: "center",
            top: "middle",
            textStyle: { color: COLORS.faint, fontSize: 14, fontWeight: 500 },
          },
        }),
        { notMerge: true }
      );
      return;
    }

    const categories = points.map((point) => point.ts);
    chart.setOption(
      Object.assign(baseOptions(), {
        tooltip: Object.assign(baseOptions().tooltip, {
          trigger: "axis",
          valueFormatter: (value) => (value === null ? "–" : `${num(value)} ${unitLabel(unit)}`),
        }),
        legend: {
          data: ["Utfall", `Prognos ${lead} h innan`],
          top: 0,
          textStyle: { color: COLORS.muted, fontSize: 11 },
          itemWidth: 14,
          itemHeight: 8,
        },
        grid: { left: 8, right: 12, top: 34, bottom: 8, containLabel: true },
        xAxis: Object.assign(axisCommon(), {
          type: "category",
          data: categories,
          boundaryGap: false,
          splitLine: { show: false },
          axisLabel: {
            color: COLORS.muted,
            fontSize: 11,
            interval: (index) => new Date(categories[index]).getHours() === 0,
            formatter: (value) =>
              new Date(value).toLocaleDateString("sv-SE", { day: "numeric", month: "short" }),
          },
        }),
        yAxis: Object.assign(axisCommon(), { type: "value", scale: true, name: unitLabel(unit),
          nameTextStyle: { color: COLORS.faint, fontSize: 11, align: "left" } }),
        series: [
          {
            name: "Utfall",
            type: "line",
            data: points.map((point) => scale(point.actual, unit)),
            symbol: "none",
            lineStyle: { color: COLORS.official, width: 1.6 },
            itemStyle: { color: COLORS.official },
          },
          {
            name: `Prognos ${lead} h innan`,
            type: "line",
            data: points.map((point) => scale((point.forecast || {})[lead], unit)),
            symbol: "none",
            connectNulls: false,
            lineStyle: { color: COLORS.accent, width: 1.6, opacity: 0.9 },
            itemStyle: { color: COLORS.accent },
          },
        ],
      }),
      { notMerge: true }
    );
    chart.resize();
  }

  window.PPOCharts = { renderMain, renderMaeBars, renderSkill, renderSnapshot, renderHistory };
})();
