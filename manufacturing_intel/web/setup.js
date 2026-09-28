/* Cameras (status, live view, zone editor) and Setup (factory, shifts, lines, products, routes, stations, integration). */
(() => {
  const { $, $$, esc, api, act, fmtNum, fmtTime, badge, empty, label, reloadConfig, toast } = MPI;

  /* -------------------------------------------------------------------- cameras */
  let camId = null, zoneStation = null, points = [], frameTick = 0;
  const camState = (c) => {
    const s = c.status;
    if (!s.running) return badge("stopped", "");
    if (s.error) return badge("problem", "crit");
    if (s.learning) return badge("learning empty scene", "warn");
    return badge(s.connected ? "running" : "connecting", s.connected ? "ok" : "warn");
  };
  function draw() {
    const cv = $("#cam-canvas"), img = $("#cam-img");
    if (!cv || !img) return;
    cv.width = img.clientWidth; cv.height = img.clientHeight;
    const g = cv.getContext("2d");
    g.clearRect(0, 0, cv.width, cv.height);
    const cam = (MPI.S.camList || []).find((c) => c.id === camId);
    const poly = (pts, col, fill, name) => {
      if (!pts.length) return;
      g.beginPath(); pts.forEach(([x, y], i) => (i ? g.lineTo(x * cv.width, y * cv.height) : g.moveTo(x * cv.width, y * cv.height)));
      g.closePath(); g.fillStyle = fill; g.fill(); g.lineWidth = 2; g.strokeStyle = col; g.stroke();
      if (name) { g.fillStyle = col; g.font = "12px sans-serif"; g.fillText(name, pts[0][0] * cv.width + 4, pts[0][1] * cv.height - 5); }
    };
    if (cam) cam.stations.forEach((s) => { if (s.zone && String(s.id) !== String(zoneStation)) poly(s.zone, "#f0883e", "rgba(240,136,62,.18)", s.name); });
    if (points.length) {
      poly(points.length > 2 ? points : [], "#3fb950", "rgba(63,185,80,.25)");
      g.strokeStyle = "#3fb950"; g.lineWidth = 2; g.beginPath(); points.forEach(([x, y], i) => (i ? g.lineTo(x * cv.width, y * cv.height) : g.moveTo(x * cv.width, y * cv.height))); g.stroke();
      g.fillStyle = "#3fb950"; points.forEach(([x, y]) => { g.beginPath(); g.arc(x * cv.width, y * cv.height, 4, 0, 6.3); g.fill(); });
    }
  }
  function liveView(cam) {
    const box = $("#cam-live");
    if (!cam) { box.innerHTML = empty("Add a camera to see it here."); return; }
    const editing = !!zoneStation;
    const src = editing || !cam.status.running ? `/api/cameras/${cam.id}/frame.jpg?t=${++frameTick}` : `/api/cameras/${cam.id}/live.mjpg`;
    box.innerHTML = `<div class="video"><img id="cam-img" alt="camera view" src="${src}"><canvas id="cam-canvas"></canvas></div>
      <p class="note" style="margin-top:8px">${editing ? "Click on the picture to outline the station's zone (at least 3 points)." : cam.status.running ? "Live view with detections. Orange = zones, green boxes = products being tracked." : "Camera stopped - showing a still picture."}</p>`;
    const img = $("#cam-img");
    img.addEventListener("load", draw); img.addEventListener("error", () => { box.querySelector(".note").textContent = "No picture from this camera (is it running and reachable?)."; });
    $("#cam-canvas").addEventListener("click", (e) => {
      if (!zoneStation) return;
      const r = e.target.getBoundingClientRect();
      points.push([+((e.clientX - r.left) / r.width).toFixed(4), +((e.clientY - r.top) / r.height).toFixed(4)]);
      draw();
    });
    $("#cam-canvas").style.pointerEvents = editing ? "auto" : "none";
  }
  function zonePanel(cam) {
    const p = $("#cam-zone");
    if (!cam) { p.innerHTML = ""; return; }
    const line = MPI.S.cfg.stations.filter((s) => s.line_id === cam.line_id);
    p.innerHTML = `<div class="card"><div class="card-head"><span>Station zones</span></div>
      <p class="note">A station's zone is the area of this camera's picture where its work happens. A product settling in the zone starts the station's clock; leaving stops it.</p>
      <div class="stack">${line.filter((s) => s.kind !== "source" || true).map((s) => {
        const mine = s.camera_id === cam.id && s.zone;
        return `<div class="step"><span style="flex:1"><b>${esc(s.name)}</b> <span class="muted small">${s.kind}</span></span>
          ${mine ? badge("zone set", "ok") : s.camera_id && s.camera_id !== cam.id ? badge("other camera", "") : badge("no zone", "")}
          <button class="btn small" data-zone="${s.id}">${mine ? "Redraw" : "Draw zone"}</button>${mine ? `<button class="btn-link danger" data-unzone="${s.id}">Remove</button>` : ""}</div>`;
      }).join("") || empty("This camera isn't assigned to a line, or the line has no stations.")}</div>
      <div id="cam-editbar" class="row" style="margin-top:12px" ${zoneStation ? "" : "hidden"}>
        <button class="btn primary" id="cz-save">Save zone</button><button class="btn" id="cz-undo">Undo point</button><button class="btn" id="cz-clear">Clear</button><button class="btn" id="cz-cancel">Cancel</button></div></div>`;
  }

  MPI.register("cameras", {
    title: "Cameras", filters: [], every: 3000,
    init(root) {
      root.innerHTML = `<div class="split"><div><div class="card"><div class="card-head"><span>Cameras</span></div><div id="cam-list"></div></div>
          <div class="card" style="margin-top:18px"><div class="card-head"><span>Add a camera</span></div>
          <p class="note">The source can be an RTSP/HTTP address, a device number (0 for a USB camera) or the path of a video file. A video file plays in a loop, which is how to try the product without a live camera.</p>
          <div class="form-grid"><label class="field">Name<input id="ca-name"></label><label class="field" style="grid-column:span 2">Source<input id="ca-src" placeholder="rtsp://user:pass@192.168.1.20/stream   or   C:\\videos\\line.mp4"></label>
          <label class="field">Line<select id="ca-line"></select></label>
          <label class="field">Detection<select id="ca-det"><option value="motion">Background reference (any product, fixed camera)</option><option value="yolo">YOLO (object detector)</option></select></label>
          <label class="field">Read QR codes<select id="ca-qr"><option value="0">No</option><option value="1">Yes - QR on each product</option></select></label>
          <button class="btn primary" id="ca-add">Add camera</button></div></div></div>
        <div><div class="card"><div class="card-head"><span id="cam-live-title">Live view</span></div><div id="cam-live"></div></div><div id="cam-zone" style="margin-top:18px"></div></div></div>`;
      $("#ca-add").addEventListener("click", () => act(async () => {
        await api("/cameras", { method: "POST", body: { name: $("#ca-name").value, source: $("#ca-src").value, line_id: $("#ca-line").value ? +$("#ca-line").value : null, detector: $("#ca-det").value, decode_qr: $("#ca-qr").value === "1", enabled: true } });
        $("#ca-name").value = ""; $("#ca-src").value = ""; await reloadConfig(); await this.load();
      }, "Camera added"));
      $("#cam-list").addEventListener("click", (e) => {
        const b = e.target.closest("[data-act]"), row = e.target.closest("[data-cam]");
        if (b) {
          const id = +b.dataset.cam, a = b.dataset.act;
          act(async () => {
            if (a === "delete") { if (!confirm("Delete this camera? Its station zones are removed too.")) return; await api(`/cameras/${id}`, { method: "DELETE" }); if (camId === id) camId = null; await reloadConfig(); }
            else await api(`/cameras/${id}/${a}`, { method: "POST" });
            zoneStation = null; points = []; await this.load(true);
          }, a === "recalibrate" ? "Learning the empty scene again - keep the line clear for a few seconds" : undefined);
        } else if (row) { camId = +row.dataset.cam; zoneStation = null; points = []; this.render(); }
      });
      $("#cam-zone").addEventListener("click", (e) => {
        const z = e.target.closest("[data-zone]"), u = e.target.closest("[data-unzone]");
        if (z) { zoneStation = z.dataset.zone; points = []; this.render(); }
        else if (u) act(async () => { await api(`/stations/${u.dataset.unzone}`, { method: "PATCH", body: { clear: ["zone", "camera_id"] } }); await reloadConfig(); await this.load(true); }, "Zone removed");
        else if (e.target.id === "cz-undo") { points.pop(); draw(); }
        else if (e.target.id === "cz-clear") { points = []; draw(); }
        else if (e.target.id === "cz-cancel") { zoneStation = null; points = []; this.render(); }
        else if (e.target.id === "cz-save") act(async () => {
          if (points.length < 3) throw new Error("Click at least 3 points to outline the zone.");
          await api(`/stations/${zoneStation}`, { method: "PATCH", body: { camera_id: camId, zone: points } });
          zoneStation = null; points = []; await reloadConfig(); await this.load(true);
        }, "Zone saved - the camera picks it up within a few seconds");
      });
    },
    render() {
      const list = MPI.S.camList || [];
      const cam = list.find((c) => c.id === camId);
      $("#cam-live-title").textContent = cam ? `Live view · ${cam.name}` : "Live view";
      liveView(cam); zonePanel(cam);
    },
    async load(quiet) {
      const cams = await api("/cameras");
      MPI.S.camList = cams;
      $("#ca-line").innerHTML = '<option value="">(none)</option>' + MPI.S.cfg.lines.map((l) => `<option value="${l.id}">${esc(l.name)}</option>`).join("");
      if (camId == null && cams.length) camId = cams[0].id;
      $("#cam-list").innerHTML = cams.length ? cams.map((c) => {
        const s = c.status, run = s.running;
        return `<div class="step" data-cam="${c.id}" style="flex-wrap:wrap;cursor:pointer;${c.id === camId ? "border-color:var(--accent);background:var(--accent-soft)" : ""}">
          <span style="flex:1;min-width:180px"><b>${esc(c.name)}</b> ${camState(c)}<div class="muted small" style="word-break:break-all">${esc(c.source)} · ${esc(c.detector)}${c.decode_qr ? " · QR" : ""}</div>
          ${run ? `<div class="muted small">${s.fps} fps · ${s.objects} in view · ${fmtNum(s.events_sent)} events sent${s.events_refused ? ` (${s.events_refused} refused)` : ""} · ${s.zones} zone(s)${s.last_event ? ` · last: ${esc(s.last_event.type)} ${fmtTime(s.last_event.ts)} ${esc(s.last_event.result)}` : ""}</div>` : ""}
          ${s.error ? `<div class="small" style="color:var(--crit)">${esc(s.error)}</div>` : ""}</span>
          <span class="row">${run ? `<button class="btn small" data-cam="${c.id}" data-act="stop">Stop</button><button class="btn small" data-cam="${c.id}" data-act="recalibrate" title="Re-learn the empty scene">Recalibrate</button>` : `<button class="btn small primary" data-cam="${c.id}" data-act="start">Start</button>`}
          <button class="btn-link danger" data-cam="${c.id}" data-act="delete">Delete</button></span></div>`;
      }).join("") : empty("No cameras yet.");
      const cam = cams.find((c) => c.id === camId);
      if (!quiet || !$("#cam-img")) this.render();
      else if (cam) { const wasRunning = $("#cam-img") && $("#cam-img").src.includes("live.mjpg"); if (wasRunning !== cam.status.running && !zoneStation) this.render(); else if (!zoneStation) draw(); }
    },
    stop() { zoneStation = null; points = []; },
  });

  /* ---------------------------------------------------------------------- setup */
  let tab = "factory", routeDraft = [];
  const num = (id) => ($(id).value === "" ? null : +$(id).value);
  const stationsOf = (line) => MPI.S.cfg.stations.filter((s) => String(s.line_id) === String(line));

  function factoryTab() {
    const c = MPI.S.cfg, f = c.factory;
    return `<div class="card"><div class="card-head"><span>Factory and shifts</span></div>
      <div class="form-grid"><label class="field">Factory name<input id="fa-name" value="${esc(f.name)}"></label></div>
      <p class="note" style="margin-top:14px">Shifts are used for the "current shift" / "previous shift" periods and the by-shift breakdown. A shift may run past midnight.</p>
      <div class="stack" id="fa-shifts">${f.shifts.map((s, i) => shiftRow(s, i)).join("")}</div>
      <div class="row" style="margin-top:12px"><button class="btn" id="fa-addshift">Add shift</button><button class="btn primary" id="fa-save">Save</button></div></div>
      <div class="card"><div class="card-head"><span>Production lines</span></div>
      <table class="data"><thead><tr><th>Line</th><th>Stations</th><th></th></tr></thead><tbody>${c.lines.map((l) => `<tr><td><b>${esc(l.name)}</b></td><td>${stationsOf(l.id).length}</td><td><button class="btn-link" data-rename="${l.id}">Rename</button></td></tr>`).join("")}</tbody></table>
      <div class="row" style="margin-top:12px"><input id="ln-name" placeholder="New line name"><button class="btn" id="ln-add">Add line</button></div></div>`;
  }
  const shiftRow = (s, i) => `<div class="step" data-shift="${i}"><input class="sh-name" value="${esc(s.name)}" placeholder="Name" style="width:150px"><input class="sh-start" type="time" value="${esc(s.start)}"> <span class="muted">to</span> <input class="sh-end" type="time" value="${esc(s.end)}"><button class="btn-link danger" data-delshift="${i}" style="margin-left:auto">Remove</button></div>`;

  function productsTab() {
    const c = MPI.S.cfg;
    return `<div class="card"><div class="card-head"><span>Products</span></div>
      <table class="data"><thead><tr><th>Code</th><th>Name</th><th class="num">Target production time (min)</th><th></th></tr></thead><tbody>
      ${c.products.map((p) => `<tr><td><b>${esc(p.code)}</b></td><td>${esc(p.name)}</td><td class="num"><input type="number" min="1" step="1" value="${p.target_cycle_min ?? ""}" data-target="${p.id}"></td><td><button class="btn small" data-savep="${p.id}">Save</button></td></tr>`).join("")}</tbody></table>
      <div class="form-grid" style="margin-top:14px"><label class="field">Code<input id="pr-code" placeholder="GEAR"></label><label class="field">Name<input id="pr-name" placeholder="Metal Gear"></label>
      <label class="field">Target time (min)<input id="pr-target" type="number" min="1"></label><button class="btn primary" id="pr-add">Add product</button></div></div>
      <div class="card"><div class="card-head"><span>Production routes</span></div>
      <p class="note">A route is the ordered list of stations a product passes through: it starts at a <b>source</b> station, ends at a <b>sink</b> and has at least one <b>process</b> station between them. A route with production history can't be changed.</p>
      <table class="data"><thead><tr><th>Line</th><th>Product</th><th>Route</th></tr></thead><tbody>
      ${c.routes.map((r) => `<tr><td>${esc((c.lines.find((l) => l.id === r.line_id) || {}).name)}</td><td><b>${esc(r.product_code)}</b></td><td>${r.steps.map((s) => esc(s.station_name)).join(' <span class="muted">→</span> ')}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">No routes yet.</td></tr>`}</tbody></table>
      <div class="form-grid" style="margin-top:14px"><label class="field">Line<select id="rt-line">${c.lines.map((l) => `<option value="${l.id}">${esc(l.name)}</option>`).join("")}</select></label>
      <label class="field">Product<select id="rt-prod">${c.products.map((p) => `<option value="${p.id}">${esc(p.code)}</option>`).join("")}</select></label></div>
      <div class="split" style="margin-top:12px"><div><div class="small muted" style="margin-bottom:6px">Stations on this line (click to add)</div><div class="chips" id="rt-avail"></div></div>
      <div><div class="small muted" style="margin-bottom:6px">Route (in order)</div><div class="steps" id="rt-steps"></div><div class="row" style="margin-top:10px"><button class="btn primary" id="rt-save">Save route</button><button class="btn" id="rt-clear">Clear</button></div></div></div></div>`;
  }
  function routeUI() {
    const line = $("#rt-line") && $("#rt-line").value;
    if (!line) return;
    const sts = stationsOf(line), by = Object.fromEntries(sts.map((s) => [s.id, s]));
    $("#rt-avail").innerHTML = sts.filter((s) => !routeDraft.includes(s.id)).map((s) => `<span class="chip" data-add="${s.id}">${esc(s.name)} <span class="muted">${s.kind}</span></span>`).join("") || '<span class="muted small">all added</span>';
    $("#rt-steps").innerHTML = routeDraft.map((id, i) => `<div class="step"><span class="idx">${i + 1}</span><span style="flex:1">${esc(by[id].name)} <span class="muted small">${by[id].kind}</span></span>
      <button class="btn-link" data-up="${i}">↑</button><button class="btn-link" data-down="${i}">↓</button><button class="btn-link danger" data-rm="${i}">✕</button></div>`).join("") || '<span class="muted small">empty</span>';
  }

  function stationsTab() {
    const c = MPI.S.cfg, line = MPI.S.setupLine || (c.lines[0] && c.lines[0].id);
    const sts = stationsOf(line);
    const inp = (s, k, step = "any", min = 0) => `<input type="number" step="${step}" min="${min}" value="${s[k] ?? ""}" data-f="${k}" style="width:82px">`;
    return `<div class="card"><div class="card-head"><span>Stations and thresholds</span><select id="st-line">${c.lines.map((l) => `<option value="${l.id}" ${String(l.id) === String(line) ? "selected" : ""}>${esc(l.name)}</option>`).join("")}</select></div>
      <p class="note">Thresholds drive the alerts and the status shown on the flow. <b>Machines</b> is how many units the station can work on at once (used for utilisation).</p>
      <div class="scroll-x"><table class="data"><thead><tr><th>Station</th><th>Type</th><th>Expected process (min)</th><th>Expected queue</th><th>Target / hour</th><th>Reject alert (%)</th><th>Idle alert (min)</th><th>Machines</th><th></th></tr></thead><tbody>
      ${sts.map((s) => s.kind === "process" ? `<tr data-sid="${s.id}"><td><b>${esc(s.name)}</b></td><td>${s.kind}</td><td>${inp(s, "expected_process_min")}</td><td>${inp(s, "expected_wait_qty", 1)}</td><td>${inp(s, "target_units_per_h")}</td><td>${inp(s, "reject_threshold_pct")}</td><td>${inp(s, "idle_alert_min")}</td><td>${inp(s, "capacity", 1, 1)}</td><td><button class="btn small" data-savest="${s.id}">Save</button></td></tr>`
        : `<tr><td><b>${esc(s.name)}</b></td><td>${s.kind}</td><td colspan="7" class="muted">${s.kind === "source" ? "Raw material enters here." : "Finished units are counted here."}</td></tr>`).join("")}</tbody></table></div>
      <div class="form-grid" style="margin-top:14px"><label class="field">New station<input id="ns-name"></label><label class="field">Type<select id="ns-kind"><option value="process">process</option><option value="source">source</option><option value="sink">sink</option></select></label>
      <label class="field">Expected process (min)<input id="ns-exp" type="number" min="1"></label><button class="btn primary" id="ns-add">Add station</button></div></div>`;
  }

  function integrationTab() {
    const c = MPI.S.cfg, line = c.lines[0], sts = line ? stationsOf(line.id) : [], proc = sts.find((s) => s.kind === "process");
    const base = location.origin;
    return `<div class="card"><div class="card-head"><span>Send events from other systems</span></div>
      <p class="note">Barcode scanners, PLCs, IoT gateways or any script can post the same events cameras produce. <code>type</code> is <code>issue</code>, <code>enter</code>, <code>exit</code> or <code>reject</code>. Leave <code>unit_code</code> out if the source can't identify the unit; the line then matches the oldest waiting unit (FIFO).</p>
      <p class="note">From this machine no key is needed. From another machine set <code>MPI_API_KEY</code> on the server and send it as the <code>X-API-Key</code> header.</p>
      <div class="small muted">One event</div><pre class="code">curl -X POST ${esc(base)}/api/ingest/event -H "Content-Type: application/json" -H "X-API-Key: YOUR_KEY" \\
  -d '{"type":"enter","station_id":${proc ? proc.id : 1},"unit_code":"GEAR-000125"}'</pre>
      <div class="small muted" style="margin-top:10px">By name, with a timestamp (epoch seconds)</div><pre class="code">curl -X POST ${esc(base)}/api/ingest/event -H "Content-Type: application/json" \\
  -d '{"type":"reject","line_id":${line ? line.id : 1},"station":"${proc ? esc(proc.name) : "Drilling"}","unit_code":"GEAR-000125","reason":"surface_defect","ts":${Math.floor(Date.now() / 1000)}}'</pre>
      <div class="small muted" style="margin-top:10px">Up to 500 events per request</div><pre class="code">POST ${esc(base)}/api/ingest/events     [ {...}, {...} ]</pre>
      <p class="note" style="margin-top:12px">Every event - accepted or not - is kept in the audit log with the reason it was placed or refused (Operator → Recent events).
      Reject reasons: ${c.reject_reasons.map((r) => `<code>${r}</code>`).join(" ")}.</p></div>`;
  }

  MPI.register("setup", {
    title: "Setup", filters: [],
    init(root) {
      root.innerHTML = `<div class="tabs" id="su-tabs">${[["factory", "Factory & lines"], ["products", "Products & routes"], ["stations", "Stations"], ["integration", "Integration"]].map(([k, n]) => `<button class="tab" data-tab="${k}">${n}</button>`).join("")}</div><div id="su-body" class="stack"></div>`;
      $("#su-tabs").addEventListener("click", (e) => { const t = e.target.closest("[data-tab]"); if (t) { tab = t.dataset.tab; this.render(); } });
      $("#su-body").addEventListener("click", (e) => this.click(e));
      $("#su-body").addEventListener("change", (e) => {
        if (e.target.id === "st-line") { MPI.S.setupLine = e.target.value; this.render(); }
        if (e.target.id === "rt-line" || e.target.id === "rt-prod") { routeDraft = []; routeUI(); }
      });
    },
    render() {
      $$("#su-tabs .tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
      $("#su-body").innerHTML = { factory: factoryTab, products: productsTab, stations: stationsTab, integration: integrationTab }[tab]();
      if (tab === "products") routeUI();
    },
    async load() { this.render(); },
    async refresh() { await reloadConfig(); this.render(); },
    click(e) {
      const t = e.target, d = (k) => t.closest(`[data-${k}]`);
      const save = (fn, msg) => act(async () => { await fn(); await this.refresh(); }, msg);
      if (t.id === "fa-addshift") { $("#fa-shifts").insertAdjacentHTML("beforeend", shiftRow({ name: "", start: "06:00", end: "14:00" }, 99)); }
      else if (d("delshift")) d("delshift").closest(".step").remove();
      else if (t.id === "fa-save") save(async () => {
        const shifts = $$("#fa-shifts .step").map((r) => ({ name: $(".sh-name", r).value.trim(), start: $(".sh-start", r).value, end: $(".sh-end", r).value }));
        await api("/factory", { method: "PUT", body: { name: $("#fa-name").value, shifts } });
      }, "Saved");
      else if (t.id === "ln-add") save(async () => { await api("/lines", { method: "POST", body: { name: $("#ln-name").value } }); }, "Line added");
      else if (d("rename")) { const n = prompt("New name for the line:"); if (n) save(async () => { await api(`/lines/${d("rename").dataset.rename}`, { method: "PATCH", body: { name: n } }); }, "Renamed"); }
      else if (t.id === "pr-add") save(async () => { await api("/products", { method: "POST", body: { code: $("#pr-code").value, name: $("#pr-name").value, target_cycle_min: num("#pr-target") } }); }, "Product added");
      else if (d("savep")) { const id = d("savep").dataset.savep, v = $(`[data-target="${id}"]`).value; save(async () => { await api(`/products/${id}`, { method: "PATCH", body: v === "" ? { clear_target: true } : { target_cycle_min: +v } }); }, "Saved"); }
      else if (d("add")) { routeDraft.push(+d("add").dataset.add); routeUI(); }
      else if (d("rm")) { routeDraft.splice(+d("rm").dataset.rm, 1); routeUI(); }
      else if (d("up")) { const i = +d("up").dataset.up; if (i > 0) [routeDraft[i - 1], routeDraft[i]] = [routeDraft[i], routeDraft[i - 1]]; routeUI(); }
      else if (d("down")) { const i = +d("down").dataset.down; if (i < routeDraft.length - 1) [routeDraft[i + 1], routeDraft[i]] = [routeDraft[i], routeDraft[i + 1]]; routeUI(); }
      else if (t.id === "rt-clear") { routeDraft = []; routeUI(); }
      else if (t.id === "rt-save") save(async () => { await api("/routes", { method: "PUT", body: { line_id: +$("#rt-line").value, product_id: +$("#rt-prod").value, station_ids: routeDraft } }); routeDraft = []; }, "Route saved");
      else if (d("savest")) {
        const row = d("savest").closest("tr"), body = {};
        $$("[data-f]", row).forEach((i) => { if (i.value !== "") body[i.dataset.f] = +i.value; });
        save(async () => { await api(`/stations/${row.dataset.sid}`, { method: "PATCH", body }); }, "Saved");
      }
      else if (t.id === "ns-add") save(async () => { await api("/stations", { method: "POST", body: { line_id: +$("#st-line").value, name: $("#ns-name").value, kind: $("#ns-kind").value, expected_process_min: num("#ns-exp") } }); }, "Station added");
    },
  });
})();
