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
    dflash: null, dflashSince: 0, dflashPoll: 0,
    history: [], open: null, viewing: null, historyDrawn: 0, requestSeq: 0,
    router: null, ple: null, routerLevel: 2, routingPhase: 0,
  };
  /* ds3 router telemetry. The runtime's MoE grouping kernel counts the routes each step executes per layer and
   * expert (speculative verify rows a later check rejects included) into a small page the engine reads when polled;
   * /telemetry hands out the differences. Aggregate is the default; "sampled" adds one decode step in N's selected
   * experts (row 0, no weights). Bounded: two 48 x 512 Uint32 session histograms, one decaying Float32 "recent"
   * histogram, the last SAMPLES_KEPT samples; per request a lazily made 96 KiB decode histogram and at most
   * REQUEST_SAMPLES samples. Counts and ids only, in memory, never saved unless a request is exported. */
  const SAMPLES_KEPT = 64, REQUEST_SAMPLES = 128, RECENT_MS = 1500;
  /* Request history (ds2). The runtime reports decode cycles, not request ids, so a request here is one uninterrupted
   * decode run: a new entry starts after GAP_MS with no cycle (between agent turns there is always tool time and a
   * prefill, which produce none). Bounded: the newest HISTORY_MAX entries, each keeping its first CYCLES_MAX cycles
   * as six float32 numbers in one fixed buffer (96 KiB per request, under 2 MiB in all); totals keep counting past the
   * cap. In memory only, never written, and never any text. */
  const HISTORY_MAX = 20, CYCLES_MAX = 4096, GAP_MS = 400;
  const CYCLE_FIELDS = ["t_ms", "output", "drafted", "accepted", "cycle_ms", "width"];
  const COLORS = { text: "#dbe3ee", muted: "#8593a6", faint: "#42505f", line: "#232c3a", panel: "#10151d", accent: "#5cc8ff", good: "#4fd695", warn: "#ffc857", bad: "#ff6b6b", ple: "#ffb454", mtp: "#7fd6a3", cyber: "#b98cff" };
  const TOOL_NODES = [["workspace", "Workspace", "files"], ["shell", "Shell", "commands"], ["browser", "Browser", "pages"], ["gui", "Computer use", "KimiCU"], ["vision", "Vision", "images"]];

  function tone() { const m = D.runningModel() || S.model; return { scope: "#5cc8ff", swift: "#ffb454", cyber: "#b98cff" }[m] || COLORS.accent; }
  function model() { const id = (V.replay && V.replay.model) || D.runningModel() || S.model; return D.modelOf(id); }
  /* What the model's backend reports. Activity a backend does not report is labelled as such, never drawn from guesses. */
  function caps() { return (model().backend || {}).telemetry || { router: true, ple: true, mtp: true, requests: true, dflash: "none" }; }
  /* ds3 router aggregates: "aggregate" or "sampled" when the runtime reports them, else "unavailable". Distinct from
   * caps().router, which means the native runtime's per-token rows with weights. */
  function routing() { return V.replay ? "unavailable" : caps().routing || "unavailable"; }
  function hasRouting() { return routing() !== "unavailable"; }
  function routerModeLabel() {
    const r = V.router;
    if (!r) return "aggregate";
    return r.mode === "sampled" || r.mode === "exact" ? (r.every > 1 ? "sampled 1 in " + r.every : "every step") : "aggregate";
  }
  function backendName() { return "Splash-m1"; }
  function notReported(x, y, w, h, what) {
    const g = V.g; g.fillStyle = "rgba(16,21,29,.86)"; g.fillRect(x, y, w, h);
    label(what + " not reported by the " + backendName() + " backend", x + w / 2, y + h / 2 - 14, COLORS.muted, "600 12px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "center", w - 20);
    label("Nothing is drawn rather than estimated. Live rates, context and memory still come from the runtime.", x + w / 2, y + h / 2 + 4, COLORS.faint, "10.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "center", w - 20);
  }

  function mount() {
    if (V.mounted) { V.root.querySelector("#viz-status").innerHTML = D.runningLine(); resize(); return; }
    V.root = document.querySelector("#view-visualizer");
    V.root.innerHTML = `
      <div class="page-head"><h1>Live</h1>${D.liveModeSwitch("visualizer")}<span class="spacer"></span><span id="viz-status"></span></div>
      <div class="row" style="margin-bottom:10px">
        <div class="seg" id="viz-modes">
          <button class="chip selected" data-mode="overview">Overview</button>
          <button class="chip" data-mode="layer">Layer focus</button>
          <button class="chip" data-mode="heatmap">Routing heatmap</button>
        </div>
        <div class="seg" id="viz-phase" style="display:none" title="Which steps the routing views count: decode (verify cycles) or prefill (prompt chunks)">
          <button class="chip selected" data-phase="0">Decode</button>
          <button class="chip" data-phase="1">Prefill</button>
        </div>
        <label class="small muted" id="viz-sampled-wrap" style="display:none" title="Also copy one decode step in N's selected experts from the GPU (row 0 of the step, no weights). Aggregates stay on either way."><input type="checkbox" id="viz-sampled" aria-label="Sampled selections" style="vertical-align:middle"> Sampled selections</label>
        <label class="small muted" id="viz-layer-wrap" style="display:none">Layer <input type="range" id="viz-layer" min="0" max="47" value="24" style="vertical-align:middle;width:160px"> <b id="viz-layer-n">24</b></label>
        <span class="spacer"></span>
        <select id="viz-session" aria-label="Session"><option value="">Live</option></select>
        <button class="ghost small-button" id="viz-pause">Pause</button>
      </div>
      <canvas id="viz-canvas" style="height:600px" aria-label="Model visualizer"></canvas>
      <div class="row" style="margin-top:8px"><span class="small muted" style="width:70px">Timeline</span>
        <input type="range" id="viz-scrub" min="0" max="0" value="0" style="flex:1" aria-label="Scrub recent events">
        <span class="small mono" id="viz-clock" style="width:190px;text-align:right">live</span></div>
      <div class="grid cols-2" style="margin-top:12px">
        <div class="panel"><h2>Action timeline</h2><div class="timeline" id="viz-actions"></div></div>
        <div class="panel"><h2>What this shows</h2><div class="small muted" id="viz-facts"></div></div>
      </div>
      <div class="panel" style="margin-top:12px"><h2>Recent requests</h2><div id="viz-history" class="small"></div></div>
      <div class="panel" style="margin-top:12px"><h2>Requests</h2><div id="viz-requests" class="small"></div></div>`;
    D.bindLiveModes(V.root);
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
    V.root.querySelectorAll("[data-phase]").forEach((b) => b.onclick = () => {
      V.routingPhase = +b.dataset.phase;
      V.root.querySelectorAll("[data-phase]").forEach((x) => x.classList.toggle("selected", x === b));
    });
    V.root.querySelector("#viz-sampled").onchange = (e) => { V.routerLevel = e.target.checked ? 3 : 2; };
    const scrub = V.root.querySelector("#viz-scrub");
    scrub.oninput = () => { setPaused(true); V.playhead = +scrub.value; };
    V.root.querySelector("#viz-session").onchange = (e) => loadSession(e.target.value);
    V.root.querySelector("#viz-history").onclick = (event) => {
      const button = event.target.closest("[data-request]"); if (!button) return;
      const entry = V.history.find((h) => h.id === +button.dataset.request); if (!entry) return;
      if (button.dataset.action === "export") exportRequest(entry); else viewRequest(entry);
    };
    V.canvas.onclick = (event) => pickLayer(event);
    V.canvas.onmousemove = (event) => {
      const rect = V.canvas.getBoundingClientRect(), px = event.clientX - rect.left, py = event.clientY - rect.top;
      const hit = (V.hits || []).find((h) => px >= h.x && px <= h.x + h.w && py >= h.y && py <= h.y + h.h);
      V.canvas.title = hit ? hit.text : "";
    };
    new ResizeObserver(resize).observe(V.canvas);
    S.listeners.push(onEvent);
    V.mounted = true;
    V.root.querySelector("#viz-status").innerHTML = D.runningLine();
    resize();
    refreshSessions();
    D.api("/api/heat").then((r) => { if (r.ok && r.layers.length) seedHeat(r.layers); });
    D.api("/api/recent").then((r) => { if (r.ok) r.events.forEach((e) => onEvent(e, true)); });
    startDflashPoll();
    loop();
  }

  /* DwarfStar advanced telemetry: poll /api/telemetry (5 Hz) for per-verify-cycle speculative-decode events (ds2+)
   * and, on ds3, router and PLE aggregates. Bounded: rolling accumulators, fixed histograms and the synthetic ticks the
   * pill animation already caps. Runs only live (not a replay), only while a model with these capabilities runs. */
  function startDflashPoll() {
    clearInterval(V.dflashPoll);
    V.dflashPoll = setInterval(async () => {
      if (V.replay || (caps().dflash !== "cycle" && !hasRouting()) || !D.runningModel()) return;
      const level = hasRouting() && (caps().routing_levels || []).includes("sampled") ? V.routerLevel : 2;
      const r = await D.api("/api/telemetry?since=" + V.dflashSince + "&level=" + level).catch(() => null);
      const batch = r && r.ok && r.telemetry;
      if (!batch || !batch.events) return;
      const first = !V.dflashSince;  // the first drain also returns what the ring held before this view opened
      if (batch.dropped && V.dflash) V.dflash.dropped = (V.dflash.dropped || 0) + batch.dropped;
      if (batch.dropped && V.open) V.open.dropped += batch.dropped;
      V.dflashSince = batch.next || V.dflashSince;
      if (batch.router && batch.router.available) routerHeader(batch.router);
      for (const e of batch.events) {
        if (e.kind === "router_aggregate") { routerAggregate(e, first); continue; }
        if (e.kind === "router_sample") { routerSample(e); continue; }
        if (e.kind === "ple_aggregate") { pleAggregate(e); continue; }
        if (e.kind !== "decode_cycle") continue;
        recordCycle(e);
        const d = V.dflash || (V.dflash = { proposed: 0, accepted: 0, rejected: 0, cycles: 0, tokens: 0, acceptance_pct: null, dropped: 0 });
        d.cycles += 1; d.proposed += e.drafted; d.accepted += e.accepted;
        d.rejected += Math.max(0, e.drafted - e.accepted); d.tokens += e.output;
        d.acceptance_pct = d.proposed ? 100 * d.accepted / d.proposed : null;
        d.lastCycle = { n: e.output, dp: e.drafted, da: e.accepted, ms: e.cycle_ms };
        // Synthetic tick for the pill/"this cycle" animation the native path already draws.
        V.ticks.push({ dflash: true, n: e.output, dp: e.drafted, da: e.accepted, ms: e.cycle_ms, seq: e.seq, t: Date.now() / 1000, pos: e.seq });
        if (V.ticks.length > 1500) V.ticks.shift();
        for (let i = 0; i < e.drafted; i++) V.drafts.push({ t0: performance.now() + i * 20, ok: i < e.accepted });
        if (V.drafts.length > 240) V.drafts.splice(0, V.drafts.length - 240);
      }
      if (V.open && performance.now() - V.historyDrawn > 1000) renderHistory();
    }, 200);
  }
  function routerHeader(h) {
    const layers = Math.min(64, h.layers || 0), experts = Math.min(EXPERTS, h.experts || 0);
    if (!layers || !experts) return;
    let r = V.router;
    if (!r || r.layers !== layers || r.experts !== experts) {
      r = V.router = { layers, experts, topK: h.top_k, heat: [new Uint32Array(layers * experts), new Uint32Array(layers * experts)],
        recent: new Float32Array(layers * experts), recentAt: 0, steps: [0, 0], routes: [0, 0], summaries: 0,
        samples: [], sample: null, version: 0, mode: "aggregate", every: 1, samplesDropped: 0 };
    }
    r.mode = h.mode || (routing() === "sampled" ? "sampled" : "aggregate"); r.every = h.sample_every || 1; r.samplesDropped = h.samples_dropped || 0;
    r.pageBytes = h.page_bytes;
    const slider = V.root && V.root.querySelector("#viz-layer");
    if (slider && +slider.max !== layers - 1) { slider.max = layers - 1; if (V.focusLayer > layers - 1) { V.focusLayer = layers - 1; slider.value = V.focusLayer; } }
  }
  /* Payload-less events are another consumer's interval: counted, not drawn. Decode intervals also fold into the
   * decaying "recent" histogram and into the request being generated. */
  function routerAggregate(e, first) {
    const r = V.router; if (!r) return;
    const phase = e.phase === "prefill" ? 1 : 0;
    r.steps[phase] += e.steps || 0; r.routes[phase] += e.routes || 0;
    if (!e.payload || !e.layers) { if (!first) r.summaries += 1; return; }
    const heat = r.heat[phase], E = r.experts, now = performance.now();
    if (phase === 0) {
      const decay = r.recentAt ? Math.exp(-(now - r.recentAt) / RECENT_MS) : 0;
      for (let i = 0; i < r.recent.length; i++) r.recent[i] *= decay;
      r.recentAt = now;
    }
    const open = phase === 0 && V.open && now - (V.open.arrived || 0) < 1000 ? V.open : null;
    if (open && !open.heat) { open.heat = new Uint32Array(r.layers * E); open.heatLayers = r.layers; open.heatExperts = E; open.routes = 0; open.samples = []; }
    const own = open && open.heatLayers === r.layers && open.heatExperts === E ? open : null;
    e.layers.forEach((row, l) => {
      if (l >= r.layers) return;
      for (let i = 0; i + 1 < row.length; i += 2) {
        const at = l * E + row[i], count = row[i + 1];
        if (row[i] >= E) continue;
        heat[at] += count;
        if (phase === 0) r.recent[at] += count;
        if (own) own.heat[at] += count;
      }
    });
    if (own) { own.routes += e.routes || 0; own.routerMode = routerModeLabel(); }
    r.version += 1;
  }
  function routerSample(e) {
    const r = V.router; if (!r || !e.layers) return;
    const sample = { step: e.step, every: e.every, layers: e.layers.map((row) => Array.from(row)) };
    r.sample = sample; r.sampleAt = performance.now();
    r.samples.push(sample); if (r.samples.length > SAMPLES_KEPT) r.samples.shift();
    const open = V.open && performance.now() - (V.open.arrived || 0) < 1000 ? V.open : null;
    if (open && open.samples && open.samples.length < REQUEST_SAMPLES) open.samples.push(sample);
  }
  function pleAggregate(e) {
    const p = V.ple || (V.ple = { requested: 0, hits: 0, rows: 0, bytes: 0, bytes_per_s: 0, rows_per_s: 0 });
    const seconds = (e.interval_ms || 0) / 1000;
    if (e.table) {
      const t = e.table;
      p.requested += t.requested_rows || 0; p.hits += t.hit_rows || 0; p.rows += t.read_rows || 0; p.bytes += t.read_bytes || 0;
      p.bytes_per_s = seconds > 0 ? (t.read_bytes || 0) / seconds : p.bytes_per_s;
      p.rows_per_s = seconds > 0 ? (t.read_rows || 0) / seconds : p.rows_per_s;
      p.hit_pct = p.requested ? 100 * p.hits / p.requested : null;
      p.cached_rows = t.cached_rows; p.capacity_rows = t.cache_capacity_rows; p.cache_bytes = t.cache_bytes; p.table_bytes = t.table_bytes;
      p.p50 = t.read_ms_p50; p.p95 = t.read_ms_p95;
      if ((t.read_bytes || 0) > 0 && !V.paused) V.reads.push({ t0: performance.now(), bytes: t.read_bytes });
    }
    if (e.experts) {
      const x = e.experts;
      p.experts = x; p.expertRouted = (p.expertRouted || 0) + (x.routed || 0); p.expertRead = (p.expertRead || 0) + (x.read || 0);
      p.expertBytes = (p.expertBytes || 0) + (x.read_bytes || 0);
      p.expert_bytes_per_s = seconds > 0 ? (x.read_bytes || 0) / seconds : p.expert_bytes_per_s;
    }
  }
  /* The histogram a ds3 view draws: the request being viewed, else the session's, for the chosen phase. */
  function currentHeat() {
    const r = V.router; if (!r) return null;
    if (V.viewing) return V.viewing.heat && V.viewing.heatLayers === r.layers ? V.viewing.heat : null;
    return r.heat[V.routingPhase];
  }
  /* A layers x experts image of a histogram, log-scaled, made only when the data changed (at most at the poll rate)
   * and drawn scaled with smoothing off: the canvas never draws 24 576 cells itself. */
  const heatImages = new Map();
  function heatImage(key, values, layers, experts, version, perLayer) {
    let cached = heatImages.get(key);
    if (cached && cached.version === version && cached.values === values) return cached.canvas;
    if (!cached || cached.canvas.width !== experts || cached.canvas.height !== layers) {
      const canvas = document.createElement("canvas"); canvas.width = experts; canvas.height = layers;
      cached = { canvas, g: canvas.getContext("2d"), image: null };
      cached.image = cached.g.createImageData(experts, layers);
      heatImages.set(key, cached);
    }
    const data = cached.image.data;
    let max = 0;
    if (!perLayer) for (let i = 0; i < layers * experts; i++) if (values[i] > max) max = values[i];
    for (let l = 0; l < layers; l++) {
      let rowMax = max;
      if (perLayer) { rowMax = 0; for (let e = 0; e < experts; e++) if (values[l * experts + e] > rowMax) rowMax = values[l * experts + e]; }
      const scale = rowMax > 0 ? 1 / Math.log1p(rowMax) : 0;
      for (let e = 0; e < experts; e++) {
        const v = values[l * experts + e], o = 4 * (l * experts + e);
        if (!(v > 0)) { data[o] = 12; data[o + 1] = 16; data[o + 2] = 23; data[o + 3] = 255; continue; }
        // The native heatmap's ramp, rgba(60 + 195a, 120 + 80a, 255, 0.2 + 0.8a), composited over its #0c1017.
        const a = Math.log1p(v) * scale, alpha = 0.2 + 0.8 * a;
        data[o] = Math.round(12 + (60 + 195 * a - 12) * alpha);
        data[o + 1] = Math.round(16 + (120 + 80 * a - 16) * alpha);
        data[o + 2] = Math.round(23 + (255 - 23) * alpha);
        data[o + 3] = 255;
      }
    }
    cached.g.putImageData(cached.image, 0, 0);
    cached.version = version; cached.values = values;
    return cached.canvas;
  }
  function drawImageCells(image, x, y, w, h) {
    const g = V.g; g.save(); g.imageSmoothingEnabled = false; g.drawImage(image, x, y, w, h); g.restore();
  }
  /* Concentration of one layer's routes: the share of its ten busiest experts and the effective number of experts
   * (the exponential of the routes' entropy; 512 would be perfectly even). */
  function concentration(values, layer, experts) {
    let total = 0; const row = [];
    for (let e = 0; e < experts; e++) { const v = values[layer * experts + e]; if (v) { total += v; row.push([e, v]); } }
    if (!total) return null;
    row.sort((a, b) => b[1] - a[1]);
    let entropy = 0; for (const [, v] of row) { const q = v / total; entropy -= q * Math.log(q); }
    return { total, top: row.slice(0, 10), top10: row.slice(0, 10).reduce((a, [, v]) => a + v, 0) / total, effective: Math.exp(entropy), used: row.length };
  }
  function recordCycle(e) {
    const running = D.runningModel(), open = V.open;
    if (!open || open.model !== running || e.t - open.lastT > Math.max(GAP_MS, 4 * (e.cycle_ms || 0))) {
      const profile = D.S.state && D.S.state.active_profile;
      const name = D.profileLabel(profile) || D.modelOf(running).display_name.replace("DwarfStar ", "");
      const wall = Date.now();
      V.open = { id: ++V.requestSeq, model: running, profile, wall, label: name + " · " + new Date(wall).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        t0: e.t, lastT: e.t, buf: new Float32Array(CYCLES_MAX * CYCLE_FIELDS.length), n: 0, output: 0, drafted: 0, accepted: 0, cycleMs: 0, width: 0, truncated: 0, dropped: 0 };
      V.history.push(V.open);
      if (V.history.length > HISTORY_MAX) V.history.shift();
      renderHistory();
    }
    const r = V.open;
    r.lastT = e.t; r.arrived = performance.now();
    r.output += e.output; r.drafted += e.drafted; r.accepted += e.accepted; r.cycleMs += e.cycle_ms || 0; r.width = Math.max(r.width, e.width || 0);
    if (r.n < CYCLES_MAX) r.buf.set([e.t - r.t0, e.output, e.drafted, e.accepted, e.cycle_ms || 0, e.width || 0], r.n++ * CYCLE_FIELDS.length);
    else r.truncated += 1;
  }
  function cycleRows(r) {
    const rows = [], k = CYCLE_FIELDS.length;
    for (let i = 0; i < r.n; i++) rows.push([Math.round(r.buf[i * k]), r.buf[i * k + 1], r.buf[i * k + 2], r.buf[i * k + 3], +r.buf[i * k + 4].toFixed(3), r.buf[i * k + 5]]);
    return rows;
  }
  function decodeSeconds(r) { return (r.lastT - r.t0 + (r.n ? r.buf[4] : 0)) / 1000; }
  function isOpen(r) { return r === V.open && performance.now() - (r.arrived || 0) < 1000; }
  function renderHistory() {
    const node = V.root && V.root.querySelector("#viz-history"); if (!node) return;
    V.historyDrawn = performance.now();
    if (!V.history.length) {
      node.innerHTML = `<span class="muted">${caps().dflash === "cycle" ? "Requests appear here as the model generates: the last " + HISTORY_MAX + " are kept in this window to view and scrub cycle by cycle." : "Request history needs the advanced-telemetry runtime, which reports each decode cycle."}</span>`;
      return;
    }
    const n = (v, d = 0) => Number(v).toLocaleString(undefined, { maximumFractionDigits: d });
    node.innerHTML = `<table><thead><tr><th>Request</th><th>Generated</th><th>Cycles</th><th>Drafts accepted</th><th>Decode</th><th>tok/s</th><th></th></tr></thead><tbody>${V.history.slice().reverse().map((r) => {
      const seconds = decodeSeconds(r), cycles = r.n + r.truncated;
      return `<tr${r === V.viewing ? ' class="selected"' : ""}><td><b>${D.esc(r.label)}</b>${isOpen(r) ? ' <span class="badge live">generating</span>' : ""}${r === V.viewing ? ' <span class="badge">viewing</span>' : ""}</td>
        <td>${n(r.output)}</td><td>${n(cycles)}${r.truncated ? ` <span class="faint" title="Only the first ${CYCLES_MAX} cycles are kept for replay; totals count all">(${n(CYCLES_MAX)} kept)</span>` : ""}</td>
        <td>${r.drafted ? Math.round(100 * r.accepted / r.drafted) + " %" : "–"} <span class="faint">${n(r.accepted)}/${n(r.drafted)}</span>${r.dropped ? ` <span class="warn" title="The engine ring overwrote ${r.dropped} cycles before they were read">· ${n(r.dropped)} missed</span>` : ""}</td>
        <td>${seconds.toFixed(1)} s</td><td>${seconds > 0 ? n(r.output / seconds, 1) : "–"}</td>
        <td style="white-space:nowrap"><button class="ghost small-button" data-request="${r.id}" data-action="view">View</button> <button class="ghost small-button" data-request="${r.id}" data-action="export">Export JSON</button></td></tr>`;
    }).join("")}</tbody></table>
      <p class="faint" style="margin:8px 0 0">The runtime reports decode cycles, not requests: an entry is one uninterrupted decode run, split after ${GAP_MS} ms without a cycle. Numbers only, kept in this window (the last ${HISTORY_MAX}), never saved; Export writes one request's cycles to a file you choose.</p>`;
  }
  /* Show one recorded request: the canvas, output stream and draft panel draw its cycles; the scrubber walks them. */
  function viewRequest(entry) {
    if (V.replay) { V.replay = null; V.root.querySelector("#viz-session").value = ""; resetBuffers(); }
    let proposed = 0, accepted = 0;
    const rows = cycleRows(entry);
    entry.ticks = rows.map((c, i) => ({ dflash: true, n: c[1], dp: c[2], da: c[3], ms: c[4], seq: i + 1, t: (entry.wall + c[0]) / 1000, pos: i + 1 }));
    entry.cum = rows.map((c) => [proposed += c[2], accepted += c[3]]);
    V.viewing = entry;
    setPaused(true); V.playhead = 0;
    renderHistory();
  }
  function viewedDraft() {
    const r = V.viewing, i = Math.max(0, Math.min(V.playhead, r.cum.length - 1)), [proposed, accepted] = r.cum[i] || [0, 0];
    return { proposed, accepted, rejected: proposed - accepted, cycles: i + 1, acceptance_pct: proposed ? 100 * accepted / proposed : null };
  }
  function exportRequest(r) {
    const document_ = {
      kind: "DwarfStar request telemetry", format: 1, name: r.label, model: r.model, profile: r.profile || null,
      started: new Date(r.wall).toISOString(), capabilities: caps(),
      segmentation: `one uninterrupted decode run; a new request starts after ${GAP_MS} ms without a decode cycle (the runtime reports cycles, not request ids)`,
      totals: { cycles: r.n + r.truncated, output_tokens: r.output, drafted: r.drafted, accepted: r.accepted, decode_s: +decodeSeconds(r).toFixed(3),
        max_batch_width: r.width, cycles_missed_by_reader: r.dropped },
      cycles_kept: r.n, cycles_not_kept: r.truncated, fields: CYCLE_FIELDS, cycles: cycleRows(r),
    };
    if (r.heat) {
      // ds3: decode routes executed per layer and expert during this request (speculative rows included), as
      // [layer, expert, routes] triplets, and any sampled selections (row 0 of one decode step in N, no weights).
      const cells = [];
      for (let i = 0; i < r.heat.length; i++) if (r.heat[i]) cells.push([Math.floor(i / r.heatExperts), i % r.heatExperts, r.heat[i]]);
      document_.router = { router_mode: r.samples && r.samples.length ? "sampled" : "aggregate", mode_detail: r.routerMode || "aggregate",
        counts: "routes_executed: every verified row of a decode step, speculative rows a later check rejected included",
        phase: "decode", layers: r.heatLayers, experts: r.heatExperts, routes: r.routes || 0, fields: ["layer", "expert", "routes"], cells,
        samples: (r.samples || []).map((x) => ({ step: x.step, every: x.every, layers: x.layers })), samples_kept_max: REQUEST_SAMPLES };
    }
    const blob = new Blob([JSON.stringify(document_, null, 1)], { type: "application/json" });
    const link = document.createElement("a"), at = new Date(r.wall), two = (v) => String(v).padStart(2, "0");
    const stamp = `${at.getFullYear()}${two(at.getMonth() + 1)}${two(at.getDate())}-${two(at.getHours())}${two(at.getMinutes())}`;  // local, like the name
    link.href = URL.createObjectURL(blob); link.download = `dwarfstar-request-${r.model}-${stamp}.json`;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 60000);
  }
  function ticks() { return V.viewing ? V.viewing.ticks : V.ticks; }
  function unmount() { clearInterval(V.dflashPoll); V.dflashPoll = 0; /* keep state; the loop idles when hidden */ }
  function setPaused(paused) {
    V.paused = paused;
    if (!paused && V.viewing) { V.viewing.ticks = V.viewing.cum = null; V.viewing = null; renderHistory(); }
    if (!paused) { V.playhead = -1; if (V.replay) { V.replay = null; V.root.querySelector("#viz-session").value = ""; resetBuffers(); } }
    V.root.querySelector("#viz-pause").textContent = paused ? (V.viewing ? "Back to live" : "Resume live") : "Pause";
  }
  function resetBuffers() { V.ticks = []; V.tools = []; V.requests = []; V.heat = null; V.heatMax = 1; V.pills = []; V.drafts = []; V.dflash = null; V.dflashSince = 0; V.router = null; V.ple = null; }
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
    const all = ticks();
    if (!all.length) return null;
    if (V.paused && V.playhead >= 0) return all[Math.min(V.playhead, all.length - 1)];
    return all[all.length - 1];
  }
  function draw() {
    const g = V.g, W = V.width, H = V.height, m = model(), now = performance.now();
    g.clearRect(0, 0, W, H);
    V.hits = [];
    if (!m) return;
    const tick = currentTick();
    const scrub = V.root.querySelector("#viz-scrub");
    scrub.max = Math.max(0, ticks().length - 1);
    if (!V.paused) scrub.value = scrub.max;
    V.root.querySelector("#viz-clock").textContent = tick ? (V.paused ? (V.viewing ? "request · " : V.replay ? "replay · " : "paused · ") : "live · ") + (tick.t ? new Date(tick.t * 1000).toLocaleTimeString() : "") + (tick.dflash ? " · cycle " + tick.seq : tick.pos != null ? " · token " + tick.pos : "") : (D.runningModel() ? "waiting for tokens" : "no model running");
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
    // ds3: the row store's own counters (requests, cache hits, SSD reads), aggregated per poll interval.
    const pleRows = caps().ple_rows === "aggregate" && !V.replay, ple = pleRows ? (V.ple || {}) : (live.ple || {});
    if (!caps().ple && !pleRows) label("read rate: not reported", x + 18, pleY + 60, COLORS.muted, "10.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", inner);
    else label(((ple.bytes_per_s || 0) / 1024).toFixed(0) + " KiB/s" + (pleRows && ple.hit_pct != null ? "  ·  " + ple.hit_pct.toFixed(0) + " % cached" : ""), x + 18, pleY + 60, COLORS.text, "600 14px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", inner);
    if (caps().ple || pleRows) label(((pleRows ? ple.rows : ple.reads) || 0).toLocaleString() + " row reads · " + ((ple.bytes || 0) / 2 ** 20).toFixed(1) + " MiB", x + 18, pleY + 78, COLORS.faint, "10.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", inner);
    if (pleRows && V.ple) hint(x + 10, pleY + 56, w - 20, pleH - 56, `This session: ${(ple.requested || 0).toLocaleString()} rows requested, ${(ple.hits || 0).toLocaleString()} served from the row cache, ${(ple.rows || 0).toLocaleString()} read from the SSD (${((ple.bytes || 0) / 2 ** 20).toFixed(2)} MiB). Read latency p50 ${ple.p50 != null ? ple.p50 + " ms" : "–"}, p95 ${ple.p95 != null ? ple.p95 + " ms" : "–"}. Row cache ${(ple.cached_rows || 0).toLocaleString()} of ${(ple.capacity_rows || 0).toLocaleString()} rows.`);
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
    const prefix = (S.state?.models?.[D.runningModel()]?.splash || {}).prefix_cache;
    label(caps().mtp ? "KV CHECKPOINTS · disk cache" : "PREFIX CACHE · reused KV", x + 12, fy + 6, COLORS.faint, "600 9.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", w - 24);
    label(caps().mtp ? (kv.stores || 0) + " stored · " + (kv.hits || 0) + " restored"
      : prefix ? (prefix.reused_tokens || 0).toLocaleString() + " tokens · " + Math.round(100 * (prefix.hit_rate || 0)) + " % hits" : "–", x + 12, fy + 22, COLORS.muted, undefined, "left", w - 24);
    // ds3: a model whose routed experts stream from the SSD reports its expert store; resident ones say so.
    const store = V.ple && V.ple.experts;
    if (store && fy + 70 < y + h) {
      const streamed = store.slots && store.experts && store.slots < store.experts;
      label("EXPERT STORE", x + 12, fy + 44, COLORS.faint, "600 9.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", w - 24);
      label(streamed ? ((V.ple.expert_bytes_per_s || 0) / 2 ** 20).toFixed(1) + " MiB/s · " + (V.ple.expertRouted ? Math.round(100 * (1 - V.ple.expertRead / V.ple.expertRouted)) + " % resident hits" : "–") : "all routed experts resident", x + 12, fy + 60, COLORS.muted, undefined, "left", w - 24);
    }
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
    const g = V.g, ds3 = hasRouting() ? V.router : null, layers = ds3 ? ds3.layers : 48;
    const experts = ds3 ? ds3.experts : EXPERTS, topK = ds3 ? ds3.topK : (tick && tick.k || 10);
    box(x, y, w, h, "Processor (GPU)", m.display_name.replace("DwarfStar ", "") + " · " + layers + " layers · " + experts + " experts per layer, " + topK + " active per token", active || (ds3 && now - ds3.recentAt < 600) ? tone() : COLORS.line);
    if (hasRouting()) routingBadge(x + w - 10, y + 6);
    const px = x + 12, py = y + 30, pw = w - 24, ph = h - 42;
    if (!caps().router && !hasRouting() && V.mode !== "overview") return notReported(px, py, pw, ph, "Router selections are");
    if (V.mode === "heatmap") return hasRouting() ? drawRouterHeat(px, py, pw, ph) : drawHeat(px, py, pw, ph);
    if (V.mode === "layer") return hasRouting() ? drawRouterLayer(px, py, pw, ph) : drawLayer(px, py, pw, ph, tick);
    // overview: one row per layer, router dot + expert strip
    const mtpW = Math.min(150, pw * 0.2), stripX = px + 62, stripW = pw - 62 - mtpW - 16, rowH = ph / layers;
    const rows = tick && tick.r;
    const progress = Math.min(1, (now - V.lastTickAt) / 220);
    // ds3: each strip is that layer's routes over the last ~1.5 s (decaying), scaled to the layer's busiest expert.
    const recentLive = ds3 && ds3.recentAt && now - ds3.recentAt < 4 * RECENT_MS && !V.viewing;
    if (recentLive) drawImageCells(heatImage("recent", ds3.recent, layers, experts, ds3.version, true), stripX, py, stripW, ph);
    const sample = ds3 && ds3.sample && now - ds3.sampleAt < 3000 && !V.viewing ? ds3.sample : null;
    const sampleLabel = Math.max(1, Math.ceil(layers / 8));
    for (let l = 0; l < layers; l++) {
      const ry = py + l * rowH;
      if (l % (ds3 ? sampleLabel : 6) === 0) label("L" + l, px, ry - 1, COLORS.faint, "9px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
      if (!recentLive) { g.fillStyle = "#0c1017"; g.fillRect(stripX, ry + 1, stripW, Math.max(1.5, rowH - 2)); }
      const lit = !V.paused ? l / layers <= progress : true;
      g.fillStyle = (lit && rows) || recentLive ? tone() : COLORS.faint; g.beginPath(); g.arc(px + 44, ry + rowH / 2, 2.2, 0, 6.3); g.fill();
      if (sample && sample.layers[l]) {
        g.fillStyle = COLORS.text;
        for (const e of sample.layers[l]) g.fillRect(stripX + e / experts * stripW - 0.5, ry + 0.5, 2, Math.max(2, rowH - 1));
      }
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
    label("expert " + (experts - 1), stripX + stripW, py + ph + 1, COLORS.faint, "9px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "right");
    if (ds3) {
      label(recentLive ? "routes in the last ~1.5 s, each layer to its busiest expert" + (sample ? " · white: sampled step " + sample.step : "") : "", stripX + stripW / 2, py + ph + 1, COLORS.faint, "9px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "center", stripW - 140);
      if (!recentLive) label(D.runningModel() ? (V.viewing ? "Viewing a request: its routing is in the Routing heatmap and Layer focus views." : "Waiting for decode steps. Routing appears while the model generates.") : "Start a model to see routing.", stripX + 10, py + ph / 2 - 6, COLORS.muted, "12px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", stripW - 20);
    } else if (!caps().router && !hasRouting()) notReported(stripX - 62, py, stripW + 62, ph, "Router selections are");
    else if (!rows) label(D.runningModel() ? "Waiting for the next token. Router selections appear while the model generates." : "Start a model to see routing.", stripX + 10, py + ph / 2 - 6, COLORS.muted, "12px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
    // token pill travelling down the stack
    V.pills.forEach((p) => { const t = (now - p.t0) / 260; if (t > 1) return; g.fillStyle = COLORS.text; g.beginPath(); g.roundRect(px + 14, py + t * (ph - 10), 20, 8, 4); g.fill(); });
    // MTP lane
    const mx = px + pw - mtpW, mw = mtpW;
    g.strokeStyle = COLORS.mtp; g.fillStyle = "rgba(127,214,163,.06)"; g.lineWidth = 1; g.beginPath(); g.roundRect(mx, py + 4, mw, ph - 8, 7); g.fill(); g.stroke();
    const dflashCycle = caps().dflash === "cycle";
    label(caps().mtp ? "MTP HEAD" : "DFlash2 DRAFT", mx + 10, py + 12, COLORS.mtp, "600 10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", mw - 20);
    label(caps().mtp ? "drafts next tokens;" : "a draft model proposes;", mx + 10, py + 28, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", mw - 20);
    label("the target verifies them", mx + 10, py + 41, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", mw - 20);
    if (!caps().mtp && !dflashCycle) {
      label((model().backend || {}).speculative || "draft model", mx + 10, py + 64, COLORS.text, "600 11px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", mw - 20);
      label("per-cycle drafts not", mx + 10, py + 84, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", mw - 20);
      label("reported by " + backendName(), mx + 10, py + 97, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", mw - 20);
      return;
    }
    // Native: cumulative MTP from the live hub. ds2 Splash: the per-verify-cycle DFlash2 ring (V.dflash).
    const mtp = caps().mtp ? ((S.live && S.live.mtp) || {}) : V.viewing ? viewedDraft() : (V.dflash || {});
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
  /* What the routing views are drawn from, top right of the processor card. */
  function routingBadge(right, y) {
    const g = V.g, text = (routerModeLabel() + (V.viewing ? " · request" : "")).toUpperCase();
    g.font = "600 9.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif";
    const w = g.measureText(text).width + 14, x = right - w;
    g.fillStyle = COLORS.panel; g.fillRect(x - 8, y - 2, w + 8, 18);
    g.strokeStyle = COLORS.faint; g.lineWidth = 1; g.beginPath(); g.roundRect(x, y, w, 15, 7); g.stroke();
    label(text, x + 7, y + 3, COLORS.muted, "600 9.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
    hint(x, y, w, 15, routerModeLabel() === "aggregate"
      ? "Aggregate: every route the GPU executed, counted per layer and expert by the grouping kernel. Speculative rows a later check rejects are included. Not a per-token record."
      : "Sampled: the aggregate, plus the selected experts of one decode step in " + ((V.router && V.router.every) || "N") + " (row 0 of that step, without weights)." + (V.router && V.router.samplesDropped ? " " + V.router.samplesDropped + " samples were overwritten before a poll read them." : ""));
  }
  function phaseName() { return V.routingPhase ? "prefill" : "decode"; }
  let summaryFor = null, summary = null;
  function heatSummary(values, layers, experts, version) {
    const key = version + "/" + layers + "/" + V.routingPhase + "/" + (V.viewing ? V.viewing.id : "");
    if (summaryFor === key) return summary;
    const totals = new Float64Array(experts), effective = [];
    let all = 0;
    for (let l = 0; l < layers; l++) {
      for (let e = 0; e < experts; e++) { totals[e] += values[l * experts + e]; all += values[l * experts + e]; }
      const c = concentration(values, l, experts); if (c) effective.push(c.effective);
    }
    effective.sort((a, b) => a - b);
    const top = Array.from(totals, (v, e) => [e, v]).filter((x) => x[1]).sort((a, b) => b[1] - a[1]).slice(0, 5);
    summaryFor = key;
    summary = { all, top, median: effective.length ? effective[effective.length >> 1] : null };
    return summary;
  }
  function drawRouterHeat(x, y, w, h) {
    const r = V.router, values = currentHeat(), layers = r ? r.layers : 48, experts = r ? r.experts : EXPERTS;
    const what = V.viewing ? "this request (decode)" : "this session · " + phaseName();
    label("Routes executed per layer (rows, 0 at top) and expert (columns, 0 to " + (experts - 1) + "), " + what + ". Click a row to focus that layer.", x + 4, y, COLORS.muted, "11px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", w - 8);
    const lx = 26, hy = y + 22, hh = h - 58, hw = w - lx, rowH = hh / layers;
    V.g.fillStyle = "#0c1017"; V.g.fillRect(x + lx, hy, hw, hh);
    V.heatRect = { x: x + lx, y: hy, w: hw, h: hh, rowH, layers };
    for (let l = 0; l < layers; l += Math.max(1, Math.ceil(layers / 8))) label("L" + l, x, hy + l * rowH - 1, COLORS.faint, "9px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif");
    if (!r || !values) { label(V.viewing ? "This request has no routing record." : "No routing data yet.", x + lx + 10, hy + 10, COLORS.faint); return; }
    const version = V.viewing ? "v" + V.viewing.id + ":" + (V.viewing.routes || 0) : r.version;
    drawImageCells(heatImage("heat", values, layers, experts, version, false), x + lx, hy, hw, hh);
    const sm = heatSummary(values, layers, experts, version);
    if (!sm.all) { label("No " + phaseName() + " routes yet.", x + lx + 10, hy + 10, COLORS.faint); return; }
    const pct = (v) => (100 * v / sm.all).toFixed(1) + " %";
    label("Busiest experts across layers: " + sm.top.map(([e, v]) => "#" + e + " " + pct(v)).join(" · ") + (sm.median != null ? "  ·  effective experts per layer (median) " + sm.median.toFixed(0) + "/" + experts : ""),
      x + lx, hy + hh + 8, COLORS.muted, "10.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", hw);
    label(sm.all.toLocaleString() + " routes · log scale · rejected draft rows included" + (r.summaries ? " · " + r.summaries + " intervals went to another viewer" : ""),
      x + lx, hy + hh + 23, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", hw);
  }
  function drawRouterLayer(x, y, w, h) {
    const g = V.g, r = V.router, values = currentHeat();
    if (!r || !values) { label(V.viewing ? "This request has no routing record." : "No routing data yet.", x + 4, y + 4, COLORS.faint); return; }
    const experts = r.experts, layer = Math.min(V.focusLayer, r.layers - 1), cols = Math.ceil(Math.sqrt(experts * 2)), gridRows = Math.ceil(experts / cols);
    const cell = Math.max(4, Math.floor(Math.min((w - 260) / cols, (h - 34) / gridRows))), gx = x + 4, gy = y + 22;
    label("Layer " + layer + " · routes per expert, " + (V.viewing ? "this request (decode)" : "this session · " + phaseName()), x + 4, y, COLORS.muted, "11px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", w - 8);
    const c = concentration(values, layer, experts), max = c ? c.top[0][1] : 0;
    const sample = r.sample && !V.viewing && performance.now() - r.sampleAt < 3000 && r.sample.layers[layer] ? new Set(r.sample.layers[layer]) : null;
    for (let e = 0; e < experts; e++) {
      const cx = gx + (e % cols) * cell, cy = gy + Math.floor(e / cols) * cell, v = values[layer * experts + e];
      const a = max ? Math.log1p(v) / Math.log1p(max) : 0;
      g.fillStyle = v ? "rgba(92,200,255," + (0.12 + 0.88 * a) + ")" : "#0c1017"; g.fillRect(cx, cy, cell - 1, cell - 1);
      if (sample && sample.has(e)) { g.strokeStyle = COLORS.text; g.lineWidth = 1; g.strokeRect(cx - 0.5, cy - 0.5, cell, cell); }
    }
    const lx = gx + cols * cell + 18; let ly = gy;
    const side = w - (lx - x) - 4;
    label("BUSIEST EXPERTS", lx, ly, COLORS.muted, "600 10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif"); ly += 18;
    if (!c) { label("No " + phaseName() + " routes in this layer yet.", lx, ly, COLORS.faint, undefined, "left", side); return; }
    const barW = Math.max(40, side - 110);
    for (const [e, v] of c.top) {
      if (ly > y + h - 70) break;
      const share = v / c.total, bar = Math.max(1, v / max * barW);  // bars relative to the busiest expert
      label("#" + e, lx, ly, COLORS.text, "11px ui-monospace, Menlo"); g.fillStyle = tone(); g.fillRect(lx + 44, ly + 3, bar, 7);
      label((100 * share).toFixed(1) + " %", lx + 48 + bar, ly, COLORS.muted, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif"); ly += 17;
    }
    ly += 8;
    label("Top 10 take " + (100 * c.top10).toFixed(0) + " % of this layer's routes", lx, ly, COLORS.text, "10.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", side); ly += 15;
    label("effective experts " + c.effective.toFixed(0) + " of " + experts + " · " + c.used + " used", lx, ly, COLORS.muted, "10.5px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", side); ly += 15;
    label(c.total.toLocaleString() + " routes", lx, ly, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", side);
    label(sample ? "Outlined: the experts sampled step " + r.sample.step + " selected in this layer." : "Shade: how often each expert was routed to (log scale).", gx, gy + gridRows * cell + 8, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", cols * cell);
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
    V.focusLayer = Math.max(0, Math.min((r.layers || 48) - 1, Math.floor((py - r.y) / r.rowH)));
    V.root.querySelector("#viz-layer").value = V.focusLayer; V.root.querySelector("#viz-layer-n").textContent = V.focusLayer;
    V.root.querySelector('[data-mode="layer"]').click();
  }
  function drawBottom(x, y, w, h, m, live, tick, now) {
    const g = V.g, outW = w * 0.42;
    box(x, y, outW, h, "Output stream", "committed tokens (counts only)");
    const all = ticks(), end = V.paused && V.playhead >= 0 ? V.playhead + 1 : all.length, fits = Math.max(1, Math.floor((outW - 24) / 8)), recent = all.slice(Math.max(0, end - fits), end);
    let px = x + 12; const py = y + 34;
    recent.forEach((t) => { for (let i = 0; i < t.n; i++) { g.fillStyle = i === 0 ? COLORS.text : COLORS.mtp; g.globalAlpha = 0.9; g.beginPath(); g.roundRect(px, py + (i ? 12 : 0), 6, 9, 2); g.fill(); } px += 8; if (t.dp > t.da) { g.fillStyle = COLORS.bad; g.fillRect(px - 8, py + 26, 6, 2); } });
    g.globalAlpha = 1;
    label(caps().mtp || caps().dflash === "cycle" ? "green: accepted draft token · red mark: rejected draft" : "token counts only · draft acceptance is reported as a total in the Standard view", x + 12, y + 70, COLORS.faint, "10px -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif", "left", outW - 24);
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
    if (!rows.length) { node.innerHTML = `<span class="muted">No per-request rows in this session. Splash-m1 does not send per-request events; the latest request (from the agent's own record) and Splash's running totals are in the Standard view. Replays of sessions recorded with the earlier native runtime show their rows.</span>`; return; }
    const top = Math.max(1, ...rows.map((r) => r.decode || 0)), n = (v, d = 0) => v == null ? "–" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d });
    node.innerHTML = `<table><thead><tr><th>Time</th><th>Prompt</th><th>Reused prefix</th><th>New prefill</th><th>Prefill tok/s</th><th>Generated</th><th>Decode tok/s</th><th>Total</th><th>End</th></tr></thead><tbody>${rows.slice(-40).reverse().map((r) => `<tr>
      <td class="mono">${new Date(r.t * 1000).toLocaleTimeString()}</td><td>${n(r.prompt)}</td>
      <td>${r.reused ? n(r.reused) + ` <span class="faint">(${Math.round(100 * r.reused / Math.max(1, r.prompt))} %)</span>` : "0"}</td><td>${n(r.fresh)}</td><td>${n(r.prefill, 0)}</td><td>${n(r.generated)}</td>
      <td><span class="req-bar"><i style="width:${100 * (r.decode || 0) / top}%"></i></span>${n(r.decode, 1)}</td><td>${r.seconds != null ? r.seconds.toFixed(1) + " s" : '<span class="muted">running</span>'}</td>
      <td class="${r.finish === "error" || r.finish === "cancelled" ? "bad" : "muted"}">${D.esc(r.finish || "")}</td></tr>`).join("")}</tbody></table>`;
  }
  let factsFor = "";
  function renderFacts(m, live) {
    const key = m.id + (D.runningModel() || "") + routing() + (V.replay ? "r" : ""); if (factsFor === key) return; factsFor = key;
    const sampled = hasRouting() && (caps().routing_levels || []).includes("sampled");
    V.root.querySelector("#viz-sampled-wrap").style.display = sampled ? "" : "none";
    V.root.querySelector("#viz-phase").style.display = hasRouting() ? "" : "none";
    if (hasRouting()) {
      V.root.querySelector("#viz-facts").innerHTML = `
      <p><b>Storage.</b> Only the PLE n-gram table (${D.esc(m.quantization.ple.split(",")[0])}, ${(S.catalog.details[m.id].assets.ple.bytes / 2 ** 30).toFixed(1)} GiB) is read from disk while the model runs, a few rows per token; the row store reports its requests, cache hits and SSD reads, shown here per poll. The model, the draft model and the vision projector stay resident in unified memory.</p>
      <p><b>Processor.</b> Each token passes through the layers; in every layer a router picks a few of the experts. The GPU's own grouping step counts those routes per layer and expert into a small page the engine reads when this view polls: an <b>aggregate</b>, not a per-token record, and it includes speculative rows a later check rejects. <i>Sampled selections</i> also copies the experts one decode step in N chose (no weights). Nothing is recomputed and nothing waits for this view.</p>
      <p><b>Draft.</b> A small DFlash2 draft model proposes tokens and the target verifies them; only verified tokens are output. This panel shows proposals, acceptances and rejections per verify cycle; the Standard view keeps the running total.</p>
      <p><b>Context.</b> The KV cache (K ${D.esc(m.cache.k_cache_dtype)}, V ${D.esc(m.cache.v_cache_dtype)}) is reserved for the full ${m.context_label} context at load and fills as the conversation grows.</p>
      <p><b>Runtime.</b> Splash-m1 (ds3 GPU-telemetry build) reports requests, rates, latency, draft acceptance, prefix-cache use, its memory plan, decode cycles, router aggregates and row-store aggregates. Router weights and per-token PLE reads are not reported and are not drawn.</p>
      <p><b>Privacy.</b> The view shows counts, ids and timings. It never shows token text, reasoning or tool arguments.</p>`;
      renderActions(); renderRequests(); renderHistory();
      return;
    }
    V.root.querySelector("#viz-facts").innerHTML = `
      <p><b>Storage.</b> Only the PLE n-gram table (${D.esc(m.quantization.ple.split(",")[0])}, ${(S.catalog.details[m.id].assets.ple.bytes / 2 ** 30).toFixed(1)} GiB) is read from disk while the model runs, a few rows per token. The model, the draft model and the vision projector are loaded into unified memory at start and stay resident; experts are <b>not</b> streamed from disk.</p>
      <p><b>Processor.</b> Each token passes through 48 layers; in every layer a router picks 10 of 512 experts. Splash-m1 does not report those selections, so the grid stays empty for live sessions. Sessions recorded with the earlier native runtime replay the selections they recorded.</p>
      <p><b>Draft.</b> A small DFlash2 draft model proposes tokens and the target verifies them; only verified tokens are output. On the advanced-telemetry runtime this panel shows the proposals, acceptances and rejections per verify cycle, read from a bounded ring in the engine that adds no inference cost; the Standard view keeps the running total.</p>
      <p><b>Context.</b> The KV cache (K ${D.esc(m.cache.k_cache_dtype)}, V ${D.esc(m.cache.v_cache_dtype)}) is reserved for the full ${m.context_label} context at load and fills as the conversation grows.</p>
      <p><b>Runtime.</b> Splash-m1 reports requests, rates, latency, draft acceptance, prefix-cache use and its memory plan; the advanced-telemetry build adds per-verify-cycle speculative-decode detail. Router selections and PLE reads are not reported; those regions say so instead of drawing estimates.</p>
      <p><b>Privacy.</b> The view shows counts, ids and timings. It never shows token text, reasoning or tool arguments.</p>`;
    renderActions(); renderRequests(); renderHistory();
  }
  window.Visualizer = { mount, unmount };
})();
