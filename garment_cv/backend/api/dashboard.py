"""The production dashboard (spec sections 14-18 plus the Odoo work-order link).

Five tabs on one page: Live Monitoring, Production Timeline, Activity History, Analytics and Odoo.
Plain server-rendered HTML with vanilla JavaScript - the same approach as every earlier phase, no
frontend build. The look lives in `ui.py` and is shared with the status page and the zone editor.

The live view draws its own overlay client-side on a `<canvas>` over the RAW `/live.mjpg` stream
(polling `/api/detections`, `/api/activities` and `/api/zones`) instead of asking the server for a
pre-annotated stream: `ObjectDetector` renders one shared annotated JPEG per detection cycle for
every viewer, so a per-viewer "hide zones" toggle (spec section 15) is impossible on that stream.

Design notes (see `ui.py`): system fonts only (the factory network may be offline), colour reserved
for meaning, two-step confirmation instead of pop-ups for actions that change Odoo, no `alert()`.
"""
from __future__ import annotations

from .ui import BASE_CSS, FAVICON, esc, fill, js

_PAGE_CSS = r"""
.job-order { font-size: 22px; font-weight: 600; letter-spacing: -.02em; color: var(--ink); }
.job-meta { color: var(--muted); font-size: 13.5px; margin-top: 2px; }
.mini-steps { list-style: none; margin: 18px 0 0; padding: 0; display: grid; gap: 0; }
.mini-steps li { display: grid; grid-template-columns: 22px 1fr auto; gap: 12px; align-items: center; padding: 11px 0; border-top: 1px solid var(--line); font-size: 14.5px; }
.mini-steps .mark { width: 22px; height: 22px; border-radius: 50%; border: 1.5px solid var(--line-strong); display: grid; place-items: center; }
.mini-steps .mark svg { width: 12px; height: 12px; }
.mini-steps .is-active .mark { border-color: var(--ink); }
.mini-steps .is-active .mark::after { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--ink); }
.mini-steps .is-done .mark { background: var(--green-fg); border-color: var(--green-fg); }
.mini-steps .name { color: var(--ink); font-weight: 500; }
.mini-steps .is-waiting .name { color: var(--muted); font-weight: 400; }

.person { display: grid; grid-template-columns: 40px minmax(0, 1fr) auto; gap: 4px 12px; align-items: center; padding: 14px 0; border-top: 1px solid var(--line); }
.person:first-child { border-top: 0; padding-top: 2px; }
.avatar { width: 40px; height: 40px; border-radius: 11px; background: var(--sunken); color: var(--ink); display: grid; place-items: center; font-weight: 600; font-size: 15px; }
.person-name { color: var(--ink); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.person-meta { color: var(--muted); font-size: 13px; }
.assign { grid-column: 1 / -1; display: flex; gap: 8px; margin-top: 8px; }
.assign .input { flex: 1; height: 34px; font-size: 14px; }

.flow { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; }
.step { position: relative; border: 1px solid var(--line); border-radius: var(--radius); padding: 22px; background: var(--surface); transition: border-color .3s var(--ease), background .3s var(--ease); }
.step:not(:last-child)::after { content: ""; position: absolute; right: -21px; top: 50%; width: 22px; height: 1px; background: var(--line-strong); }
.step-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 18px; }
.step-no { width: 28px; height: 28px; border-radius: 8px; background: var(--sunken); color: var(--muted); display: grid; place-items: center; font-weight: 600; font-size: 14px; }
.step h3 { font-size: 22px; font-weight: 600; letter-spacing: -.02em; }
.step p { color: var(--muted); font-size: 13.5px; margin-top: 4px; }
.step .meter { margin-top: 18px; }
.step .note { margin-top: 10px; font-size: 13px; color: var(--muted); min-height: 20px; }
.count { display: flex; align-items: baseline; gap: 10px; margin-top: 14px; }
.count-num { font-size: 44px; font-weight: 600; letter-spacing: -.035em; color: var(--ink); line-height: 1; }
.count-label { color: var(--text); font-size: 14.5px; }
.step.is-active { border-color: var(--ink); box-shadow: 0 0 0 1px var(--ink); }
.step.is-active .step-no { background: var(--ink); color: #fff; }
.step.is-done { background: var(--green-bg); border-color: #D5E3D3; }
.step.is-done .step-no { background: var(--green-fg); color: #fff; }
.step.is-waiting h3 { color: var(--muted); font-weight: 500; }

.facts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0; }
.fact { padding: 4px 20px 4px 0; }
.fact + .fact { padding-left: 20px; border-left: 1px solid var(--line); }
.fact .k { color: var(--muted); font-size: 13px; }
.fact .v { color: var(--ink); font-weight: 600; font-size: 17px; margin-top: 3px; overflow-wrap: anywhere; }
.finish-line { display: grid; gap: 10px; }
.finish-line .big { font-size: 28px; font-weight: 600; letter-spacing: -.03em; color: var(--ink); }
.see-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 0; border-top: 1px solid var(--line); }
.see-row:first-of-type { border-top: 0; padding-top: 0; }
details.manual { margin-top: 18px; border-top: 1px solid var(--line); padding-top: 16px; }
details.manual summary { cursor: pointer; color: var(--ink); font-weight: 500; font-size: 14.5px; list-style: none; display: flex; align-items: center; justify-content: space-between; }
details.manual summary::-webkit-details-marker { display: none; }
details.manual summary::after { content: "+"; font-size: 20px; line-height: 1; color: var(--muted); }
details.manual[open] summary::after { content: "\2212"; }
details.manual .manual-body { display: grid; gap: 10px; margin-top: 14px; }
.manual-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }

.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; padding: 0 24px 6px; color: var(--muted); font-size: 13px; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 7px; vertical-align: -1px; }
.tl { padding: 8px 24px 24px; }
.tl-row { display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: 16px; align-items: center; margin-top: 12px; }
.tl-name { color: var(--ink); font-size: 14px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tl-track { position: relative; height: 30px; background: var(--sunken); border-radius: 7px; overflow: hidden; }
.tl-block { position: absolute; top: 4px; bottom: 4px; border-radius: 4px; min-width: 3px; }
.tl-axis { display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: 16px; margin-top: 10px; color: var(--muted); font-size: 12.5px; }
.tl-axis div { display: flex; justify-content: space-between; }
.swatch { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 10px; vertical-align: -1px; }

@media (max-width: 1100px) { .flow { grid-template-columns: minmax(0, 1fr); } .step:not(:last-child)::after { display: none; } .facts { grid-template-columns: repeat(2, minmax(0, 1fr)); row-gap: 18px; } .fact:nth-child(3) { padding-left: 0; border-left: 0; } }
@media (max-width: 640px) { .tl-row, .tl-axis { grid-template-columns: 96px minmax(0, 1fr); } .facts { grid-template-columns: minmax(0, 1fr); } .fact + .fact { padding-left: 0; border-left: 0; } }
"""

