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

  function yAxisCeiling(actual, p50) {
    const values = actual.concat(p50).filter((v) => v !== null && v !== undefined);
    if (!values.length) return null;
    const top = Math.max(...values);
    const bottom = Math.min(...values, 0);
    // Twice the visible range leaves clear room above the median line for the
    // band without handing the axis over to its spikes.
    return Math.ceil((bottom + (top - bottom) * 2.2) / 10) * 10;
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
          // The calibrated band is asymmetric and its upper tail runs to two or
          // three times the level. Letting it set the axis squeezes the median
          // and the official price into the bottom fifth of the chart, which is
          // the part people actually read. The band still draws; it clips at the
          // top edge, and the tooltip carries the exact numbers.
          max: yAxisCeiling(actual, p50),
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

    // Thirty days of hours is too dense to read whole, so the view opens on the
    // last week and zooms. Keep the reader's window when only the lead time or
    // unit changes.
    const previous = (chart.getOption() || {}).dataZoom;
    const keep = chart.__historyLength === categories.length && previous && previous[0];
    chart.__historyLength = categories.length;
    // The series runs on into hours with no outcome yet; open on the week that
    // ends at the latest outcome rather than on empty future.
    let lastActual = -1;
    points.forEach((point, index) => {
      if (point.actual !== null && point.actual !== undefined) lastActual = index;
    });
    const span = Math.max(categories.length - 1, 1);
    const endIndex = lastActual >= 0 ? Math.min(span, lastActual + 6) : span;
    const startIndex = Math.max(0, endIndex - 7 * 24);
    const start = keep ? previous[0].start : (startIndex / span) * 100;
    const end = keep ? previous[0].end : (endIndex / span) * 100;

    const dayLabel = (value) =>
      new Date(value).toLocaleDateString("sv-SE", { day: "numeric", month: "short" });
    const stampLabel = (value) => {
      const date = new Date(value);
      return `${dayLabel(value)} ${String(date.getHours()).padStart(2, "0")}:00`;
    };

    chart.setOption(
      Object.assign(baseOptions(), {
        tooltip: Object.assign(baseOptions().tooltip, {
          trigger: "axis",
          valueFormatter: (value) => (value === null ? "–" : `${num(value)} ${unitLabel(unit)}`),
        }),
        dataZoom: [
          {
            type: "inside",
            xAxisIndex: 0,
            start,
            end,
            // Mouse zoom and pan are handled by bindWheelZoom and bindDragPan
            // below: ECharts' own did not respond to a mouse drag or wheel here.
            // What is left to it is touch — a two-finger pinch on a phone.
            zoomOnMouseWheel: false,
            moveOnMouseWheel: false,
            moveOnMouseMove: false,
            minValueSpan: 12,
          },
          {
            type: "slider",
            xAxisIndex: 0,
            start,
            end,
            height: 22,
            bottom: 6,
            minValueSpan: 12,
            borderColor: COLORS.grid,
            backgroundColor: "rgba(30, 42, 68, 0.35)",
            fillerColor: "rgba(94, 234, 212, 0.14)",
            handleStyle: { color: COLORS.accent, borderColor: COLORS.accent },
            moveHandleStyle: { color: COLORS.accent, opacity: 0.5 },
            dataBackground: {
              lineStyle: { color: COLORS.faint, opacity: 0.6 },
              areaStyle: { color: COLORS.grid, opacity: 0.4 },
            },
            textStyle: { color: COLORS.faint, fontSize: 10 },
            labelFormatter: (index) => (categories[index] ? dayLabel(categories[index]) : ""),
          },
        ],
        legend: {
          data: ["Utfall", `Prognos ${lead} h innan`],
          top: 0,
          textStyle: { color: COLORS.muted, fontSize: 11 },
          itemWidth: 14,
          itemHeight: 8,
        },
        grid: { left: 8, right: 12, top: 34, bottom: 40, containLabel: true },
        xAxis: Object.assign(axisCommon(), {
          type: "category",
          data: categories,
          boundaryGap: false,
          splitLine: { show: false },
          axisPointer: { label: { formatter: (params) => stampLabel(params.value) } },
          axisLabel: {
            color: COLORS.muted,
            fontSize: 11,
            hideOverlap: true,
            // Dates at midnight, hours in between once zoomed in far enough.
            formatter: (value) => {
              const date = new Date(value);
              return date.getHours() === 0
                ? dayLabel(value)
                : `${String(date.getHours()).padStart(2, "0")}:00`;
            },
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
    bindWheelZoom(node, chart);
    bindDragPan(node, chart);
    bindGestureZoom(node, chart);
    chart.resize();
  }

  // Drag inside the plot to move through time. Only the plot area: the slider
  // underneath has its own handles. Every listener is capture-phase: ECharts
  // stops mouse events from bubbling out of the canvas, so a bubbling listener
  // on window never saw the drag's movement.
  function bindDragPan(node, chart) {
    if (node.dataset.dragPan) return;
    node.dataset.dragPan = "1";
    let drag = null;
    node.addEventListener("mousedown", (event) => {
      if (event.button !== 0) return;
      const rect = node.getBoundingClientRect();
      const point = [event.clientX - rect.left, event.clientY - rect.top];
      if (!chart.containPixel({ gridIndex: 0 }, point)) return;
      const zoom = currentZoom(chart);
      const hours = Math.max((chart.__historyLength || 2) - 1, 1);
      const left = chart.convertToPixel({ xAxisIndex: 0 }, Math.round((zoom.start / 100) * hours));
      const right = chart.convertToPixel({ xAxisIndex: 0 }, Math.round((zoom.end / 100) * hours));
      const width = Math.max(right - left, 1);
      drag = { x: event.clientX, start: zoom.start, end: zoom.end, width };
      node.style.cursor = "grabbing";
      event.preventDefault();
    }, { capture: true });
    window.addEventListener("mousemove", (event) => {
      if (!drag) return;
      const shift = ((event.clientX - drag.x) / drag.width) * (drag.end - drag.start);
      zoomHistory(chart, drag.start - shift, drag.end - shift);
    }, { capture: true });
    window.addEventListener("mouseup", () => {
      if (!drag) return;
      drag = null;
      node.style.cursor = "";
    }, { capture: true });
  }

  /* Zoom the history window to [start, end] percent, keeping at least twelve
     hours in view and the window inside the data. */
  function zoomHistory(chart, start, end) {
    const hours = Math.max((chart.__historyLength || 2) - 1, 1);
    const span = Math.min(100, Math.max((12 / hours) * 100, end - start));
    let from = Math.max(0, Math.min(start, 100 - span));
    chart.dispatchAction({ type: "dataZoom", start: from, end: from + span });
  }

  function currentZoom(chart) {
    return ((chart.getOption() || {}).dataZoom || [])[0] || { start: 0, end: 100 };
  }

  // A trackpad pinch arrives as ctrl+wheel in Chrome, Edge and Firefox. Handled
  // here rather than by ECharts so the zoom centres on the pointer and a plain
  // wheel is left alone to scroll the page past the chart.
  function bindWheelZoom(node, chart) {
    if (node.dataset.wheelZoom) return;
    node.dataset.wheelZoom = "1";
    node.addEventListener(
      "wheel",
      (event) => {
        if (!event.ctrlKey) return;
        event.preventDefault();
        const zoom = currentZoom(chart);
        const width = zoom.end - zoom.start || 1;
        const hours = Math.max((chart.__historyLength || 2) - 1, 1);
        const index = chart.convertFromPixel(
          { xAxisIndex: 0 },
          event.clientX - node.getBoundingClientRect().left
        );
        const focus = Number.isFinite(index)
          ? Math.min(100, Math.max(0, (index / hours) * 100))
          : zoom.start + width / 2;
        const ratio = Math.min(1, Math.max(0, (focus - zoom.start) / width));
        // A trackpad sends many small deltas, a mouse wheel ~100 per notch;
        // this gives a smooth pinch and about 1.6x per notch.
        const span = width * Math.exp(event.deltaY * 0.004);
        const start = focus - span * ratio;
        zoomHistory(chart, start, start + span);
      },
      { passive: false }
    );
  }

  // Safari reports a trackpad pinch as gesture events rather than ctrl+wheel,
  // and would zoom the whole page instead of the chart.
  function bindGestureZoom(node, chart) {
    if (node.dataset.gestureZoom) return;
    node.dataset.gestureZoom = "1";
    let base = null;
    node.addEventListener("gesturestart", (event) => {
      event.preventDefault();
      const zoom = currentZoom(chart);
      base = { start: zoom.start, end: zoom.end };
    });
    node.addEventListener("gesturechange", (event) => {
      if (!base) return;
      event.preventDefault();
      const centre = (base.start + base.end) / 2;
      const span = (base.end - base.start) / event.scale;
      zoomHistory(chart, centre - span / 2, centre + span / 2);
    });
    node.addEventListener("gestureend", (event) => {
      event.preventDefault();
      base = null;
    });
  }

  /* ---------------------------------------------------------- long-term */

  // One bar per month for our forecast, its p10–p90 as a whisker on the bar,
  // the futures price as a diamond and last year's outcome as a grey dash.
  function renderLongterm(node, months, unit, modelId) {
    const chart = chartFor(node);
    if (!chart) return;
    const labels = months.map((m) => m.label.charAt(0).toUpperCase() + m.label.slice(1));
    const pick = (month, id, key = "p50") => scale(((month.models || {})[id] || {})[key], unit);
    // ECharts sizes the axis from the bar and scatter series only; the whisker
    // is a custom series and would run off the top into the legend.
    const values = months
      .flatMap((m) => [pick(m, modelId), pick(m, modelId, "p90"), pick(m, "lt_market"), scale(m.last_year, unit)])
      .filter((v) => Number.isFinite(v));
    // A round step, and a maximum on it, so the top label never lands on a tick.
    const top = values.length ? Math.max(...values) : null;
    const step = top !== null ? [5, 10, 20, 25, 50, 100, 200].find((s) => s >= (top * 1.08) / 5) || 500 : null;
    const yMax = step ? Math.ceil((top * 1.08) / step) * step : null;

    chart.setOption(
      Object.assign(baseOptions(), {
        tooltip: Object.assign(baseOptions().tooltip, {
          trigger: "axis",
          axisPointer: { type: "shadow" },
          formatter: (params) => {
            const index = params.length ? params[0].dataIndex : 0;
            const month = months[index];
            const line = (name, value) =>
              `<div>${name}: <b>${value === null || value === undefined ? "–" : `${num(value)} ${unitLabel(unit)}`}</b></div>`;
            return [
              `<div style="margin-bottom:4px">${labels[index]}</div>`,
              line("Vår prognos", pick(month, modelId)),
              line("Intervall p10", pick(month, modelId, "p10")),
              line("Intervall p90", pick(month, modelId, "p90")),
              line("Terminsmarknaden", pick(month, "lt_market")),
              line("Samma månad i fjol", scale(month.last_year, unit)),
            ].join("");
          },
        }),
        legend: {
          data: ["Vår prognos", "Terminsmarknaden", "Samma månad i fjol"],
          top: 0,
          textStyle: { color: COLORS.muted, fontSize: 11 },
          itemWidth: 14,
          itemHeight: 8,
        },
        grid: { left: 8, right: 12, top: 48, bottom: 8, containLabel: true },
        xAxis: Object.assign(axisCommon(), {
          type: "category",
          data: labels,
          splitLine: { show: false },
        }),
        yAxis: Object.assign(axisCommon(), {
          type: "value",
          min: 0,
          max: yMax,
          interval: step || undefined,
          name: unitLabel(unit),
          nameTextStyle: { color: COLORS.faint, fontSize: 11, align: "left" },
        }),
        series: [
          {
            name: "Vår prognos",
            type: "bar",
            barMaxWidth: 90,
            data: months.map((m) => pick(m, modelId)),
            itemStyle: { color: COLORS.accent, opacity: 0.8, borderRadius: [4, 4, 0, 0] },
          },
          {
            name: "Intervall",
            type: "custom",
            z: 5,
            data: months.map((m, i) => [i, pick(m, modelId, "p10"), pick(m, modelId, "p90")]),
            renderItem: (params, api) => {
              const low = api.value(1);
              const high = api.value(2);
              if (!Number.isFinite(low) || !Number.isFinite(high)) return null;
              const bottom = api.coord([api.value(0), low]);
              const top = api.coord([api.value(0), high]);
              const cap = api.size([1, 0])[0] * 0.1;
              const style = { stroke: COLORS.official, lineWidth: 1.6, opacity: 0.85 };
              return {
                type: "group",
                children: [
                  { type: "line", shape: { x1: bottom[0], y1: bottom[1], x2: top[0], y2: top[1] }, style },
                  { type: "line", shape: { x1: bottom[0] - cap, y1: bottom[1], x2: bottom[0] + cap, y2: bottom[1] }, style },
                  { type: "line", shape: { x1: top[0] - cap, y1: top[1], x2: top[0] + cap, y2: top[1] }, style },
                ],
              };
            },
          },
          {
            name: "Terminsmarknaden",
            type: "scatter",
            z: 6,
            symbol: "diamond",
            symbolSize: 16,
            data: months.map((m) => pick(m, "lt_market")),
            itemStyle: { color: COLORS.models[2], borderColor: "#0b1220", borderWidth: 1 },
          },
          {
            name: "Samma månad i fjol",
            type: "scatter",
            z: 4,
            symbol: "rect",
            symbolSize: [46, 4],
            data: months.map((m) => scale(m.last_year, unit)),
            itemStyle: { color: COLORS.muted, opacity: 0.9 },
          },
        ],
      }),
      { notMerge: true }
    );
    chart.resize();
  }

  window.PPOCharts = {
    renderMain,
    renderMaeBars,
    renderSkill,
    renderSnapshot,
    renderHistory,
    renderLongterm,
  };
})();
