"""The two supporting pages: System status (`/`) and the zone editor (`/zones`).

Both use the same design system as the dashboard (`ui.py`). They are plain templates with
`__TOKEN__` placeholders; every value that comes from configuration or the camera is escaped.
"""
from __future__ import annotations

from .ui import BASE_CSS, FAVICON, esc, fill, js

_SHELL_TOP = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<meta name="theme-color" content="#F7F6F3">
<link rel="icon" href="__FAVICON__">
<style>
__BASE_CSS__
__EXTRA_CSS__
</style>
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
<div class="shell">
  <header class="top">
    <div class="brand">
      <div class="brand-mark" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M10 22V10h12M10 16h8"/></svg></div>
      <div><h1>__HEADING__</h1><div class="where">__CAMERA_NAME__</div></div>
    </div>
    <nav class="chips" aria-label="Pages">__NAV__</nav>
  </header>
  <main id="main">
"""

_SHELL_BOTTOM = r"""
  </main>
</div>
<div class="toast-zone" id="toasts" role="status" aria-live="polite"></div>
__SCRIPT__
</body>
</html>
"""


def _nav(current: str, dashboard: bool) -> str:
    items = []
    if dashboard:
        items.append(("dashboard", "/dashboard", "Dashboard"))
    items += [("zones", "/zones", "Zones"), ("status", "/", "System status")]
    return "".join(
        f'<a class="chip" href="{href}"' + (' aria-current="page" style="border-color:var(--ink)"' if key == current else "") + f">{label}</a>"
        for key, href, label in items)


# ======================================================================================= status
_STATUS_CSS = r"""
.g-status { grid-template-columns: minmax(0, 1.5fr) minmax(300px, 1fr); align-items: start; }
.checks { list-style: none; margin: 0; padding: 0; }
.checks li { display: grid; grid-template-columns: 12px minmax(0, 1fr); gap: 14px; padding: 14px 0; border-top: 1px solid var(--line); align-items: start; }
.checks li:first-child { border-top: 0; padding-top: 0; }
.checks .dot { margin-top: 7px; }
.checks .what { color: var(--ink); font-weight: 600; }
.checks .detail { color: var(--muted); font-size: 14px; margin-top: 1px; overflow-wrap: anywhere; }
@media (max-width: 1100px) { .g-status { grid-template-columns: minmax(0, 1fr); } }
"""

_STATUS_BODY = r"""
    <div class="grid g-status">
      <div class="card flush">
        <div class="frame"><img src="__IMG_SRC__" alt="Live camera view"></div>
        <div class="card-head" style="padding-top:18px;margin-bottom:6px"><h2>Camera feed</h2><span class="sub">This is what the system is analysing right now</span></div>
        <div style="padding:0 24px 22px"><p class="help">__FEED_NOTE__</p></div>
      </div>
      <div class="stack">
        <div class="card">
          <div class="card-head"><h2>System health</h2><span class="sub">Checked each time the page loads</span></div>
          <ul class="checks">__CHECKS__</ul>
        </div>
      </div>
    </div>
    <div class="card flush" style="margin-top:20px">
      <div class="card-head"><h2>Camera stream details</h2><span class="sub" id="stream-age">Updates every few seconds</span></div>
      <div class="table-wrap"><table class="tbl" id="stream-table"><tbody>__ROWS__</tbody></table></div>
    </div>