_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Garment CV - __CAMERA_NAME__</title>
<meta name="description" content="Live production monitoring: what the camera sees at the work table and how Odoo work orders follow it.">
<meta name="theme-color" content="#F7F6F3">
<link rel="icon" href="__FAVICON__">
<style>
__BASE_CSS__
__PAGE_CSS__
</style>
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
<div class="shell">
  <header class="top">
    <div class="brand">
      <div class="brand-mark" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M10 22V10h12M10 16h8"/></svg></div>
      <div><h1>Garment CV</h1><div class="where">__CAMERA_NAME__</div></div>
    </div>
    <div class="chips" aria-live="polite">
      <span class="chip" id="chip-camera"><span class="dot"></span><span>Checking camera</span></span>
      <span class="chip" id="chip-odoo"><span class="dot"></span><span>Checking Odoo</span></span>
      <a class="chip" href="/zones">Zones</a>
      <a class="chip" href="/">System status</a>
    </div>
  </header>

  <nav class="tabs" role="tablist" aria-label="Dashboard sections">
    <button class="tab" role="tab" id="tab-live" data-view="live" aria-controls="view-live" aria-selected="true">Live monitoring</button>
    <button class="tab" role="tab" id="tab-timeline" data-view="timeline" aria-controls="view-timeline" aria-selected="false" tabindex="-1">Production timeline</button>
    <button class="tab" role="tab" id="tab-history" data-view="history" aria-controls="view-history" aria-selected="false" tabindex="-1">Activity history</button>
    <button class="tab" role="tab" id="tab-analytics" data-view="analytics" aria-controls="view-analytics" aria-selected="false" tabindex="-1">Analytics</button>
    <button class="tab" role="tab" id="tab-odoo" data-view="odoo" aria-controls="view-odoo" aria-selected="false" tabindex="-1">Odoo</button>
  </nav>

  <main id="main">
    <noscript><div class="banner warn">This dashboard needs JavaScript. Please enable it in your browser.</div></noscript>

    <!-- ============================== Live monitoring ============================== -->
    <section id="view-live" class="view active" role="tabpanel" aria-labelledby="tab-live" tabindex="-1">
      <div class="grid g-live">
        <div class="card flush">
          <div class="frame">
            <img id="bg" src="/live.mjpg" alt="Live camera view of the work table, with detected people and objects outlined">
            <canvas id="cv" aria-hidden="true"></canvas>
            <span class="frame-badge" id="frame-badge"><span class="dot"></span><span id="frame-badge-text">Live</span></span>
          </div>
          <div class="frame-bar" role="group" aria-label="Choose what is drawn on the picture">
            <span class="label">Show on picture</span>
            <label class="toggle-chip"><input type="checkbox" id="tg-boxes" checked> Outlines</label>
            <label class="toggle-chip"><input type="checkbox" id="tg-labels" checked> Names</label>
            <label class="toggle-chip"><input type="checkbox" id="tg-ids" checked> Tracking numbers</label>
            <label class="toggle-chip"><input type="checkbox" id="tg-zones" checked> Zones</label>
            <label class="toggle-chip"><input type="checkbox" id="tg-activity" checked> Activity</label>
          </div>
        </div>

        <div class="stack">
          <div class="card" id="job-card" aria-live="polite">
            <div class="card-head"><h2>Current job</h2><span class="badge b-gray" id="job-mode">Checking</span></div>
            <div id="job-body"><div class="skel" style="width:60%"></div><div class="skel" style="width:85%"></div><div class="skel" style="width:70%"></div></div>
          </div>
          <div class="card">
            <div class="card-head"><h2>People on camera</h2><span class="sub" id="people-count"></span></div>
            <div id="tracks"><div class="empty compact"><strong>No one in view</strong>People appear here as soon as the camera tracks them.</div></div>
          </div>
        </div>
      </div>
    </section>

    <!-- ============================== Production timeline ============================== -->
    <section id="view-timeline" class="view" role="tabpanel" aria-labelledby="tab-timeline" tabindex="-1" hidden>
      <div class="card flush">
        <div class="card-head">
          <div><h2>Production timeline</h2><span class="sub">One row per tracked worker. Blocks show the stage they were in, in order.</span></div>
          <div class="seg" id="tl-range" role="group" aria-label="Time range">
            <button type="button" data-hours="1" aria-pressed="false">Last hour</button>
            <button type="button" data-hours="8" aria-pressed="true">8 hours</button>
            <button type="button" data-hours="24" aria-pressed="false">24 hours</button>
            <button type="button" data-hours="0" aria-pressed="false">All</button>
          </div>
        </div>
        <div class="legend" id="tl-legend"></div>
        <div class="tl" id="timeline-body"><div class="skel" style="width:100%;height:30px"></div><div class="skel" style="width:100%;height:30px"></div></div>
      </div>
    </section>

    <!-- ============================== Activity history ============================== -->
    <section id="view-history" class="view" role="tabpanel" aria-labelledby="tab-history" tabindex="-1" hidden>
      <div class="card flush">
        <div class="card-head">
          <h2>Activity history</h2>
          <div class="seg" id="hist-filter" role="group" aria-label="Filter by stage">
            <button type="button" data-stage="" aria-pressed="true">All</button>
            <button type="button" data-stage="MARKING" aria-pressed="false">Marking</button>
            <button type="button" data-stage="CUTTING" aria-pressed="false">Cutting</button>
            <button type="button" data-stage="STITCHING" aria-pressed="false">Stitching</button>
            <button type="button" data-stage="PIECE_HANDLING" aria-pressed="false">Piece handling</button>
            <button type="button" data-stage="IDLE" aria-pressed="false">Idle</button>
          </div>
        </div>
        <div class="table-wrap">
          <table class="tbl" id="hist-table">
            <thead><tr><th scope="col">Worker</th><th scope="col">Stage</th><th scope="col">Zone</th><th scope="col">Start</th><th scope="col">End</th><th scope="col" class="r">Duration</th><th scope="col" class="r">Confidence</th></tr></thead>
            <tbody><tr><td colspan="7"><div class="skel" style="width:70%"></div><div class="skel" style="width:90%"></div><div class="skel" style="width:60%"></div></td></tr></tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- ============================== Analytics ============================== -->
    <section id="view-analytics" class="view" role="tabpanel" aria-labelledby="tab-analytics" tabindex="-1" hidden>
      <div class="grid g-stats" id="an-cards"></div>
      <div class="stack" style="margin-top:20px">
        <div class="card flush">
          <div class="card-head"><h2>Stage performance</h2><span class="sub">Completed activities, by stage</span></div>
          <div class="table-wrap"><table class="tbl" id="an-stage-table">
            <thead><tr><th scope="col">Stage</th><th scope="col" class="r">Completed</th><th scope="col" class="r">Average time</th><th scope="col" class="r">Total time</th></tr></thead>
            <tbody></tbody></table></div>
        </div>
        <div class="grid g-two">
          <div class="card flush">
            <div class="card-head"><h2>Bottleneck check</h2><span class="sub">Baseline against measured average</span></div>
            <div class="table-wrap"><table class="tbl" id="an-bottleneck-table">
              <thead><tr><th scope="col">Stage</th><th scope="col" class="r">Baseline</th><th scope="col" class="r">Actual</th><th scope="col">Status</th></tr></thead>
              <tbody></tbody></table></div>
            <p class="help" style="padding:14px 24px 22px">This compares numbers only. It never guesses the cause, so look into a flagged stage yourself.</p>
          </div>
          <div class="card flush">
            <div class="card-head"><h2>Worker time</h2><span class="sub">Active against idle</span></div>
            <div class="table-wrap"><table class="tbl" id="an-worker-table">
              <thead><tr><th scope="col">Worker</th><th scope="col" class="r">Active</th><th scope="col" class="r">Idle</th></tr></thead>
              <tbody></tbody></table></div>
          </div>
        </div>
      </div>
    </section>

    <!-- ============================== Odoo ============================== -->
    <section id="view-odoo" class="view" role="tabpanel" aria-labelledby="tab-odoo" tabindex="-1" hidden>
      <div class="stack">
        <div id="od-alert" aria-live="polite"></div>

        <div class="card">
          <div class="card-head">
            <h2>Connection</h2>
            <div class="seg" id="od-mode" role="group" aria-label="Operating mode">
              <button type="button" data-act="mode" data-mode="off" aria-pressed="false" title="Stops all writes to Odoo at once">Off</button>
              <button type="button" data-act="mode" data-mode="shadow" aria-pressed="false" title="Records what it would do; changes nothing in Odoo">Shadow</button>
              <button type="button" data-act="mode" data-mode="live" data-confirm="Confirm live" aria-pressed="false" title="Updates Odoo by itself">Live</button>
            </div>
          </div>
          <div class="facts" id="od-facts"></div>
          <p class="help" style="margin-top:16px"><strong style="color:var(--ink);font-weight:600">Off</strong> does nothing. <strong style="color:var(--ink);font-weight:600">Shadow</strong> records what it would do and changes nothing. <strong style="color:var(--ink);font-weight:600">Live</strong> updates Odoo.</p>
        </div>

        <div class="card">
          <div class="card-head"><h2>Operations</h2><span class="sub" id="od-order-sub"></span></div>
          <div id="od-flow"><div class="empty"><strong>No order is being followed</strong>Create an order in Odoo, then press Confirm and Start. It appears here within a few seconds.</div></div>
        </div>

        <div class="grid g-two">
          <div class="card">
            <div class="card-head"><h2>What the camera sees</h2><span class="sub" id="od-seen-age"></span></div>
            <div id="od-seen"></div>
            <div id="od-finish" style="margin-top:16px"></div>
          </div>
          <div class="card">
            <div class="card-head"><h2>Controls</h2></div>
            <div class="btn-row">
              <button type="button" class="btn" id="od-hold" data-act="hold">Hold auto-finish</button>
              <button type="button" class="btn" data-act="retry">Retry failed</button>
              <button type="button" class="btn btn-danger" data-act="discard" data-confirm="Confirm discard">Discard failed</button>
            </div>
            <p class="help" style="margin-top:12px">Hold stops the last operation from finishing by itself until you release it.</p>
            <details class="manual" id="od-manual-box">
              <summary>Manual override</summary>
              <div class="manual-body" id="od-manual"><p class="help">Available when an order is being followed in live mode.</p></div>
            </details>
          </div>
        </div>

        <div class="card flush">
          <div class="card-head"><h2>Audit log</h2><span class="sub">Every decision and what was sent to Odoo</span></div>
          <div class="table-wrap"><table class="tbl" id="od-audit">
            <thead><tr><th scope="col">Time</th><th scope="col">Event</th><th scope="col">Detail</th></tr></thead>
            <tbody><tr><td colspan="3"><div class="skel" style="width:80%"></div><div class="skel" style="width:60%"></div></td></tr></tbody>
          </table></div>
        </div>
      </div>
    </section>
  </main>
