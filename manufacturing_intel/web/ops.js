/* Alerts, Assistant, Operator console. */
(() => {
  const { $, esc, api, act, fmtDur, fmtNum, fmtTime, ago, badge, empty, label } = MPI;
  const KIND = { bottleneck: ["Bottleneck", "crit"], delay: ["Production delay", "warn"], idle: ["Machine idle", "warn"], quality: ["Quality", "crit"], low_production: ["Low production", "info"] };

  function evidence(e) {
    const f = { waiting: (v) => v, expected_waiting: (v) => v, arrived_last_hour: (v) => v, finished_last_hour: (v) => v,
      avg_processing_s: fmtDur, expected_s: fmtDur, variance_s: fmtDur, samples: (v) => v, idle_min: (v) => `${v} min`, threshold_min: (v) => `${v} min`,
      rejection_rate_pct: (v) => `${v}%`, threshold_pct: (v) => `${v}%`, rejected: (v) => v, decided: (v) => v, actual_per_h: (v) => v, target_per_h: (v) => v, window_min: (v) => `${v} min` };
    return Object.entries(e).filter(([k]) => f[k]).map(([k, v]) => `${esc(label(k.replace(/_s$/, "")))}: <b>${esc(String(f[k](v)))}</b>`).join(" · ");
  }

  /* --------------------------------------------------------------------- alerts */
  MPI.register("alerts", {
    title: "Alerts", filters: [],
    init(root) {
      root.innerHTML = `<div class="row"><label class="field">Show<select id="al-status"><option value="active">Active</option><option value="resolved">Resolved</option><option value="">All</option></select></label>
        <button class="btn" id="al-eval" style="align-self:end">Check now</button>
        <span class="muted small" style="align-self:end">Alerts are re-checked every 30 seconds and clear themselves when the condition goes away.</span></div>
        <div id="al-list" class="stack"></div>`;
      $("#al-status").addEventListener("change", () => this.load());
      $("#al-eval").addEventListener("click", () => act(async () => { const r = await api("/alerts/evaluate", { method: "POST" }); await this.load(); MPI.toast(`${r.active} active · ${r.opened} new · ${r.resolved} cleared`); }));
      $("#al-list").addEventListener("click", (e) => { const b = e.target.closest("[data-ack]"); if (b) act(async () => { await api(`/alerts/${b.dataset.ack}/ack`, { method: "POST" }); await this.load(); }, "Acknowledged"); });
    },
    every: 10000,
    async load() {
      const sel = $("#al-status").value;
      let rows = await api("/alerts?limit=200");
      if (sel === "active") rows = rows.filter((a) => a.status !== "resolved"); else if (sel === "resolved") rows = rows.filter((a) => a.status === "resolved");
      $("#al-list").innerHTML = rows.length ? rows.map((a) => `<div class="alert ${esc(a.severity)} ${a.status === "resolved" ? "resolved" : ""}"><div>
        <div class="msg">${esc(a.message)}</div><div class="ev">${evidence(a.evidence)}</div>
        <div class="ev">${badge(...(KIND[a.kind] || [a.kind, ""]))} ${badge(a.severity, a.severity)} opened ${ago(a.opened_at)}${a.status === "resolved" ? ` · cleared ${ago(a.resolved_at)}` : a.status === "ack" ? " · acknowledged" : ""}</div></div>
        <div>${a.status === "open" ? `<button class="btn small" data-ack="${a.id}">Acknowledge</button>` : ""}</div></div>`).join("") : empty(sel === "resolved" ? "No resolved alerts yet." : "No active alerts. Everything is within its configured thresholds.");
    },
  });

  /* ------------------------------------------------------------------- assistant */
  MPI.register("assistant", {
    title: "Assistant", filters: ["line"],
    init(root) {
      root.innerHTML = `<div class="card"><p class="note">Ask about production in plain language. Answers come straight from the recorded data and say which line and period they assumed.
        For "why" questions the assistant lists what changed - it never guesses the operational cause.</p>
        <div class="row"><input id="as-q" class="wide" style="flex:1;min-width:260px" maxlength="500" placeholder="e.g. Why is today's production lower than yesterday?"><button class="btn primary" id="as-send">Ask</button></div>
        <div class="chips" id="as-ex" style="margin-top:12px"></div></div><div class="chat" id="as-chat"></div>`;
      const send = (q) => this.ask(q || $("#as-q").value);
      $("#as-send").addEventListener("click", () => send());
      $("#as-q").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
      $("#as-ex").addEventListener("click", (e) => { const c = e.target.closest(".chip"); if (c) send(c.textContent); });
      api("/assistant/examples").then((ex) => { $("#as-ex").innerHTML = ex.map((q) => `<span class="chip">${esc(q)}</span>`).join(""); });
    },
    load() {},
    async ask(q) {
      q = (q || "").trim();
      if (!q) return;
      $("#as-q").value = "";
      const chat = $("#as-chat");
      chat.insertAdjacentHTML("afterbegin", `<div class="bubble q">${esc(q)}</div>`);
      try {
        const r = await api("/assistant", { method: "POST", body: { question: q, line_id: MPI.S.lineId ? +MPI.S.lineId : null } });
        const ev = r.evidence.length ? `<table class="data" style="margin-top:10px"><tbody>${r.evidence.map(([k, v]) => `<tr><td class="muted">${esc(k)}</td><td><b>${esc(String(v))}</b></td></tr>`).join("")}</tbody></table>` : "";
        const tb = r.table ? `<div class="scroll-x"><table class="data" style="margin-top:10px"><thead><tr>${r.table.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${r.table.rows.map((row) => `<tr>${row.map((c) => `<td>${esc(String(c))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : "";
        const sug = r.suggestions.length ? `<div class="chips" style="margin-top:10px">${r.suggestions.map((s) => `<span class="chip">${esc(s)}</span>`).join("")}</div>` : "";
        chat.children[0].insertAdjacentHTML("afterend", `<div class="bubble">${esc(r.answer)}${ev}${tb}${sug}${r.scope ? `<div class="scope">${esc(r.scope)}</div>` : ""}</div>`);
        chat.children[1].addEventListener("click", (e) => { const c = e.target.closest(".chip"); if (c) this.ask(c.textContent); });
      } catch (e) { chat.children[0].insertAdjacentHTML("afterend", `<div class="bubble">${esc(e.message)}</div>`); }
    },
  });

  /* -------------------------------------------------------------------- operator */
  MPI.register("operator", {
    title: "Operator console", filters: ["line"], every: 4000,
    init(root) {
      root.innerHTML = `<div class="card"><div class="card-head"><span>Record an event by hand</span></div>
        <p class="note">For stations without a camera or scanner. It creates the same events as a camera or PLC would. Leave the unit code empty to let the line match the oldest unit waiting (FIFO).</p>
        <div class="form-grid"><label class="field">Station<select id="op-station"></select></label>
          <label class="field">Unit code (optional)<input id="op-code" placeholder="e.g. GEAR-000125"></label>
          <label class="field" id="op-prod-w">Product (when issuing)<select id="op-prod"></select></label>
          <label class="field" id="op-reason-w">Reject reason<select id="op-reason"></select></label></div>
        <div class="row" style="margin-top:14px" id="op-actions"></div><div id="op-result" style="margin-top:12px"></div></div>
        <div class="card"><div class="card-head"><span>Recent events</span><label class="row small muted" style="gap:6px">show <select id="op-filter"><option value="">all</option><option value="applied">applied</option><option value="ignored">ignored</option><option value="unmatched">unmatched</option><option value="error">errors</option></select></label></div><div id="op-events"></div></div>`;
      $("#op-station").addEventListener("change", () => this.buttons());
      $("#op-filter").addEventListener("change", () => this.events());
      $("#op-actions").addEventListener("click", (e) => { const b = e.target.closest("[data-type]"); if (b) this.send(b.dataset.type); });
    },
    stations() { return MPI.S.cfg.stations.filter((s) => String(s.line_id) === String(MPI.S.lineId)); },
    buttons() {
      const st = this.stations().find((s) => String(s.id) === $("#op-station").value);
      const defs = { source: [["issue", "Issue raw material", "primary"]], process: [["enter", "Enter (start processing)", "primary"], ["exit", "Exit (finished here)", ""], ["reject", "Reject", "danger"]], sink: [["enter", "Mark finished", "primary"]] };
      $("#op-actions").innerHTML = st ? defs[st.kind].map(([t, n, c]) => `<button class="btn ${c}" data-type="${t}">${n}</button>`).join("") : "";
      $("#op-prod-w").style.display = st && st.kind !== "sink" ? "" : "none";
      $("#op-reason-w").style.display = st && st.kind === "process" ? "" : "none";
    },
    async send(type) {
      const body = { type, station_id: +$("#op-station").value, unit_code: $("#op-code").value.trim() || null, product: $("#op-prod").value || null, reason: type === "reject" ? $("#op-reason").value : null };
      try {
        const r = await api("/operator/event", { method: "POST", body });
        $("#op-result").innerHTML = `${badge(r.result, r.result === "applied" ? "ok" : "warn")} ${r.unit_code ? `<b>${esc(r.unit_code)}</b>` : ""} <span class="muted">${esc(r.detail || "")}</span>`;
        if (r.result === "applied") $("#op-code").value = "";
      } catch (e) { $("#op-result").innerHTML = `${badge("error", "crit")} ${esc(e.message)}`; }
      this.events();
    },
    async events() {
      const q = new URLSearchParams({ limit: 40 });
      if (MPI.S.lineId) q.set("line_id", MPI.S.lineId);
      if ($("#op-filter").value) q.set("result", $("#op-filter").value);
      const rows = await api("/events?" + q);
      $("#op-events").innerHTML = rows.length ? `<div class="scroll-x"><table class="data"><thead><tr><th>Time</th><th>Event</th><th>Station</th><th>Unit</th><th>Source</th><th>Result</th><th>Detail</th></tr></thead><tbody>
        ${rows.map((e) => `<tr><td class="nowrap">${fmtTime(e.ts)}</td><td>${esc(e.type)}</td><td>${esc(e.station || "")}</td><td><b>${esc(e.unit_code || "")}</b></td><td>${esc(e.source)}</td>
        <td>${badge(e.result, e.result === "applied" ? "ok" : e.result === "error" ? "crit" : "warn")}</td><td class="muted">${esc(e.detail || (e.reason ? label(e.reason) : ""))}</td></tr>`).join("")}</tbody></table></div>` : empty("No events.");
    },
    async load(quiet) {
      const cfg = MPI.S.cfg, sts = this.stations();
      if (!quiet || !$("#op-station").options.length) {
        const cur = $("#op-station").value;
        $("#op-station").innerHTML = sts.map((s) => `<option value="${s.id}">${esc(s.name)} (${s.kind})</option>`).join("");
        if (sts.some((s) => String(s.id) === cur)) $("#op-station").value = cur;
        const routes = new Set(cfg.routes.filter((r) => String(r.line_id) === String(MPI.S.lineId)).map((r) => r.product_id));
        $("#op-prod").innerHTML = '<option value="">(active batch)</option>' + cfg.products.filter((p) => routes.has(p.id)).map((p) => `<option value="${esc(p.code)}">${esc(p.code)}</option>`).join("");
        $("#op-reason").innerHTML = cfg.reject_reasons.map((r) => `<option value="${r}">${esc(label(r))}</option>`).join("");
        this.buttons();
      }
      await this.events();
    },
  });
})();
