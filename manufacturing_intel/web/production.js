/* Batches (start / follow) and Units (search / journey). */
(() => {
  const { $, esc, api, qs, act, fmtDur, fmtNum, fmtPct, fmtDateTime, fmtTime, badge, empty, progress, label, toast } = MPI;
  const STATUS = { in_progress: ["In production", "info"], finished: ["Finished", "ok"], rejected: ["Rejected", "crit"], active: ["Active", "info"], closed: ["Closed", ""] };
  const stat = (s) => badge(...(STATUS[s] || [s, ""]));

  /* -------------------------------------------------------------------- batches */
  let selectedBatch = null;
  MPI.register("batches", {
    title: "Batches", filters: ["line"],
    init(root) {
      root.innerHTML = `<div class="card"><div class="card-head"><span>Start a batch</span></div>
        <p class="note">A batch groups the units of one product made together. Units issued without naming a batch join the line's active batch (or an automatic one).</p>
        <div class="form-grid"><label class="field">Product<select id="b-product"></select></label>
          <label class="field">Planned quantity<input type="number" id="b-qty" min="1" placeholder="optional"></label>
          <label class="field">Batch code<input id="b-code" placeholder="auto: BATCH-YYYY-MMDD-001"></label>
          <button class="btn primary" id="b-create">Start batch</button></div></div>
        <div class="card"><div class="card-head"><span>Batches</span><span class="muted small">click a batch for its flow</span></div><div id="b-list"></div></div>
        <div id="b-detail"></div>`;
      $("#b-create").addEventListener("click", () => act(async () => {
        const body = { product_id: +$("#b-product").value, line_id: +MPI.S.lineId, qty_planned: $("#b-qty").value ? +$("#b-qty").value : null, code: $("#b-code").value.trim() || null };
        const b = await api("/batches", { method: "POST", body });
        $("#b-qty").value = ""; $("#b-code").value = ""; selectedBatch = b.id;
        await this.load();
      }, "Batch started"));
      $("#b-list").addEventListener("click", (e) => {
        const close = e.target.closest("[data-close]");
        if (close) { act(async () => { await api(`/batches/${close.dataset.close}/close`, { method: "POST" }); await this.load(); }, "Batch closed"); return; }
        const tr = e.target.closest("tr[data-id]");
        if (tr) { selectedBatch = +tr.dataset.id; this.detail(); }
      });
    },
    async load() {
      const cfg = MPI.S.cfg;
      const routes = new Set(cfg.routes.filter((r) => String(r.line_id) === String(MPI.S.lineId)).map((r) => r.product_id));
      $("#b-product").innerHTML = cfg.products.filter((p) => routes.has(p.id)).map((p) => `<option value="${p.id}">${esc(p.code)} · ${esc(p.name)}</option>`).join("") || "<option value=''>no routes on this line</option>";
      const rows = await api("/batches?" + (MPI.S.lineId ? `line_id=${MPI.S.lineId}` : ""));
      $("#b-list").innerHTML = rows.length ? `<div class="scroll-x"><table class="data"><thead><tr><th>Batch</th><th>Product</th><th>Status</th><th class="num">Planned</th>
        <th class="num">Started</th><th class="num">Finished</th><th class="num">Rejected</th><th class="num">In production</th><th style="width:140px">Progress</th><th>Created</th><th></th></tr></thead><tbody>
        ${rows.map((b) => `<tr class="click" data-id="${b.id}" ${b.id === selectedBatch ? 'style="background:var(--accent-soft)"' : ""}><td><b>${esc(b.code)}</b></td><td>${esc(b.product_code)}</td><td>${stat(b.status)}</td>
          <td class="num">${b.qty_planned ? fmtNum(b.qty_planned) : "–"}</td><td class="num">${fmtNum(b.issued)}</td><td class="num">${fmtNum(b.finished)}</td>
          <td class="num">${fmtNum(b.rejected)}</td><td class="num">${fmtNum(b.in_progress)}</td>
          <td>${progress(b.finished + b.rejected, b.qty_planned || b.issued, b.rejected > b.finished * 0.2 ? "warn" : "good")}</td>
          <td class="muted nowrap">${fmtDateTime(b.created_at)}</td><td>${b.status === "active" ? `<button class="btn-link" data-close="${b.id}">Close</button>` : ""}</td></tr>`).join("")}
        </tbody></table></div>` : empty("No batches yet.");
      if (selectedBatch) this.detail();
    },
    async detail() {
      const f = await api(`/batches/${selectedBatch}/flow`);
      const b = f.batch, k = f.kpis;
      const max = Math.max(1, ...f.stages.map((s) => s.reached));
      $("#b-detail").innerHTML = `<div class="card"><div class="card-head"><span>${esc(b.code)} · ${esc(b.product_name)} on ${esc(b.line_name)}</span>${stat(b.status)}</div>
        <div class="row" style="margin-bottom:12px"><span>Started <b>${fmtNum(k.cohort.started)}</b>${b.qty_planned ? ` of ${fmtNum(b.qty_planned)} planned` : ""}</span><span>Finished <b>${fmtNum(k.cohort.finished)}</b></span>
          <span>Rejected <b>${fmtNum(k.cohort.rejected)}</b></span><span>In production <b>${fmtNum(k.cohort.in_progress)}</b></span><span>Yield <b>${fmtPct(k.yield_pct)}</b></span>
          <span>Avg production time <b>${k.avg_cycle_s == null ? "–" : fmtDur(k.avg_cycle_s)}</b></span></div>
        <div class="scroll-x"><table class="data"><thead><tr><th>Stage</th><th style="width:220px">Reached</th><th class="num">Completed</th><th class="num">Rejected</th><th class="num">Waiting</th>
        <th class="num">Processing</th><th class="num">Skipped</th><th class="num">Avg process</th><th class="num">Avg wait</th></tr></thead><tbody>
        ${f.stages.map((s) => `<tr><td><b>${esc(s.station)}</b></td><td><div class="row" style="gap:8px;flex-wrap:nowrap"><span class="num" style="width:44px;text-align:right">${fmtNum(s.reached)}</span><div style="flex:1">${progress(s.reached, max)}</div></div></td>
          <td class="num">${fmtNum(s.completed)}</td><td class="num">${fmtNum(s.rejected)}</td><td class="num">${fmtNum(s.waiting)}</td><td class="num">${fmtNum(s.processing)}</td>
          <td class="num">${s.skipped ? fmtNum(s.skipped) : "–"}</td><td class="num">${s.avg_processing_s ? fmtDur(s.avg_processing_s) : "–"}</td><td class="num">${s.avg_waiting_s ? fmtDur(s.avg_waiting_s) : "–"}</td></tr>`).join("")}
        </tbody></table></div><p class="note" style="margin-top:10px"><a href="#units" data-batch="${b.id}" id="b-units">See this batch's units</a></p></div>`;
      $("#b-units").addEventListener("click", () => { MPI.S.unitBatch = b.id; });
    },
  });

  /* ---------------------------------------------------------------------- units */
  let shown = 0;
  function journey(j) {
    const u = j.unit, t = j.totals;
    const segs = [];
    for (const v of j.visits) {
      if (v.kind !== "process") continue;
      if (v.waiting_s) segs.push({ cls: "wait", s: v.waiting_s, tip: `${v.station}: waited ${fmtDur(v.waiting_s)}` });
      if (v.processing_s) segs.push({ cls: v.outcome === "rejected" ? "rej" : "proc", s: v.processing_s, tip: `${v.station}: processing ${fmtDur(v.processing_s)}` });
    }
    const total = segs.reduce((a, s) => a + s.s, 0) || 1;
    return `<div class="card"><div class="card-head"><span>${esc(u.code)} · ${esc(u.product_name)}</span>${stat(u.status)}</div>
      <div class="row" style="margin-bottom:8px"><span>Line <b>${esc(u.line_name)}</b></span><span>Batch <b>${esc(u.batch_code)}</b></span><span>Started <b>${fmtDateTime(u.created_at)}</b></span>
        <span>${u.status === "finished" ? "Total production time" : "Elapsed"} <b>${fmtDur(t.elapsed_s)}</b>${t.target_s ? ` <span class="muted">(target ${fmtDur(t.target_s)})</span>` : ""}</span>
        <span>Processing <b>${fmtDur(t.processing_s)}</b></span><span>Waiting <b>${fmtDur(t.waiting_s)}</b></span>
        ${t.skipped_steps ? badge(`${t.skipped_steps} step(s) not recorded`, "warn") : ""}</div>
      <div class="tline">${segs.map((s) => `<div class="${s.cls}" style="width:${(100 * s.s) / total}%" title="${esc(s.tip)}"></div>`).join("")}</div>
      <div class="legend"><span><i style="background:var(--accent)"></i>Processing</span><span><i style="background:#f2c94c"></i>Waiting</span><span><i style="background:var(--crit)"></i>Rejected</span></div>
      <div class="scroll-x" style="margin-top:14px"><table class="data"><thead><tr><th>Step</th><th>Station</th><th>Arrived</th><th>Started</th><th>Left</th><th class="num">Waiting</th><th class="num">Processing</th><th>Result</th></tr></thead><tbody>
      ${j.visits.map((v) => `<tr><td class="muted">${v.step_seq}</td><td><b>${esc(v.station)}</b></td><td>${fmtTime(v.arrived_ts)}</td><td>${fmtTime(v.start_ts)}</td><td>${fmtTime(v.end_ts)}</td>
        <td class="num">${v.waiting_s == null ? "–" : fmtDur(v.waiting_s)}</td><td class="num">${v.processing_s == null ? "–" : fmtDur(v.processing_s)}${v.expected_process_min && v.processing_s ? ` <span class="muted">/ ${fmtNum(v.expected_process_min, 1)} min</span>` : ""}</td>
        <td>${v.outcome === "rejected" ? badge("Rejected: " + label(v.reject_reason), "crit") : v.outcome === "skipped" ? badge("not recorded", "warn") : badge(label(v.outcome), v.outcome === "completed" ? "ok" : "info")}</td></tr>`).join("")}
      </tbody></table></div>
      <details style="margin-top:12px"><summary class="small muted" style="cursor:pointer">Event log (${j.events.length})</summary><table class="data" style="margin-top:8px"><thead><tr><th>Time</th><th>Event</th><th>Station</th><th>Source</th><th>Result</th><th>Detail</th></tr></thead><tbody>
      ${j.events.map((e) => `<tr><td>${fmtTime(e.ts)}</td><td>${esc(e.type)}</td><td>${esc(e.station)}</td><td>${esc(e.source)}</td><td>${badge(e.result, e.result === "applied" ? "ok" : "warn")}</td><td class="muted">${esc(e.detail || "")}</td></tr>`).join("")}</tbody></table></details></div>`;
  }

  MPI.register("units", {
    title: "Units", filters: ["line", "product"],
    init(root) {
      root.innerHTML = `<div class="card"><div class="row"><label class="field">Unit code<input id="u-q" placeholder="e.g. GEAR-000125"></label>
        <label class="field">Status<select id="u-status"><option value="">Any</option><option value="in_progress">In production</option><option value="finished">Finished</option><option value="rejected">Rejected</option></select></label>
        <label class="field">Batch filter<select id="u-batch"><option value="">Any batch</option></select></label>
        <button class="btn primary" id="u-go" style="align-self:end">Search</button></div></div>
        <div id="u-journey"></div><div class="card"><div class="card-head"><span>Units</span><span class="muted small" id="u-count"></span></div><div id="u-list"></div></div>`;
      const go = () => { shown = 0; this.load(); };
      $("#u-go").addEventListener("click", go);
      $("#u-q").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      $("#u-status").addEventListener("change", go); $("#u-batch").addEventListener("change", go);
      $("#u-list").addEventListener("click", (e) => {
        if (e.target.id === "u-more") { shown += 50; this.load(true); return; }
        const tr = e.target.closest("tr[data-code]");
        if (tr) this.show(tr.dataset.code);
      });
    },
    async show(code) {
      try { $("#u-journey").innerHTML = journey(await api("/units/" + encodeURIComponent(code))); $("#u-journey").scrollIntoView({ behavior: "smooth", block: "start" }); }
      catch (e) { $("#u-journey").innerHTML = `<div class="card">${empty(e.message)}</div>`; }
    },
    async load(append) {
      const S = MPI.S;
      if (S.openUnit) { const c = S.openUnit; S.openUnit = null; $("#u-q").value = c; this.show(c); }
      const batches = await api("/batches?" + (S.lineId ? `line_id=${S.lineId}&` : "") + "limit=100");
      const cur = MPI.S.unitBatch || $("#u-batch").value;
      $("#u-batch").innerHTML = '<option value="">Any batch</option>' + batches.map((b) => `<option value="${b.id}">${esc(b.code)}</option>`).join("");
      $("#u-batch").value = cur || ""; MPI.S.unitBatch = null;
      const p = new URLSearchParams({ limit: 50, offset: shown });
      if (S.lineId) p.set("line_id", S.lineId);
      if (S.productId) p.set("product_id", S.productId);
      if ($("#u-status").value) p.set("status", $("#u-status").value);
      if ($("#u-q").value.trim()) p.set("q", $("#u-q").value.trim());
      if ($("#u-batch").value) p.set("batch_id", $("#u-batch").value);
      const r = await api("/units?" + p);
      const rows = r.units.map((u) => `<tr class="click" data-code="${esc(u.code)}"><td><b>${esc(u.code)}</b></td><td>${esc(u.batch_code)}</td><td>${stat(u.status)}</td>
        <td>${u.status === "in_progress" && u.at_station ? `${esc(u.at_station)} <span class="muted">(${label(u.at_state)})</span>` : u.status === "finished" ? "Finished Goods" : "–"}</td>
        <td class="muted nowrap">${fmtDateTime(u.created_at)}</td><td class="num">${u.cycle_s ? fmtDur(u.cycle_s) : "–"}</td><td class="muted">${esc(u.source)}</td></tr>`).join("");
      $("#u-count").textContent = `${fmtNum(r.total)} matching`;
      const head = `<div class="scroll-x"><table class="data"><thead><tr><th>Unit</th><th>Batch</th><th>Status</th><th>Now at</th><th>Started</th><th class="num">Production time</th><th>Source</th></tr></thead><tbody>`;
      if (append) $("#u-list tbody").insertAdjacentHTML("beforeend", rows);
      else $("#u-list").innerHTML = r.units.length ? head + rows + "</tbody></table></div>" : empty("No units match.");
      const more = $("#u-more"); if (more) more.remove();
      if (shown + 50 < r.total) $("#u-list").insertAdjacentHTML("beforeend", `<div style="text-align:center;padding-top:10px"><button class="btn" id="u-more">Show more</button></div>`);
    },
  });
})();