</div>
<div class="toast-zone" id="toasts" role="status" aria-live="polite"></div>

<script>
(() => {
'use strict';
const CAMERA_ID = __CAMERA_ID_JS__;
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

const STAGES = {
  MARKING:        {label: 'Marking',        badge: 'b-blue',   color: 'var(--st-marking)'},
  CUTTING:        {label: 'Cutting',        badge: 'b-yellow', color: 'var(--st-cutting)'},
  STITCHING:      {label: 'Stitching',      badge: 'b-violet', color: 'var(--st-stitching)'},
  PIECE_HANDLING: {label: 'Piece handling', badge: 'b-green',  color: 'var(--st-handling)'},
  IDLE:           {label: 'Idle',           badge: 'b-gray',   color: 'var(--st-idle)'},
};
const stageLabel = s => STAGES[s] ? STAGES[s].label : (s ? s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, ' ') : 'Unknown');
const stageBadge = s => '<span class="badge ' + (STAGES[s] ? STAGES[s].badge : 'b-gray') + '">' + esc(stageLabel(s)) + '</span>';
const OBJECT_NAMES = {person: 'Person', scissors: 'Scissors', marking_tool: 'Pen', fabric: 'Cloth', fabric_piece: 'Cloth piece'};

function fmtDur(s) {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const mm = String(m).padStart(2, '0'), rr = String(r).padStart(2, '0');
  return h ? h + ':' + mm + ':' + rr : mm + ':' + rr;
}
function fmtTime(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const t = d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
  return d.toDateString() === now.toDateString() ? t : d.toLocaleDateString([], {day: 'numeric', month: 'short'}) + ' ' + t;
}
async function getJSON(url) {
  const r = await fetch(url, {cache: 'no-store'});
  if (!r.ok) throw new Error(url + ' ' + r.status);
  return r.json();
}

// ------------------------------------------------------------------------ feedback
function toast(message, kind) {
  const el = document.createElement('div');
  el.className = 'toast' + (kind === 'bad' ? ' bad' : '');
  el.textContent = message;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), kind === 'bad' ? 6000 : 3500);
}

// Two-step confirmation instead of a pop-up: the first press arms the button, a second press within
// four seconds does it. Used for anything that changes Odoo or discards work.
const armed = new WeakMap();
function disarm(btn) {
  const a = armed.get(btn);
  if (!a) return;
  clearTimeout(a.timer);
  btn.classList.remove('is-confirm');
  btn.textContent = a.text;
  armed.delete(btn);
}
function arm(btn, label) {
  armed.set(btn, {text: btn.textContent, timer: setTimeout(() => disarm(btn), 4000)});
  btn.classList.add('is-confirm');
  btn.textContent = label;
}

