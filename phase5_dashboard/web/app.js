"use strict";
const $ = (s) => document.querySelector(s);
const API = "api";

const state = { events: [], filter: { status: "", alert: "", q: "" }, lastId: 0, cameraId: "", zones: [] };

// ---------------------------------------------------------------- formatting
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
const fmtDateTime = (ts) => new Date(ts * 1000).toLocaleString([], { hour12: false });
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ---------------------------------------------------------------- rendering
function eventRow(e) {
  const who = e.identity_status === "RECOGNIZED"
    ? esc(e.employee_name || e.employee_code)
    : e.identity_status;
  const snap = e.snapshot ? `${API}/events/${e.id}/snapshot` : "";
  const alert = e.alert_status === "open"
    ? `<div class="alertbtns" data-id="${e.id}">
         <button data-act="ack">ack</button><button data-act="closed">close</button></div>`
    : (e.alert_status !== "none" ? `<span class="conf">${esc(e.alert_status)}</span>` : "");
  return `<tr class="event-row" data-id="${e.id}">
    <td><div style="display:flex;align-items:center;gap:10px">
      <img class="thumb" loading="lazy" src="${snap}" onerror="this.style.visibility='hidden'">
      <div><div class="cell-title">${who}</div><span class="badge ${e.identity_status}">${e.identity_status}</span></div>
    </div></td>
    <td class="cell-mono">${esc(e.camera_id)}</td>
    <td class="cell-muted">track ${e.track_id}</td>
    <td class="cell-muted">${e.zone ? esc(e.zone) : "—"}</td>
    <td class="cell-muted">${fmtTime(e.ts)}</td>
    <td>${alert}</td>
  </tr>`;
}

function renderEvents() {
  const { status, alert, q } = state.filter;
  const ql = q.trim().toLowerCase();
  const rows = state.events.filter((e) =>
    (!status || e.identity_status === status) &&
    (!alert || e.alert_status === alert) &&
    (!ql || (e.employee_name || "").toLowerCase().includes(ql) || (e.employee_code || "").toLowerCase().includes(ql))
  );
  $("#event-list").innerHTML = rows.length
    ? rows.map(eventRow).join("")
    : `<tr><td colspan="6" class="cell-muted">no matching events</td></tr>`;
}

function setTiles(stats, emp, live) {
  $("#t-employees").textContent = emp?.count ?? "–";
  $("#t-recognized").textContent = stats?.recognized ?? "–";
  $("#t-unknown").textContent = stats?.unknown ?? "–";
  $("#t-events").textContent = stats?.total ?? "–";
  $("#t-alerts").textContent = stats?.open_alerts ?? "–";
  $("#t-fps").textContent = live?.proc_fps != null ? live.proc_fps.toFixed(1) : "–";
  if (live) {
    $("#live-dot").classList.toggle("on", !!live.alive);
    $("#topbar-dot").classList.toggle("on", !!live.alive);
    $("#topbar-pill").classList.toggle("offline", !live.alive);
    $("#live-status-text").textContent = live.alive ? "pipeline online" : "pipeline down";
    $("#topbar-status-text").textContent = live.alive ? "System Online" : "System Offline";
    $("#cam-label").textContent = live.camera_id || "cam";
    state.cameraId = live.camera_id || state.cameraId;
    $("#live-meta").textContent = `${live.resolution} · ${live.proc_fps?.toFixed(1)} fps · person ${live.person_ms}ms · face ${live.face_ms}ms · up ${live.uptime_s}s`;
  }
}

// ---------------------------------------------------------------- data
async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

async function loadEvents() {
  const d = await getJSON(`${API}/events?limit=100&order=desc`);
  state.events = d.events;
  state.lastId = d.events.length ? Math.max(...d.events.map((e) => e.id)) : 0;
  renderEvents();
}

async function refreshStats() {
  try {
    const [stats, emp, live, shelves, zones, lines, attendanceToday, vehiclesToday] = await Promise.all([
      getJSON(`${API}/stats?window=today`),
      getJSON(`${API}/employees`),
      getJSON(`live/status`),
      getJSON(`${API}/shelves/summary`).catch(() => ({ shelves: [] })),
      getJSON(`${API}/zones/summary`).catch(() => ({ zones: [] })),
      getJSON(`${API}/count-lines/summary`).catch(() => ({ lines: [] })),
      getJSON(`${API}/attendance`).catch(() => ({ attendance: [] })),
      getJSON(`${API}/vehicle-log`).catch(() => ({ log: [] })),
    ]);
    setTiles(stats, emp, live);
    const low = shelves.shelves.filter((s) => s.last_check && s.last_check.pct_full <= s.low_stock_threshold);
    $("#t-shelves-low").textContent = shelves.shelves.length ? low.length : "–";
    const queueZones = zones.zones.filter((z) => z.capacity);
    const over = queueZones.filter((z) => z.last_check && z.last_check.count > z.capacity);
    $("#t-queues-over").textContent = queueZones.length ? over.length : "–";
    $("#t-entries").textContent = lines.lines.length ? lines.lines.reduce((s, l) => s + l.entries, 0) : "–";
    $("#t-exits").textContent = lines.lines.length ? lines.lines.reduce((s, l) => s + l.exits, 0) : "–";
    const inBuilding = attendanceToday.attendance.filter((a) => a.entry_ts != null && a.exit_ts == null);
    $("#t-in-building").textContent = attendanceToday.attendance.length ? inBuilding.length : "–";
    $("#t-vehicles").textContent = vehiclesToday.log.length || "–";
    loadAlerts().catch(console.warn);
  } catch (e) { console.warn(e); }
}

