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

let ppeSeenTotal = null;  // today's PPE violation count at the last refresh

async function refreshStats() {
  try {
    const [stats, emp, live, shelves, zones, lines, attendanceToday, vehiclesToday, ppe] = await Promise.all([
      getJSON(`${API}/stats?window=today`),
      getJSON(`${API}/employees`),
      getJSON(`live/status`),
      getJSON(`${API}/shelves/summary`).catch(() => ({ shelves: [] })),
      getJSON(`${API}/zones/summary`).catch(() => ({ zones: [] })),
      getJSON(`${API}/count-lines/summary`).catch(() => ({ lines: [] })),
      getJSON(`${API}/attendance`).catch(() => ({ attendance: [] })),
      getJSON(`${API}/vehicle-log`).catch(() => ({ log: [] })),
      getJSON(`${API}/ppe-violations/summary`).catch(() => null),
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
    if (ppe) {
      $("#t-ppe").textContent = ppe.today.total;
      if (ppeSeenTotal !== null && ppe.today.total > ppeSeenTotal) {
        playAlertSound();
        showToast(`New PPE violation - see Safety (PPE)`);
      }
      ppeSeenTotal = ppe.today.total;
    }
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
      <div class="name" style="color:${z.color}">■ ${esc(z.name)}${z.ppe_required ? ` <span class="badge badge-info">PPE</span>` : ""}</div>
      <div class="cond">${esc(z.camera_id)} · ${z.points.length} points${z.capacity ? ` · capacity ${z.capacity}` : ""}${z.ppe_required ? ` · requires ${esc(z.ppe_required.split(",").join(", "))}` : ""}</div>
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
  const ppeBoxes = $("#zone-ppe-on").checked ? [...document.querySelectorAll("#zone-ppe input:checked")] : [];
  if ($("#zone-ppe-on").checked && !ppeBoxes.length) return alert("Tick at least one PPE item, or turn PPE zone off.");
  const r = await fetch(`${API}/zones`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      camera_id: state.cameraId || "cam-1", name, points: zonePoints, color: $("#zone-color").value,
      capacity: capacityRaw ? Number(capacityRaw) : null,
      queue_cooldown_s: Number($("#zone-cooldown").value) || 60,
      ppe_required: ppeBoxes.map((b) => b.value).join(",") || null,
    }),
  });
  if (!r.ok) return alert("Could not save zone: " + (await r.text()));
  zonePoints = [];
  $("#zone-name").value = "";
  $("#zone-capacity").value = "";
  $("#zone-ppe-on").checked = false;
  $("#zone-ppe").hidden = true;
  loadZonesList();
});

