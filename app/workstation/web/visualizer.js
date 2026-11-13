/* Live model and action visualizer.
 *
 * Everything drawn here comes from telemetry: router selections and weights,
 * verify cycles, draft acceptance, n-gram (PLE) disk reads, context use and
 * tool calls. No token text, reasoning or tool arguments are ever shown.
 * The memory and storage regions follow what the runtime really does:
 * experts, trunk, MTP block and projector are resident in unified memory;
 * only the PLE table is read from disk during inference. */
"use strict";
(function () {
  const D = window.DwarfStar, S = D.S;
  const EXPERTS = 512, GRID_COLS = 32, GRID_ROWS = 16;
  const V = {
    mounted: false, root: null, canvas: null, g: null, frame: 0, width: 0, height: 0,
    mode: "overview", focusLayer: 24, paused: false, playhead: -1,
    hits: [], ticks: [], tools: [], requests: [], heat: null, heatLayers: 48, heatMax: 1,
    pills: [], drafts: [], reads: [], lastTickAt: 0, replay: null, toolPulse: {}, activeTool: null,
  };
  const COLORS = { text: "#dbe3ee", muted: "#8593a6", faint: "#42505f", line: "#232c3a", panel: "#10151d", accent: "#5cc8ff", good: "#4fd695", warn: "#ffc857", bad: "#ff6b6b", ple: "#ffb454", mtp: "#7fd6a3", cyber: "#b98cff" };
  const TOOL_NODES = [["workspace", "Workspace", "files"], ["shell", "Shell", "commands"], ["browser", "Browser", "pages"], ["gui", "Computer use", "KimiCU"], ["vision", "Vision", "images"]];

  function tone() { const m = D.runningModel() || S.model; return { scope: "#5cc8ff", swift: "#ffb454", cyber: "#b98cff" }[m] || COLORS.accent; }
  function model() { const id = (V.replay && V.replay.model) || D.runningModel() || S.model; return D.modelOf(id); }
  /* What the model's backend reports. Activity a backend does not report is labelled as such, never drawn from guesses. */
  function caps() { return (model().backend || {}).telemetry || { router: true, ple: true, mtp: true, requests: true }; }
  function backendName() { return "Splash-m1"; }
  function notReported(x, y, w, h, what) {
    const g = V.g; g.fillStyle = "rgba(16,21,29,.86)"; g.fillRect(x, y, w, h);
    label(what + " not reported by the " + backendName() + " backend", x + w / 2, y + h / 2 - 14, COLORS.muted, "600 12px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "center", w - 20);
    label("Nothing is drawn rather than estimated. Live rates, context and memory still come from the runtime.", x + w / 2, y + h / 2 + 4, COLORS.faint, "10.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "center", w - 20);
  }

  function mount() {
    if (V.mounted) { resize(); return; }
    V.root = document.querySelector("#view-visualizer");
    V.root.innerHTML = `
      <h1>Visualizer</h1>
      <p class="lead">A live picture of the running model: where its data lives, which experts the router picks for each token, what the speculative head drafts, and what the agent's tools are doing.</p>
      <div class="row" style="margin-bottom:10px">
        <div class="chips" id="viz-modes">
          <button class="chip selected" data-mode="overview">Overview</button>
          <button class="chip" data-mode="layer">Layer focus</button>
          <button class="chip" data-mode="heatmap">Routing heatmap</button>
        </div>
        <label class="small muted" id="viz-layer-wrap" style="display:none">Layer <input type="range" id="viz-layer" min="0" max="47" value="24" style="vertical-align:middle;width:160px"> <b id="viz-layer-n">24</b></label>
        <span class="spacer"></span>
        <select id="viz-session" aria-label="Session"><option value="">Live</option></select>
        <button class="ghost" id="viz-pause">Pause</button>
      </div>
      <canvas id="viz-canvas" style="height:600px" aria-label="Model visualizer"></canvas>
      <div class="row" style="margin-top:8px"><span class="small muted" style="width:70px">Timeline</span>
        <input type="range" id="viz-scrub" min="0" max="0" value="0" style="flex:1" aria-label="Scrub recent events">
        <span class="small mono" id="viz-clock" style="width:190px;text-align:right">live</span></div>
      <div class="grid cols-2" style="margin-top:12px">
        <div class="panel"><h2>Action timeline</h2><div class="timeline" id="viz-actions"></div></div>
        <div class="panel"><h2>What this shows</h2><div class="small muted" id="viz-facts"></div></div>
      </div>
      <div class="panel" style="margin-top:12px"><h2>Requests</h2><div id="viz-requests" class="small"></div></div>`;
    V.canvas = V.root.querySelector("#viz-canvas");
    V.g = V.canvas.getContext("2d");
    V.root.querySelectorAll("[data-mode]").forEach((b) => b.onclick = () => {
      V.mode = b.dataset.mode;
      V.root.querySelectorAll("[data-mode]").forEach((x) => x.classList.toggle("selected", x === b));
      V.root.querySelector("#viz-layer-wrap").style.display = V.mode === "layer" ? "" : "none";
    });
    const layer = V.root.querySelector("#viz-layer");
    layer.oninput = () => { V.focusLayer = +layer.value; V.root.querySelector("#viz-layer-n").textContent = layer.value; };
    V.root.querySelector("#viz-pause").onclick = () => setPaused(!V.paused);
    const scrub = V.root.querySelector("#viz-scrub");
    scrub.oninput = () => { setPaused(true); V.playhead = +scrub.value; };
    V.root.querySelector("#viz-session").onchange = (e) => loadSession(e.target.value);
    V.canvas.onclick = (event) => pickLayer(event);
    V.canvas.onmousemove = (event) => {
      const rect = V.canvas.getBoundingClientRect(), px = event.clientX - rect.left, py = event.clientY - rect.top;
      const hit = (V.hits || []).find((h) => px >= h.x && px <= h.x + h.w && py >= h.y && py <= h.y + h.h);
      V.canvas.title = hit ? hit.text : "";
    };
    new ResizeObserver(resize).observe(V.canvas);
    S.listeners.push(onEvent);
    V.mounted = true;
    resize();
    refreshSessions();
    D.api("/api/heat").then((r) => { if (r.ok && r.layers.length) seedHeat(r.layers); });
    D.api("/api/recent").then((r) => { if (r.ok) r.events.forEach((e) => onEvent(e, true)); });
    loop();
  }
  function unmount() { /* keep state; the loop idles when the view is hidden */ }
  function setPaused(paused) {
    V.paused = paused;
    if (!paused) { V.playhead = -1; if (V.replay) { V.replay = null; V.root.querySelector("#viz-session").value = ""; resetBuffers(); } }
    V.root.querySelector("#viz-pause").textContent = paused ? "Resume live" : "Pause";
  }
  function resetBuffers() { V.ticks = []; V.tools = []; V.requests = []; V.heat = null; V.heatMax = 1; V.pills = []; V.drafts = []; }
  async function refreshSessions() {
    const r = await D.api("/api/sessions"); if (!r.ok) return;
    const select = V.root.querySelector("#viz-session");
    select.innerHTML = `<option value="">Live</option>` + r.sessions.slice(0, 20).map((s) => `<option value="${D.esc(s.id)}">Replay ${D.esc(s.id)}</option>`).join("");
  }
  async function loadSession(id) {
    if (!id) { setPaused(false); return; }
    const r = await D.api("/api/sessions/" + encodeURIComponent(id)); if (!r.ok) { D.toast(r.error); return; }
    resetBuffers();
    V.replay = { id, model: (r.events.find((e) => e.model) || {}).model };
    r.events.forEach((e) => onEvent(e, true));
    setPaused(true); V.playhead = 0;
  }
  function seedHeat(layers) {
    V.heatLayers = Math.max(48, layers.length);
    V.heat = new Uint32Array(64 * EXPERTS);
    layers.forEach((row, l) => row.forEach((count, e) => { V.heat[l * EXPERTS + e] = count; if (count > V.heatMax) V.heatMax = count; }));
  }
  function onEvent(event, silent) {
    if (!V.mounted) return;
    if (V.replay && !silent) return;                       // while replaying, ignore live events
    if (event.kind === "tick") {
      V.ticks.push(event); if (V.ticks.length > 1500) V.ticks.shift();
      if (event.r) {
        if (!V.heat) V.heat = new Uint32Array(64 * EXPERTS);
        event.r.forEach((row, l) => { for (let i = 0; i < row.length; i += 2) { const v = ++V.heat[l * EXPERTS + row[i]]; if (v > V.heatMax) V.heatMax = v; } });
      }
      if (!silent && !V.paused) animateTick(event);
    } else if (event.kind === "tool") {
      if (event.phase === "start") V.activeTool = event; else { if (V.activeTool && V.activeTool.call === event.call) V.activeTool = null; V.tools.push(event); if (V.tools.length > 300) V.tools.shift(); }
      V.toolPulse[event.category] = performance.now();
      renderActions();
    } else if (event.kind === "request") {
      V.requests.push(event); if (V.requests.length > 400) V.requests.shift();
      if (event.phase === "start" || event.phase === "finish") renderRequests();
    } else if (event.kind === "vision") { V.toolPulse.vision = performance.now(); }
  }
  function animateTick(tick) {
    const now = performance.now();
    V.lastTickAt = now;
    V.pills.push({ t0: now, n: tick.n });
    for (let i = 0; i < tick.dp; i++) V.drafts.push({ t0: now + i * 40, ok: i < tick.da });
    if (tick.ple && tick.ple[1] > 0) V.reads.push({ t0: now, bytes: tick.ple[1] });
    V.pills = V.pills.filter((p) => now - p.t0 < 1400); V.drafts = V.drafts.filter((d) => now - d.t0 < 1500); V.reads = V.reads.filter((r) => now - r.t0 < 900);
  }
  function resize() {
    if (!V.canvas) return;
    const ratio = window.devicePixelRatio || 1;
    V.width = V.canvas.clientWidth; V.height = V.canvas.clientHeight;
    V.canvas.width = Math.round(V.width * ratio); V.canvas.height = Math.round(V.height * ratio);
    V.g.setTransform(ratio, 0, 0, ratio, 0, 0);
  }
  function loop() {
    V.frame = requestAnimationFrame(loop);
    if (S.view !== "visualizer" || document.hidden) return;
    draw();
  }

  /* -------------------------------------------------------------- drawing */
  function box(x, y, w, h, title, sub, stroke) {
    const g = V.g;
    g.fillStyle = COLORS.panel; g.strokeStyle = stroke || COLORS.line; g.lineWidth = 1;
    g.beginPath(); g.roundRect(x, y, w, h, 9); g.fill(); g.stroke();
    g.fillStyle = COLORS.muted; g.font = "600 10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif"; g.textBaseline = "top"; g.textAlign = "left";
    g.fillText(title.toUpperCase(), x + 10, y + 8);
    if (sub) { const sx = x + 10 + g.measureText(title.toUpperCase()).width + 16; g.fillStyle = COLORS.faint; g.font = "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif"; g.fillText(fit(sub, x + w - 10 - sx), sx, y + 8); }
  }
  /* Text never leaves its region: anything wider than maxWidth is shortened with an ellipsis, and the full
   * value stays available as the canvas tooltip for that region (see hint()). */
  function fit(text, maxWidth) {
    const g = V.g; text = String(text);
    if (!maxWidth || g.measureText(text).width <= maxWidth) return text;
    let lo = 0, hi = text.length;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (g.measureText(text.slice(0, mid) + "…").width <= maxWidth) lo = mid; else hi = mid - 1; }
    return text.slice(0, lo) + "…";
  }
  function label(text, x, y, color, font, align, maxWidth) {
    const g = V.g; g.fillStyle = color || COLORS.text; g.font = font || "11px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif"; g.textAlign = align || "left"; g.textBaseline = "top";
    g.fillText(fit(text, maxWidth), x, y);
  }
  function hint(x, y, w, h, text) { V.hits.push({ x, y, w, h, text }); }
  function currentTick() {
    if (!V.ticks.length) return null;
    if (V.paused && V.playhead >= 0) return V.ticks[Math.min(V.playhead, V.ticks.length - 1)];
    return V.ticks[V.ticks.length - 1];
  }
  function draw() {
    const g = V.g, W = V.width, H = V.height, m = model(), now = performance.now();
    g.clearRect(0, 0, W, H);
    V.hits = [];
    if (!m) return;
    const tick = currentTick();
    const scrub = V.root.querySelector("#viz-scrub");
    scrub.max = Math.max(0, V.ticks.length - 1);
    if (!V.paused) scrub.value = scrub.max;
    V.root.querySelector("#viz-clock").textContent = tick ? (V.paused ? (V.replay ? "replay · " : "paused · ") : "live · ") + new Date(tick.t * 1000).toLocaleTimeString() + " · token " + tick.pos : (D.runningModel() ? "waiting for tokens" : "no model running");
    const live = S.live || {}, active = !V.paused && now - V.lastTickAt < 600;
    const colL = 14, wL = Math.max(190, W * 0.17), colM = colL + wL + 14, wM = Math.max(170, W * 0.15), colR = colM + wM + 14, wR = W - colR - 14;
    const top = 12, bottomH = 118, mainH = H - top - bottomH - 22;
    drawStorage(colL, top, wL, mainH, m, live, now);
    drawMemory(colM, top, wM, mainH, m, live);
    drawProcessor(colR, top, wR, mainH, m, tick, active, now);
    drawBottom(colL, top + mainH + 12, W - 28, bottomH, m, live, tick, now);
    // disk -> memory read pulses (PLE rows)
    const y = top + 92;
    V.reads.forEach((r) => { const p = (now - r.t0) / 900; g.globalAlpha = 1 - p; g.fillStyle = COLORS.ple; g.beginPath(); g.arc(colL + wL + p * (colR - colL - wL + 20), y + Math.sin(p * 6) * 3, 2.5, 0, 6.3); g.fill(); });
    g.globalAlpha = 1;
    renderFacts(m, live);
  }
  function drawStorage(x, y, w, h, m, live, now) {
    const g = V.g, assets = S.catalog.details[m.id].assets;
    box(x, y, w, h, "Storage", "SSD");
    const reading = V.reads.length > 0;
    const pleY = y + 34, pleH = 96;
    g.fillStyle = reading ? "rgba(255,180,84,.16)" : "rgba(255,180,84,.07)"; g.strokeStyle = COLORS.ple; g.lineWidth = reading ? 1.6 : 1;
    g.beginPath(); g.roundRect(x + 10, pleY, w - 20, pleH, 7); g.fill(); g.stroke();
    const inner = w - 36;  // text width inside a card: 18 px inset on both sides
    label("PLE n-gram table", x + 18, pleY + 8, COLORS.ple, "600 11.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", inner);
    label(m.quantization.ple.split(",")[0] + " · " + (assets.ple.bytes / 2 ** 30).toFixed(1) + " GiB", x + 18, pleY + 25, COLORS.muted, undefined, "left", inner);
    label("stays on disk · read per token", x + 18, pleY + 41, COLORS.faint, "10.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", inner);
    hint(x + 10, pleY, w - 20, pleH, `PLE n-gram table: ${assets.ple.file} (${(assets.ple.bytes / 2 ** 30).toFixed(2)} GiB, ${m.quantization.ple})`);
    const ple = live.ple || {};
    if (!caps().ple) label("read rate not reported by " + backendName(), x + 18, pleY + 60, COLORS.muted, "10.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", inner);
    else label(((ple.bytes_per_s || 0) / 1024).toFixed(0) + " KiB/s", x + 18, pleY + 60, COLORS.text, "600 14px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", inner);
    if (caps().ple) label((ple.reads || 0).toLocaleString() + " row reads · " + ((ple.bytes || 0) / 2 ** 20).toFixed(1) + " MiB", x + 18, pleY + 78, COLORS.faint, "10.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", inner);
    let fy = pleY + pleH + 14;
    label("MODEL FILES · resident", x + 12, fy, COLORS.faint, "600 9.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", w - 24); fy += 16;
    [["main", "Model, part 1"], ["draft", "Draft model"], ["vision", "Vision projector"]].forEach(([k, name]) => {
      if (fy + 44 > y + h - 40) return;  // never draw below the panel
      g.fillStyle = "#0c1017"; g.strokeStyle = COLORS.line; g.lineWidth = 1; g.beginPath(); g.roundRect(x + 10, fy, w - 20, 44, 6); g.fill(); g.stroke();
      label(name + " · " + (assets[k].bytes / 2 ** 30).toFixed(2) + " GiB", x + 18, fy + 5, COLORS.text, "11px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", inner);
      label(assets[k].file, x + 18, fy + 20, COLORS.faint, "10px ui-monospace, Menlo, monospace", "left", inner);
      hint(x + 10, fy, w - 20, 44, `${name}: ${assets[k].file} · ${(assets[k].bytes / 2 ** 30).toFixed(2)} GiB · SHA-256 ${assets[k].sha256}`);
      fy += 50;
    });
    const kv = live.kv || {};
    label("KV CHECKPOINTS · disk cache", x + 12, fy + 6, COLORS.faint, "600 9.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", w - 24);
    label((kv.stores || 0) + " stored · " + (kv.hits || 0) + " restored", x + 12, fy + 22, COLORS.muted, undefined, "left", w - 24);
  }
  function drawMemory(x, y, w, h, m, live) {
    const g = V.g, mem = m.memory, plan = m.memory_plan, total = 64, limit = plan.metal_working_set_gib;
    box(x, y, w, h, "Unified memory", "64 GiB");
    const bx = x + 14, by = y + 34, bw = 46, bh = h - 50;
    g.fillStyle = "#0c1017"; g.strokeStyle = COLORS.line; g.beginPath(); g.roundRect(bx, by, bw, bh, 5); g.fill(); g.stroke();
    const used = (live.context && live.context.used) || 0, kvFilled = Math.min(mem.kv_gib, used * m.cache.kv_bytes_per_token / 2 ** 30);
    const expertsGib = mem.routed_experts_gib, trunk = Math.max(0, mem.resident_model_gib - expertsGib);
    const parts = [["Routed experts", expertsGib, tone()], ["Trunk + MTP", trunk, "#3f6fa0"], ["Vision projector", mem.projector_gib || 0, "#e58f8f"], ["KV cache (filled)", kvFilled, COLORS.good], ["KV cache (reserved)", mem.kv_gib - kvFilled, "#1f4a37"], ["Graph buffers", mem.buffers_gib, COLORS.warn]];
    let cy = by + bh, ly = by;
    parts.forEach(([name, gibs, color]) => {
      const ph = gibs / total * bh; cy -= ph;
      g.fillStyle = color; g.fillRect(bx + 1, cy, bw - 2, Math.max(0, ph - 1));
      g.fillStyle = color; g.fillRect(bx + bw + 12, ly + 3, 8, 8);
      const legendW = x + w - (bx + bw + 25) - 8;
      label(name, bx + bw + 25, ly, COLORS.text, "10.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", legendW);
      label(gibs.toFixed(2) + " GiB", bx + bw + 25, ly + 13, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", legendW);
      ly += 31;
    });
    // Reference lines are keyed in the legend, never labelled on top of it.
    const limitY = by + bh - limit / total * bh;
    g.strokeStyle = "#ff8a8a"; g.lineWidth = 1.5; g.setLineDash([5, 3]); g.beginPath(); g.moveTo(bx - 5, limitY); g.lineTo(bx + bw + 5, limitY); g.stroke(); g.setLineDash([]); g.lineWidth = 1;
    const lx = bx + bw + 12, legendW = x + w - lx - 8;
    ly += 6;
    g.fillStyle = "rgba(255,107,107,.12)"; g.beginPath(); g.roundRect(lx - 4, ly - 4, legendW + 4, 48, 5); g.fill();
    g.strokeStyle = "#ff8a8a"; g.lineWidth = 1.5; g.setLineDash([5, 3]); g.beginPath(); g.moveTo(lx, ly + 7); g.lineTo(lx + 9, ly + 7); g.stroke(); g.setLineDash([]); g.lineWidth = 1;
    label("Metal limit", lx + 13, ly, "#ffb0b0", "600 11px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", legendW - 13);
    label(limit.toFixed(2) + " GiB", lx + 13, ly + 14, "#ffd6d6", "600 11px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", legendW - 13);
    label("plan " + plan.planned_gib.toFixed(2) + " GiB", lx + 13, ly + 28, "#d8b3b3", "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", legendW - 13);
    hint(lx - 4, ly - 4, legendW + 4, 48, `Metal recommended working set ${limit.toFixed(2)} GiB (${plan.metal_working_set_source}); this model plans ${plan.planned_gib.toFixed(2)} GiB, ${plan.metal_headroom_gib.toFixed(2)} GiB headroom`);
    const wired = S.system && S.system.memory && S.system.memory.wired_bytes;
    if (wired) {
      const wy = by + bh - wired / 2 ** 30 / total * bh;
      g.strokeStyle = COLORS.text; g.beginPath(); g.moveTo(bx - 5, wy); g.lineTo(bx + bw + 5, wy); g.stroke();
      ly += 54;
      g.strokeStyle = COLORS.text; g.beginPath(); g.moveTo(lx, ly + 7); g.lineTo(lx + 9, ly + 7); g.stroke();
      label("Wired now", lx + 13, ly, COLORS.text, "600 11px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", legendW - 13);
      label((wired / 2 ** 30).toFixed(1) + " GiB, whole machine", lx + 13, ly + 14, COLORS.muted, "10.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", legendW - 13);
    }
    label("Context " + used.toLocaleString() + " / " + m.context_label + " · KV " + m.cache.k_cache_dtype + "/" + m.cache.v_cache_dtype, x + 12, y + h - 16, COLORS.muted, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", w - 24);
  }
  function drawProcessor(x, y, w, h, m, tick, active, now) {
    const g = V.g, layers = 48;
    box(x, y, w, h, "Processor (GPU)", m.display_name.replace("DwarfStar ", "") + " · 48 layers · 512 experts per layer, " + (tick && tick.k || 10) + " active per token", active ? tone() : COLORS.line);
    const px = x + 12, py = y + 30, pw = w - 24, ph = h - 42;
    if (!caps().router && V.mode !== "overview") return notReported(px, py, pw, ph, "Router selections are");
    if (V.mode === "heatmap") return drawHeat(px, py, pw, ph);
    if (V.mode === "layer") return drawLayer(px, py, pw, ph, tick);
    // overview: one row per layer, router dot + expert strip
    const mtpW = Math.min(150, pw * 0.2), stripX = px + 62, stripW = pw - 62 - mtpW - 16, rowH = ph / layers;
    const rows = tick && tick.r;
    const progress = Math.min(1, (now - V.lastTickAt) / 220);
    for (let l = 0; l < layers; l++) {
      const ry = py + l * rowH;
      if (l % 6 === 0) label("L" + l, px, ry - 1, COLORS.faint, "9px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
      g.fillStyle = "#0c1017"; g.fillRect(stripX, ry + 1, stripW, Math.max(1.5, rowH - 2));
      const lit = !V.paused ? l / layers <= progress : true;
      g.fillStyle = lit && rows ? tone() : COLORS.faint; g.beginPath(); g.arc(px + 44, ry + rowH / 2, 2.2, 0, 6.3); g.fill();
      if (rows && rows[l] && lit) {
        const row = rows[l];
        for (let i = 0; i < row.length; i += 2) {
          const ex = stripX + row[i] / EXPERTS * stripW, weight = row[i + 1] / 255;
          g.globalAlpha = 0.35 + 0.65 * Math.min(1, weight * 4); g.fillStyle = tone();
          g.fillRect(ex - 1, ry + 0.5, 3, Math.max(2, rowH - 1));
        }
        g.globalAlpha = 1;
      }
    }
    label("router", px + 30, py + ph + 1, COLORS.faint, "9px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
    label("expert 0", stripX, py + ph + 1, COLORS.faint, "9px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
    label("expert 511", stripX + stripW, py + ph + 1, COLORS.faint, "9px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "right");
    if (!caps().router) notReported(stripX - 62, py, stripW + 62, ph, "Router selections are");
    else if (!rows) label(D.runningModel() ? "Waiting for the next token. Router selections appear while the model generates." : "Start a model to see routing.", stripX + 10, py + ph / 2 - 6, COLORS.muted, "12px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
    // token pill travelling down the stack
    V.pills.forEach((p) => { const t = (now - p.t0) / 260; if (t > 1) return; g.fillStyle = COLORS.text; g.beginPath(); g.roundRect(px + 14, py + t * (ph - 10), 20, 8, 4); g.fill(); });
    // MTP lane
    const mx = px + pw - mtpW, mw = mtpW;
    g.strokeStyle = COLORS.mtp; g.fillStyle = "rgba(127,214,163,.06)"; g.lineWidth = 1; g.beginPath(); g.roundRect(mx, py + 4, mw, ph - 8, 7); g.fill(); g.stroke();
    label(caps().mtp ? "MTP HEAD" : "SPECULATIVE DRAFT", mx + 10, py + 12, COLORS.mtp, "600 10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", mw - 20);
    label(caps().mtp ? "drafts next tokens;" : "a draft model proposes;", mx + 10, py + 28, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", mw - 20);
    label("trunk verifies them", mx + 10, py + 41, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", mw - 20);
    if (!caps().mtp) {
      label((model().backend || {}).speculative || "draft model", mx + 10, py + 64, COLORS.text, "600 11px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", mw - 20);
      label("per-cycle drafts not", mx + 10, py + 84, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", mw - 20);
      label("reported by " + backendName(), mx + 10, py + 97, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", mw - 20);
      return;
    }
    const mtp = (S.live && S.live.mtp) || {};
    label(mtp.acceptance_pct != null ? mtp.acceptance_pct.toFixed(1) + " %" : "–", mx + 10, py + 64, COLORS.text, "600 20px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
    label("accepted", mx + 10, py + 90, COLORS.muted, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
    label((mtp.accepted || 0) + " / " + (mtp.proposed || 0) + " drafts", mx + 10, py + 106, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
    label((mtp.rejected || 0) + " rejected", mx + 10, py + 120, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
    const cardBottom = py + ph - 4, inset = 10, mtw = mw - 2 * inset;
    if (tick && cardBottom - (py + 177) > 24) { label("this cycle", mx + inset, py + 148, COLORS.muted, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", mtw); label(tick.n + " token" + (tick.n === 1 ? "" : "s") + " · " + tick.da + "/" + tick.dp + " drafts", mx + inset, py + 162, COLORS.text, "11px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", mtw); label(tick.ms.toFixed(0) + " ms", mx + inset, py + 177, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", mtw); }
    // Draft pills move only inside the card: every pill keeps an inset from all four edges.
    const pillW = 18, pillH = 7, laneTop = py + 200, laneBottom = cardBottom - inset - pillH;
    if (laneBottom > laneTop) {
      const lanes = Math.max(1, Math.min(6, Math.floor((laneBottom - laneTop) / 14) + 1));
      V.drafts.forEach((d, i) => {
        const t = (now - d.t0) / 900; if (t < 0 || t > 1) return;
        const dy = laneTop + (i % lanes) * Math.min(14, (laneBottom - laneTop) / lanes);
        const px0 = mx + inset, travel = mw - 2 * inset - pillW;
        g.globalAlpha = d.ok ? 1 : 1 - t; g.fillStyle = d.ok ? COLORS.mtp : COLORS.bad; g.beginPath();
        g.roundRect(px0 + (d.ok ? t * travel : Math.min(20, travel) * t), d.ok ? dy + t * (laneBottom - dy) : dy, pillW, pillH, 3.5); g.fill(); g.globalAlpha = 1;
      });
    }
  }
  function drawLayer(x, y, w, h, tick) {
    const g = V.g, row = tick && tick.r && tick.r[V.focusLayer];
    const cell = Math.floor(Math.min((w - 250) / GRID_COLS, (h - 30) / GRID_ROWS)), gx = x + 4, gy = y + 22;
    label("Layer " + V.focusLayer + " · router output for the current token", x + 4, y, COLORS.muted, "11px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
    const chosen = new Map(); if (row) for (let i = 0; i < row.length; i += 2) chosen.set(row[i], row[i + 1] / 255);
    for (let e = 0; e < EXPERTS; e++) {
      const cx = gx + (e % GRID_COLS) * cell, cy = gy + Math.floor(e / GRID_COLS) * cell;
      const heat = V.heat ? V.heat[V.focusLayer * EXPERTS + e] / V.heatMax : 0;
      g.fillStyle = "rgba(92,200,255," + (0.05 + 0.5 * Math.sqrt(heat)) + ")"; g.fillRect(cx, cy, cell - 2, cell - 2);
      if (chosen.has(e)) { g.fillStyle = tone(); g.globalAlpha = 0.45 + 0.55 * Math.min(1, chosen.get(e) * 4); g.fillRect(cx - 1, cy - 1, cell, cell); g.globalAlpha = 1; g.strokeStyle = "#fff"; g.lineWidth = 1; g.strokeRect(cx - 0.5, cy - 0.5, cell - 1, cell - 1); }
    }
    const lx = gx + GRID_COLS * cell + 18; let ly = gy;
    label("SELECTED EXPERTS", lx, ly, COLORS.muted, "600 10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif"); ly += 18;
    if (row) for (let i = 0; i < row.length; i += 2) { const weight = row[i + 1] / 255; label("#" + row[i], lx, ly, COLORS.text, "11px ui-monospace, Menlo"); g.fillStyle = tone(); g.fillRect(lx + 44, ly + 3, weight * 150, 7); label((weight * 100).toFixed(0) + " %", lx + 44 + weight * 150 + 6, ly, COLORS.muted, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif"); ly += 17; }
    else label("No routing data yet.", lx, ly, COLORS.faint);
    label("Background shade: how often each expert was chosen this session.", gx, gy + GRID_ROWS * cell + 8, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
  }
  function drawHeat(x, y, w, h) {
    const g = V.g, layers = 48;
    label("How often each expert was selected this session (rows: layers 0 to 47, columns: experts 0 to 511). Click a row to focus that layer.", x + 4, y, COLORS.muted, "11px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
    const hy = y + 22, hh = h - 40, rowH = hh / layers, colW = w / EXPERTS;
    g.fillStyle = "#0c1017"; g.fillRect(x, hy, w, hh);
    if (!V.heat) { label("No routing data yet.", x + 10, hy + 10, COLORS.faint); return; }
    const max = Math.log1p(V.heatMax);
    for (let l = 0; l < layers; l++) for (let e = 0; e < EXPERTS; e++) {
      const v = V.heat[l * EXPERTS + e]; if (!v) continue;
      const a = Math.log1p(v) / max; g.fillStyle = "rgba(" + Math.round(60 + 195 * a) + "," + Math.round(120 + 80 * a) + ",255," + (0.2 + 0.8 * a) + ")";
      g.fillRect(x + e * colW, hy + l * rowH, Math.max(1, colW), Math.max(1, rowH - 0.5));
    }
    V.heatRect = { x, y: hy, w, h: hh, rowH };
  }
  function pickLayer(event) {
    if (V.mode !== "heatmap" || !V.heatRect) return;
    const rect = V.canvas.getBoundingClientRect(), py = event.clientY - rect.top, r = V.heatRect;
    if (py < r.y || py > r.y + r.h) return;
    V.focusLayer = Math.max(0, Math.min(47, Math.floor((py - r.y) / r.rowH)));
    V.root.querySelector("#viz-layer").value = V.focusLayer; V.root.querySelector("#viz-layer-n").textContent = V.focusLayer;
    V.root.querySelector('[data-mode="layer"]').click();
  }
  function drawBottom(x, y, w, h, m, live, tick, now) {
    const g = V.g, outW = w * 0.42;
    box(x, y, outW, h, "Output stream", "committed tokens (counts only)");
    const end = V.paused && V.playhead >= 0 ? V.playhead + 1 : V.ticks.length, fits = Math.max(1, Math.floor((outW - 24) / 8)), recent = V.ticks.slice(Math.max(0, end - fits), end);
    let px = x + 12; const py = y + 34;
    recent.forEach((t) => { for (let i = 0; i < t.n; i++) { g.fillStyle = i === 0 ? COLORS.text : COLORS.mtp; g.globalAlpha = 0.9; g.beginPath(); g.roundRect(px, py + (i ? 12 : 0), 6, 9, 2); g.fill(); } px += 8; if (t.dp > t.da) { g.fillStyle = COLORS.bad; g.fillRect(px - 8, py + 26, 6, 2); } });
    g.globalAlpha = 1;
    label("white: verified by the trunk · green: accepted MTP draft · red mark: rejected draft", x + 12, y + 70, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", outW - 24);
    const rates = live.rates || {}, request = live.request || {};
    label((rates.live_tps != null ? rates.live_tps.toFixed(1) : rates.decode_tps != null ? rates.decode_tps.toFixed(1) : "–") + " tok/s", x + 12, y + 88, COLORS.text, "600 15px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
    label((request.phase || "IDLE").replace("_", " ") + " · " + (request.generated || 0) + " generated · prompt " + (request.prompt_tokens || 0).toLocaleString(), x + 110, y + 92, COLORS.muted, "10.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", outW - 122);
    const tx = x + outW + 12, tw = w - outW - 12;
    box(tx, y, tw, h, "Agent actions", "tool calls leave the model and return a result");
    const nodeW = (tw - 24) / TOOL_NODES.length;
    TOOL_NODES.forEach(([key, name, sub], i) => {
      const nx = tx + 12 + i * nodeW, pulse = V.toolPulse[key] ? Math.max(0, 1 - (now - V.toolPulse[key]) / 1600) : 0;
      const running = V.activeTool && V.activeTool.category === key;
      const color = { workspace: "#5cc8ff", shell: "#4fd695", browser: "#ffb454", gui: "#b98cff", vision: "#ff6b6b" }[key];
      g.fillStyle = "rgba(255,255,255," + (0.02 + 0.1 * pulse) + ")"; g.strokeStyle = running || pulse > 0 ? color : COLORS.line; g.lineWidth = running ? 2 : 1;
      g.beginPath(); g.roundRect(nx, y + 30, nodeW - 10, h - 42, 7); g.fill(); g.stroke();
      label(name, nx + 9, y + 38, running || pulse > 0 ? color : COLORS.text, "600 11px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
      label(sub, nx + 9, y + 53, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
      const done = V.tools.filter((t) => t.category === key), last = done[done.length - 1];
      label(done.length + " call" + (done.length === 1 ? "" : "s"), nx + 9, y + 72, COLORS.muted, "10.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
      if (running) label("running…", nx + 9, y + 88, color, "10.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
      else if (last) label((last.ok === false ? "✗ " : "✓ ") + (last.ms != null ? last.ms + " ms" : ""), nx + 9, y + 88, last.ok === false ? COLORS.bad : COLORS.good, "10.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
    });
  }
  function renderActions() {
    const node = V.root && V.root.querySelector("#viz-actions"); if (!node) return;
    const rows = V.tools.slice(-60).reverse();
    node.innerHTML = (V.activeTool ? `<div class="item"><span class="muted">running</span><span class="tag ${D.esc(V.activeTool.category)}">${D.esc(V.activeTool.category)}</span><b>${D.esc(V.activeTool.name)}</b><span></span><span>…</span></div>` : "") +
      (rows.map((t) => `<div class="item"><span class="muted">${new Date(t.t * 1000).toLocaleTimeString()}</span><span class="tag ${D.esc(t.category)}">${D.esc(t.category)}</span><span>${D.esc(t.name)}</span><span class="muted">${t.ms != null ? t.ms + " ms" : ""}</span><span>${t.ok === false ? "✗" : "✓"}</span></div>`).join("") || (V.activeTool ? "" : `<span class="muted">No tool calls yet. They appear here as the agent works: file reads and edits, shell commands, browser and computer-use actions.</span>`));
  }
  /* One row per model request, from the runtime's own start and finish records (newest first). */
  function renderRequests() {
    const node = V.root && V.root.querySelector("#viz-requests"); if (!node) return;
    const rows = []; let open = null;
    V.requests.forEach((e) => {
      if (e.phase === "start") { open = { t: e.t, prompt: e.prompt_tokens, reused: e.reused_tokens, fresh: e.new_prefill_tokens, tools: e.tools }; rows.push(open); }
      else if (e.phase === "finish" && open) { Object.assign(open, { generated: e.generated, finish: e.finish, seconds: e.seconds, decode: e.decode_tps, prefill: e.prefill_tps }); open = null; }
    });
    if (!rows.length) { node.innerHTML = `<span class="muted">No requests yet in this session. Each model request appears here with its prompt, prefix reuse, prefill and decode rates.</span>`; return; }
    const top = Math.max(1, ...rows.map((r) => r.decode || 0)), n = (v, d = 0) => v == null ? "–" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d });
    node.innerHTML = `<table><thead><tr><th>Time</th><th>Prompt</th><th>Reused prefix</th><th>New prefill</th><th>Prefill tok/s</th><th>Generated</th><th>Decode tok/s</th><th>Total</th><th>End</th></tr></thead><tbody>${rows.slice(-40).reverse().map((r) => `<tr>
      <td class="mono">${new Date(r.t * 1000).toLocaleTimeString()}</td><td>${n(r.prompt)}</td>
      <td>${r.reused ? n(r.reused) + ` <span class="faint">(${Math.round(100 * r.reused / Math.max(1, r.prompt))} %)</span>` : "0"}</td><td>${n(r.fresh)}</td><td>${n(r.prefill, 0)}</td><td>${n(r.generated)}</td>
      <td><span class="req-bar"><i style="width:${100 * (r.decode || 0) / top}%"></i></span>${n(r.decode, 1)}</td><td>${r.seconds != null ? r.seconds.toFixed(1) + " s" : '<span class="muted">running</span>'}</td>
      <td class="${r.finish === "error" || r.finish === "cancelled" ? "bad" : "muted"}">${D.esc(r.finish || "")}</td></tr>`).join("")}</tbody></table>`;
  }
  let factsFor = "";
  function renderFacts(m, live) {
    const key = m.id + (D.runningModel() || ""); if (factsFor === key) return; factsFor = key;
    V.root.querySelector("#viz-facts").innerHTML = `
      <p><b>Storage.</b> Only the PLE n-gram table (${D.esc(m.quantization.ple.split(",")[0])}, ${(S.catalog.details[m.id].assets.ple.bytes / 2 ** 30).toFixed(1)} GiB) is read from disk while the model runs, a few rows per token. The model, the draft model and the vision projector are loaded into unified memory at start and stay resident; experts are <b>not</b> streamed from disk.</p>
      <p><b>Processor.</b> Each token passes through 48 layers. In every layer a router picks ${10} of 512 experts; the lit cells are the experts actually chosen for the current token, brighter for higher routing weight.</p>
      <p><b>MTP.</b> A small head drafts the next one or two tokens. The trunk checks them in the same pass; accepted drafts are committed without an extra pass, rejected drafts are discarded.</p>
      <p><b>Context.</b> The KV cache (K ${D.esc(m.cache.k_cache_dtype)}, V ${D.esc(m.cache.v_cache_dtype)}) is reserved for the full ${m.context_label} context at load and fills as the conversation grows.</p>
      <p><b>Runtime.</b> Splash-m1 reports requests, rates, context and its memory plan; it does not report router selections, PLE reads or per-cycle drafts, so those regions say so instead of drawing estimates. Sessions recorded with the earlier native runtime replay with their full detail.</p>
      <p><b>Privacy.</b> The view shows counts, ids and timings. It never shows token text, reasoning or tool arguments.</p>`;
    renderActions(); renderRequests();
  }
  window.Visualizer = { mount, unmount };
})();