// ---------------------------------------------------------------- active alerts
function relTimeShort(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function alertCard(a) {
  const who = a.employee_name ? ` · ${esc(a.employee_name)}` : "";
  return `<div class="alert-card prio-${a.priority}" data-id="${a.id}" data-event="${a.event_id}">
    <div>
      <div class="name">${esc(a.rule_name || "Alert")} <span class="prio prio-${a.priority}">${a.priority}</span></div>
      <div class="cond"><span class="mono">${esc(a.camera_id)}</span>${who} · ${relTimeShort(a.created_at)}</div>
    </div>
    <div class="actions">
      <button data-act="ack">Acknowledge</button>
      <button data-act="view" class="view">View Event</button>
    </div>
  </div>`;
}

async function loadAlerts() {
  const d = await getJSON(`${API}/alerts?status=open&limit=50`);
  $("#alerts-list").innerHTML = d.alerts.length
    ? d.alerts.map(alertCard).join("")
    : `<p class="hint">No open alerts.</p>`;
  $("#alerts-count").textContent = `${d.alerts.length} Open`;
  $("#bell-badge").hidden = d.alerts.length === 0;
  $("#bell-badge").textContent = d.alerts.length;
}

$("#alerts-list").addEventListener("click", async (e) => {
  const card = e.target.closest(".alert-card");
  if (!card || !e.target.dataset.act) return;
  if (e.target.dataset.act === "ack") {
    await fetch(`${API}/alerts/${card.dataset.id}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: "ack" }),
    });
    loadAlerts();
    refreshStats();
  } else if (e.target.dataset.act === "view") {
    openModal(card.dataset.event);
  }
});

$("#bell-btn").addEventListener("click", () => {
  showPage("dashboard");
  $("#alerts-list").scrollIntoView({ behavior: "smooth", block: "center" });
});

// ---------------------------------------------------------------- alert sound
let audioCtx = null;
function playAlertSound() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const t0 = audioCtx.currentTime;
    // two short beeps read clearly as "alert" without being jarring
    [0, 0.18].forEach((delay) => {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "sine";
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.0001, t0 + delay);
      gain.gain.exponentialRampToValueAtTime(0.25, t0 + delay + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + delay + 0.15);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(t0 + delay);
      osc.stop(t0 + delay + 0.16);
    });
  } catch (e) { /* audio blocked (autoplay policy) or unsupported - ignore */ }
}

function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/${API}/ws/events`);
  ws.onmessage = (msg) => {
    const d = JSON.parse(msg.data);
    if (d.type === "event") {
      state.events.unshift(d.event);
      state.events = state.events.slice(0, 200);
      renderEvents();
      refreshStats();
      if (d.event.alert_status === "open") playAlertSound();
      if (trackedEmployee && d.event.employee_code === trackedEmployee
          && d.event.identity_status === "RECOGNIZED" && $("#page-track").classList.contains("active")) {
        loadTrack();   // instant update, no polling delay - this is the whole point of Task 3
      }
    }
  };
  ws.onclose = () => setTimeout(connectWS, 3000);
}

// ---------------------------------------------------------------- modal
async function openModal(id) {
  const e = await getJSON(`${API}/events/${id}`);
  $("#modal-img").src = e.snapshot ? `${API}/events/${id}/snapshot` : "";
  $("#modal-body").innerHTML = `
    <dt>event</dt><dd>#${e.id} · ${esc(e.event_uid.slice(0, 12))}</dd>
    <dt>time</dt><dd>${fmtDateTime(e.ts)}</dd>
    <dt>camera</dt><dd>${esc(e.camera_id)}</dd>
    <dt>track</dt><dd>${e.track_id}</dd>
    <dt>type</dt><dd>${esc(e.event_type)}</dd>
    <dt>status</dt><dd>${esc(e.identity_status)}</dd>
    <dt>employee</dt><dd>${esc(e.employee_name || "–")} ${e.employee_code ? "(" + esc(e.employee_code) + ")" : ""}</dd>
    <dt>confidence</dt><dd>${e.confidence != null ? e.confidence.toFixed(4) : "–"}</dd>
    <dt>alert</dt><dd>${esc(e.alert_status)}</dd>`;
  $("#modal").hidden = false;
}

async function setAlert(id, status) {
  await fetch(`${API}/events/${id}/alert`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  const e = state.events.find((x) => x.id === Number(id));
  if (e) e.alert_status = status;
  renderEvents();
  refreshStats();
}

// ---------------------------------------------------------------- rules
function condSummary(r) {
  const parts = [];
  parts.push(r.identity_status || "any identity");
  parts.push(r.camera_id ? `cam=${r.camera_id}` : "any camera");
  if (r.zone) parts.push(`zone=${r.zone}`);
  if (r.time_start && r.time_end) parts.push(`${r.time_start}–${r.time_end}`);
  parts.push(`cooldown ${r.cooldown_s}s`);
  if (r.notify_email) parts.push("email");
  return parts.join("  ·  ");
}

function ruleRow(r) {
  const trigger = r.identity_status || "any identity";
  const where = [r.camera_id || "any camera", r.zone ? `zone=${esc(r.zone)}` : null].filter(Boolean).join(" · ");
  const when = (r.time_start && r.time_end) ? `${r.time_start}–${r.time_end}` : "";
  return `<tr data-id="${r.id}" data-enabled="${r.enabled ? "1" : "0"}">
    <td><div class="cell-title">${esc(r.name)}</div>${when ? `<div class="cell-sub">${when}</div>` : ""}</td>
    <td class="cell-muted">${esc(trigger)}</td>
    <td class="cell-muted">${where}</td>
    <td><span class="prio prio-${r.priority}">${r.priority}</span></td>
    <td><button class="badge-btn" data-act="toggle"><span class="badge ${r.enabled ? "badge-good" : "badge-muted"}">${r.enabled ? "Active" : "Inactive"}</span></button></td>
    <td class="cell-actions"><button class="btn-link danger" data-act="delete">Delete</button></td>
  </tr>`;
}

async function loadRules() {
  const [rulesD, zonesD] = await Promise.all([getJSON(`${API}/rules`), getJSON(`${API}/zones`)]);
  state.zones = zonesD.zones;
  $("#rules-list").innerHTML = rulesD.rules.length
    ? rulesD.rules.map(ruleRow).join("")
    : `<tr><td colspan="6" class="cell-muted">No rules yet - nothing will raise an alert.</td></tr>`;
  const sel = document.querySelector('select[name=zone]');
  const cur = sel.value;
  sel.innerHTML = `<option value="">any</option>` +
    state.zones.map((z) => `<option value="${esc(z.name)}">${esc(z.name)}</option>`).join("");
  sel.value = cur;
}

$("#rules-list").addEventListener("click", async (e) => {
  const row = e.target.closest("tr[data-id]");
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (!row || !act) return;
  const id = row.dataset.id;
  if (act === "toggle") {
    const enabled = row.dataset.enabled !== "1";
    await fetch(`${API}/rules/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    loadRules();
  } else if (act === "delete") {
    await fetch(`${API}/rules/${id}`, { method: "DELETE" });
    loadRules();
  }
});

$("#rule-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = {
    name: f.get("name"),
    identity_status: f.get("identity_status") || null,
    camera_id: f.get("camera_id") || null,
    zone: f.get("zone") || null,
    time_start: f.get("time_start") || null,
    time_end: f.get("time_end") || null,
    priority: f.get("priority"),
    cooldown_s: Number(f.get("cooldown_s")) || 0,
    notify_email: f.get("notify_email") === "on",
  };
  const r = await fetch(`${API}/rules`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (r.ok) { e.target.reset(); closeDrawer("rule-drawer"); loadRules(); }
  else console.warn("add rule failed", await r.text());
});

// ---------------------------------------------------------------- zones
let zonePoints = [];

function sizeZoneCanvas() {
  const img = $("#zone-bg"), c = $("#zone-canvas");
  c.width = img.clientWidth; c.height = img.clientHeight;
  redrawZoneCanvas();
}

function drawPolygon(ctx, pts, color, w, h, label, open) {
  if (pts.length < 2) return;
  ctx.beginPath();
  pts.forEach(([x, y], i) => { const px = x * w, py = y * h; i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py); });
  if (!open) ctx.closePath();
  ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke();
  if (!open) { ctx.fillStyle = color + "33"; ctx.fill(); }
  ctx.fillStyle = color;
  pts.forEach(([x, y]) => { ctx.beginPath(); ctx.arc(x * w, y * h, 4, 0, Math.PI * 2); ctx.fill(); });
  if (label) {
    const cx = (pts.reduce((s, p) => s + p[0], 0) / pts.length) * w;
    const cy = (pts.reduce((s, p) => s + p[1], 0) / pts.length) * h;
    ctx.font = "600 12px 'IBM Plex Mono', monospace";
    ctx.fillStyle = "#000"; ctx.fillText(label, cx + 1, cy + 1);
    ctx.fillStyle = "#fff"; ctx.fillText(label, cx, cy);
  }
}

function redrawZoneCanvas() {
  const c = $("#zone-canvas"), ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  state.zones.forEach((z) => drawPolygon(ctx, z.points, z.color, c.width, c.height, z.name, false));
  if (zonePoints.length) drawPolygon(ctx, zonePoints, $("#zone-color").value, c.width, c.height, null, true);
}

async function loadZoneFrame() {
  $("#zone-bg").src = `live/frame.jpg?_=${Date.now()}`;
}
$("#zone-bg").addEventListener("load", sizeZoneCanvas);
window.addEventListener("resize", () => { if ($("#page-zones").classList.contains("active")) sizeZoneCanvas(); });

function queueColor(count, capacity) {
  if (count > capacity) return "var(--crit)";
  if (count === capacity) return "var(--warn)";
  return "var(--good)";
}

function zoneRow(z) {
  const chk = z.last_check;
  const occupancy = z.capacity
    ? (chk
        ? `<div><div class="stockbar"><i style="width:${Math.min(100, (chk.count / z.capacity) * 100)}%;background:${queueColor(chk.count, z.capacity)}"></i></div>
             <div class="stockpct">${chk.count}/${z.capacity}</div></div>`
        : `<div class="stockpct">no checks yet</div>`)
    : `<span></span>`;
  return `<div class="rule-row" data-id="${z.id}">
    <div>
      <div class="name" style="color:${z.color}">■ ${esc(z.name)}</div>
      <div class="cond">${esc(z.camera_id)} · ${z.points.length} points${z.capacity ? ` · capacity ${z.capacity}` : ""}</div>
    </div>
    ${occupancy}
    <button data-act="delete">delete</button>
  </div>`;
}

async function loadZonesList() {
  const d = await getJSON(`${API}/zones/summary`);
  state.zones = d.zones;
  $("#zones-list").innerHTML = d.zones.length
    ? d.zones.map(zoneRow).join("")
    : `<p class="hint">No zones yet.</p>`;
  redrawZoneCanvas();
}

function queueAlertRow(a) {
  const btns = a.status === "open"
    ? `<div class="alertbtns" data-id="${a.id}"><button data-act="ack">ack</button><button data-act="closed">close</button></div>`
    : `<span class="conf">${esc(a.status)}</span>`;
  return `<div class="rule-row" data-id="${a.id}">
    <div>
      <div class="name">${esc(a.zone_name)} <span class="prio prio-high">over capacity</span></div>
      <div class="cond">${a.count}/${a.capacity} · ${fmtTime(a.created_at)}</div>
    </div>
    ${btns}
  </div>`;
}

async function loadQueueAlerts() {
  const d = await getJSON(`${API}/queue-alerts?status=open`);
  $("#queue-alerts-list").innerHTML = d.alerts.length
    ? d.alerts.map(queueAlertRow).join("")
    : `<p class="hint">No open queue/crowd alerts.</p>`;
}

$("#queue-alerts-list").addEventListener("click", async (e) => {
  const btns = e.target.closest(".alertbtns");
  if (!btns) return;
  await fetch(`${API}/queue-alerts/${btns.dataset.id}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status: e.target.dataset.act }),
  });
  loadQueueAlerts();
  refreshStats();
});

$("#zone-canvas").addEventListener("click", (e) => {
  const rect = e.target.getBoundingClientRect();
  const x = (e.clientX - rect.left) / rect.width;
  const y = (e.clientY - rect.top) / rect.height;
  zonePoints.push([Number(x.toFixed(4)), Number(y.toFixed(4))]);
  redrawZoneCanvas();
});
$("#zone-undo").addEventListener("click", () => { zonePoints.pop(); redrawZoneCanvas(); });
$("#zone-cancel").addEventListener("click", () => { zonePoints = []; redrawZoneCanvas(); });
$("#zone-refresh").addEventListener("click", loadZoneFrame);

$("#zone-finish").addEventListener("click", async () => {
  if (zonePoints.length < 3) return alert("Add at least 3 points before saving.");
  const name = $("#zone-name").value.trim();
  if (!name) return alert("Give the zone a name.");
  const capacityRaw = $("#zone-capacity").value.trim();
  const r = await fetch(`${API}/zones`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      camera_id: state.cameraId || "cam-1", name, points: zonePoints, color: $("#zone-color").value,
      capacity: capacityRaw ? Number(capacityRaw) : null,
      queue_cooldown_s: Number($("#zone-cooldown").value) || 60,
    }),
  });
  if (!r.ok) return alert("Could not save zone: " + (await r.text()));
  zonePoints = [];
  $("#zone-name").value = "";
  $("#zone-capacity").value = "";
  loadZonesList();
});

