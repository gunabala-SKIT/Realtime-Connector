/* Overview: KPI tiles, previous-period comparison, production flow, stage table, trend, alerts. */
(() => {
  const { $, esc, api, qs, fmtDur, fmtDelta, fmtNum, fmtPct, fmtTime, ago, badge, tile, empty, delta, barChart, progress, label } = MPI;
  const KIND_LABEL = { bottleneck: "Bottleneck", delay: "Delayed", idle: "Idle", quality: "Quality", low_production: "Below target" };
  const KIND_CLASS = { bottleneck: "crit", delay: "warn", idle: "warn", quality: "crit", low_production: "info" };

  function stageState(s, alerts, bottleneck) {
    const mine = alerts.filter((a) => a.station_id === s.id);
    const kinds = new Set(mine.map((a) => a.kind));
    if (s.kind === "process" && s.waiting > s.expected_wait_qty) kinds.add("bottleneck");
    if (bottleneck && bottleneck.station_id === s.id) kinds.add("bottleneck");
    const cls = kinds.has("bottleneck") ? "bottleneck" : kinds.has("quality") ? "quality" : kinds.has("delay") ? "delayed" : kinds.has("idle") ? "idle" : "";
    return { kinds: [...kinds], cls };
  }

  function stageCard(s, st) {
    const chips = st.kinds.map((k) => badge(KIND_LABEL[k] || k, KIND_CLASS[k] || "")).join("") || (s.kind === "process" ? badge("OK", "ok") : "");
    const exp = s.expected_process_min ? ` <span class="muted">/ ${fmtNum(s.expected_process_min, 1)} min exp.</span>` : "";
    const rows = s.kind === "process" ? `
      <div class="kv"><span>Out</span><b>${fmtNum(s.completed)}</b></div>
      <div class="kv"><span>Rejected</span><b>${fmtNum(s.rejected)}</b></div>
      <div class="kv"><span>Waiting</span><b>${fmtNum(s.waiting)}</b></div>
      <div class="kv"><span>Processing</span><b>${fmtNum(s.processing)}</b></div>
      <div class="kv"><span>Avg process</span><b>${s.avg_processing_s == null ? "–" : fmtDur(s.avg_processing_s)}</b></div>
      <div class="kv"><span>Avg wait</span><b>${s.avg_waiting_s == null ? "–" : fmtDur(s.avg_waiting_s)}</b></div>
      <div class="kv"><span>Per hour</span><b>${fmtNum(s.throughput_per_h, 1)}</b></div>
      ${s.utilization_pct == null ? "" : `<div class="kv"><span>Utilisation</span><b>${fmtPct(s.utilization_pct)}</b></div>`}`
      : s.kind === "sink" ? `<div class="kv"><span>Waiting</span><b>${fmtNum(s.waiting)}</b></div>` : "";
    const heading = s.kind === "source" ? "Issued" : s.kind === "sink" ? "Finished" : "In";
    return `<div class="stage ${st.cls}" title="${esc(s.name)}">
      <div class="sname">${esc(s.name)}</div>
      <div><div class="big">${fmtNum(s.entered)}</div><div class="sub">${heading}${exp}</div></div>
      ${rows}<div class="chips">${chips}</div></div>`;
  }

  function flow(stations, alerts, bottleneck) {
    if (!stations.length) return empty("No stations on this line yet. Add them in Setup.");
    const arrow = '<svg viewBox="0 0 30 12"><path d="M0 6h26M21 1l5 5-5 5"/></svg>';
    return `<div class="flow">${stations.map((s, i) => {
      const card = stageCard(s, stageState(s, alerts, bottleneck));
      const next = stations[i + 1];
      const link = next ? `<div class="link">${arrow}<span class="n">${fmtNum(s.kind === "source" ? s.completed : s.completed)}</span></div>` : "";
      return card + link;
    }).join("")}</div>`;
  }

  function stageTable(stations) {
    const rows = stations.filter((s) => s.kind === "process").map((s) => {
      const late = s.expected_process_min && s.avg_processing_s && s.avg_processing_s > s.expected_process_min * 60 * 1.15;
      return `<tr><td><b>${esc(s.name)}</b></td><td class="num">${fmtNum(s.entered)}</td><td class="num">${fmtNum(s.completed)}</td>
        <td class="num">${fmtNum(s.rejected)}</td><td class="num">${fmtNum(s.waiting)} <span class="muted">/ ${s.expected_wait_qty}</span></td>
        <td class="num">${fmtNum(s.processing)}</td>
        <td class="num" ${late ? 'style="color:var(--crit);font-weight:600"' : ""}>${s.avg_processing_s == null ? "–" : fmtDur(s.avg_processing_s)}${s.expected_process_min ? ` <span class="muted">/ ${fmtNum(s.expected_process_min, 1)} min</span>` : ""}</td>
        <td class="num">${s.avg_waiting_s == null ? "–" : fmtDur(s.avg_waiting_s)}</td><td class="num">${fmtNum(s.peak_waiting)}</td>
        <td class="num">${fmtNum(s.throughput_per_h, 1)}${s.target_units_per_h ? ` <span class="muted">/ ${fmtNum(s.target_units_per_h)}</span>` : ""}</td>
        <td class="num">${fmtPct(s.utilization_pct)}</td><td class="num">${fmtPct(s.efficiency_pct)}</td>
        <td class="num" ${s.rejection_rate_pct > s.reject_threshold_pct ? 'style="color:var(--crit);font-weight:600"' : ""}>${fmtPct(s.rejection_rate_pct)}</td></tr>`;
    }).join("");
    return `<div class="scroll-x"><table class="data"><thead><tr><th>Stage</th><th class="num">Entered</th><th class="num">Completed</th><th class="num">Rejected</th>
      <th class="num">Waiting / exp.</th><th class="num">Processing</th><th class="num">Avg process / exp.</th><th class="num">Avg wait</th><th class="num">Peak queue</th>
      <th class="num">Per hour / target</th><th class="num">Utilisation</th><th class="num">Efficiency</th><th class="num">Reject rate</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function comparison(o) {
    if (!o.previous) return "";
    const k = o.kpis, p = o.previous.kpis, w = o.previous.window;
    const row = (name, a, b, good, fmt = (x) => fmtNum(x, 1), av = fmt(a), bv = fmt(b)) =>
      `<tr><td>${name}</td><td class="num">${av}</td><td class="num muted">${bv}</td><td class="num">${delta(a, b, good, fmt)}</td></tr>`;
    return `<div class="card"><div class="card-head"><span>Compared with ${esc(w.label)}</span><span class="badge">like for like</span></div>
      <table class="data"><thead><tr><th>Metric</th><th class="num">${esc(o.window.label)}</th><th class="num">${esc(w.label)}</th><th class="num">Change</th></tr></thead><tbody>
      ${row("Started", k.issued, p.issued, "up", (x) => fmtNum(x, 0))}
      ${row("Finished", k.finished, p.finished, "up", (x) => fmtNum(x, 0))}
      ${row("Rejected", k.rejected, p.rejected, "down", (x) => fmtNum(x, 0))}
      ${row("Avg production time", k.avg_cycle_s, p.avg_cycle_s, "down", fmtDur)}
      ${row("Yield", k.yield_pct, p.yield_pct, "up", (x) => fmtNum(x, 1) + "%")}
      ${row("On-time", k.on_time_pct, p.on_time_pct, "up", (x) => fmtNum(x, 1) + "%")}
      </tbody></table>
      ${o.previous.changes.length && o.previous.changes[0].added_time_per_unit_s > 60 ? `<p class="note" style="margin-top:10px">Biggest change per unit: <b>${esc(o.previous.changes[0].station)}</b> (${fmtDelta(o.previous.changes[0].added_time_per_unit_s)} processing + waiting).</p>` : ""}</div>`;
  }

  function alertList(alerts) {
    if (!alerts.length) return empty("No active alerts.");
    return `<div class="stack">${alerts.map((a) => `<div class="alert ${esc(a.severity)}"><div><div class="msg">${esc(a.message)}</div>
      <div class="ev">${badge(KIND_LABEL[a.kind] || a.kind, KIND_CLASS[a.kind] || "")} opened ${ago(a.opened_at)}${a.status === "ack" ? " · acknowledged" : ""}</div></div></div>`).join("")}</div>`;
  }

  MPI.register("overview", {
    title: "Overview", every: 5000,
    init(root) {
      root.innerHTML = `<div id="ov-empty"></div><div class="tiles" id="ov-tiles"></div><div id="ov-cmp"></div>
        <div class="card"><div class="card-head"><span>Production flow</span><span class="muted small" id="ov-flowsub"></span></div><div id="ov-flow"></div></div>
        <div class="split"><div class="card"><div class="card-head"><span>Production over time</span></div><div id="ov-trend"></div></div>
        <div class="card"><div class="card-head"><span>Active alerts</span><a href="#alerts" class="btn-link">All alerts</a></div><div id="ov-alerts"></div></div></div>
        <div class="card"><div class="card-head"><span>Stage details</span></div><div id="ov-table"></div></div>
        <div class="note" id="ov-quality"></div>`;
    },
    async load(quiet) {
      if (!MPI.S.lineId) { $("#ov-empty").innerHTML = `<div class="card">${empty("No production line yet. Open Setup to create one, or run the demo seeder.")}</div>`; return; }
      $("#ov-empty").innerHTML = "";
      const [o, trend] = await Promise.all([api("/overview" + qs()), api("/trend" + qs({ product_id: MPI.S.productId || undefined }))]);
      const k = o.kpis, w = o.window;
      $("#f-window").textContent = `${o.line.name} · ${w.label}${w.live ? " (in progress)" : ""}`;
      const tgt = k.target_cycle_s;
      const cycleKind = k.cycle_diff_s == null ? "" : k.cycle_diff_s > 0 ? "warn" : "good";
      const b = o.bottleneck;
      $("#ov-tiles").innerHTML =
        tile("Raw material", fmtNum(k.raw_material), "issued to the line") +
        tile("In production", fmtNum(k.in_production), "on the floor now") +
        tile("Finished goods", fmtNum(k.finished), `${fmtNum(k.throughput_per_h, 1)} per active hour`, "good") +
        tile("Rejected", fmtNum(k.rejected), `${fmtPct(k.rejection_rate_pct)} of decided units`, k.rejected ? "warn" : "") +
        tile("Avg production time", k.avg_cycle_s == null ? "–" : fmtDur(k.avg_cycle_s),
          tgt ? `target ${fmtDur(tgt)} · <span class="${k.cycle_diff_s > 0 ? "" : ""}">${fmtDelta(k.cycle_diff_s)}</span>` : "no target set", cycleKind) +
        tile("Yield", fmtPct(k.yield_pct), `${fmtNum(k.cohort.in_progress)} of ${fmtNum(k.cohort.started)} started still in production`) +
        tile("Bottleneck", b ? esc(b.station) : "None", b ? `${b.level === "bottleneck" ? "" : "potential · "}${b.waiting} waiting (expected ≤ ${b.expected_waiting})` : "no congestion right now", b ? "crit" : "good");
      $("#ov-cmp").innerHTML = comparison(o);
      $("#ov-flowsub").textContent = b ? b.evidence : "";
      $("#ov-flow").innerHTML = o.flows.length > 1
        ? o.flows.map((f) => `<div class="small muted" style="margin:6px 2px 2px"><b>${esc(f.product_code)}</b> · ${esc(f.product_name)}</div>${flow(f.stations, o.alerts, b)}`).join("")
        : flow((o.flows[0] || { stations: o.stations }).stations, o.alerts, b);
      $("#ov-table").innerHTML = stageTable(o.stations);
      $("#ov-alerts").innerHTML = alertList(o.alerts);
      const rows = trend.map((t) => ({ ...t, label: new Date(t.start * 1000).toLocaleString([], t.step_s >= 86400 ? { month: "short", day: "numeric" } : { hour: "2-digit", minute: "2-digit" }) }));
      $("#ov-trend").innerHTML = barChart(rows, [{ key: "issued", name: "Started", color: "#9fb8e8" }, { key: "finished", name: "Finished", color: "#1a7f37" }, { key: "rejected", name: "Rejected", color: "#cf222e" }]);
      const q = o.data_quality;
      $("#ov-quality").innerHTML = q.events ? `Data quality (${esc(w.label)}): ${fmtNum(q.events)} events, ${fmtNum(q.applied)} applied, ${fmtNum(q.ignored)} ignored, ${fmtNum(q.unmatched)} unmatched, ${fmtNum(q.errors)} errors` +
        `${q.units_with_skipped_steps ? `, ${q.units_with_skipped_steps} units with missed steps` : ""}. Refused events are listed in <a href="#operator">Operator</a>.` : "No events in this period.";
      if (!k.issued && !k.in_production && !k.finished) $("#ov-empty").innerHTML = `<div class="card">${empty("No production recorded in this period yet.")}</div>`;
    },
  });
})();
