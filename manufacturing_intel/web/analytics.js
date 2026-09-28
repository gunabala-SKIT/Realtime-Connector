/* Analytics: comparison, stage ranking, drill-down breakdown, rejection reasons, trend. */
(() => {
  const { $, esc, api, qs, fmtDur, fmtDelta, fmtNum, fmtPct, delta, badge, empty, barChart, progress, label, fmtDateTime } = MPI;
  const CHAIN = ["day", "shift", "line", "product", "batch"];
  const CHAIN_NAME = { day: "Day", shift: "Shift", line: "Line", product: "Product", batch: "Batch" };
  let path = []; // drill-down: [{by, key, label}]

  const METRICS = [
    ["issued", "Started", "up", (x) => fmtNum(x, 0)], ["finished", "Finished", "up", (x) => fmtNum(x, 0)], ["rejected", "Rejected", "down", (x) => fmtNum(x, 0)],
    ["rejection_rate_pct", "Rejection rate", "down", (x) => fmtNum(x, 1) + "%"], ["yield_pct", "Yield", "up", (x) => fmtNum(x, 1) + "%"],
    ["avg_cycle_s", "Avg production time", "down", fmtDur], ["avg_processing_total_s", "Avg processing per unit", "down", fmtDur],
    ["avg_waiting_total_s", "Avg waiting per unit", "down", fmtDur], ["on_time_pct", "On-time", "up", (x) => fmtNum(x, 1) + "%"],
    ["throughput_per_h", "Throughput / active hour", "up", (x) => fmtNum(x, 1)],
  ];

  function filtersFor(by) {
    const S = MPI.S, f = {};
    for (const p of path) {
      if (p.by === "day") { f.period = "custom"; f.start = p.key; f.end = p.key; }
      if (p.by === "shift") f.shift = p.key;
      if (p.by === "line") f.line_id = p.key;
      if (p.by === "product") f.product_id = p.key;
      if (p.by === "batch") f.batch_id = p.key;
    }
    return f;
  }
  function crumbs() {
    const parts = [`<button class="btn-link" data-pop="0">All</button>`];
    path.forEach((p, i) => parts.push(`<span class="muted">›</span> <button class="btn-link" data-pop="${i + 1}">${esc(CHAIN_NAME[p.by])}: ${esc(p.label)}</button>`));
    return parts.join(" ");
  }

  async function breakdown() {
    const by = CHAIN[path.length];
    const f = filtersFor(by);
    const box = $("#an-drill");
    if (!by) {                                   // end of the chain: the units themselves
      const p = new URLSearchParams({ limit: 50 });
      const S = MPI.S, line = f.line_id || S.lineId, prod = f.product_id || S.productId;
      if (line) p.set("line_id", line); if (prod) p.set("product_id", prod); if (f.batch_id) p.set("batch_id", f.batch_id);
      const r = await api("/units?" + p);
      $("#an-crumbs").innerHTML = crumbs();
      box.innerHTML = r.units.length ? `<table class="data"><thead><tr><th>Unit</th><th>Status</th><th>Started</th><th class="num">Production time</th></tr></thead><tbody>
        ${r.units.map((u) => `<tr class="click" data-unit="${esc(u.code)}"><td><b>${esc(u.code)}</b></td><td>${badge(label(u.status), u.status === "finished" ? "ok" : u.status === "rejected" ? "crit" : "info")}</td>
        <td>${fmtDateTime(u.created_at)}</td><td class="num">${u.cycle_s ? fmtDur(u.cycle_s) : "–"}</td></tr>`).join("")}</tbody></table>` : empty("No units.");
      return;
    }
    const q = new URLSearchParams({ by });
    const S = MPI.S;
    const period = f.period || S.period;
    q.set("period", period);
    if (period === "custom") { q.set("start", f.start || S.start); q.set("end", f.end || S.end); }
    const line = "line_id" in f ? f.line_id : by === "line" ? "" : S.lineId;
    const prod = "product_id" in f ? f.product_id : by === "product" ? "" : S.productId;
    if (line) q.set("line_id", line);
    if (prod) q.set("product_id", prod);
    if (f.batch_id) q.set("batch_id", f.batch_id);
    if (f.shift) q.set("shift", f.shift);
    const rows = await api("/breakdown?" + q);
    $("#an-crumbs").innerHTML = crumbs();
    $("#an-drill-title").textContent = `By ${CHAIN_NAME[by].toLowerCase()} · click a row to drill down`;
    box.innerHTML = rows.length ? `<div class="scroll-x"><table class="data"><thead><tr><th>${CHAIN_NAME[by]}</th><th class="num">Started</th><th class="num">Finished</th><th class="num">Rejected</th>
      <th class="num">In production</th><th class="num">Avg production time</th><th class="num">Yield</th></tr></thead><tbody>
      ${rows.map((r) => `<tr class="click" data-key="${esc(r.key)}" data-label="${esc(r.label)}"><td><b>${esc(r.label)}</b></td><td class="num">${fmtNum(r.started)}</td><td class="num">${fmtNum(r.finished)}</td>
        <td class="num">${fmtNum(r.rejected)}</td><td class="num">${fmtNum(r.in_production)}</td><td class="num">${r.avg_cycle_s == null ? "–" : fmtDur(r.avg_cycle_s)}</td><td class="num">${fmtPct(r.yield_pct)}</td></tr>`).join("")}
      </tbody></table></div><p class="note" style="margin-top:8px">Units are grouped by when they were <b>started</b>; "Finished" counts those of them that have finished since.</p>` : empty("Nothing was started in this period.");
  }

  MPI.register("analytics", {
    title: "Analytics",
    init(root) {
      root.innerHTML = `<div class="card"><div class="card-head"><span>Compare</span>
          <label class="row small muted" style="gap:6px">against <select id="an-vs"></select></label></div><div id="an-cmp"></div></div>
        <div class="split"><div class="card"><div class="card-head"><span>Where the time went</span><span class="muted small" id="an-rank-sub"></span></div><div id="an-rank"></div></div>
        <div class="card"><div class="card-head"><span>Rejections by stage and reason</span></div><div id="an-rej"></div></div></div>
        <div class="card"><div class="card-head"><span>Production over time</span></div><div id="an-trend"></div></div>
        <div class="card"><div class="card-head"><span id="an-drill-title">Breakdown</span></div><div id="an-crumbs" class="row" style="margin-bottom:10px"></div><div id="an-drill"></div></div>`;
      $("#an-vs").addEventListener("change", () => this.load());
      $("#an-crumbs").addEventListener("click", (e) => { const b = e.target.closest("[data-pop]"); if (b) { path = path.slice(0, +b.dataset.pop); breakdown(); } });
      $("#an-drill").addEventListener("click", (e) => {
        const u = e.target.closest("tr[data-unit]");
        if (u) { MPI.S.openUnit = u.dataset.unit; location.hash = "units"; return; }
        const tr = e.target.closest("tr[data-key]");
        if (tr) { path.push({ by: CHAIN[path.length], key: tr.dataset.key, label: tr.dataset.label }); breakdown(); }
      });
    },
    async load() {
      const S = MPI.S, cfg = S.cfg;
      const sel = $("#an-vs"), keep = sel.value;
      const others = cfg.lines.filter((l) => String(l.id) !== String(S.lineId));
      sel.innerHTML = '<option value="">the previous period (like for like)</option>' +
        Object.entries(MPI.PERIOD_NAMES).filter(([k]) => k !== "custom" && k !== S.period).map(([k, n]) => `<option value="p:${k}">${n}</option>`).join("") +
        others.map((l) => `<option value="l:${l.id}">${esc(l.name)} (same period)</option>`).join("");
      sel.value = [...sel.options].some((o) => o.value === keep) ? keep : "";
      const vs = sel.value;
      const cq = new URLSearchParams({ a: S.period, line_id: S.lineId });
      if (S.period === "custom") { cq.set("start_a", S.start); cq.set("end_a", S.end); }
      if (S.productId) cq.set("product_id", S.productId);
      if (vs.startsWith("p:")) cq.set("b", vs.slice(2));
      if (vs.startsWith("l:")) cq.set("line_b", vs.slice(2));
      const [c, rej, trend] = await Promise.all([api("/compare?" + cq), api("/rejections" + qs()), api("/trend" + qs())]);
      const nameA = vs.startsWith("l:") ? cfg.lines.find((l) => String(l.id) === String(S.lineId)).name : c.a.label;
      const nameB = vs.startsWith("l:") ? others.find((l) => "l:" + l.id === vs).name : c.b.label;
      $("#f-window").textContent = `${c.a.label}${c.a.live ? " (in progress)" : ""}`;
      $("#an-cmp").innerHTML = `<div class="scroll-x"><table class="data"><thead><tr><th>Metric</th><th class="num">${esc(nameA)}</th><th class="num">${esc(nameB)}</th><th class="num">Change</th></tr></thead><tbody>
        ${METRICS.map(([k, name, good, fmt]) => { const r = c.kpis.find((x) => x.metric === k); return `<tr><td>${name}</td><td class="num"><b>${r.a == null ? "–" : fmt(r.a)}</b></td>
          <td class="num muted">${r.b == null ? "–" : fmt(r.b)}</td><td class="num">${delta(r.a, r.b, good, fmt)}</td></tr>`; }).join("")}</tbody></table></div>`;
      $("#an-rank-sub").textContent = "added time per unit vs. " + nameB;
      const ranked = c.stages.filter((s) => s.added_time_per_unit_s != null);
      $("#an-rank").innerHTML = ranked.length ? `<table class="data"><thead><tr><th>Stage</th><th class="num">Processing</th><th class="num">Waiting</th><th class="num">Peak queue</th><th class="num">Added per unit</th></tr></thead><tbody>
        ${ranked.map((s) => `<tr><td><b>${esc(s.station)}</b></td><td class="num">${fmtDur(s.a.avg_processing_s)} <span class="muted">vs ${fmtDur(s.b.avg_processing_s)}</span></td>
          <td class="num">${fmtDur(s.a.avg_waiting_s)} <span class="muted">vs ${fmtDur(s.b.avg_waiting_s)}</span></td><td class="num">${s.a.peak_waiting} <span class="muted">vs ${s.b.peak_waiting}</span></td>
          <td class="num"><span class="delta ${s.added_time_per_unit_s > 60 ? "up-bad" : s.added_time_per_unit_s < -60 ? "down-good" : "flat"}">${fmtDelta(s.added_time_per_unit_s)}</span></td></tr>`).join("")}</tbody></table>
        <p class="note" style="margin-top:8px">This ranks observed changes. It doesn't say why a stage changed - check machine and operator records for the cause.</p>` : empty("No comparable stage data.");
      const total = rej.reduce((a, r) => a + r.n, 0);
      $("#an-rej").innerHTML = rej.length ? `<table class="data"><thead><tr><th>Stage</th><th>Reason</th><th class="num">Units</th><th style="width:120px"></th></tr></thead><tbody>
        ${rej.slice(0, 12).map((r) => `<tr><td><b>${esc(r.station)}</b></td><td>${esc(label(r.reason))}</td><td class="num">${r.n}</td><td>${progress(r.n, rej[0].n, "crit")}</td></tr>`).join("")}</tbody></table>
        <p class="note" style="margin-top:8px">${fmtNum(total)} rejected in total.</p>` : empty("Nothing was rejected in this period.");
      const rows = trend.map((t) => ({ ...t, label: new Date(t.start * 1000).toLocaleString([], t.step_s >= 86400 ? { month: "short", day: "numeric" } : { hour: "2-digit", minute: "2-digit" }) }));
      $("#an-trend").innerHTML = barChart(rows, [{ key: "issued", name: "Started", color: "#9fb8e8" }, { key: "finished", name: "Finished", color: "#1a7f37" }, { key: "rejected", name: "Rejected", color: "#cf222e" }]);
      await breakdown();
    },
  });
})();