// ------------------------------------------------------------------------ tabs
const VIEWS = ['live', 'timeline', 'history', 'analytics', 'odoo'];
let activeView = 'live';
function showView(view, moveFocus) {
  if (!VIEWS.includes(view)) view = 'live';
  activeView = view;
  VIEWS.forEach(v => {
    const on = v === view, tab = $('#tab-' + v), panel = $('#view-' + v);
    tab.setAttribute('aria-selected', on ? 'true' : 'false');
    tab.tabIndex = on ? 0 : -1;
    panel.classList.toggle('active', on);
    panel.hidden = !on;
  });
  if (moveFocus) $('#tab-' + view).focus();
  try { history.replaceState(null, '', '#' + view); } catch (e) { /* file:// or sandbox */ }
  refreshActive();
}
$$('.tab').forEach(tab => {
  tab.addEventListener('click', () => showView(tab.dataset.view));
  tab.addEventListener('keydown', e => {
    const i = VIEWS.indexOf(tab.dataset.view);
    const next = {ArrowRight: (i + 1) % VIEWS.length, ArrowLeft: (i + VIEWS.length - 1) % VIEWS.length, Home: 0, End: VIEWS.length - 1}[e.key];
    if (next !== undefined) { e.preventDefault(); showView(VIEWS[next], true); }
  });
});

// ------------------------------------------------------------------------ header chips
let cameraStatus = null;
function setChip(id, state, text) {
  const chip = $(id);
  $('.dot', chip).className = 'dot ' + (state || '');
  $('span:last-child', chip).textContent = text;
}
async function refreshCamera() {
  try {
    cameraStatus = await getJSON('/api/status');
    if (cameraStatus.connected) setChip('#chip-camera', 'ok', 'Camera online');
    else setChip('#chip-camera', 'bad', 'Camera not connected');
    const m = /^(\d+)x(\d+)$/.exec(cameraStatus.resolution || '');
    if (m) { srcW = +m[1]; srcH = +m[2]; }
    const badge = $('#frame-badge');
    badge.classList.toggle('off', !cameraStatus.connected);
    $('#frame-badge-text').textContent = cameraStatus.connected ? 'Live' : 'No signal';
  } catch (e) {
    cameraStatus = null;
    setChip('#chip-camera', 'bad', 'Camera status unavailable');
  }
}

// ------------------------------------------------------------------------ live view
const img = $('#bg'), cv = $('#cv');
let srcW = 0, srcH = 0, zonesCache = [], workersCache = {}, lastAct = {current: {}};
const DPR = () => Math.min(window.devicePixelRatio || 1, 2);
function sizeCanvas() {
  // The canvas must match the picture's real on-screen size or everything drawn on it is stretched.
  // It is re-measured whenever the frame resizes (first video frame arriving, window resized, tab
  // shown again) and re-checked on every draw.
  const w = cv.clientWidth, h = cv.clientHeight, d = DPR();
  if (!w || !h) return;
  const bw = Math.round(w * d), bh = Math.round(h * d);
  if (cv.width !== bw || cv.height !== bh) { cv.width = bw; cv.height = bh; }
}
window.addEventListener('resize', sizeCanvas);
img.addEventListener('load', sizeCanvas);
if ('ResizeObserver' in window) new ResizeObserver(sizeCanvas).observe($('.frame'));

const BOX_COLORS = {person: '#7CC48F', scissors: '#F2B84B', marking_tool: '#82B8F2', fabric: '#EDEBE4', fabric_piece: '#EDEBE4'};
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}
function drawTag(ctx, text, x, y, W) {
  ctx.font = '600 12px "Segoe UI", system-ui, sans-serif';
  const w = ctx.measureText(text).width + 14, h = 20;
  const px = Math.max(2, Math.min(x, W - w - 2)), py = Math.max(2, y - h - 3);
  ctx.fillStyle = 'rgba(17,17,17,.82)'; roundRect(ctx, px, py, w, h, 5); ctx.fill();
  ctx.fillStyle = '#fff'; ctx.textBaseline = 'middle'; ctx.fillText(text, px + 7, py + h / 2 + .5);
}
function drawZone(ctx, z, W, H) {
  if (!z.points || z.points.length < 3) return;
  ctx.beginPath();
  z.points.forEach(([x, y], i) => { i ? ctx.lineTo(x * W, y * H) : ctx.moveTo(x * W, y * H); });
  ctx.closePath();
  ctx.fillStyle = 'rgba(255,255,255,.07)'; ctx.fill();
  ctx.setLineDash([7, 6]); ctx.lineWidth = 1.5; ctx.strokeStyle = 'rgba(255,255,255,.85)'; ctx.stroke(); ctx.setLineDash([]);
  drawTag(ctx, z.name, z.points[0][0] * W + 4, z.points[0][1] * H + 26, W);
}

async function refreshZones() { try { zonesCache = await getJSON('/api/zones?camera_id=' + encodeURIComponent(CAMERA_ID)); } catch (e) { /* keep last */ } }
async function refreshWorkers() {
  try {
    const list = await getJSON('/api/workers');
    workersCache = {};
    list.forEach(w => { workersCache[w.track_id] = w.name; });
  } catch (e) { /* keep last */ }
}

async function refreshLive() {
  let dets = [], acts = lastAct;
  try {
    dets = (await getJSON('/api/detections')).detections || [];
    acts = lastAct = await getJSON('/api/activities');
  } catch (e) { return; }
  drawOverlay(dets, acts);
  renderPeople(acts.current || {});
}

function drawOverlay(dets, acts) {
  sizeCanvas();
  if (!cv.clientWidth) return;
  const d = DPR(), W = cv.width / d, H = cv.height / d;
  const ctx = cv.getContext('2d');
  ctx.setTransform(d, 0, 0, d, 0, 0);
  ctx.clearRect(0, 0, W, H);
  if ($('#tg-zones').checked) zonesCache.forEach(z => drawZone(ctx, z, W, H));
  const showBoxes = $('#tg-boxes').checked, showLabels = $('#tg-labels').checked;
  const showIds = $('#tg-ids').checked, showActivity = $('#tg-activity').checked;
  const sx = W / (srcW || W), sy = H / (srcH || H);
  dets.forEach(det => {
    const [x1, y1, x2, y2] = det.box;
    const rx = x1 * sx, ry = y1 * sy, rw = (x2 - x1) * sx, rh = (y2 - y1) * sy;
    if (showBoxes) {
      ctx.lineWidth = det.class === 'person' ? 2 : 2.5; ctx.strokeStyle = BOX_COLORS[det.class] || '#fff';
      roundRect(ctx, rx, ry, rw, rh, 6); ctx.stroke();
    }
    const parts = [];
    if (showLabels) parts.push(OBJECT_NAMES[det.class] || det.class);
    if (showIds && det.track_id != null) parts.push('#' + det.track_id);
    if (showActivity && det.track_id != null) {
      const cur = (acts.current || {})[det.track_id];
      if (cur && cur.activity) parts.push(stageLabel(cur.activity));
    }
    if (parts.length) drawTag(ctx, parts.join(' · '), rx, ry, W);
  });
}