"""

_STATUS_SCRIPT = r"""<script>
(() => {
'use strict';
const fmtUp = s => { s = Math.round(s || 0); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60); return h ? h + ' h ' + m + ' min' : m + ' min ' + (s % 60) + ' s'; };
async function tick() {
  try {
    const r = await fetch('/api/status', {cache: 'no-store'});
    if (!r.ok) throw new Error(r.status);
    const st = await r.json();
    const set = (key, text) => { const el = document.querySelector('[data-k="' + key + '"]'); if (el) el.textContent = text; };
    set('connected', st.connected ? 'Connected' : 'Not connected');
    set('actual_fps', st.actual_fps != null ? st.actual_fps.toFixed(1) + ' per second' : '-');
    set('frames_received', String(st.frames_received));
    set('frames_failed', String(st.frames_failed));
    set('reconnections', String(st.reconnections));
    set('stream_uptime_s', fmtUp(st.stream_uptime_s));
    set('last_error', st.last_error || 'None');
    document.getElementById('stream-age').textContent = 'Updated ' + new Date().toLocaleTimeString();
  } catch (e) { document.getElementById('stream-age').textContent = 'Could not refresh'; }
}
setInterval(tick, 5000);
})();
</script>"""


def render_status(camera_name: str, checks: list[tuple[str, str, str]], img_src: str, feed_note: str,
                  rows: list[tuple[str, str, str]], dashboard: bool) -> str:
    """`checks`: (state ok|warn|bad, title, detail). `rows`: (data-key, label, value)."""
    check_html = "".join(
        f'<li><span class="dot {esc(state)}"></span><div><div class="what">{esc(title)}</div><div class="detail">{esc(detail)}</div></div></li>'
        for state, title, detail in checks)
    row_html = "".join(f'<tr><td class="k">{esc(label)}</td><td data-k="{esc(key)}">{esc(value)}</td></tr>' for key, label, value in rows)
    page = _SHELL_TOP + _STATUS_BODY + _SHELL_BOTTOM
    return fill(page, title=f"System status - {esc(camera_name)}", favicon=FAVICON, base_css=BASE_CSS, extra_css=_STATUS_CSS,
                heading="System status", camera_name=esc(camera_name), nav=_nav("status", dashboard),
                img_src=esc(img_src), feed_note=esc(feed_note), checks=check_html, rows=row_html, script=_STATUS_SCRIPT)


# ======================================================================================== zones
_ZONES_CSS = r"""
.g-zones { grid-template-columns: minmax(0, 1.85fr) minmax(320px, 1fr); align-items: start; }
.frame.draw canvas { cursor: crosshair; }
.zone-list { list-style: none; margin: 0; padding: 0; }
.zone-list li { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 13px 0; border-top: 1px solid var(--line); }
.zone-list li:first-child { border-top: 0; padding-top: 0; }
.zone-name { color: var(--ink); font-weight: 500; overflow-wrap: anywhere; }
.zone-meta { color: var(--muted); font-size: 13px; }
.field { display: grid; gap: 6px; margin-bottom: 14px; }
.field label { font-size: 13.5px; color: var(--muted); }
.field .input, .field .select { width: 100%; }
.steps { margin: 0; padding-left: 20px; color: var(--muted); font-size: 14px; display: grid; gap: 4px; }
@media (max-width: 1100px) { .g-zones { grid-template-columns: minmax(0, 1fr); } }
"""

_ZONES_BODY = r"""
    <div class="grid g-zones">
      <div class="card flush">
        <div class="frame draw">
          <img id="bg" src="/live/frame.jpg?t=0" alt="Camera view. Click to place the corners of a zone.">
          <canvas id="cv" aria-label="Drawing area for zones"></canvas>
        </div>
        <div class="frame-bar">
          <button class="btn btn-sm" id="undo" type="button">Undo point</button>
          <button class="btn btn-sm" id="clear" type="button">Clear points</button>
          <button class="btn btn-sm" id="refresh" type="button">Refresh picture</button>
          <span class="help" id="point-count" style="margin-left:auto" aria-live="polite">0 points placed</span>
        </div>
      </div>
      <div class="stack">
        <div class="card">
          <div class="card-head"><h2>New zone</h2></div>
          <ol class="steps" style="margin-bottom:18px">
            <li>Click the corners of the area on the picture (at least 3).</li>
            <li>Name the zone and choose what happens there.</li>
            <li>Press Save zone.</li>
          </ol>
          <form id="zone-form" novalidate>
            <div class="field"><label for="name">Zone name</label><input class="input" id="name" autocomplete="off" placeholder="For example, Cutting table"></div>
            <div class="field"><label for="activity">What happens here</label>
              <select class="select" id="activity">
                <option value="">Nothing specific (just an area)</option>
                <option value="marking,cutting">Marking and cutting (one table)</option>
                <option value="cutting">Cutting</option>
                <option value="marking">Marking</option>
                <option value="stitching">Stitching</option>
                <option value="piece_handling">Piece handling</option>
              </select></div>
            <button class="btn btn-primary" id="save" type="submit">Save zone</button>
          </form>
        </div>
        <div class="card">
          <div class="card-head"><h2>Zones</h2><span class="sub" id="zone-count"></span></div>
          <ul class="zone-list" id="list"><li><span class="zone-meta">Loading...</span></li></ul>
        </div>
      </div>
    </div>