// Turning "PPE zone" on pre-ticks the configured default items (PPE_REQUIRED_ITEMS).
$("#zone-ppe-on").addEventListener("change", async (e) => {
  $("#zone-ppe").hidden = !e.target.checked;
  if (!e.target.checked) return;
  let defaults = ["helmet", "vest"];
  try { defaults = (await getJSON(`${API}/ppe/status`)).default_items; } catch (err) { /* keep fallback */ }
  document.querySelectorAll("#zone-ppe input").forEach((b) => { b.checked = defaults.includes(b.value); });
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

// ---------------------------------------------------------------- safety (PPE)
const PPE_LABEL = { helmet: "Helmet", vest: "Vest", gloves: "Gloves", boots: "Boots", goggles: "Goggles" };
const PPE_STATE = {
  worn: ["badge-good", "worn"], missing: ["badge-crit", "missing"],
  not_seen: ["badge-muted", "not seen"], unknown: ["badge-muted", "can't tell"],
};
let ppeTimer = null;
let ppeViolations = [];

const ppeWho = (p) => p.employee_name
  ? esc(p.employee_name)
  : `Track ${p.track_id} <span class="muted">· ${esc((p.identity_status || "unverified").toLowerCase())}</span>`;

function ppeChip(item, s, failing) {
  // not_seen only counts against helmet/vest (the server says which items are failing)
  const [cls, word] = s === "not_seen" && failing ? ["badge-crit", "not seen"] : PPE_STATE[s] || PPE_STATE.unknown;
  return `<span class="badge ${cls}">${PPE_LABEL[item] || esc(item)}: ${word}</span>`;
}

function renderPpeLive(st) {
  $("#ppe-disabled").hidden = st.enabled;
  if (!st.enabled) $("#ppe-disabled").innerHTML = `<p><b>PPE checking is off.</b> ${esc(st.disabled_reason || "")}</p>`;
  const people = (st.live && st.live.people) || [];
  $("#ppe-t-people").textContent = st.enabled ? people.length : "–";
  $("#ppe-t-ok").textContent = st.enabled ? people.filter((p) => !p.failing.length).length : "–";
  $("#ppe-live-meta").textContent = st.enabled && st.live.ts
    ? `checked ${relTimeShort(st.live.ts)} · ${st.infer_ms} ms per check` : "not checking";
  $("#ppe-live").innerHTML = people.length ? people.map((p) => {
    const confirming = Object.entries(p.streak || {}).filter(([, n]) => n > 0 && n < st.confirm_checks)
      .map(([i, n]) => `${PPE_LABEL[i] || i} ${n}/${st.confirm_checks}`);
    return `<div class="rule-row ppe-person">
      <div>
        <div class="name">${ppeWho(p)}</div>
        <div class="cond">zone ${esc(p.zone_name)}${confirming.length ? ` · confirming: ${esc(confirming.join(", "))}` : ""}</div>
      </div>
      <div class="ppe-chips">${Object.entries(p.items).map(([i, s]) => ppeChip(i, s, p.failing.includes(i))).join("")}</div>
      <span class="badge ${p.failing.length ? "badge-crit" : "badge-good"}">${p.failing.length ? "Not compliant" : "Compliant"}</span>
    </div>`;
  }).join("") : `<p class="hint">No one is standing in a PPE zone right now.</p>`;
}

function ppeRow(v) {
  const missing = v.missing.split(",").filter(Boolean)
    .map((m) => `<span class="badge badge-crit">No ${esc((PPE_LABEL[m] || m).toLowerCase())}</span>`).join(" ");
  const who = v.employee_name ? esc(v.employee_name)
    : `Track ${v.track_id} <span class="cell-sub">${esc((v.identity_status || "").toLowerCase())}</span>`;
  const status = v.status === "open"
    ? `<div class="alertbtns" data-id="${v.id}"><button data-act="ack">ack</button><button data-act="closed">close</button></div>`
    : `<span class="badge badge-muted">${v.status === "ack" ? "acknowledged" : "closed"}</span>`;
  const when = new Date(v.ts * 1000).toDateString() === new Date().toDateString() ? fmtTime(v.ts) : fmtDateTime(v.ts);
  return `<tr class="ppe-row" data-id="${v.id}">
    <td>${v.snapshot ? `<img class="thumb" loading="lazy" src="${API}/ppe-violations/${v.id}/snapshot" alt="evidence">` : "—"}</td>
    <td class="cell-muted">${when}</td>
    <td>${who}</td>
    <td class="cell-muted">${esc(v.zone_name || "—")}</td>
    <td>${missing}</td>
    <td>${status}</td>
  </tr>`;
}

async function loadPpe() {
  const status = $("#ppe-f-status").value;
  const windowSel = $("#ppe-f-window").value;
  const qs = new URLSearchParams({ limit: "200" });
  if (status) qs.set("status", status);
  if (windowSel) qs.set("window", windowSel);
  try {
    const [st, sum, list] = await Promise.all([
      getJSON(`${API}/ppe/status`),
      getJSON(`${API}/ppe-violations/summary`),
      getJSON(`${API}/ppe-violations?${qs}`),
    ]);
    renderPpeLive(st);
    $("#ppe-t-today").textContent = sum.today.total;
    $("#ppe-t-open").textContent = sum.today.open;
    ppeViolations = list.violations;
    $("#ppe-list").innerHTML = ppeViolations.length ? ppeViolations.map(ppeRow).join("")
      : `<tr><td colspan="6" class="cell-muted">No violations${windowSel === "today" ? " today" : ""}.</td></tr>`;
  } catch (e) { console.warn(e); }
}

function openPpeModal(v) {
  $("#modal-img").src = v.snapshot ? `${API}/ppe-violations/${v.id}/snapshot` : "";
  $("#modal-body").innerHTML = `
    <dt>violation</dt><dd>#${v.id}</dd>
    <dt>time</dt><dd>${fmtDateTime(v.ts)}</dd>
    <dt>missing</dt><dd>${esc(v.missing.split(",").join(", "))}</dd>
    <dt>person</dt><dd>${esc(v.employee_name || "-")} ${v.employee_code ? "(" + esc(v.employee_code) + ")" : ""}</dd>
    <dt>identity</dt><dd>${esc(v.identity_status || "-")}</dd>
    <dt>track</dt><dd>${v.track_id}</dd>
    <dt>zone</dt><dd>${esc(v.zone_name || "-")}</dd>
    <dt>camera</dt><dd>${esc(v.camera_id)}</dd>
    <dt>status</dt><dd>${esc(v.status)}</dd>`;
  $("#modal").hidden = false;
}

$("#ppe-list").addEventListener("click", async (e) => {
  const btns = e.target.closest(".alertbtns");
  if (btns && e.target.dataset.act) {
    await fetch(`${API}/ppe-violations/${btns.dataset.id}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: e.target.dataset.act }),
    });
    loadPpe();
    refreshStats();
    return;
  }
  const row = e.target.closest(".ppe-row");
  const v = row && ppeViolations.find((x) => x.id === Number(row.dataset.id));
  if (v) openPpeModal(v);
});
["ppe-f-status", "ppe-f-window"].forEach((id) => $("#" + id).addEventListener("change", loadPpe));

// ---------------------------------------------------------------- settings
const settings = { data: null, original: {}, dirty: new Set(), saving: false };
const RESTART_HOW = {
  dashboard: "Dashboard: stop it (Ctrl+C) and run <code>python -m phase5_dashboard</code> again.",
  shelf: "Shelf camera: restart <code>python -m phase8_shelf.app</code>.",
  vehicle: "Vehicle camera: restart <code>python -m phase13_vehicle.app</code>.",
};
const JSON_HEADERS = { "Content-Type": "application/json" };

// passwords are sent exactly as typed; everything else is trimmed
const controlValue = (el) =>
  el.type === "checkbox" ? (el.checked ? "true" : "false") : el.type === "password" ? el.value : el.value.trim();

function settingControl(f, disabled) {
  const attrs = `id="set-${f.key}" data-key="${f.key}"${disabled ? " disabled" : ""}`;
  const ph = esc(f.placeholder || (f.default ? `Default: ${f.default}` : ""));
  const value = f.value || f.default || "";
  if (f.type === "bool") {
    const on = value === "true";
    return `<label class="switch"><input type="checkbox" ${attrs}${on ? " checked" : ""}>
      <span class="track"></span><span class="switch-text">${on ? "On" : "Off"}</span></label>`;
  }
  if (f.type === "select") {
    const opts = f.options.includes(value) || !value ? f.options : [value, ...f.options];
    return `<select ${attrs}>${opts.map((o) =>
      `<option value="${esc(o)}"${o === value ? " selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
  }
  if (f.type === "secret") {
    return `<input type="password" autocomplete="new-password" ${attrs}
      placeholder="${f.is_set ? "Saved - leave blank to keep it" : "Not set"}">`;
  }
  if (f.type === "int" || f.type === "number") {
    const range = `${f.min != null ? ` min="${f.min}"` : ""}${f.max != null ? ` max="${f.max}"` : ""}`;
    return `<input type="number" ${attrs}${range} step="${f.type === "int" ? 1 : (f.step || "any")}"
      value="${esc(f.value)}" placeholder="${ph}">`;
  }
  return `<input type="text" autocomplete="off" spellcheck="false" ${attrs} value="${esc(f.value)}" placeholder="${ph}">`;
}

function settingField(f, disabled) {
  const badges = (f.pending ? `<span class="badge badge-warn">Restart pending</span>` : "") +
    (f.overridden ? `<span class="badge badge-muted" title="A system environment variable with this name takes priority over the .env file">Overridden by environment</span>` : "");
  return `<div class="setting${f.type === "camera_url" ? " wide" : ""}" data-field="${f.key}">
    <label class="setting-label" for="set-${f.key}">${esc(f.label)}${badges}</label>
    ${settingControl(f, disabled)}
    ${f.help ? `<div class="setting-help">${esc(f.help)}</div>` : ""}
    <div class="setting-error" hidden></div>
  </div>`;
}

function settingsSection(s, disabled) {
  const desc = esc(s.description).replace(/(python -m \S+)/, "<code>$1</code>");
  const test = s.camera_prefix === null ? "" : `<div class="camera-test" data-prefix="${esc(s.camera_prefix)}">
      <div class="camera-test-row">
        <button type="button" class="btn" data-act="test-camera"${disabled ? " disabled" : ""}>Test connection</button>
        <span class="test-status"></span>
      </div>
      <img class="test-preview" alt="Frame from the tested camera" hidden>
    </div>`;
  return `<section class="panel-card settings-card" data-section="${s.id}">
    <div class="card-head"><span>${esc(s.title)}</span><span class="card-desc">${desc}</span></div>
    <div class="settings-grid">${s.fields.map((f) => settingField(f, disabled)).join("")}${test}</div>
  </section>`;
}

function renderSettings(data) {
  settings.data = data;
  settings.original = {};
  settings.dirty.clear();
  $("#settings-env-file").textContent = data.env_file;
  $("#settings-readonly").hidden = data.can_edit;
  $("#settings-form").innerHTML = data.sections.map((s) => settingsSection(s, !data.can_edit)).join("");
  document.querySelectorAll("#settings-form [data-key]").forEach((el) => {
    settings.original[el.dataset.key] = controlValue(el);
  });
  const waiting = Object.entries(data.restart).filter(([, pending]) => pending).map(([p]) => `<p>${RESTART_HOW[p]}</p>`);
  $("#settings-restart").hidden = !waiting.length;
  $("#settings-restart").innerHTML = waiting.length
    ? `<p><b>Saved changes are waiting for a restart.</b></p>${waiting.join("")}` : "";
  updateSaveBar();
}

function updateSaveBar() {
  const n = settings.dirty.size;
  $("#settings-savebar").hidden = n === 0;
  $("#settings-savebar-text").textContent = `${n} unsaved change${n === 1 ? "" : "s"}`;
}

async function loadSettings() {
  if (settings.dirty.size) return;  // don't wipe edits in progress when revisiting the page
  try {
    renderSettings(await getJSON(`${API}/settings`));
  } catch (e) {
    $("#settings-form").innerHTML = `<p class="hint">Couldn't load settings: ${esc(e.message)}</p>`;
  }
}

function onSettingEdit(e) {
  const el = e.target.closest("[data-key]");
  if (!el) return;
  const v = controlValue(el);
  const dirty = el.type === "password" ? v !== "" : v !== settings.original[el.dataset.key];
  settings.dirty[dirty ? "add" : "delete"](el.dataset.key);
  const wrap = el.closest(".setting");
  wrap.classList.toggle("dirty", dirty);
  wrap.classList.remove("has-error");
  wrap.querySelector(".setting-error").hidden = true;
  if (el.type === "checkbox") wrap.querySelector(".switch-text").textContent = el.checked ? "On" : "Off";
  updateSaveBar();
}

function showSettingErrors(errors) {
  let first = null;
  for (const [key, msg] of Object.entries(errors)) {
    const wrap = document.querySelector(`#settings-form [data-field="${key}"]`);
    if (!wrap) continue;
    wrap.classList.add("has-error");
    const box = wrap.querySelector(".setting-error");
    box.textContent = msg;
    box.hidden = false;
    first = first || wrap;
  }
  if (first) first.scrollIntoView({ behavior: "smooth", block: "center" });
  showToast("Fix the highlighted fields, then save again");
}

async function saveSettings() {
  if (settings.saving || !settings.dirty.size) return;
  settings.saving = true;
  const btn = $("#settings-save");
  btn.disabled = true;
  btn.textContent = "Saving...";
  const values = {};
  settings.dirty.forEach((k) => {
    values[k] = controlValue(document.querySelector(`#settings-form [data-key="${k}"]`));
  });
  try {
    const r = await fetch(`${API}/settings`, { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ values }) });
    const body = await r.json().catch(() => ({}));
    if (r.ok) {
      renderSettings(body);
      showToast(body.restart.dashboard || body.restart.shelf || body.restart.vehicle
        ? "Saved - restart to apply" : "Saved");
    } else if (r.status === 400 && body.detail && body.detail.errors) {
      showSettingErrors(body.detail.errors);
    } else {
      showToast(typeof body.detail === "string" ? body.detail : `Couldn't save (error ${r.status})`);
    }
  } catch (e) {
    showToast("Couldn't save - is the dashboard still running?");
  } finally {
    settings.saving = false;
    btn.disabled = false;
    btn.textContent = "Save changes";
  }
}

async function testCamera(btn) {
  const box = btn.closest(".camera-test");
  const prefix = box.dataset.prefix;
  const val = (k) => {
    const el = document.querySelector(`#settings-form [data-key="${prefix}${k}"]`);
    return el ? controlValue(el) : "";
  };
  const status = box.querySelector(".test-status");
  const img = box.querySelector(".test-preview");
  btn.disabled = true;
  img.hidden = true;
  status.className = "test-status";
  status.textContent = "Connecting... this can take up to 15 seconds.";
  try {
    const r = await fetch(`${API}/settings/test-camera`, {
      method: "POST", headers: JSON_HEADERS,
      body: JSON.stringify({
        prefix, url: val("CAMERA_URL"), user: val("CAMERA_USER"),
        password: val("CAMERA_PASSWORD"), transport: val("RTSP_TRANSPORT") || "tcp",
      }),
    });
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.ok) {
      const bits = [d.resolution, d.fps ? `${d.fps} fps` : null,
        d.shared ? "the camera this dashboard is using now" : `${(d.elapsed_ms / 1000).toFixed(1)} s to connect`];
      status.className = "test-status ok";
      status.innerHTML = `Connected <span class="muted">· ${esc(bits.filter(Boolean).join(" · "))}</span>`;
      if (d.preview) { img.src = d.preview; img.hidden = false; }
    } else {
      status.className = "test-status fail";
      status.textContent = (r.ok ? d.error : (typeof d.detail === "string" ? d.detail : null)) || "Couldn't connect.";
    }
  } catch (e) {
    status.className = "test-status fail";
    status.textContent = "Test failed - is the dashboard still running?";
  } finally {
    btn.disabled = !settings.data.can_edit;
  }
}

let toastTimer = null;
function showToast(msg) {
  let t = document.querySelector(".toast");
  if (!t) {
    t = document.createElement("div");
    t.className = "toast";
    t.setAttribute("role", "status");
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3000);
}

$("#settings-form").addEventListener("input", onSettingEdit);
$("#settings-form").addEventListener("change", onSettingEdit);
$("#settings-form").addEventListener("click", (e) => {
  const btn = e.target.closest('[data-act="test-camera"]');
  if (btn) testCamera(btn);
});
$("#settings-save").addEventListener("click", saveSettings);
$("#settings-discard").addEventListener("click", () => settings.data && renderSettings(settings.data));
window.addEventListener("beforeunload", (e) => {
  if (settings.dirty.size) { e.preventDefault(); e.returnValue = ""; }
});

// ---------------------------------------------------------------- page nav
const PAGES = ["dashboard", "shelves", "zones", "footfall", "attendance", "track", "vehicles", "safety", "rules", "settings"];
const PAGE_TITLES = {
  dashboard: "Dashboard", shelves: "Shelf Configuration", zones: "Zones", footfall: "Footfall",
  attendance: "Attendance", track: "Track", vehicles: "Vehicles", safety: "Safety (PPE)", rules: "Rules",
  settings: "Settings",
};

function onPageShown(name) {
  clearInterval(ppeTimer);  // the Safety page's live panel polls only while it's open
  if (name === "safety") { loadPpe(); ppeTimer = setInterval(loadPpe, 2000); }
  else if (name === "shelves") { loadShelves(); loadShelfAlerts(); }
  else if (name === "zones") { zonePoints = []; loadZoneFrame(); loadZonesList(); loadQueueAlerts(); }
  else if (name === "footfall") { linePoints = []; lineFlipped = false; loadLineFrame(); loadCountLines(); }
  else if (name === "attendance") { $("#attendance-date").value = todayStr(); loadCameraRoles(); loadAttendance(); }
  else if (name === "track") { loadTrackEmployeeOptions(); loadTrack(); }
  else if (name === "vehicles") { $("#vehicle-log-date").value = todayStr(); loadVehicleLog(); loadVehicleEvents(); }
  else if (name === "rules") { loadRules(); }
  else if (name === "settings") { loadSettings(); }
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