// "People on camera" is updated in place, never rebuilt: a rebuild every poll would drop the keyboard
// focus (and the half-typed name) of anyone using the Assign box.
const peopleEls = new Map();
function buildPerson(tid) {
  const el = document.createElement('div');
  el.className = 'person';
  el.innerHTML = '<div class="avatar" aria-hidden="true"></div>' +
    '<div><div class="person-name"></div><div class="person-meta"></div></div><div class="badge-slot"></div>' +
    '<form class="assign"><input class="input" autocomplete="off"><button class="btn btn-sm" type="submit">Save name</button></form>';
  const input = $('input', el);
  input.placeholder = 'Assign a name';
  input.setAttribute('aria-label', 'Assign a name to person ' + tid);
  $('form', el).addEventListener('submit', async ev => {
    ev.preventDefault();
    const name = input.value.trim();
    if (!name) return;
    try {
      const r = await fetch('/api/workers', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({track_id: +tid, name})});
      if (!r.ok) throw new Error(r.status);
      input.value = '';
      toast('Saved ' + name);
      await refreshWorkers();
      refreshLive();
    } catch (e) { toast('Could not save the name. Please try again.', 'bad'); }
  });
  return el;
}
function renderPeople(current) {
  const ids = Object.keys(current);
  const host = $('#tracks');
  $('#people-count').textContent = ids.length ? ids.length + ' tracked' : '';
  if (!ids.length) {
    if (peopleEls.size || !$('.empty', host)) {
      peopleEls.clear();
      host.innerHTML = '<div class="empty compact"><strong>No one in view</strong>People appear here as soon as the camera tracks them.</div>';
    }
    return;
  }
  const placeholder = $('.empty', host);
  if (placeholder) placeholder.remove();
  const now = Date.now() / 1000;
  ids.forEach(tid => {
    let el = peopleEls.get(tid);
    if (!el || !el.isConnected) { el = buildPerson(tid); peopleEls.set(tid, el); host.appendChild(el); }
    const c = current[tid], name = workersCache[tid] || 'Person ' + tid;
    $('.avatar', el).textContent = (workersCache[tid] ? name : String(tid)).charAt(0).toUpperCase();
    $('.person-name', el).textContent = name;
    $('.person-meta', el).textContent = (c.zone || 'No zone') + ' · ' + (c.since ? fmtDur(now - c.since) : '--:--');
    const slot = $('.badge-slot', el), html = stageBadge(c.activity || 'IDLE');
    if (slot.innerHTML !== html) slot.innerHTML = html;
  });
  [...peopleEls.keys()].forEach(tid => { if (!ids.includes(tid)) { peopleEls.get(tid).remove(); peopleEls.delete(tid); } });
}

// ------------------------------------------------------------------------ timeline
let tlHours = 8;
async function refreshTimeline() {
  const now = Date.now() / 1000, since = tlHours ? now - tlHours * 3600 : null;
  let events;
  try { events = await getJSON('/api/events?limit=500' + (since ? '&since_ts=' + since : '')); } catch (e) { return; }
  const body = $('#timeline-body');
  $('#tl-legend').innerHTML = Object.keys(STAGES).map(k => '<span><i style="background:' + STAGES[k].color + '"></i>' + STAGES[k].label + '</span>').join('');
  if (!events.length) {
    body.innerHTML = '<div class="empty"><strong>Nothing in this period</strong>' + (tlHours ? 'No finished activity in the last ' + (tlHours === 1 ? 'hour' : tlHours + ' hours') + '. Try a longer range.' : 'Finished activities show up here as coloured blocks.') + '</div>';
    return;
  }
  const byWorker = {};
  events.forEach(e => { (byWorker[e.worker] = byWorker[e.worker] || []).push(e); });
  // a fixed window (now minus N hours) keeps the scale honest; "All" fits the recorded range instead
  const minTs = since || Math.min(...events.map(e => e.start_ts)), maxTs = since ? now : Math.max(...events.map(e => e.end_ts));
  const span = Math.max(maxTs - minTs, 1);
  let html = '';
  Object.keys(byWorker).forEach(worker => {
    const blocks = byWorker[worker].slice().sort((a, b) => a.start_ts - b.start_ts).map(e => {
      const left = Math.max(0, (e.start_ts - minTs) / span * 100), width = Math.max((e.end_ts - Math.max(e.start_ts, minTs)) / span * 100, .3);
      const color = STAGES[e.stage] ? STAGES[e.stage].color : 'var(--st-idle)';
      const tip = stageLabel(e.stage) + ': ' + fmtTime(e.start_ts) + ' to ' + fmtTime(e.end_ts) + ' (' + fmtDur(e.duration_s) + ')';
      return '<div class="tl-block" title="' + esc(tip) + '" style="left:' + left + '%;width:' + width + '%;background:' + color + '"></div>';
    }).join('');
    html += '<div class="tl-row"><div class="tl-name" title="' + esc(worker) + '">' + esc(worker) + '</div><div class="tl-track">' + blocks + '</div></div>';
  });
  html += '<div class="tl-axis"><span></span><div><span>' + fmtTime(minTs) + '</span><span>' + fmtTime((minTs + maxTs) / 2) + '</span><span>' + fmtTime(maxTs) + '</span></div></div>';
  body.innerHTML = html;
}
$('#tl-range').addEventListener('click', e => {
  const b = e.target.closest('button[data-hours]');
  if (!b) return;
  tlHours = +b.dataset.hours;
  $$('#tl-range button').forEach(x => x.setAttribute('aria-pressed', x === b ? 'true' : 'false'));
  refreshTimeline();
});

// ------------------------------------------------------------------------ history
let histStage = '';
async function refreshHistory() {
  let url = '/api/events?limit=200';
  if (histStage) url += '&stage=' + encodeURIComponent(histStage);
  let events;
  try { events = await getJSON(url); } catch (e) { return; }
  const tbody = $('#hist-table tbody');
  if (!events.length) {
    tbody.innerHTML = '<tr><td colspan="7"><div class="empty"><strong>No activity recorded</strong>' + (histStage ? 'Nothing in this stage yet.' : 'Finished activities are listed here, newest first.') + '</div></td></tr>';
    return;
  }
  tbody.innerHTML = events.map(e => '<tr><td>' + esc(e.worker) + '</td><td>' + stageBadge(e.stage) + '</td><td>' + esc(e.zone || '—') + '</td>' +
    '<td class="num">' + fmtTime(e.start_ts) + '</td><td class="num">' + fmtTime(e.end_ts) + '</td>' +
    '<td class="r num">' + fmtDur(e.duration_s) + '</td><td class="r num">' + Math.round(e.confidence * 100) + '%</td></tr>').join('');
}
$('#hist-filter').addEventListener('click', e => {
  const b = e.target.closest('button[data-stage]');
  if (!b) return;
  histStage = b.dataset.stage;
  $$('#hist-filter button').forEach(x => x.setAttribute('aria-pressed', x === b ? 'true' : 'false'));
  refreshHistory();
});

