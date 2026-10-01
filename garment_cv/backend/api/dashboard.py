"""Phase 7's dashboard page (spec sections 14-18): live monitoring, a per-worker timeline,
activity history, and analytics (stage averages, idle time, bottleneck flags), all in one plain
server-rendered HTML page with vanilla JS - the same style every earlier phase's page already
uses (`_PAGE`, `_ZONES_PAGE` in `routes.py`), not a separate frontend build.

The live view deliberately draws its own overlay client-side (a `<canvas>` over the RAW
`/live.mjpg` stream, polling `/api/detections` + `/api/activities` + `/api/zones`) rather than
asking the server for a pre-annotated stream: `ObjectDetector` renders one shared annotated JPEG
per detection cycle for every viewer (see its module docstring), so there is no way to honor a
per-viewer "hide zones" toggle (spec section 15's explicit requirement) on that shared stream
without re-rendering it once per combination of toggles. Drawing client-side is also exactly the
technique `/zones` (Phase 4) already uses for its own overlay - proven, not new.
"""
from __future__ import annotations

DASHBOARD_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Garment CV - Dashboard</title>
<style>
:root {{ color-scheme: dark; }}
body{{font:14px/1.5 system-ui,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:0}}
a{{color:#4c9eff}}
.wrap{{max-width:1180px;margin:0 auto;padding:20px 24px 60px}}
h1{{font-size:1.15rem;margin:0 0 2px}}
p.note{{color:#8b949e;font-size:.8rem;margin:0 0 16px}}
.tabs{{display:flex;gap:4px;border-bottom:1px solid #21262d;margin-bottom:18px}}
.tab{{padding:9px 16px;cursor:pointer;color:#8b949e;border-bottom:2px solid transparent;font-size:.86rem}}
.tab.active{{color:#e6edf3;border-bottom-color:#1f6feb}}
.view{{display:none}} .view.active{{display:block}}
.row{{display:flex;gap:18px;flex-wrap:wrap}}
.stage{{position:relative;background:#000;border-radius:8px;overflow:hidden;line-height:0;flex:2;min-width:420px}}
.stage img{{width:100%;display:block}} .stage canvas{{position:absolute;inset:0;width:100%;height:100%}}
.toggles{{display:flex;gap:14px;flex-wrap:wrap;margin:10px 0;font-size:.8rem;color:#c9d1d9}}
.toggles label{{display:flex;align-items:center;gap:5px;cursor:pointer}}
.panel{{flex:1;min-width:280px;background:#161b22;border:1px solid #21262d;border-radius:8px;padding:12px}}
.panel h3{{margin:0 0 8px;font-size:.88rem;color:#8b949e;font-weight:600}}
.track{{border:1px solid #21262d;border-radius:6px;padding:8px 10px;margin-bottom:8px;font-size:.82rem}}
.track .t1{{display:flex;justify-content:space-between;font-weight:600}}
.badge{{display:inline-block;padding:1px 7px;border-radius:999px;font-size:.72rem;margin-left:6px}}
.b-cutting{{background:#9e6a03;color:#fff}} .b-marking{{background:#1f6feb;color:#fff}}
.b-stitching{{background:#8957e5;color:#fff}} .b-piece_handling{{background:#1a7f37;color:#fff}}
.b-idle{{background:#30363d;color:#8b949e}}
.assign{{display:flex;gap:4px;margin-top:6px}}
.assign input{{flex:1;background:#0d1117;border:1px solid #30363d;color:#e6edf3;border-radius:4px;padding:4px 6px;font:inherit;font-size:.78rem}}
.assign button{{background:#21262d;border:1px solid #30363d;color:#e6edf3;border-radius:4px;padding:4px 8px;font-size:.78rem;cursor:pointer}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px}}
.card{{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:14px 18px;min-width:140px}}
.card .v{{font-size:1.5rem;font-weight:700}} .card .k{{color:#8b949e;font-size:.76rem;margin-top:2px}}
table{{width:100%;border-collapse:collapse;font-size:.82rem;margin-bottom:18px}}
th{{text-align:left;color:#8b949e;font-weight:600;padding:6px 8px;border-bottom:1px solid #30363d}}
td{{padding:6px 8px;border-bottom:1px solid #21262d}}
.pill{{display:inline-block;padding:2px 9px;border-radius:999px;font-size:.74rem}}
.pill-ok{{background:#1a7f37;color:#fff}} .pill-warn{{background:#9e6a03;color:#fff}}
select,.filt-input{{background:#161b22;border:1px solid #30363d;color:#e6edf3;border-radius:6px;padding:6px 9px;font:inherit;font-size:.8rem}}
.timeline-row{{display:flex;align-items:center;margin-bottom:10px;font-size:.8rem}}
.timeline-row .lbl{{width:110px;flex-shrink:0;color:#c9d1d9}}
.timeline-track{{position:relative;flex:1;height:22px;background:#161b22;border-radius:4px;overflow:hidden}}
.timeline-block{{position:absolute;top:0;bottom:0;border-radius:2px}}
.axis{{display:flex;justify-content:space-between;color:#8b949e;font-size:.72rem;margin:4px 0 16px 110px}}
.empty{{color:#8b949e;font-size:.82rem;padding:10px 0}}
</style></head><body><div class="wrap">
<h1>Garment CV — {camera_name}</h1>
<p class="note">Phase 7 dashboard. <a href="/">&larr; Status page</a> &middot; <a href="/zones">Zones &rarr;</a></p>

<div class="tabs">
  <div class="tab active" data-view="live">Live Monitoring</div>
  <div class="tab" data-view="timeline">Production Timeline</div>
  <div class="tab" data-view="history">Activity History</div>
  <div class="tab" data-view="analytics">Analytics</div>
</div>

<div id="view-live" class="view active">
  <div class="row">
    <div class="stage"><img id="bg" src="/live.mjpg" alt="camera view"><canvas id="cv"></canvas></div>
    <div class="panel">
      <h3>Tracked people</h3>
      <div id="tracks"><p class="empty">No one currently tracked.</p></div>
    </div>
  </div>
  <div class="toggles">
    <label><input type="checkbox" id="tg-boxes" checked> Bounding boxes</label>
    <label><input type="checkbox" id="tg-labels" checked> Object labels</label>
    <label><input type="checkbox" id="tg-ids" checked> Tracking IDs</label>
    <label><input type="checkbox" id="tg-zones" checked> Zones</label>
    <label><input type="checkbox" id="tg-activity" checked> Activity labels</label>
  </div>
</div>

<div id="view-timeline" class="view">
  <p class="note">Each row is one tracked worker; blocks show which stage they were in, in order.</p>
  <div id="timeline-body"><p class="empty">No production events recorded yet.</p></div>
</div>

<div id="view-history" class="view">
  <div style="margin-bottom:10px">
    <select id="hist-stage">
      <option value="">All stages</option>
      <option>MARKING</option><option>CUTTING</option><option>STITCHING</option>
      <option>PIECE_HANDLING</option><option>IDLE</option>
    </select>
  </div>
  <table id="hist-table">
    <thead><tr><th>Worker</th><th>Stage</th><th>Zone</th><th>Start</th><th>End</th><th>Duration</th><th>Confidence</th></tr></thead>
    <tbody><tr><td colspan="7" class="empty">Loading…</td></tr></tbody>
  </table>
</div>

<div id="view-analytics" class="view">
  <div class="cards" id="an-cards"></div>
  <h3 style="color:#8b949e;font-size:.88rem;margin:0 0 8px">Stage performance</h3>
  <table id="an-stage-table">
    <thead><tr><th>Stage</th><th>Completed</th><th>Average time</th><th>Total time</th></tr></thead>
    <tbody></tbody>
  </table>
  <h3 style="color:#8b949e;font-size:.88rem;margin:0 0 8px">Bottleneck detection</h3>
  <p class="note">Baseline vs. measured average only - this never guesses the cause (spec section 18); investigate manually.</p>
  <table id="an-bottleneck-table">
    <thead><tr><th>Stage</th><th>Baseline</th><th>Actual average</th><th>Status</th></tr></thead>
    <tbody></tbody>
  </table>
  <h3 style="color:#8b949e;font-size:.88rem;margin:0 0 8px">Worker active vs. idle time</h3>
  <table id="an-worker-table">
    <thead><tr><th>Worker</th><th>Active time</th><th>Idle time</th></tr></thead>
    <tbody></tbody>
  </table>
</div>

<script>
const CAMERA_ID = {camera_id_js};
const STAGE_COLORS = {{CUTTING:'#9e6a03', MARKING:'#1f6feb', STITCHING:'#8957e5', PIECE_HANDLING:'#1a7f37', IDLE:'#30363d'}};

function fmtDur(s) {{
  s = Math.max(0, Math.round(s));
  const m = Math.floor(s / 60), r = s % 60;
  return `${{String(m).padStart(2,'0')}}:${{String(r).padStart(2,'0')}}`;
}}
function fmtTime(ts) {{ return new Date(ts * 1000).toLocaleTimeString(); }}

// ---------------------------------------------------------------- tabs
document.querySelectorAll('.tab').forEach(t => {{
  t.onclick = () => {{
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.view').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById('view-' + t.dataset.view).classList.add('active');
  }};
}});

// ---------------------------------------------------------------- live view
const img = document.getElementById('bg'), cv = document.getElementById('cv');
function resizeCanvas() {{ cv.width = img.clientWidth; cv.height = img.clientHeight; }}
window.addEventListener('resize', resizeCanvas);
img.addEventListener('load', resizeCanvas);

let zonesCache = [], workersCache = {{}}, srcW = 0, srcH = 0;

async function refreshSourceSize() {{
  // Detection boxes are in the camera's ORIGINAL capture resolution (Ultralytics rescales boxes
  // back to the input frame it was given - see detector.py's `observe`), which can be larger than
  // what `/live.mjpg` actually displays (`GARMENT_STREAM_MAX_WIDTH` may downscale it for the
  // stream). `img.naturalWidth/Height` would reflect the DISPLAYED size, not the detection
  // coordinate space, so the scale factor has to come from `/api/status`'s real sensor resolution.
  try {{
    const st = await (await fetch('/api/status')).json();
    const m = /^(\\d+)x(\\d+)$/.exec(st.resolution || '');
    if (m) {{ srcW = +m[1]; srcH = +m[2]; }}
  }} catch (e) {{}}
}}

function poly(ctx, pts, color) {{
  if (!pts.length) return;
  ctx.beginPath();
  pts.forEach(([x, y], i) => {{ const px = x * cv.width, py = y * cv.height; i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); }});
  ctx.closePath(); ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke();
}}

async function refreshZones() {{
  try {{
    const r = await fetch('/api/zones?camera_id=' + encodeURIComponent(CAMERA_ID));
    zonesCache = await r.json();
  }} catch (e) {{}}
}}
async function refreshWorkers() {{
  try {{
    const r = await fetch('/api/workers');
    const list = await r.json();
    workersCache = {{}};
    list.forEach(w => {{ workersCache[w.track_id] = w.name; }});
  }} catch (e) {{}}
}}

async function refreshLive() {{
  const showBoxes = document.getElementById('tg-boxes').checked;
  const showLabels = document.getElementById('tg-labels').checked;
  const showIds = document.getElementById('tg-ids').checked;
  const showZones = document.getElementById('tg-zones').checked;
  const showActivity = document.getElementById('tg-activity').checked;

  let dets = [], acts = {{current: {{}}}};
  try {{
    dets = (await (await fetch('/api/detections')).json()).detections || [];
    acts = await (await fetch('/api/activities')).json();
  }} catch (e) {{ return; }}

  const ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, cv.width, cv.height);

  if (showZones) {{
    zonesCache.forEach(z => poly(ctx, z.points, '#f0883e'));
  }}

  dets.forEach(d => {{
    const [x1, y1, x2, y2] = d.box;
    // detections are in the camera's original capture resolution; scale into the canvas's
    // displayed CSS-pixel size using the real sensor resolution from /api/status (see refreshSourceSize)
    const sx = cv.width / (srcW || cv.width), sy = cv.height / (srcH || cv.height);
    const rx1 = x1 * sx, ry1 = y1 * sy, rx2 = x2 * sx, ry2 = y2 * sy;
    if (showBoxes) {{
      ctx.strokeStyle = d.class === 'person' ? '#3fb950' : '#4c9eff';
      ctx.lineWidth = 2;
      ctx.strokeRect(rx1, ry1, rx2 - rx1, ry2 - ry1);
    }}
    let label = '';
    if (showLabels) label += d.class;
    if (showIds && d.track_id != null) label += ` #${{d.track_id}}`;
    if (showActivity && d.track_id != null) {{
      const cur = acts.current[d.track_id];
      if (cur && cur.activity) label += ` ${{cur.activity}}`;
    }}
    if (label) {{
      ctx.fillStyle = '#e6edf3'; ctx.font = '12px sans-serif';
      ctx.fillText(label.trim(), rx1 + 2, Math.max(10, ry1 - 4));
    }}
  }});

  // side panel - rebuilt from scratch every poll, so anything a viewer is mid-typing into an
  // "Assign name" box would otherwise vanish before they can click Save (found by actually
  // typing into it in a real browser, not assumed) - captured here and restored below.
  const inProgress = {{}};
  document.querySelectorAll('[id^="name-"]').forEach(el => {{
    if (el.value) inProgress[el.id.slice(5)] = el.value;
  }});
  const trackIds = Object.keys(acts.current || {{}});
  const panel = document.getElementById('tracks');
  if (!trackIds.length) {{
    panel.innerHTML = '<p class="empty">No one currently tracked.</p>';
  }} else {{
    const now = Date.now() / 1000;
    panel.innerHTML = trackIds.map(tid => {{
      const c = acts.current[tid];
      const activity = c.activity || 'unknown';
      const cls = 'b-' + activity.toLowerCase();
      const dur = c.since ? fmtDur(now - c.since) : '--:--';
      const name = workersCache[tid] || ('track-' + tid);
      const restored = inProgress[tid] ? ` value="${{inProgress[tid].replace(/"/g, '&quot;')}}"` : '';
      return `<div class="track">
        <div class="t1"><span>${{name}}</span><span class="badge ${{cls}}">${{activity}}</span></div>
        <div>Zone: ${{c.zone || '—'}} &middot; Duration: ${{dur}}</div>
        <div class="assign">
          <input placeholder="Assign name" id="name-${{tid}}"${{restored}}>
          <button onclick="assignWorker(${{tid}})">Save</button>
        </div>
      </div>`;
    }}).join('');
  }}
}}

async function assignWorker(trackId) {{
  const input = document.getElementById('name-' + trackId);
  const name = input.value.trim();
  if (!name) return;
  await fetch('/api/workers', {{method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{track_id: trackId, name}})}});
  await refreshWorkers();
}}

// ---------------------------------------------------------------- timeline
async function refreshTimeline() {{
  let events = [];
  try {{
    events = await (await fetch('/api/events?limit=500')).json();
  }} catch (e) {{ return; }}
  const body = document.getElementById('timeline-body');
  if (!events.length) {{
    body.innerHTML = '<p class="empty">No production events recorded yet.</p>';
    return;
  }}
  const byWorker = {{}};
  events.forEach(e => {{ (byWorker[e.worker] = byWorker[e.worker] || []).push(e); }});
  const minTs = Math.min(...events.map(e => e.start_ts));
  const maxTs = Math.max(...events.map(e => e.end_ts));
  const span = Math.max(maxTs - minTs, 1);

  let html = '';
  Object.keys(byWorker).forEach(worker => {{
    const blocks = byWorker[worker].slice().sort((a, b) => a.start_ts - b.start_ts).map(e => {{
      const left = ((e.start_ts - minTs) / span) * 100;
      const width = Math.max(((e.end_ts - e.start_ts) / span) * 100, 0.3);
      const color = STAGE_COLORS[e.stage] || '#30363d';
      return `<div class="timeline-block" title="${{e.stage}}: ${{fmtTime(e.start_ts)}}–${{fmtTime(e.end_ts)}} (${{fmtDur(e.duration_s)}})" style="left:${{left}}%;width:${{width}}%;background:${{color}}"></div>`;
    }}).join('');
    html += `<div class="timeline-row"><div class="lbl">${{worker}}</div><div class="timeline-track">${{blocks}}</div></div>`;
  }});
  html += `<div class="axis"><span>${{fmtTime(minTs)}}</span><span>${{fmtTime(maxTs)}}</span></div>`;
  body.innerHTML = html;
}}

// ---------------------------------------------------------------- history
async function refreshHistory() {{
  const stage = document.getElementById('hist-stage').value;
  let url = '/api/events?limit=200';
  if (stage) url += '&stage=' + encodeURIComponent(stage);
  let events = [];
  try {{ events = await (await fetch(url)).json(); }} catch (e) {{ return; }}
  const tbody = document.querySelector('#hist-table tbody');
  if (!events.length) {{
    tbody.innerHTML = '<tr><td colspan="7" class="empty">No production events recorded yet.</td></tr>';
    return;
  }}
  tbody.innerHTML = events.map(e => `<tr>
    <td>${{e.worker}}</td><td>${{e.stage}}</td><td>${{e.zone || '—'}}</td>
    <td>${{fmtTime(e.start_ts)}}</td><td>${{fmtTime(e.end_ts)}}</td>
    <td>${{fmtDur(e.duration_s)}}</td><td>${{Math.round(e.confidence * 100)}}%</td>
  </tr>`).join('');
}}
document.getElementById('hist-stage').addEventListener('change', refreshHistory);

// ---------------------------------------------------------------- analytics
async function refreshAnalytics() {{
  let an = {{stage_summary: {{}}, worker_active_seconds: {{}}, worker_idle_seconds: {{}}, bottlenecks: []}};
  let acts = {{current: {{}}}};
  try {{
    an = await (await fetch('/api/analytics')).json();
    acts = await (await fetch('/api/activities')).json();
  }} catch (e) {{ return; }}

  const cur = Object.values(acts.current || {{}});
  const activeWorkers = cur.filter(c => c.activity && c.activity !== 'IDLE').length;
  const countBy = stage => cur.filter(c => c.activity === stage).length;
  const totalCompleted = Object.values(an.stage_summary).reduce((s, v) => s + v.count, 0);

  document.getElementById('an-cards').innerHTML = [
    ['Active workers', activeWorkers], ['Active marking', countBy('MARKING')],
    ['Active cutting', countBy('CUTTING')], ['Active piece handling', countBy('PIECE_HANDLING')],
    ['Active stitching', countBy('STITCHING')], ['Completed activities', totalCompleted],
  ].map(([k, v]) => `<div class="card"><div class="v">${{v}}</div><div class="k">${{k}}</div></div>`).join('');

  const stages = ['MARKING', 'CUTTING', 'PIECE_HANDLING', 'STITCHING', 'IDLE'];
  document.querySelector('#an-stage-table tbody').innerHTML = stages.map(s => {{
    const row = an.stage_summary[s];
    return `<tr><td>${{s}}</td><td>${{row ? row.count : 0}}</td><td>${{row ? fmtDur(row.avg_s) : '—'}}</td><td>${{row ? fmtDur(row.total_s) : '—'}}</td></tr>`;
  }}).join('');

  const bTbody = document.querySelector('#an-bottleneck-table tbody');
  bTbody.innerHTML = an.bottlenecks.length
    ? an.bottlenecks.map(b => `<tr><td>${{b.stage}}</td><td>${{fmtDur(b.baseline_s)}}</td><td>${{fmtDur(b.actual_avg_s)}}</td>
        <td><span class="pill ${{b.status === 'OK' ? 'pill-ok' : 'pill-warn'}}">${{b.status}}</span></td></tr>`).join('')
    : '<tr><td colspan="4" class="empty">No baselines configured (GARMENT_BASELINE_&lt;STAGE&gt;_S) - nothing to compare yet.</td></tr>';

  const workers = new Set([...Object.keys(an.worker_active_seconds), ...Object.keys(an.worker_idle_seconds)]);
  const wTbody = document.querySelector('#an-worker-table tbody');
  wTbody.innerHTML = workers.size
    ? [...workers].map(w => `<tr><td>${{w}}</td><td>${{fmtDur(an.worker_active_seconds[w] || 0)}}</td><td>${{fmtDur(an.worker_idle_seconds[w] || 0)}}</td></tr>`).join('')
    : '<tr><td colspan="3" class="empty">No completed activities recorded yet.</td></tr>';
}}

// ---------------------------------------------------------------- polling
resizeCanvas();
refreshSourceSize(); refreshZones(); refreshWorkers(); refreshLive();
refreshTimeline(); refreshHistory(); refreshAnalytics();
setInterval(refreshLive, 700);
setInterval(() => {{ refreshZones(); refreshWorkers(); refreshSourceSize(); }}, 5000);
setInterval(refreshTimeline, 8000);
setInterval(refreshHistory, 8000);
setInterval(refreshAnalytics, 8000);
</script>
</div></body></html>"""