$("#zones-list").addEventListener("click", async (e) => {
  const row = e.target.closest(".rule-row");
  if (!row || e.target.dataset.act !== "delete") return;
  await fetch(`${API}/zones/${row.dataset.id}`, { method: "DELETE" });
  loadZonesList();
});

// -------------------------------------------------------------- footfall
let linePoints = [];
let lineFlipped = false;
let countLines = [];

function sizeLineCanvas() {
  const img = $("#line-bg"), c = $("#line-canvas");
  c.width = img.clientWidth; c.height = img.clientHeight;
  redrawLineCanvas();
}

function drawDirectionArrow(ctx, pts, positiveIsIn, color, w, h) {
  if (pts.length !== 2) return;
  const [ax, ay] = pts[0], [bx, by] = pts[1];
  const mx = (ax + bx) / 2 * w, my = (ay + by) / 2 * h;
  const dx = (bx - ax) * w, dy = (by - ay) * h;
  const len = Math.hypot(dx, dy) || 1;
  // perpendicular (-dy, dx) points toward the "positive" side, matching the
  // backend's _signed_side cross-product convention (phase10_footfall/counter.py)
  let px = -dy / len, py = dx / len;
  if (!positiveIsIn) { px = -px; py = -py; }
  const arrowLen = 26;
  const ex = mx + px * arrowLen, ey = my + py * arrowLen;
  ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(mx, my); ctx.lineTo(ex, ey); ctx.stroke();
  const ang = Math.atan2(ey - my, ex - mx);
  ctx.beginPath();
  ctx.moveTo(ex, ey);
  ctx.lineTo(ex - 8 * Math.cos(ang - Math.PI / 6), ey - 8 * Math.sin(ang - Math.PI / 6));
  ctx.lineTo(ex - 8 * Math.cos(ang + Math.PI / 6), ey - 8 * Math.sin(ang + Math.PI / 6));
  ctx.closePath(); ctx.fill();
  ctx.font = "700 11px 'IBM Plex Mono', monospace";
  ctx.fillText("IN", ex + px * 6, ey + py * 6);
}