// ------------------------------------------------------------------------ analytics
async function refreshAnalytics() {
  let an, acts;
  try { an = await getJSON('/api/analytics'); acts = await getJSON('/api/activities'); } catch (e) { return; }
  const cur = Object.values(acts.current || {});
  const countBy = s => cur.filter(c => c.activity === s).length;
  const activeWorkers = cur.filter(c => c.activity && c.activity !== 'IDLE').length;
  const totalCompleted = Object.values(an.stage_summary).reduce((s, v) => s + v.count, 0);
  $('#an-cards').innerHTML = [
    ['Active workers', activeWorkers], ['Marking now', countBy('MARKING')], ['Cutting now', countBy('CUTTING')],
    ['Handling pieces', countBy('PIECE_HANDLING')], ['Stitching now', countBy('STITCHING')], ['Completed activities', totalCompleted],
  ].map(([k, v]) => '<div class="card stat"><div class="stat-value">' + v + '</div><div class="stat-label">' + k + '</div></div>').join('');

  $('#an-stage-table tbody').innerHTML = ['MARKING', 'CUTTING', 'PIECE_HANDLING', 'STITCHING', 'IDLE'].map(s => {
    const row = an.stage_summary[s];
    return '<tr><td><span class="swatch" style="background:' + STAGES[s].color + '"></span>' + STAGES[s].label + '</td><td class="r num">' + (row ? row.count : 0) +
      '</td><td class="r num">' + (row ? fmtDur(row.avg_s) : '—') + '</td><td class="r num">' + (row ? fmtDur(row.total_s) : '—') + '</td></tr>';
  }).join('');

  $('#an-bottleneck-table tbody').innerHTML = an.bottlenecks.length
    ? an.bottlenecks.map(b => '<tr><td>' + esc(stageLabel(b.stage)) + '</td><td class="r num">' + fmtDur(b.baseline_s) + '</td><td class="r num">' + fmtDur(b.actual_avg_s) +
        '</td><td><span class="badge ' + (b.status === 'OK' ? 'b-green' : 'b-yellow') + '">' + esc(b.status) + '</span></td></tr>').join('')
    : '<tr><td colspan="4"><div class="empty compact"><strong>No baselines set</strong>Add expected stage times in the settings file to compare against.</div></td></tr>';

  const workers = new Set([...Object.keys(an.worker_active_seconds), ...Object.keys(an.worker_idle_seconds)]);
  $('#an-worker-table tbody').innerHTML = workers.size
    ? [...workers].map(w => '<tr><td>' + esc(w) + '</td><td class="r num">' + fmtDur(an.worker_active_seconds[w] || 0) + '</td><td class="r num">' + fmtDur(an.worker_idle_seconds[w] || 0) + '</td></tr>').join('')
    : '<tr><td colspan="3"><div class="empty compact"><strong>Nothing yet</strong>Worker times appear after the first finished activity.</div></td></tr>';
}

// ------------------------------------------------------------------------ odoo
let odooState = null, manualSig = '';
const MODE_BADGE = {live: ['b-green', 'Live'], shadow: ['b-yellow', 'Shadow'], off: ['b-gray', 'Off']};
const KIND_BADGE = {applied: 'b-green', decision: 'b-blue', manual: 'b-blue', mode: 'b-blue', conflict: 'b-yellow', error: 'b-red', reconcile: 'b-gray'};
const STEP_STATE = {pending: ['b-gray', 'Waiting'], active: ['b-yellow', 'In progress'], finished: ['b-green', 'Finished']};
const CHECK = '<svg viewBox="0 0 12 12" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 6.4l2.3 2.3 4.7-5"/></svg>';

function friendly(text) {
  // Odoo and the network layer speak in method names and exception classes; say it in plain words.
  let t = String(text || '').replace(/^[\w.]+\.\w+:\s*/, '').replace(/^cannot reach Odoo:\s*/i, '');
  if (/timed? ?out/i.test(t)) t = 'Odoo is not answering (timed out). The camera keeps running and updates wait in the queue.';
  else if (/ConnectError|refused|unreachable/i.test(t) && !/^Please/.test(t)) t = 'Odoo cannot be reached. Updates wait in the queue and are sent when it is back.';
  return t.charAt(0).toUpperCase() + t.slice(1);
}

async function odooPost(path, body, doneText) {
  try {
    const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
    if (!r.ok) {
      let detail = '';
      try { detail = (await r.json()).detail; } catch (e) { /* no body */ }
      toast('Not done. ' + (detail || 'The server answered ' + r.status + '.'), 'bad');
    } else if (doneText) toast(doneText);
  } catch (e) { toast('Could not reach the dashboard server.', 'bad'); }
  refreshOdoo();
}

function stepNeeds(flow, i) {
  // seconds of continuous activity the camera must see before this step may start / take over
  return (i > 0 && flow.steps[i - 1].state === 'active') ? flow.handoff_hold_s : flow.start_hold_s;
}

// An order that has just finished stays on screen for ten minutes, so "moved to completed" is actually seen.
function recentlyCompleted(st) {
  const lc = st && st.last_completed;
  return lc && (Date.now() / 1000 - lc.ts) < 600 ? lc : null;
}
function completedFlow(lc) {
  return {steps: lc.steps.map(x => ({name: x.name, activity: x.activity, state: 'finished'})), held_s: {}, finishing_in_s: null,
          quiet_s: 0, held: false, start_hold_s: 2, handoff_hold_s: 3, finish_wait_s: 15};
}
const completedText = lc => esc(lc.name) + ' completed at ' + fmtTime(lc.ts) + (lc.by_camera ? ' automatically by the camera.' : ' in Odoo.');