"""

_ZONES_SCRIPT = r"""<script>
(() => {
'use strict';
const CAMERA_ID = __CAMERA_ID_JS__;
const $ = s => document.querySelector(s);
const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const img = $('#bg'), cv = $('#cv'), list = $('#list');
let points = [], zones = [];
const ACTIVITY = {marking: 'Marking', cutting: 'Cutting', stitching: 'Stitching', piece_handling: 'Piece handling'};
const activityText = a => a ? a.split(',').map(x => ACTIVITY[x] || x).join(' and ') : 'No specific activity';

function toast(msg, kind) {
  const el = document.createElement('div');
  el.className = 'toast' + (kind === 'bad' ? ' bad' : '');
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), kind === 'bad' ? 6000 : 3500);
}

const DPR = () => Math.min(window.devicePixelRatio || 1, 2);
function size() {
  const w = cv.clientWidth, h = cv.clientHeight, d = DPR();
  if (!w || !h) return;
  const bw = Math.round(w * d), bh = Math.round(h * d);
  if (cv.width !== bw || cv.height !== bh) { cv.width = bw; cv.height = bh; }
  draw();
}
window.addEventListener('resize', size);
img.addEventListener('load', size);
if ('ResizeObserver' in window) new ResizeObserver(size).observe($('.frame'));

function path(ctx, pts, W, H, close) {
  ctx.beginPath();
  pts.forEach(([x, y], i) => { i ? ctx.lineTo(x * W, y * H) : ctx.moveTo(x * W, y * H); });
  if (close) ctx.closePath();
}
function tag(ctx, text, x, y, W) {
  ctx.font = '600 12px "Segoe UI", system-ui, sans-serif';
  const w = ctx.measureText(text).width + 14, h = 20, px = Math.max(2, Math.min(x, W - w - 2)), py = Math.max(2, y - h - 3);
  ctx.fillStyle = 'rgba(17,17,17,.82)'; ctx.beginPath(); if (ctx.roundRect) ctx.roundRect(px, py, w, h, 5); else ctx.rect(px, py, w, h); ctx.fill();
  ctx.fillStyle = '#fff'; ctx.textBaseline = 'middle'; ctx.fillText(text, px + 7, py + h / 2 + .5);
}
function draw() {
  const d = DPR(), W = cv.width / d, H = cv.height / d, ctx = cv.getContext('2d');
  ctx.setTransform(d, 0, 0, d, 0, 0); ctx.clearRect(0, 0, W, H);
  zones.forEach(z => {
    if (z.points.length < 3) return;
    path(ctx, z.points, W, H, true);
    ctx.fillStyle = 'rgba(255,255,255,.08)'; ctx.fill();
    ctx.setLineDash([7, 6]); ctx.lineWidth = 1.5; ctx.strokeStyle = 'rgba(255,255,255,.9)'; ctx.stroke(); ctx.setLineDash([]);
    tag(ctx, z.name, z.points[0][0] * W + 4, z.points[0][1] * H + 26, W);
  });
  if (points.length) {
    path(ctx, points, W, H, points.length > 2);
    ctx.fillStyle = 'rgba(124,196,143,.28)'; if (points.length > 2) ctx.fill();
    ctx.lineWidth = 2; ctx.strokeStyle = '#7CC48F'; ctx.stroke();
    ctx.fillStyle = '#7CC48F';
    points.forEach(([x, y]) => { ctx.beginPath(); ctx.arc(x * W, y * H, 4.5, 0, 7); ctx.fill(); });
  }
  $('#point-count').textContent = points.length + (points.length === 1 ? ' point placed' : ' points placed');
}