function redrawLineCanvas() {
  const c = $("#line-canvas"), ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  countLines.forEach((l) => {
    drawPolygon(ctx, l.points, l.color, c.width, c.height, l.name, true);
    drawDirectionArrow(ctx, l.points, l.positive_is_in, l.color, c.width, c.height);
  });
  if (linePoints.length) {
    const color = $("#line-color").value;
    drawPolygon(ctx, linePoints, color, c.width, c.height, null, true);
    if (linePoints.length === 2) drawDirectionArrow(ctx, linePoints, !lineFlipped, color, c.width, c.height);
  }
}

async function loadLineFrame() {
  $("#line-bg").src = `live/frame.jpg?_=${Date.now()}`;
}
$("#line-bg").addEventListener("load", sizeLineCanvas);
window.addEventListener("resize", () => { if ($("#page-footfall").classList.contains("active")) sizeLineCanvas(); });

function lineRow(l) {
  return `<div class="rule-row" data-id="${l.id}">
    <div>
      <div class="name" style="color:${l.color}">⇅ ${esc(l.name)}</div>
      <div class="cond">${esc(l.camera_id)} · in ${l.entries} · out ${l.exits} · net ${l.net}</div>
    </div>
    <span></span>
    <div class="alertbtns" data-id="${l.id}">
      <button data-act="reset">reset</button><button data-act="delete">delete</button>
    </div>
  </div>`;
}