function renderChipAndJob(st) {
  if (!st) {
    setChip('#chip-odoo', 'bad', 'Odoo status unavailable');
    return;
  }
  const mode = MODE_BADGE[st.mode] || MODE_BADGE.off;
  if (!st.configured) setChip('#chip-odoo', 'warn', 'Odoo not set up');
  else if (st.odoo_ok === false) setChip('#chip-odoo', 'bad', 'Odoo unreachable');
  else if (st.odoo_ok === null) setChip('#chip-odoo', '', 'Odoo connecting');
  else setChip('#chip-odoo', st.mode === 'off' ? 'warn' : 'ok', 'Odoo ' + (st.mode === 'off' ? 'paused' : 'connected · ' + mode[1].toLowerCase()));

  const badge = $('#job-mode');
  badge.className = 'badge ' + mode[0]; badge.textContent = mode[1];
  const body = $('#job-body');
  if (st.conflict) {
    body.innerHTML = '<div class="banner warn"><span class="dot"></span><span>' + esc(friendly(st.conflict)) + '</span></div>';
  } else if (st.production && st.flow) {
    const rows = st.flow.steps.map(s => {
      const state = s.state === 'finished' ? 'is-done' : s.state === 'active' ? 'is-active' : 'is-waiting';
      return '<li class="' + state + '"><span class="mark">' + (s.state === 'finished' ? CHECK : '') + '</span><span class="name">' + esc(s.name) + '</span>' +
        '<span class="badge ' + STEP_STATE[s.state][0] + '">' + (s.state === 'active' && st.flow.finishing_in_s != null && s === st.flow.steps[st.flow.steps.length - 1] ? 'Completes in ' + Math.max(0, Math.ceil(st.flow.finishing_in_s)) + ' s' : STEP_STATE[s.state][1]) + '</span></li>';
    }).join('');
    body.innerHTML = '<div class="job-order">' + esc(st.production.name) + '</div><div class="job-meta">Work center ' + esc(st.workcenter_code) + '</div><ol class="mini-steps">' + rows + '</ol>';
  } else if (recentlyCompleted(st)) {
    const lc = recentlyCompleted(st);
    const rows = lc.steps.map(x => '<li class="is-done"><span class="mark">' + CHECK + '</span><span class="name">' + esc(x.name) + '</span><span class="badge b-green">Finished</span></li>').join('');
    body.innerHTML = '<div class="job-order">' + esc(lc.name) + '</div><div class="job-meta">Completed at ' + fmtTime(lc.ts) + (lc.by_camera ? ', automatically' : '') + '</div><ol class="mini-steps">' + rows + '</ol>';
    badge.className = 'badge b-green'; badge.textContent = 'Completed';
  } else {
    body.innerHTML = '<div class="empty compact"><strong>No order running</strong>' + (st.configured ? 'Start an order in Odoo and it appears here.' : 'Odoo is not set up yet.') + '</div>';
  }
}

function renderOdooTab(st, audit) {
  const alerts = [];
  if (!st.configured) alerts.push(['warn', 'Odoo is not set up. Add the address, database, API key and work center code to the settings file.']);
  if (st.conflict) alerts.push(['warn', friendly(st.conflict)]);
  if (st.last_error && st.odoo_ok === false) alerts.push(['bad', friendly(st.last_error)]);
  (st.failed || []).forEach(f => alerts.push(['bad', 'Odoo refused to ' + f.action.toLowerCase() + (f.step_name ? ' ' + f.step_name : '') + '. ' + friendly(f.last_error)]));
  if (st.flow && st.flow.unexpected) alerts.push(['info', friendly(st.flow.unexpected)]);
  $('#od-alert').innerHTML = alerts.map(a => '<div class="banner ' + a[0] + '"><span class="dot"></span><span>' + esc(a[1]) + '</span></div>').join('');

  $$('#od-mode button').forEach(b => { b.setAttribute('aria-pressed', b.dataset.mode === st.mode ? 'true' : 'false'); });
  const conn = !st.configured ? ['Not set up', 'warn'] : st.odoo_ok === false ? ['Unreachable', 'bad'] : st.odoo_ok === null ? ['Connecting', ''] : ['Connected', 'ok'];
  const queued = (st.queue.pending || 0), failed = (st.queue.failed || 0);
  $('#od-facts').innerHTML = [
    ['Odoo', '<span class="dot ' + conn[1] + '" style="display:inline-block;margin-right:8px"></span>' + conn[0]],
    ['Work center', esc(st.workcenter_code || '—')],
    ['Order', st.production ? esc(st.production.name) : 'None'],
    ['Waiting to send', queued + (failed ? ' · ' + failed + ' failed' : '')],
  ].map(f => '<div class="fact"><div class="k">' + f[0] + '</div><div class="v">' + f[1] + '</div></div>').join('');

  // operations flow
  const flowHost = $('#od-flow');
  const lcDone = !st.flow ? recentlyCompleted(st) : null;
  const flowView = st.flow || (lcDone ? completedFlow(lcDone) : null);
  $('#od-order-sub').textContent = st.production ? st.production.name : (lcDone ? lcDone.name : '');
  if (!flowView) {
    flowHost.innerHTML = '<div class="empty"><strong>' + (st.conflict ? 'Paused' : 'No order is being followed') + '</strong>' +
      (st.conflict ? 'Fix the message above and the camera resumes by itself.' : 'Create an order in Odoo, then press Confirm and Start. It appears here within a few seconds.') + '</div>';
  } else {
    const f = flowView, held = f.held_s || {};
    const cards = f.steps.map((s, i) => {
      const cls = s.state === 'finished' ? 'is-done' : s.state === 'active' ? 'is-active' : 'is-waiting';
      let note = '', pct = 0, showMeter = false, countdown = '';
      if (s.state === 'pending') {
        const need = stepNeeds(f, i), h = held[s.activity] || 0;
        if (h > 0) { note = 'Camera sees ' + stageLabel(s.activity).toLowerCase() + ' for ' + h.toFixed(1) + ' s of ' + need + ' s'; pct = Math.min(1, h / need); showMeter = true; }
        else note = 'Waiting for the camera to see ' + stageLabel(s.activity).toLowerCase();
      } else if (s.state === 'active') {
        const last = i === f.steps.length - 1, wait = f.finish_wait_s || 15;
        if (last && f.held) {
          note = 'Auto-finish is on hold. Release the hold to let it complete.';
        } else if (last && f.finishing_in_s != null) {
          const left = Math.max(0, Math.ceil(f.finishing_in_s));
          countdown = '<div class="count" role="timer" aria-label="Seconds until ' + esc(s.name) + ' completes"><span class="count-num num">' + left + '</span><span class="count-label">s until ' + esc(s.name) + ' completes</span></div>';
          note = 'The worker is idle or away. Work starting again cancels this.';
          pct = wait > 0 ? Math.min(1, f.quiet_s / wait) : 0; showMeter = true;
        } else if (last) {
          note = 'Work in progress. When it stops, a ' + wait + ' s countdown starts and the operation completes.';
        } else note = 'Running. The next step takes over when the camera sees it.';
      } else note = 'Done in Odoo';
      const stt = STEP_STATE[s.state];
      return '<li class="step ' + cls + '"><div class="step-top"><span class="step-no">' + (i + 1) + '</span><span class="badge ' + stt[0] + '">' + stt[1] + '</span></div>' +
        '<h3>' + esc(s.name) + '</h3><p>Recognised from ' + esc(stageLabel(s.activity).toLowerCase()) + ' activity</p>' + countdown +
        '<div class="meter" aria-hidden="true"' + (showMeter ? '' : ' style="visibility:hidden"') + '><i style="transform:scaleX(' + pct + ')"></i></div><div class="note">' + esc(note) + '</div></li>';
    });
    const allDone = f.steps.every(s => s.state === 'finished');
    cards.push('<li class="step ' + (allDone ? 'is-done' : 'is-waiting') + '"><div class="step-top"><span class="step-no">' + (f.steps.length + 1) + '</span><span class="badge ' + (allDone ? 'b-green' : 'b-gray') + '">' + (allDone ? 'Done' : 'Waiting') + '</span></div>' +
      '<h3>Order done</h3><p>Closed automatically after the last step</p><div class="meter" aria-hidden="true"><i style="transform:scaleX(' + (allDone ? 1 : 0) + ')"></i></div><div class="note">' + (allDone ? 'The order is complete in Odoo' : 'After the last step finishes') + '</div></li>');
    flowHost.innerHTML = (lcDone ? '<div class="banner ok" style="margin-bottom:16px"><span class="dot"></span><span>' + completedText(lcDone) + '</span></div>' : '') +
      '<ol class="flow">' + cards.join('') + '</ol>';
  }

  // what the camera sees
  const cam = st.camera || {};
  const seeing = cam.seeing || [];
  $('#od-seen-age').textContent = cam.last_tick_age_s == null ? 'No camera updates yet' : (cam.last_tick_age_s > 5 ? 'Stalled for ' + Math.round(cam.last_tick_age_s) + ' s' : 'Updated just now');
  const rows = [];
  rows.push('<div class="see-row"><span>Right now</span><span>' + (seeing.length ? seeing.map(stageBadge).join(' ') : '<span class="muted">No marking or cutting confirmed</span>') + '</span></div>');
  if (st.flow) {
    Object.keys(st.flow.held_s || {}).forEach(a => {
      const h = st.flow.held_s[a];
      rows.push('<div class="see-row"><span class="muted">' + esc(stageLabel(a)) + ' seen without a break</span><span class="num">' + h.toFixed(1) + ' s</span></div>');
    });
  }
  $('#od-seen').innerHTML = rows.join('');
  const fin = $('#od-finish');
  if (st.flow && st.flow.finishing_in_s != null) {
    const total = st.flow.quiet_s + st.flow.finishing_in_s;
    fin.innerHTML = '<div class="finish-line"><div class="muted">Last operation completes in</div><div class="big num">' + Math.ceil(st.flow.finishing_in_s) +
      ' s</div><div class="meter" aria-hidden="true"><i style="transform:scaleX(' + (total > 0 ? st.flow.quiet_s / total : 0) + ')"></i></div><div class="help">Starting the work again cancels the countdown.</div></div>';
  } else if (st.flow && st.flow.held) {
    fin.innerHTML = '<div class="banner info"><span class="dot"></span><span>Auto-finish is on hold. Nothing closes until you release it.</span></div>';
  } else fin.innerHTML = '';

  // controls
  const hold = $('#od-hold');
  hold.textContent = st.flow && st.flow.held ? 'Release hold' : 'Hold auto-finish';
  hold.dataset.on = st.flow && st.flow.held ? '0' : '1';
  hold.disabled = !st.flow;
  const sig = st.mode + '|' + (st.flow ? st.flow.steps.map(s => s.name).join(',') : '');
  if (sig !== manualSig) {
    manualSig = sig;
    $('#od-manual').innerHTML = !st.flow || st.mode !== 'live'
      ? '<p class="help">Available when an order is being followed in live mode.</p>'
      : st.flow.steps.map((s, i) => '<div class="manual-row"><span>' + esc(s.name) + '</span><span class="btn-row">' +
          '<button type="button" class="btn btn-sm" data-act="manual" data-action="start" data-step="' + i + '" data-confirm="Confirm start">Start</button>' +
          '<button type="button" class="btn btn-sm" data-act="manual" data-action="finish" data-step="' + i + '" data-confirm="Confirm finish">Finish</button></span></div>').join('');
  }

  // audit
  $('#od-audit tbody').innerHTML = audit.length ? audit.map(a =>
    '<tr><td class="num" style="white-space:nowrap">' + fmtTime(a.ts) + '</td><td><span class="badge ' + (KIND_BADGE[a.kind] || 'b-gray') + '">' + esc(a.kind) + '</span>' +
    (a.shadow ? ' <span class="muted" style="font-size:12.5px">shadow</span>' : '') + '</td><td>' + esc(a.detail) + '</td></tr>').join('')
    : '<tr><td colspan="3"><div class="empty compact"><strong>Nothing recorded yet</strong>Decisions and Odoo updates are listed here.</div></td></tr>';
}

