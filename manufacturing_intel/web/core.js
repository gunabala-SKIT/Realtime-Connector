/* Shared state, API helper, formatting, navigation and the filter bar. Pages register themselves with MPI.register(). */
window.MPI = (() => {
  const S = { cfg: null, page: "overview", lineId: null, productId: "", period: "today", start: "", end: "", open: 0 };
  const pages = {};
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /* ---------------------------------------------------------------- formatting */
  const fmtDur = (s) => {
    if (s == null || isNaN(s)) return "–";
    s = Math.abs(s);
    if (s >= 3600) return `${Math.floor(s / 3600)}h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")}m`;
    if (s >= 90) return `${(s / 60).toFixed(1)} min`;
    return `${Math.round(s)} s`;
  };
  const fmtDelta = (s) => (s == null ? "–" : (s >= 0 ? "+" : "−") + fmtDur(s));
  const fmtNum = (n, d = 0) => (n == null || isNaN(n) ? "–" : Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: 0 }));
  const fmtPct = (n) => (n == null ? "–" : `${fmtNum(n, 1)}%`);
  const fmtTime = (ts) => (ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "–");
  const fmtDateTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "–");
  const ago = (ts) => { const s = Date.now() / 1000 - ts; return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`; };
  const label = (s) => String(s || "").replace(/_/g, " ");

  /* ---------------------------------------------------------------------- api */
  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: {} };
    if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
    let r;
    try { r = await fetch("/api" + path, init); } catch (e) { throw new Error("Can't reach the server."); }
    let data = null;
    try { data = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) {
      const d = data && data.detail;
      throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => `${(x.loc || []).slice(1).join(".")}: ${x.msg}`).join("; ") : `Request failed (${r.status})`);
    }
    return data;
  }
  const qs = (extra = {}) => {
    const p = new URLSearchParams();
    if (S.lineId) p.set("line_id", S.lineId);
    if (S.productId) p.set("product_id", S.productId);
    p.set("period", S.period);
    if (S.period === "custom") { p.set("start", S.start); p.set("end", S.end); }
    for (const [k, v] of Object.entries(extra)) { if (v === null) p.delete(k); else if (v !== undefined && v !== "") p.set(k, v); }
    return "?" + p.toString();
  };
  let toastTimer;
  function toast(msg, err = false) {
    const t = $("#toast");
    t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), err ? 6000 : 3200);
  }
  /* Run a user action: shows errors as a toast instead of failing silently. */
  async function act(fn, okMsg) {
    try { const r = await fn(); if (okMsg) toast(okMsg); return r; } catch (e) { toast(e.message, true); return undefined; }
  }

  /* ---------------------------------------------------------------- components */
  const badge = (text, kind = "") => `<span class="badge ${kind}">${esc(text)}</span>`;
  const tile = (name, value, sub = "", kind = "") => `<div class="tile ${kind}"><div class="tl">${esc(name)}</div><div class="k">${value}</div><div class="ts">${sub}</div></div>`;
  const empty = (msg) => `<div class="empty">${esc(msg)}</div>`;
  /* Change vs the previous period. `goodWhen` says which direction is an improvement. */
  function delta(a, b, goodWhen = "up", fmt = (x) => fmtNum(x, 1), pctToo = true) {
    if (a == null || b == null) return `<span class="muted">n/a</span>`;
    const d = a - b;
    if (Math.abs(d) < 1e-9) return `<span class="delta flat">no change</span>`;
    const up = d > 0, good = (goodWhen === "up") === up;
    const p = pctToo && b ? ` (${d > 0 ? "+" : "−"}${Math.abs((100 * d) / b).toFixed(0)}%)` : "";
    return `<span class="delta ${up ? "up" : "down"}-${good ? "good" : "bad"}">${up ? "▲" : "▼"} ${fmt(Math.abs(d))}${p}</span>`;
  }
  /* Grouped bar chart as inline SVG (no external libraries: factories are often offline). */
  function barChart(rows, series, opts = {}) {
    if (!rows.length) return empty("No data in this period.");
    const W = 900, H = 190, L = 34, B = 24, T = 8, R = 6;
    const max = Math.max(1, ...rows.flatMap((r) => series.map((s) => r[s.key] || 0)));
    const nice = Math.ceil(max / 4) * 4 || 4;
    const bw = (W - L - R) / rows.length, sw = Math.max(2, Math.min(18, (bw * 0.8) / series.length));
    let g = "";
    for (let i = 0; i <= 4; i++) {
      const y = T + (H - T - B) * (1 - i / 4);
      g += `<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" stroke="#e5e9ee"/><text x="${L - 5}" y="${y + 3}" text-anchor="end">${fmtNum((nice * i) / 4)}</text>`;
    }
    const step = Math.ceil(rows.length / 12);
    rows.forEach((r, i) => {
      const x0 = L + i * bw + (bw - sw * series.length) / 2;
      series.forEach((s, j) => {
        const v = r[s.key] || 0, h = ((H - T - B) * v) / nice;
        g += `<rect x="${x0 + j * sw}" y="${H - B - h}" width="${sw - 1}" height="${h}" fill="${s.color}" rx="1.5"><title>${esc(r.label)} · ${esc(s.name)}: ${v}</title></rect>`;
      });
      if (i % step === 0) g += `<text x="${L + i * bw + bw / 2}" y="${H - 7}" text-anchor="middle">${esc(r.label)}</text>`;
    });
    const legend = series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("");
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${g}</svg><div class="legend">${legend}</div>`;
  }
  const progress = (n, d, kind = "") => `<div class="bar ${kind}"><i style="width:${d ? Math.min(100, (100 * n) / d) : 0}%"></i></div>`;

  /* ----------------------------------------------------------------- navigation */
  const ICONS = {
    overview: '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    batches: '<path d="M21 8 12 3 3 8l9 5 9-5Z"/><path d="m3 13 9 5 9-5"/>',
    units: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    analytics: '<path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/>',
    alerts: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10 21a2 2 0 0 0 4 0"/>',
    assistant: '<path d="M21 12a8 8 0 0 1-11.6 7.1L3 21l1.9-6.4A8 8 0 1 1 21 12Z"/>',
    operator: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M9 12h6M12 9v6"/>',
    cameras: '<path d="M3 8a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="m16 10 5-3v10l-5-3"/>',
    setup: '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 0 0-2-1.2L14 3h-4l-.6 2.6a7 7 0 0 0-2 1.2l-2.3-.9-2 3.4 2 1.5a7 7 0 0 0 0 2.4l-2 1.5 2 3.4 2.3-.9a7 7 0 0 0 2 1.2L10 21h4l.6-2.6a7 7 0 0 0 2-1.2l2.3.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2Z"/>',
  };
  function register(name, page) { pages[name] = { filters: ["line", "product", "period"], ...page, ready: false }; }

  function buildNav() {
    const items = [["overview", "Overview"], ["batches", "Batches"], ["units", "Units"], ["analytics", "Analytics"], ["alerts", "Alerts"],
      ["assistant", "Assistant"], ["-"], ["operator", "Operator"], ["cameras", "Cameras"], ["setup", "Setup"]];
    $("#nav").innerHTML = items.map(([id, name]) => id === "-" ? '<div class="nav-sep"></div>' :
      `<button class="nav-item" data-page="${id}"><svg viewBox="0 0 24 24">${ICONS[id]}</svg>${name}${id === "alerts" ? '<span class="nav-badge" id="alert-badge" hidden></span>' : ""}</button>`).join("");
    $$("#nav .nav-item").forEach((b) => b.addEventListener("click", () => { location.hash = b.dataset.page; }));
  }

  function showPage(name) {
    if (!pages[name]) name = "overview";
    const prev = pages[S.page];
    if (prev && prev.stop) prev.stop();
    clearInterval(S.timer);
    S.page = name;
    const p = pages[name];
    $$(".page").forEach((el) => el.classList.toggle("active", el.id === "page-" + name));
    $$("#nav .nav-item").forEach((b) => b.classList.toggle("active", b.dataset.page === name));
    $("#page-title").textContent = p.title;
    const show = new Set(p.filters);
    $("#filterbar").style.display = show.size ? "" : "none";
    $("#f-line").parentElement.style.display = show.has("line") ? "" : "none";
    $("#f-product").parentElement.style.display = show.has("product") ? "" : "none";
    $("#f-period").parentElement.style.display = show.has("period") ? "" : "none";
    $("#f-custom").hidden = !(show.has("period") && S.period === "custom");
    $("#f-window").textContent = "";
    if (!p.ready) { p.init($("#page-" + name)); p.ready = true; }
    reload();
    if (p.every) S.timer = setInterval(() => { if (!document.hidden) reload(true); }, p.every);
  }
  function reload(quiet = false) {
    const p = pages[S.page];
    if (p && p.load) Promise.resolve(p.load(quiet)).catch((e) => { if (!quiet) toast(e.message, true); });
  }

  /* ------------------------------------------------------------------ filters */
  const PERIOD_NAMES = { today: "Today", yesterday: "Yesterday", current_shift: "Current shift", previous_shift: "Previous shift",
    this_week: "This week", last_week: "Last week", this_month: "This month", last_month: "Last month", last_7d: "Last 7 days",
    last_30d: "Last 30 days", last_hour: "Last hour", last_24h: "Last 24 hours", custom: "Custom range…" };
  function buildFilters() {
    const c = S.cfg;
    $("#f-line").innerHTML = c.lines.map((l) => `<option value="${l.id}">${esc(l.name)}</option>`).join("") || "<option value=''>no lines</option>";
    if (!c.lines.some((l) => String(l.id) === String(S.lineId))) S.lineId = c.lines[0] ? c.lines[0].id : null;
    $("#f-line").value = S.lineId || "";
    $("#f-product").innerHTML = '<option value="">All products</option>' + c.products.map((p) => `<option value="${p.id}">${esc(p.code)} · ${esc(p.name)}</option>`).join("");
    $("#f-product").value = S.productId;
    $("#f-period").innerHTML = c.periods.map((k) => `<option value="${k}">${PERIOD_NAMES[k] || k}</option>`).join("");
    $("#f-period").value = S.period;
    $("#f-start").value = S.start; $("#f-end").value = S.end;
  }
  function bindFilters() {
    const save = () => { try { localStorage.setItem("mpi.filters", JSON.stringify({ lineId: S.lineId, productId: S.productId, period: S.period, start: S.start, end: S.end })); } catch (e) { /* private mode */ } };
    $("#f-line").addEventListener("change", (e) => { S.lineId = e.target.value; save(); reload(); });
    $("#f-product").addEventListener("change", (e) => { S.productId = e.target.value; save(); reload(); });
    $("#f-period").addEventListener("change", (e) => {
      S.period = e.target.value; save();
      $("#f-custom").hidden = S.period !== "custom";
      if (S.period === "custom" && !(S.start && S.end)) {
        const d = new Date(), iso = (x) => x.toISOString().slice(0, 10);
        S.end = iso(d); S.start = iso(new Date(d.getTime() - 6 * 864e5)); $("#f-start").value = S.start; $("#f-end").value = S.end;
      }
      if (S.period !== "custom" || (S.start && S.end)) reload();
    });
    for (const [id, key] of [["f-start", "start"], ["f-end", "end"]]) $("#" + id).addEventListener("change", (e) => { S[key] = e.target.value; save(); if (S.start && S.end) reload(); });
    $("#f-refresh").addEventListener("click", () => reload());
    try { Object.assign(S, JSON.parse(localStorage.getItem("mpi.filters") || "{}")); } catch (e) { /* ignore */ }
  }

  async function reloadConfig() {
    S.cfg = await api("/config");
    buildFilters();
    $("#factory-name").textContent = S.cfg.factory.name;
    $("#sim-banner").hidden = !S.cfg.simulated_data;
    return S.cfg;
  }

  async function poll() {
    try {
      const h = await api("/health");
      $("#status-pill").classList.remove("offline"); $("#status-text").textContent = "System online";
      $("#sim-banner").hidden = !h.simulated_data;
      const open = (await api("/alerts?status=open&limit=200")).length;
      const b = $("#alert-badge"); b.textContent = open; b.hidden = !open;
    } catch (e) {
      $("#status-pill").classList.add("offline"); $("#status-text").textContent = "Offline";
    }
  }

  function boot() {
    buildNav(); bindFilters();
    reloadConfig().then(() => {
      window.addEventListener("hashchange", () => showPage(location.hash.slice(1)));
      showPage(location.hash.slice(1) || "overview");
    }).catch((e) => { $("#status-pill").classList.add("offline"); $("#status-text").textContent = "Offline"; toast(e.message, true); });
    poll(); setInterval(poll, 15000);
  }

  return { S, $, $$, esc, api, qs, toast, act, fmtDur, fmtDelta, fmtNum, fmtPct, fmtTime, fmtDateTime, ago, label, badge, tile, empty, delta,
    barChart, progress, register, boot, reload, reloadConfig, showPage, PERIOD_NAMES };
})();