async function loadCountLines() {
  const d = await getJSON(`${API}/count-lines/summary`);
  countLines = d.lines;
  $("#lines-list").innerHTML = d.lines.length
    ? d.lines.map(lineRow).join("")
    : `<p class="hint">No counting lines yet.</p>`;
  redrawLineCanvas();
}

$("#line-canvas").addEventListener("click", (e) => {
  const rect = e.target.getBoundingClientRect();
  const x = (e.clientX - rect.left) / rect.width;
  const y = (e.clientY - rect.top) / rect.height;
  if (linePoints.length >= 2) linePoints = [];   // a line is always exactly 2 points - start over
  linePoints.push([Number(x.toFixed(4)), Number(y.toFixed(4))]);
  redrawLineCanvas();
});
$("#line-flip").addEventListener("click", () => { lineFlipped = !lineFlipped; redrawLineCanvas(); });
$("#line-cancel").addEventListener("click", () => { linePoints = []; redrawLineCanvas(); });
$("#line-refresh").addEventListener("click", loadLineFrame);

$("#line-finish").addEventListener("click", async () => {
  if (linePoints.length !== 2) return alert("Click exactly 2 points to draw a line.");
  const name = $("#line-name").value.trim();
  if (!name) return alert("Give the line a name.");
  const r = await fetch(`${API}/count-lines`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      camera_id: state.cameraId || "cam-1", name, points: linePoints,
      color: $("#line-color").value, positive_is_in: !lineFlipped,
    }),
  });
  if (!r.ok) return alert("Could not save line: " + (await r.text()));
  linePoints = [];
  lineFlipped = false;
  $("#line-name").value = "";
  loadCountLines();
});

$("#lines-list").addEventListener("click", async (e) => {
  const btns = e.target.closest(".alertbtns");
  if (!btns) return;
  const id = btns.dataset.id;
  if (e.target.dataset.act === "delete") {
    await fetch(`${API}/count-lines/${id}`, { method: "DELETE" });
  } else if (e.target.dataset.act === "reset") {
    await fetch(`${API}/count-lines/${id}/reset`, { method: "POST" });
  } else {
    return;
  }
  loadCountLines();
  refreshStats();
});

// -------------------------------------------------------------- attendance
function todayStr() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const CAMERA_ROLE_LABEL = { entry: "entry", exit: "exit", both: "both (entry+exit)", internal: "internal (not a doorway)" };