async function refreshOdoo() {
  let st = null, audit = [];
  try { st = await getJSON('/api/odoo/status'); } catch (e) { /* shown below */ }
  odooState = st;
  renderChipAndJob(st);
  if (!st || activeView !== 'odoo') return;
  try { audit = await getJSON('/api/odoo/audit?limit=40'); } catch (e) { /* keep empty */ }
  renderOdooTab(st, audit);
}

document.addEventListener('click', e => {
  const b = e.target.closest('[data-act]');
  if (!b || b.disabled) return;
  if (b.dataset.confirm && !b.classList.contains('is-confirm')) {
    $$('.is-confirm').forEach(disarm);
    arm(b, b.dataset.confirm);
    return;
  }
  if (b.classList.contains('is-confirm')) disarm(b);
  const act = b.dataset.act;
  if (act === 'mode') odooPost('/api/odoo/mode', {mode: b.dataset.mode}, 'Mode set to ' + b.dataset.mode);
  else if (act === 'hold') odooPost('/api/odoo/hold', {on: b.dataset.on === '1'});
  else if (act === 'retry') odooPost('/api/odoo/retry', {}, 'Retrying failed updates');
  else if (act === 'discard') odooPost('/api/odoo/discard', {}, 'Failed updates discarded');
  else if (act === 'manual') odooPost('/api/odoo/manual', {action: b.dataset.action, step: +b.dataset.step}, 'Sent to Odoo');
});

// ------------------------------------------------------------------------ polling
// Only the visible tab is refreshed, and nothing runs while the browser tab is hidden.
function refreshActive() {
  if (document.hidden) return;
  if (activeView === 'live') refreshLive();
  else if (activeView === 'timeline') refreshTimeline();
  else if (activeView === 'history') refreshHistory();
  else if (activeView === 'analytics') refreshAnalytics();
  else if (activeView === 'odoo') refreshOdoo();
}
document.addEventListener('visibilitychange', refreshActive);
['tg-boxes', 'tg-labels', 'tg-ids', 'tg-zones', 'tg-activity'].forEach(id => $('#' + id).addEventListener('change', refreshLive));

window.addEventListener('hashchange', () => showView(location.hash.slice(1)));
showView(location.hash.slice(1));
sizeCanvas(); refreshCamera(); refreshZones(); refreshWorkers(); refreshOdoo();
setInterval(() => { if (!document.hidden && activeView === 'live') refreshLive(); }, 700);
setInterval(() => { if (!document.hidden) refreshOdoo(); }, 2000);
setInterval(() => { if (!document.hidden) { refreshCamera(); refreshZones(); refreshWorkers(); } }, 5000);
setInterval(() => { if (!document.hidden && ['timeline', 'history', 'analytics'].includes(activeView)) refreshActive(); }, 8000);
})();
</script>
</body>
</html>
"""


def render_dashboard(camera_name: str, camera_id: str) -> str:
    return fill(_TEMPLATE, base_css=BASE_CSS, page_css=_PAGE_CSS, favicon=FAVICON,
                camera_name=esc(camera_name), camera_id_js=js(camera_id))
