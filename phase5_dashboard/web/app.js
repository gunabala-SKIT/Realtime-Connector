"use strict";
const $ = (s) => document.querySelector(s);
const API = "api";

const state = { events: [], filter: { status: "", alert: "", q: "" }, lastId: 0 };

// ---------------------------------------------------------------- formatting
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
const fmtDateTime = (ts) => new Date(ts * 1000).toLocaleString([], { hour12: false });
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ---------------------------------------------------------------- rendering
function eventRow(e) {
  const who = e.identity_status === "RECOGNIZED"
    ? esc(e.employee_name || e.employee_code)
    : e.identity_status;
  const conf = e.confidence != null ? e.confidence.toFixed(2) : "–";
  const snap = e.snapshot ? `${API}/events/${e.id}/snapshot` : "";
  const alert = e.alert_status === "open"
    ? `<div class="alertbtns" data-id="${e.id}">
         <button data-act="ack">ack</button><button data-act="closed">close</button></div>`
    : (e.alert_status !== "none" ? `<span class="conf">${esc(e.alert_status)}</span>` : "");
  const prio = e.alert_priority
    ? `<span class="prio prio-${e.alert_priority}">${e.alert_priority}</span>` : "";
  return `<div class="event" data-id="${e.id}">
    <img loading="lazy" src="${snap}" onerror="this.style.visibility='hidden'">
    <div>
      <div class="who">${who}</div>
      <div class="meta">${fmtTime(e.ts)} · ${esc(e.camera_id)} · track ${e.track_id}</div>
    </div>
    <div class="right">
      <span class="badge ${e.identity_status}">${e.identity_status}</span>
      ${prio}
      <span class="conf">${conf}</span>
      ${alert}
    </div>
  </div>`;
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
    : `<div class="event" style="cursor:default"><div></div><div class="meta">no matching events</div><div></div></div>`;
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
    $("#cam-label").textContent = live.camera_id || "cam";
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
    const [stats, emp, live] = await Promise.all([
      getJSON(`${API}/stats?window=today`),
      getJSON(`${API}/employees`),
      getJSON(`live/status`),
    ]);
    setTiles(stats, emp, live);
  } catch (e) { console.warn(e); }
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
  if (r.time_start && r.time_end) parts.push(`${r.time_start}–${r.time_end}`);
  parts.push(`cooldown ${r.cooldown_s}s`);
  if (r.notify_email) parts.push("email");
  return parts.join("  ·  ");
}

function ruleRow(r) {
  return `<div class="rule-row" data-id="${r.id}">
    <div>
      <div class="name">${esc(r.name)} <span class="prio prio-${r.priority}">${r.priority}</span></div>
      <div class="cond">${condSummary(r)}</div>
    </div>
    <label title="enabled"><input type="checkbox" class="toggle" ${r.enabled ? "checked" : ""}></label>
    <button data-act="delete">delete</button>
  </div>`;
}

async function loadRules() {
  const d = await getJSON(`${API}/rules`);
  $("#rules-list").innerHTML = d.rules.length
    ? d.rules.map(ruleRow).join("")
    : `<p class="hint">No rules yet - nothing will raise an alert.</p>`;
}

$("#rules-btn").addEventListener("click", () => { $("#rules-modal").hidden = false; loadRules(); });
$("#rules-close").addEventListener("click", () => ($("#rules-modal").hidden = true));
$("#rules-modal").addEventListener("click", (e) => { if (e.target.id === "rules-modal") $("#rules-modal").hidden = true; });

$("#rules-list").addEventListener("click", async (e) => {
  const row = e.target.closest(".rule-row");
  if (!row) return;
  const id = row.dataset.id;
  if (e.target.matches(".toggle")) {
    await fetch(`${API}/rules/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: e.target.checked }),
    });
  } else if (e.target.dataset.act === "delete") {
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
    time_start: f.get("time_start") || null,
    time_end: f.get("time_end") || null,
    priority: f.get("priority"),
    cooldown_s: Number(f.get("cooldown_s")) || 0,
    notify_email: f.get("notify_email") === "on",
  };
  const r = await fetch(`${API}/rules`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (r.ok) { e.target.reset(); loadRules(); }
  else console.warn("add rule failed", await r.text());
});

// ---------------------------------------------------------------- wire-up
$("#event-list").addEventListener("click", (ev) => {
  const btns = ev.target.closest(".alertbtns");
  if (btns) { ev.stopPropagation(); return setAlert(btns.dataset.id, ev.target.dataset.act); }
  const row = ev.target.closest(".event[data-id]");
  if (row) openModal(row.dataset.id);
});
$("#modal-close").addEventListener("click", () => ($("#modal").hidden = true));
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").hidden = true; });
["f-status", "f-alert", "f-q"].forEach((id) =>
  $("#" + id).addEventListener("input", (e) => {
    state.filter = { status: $("#f-status").value, alert: $("#f-alert").value, q: $("#f-q").value };
    renderEvents();
  }));

setInterval(() => ($("#clock").textContent = new Date().toLocaleTimeString([], { hour12: false })), 1000);
setInterval(refreshStats, 3000);

loadEvents().catch(console.warn);
refreshStats();
connectWS();