function cameraRoleRow(cameraId, role) {
  const opts = Object.entries(CAMERA_ROLE_LABEL)
    .map(([v, label]) => `<option value="${v}" ${v === role ? "selected" : ""}>${label}</option>`).join("");
  return `<div class="rule-row" data-camera="${esc(cameraId)}">
    <div><div class="name">${esc(cameraId)}</div></div>
    <select data-role-for="${esc(cameraId)}">${opts}</select>
    <span></span>
  </div>`;
}

async function loadCameraRoles() {
  const d = await getJSON(`${API}/camera-roles`);
  const cameraIds = Array.from(new Set([...d.known_camera_ids, ...Object.keys(d.roles)])).sort();
  $("#camera-roles-list").innerHTML = cameraIds.length
    ? cameraIds.map((c) => cameraRoleRow(c, d.roles[c] || "internal")).join("")
    : `<p class="hint">No cameras have logged an event yet.</p>`;
}

$("#camera-roles-list").addEventListener("change", async (e) => {
  const cameraId = e.target.dataset.roleFor;
  if (!cameraId) return;
  await fetch(`${API}/camera-roles`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ camera_id: cameraId, role: e.target.value }),
  });
  loadAttendance();   // roles changed - the currently viewed day's entry/exit may too
});

const AVATAR_COLORS = ["#3b82f6", "#2ea043", "#d29922", "#db6d28", "#a371f7", "#f85149"];
function initialsAvatar(name) {
  const label = (name || "?").trim();
  const initials = label.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase() || "?";
  let hash = 0;
  for (let i = 0; i < label.length; i++) hash = (hash * 31 + label.charCodeAt(i)) >>> 0;
  const color = AVATAR_COLORS[hash % AVATAR_COLORS.length];
  return `<span style="display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;
    border-radius:999px;background:${color}30;color:${color};font-size:.68rem;font-weight:700;flex:none">${esc(initials)}</span>`;
}

function attendanceRow(a) {
  const entry = a.entry_ts != null ? `${fmtTime(a.entry_ts)} · ${esc(a.entry_camera)}` : "—";
  const exit = a.exit_ts != null ? `${fmtTime(a.exit_ts)} · ${esc(a.exit_camera)}` : "—";
  const status = a.entry_ts == null ? `<span class="badge badge-muted">not detected</span>` : a.exit_ts == null
    ? `<span class="badge badge-good">present</span>` : `<span class="badge badge-muted">departed</span>`;
  return `<tr>
    <td><div style="display:flex;align-items:center;gap:10px">${initialsAvatar(a.employee_name || a.employee_code)}
      <span class="cell-title">${esc(a.employee_name || a.employee_code)}</span></div></td>
    <td class="cell-muted">${entry}</td>
    <td class="cell-muted">${exit}</td>
    <td>${status}</td>
  </tr>`;
}

async function loadAttendance() {
  const dateStr = $("#attendance-date").value || todayStr();
  const d = await getJSON(`${API}/attendance?date=${encodeURIComponent(dateStr)}`);
  $("#attendance-list").innerHTML = d.attendance.length
    ? d.attendance.map(attendanceRow).join("")
    : `<tr><td colspan="4" class="cell-muted">No recognized employees for this date.</td></tr>`;
}

$("#attendance-date").addEventListener("change", loadAttendance);
$("#attendance-today").addEventListener("click", () => { $("#attendance-date").value = todayStr(); loadAttendance(); });

// ----------------------------------------------------------- live tracking
let trackedEmployee = null;

async function loadTrackEmployeeOptions() {
  const d = await getJSON(`${API}/employees`);
  const sel = $("#track-employee");
  const current = sel.value;
  sel.innerHTML = `<option value="">— select —</option>` +
    d.employees.map((e) => `<option value="${esc(e.code)}">${esc(e.name || e.code)}</option>`).join("");
  sel.value = current;
}