cv.addEventListener('click', e => {
  const r = cv.getBoundingClientRect();
  points.push([+((e.clientX - r.left) / r.width).toFixed(4), +((e.clientY - r.top) / r.height).toFixed(4)]);
  draw();
});
$('#undo').onclick = () => { points.pop(); draw(); };
$('#clear').onclick = () => { points = []; draw(); };
$('#refresh').onclick = () => { img.src = '/live/frame.jpg?t=' + Date.now(); };

async function loadZones() {
  try {
    const r = await fetch('/api/zones?camera_id=' + encodeURIComponent(CAMERA_ID), {cache: 'no-store'});
    if (!r.ok) throw new Error(r.status);
    zones = await r.json();
  } catch (e) { toast('Could not load the zones. Please refresh the page.', 'bad'); return; }
  $('#zone-count').textContent = zones.length ? zones.length + (zones.length === 1 ? ' zone' : ' zones') : '';
  list.innerHTML = zones.length ? zones.map(z => '<li><div><div class="zone-name">' + esc(z.name) + '</div><div class="zone-meta">' +
      z.points.length + ' points · ' + esc(activityText(z.activity)) + '</div></div>' +
      '<button class="btn btn-sm btn-danger" type="button" data-id="' + z.id + '" data-name="' + esc(z.name) + '">Delete</button></li>').join('')
    : '<li><div class="empty compact" style="width:100%"><strong>No zones yet</strong>Draw the first one on the picture.</div></li>';
  draw();
}

// Deleting asks for a second press instead of a pop-up.
list.addEventListener('click', async e => {
  const b = e.target.closest('button[data-id]');
  if (!b) return;
  if (!b.classList.contains('is-confirm')) {
    b.classList.add('is-confirm'); b.textContent = 'Confirm delete';
    setTimeout(() => { b.classList.remove('is-confirm'); b.textContent = 'Delete'; }, 4000);
    return;
  }
  try {
    const r = await fetch('/api/zones/' + b.dataset.id, {method: 'DELETE'});
    if (!r.ok) throw new Error(r.status);
    toast('Deleted ' + b.dataset.name);
  } catch (err) { toast('Could not delete the zone.', 'bad'); }
  loadZones();
});

$('#zone-form').addEventListener('submit', async e => {
  e.preventDefault();
  const name = $('#name').value.trim();
  if (!name) { toast('Give the zone a name first.', 'bad'); $('#name').focus(); return; }
  if (points.length < 3) { toast('Click at least 3 points on the picture first.', 'bad'); return; }
  const activity = $('#activity').value || null;
  try {
    const res = await fetch('/api/zones', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({camera_id: CAMERA_ID, name, points, activity})});
    if (!res.ok) { const b = await res.json().catch(() => ({})); toast('Could not save. ' + (b.detail || 'The server answered ' + res.status + '.'), 'bad'); return; }
  } catch (err) { toast('Could not reach the server.', 'bad'); return; }
  toast('Saved ' + name);
  points = []; $('#name').value = ''; $('#activity').value = '';
  loadZones();
});

size(); loadZones();
})();
</script>"""


def render_zones(camera_name: str, camera_id: str, dashboard: bool = True) -> str:
    page = _SHELL_TOP + _ZONES_BODY + _SHELL_BOTTOM
    return fill(page, title=f"Zones - {esc(camera_name)}", favicon=FAVICON, base_css=BASE_CSS, extra_css=_ZONES_CSS,
                heading="Zones", camera_name=esc(camera_name), nav=_nav("zones", dashboard),
                script=fill(_ZONES_SCRIPT, camera_id_js=js(camera_id)))