function relTime(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function renderTrackCurrent(d) {
  if (!d.current_camera) {
    $("#track-current").innerHTML = `<p class="hint">No sightings for this employee today yet.</p>`;
    return;
  }
  const stale = Date.now() / 1000 - d.last_seen_ts > 300;   // 5+ min old - probably out of view now
  $("#track-current").innerHTML = `<div class="rule-row">
    <div>
      <div class="name">${stale ? "Last seen" : "Currently on"}: ${esc(d.current_camera)}</div>
      <div class="cond">${fmtTime(d.last_seen_ts)} · ${relTime(d.last_seen_ts)}${stale ? " - stale" : ""}</div>
    </div>
    <span class="badge ${stale ? "UNVERIFIED" : "RECOGNIZED"}">${stale ? "stale" : "live"}</span>
    <span></span>
  </div>`;
}

function trackPathRow(p) {
  return `<div class="rule-row">
    <div><div class="name">${esc(p.camera_id)}</div></div>
    <div class="cond">${fmtTime(p.ts)}</div>
    <span></span>
  </div>`;
}

async function loadTrack() {
  if (!trackedEmployee) {
    $("#track-current").innerHTML = "";
    $("#track-path").innerHTML = `<p class="hint">Select an employee above.</p>`;
    return;
  }
  const d = await getJSON(`${API}/track/${encodeURIComponent(trackedEmployee)}`);
  renderTrackCurrent(d);
  $("#track-path").innerHTML = d.path.length
    ? d.path.map(trackPathRow).join("")
    : `<p class="hint">No sightings yet today.</p>`;
}

$("#track-employee").addEventListener("change", (e) => {
  trackedEmployee = e.target.value || null;
  loadTrack();
});

// ------------------------------------------------------------------ vehicles
function vehicleLogRow(v) {
  const entry = v.entry_ts != null ? `${fmtTime(v.entry_ts)} · ${esc(v.entry_camera)}` : "—";
  const exit = v.exit_ts != null ? `${fmtTime(v.exit_ts)} · ${esc(v.exit_camera)}` : "—";
  const status = v.entry_ts == null ? `<span class="badge badge-muted">unknown</span>` : v.exit_ts == null
    ? `<span class="badge badge-good">on-site</span>` : `<span class="badge badge-muted">departed</span>`;
  return `<tr>
    <td class="cell-mono cell-title">${esc(v.plate_number)}</td>
    <td class="cell-muted">${entry}</td>
    <td class="cell-muted">${exit}</td>
    <td>${status}</td>
  </tr>`;
}

async function loadVehicleLog() {
  const dateStr = $("#vehicle-log-date").value || todayStr();
  const d = await getJSON(`${API}/vehicle-log?date=${encodeURIComponent(dateStr)}`);
  $("#vehicle-log-list").innerHTML = d.log.length
    ? d.log.map(vehicleLogRow).join("")
    : `<tr><td colspan="4" class="cell-muted">No vehicles for this date.</td></tr>`;
}

function vehicleEventRow(e) {
  const snap = e.snapshot ? `${API}/vehicle-events/${e.id}/snapshot` : "";
  return `<tr>
    <td><img class="thumb" loading="lazy" src="${snap}" onerror="this.style.visibility='hidden'"></td>
    <td class="cell-mono cell-title">${esc(e.plate_number)}</td>
    <td class="cell-mono">${esc(e.camera_id)}</td>
    <td class="cell-muted">${fmtTime(e.ts)}</td>
    <td class="cell-muted">${e.vote_count} votes</td>
    <td class="conf">${(e.confidence * 100).toFixed(0)}%</td>
  </tr>`;
}

async function loadVehicleEvents() {
  const d = await getJSON(`${API}/vehicle-events?limit=50`);
  $("#vehicle-events-list").innerHTML = d.events.length
    ? d.events.map(vehicleEventRow).join("")
    : `<tr><td colspan="6" class="cell-muted">No plate reads yet.</td></tr>`;
}

$("#vehicle-log-date").addEventListener("change", loadVehicleLog);
$("#vehicle-log-today").addEventListener("click", () => { $("#vehicle-log-date").value = todayStr(); loadVehicleLog(); });

// ---------------------------------------------------------------- shelves
function stockColor(pct, threshold) {
  if (pct <= threshold * 0.5) return "var(--crit)";
  if (pct <= threshold) return "var(--warn)";
  return "var(--good)";
}

function shelfRow(s) {
  const chk = s.last_check;
  const pct = chk ? chk.pct_full : null;
  const bar = pct != null
    ? `<div class="stockbar"><i style="width:${Math.min(100, pct * 100)}%;background:${stockColor(pct, s.low_stock_threshold)}"></i></div>
       <div class="stockpct">${chk.detected_count}/${s.capacity} · ${(pct * 100).toFixed(0)}%</div>`
    : `<div class="stockpct">no checks yet</div>`;
  return `<tr data-id="${s.id}">
    <td class="cell-title">${esc(s.name)}</td>
    <td class="cell-mono">${esc(s.camera_id)}</td>
    <td class="cell-muted">${s.product_name ? esc(s.product_name) : "—"}</td>
    <td class="cell-muted">${s.capacity}</td>
    <td>${bar}</td>
    <td class="cell-actions"><button class="btn-link danger" data-act="delete">Delete</button></td>
  </tr>`;
}

async function loadShelves() {
  const d = await getJSON(`${API}/shelves/summary`);
  $("#shelves-list").innerHTML = d.shelves.length
    ? d.shelves.map(shelfRow).join("")
    : `<tr><td colspan="6" class="cell-muted">No shelves yet - add one above.</td></tr>`;
}

function shelfAlertRow(a) {
  const btns = a.status === "open"
    ? `<div class="alertbtns" data-id="${a.id}"><button data-act="ack">ack</button><button data-act="closed">close</button></div>`
    : `<span class="conf">${esc(a.status)}</span>`;
  return `<div class="rule-row" data-id="${a.id}">
    <div>
      <div class="name">${esc(a.shelf_name)} <span class="prio prio-high">restock</span></div>
      <div class="cond">${a.detected_count}/${a.capacity} · ${(a.pct_full * 100).toFixed(0)}% · ${fmtTime(a.created_at)}</div>
    </div>
    ${btns}
  </div>`;
}

async function loadShelfAlerts() {
  const d = await getJSON(`${API}/shelf-alerts?status=open`);
  $("#shelf-alerts-list").innerHTML = d.alerts.length
    ? d.alerts.map(shelfAlertRow).join("")
    : `<p class="hint">No open restock alerts.</p>`;
}

$("#shelves-list").addEventListener("click", async (e) => {
  const row = e.target.closest("tr[data-id]");
  if (!row || e.target.dataset.act !== "delete") return;
  await fetch(`${API}/shelves/${row.dataset.id}`, { method: "DELETE" });
  loadShelves();
});

$("#shelf-alerts-list").addEventListener("click", async (e) => {
  const btns = e.target.closest(".alertbtns");
  if (!btns) return;
  await fetch(`${API}/shelf-alerts/${btns.dataset.id}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status: e.target.dataset.act }),
  });
  loadShelfAlerts();
  refreshStats();
});

$("#shelf-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = {
    name: f.get("name"),
    camera_id: f.get("camera_id"),
    product_name: f.get("product_name") || null,
    capacity: Number(f.get("capacity")),
    low_stock_threshold: Number(f.get("low_stock_threshold_pct")) / 100,
    cooldown_s: Number(f.get("cooldown_s")) || 0,
    notify_email: f.get("notify_email") === "on",
  };
  const r = await fetch(`${API}/shelves`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (r.ok) { e.target.reset(); closeDrawer("shelf-drawer"); loadShelves(); }
  else alert("Could not add shelf: " + (await r.text()));
});

// ---------------------------------------------------------------- wire-up
$("#event-list").addEventListener("click", (ev) => {
  const btns = ev.target.closest(".alertbtns");
  if (btns) { ev.stopPropagation(); return setAlert(btns.dataset.id, ev.target.dataset.act); }
  const row = ev.target.closest(".event-row[data-id]");
  if (row) openModal(row.dataset.id);
});
$("#modal-close").addEventListener("click", () => ($("#modal").hidden = true));
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").hidden = true; });
["f-status", "f-alert", "f-q"].forEach((id) =>
  $("#" + id).addEventListener("input", (e) => {
    state.filter = { status: $("#f-status").value, alert: $("#f-alert").value, q: $("#f-q").value };
    renderEvents();
  }));

// ---------------------------------------------------------------- page nav
const PAGES = ["dashboard", "shelves", "zones", "footfall", "attendance", "track", "vehicles", "rules"];
const PAGE_TITLES = {
  dashboard: "Dashboard", shelves: "Shelf Configuration", zones: "Zones", footfall: "Footfall",
  attendance: "Attendance", track: "Track", vehicles: "Vehicles", rules: "Rules",
};

function onPageShown(name) {
  if (name === "shelves") { loadShelves(); loadShelfAlerts(); }
  else if (name === "zones") { zonePoints = []; loadZoneFrame(); loadZonesList(); loadQueueAlerts(); }
  else if (name === "footfall") { linePoints = []; lineFlipped = false; loadLineFrame(); loadCountLines(); }
  else if (name === "attendance") { $("#attendance-date").value = todayStr(); loadCameraRoles(); loadAttendance(); }
  else if (name === "track") { loadTrackEmployeeOptions(); loadTrack(); }
  else if (name === "vehicles") { $("#vehicle-log-date").value = todayStr(); loadVehicleLog(); loadVehicleEvents(); }
  else if (name === "rules") { loadRules(); }
}

function showPage(name) {
  if (!PAGES.includes(name)) return;
  PAGES.forEach((p) => $("#page-" + p).classList.toggle("active", p === name));
  document.querySelectorAll(".nav-item").forEach((el) => el.classList.toggle("active", el.dataset.page === name));
  $("#page-title").textContent = PAGE_TITLES[name];
  onPageShown(name);
}

document.querySelectorAll(".nav-item").forEach((el) => el.addEventListener("click", () => showPage(el.dataset.page)));

function openDrawer(id) {
  $("#" + id).hidden = false;
  $("#" + id).querySelector("input, select, textarea")?.focus();
}
function closeDrawer(id) { $("#" + id).hidden = true; }

$("#shelf-add-btn").addEventListener("click", () => openDrawer("shelf-drawer"));
$("#shelf-drawer-close").addEventListener("click", () => closeDrawer("shelf-drawer"));
$("#shelf-drawer").addEventListener("click", (e) => { if (e.target.id === "shelf-drawer") closeDrawer("shelf-drawer"); });

$("#rule-add-btn").addEventListener("click", () => openDrawer("rule-drawer"));
$("#rule-drawer-close").addEventListener("click", () => closeDrawer("rule-drawer"));
$("#rule-drawer").addEventListener("click", (e) => { if (e.target.id === "rule-drawer") closeDrawer("rule-drawer"); });

setInterval(() => ($("#clock").textContent = new Date().toLocaleTimeString([], { hour12: false })), 1000);
setInterval(refreshStats, 3000);

loadEvents().catch(console.warn);
refreshStats();
connectWS();
