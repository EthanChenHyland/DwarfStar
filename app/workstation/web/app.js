/* DwarfStar Workstation user interface.
 * Plain JavaScript, no dependencies, no network access beyond the local service. */
"use strict";

const S = {
  catalog: null, state: null, live: null, system: null, diagnostics: null,
  model: null, effort: null, view: "run", modelTab: null, busy: false,
  series: { tps: [], prefill: [], gpu: [], wired: [], ple: [], accept: [] },  // tps/ple/accept: replayed native sessions only
  events: [], ticks: [], tools: [], source: null, sourceLevel: 0, connected: false,
  listeners: [],
};
const TONE = { scope: "var(--scope)", swift: "var(--swift)", cyber: "var(--cyber)" };
const $ = (selector, root = document) => root.querySelector(selector);
const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const NA = '<span class="faint">Not measured</span>';
const gib = (bytes, digits = 2) => bytes == null ? NA : (bytes / 2 ** 30).toFixed(digits) + " GiB";
const num = (value, digits = 1, unit = "") => value == null || Number.isNaN(value) ? NA : Number(value).toFixed(digits) + (unit ? `<small>${unit}</small>` : "");
const plain = (value, digits = 1) => value == null || Number.isNaN(value) ? "–" : Number(value).toFixed(digits);
const ktok = (tokens) => tokens == null ? "–" : Math.round(tokens / 1024) + "K";
const modelOf = (id) => S.catalog.models.find((m) => m.id === id);
const profileOf = (model, effort) => S.catalog.profiles.find((p) => p.model === model && p.reasoning_effort === effort);
const profileLabel = (id) => id ? (S.catalog.profiles.find((p) => p.id === id)?.label || id).replace("DwarfStar ", "") : "";
const runningModel = () => (S.state?.running_models || [])[0] || null;

async function api(path, body) {
  const response = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined);
  return response.json();
}
function toast(text) {
  const node = $("#toast");
  node.textContent = text;
  node.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("show"), 4200);
}

/* ------------------------------------------------------------ live events */
function connect(level) {
  if (S.source && S.sourceLevel === level) return;
  if (S.source) S.source.close();
  S.sourceLevel = level;
  const source = S.source = new EventSource(`/api/events?level=${level}`);
  source.onopen = () => { S.connected = true; renderNav(); };
  source.onerror = () => { S.connected = false; renderNav(); };  // EventSource reconnects by itself
  source.onmessage = (message) => {
    let event;
    try { event = JSON.parse(message.data); } catch { return; }
    handleEvent(event);
  };
}
function push(series, value, limit = 240) { series.push(value); if (series.length > limit) series.shift(); }
function handleEvent(event) {
  if (event.kind === "hello") { S.live = event.live; S.system = event.system || S.system; scheduleLive(); return; }
  if (event.kind === "system") {
    S.system = event;
    push(S.series.gpu, event.gpu?.utilization_pct ?? null);
    push(S.series.wired, event.memory?.wired_bytes ? event.memory.wired_bytes / 2 ** 30 : null);
    scheduleLive();
    return;
  }
  if (event.kind === "tick") {
    S.ticks.push(event); if (S.ticks.length > 600) S.ticks.shift();
    const live = S.live || (S.live = {});
    live.mtp = live.mtp || { cycles: 0, proposed: 0, accepted: 0, rejected: 0, tokens: 0 };
    live.mtp.cycles += 1; live.mtp.proposed += event.dp; live.mtp.accepted += event.da;
    live.mtp.rejected += Math.max(0, event.dp - event.da); live.mtp.tokens += event.n;
    live.mtp.acceptance_pct = live.mtp.proposed ? 100 * live.mtp.accepted / live.mtp.proposed : null;
    if (event.ms > 0) {
      const instant = event.n / event.ms * 1000;
      live.rates = live.rates || {};
      live.rates.live_tps = live.rates.live_tps == null ? instant : 0.85 * live.rates.live_tps + 0.15 * instant;
      push(S.series.tps, live.rates.live_tps);
      live.ple = live.ple || { reads: 0, bytes: 0, bytes_per_s: 0 };
      live.ple.reads += event.ple[0]; live.ple.bytes += event.ple[1];
      live.ple.bytes_per_s = 0.8 * (live.ple.bytes_per_s || 0) + 0.2 * (event.ple[1] / event.ms * 1000);
      push(S.series.ple, live.ple.bytes_per_s / 1024);
      push(S.series.accept, live.mtp.acceptance_pct);
    }
    if (live.context) live.context.used = Math.max(live.context.used || 0, event.pos + event.n);
  } else {
    S.events.push(event); if (S.events.length > 400) S.events.shift();
    if (event.kind === "tool" && event.phase === "end") { S.tools.push(event); if (S.tools.length > 200) S.tools.shift(); }
    if (event.kind === "request" || event.kind === "runtime" || event.kind === "kv" || event.kind === "tool" || event.kind === "vision") refreshLiveSoon();
    if (event.kind === "runtime" || event.kind === "lifecycle") refreshStateSoon();
    if (event.kind === "request" && event.phase === "prefill") push(S.series.prefill, event.avg_tps);
  }
  for (const listener of S.listeners) listener(event);
  scheduleLive();
}
let liveFrame = 0;
function scheduleLive() {
  if (liveFrame) return;
  liveFrame = requestAnimationFrame(() => { liveFrame = 0; renderNav(); if (S.view === "live") renderLive(); if (S.view === "run") renderRunStats(); });
}
let liveTimer = 0, stateTimer = 0;
function refreshLiveSoon() { clearTimeout(liveTimer); liveTimer = setTimeout(async () => { const r = await api("/api/live"); if (r.ok) { S.live = r.live; S.system = r.system?.t ? r.system : S.system; scheduleLive(); } }, 120); }
function refreshStateSoon() { clearTimeout(stateTimer); stateTimer = setTimeout(refreshState, 250); }
async function refreshState() {
  try {
    S.state = await api("/api/state");
    renderNav();
    // Re-render only when something the views show has changed: a rebuild under the pointer swallows clicks.
    const key = JSON.stringify([S.state.running_models, S.state.active_profile, S.state.lifecycle?.job, S.state.lifecycle?.busy,
      S.state.lifecycle?.steps?.length, Object.values(S.state.models || {}).map((m) => [m.running, m.healthy, m.pid])]);
    if (key === S.renderedKey) return;
    S.renderedKey = key;
    if (S.view === "run") renderRun();
    if (S.view === "models") renderModels();
    if (S.view === "settings") renderSettings();
    window.Chats?.stateChanged();
  } catch { /* service restarting */ }
}

/* -------------------------------------------------------------------- nav */
function renderNav() {
  const running = runningModel();
  const dot = $("#nav-dot");
  const busy = S.state?.lifecycle?.busy;
  const state = running ? S.state.models[running] : null;
  dot.className = "dot " + (busy ? "busy" : running ? (state.healthy ? "ready" : "bad") : "");
  $("#nav-model").textContent = running ? (profileLabel(S.state.active_profile) || modelOf(running).display_name.replace("DwarfStar ", "")) : "No model running";
  const phase = S.live?.request?.active ? S.live.request.phase : null;
  $("#nav-phase").textContent = busy ? "Switching…" : running ? (phase ? phase.replace("_", " ").toLowerCase() : state.healthy ? "Ready" : "Not healthy") : (S.connected ? "Offline" : "Connecting…");
}
function show(view) {
  S.view = view;
  if (location.hash.slice(1) !== view) history.replaceState(null, "", "#" + view);
  document.querySelectorAll("#nav button[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === view || b.dataset.also === view));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + view));
  connect(view === "visualizer" ? 2 : 1);
  if (view !== "chats") window.Chats?.unmount();
  ({ run: renderRun, chats: () => window.Chats?.mount(), models: renderModels, live: renderLive, visualizer: () => window.Visualizer?.mount(),
     diagnostics: () => window.Diagnostics?.mount(), settings: renderSettings })[view]?.();
  if (view !== "visualizer") window.Visualizer?.unmount();
  clearInterval(S.livePoll);
  if (view === "live") S.livePoll = setInterval(async () => { S.renderedKey = null; await refreshState(); renderLive(); }, 2000);
  $("#main").scrollTop = 0;
}

/* -------------------------------------------------------------------- run */
/* One-line guidance per model; the numbers next to it come from the catalog's measurements. */
const GUIDE = { scope: "Dependable default for everyday work.", swift: "Fastest decode here; LOW or MEDIUM reasoning.",
                cyber: "Security-focused and the heaviest; OFF through XHIGH reasoning." };
const shortName = (text) => String(text || "").replace("DwarfStar ", "");
function runningLine() {
  const running = runningModel(), busy = S.state?.lifecycle?.busy;
  if (busy) return `<span class="status busy">Switching…</span>`;
  if (!running) return `<span class="status">No model running</span>`;
  const st = S.state.models[running];
  return `<span class="status ${st.healthy ? "ok" : "bad"}"><b>${esc(profileLabel(S.state.active_profile) || shortName(modelOf(running).display_name))}</b>
    &nbsp;${st.healthy ? "running" : "not healthy"}${st.uptime_s != null ? ` · up ${fmtDuration(st.uptime_s)}` : ""}</span>`;
}
function renderRun() {
  const root = $("#view-run");
  if (!S.catalog || !S.state) { root.innerHTML = "<h1>Run</h1><p class='lead'>Connecting to the local service…</p>"; return; }
  const model = modelOf(S.model), running = runningModel(), state = S.state.models[S.model];
  const profile = profileOf(S.model, S.effort);
  const isRunning = running === S.model, busy = S.state.lifecycle?.busy || S.busy;
  const activeProfile = S.state.active_profile, isActive = isRunning && activeProfile === profile.id;
  const requirement = S.state.requirements[S.model];
  const available = S.system?.memory?.available_bytes ? S.system.memory.available_bytes / 2 ** 30 : null;
  const cards = S.catalog.models.map((m) => {
    const measured = S.catalog.details[m.id].measured || {};
    const speed = measured.decode_range_tok_s ? measured.decode_range_tok_s.map((v) => Math.round(v)).join("–") + " tok/s" : "";
    return `<button class="model-card ${m.id === S.model ? "selected" : ""}" style="--tone:${TONE[m.id]}" data-model="${m.id}" aria-pressed="${m.id === S.model}">
      <div class="name">${esc(shortName(m.display_name))}${running === m.id ? '<span class="badge live">Running</span>' : m.default ? '<span class="badge">Default</span>' : ""}</div>
      <div class="guide">${esc(GUIDE[m.id] || "")}</div>
      <div class="meta">${[speed, m.memory_plan.planned_gib.toFixed(1) + " GiB memory", m.context_label + " context"].filter(Boolean).join(" · ")}</div>
    </button>`;
  }).join("");
  const chips = model.reasoning_efforts.map((e) => `<button class="chip ${e === S.effort ? "selected" : ""}" data-effort="${e}" aria-pressed="${e === S.effort}">${e.toUpperCase()}</button>`).join("");
  const steps = (S.state.lifecycle?.steps || []).map((s) => `<li class="${s.error ? "error" : ""}">${new Date(s.t * 1000).toLocaleTimeString()}  ${esc(s.text)}</li>`).join("");
  const permissions = S.state.permissions || {};
  // Switching stops the running model first, so its planned memory counts as available for the target.
  const freed = running && !isRunning ? (S.state.requirements?.[running]?.planned_gib || 0) : 0;
  const short = available != null && !isRunning && available + freed < requirement.required_gib;
  const label = shortName(profile.label);
  const action = isActive
    ? `<button class="ghost" id="open-zed">Open in Zed</button><button class="danger" id="stop" ${busy ? "disabled" : ""}>Stop</button>`
    : isRunning
      ? `<button class="primary" id="launch" ${busy ? "disabled" : ""}>${busy ? "Working…" : "Use " + esc(label)}</button><button class="danger" id="stop" ${busy ? "disabled" : ""}>Stop</button>`
      : `<button class="primary" id="launch" ${busy ? "disabled" : ""}>${busy ? "Working…" : (running ? "Switch to " : "Start ") + esc(label)}</button>`;
  const consequence = isActive ? "This profile is running."
    : isRunning ? "Same model: changes the reasoning level without a restart."
    : running ? `Stops ${esc(shortName(modelOf(running).display_name))}, then loads ${esc(shortName(model.display_name))} (about ${Math.round(S.catalog.details[S.model].measured?.start_s || 25)} s).`
    : `Loads ${esc(shortName(model.display_name))} in about ${Math.round(S.catalog.details[S.model].measured?.start_s || 25)} s.`;
  root.innerHTML = `
    <div class="page-head"><h1>Run</h1><span class="spacer"></span>${runningLine()}</div>
    <div class="grid cols-3">${cards}</div>
    <div class="panel section" style="--tone:${TONE[S.model]}">
      <div class="row">
        <div><h2>Reasoning</h2><div class="chips">${chips}</div></div>
        <div class="spacer"></div>
        <div class="row" style="gap:8px">${action}</div>
      </div>
      <p class="muted small" style="margin:var(--sp-3) 0 0">${consequence}</p>
      ${stageBar()}
      ${S.pendingSwitch ? switchSummary(profile, running, requirement, available, freed) : ""}
      ${short ? `<div class="notice" style="margin-top:14px">${esc(shortName(model.display_name))} needs about ${requirement.required_gib.toFixed(1)} GiB available for its ${model.context_label} context; ${available.toFixed(1)} GiB is available now${freed ? ` (${(available + freed).toFixed(1)} GiB after stopping the running model)` : ""}.</div>` : ""}
      <hr class="divider">
      <div class="metrics" id="run-stats"></div>
    </div>
    <div class="grid cols-2 section">
      <div class="panel"><h2>Runtime</h2>
        <dl class="kv">
          <dt>Status</dt><dd>${isRunning ? (state.healthy ? '<span class="status ok">Healthy</span>' : '<span class="status bad">Not healthy</span>') : '<span class="status">Stopped</span>'}
            ${isRunning && activeProfile && !isActive ? `<span class="muted small"> · running as ${esc(profileLabel(activeProfile))}</span>` : ""}
            ${running && !isRunning ? `<span class="muted small"> · ${esc(shortName(modelOf(running).display_name))} is running</span>` : ""}</dd>
          <dt>${isRunning ? "Process" : "Endpoint"}</dt><dd class="mono">${isRunning ? "pid " + state.pid + " · " : ""}${esc((state.endpoint || "–").replace("http://", ""))}</dd>
          <dt>Load time</dt><dd>${isRunning && state.load_seconds != null ? state.load_seconds.toFixed(1) + " s" : "–"}</dd>
          <dt>Memory</dt><dd>${requirement.planned_gib.toFixed(1)} GiB planned · start needs ${requirement.required_gib.toFixed(1)} GiB available</dd>
          <dt>Draft</dt><dd>${esc(model.quantization.speculative || "DFlash2 draft model")}</dd>
        </dl>
        ${steps ? `<details class="disclosure" style="margin-top:var(--sp-3)"><summary class="small">Last switch</summary><ul class="steps" style="margin-top:6px">${steps}</ul></details>` : ""}
      </div>
      <div class="panel"><h2>Agent</h2>
        <dl class="kv">
          <dt>In Zed</dt><dd>Agent panel → + → <b>${esc(profile.label)}</b></dd>
          <dt>Launcher</dt><dd class="mono">dwarfstar-acp --profile ${esc(profile.id)}</dd>
          <dt>Tools</dt><dd>Workspace, shell, browser, computer use, vision</dd>
          <dt>Tool approval</dt><dd><b>${esc(permissions.label || "–")}</b> · <a href="#settings" id="run-approval">Change</a></dd>
        </dl>
        <p class="muted small" style="margin:var(--sp-3) 0 0">Each reasoning level is its own agent entry; choosing another level here does not restart the model.</p>
      </div>
    </div>`;
  root.querySelectorAll("[data-model]").forEach((b) => b.onclick = () => selectModel(b.dataset.model));
  root.querySelectorAll("[data-effort]").forEach((b) => b.onclick = () => { S.effort = b.dataset.effort; S.pendingSwitch = null; rememberEffort(); api("/api/select", { profile: profileOf(S.model, S.effort).id }); renderRun(); });
  const launch = $("#launch", root), stop = $("#stop", root), zed = $("#open-zed", root);
  if (launch) launch.onclick = () => {
    // Pre-switch summary whenever something would be interrupted: another model, or chats still answering.
    const interrupts = (running && running !== S.model) || (S.state.answering_chats || []).length;
    if (interrupts && !S.pendingSwitch) { S.pendingSwitch = profile.id; renderRun(); return; }
    S.pendingSwitch = null; lifecycle("/api/switch", { profile: profile.id });
  };
  const confirmSwitch = $("#switch-confirm", root), cancelSwitch = $("#switch-cancel", root);
  if (confirmSwitch) confirmSwitch.onclick = () => { S.pendingSwitch = null; lifecycle("/api/switch", { profile: profile.id }); };
  if (cancelSwitch) cancelSwitch.onclick = () => { S.pendingSwitch = null; renderRun(); };
  if (stop) stop.onclick = () => lifecycle("/api/stop", {});
  if (zed) zed.onclick = async () => { const r = await api("/api/launch", { profile: profile.id }); toast(r.ok ? r.instruction : r.error); };
  $("#run-approval", root).onclick = (event) => { event.preventDefault(); show("settings"); };
  renderRunStats();
}
async function setApproval(mode, confirm) {
  const result = await api("/api/permissions", { mode, ...(confirm ? { confirm } : {}) });
  S.confirmFullAuto = false;
  toast(result.ok ? `Tool approval: ${result.permissions.label}` : result.error);
  await refreshState(); S.renderedKey = null; if (S.view === "settings") renderSettings(); else renderRun();
}
/* Switch stages, from the service's step records: the current one pulses while a job runs. */
const STAGES = [["stop", "Stop current"], ["memory", "Memory check"], ["load", "Load weights"], ["health", "Health check"], ["ready", "Ready"]];
function stageBar() {
  const job = S.state.lifecycle;
  if (!job || job.kind !== "switch" || !(job.steps || []).length) return "";
  const seen = new Set(job.steps.map((s) => s.stage).filter(Boolean)), failed = job.steps.some((s) => s.error);
  const current = [...job.steps].reverse().find((s) => s.stage)?.stage;
  const startedStep = job.steps.find((s) => s.stage === "load" && !s.load_seconds);
  const expected = S.catalog.details[S.model]?.measured?.cold_load_s;
  const elapsed = job.busy && startedStep ? Date.now() / 1000 - startedStep.t : null;
  if (!job.busy && !failed && Date.now() / 1000 - (job.finished || 0) > 30) return "";  // the step list below keeps the record
  return `<ol class="stages ${failed ? "failed" : ""}" aria-label="Switch progress">${STAGES.filter(([id]) => id !== "stop" || seen.has("stop")).map(([id, label]) => {
    const state = id === current && job.busy ? "active" : seen.has(id) ? (failed && id === current ? "error" : "done") : "";
    const extra = id === "load" && elapsed != null ? ` · ${Math.round(elapsed)} s${expected ? " of ~" + Math.round(expected) + " s" : ""}` : "";
    return `<li class="${state}">${label}${extra}</li>`;
  }).join("")}</ol>`;
}
function switchSummary(profile, running, requirement, available, freed) {
  const answering = (S.state.answering_chats || []).length;
  const runningLabel = running ? (profileLabel(S.state.active_profile) || modelOf(running).display_name) : null;
  return `<div class="notice switch-summary" style="margin-top:14px"><b>Before switching to ${esc(profile.label.replace("DwarfStar ", ""))}</b>
    <ul>${running && running !== S.model ? `<li>${esc(runningLabel.replace("DwarfStar ", ""))} will be stopped (pid ${S.state.models[running].pid}); Zed sessions on it lose their model until you switch back.</li>` : ""}
      ${answering ? `<li>${answering} chat${answering === 1 ? " is" : "s are"} still answering and will be interrupted.</li>` : ""}
      <li>Needs ${requirement.required_gib.toFixed(1)} GiB available${available != null ? `; ${(available + freed).toFixed(1)} GiB will be` : ""}.</li></ul>
    <div class="row"><button class="primary small-button" id="switch-confirm">Switch now</button><button class="ghost small-button" id="switch-cancel">Cancel</button></div></div>`;
}
/* The reasoning level last used with each model is remembered on this Mac. */
function rememberEffort() { try { const all = JSON.parse(localStorage.getItem("dwarfstar.effort") || "{}"); all[S.model] = S.effort; localStorage.setItem("dwarfstar.effort", JSON.stringify(all)); } catch {} }
function rememberedEffort(id) { try { return JSON.parse(localStorage.getItem("dwarfstar.effort") || "{}")[id]; } catch { return null; } }
function selectModel(id) {
  S.model = id; S.pendingSwitch = null;
  const efforts = modelOf(id).reasoning_efforts, remembered = rememberedEffort(id);
  S.effort = efforts.includes(remembered) ? remembered : efforts.includes(S.effort) ? S.effort : efforts.includes("low") ? "low" : efforts[0];
  renderRun();
}
/* The service owns the lifecycle. The UI submits a job, then follows the service's record of that job,
 * so a slow model start can never time out a request and leave the window disagreeing with the controller. */
async function lifecycle(path, body) {
  S.busy = true; renderRun();
  try {
    const accepted = await api(path, { ...body, wait: false });
    if (!accepted.ok) { toast(accepted.error || "Request refused"); return; }
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, 700));
      await refreshState();
      const bar = document.querySelector("#view-run .stages");
      if (bar) bar.outerHTML = stageBar() || "<span></span>";  // keep the load timer moving between steps
      const job = S.state?.lifecycle;
      if (!job || job.job > accepted.job) break;
      if (job.job === accepted.job && !job.busy) {
        S.pendingSwitch = null;
        toast(job.result?.ok ? (path.endsWith("stop") ? "Model stopped" : "Ready") : (job.result?.error || "Failed"));
        break;
      }
    }
  } catch {
    toast("Lost contact with the DwarfStar service; showing its current state.");
  } finally { S.busy = false; S.renderedKey = null; await refreshState(); refreshLiveSoon(); }
}
function fmtDuration(seconds) { const s = Math.round(seconds); return s < 90 ? s + " s" : s < 5400 ? Math.round(s / 60) + " min" : (s / 3600).toFixed(1) + " h"; }
function renderRunStats() {
  const node = $("#run-stats"); if (!node || !S.catalog) return;
  const measured = S.catalog.details[S.model].measured || {};
  const since = runningModel() === S.model ? S.state?.models?.[S.model]?.splash?.since_start : null;
  const live = since && since.decode_output_tokens > 0 ? since : null;  // nothing measured yet: show the catalog's measurement
  const memory = S.system?.memory;
  const headroom = memory?.available_bytes != null ? memory.available_bytes / 2 ** 30 : null;
  const metric = (label, value, sub) => `<div class="metric"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;
  node.innerHTML = [
    metric("Decode", num(live?.decode_tokens_per_second ?? measured.decode_tok_s, 1, "tok/s"), live?.decode_tokens_per_second != null ? "this run" : "measured, short prompt"),
    metric("First token", num(measured.ttft_s, 2, "s"), "measured, short prompt"),
    metric("Prefill", num(live?.prefill_tokens_per_second ?? measured.prefill_tok_s, 0, "tok/s"), live?.prefill_tokens_per_second != null ? "this run" : "measured, 8K prompt"),
    metric("Context", `${S.catalog.models.find((m) => m.id === S.model).context_label}`, "up to " + ktok(modelOf(S.model).max_output) + " output"),
    metric("Memory free", num(headroom, 1, "GiB"), memory?.swap_used_bytes ? "swap " + (memory.swap_used_bytes / 2 ** 30).toFixed(1) + " GiB" : "no swap"),
  ].join("");
}

/* ----------------------------------------------------------------- models */
function renderModels() {
  const root = $("#view-models"); if (!S.catalog) return;
  S.modelTab = S.modelTab || S.model;
  const m = modelOf(S.modelTab), d = S.catalog.details[S.modelTab], measured = d.measured || {};
  const assets = d.assets, total = m.disk_bytes;
  const order = [["main", "Model, part 1"], ["ple", "PLE table, part 2"], ["draft", "Draft model"], ["vision", "Vision projector"]];
  const colors = { main: TONE[m.id], ple: "#3d6f8f", draft: "#6fb59a", vision: "#c98a8a" };
  const plan = m.memory_plan, limit = plan.metal_working_set_gib;
  const state = S.state?.models[m.id];
  const splash = state?.running ? (state.splash || {}) : {};
  const memParts = [["Resident model", plan.resident_model_gib, TONE[m.id]], ["KV cache", plan.kv_gib, "#3f8f73"], ["Graph buffers", plan.buffers_gib, "#c9a75a"], ["Vision projector", plan.projector_gib || 0, "#c98a8a"]];
  const availableNow = S.system?.memory?.available_bytes != null ? S.system.memory.available_bytes / 2 ** 30 : null;
  const quality = (measured.quality || []).map((q) => `<tr><td>${esc(q.name)}</td><td>${esc(q.result)}</td><td class="muted">${esc(q.note || "")}</td></tr>`).join("");
  const metric = (label, value, sub = "") => `<div class="metric"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;
  const policy = S.catalog.policy;
  root.innerHTML = `
    <div class="page-head"><h1>Models</h1><div class="seg" role="tablist">${S.catalog.models.map((x) => `<button class="chip ${x.id === S.modelTab ? "selected" : ""}" data-tab="${x.id}" role="tab">${esc(shortName(x.display_name))}</button>`).join("")}</div></div>
    <div class="panel" style="--tone:${TONE[m.id]}">
      <div class="row" style="align-items:baseline"><h2 style="margin:0;font-size:15px">${esc(m.display_name)}</h2>${m.default ? '<span class="badge">Default</span>' : ""}${state?.running ? '<span class="badge live">Running</span>' : ""}
        <span class="muted">${esc(GUIDE[m.id] || "")}</span></div>
      <p class="muted small" style="margin:6px 0 var(--sp-4)">${esc(m.summary)}</p>
      <div class="metrics">
        ${metric("Decode", measured.decode_range_tok_s ? measured.decode_range_tok_s.map((v) => Math.round(v)).join("–") + "<small>tok/s</small>" : NA, "depends on draft acceptance")}
        ${metric("First token", num(measured.ttft_s, 2, "s"), "short prompt")}
        ${metric("Prefill", num(measured.prefill_tok_s, 0, "tok/s"), measured.prefill_32k_tok_s ? `8K prompt · ${measured.prefill_32k_tok_s} at 32K` : "8K prompt")}
        ${metric("Start", num(measured.start_s, 0, "s"), "idle to ready")}
        ${metric("Memory", plan.planned_gib.toFixed(1) + "<small>GiB</small>", measured.peak_wired_gib ? `peak ${measured.peak_wired_gib.toFixed(1)} GiB wired` : "planned")}
        ${metric("Reasoning", m.reasoning_efforts.length + (m.reasoning_efforts.length === 1 ? "<small>level</small>" : "<small>levels</small>"), m.reasoning_efforts.map((e) => e.toUpperCase()).join(" · "))}
      </div>
      <p class="faint small" style="margin:var(--sp-3) 0 0">Measured on this Mac with Splash-m1. ${esc(measured.evidence?.["speed and memory"] ? "Source: " + measured.evidence["speed and memory"].split("(")[0].trim() + "." : "")}</p>
    </div>
    <div class="grid cols-2 section">
      <div class="panel"><h2>Identity</h2><dl class="kv">
        <dt>Architecture</dt><dd>${esc(d.architecture.name)}<div class="muted small">${d.architecture.layers} layers · ${d.architecture.experts_per_layer} experts per layer, ${d.architecture.experts_active} active per token</div></dd>
        <dt>Source</dt><dd class="mono">${esc(d.source.repository)}<div class="faint">${esc(String(d.source.revision).slice(0, 12))}</div></dd>
        <dt>Derivation</dt><dd>${esc(d.source.derivation || "")}</dd>
        <dt>Context</dt><dd>${m.context.toLocaleString()} tokens · tested to ${measured.tested_context_tokens != null ? measured.tested_context_tokens.toLocaleString() : "–"} · output up to ${m.max_output.toLocaleString()}</dd>
      </dl></div>
      <div class="panel"><h2>Quantization</h2><dl class="kv">
        <dt>Routed experts</dt><dd>${esc(m.quantization.experts)}</dd>
        <dt>Dense trunk</dt><dd>${esc(m.quantization.dense)}</dd>
        <dt>PLE</dt><dd>${esc(m.quantization.ple)}</dd>
        <dt>Vision</dt><dd>${esc(m.quantization.vision)}</dd>
        <dt>Draft</dt><dd>${esc(m.quantization.speculative || "")}${splash.since_start?.draft_acceptance_rate != null ? ` · <b>${Math.round(100 * splash.since_start.draft_acceptance_rate)} %</b> accepted this run` : ""}</dd>
        <dt>KV cache</dt><dd>${esc(m.cache.k_cache_dtype)} K and V · ${(m.cache.kv_bytes_per_token / 1024).toFixed(1)} KiB per token</dd>
      </dl></div>
      <div class="panel"><h2>Memory <span class="muted small">of the ${limit.toFixed(2)} GiB Metal working set</span></h2>
        <div class="bar">${memParts.map(([, g, c]) => `<span style="width:${100 * g / limit}%;background:${c}"></span>`).join("")}</div>
        <div class="legend">${memParts.map(([l, g, c]) => `<span><i style="background:${c}"></i>${l} ${g.toFixed(2)} GiB</span>`).join("")}</div>
        <dl class="kv" style="margin-top:var(--sp-3)">
          <dt>Planned</dt><dd><b>${plan.planned_gib.toFixed(2)} GiB</b> · ${plan.metal_headroom_gib.toFixed(2)} GiB headroom</dd>
          <dt>Start needs</dt><dd>${plan.required_gib.toFixed(1)} GiB available${availableNow != null ? ` · ${availableNow.toFixed(1)} GiB available now` : ""}</dd>
        </dl>
      </div>
      <div class="panel"><h2>Storage <span class="muted small">${gib(total)} on disk</span></h2>
        <div class="bar">${order.map(([k]) => `<span style="width:${100 * assets[k].bytes / total}%;background:${colors[k]}"></span>`).join("")}</div>
        <div class="legend">${order.map(([k, label]) => `<span><i style="background:${colors[k]}"></i>${label} ${gib(assets[k].bytes)}</span>`).join("")}</div>
        <p class="muted small" style="margin:var(--sp-3) 0 0">The model, draft and vision projector stay in unified memory; the PLE table stays on disk and is read row by row.</p>
        <details class="disclosure" style="margin-top:var(--sp-3)"><summary class="small">Files and hashes</summary>
          <table class="files" style="margin-top:var(--sp-2)"><tbody>${order.map(([k, label]) => `<tr><td class="muted">${label}</td><td class="mono small">${esc(assets[k].file)}<div class="faint">${esc(assets[k].sha256)}</div></td></tr>`).join("")}</tbody></table></details>
      </div>
    </div>
    <div class="panel section"><h2>Measured quality</h2>
      ${quality ? `<table><thead><tr><th>Measurement</th><th>Result</th><th>Notes</th></tr></thead><tbody>${quality}</tbody></table>` : `<p class="muted" style="margin:0">No results recorded.</p>`}
    </div>
    <details class="disclosure section"><summary>Runtime policy and capabilities</summary>
      <div class="panel" style="margin-top:var(--sp-2)"><dl class="kv">
        <dt>Runtime</dt><dd>${esc(policy.model_defaults.runtime || policy.runtime || "")} for every model (policy ${esc(policy.version)})</dd>
        <dt>Shared settings</dt><dd>${policy.model_defaults.context / 1024}K context · ${esc(policy.model_defaults.cache.k_cache_dtype)} KV · output ${policy.model_defaults.max_output.toLocaleString()} · temperature ${policy.model_defaults.sampling.temperature}, top-p ${policy.model_defaults.sampling.top_p}, top-k ${policy.model_defaults.sampling.top_k}</dd>
        <dt>This model</dt><dd>${Object.keys(m.overrides || {}).length ? Object.entries(m.overrides).map(([k, o]) => `${esc(k)} ${esc(String(o.value))}: ${esc(o.reason)}`).join("<br>") : "No overrides"} · port ${m.port}</dd>
        <dt>KV policy</dt><dd class="small">${esc(m.cache.policy)}</dd>
        <dt>Capabilities</dt><dd>${m.capabilities.map((c) => `<span class="cap">${esc(CAPABILITY[c] || c)}</span>`).join("")}</dd>
      </dl></div></details>`;
  root.querySelectorAll("[data-tab]").forEach((b) => b.onclick = () => { S.modelTab = b.dataset.tab; renderModels(); });
}
const CAPABILITY = { chats: "Workstation chats", acp: "ACP agent", full_auto: "Full Auto", native_tool_calls: "Native tool calls", workspace: "Workspace files", shell: "Shell", browser: "Browser automation", computer_use: "Computer use (KimiCU)", vision: "Vision", speculative_decoding: "Speculative decoding", compaction: "Compaction", recovery: "Recovery", telemetry: "Telemetry" };

/* ------------------------------------------------------------------- live */
function sparkline(canvas, values, color, max) {
  if (!canvas) return;
  const ratio = devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== w * ratio) { canvas.width = w * ratio; canvas.height = h * ratio; }
  const g = canvas.getContext("2d"); g.setTransform(ratio, 0, 0, ratio, 0, 0); g.clearRect(0, 0, w, h);
  const points = values.filter((v) => v != null); if (points.length < 2) return;
  const top = max || Math.max(...points) * 1.15 || 1;
  g.beginPath();
  values.forEach((v, i) => { if (v == null) return; const x = i / (values.length - 1) * w, y = h - 3 - (v / top) * (h - 8); i ? g.lineTo(x, y) : g.moveTo(x, y); });
  g.strokeStyle = color; g.lineWidth = 1.6; g.stroke();
  g.lineTo(w, h); g.lineTo(0, h); g.closePath(); g.globalAlpha = .12; g.fillStyle = color; g.fill(); g.globalAlpha = 1;
}
/* Live: what is happening now, from the runtime's own counters (Splash /status) and the agent's request receipts.
 * Standard view here; the Advanced view is the Visualizer. Nothing is estimated: what Splash does not report says so. */
function liveModeSwitch(current) {
  return `<div class="seg" role="tablist" aria-label="Live view">
    <button class="chip ${current === "live" ? "selected" : ""}" data-live-mode="live" role="tab">Standard</button>
    <button class="chip ${current === "visualizer" ? "selected" : ""}" data-live-mode="visualizer" role="tab">Advanced</button></div>`;
}
function bindLiveModes(root) { root.querySelectorAll("[data-live-mode]").forEach((b) => b.onclick = () => show(b.dataset.liveMode)); }
function renderLive() {
  const root = $("#view-live"); if (!S.catalog) return;
  const running = runningModel(), live = S.live || {}, request = live.request || {}, rates = live.rates || {};
  const model = running ? modelOf(running) : null, system = S.system || {}, memory = system.memory || {}, gpu = system.gpu || {}, process = system.process || {};
  const splash = running ? (S.state?.models?.[running]?.splash || {}) : {};
  if (!root.dataset.built) {
    root.dataset.built = "1";
    root.innerHTML = `<div class="page-head"><h1>Live</h1>${liveModeSwitch("live")}<span class="spacer"></span><span id="live-status"></span></div>
      <div id="live-body"></div>`;
    bindLiveModes(root);
  }
  $("#live-status").innerHTML = runningLine();
  const body = $("#live-body");
  if (!running) {
    body.dataset.mode = "empty";
    body.innerHTML = `<div class="panel empty"><b>No model running.</b> Start one and its measurements appear here as it serves requests.
      <div style="margin-top:var(--sp-3)"><button class="ghost small-button" id="live-run">Open Run</button></div></div>`;
    $("#live-run").onclick = () => show("run");
    return;
  }
  if (body.dataset.mode !== "running") {
    body.dataset.mode = "running";
    body.innerHTML = `
      <div class="panel"><h2>Current request <span class="muted small" id="live-phase"></span></h2><div class="metrics" id="live-request"></div></div>
      <div class="panel section"><h2>Since the model started <span class="faint small">measured by Splash</span></h2><div class="metrics" id="live-runtime"></div></div>
      <div class="grid cols-2 section">
        <div class="panel"><h2>Context</h2><div id="live-context"></div></div>
        <div class="panel"><h2>Memory</h2><div id="live-memory"></div></div>
        <div class="panel"><h2>Processor</h2><canvas class="spark" id="spark-gpu"></canvas><div class="legend" id="gpu-legend"></div></div>
        <div class="panel"><h2>Tool activity</h2><div class="timeline" id="live-tools"></div></div>
      </div>
      <p class="faint small section">Not reported by Splash: router selections, expert activity and PLE reads. The Advanced view labels these regions instead of drawing estimates.</p>`;
  }
  const metric = (label, value, sub = "") => `<div class="metric"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;
  const ms = (v) => v == null ? NA : v >= 1000 ? (v / 1000).toFixed(2) + "<small>s</small>" : Math.round(v) + "<small>ms</small>";
  const sched = splash.scheduler || {};
  const last = S.state?.models?.[running]?.last_request || {};
  const now = sched.decoding ? "decoding" : sched.prefilling ? "prefilling" : sched.queued ? "queued" : request.active ? String(request.phase || "").replace("_", " ").toLowerCase() : "idle";
  $("#live-phase").textContent = "· " + (now === "idle" && last.started_at ? "idle · latest at " + new Date(last.started_at * 1000).toLocaleTimeString() : now);
  // The latest request as the agent recorded it (its own receipt: API usage and stream timing), else the telemetry hub's.
  const prompt = last.input_tokens ?? request.prompt_tokens, reused = last.reused_tokens ?? request.reused_tokens;
  const output = last.output_tokens ?? request.generated, decode = last.provider_decode_tps ?? rates.live_tps ?? rates.decode_tps;
  $("#live-request").innerHTML = [
    metric("Prompt", prompt != null ? prompt.toLocaleString() + "<small>tokens</small>" : "–", reused ? reused.toLocaleString() + " reused from the prefix cache" : ""),
    metric("New prefill", last.new_prefill_tokens != null ? last.new_prefill_tokens.toLocaleString() + "<small>tokens</small>" : "–", ""),
    metric("First token", last.ttft_s != null ? last.ttft_s.toFixed(2) + "<small>s</small>" : "–", ""),
    metric("Output", output != null ? output.toLocaleString() + "<small>tokens</small>" : "–", last.outcome ? "ended: " + esc(last.outcome) : ""),
    metric("Decode", decode != null ? num(decode, 1, "tok/s") : "–", last.provider_decode_tps != null ? "tokens over stream time" : ""),
  ].join("");
  const since = splash.since_start || {}, req = splash.requests || {}, cache = splash.prefix_cache || {};
  $("#live-runtime").innerHTML = splash.source ? [
    metric("Requests", (req.completed ?? 0).toLocaleString(), `${splash.active_requests || 0} active${req.failed ? ` · <span style="color:var(--bad)">${req.failed} failed</span>` : ""}${req.cancelled ? ` · ${req.cancelled} cancelled` : ""}`),
    metric("First token", ms(splash.ttft_ms?.p50), splash.ttft_ms?.p95 != null ? "median · p95 " + (splash.ttft_ms.p95 / 1000).toFixed(1) + " s" : "median"),
    metric("Between tokens", ms(splash.inter_token_ms?.p50), splash.inter_token_ms?.p95 != null ? "median · p95 " + Math.round(splash.inter_token_ms.p95) + " ms" : "median"),
    metric("Prefill", num(since.prefill_tokens_per_second, 0, "tok/s"), (since.prefill_input_tokens ?? 0).toLocaleString() + " tokens"),
    metric("Decode", num(since.decode_tokens_per_second, 1, "tok/s"), (since.decode_output_tokens ?? 0).toLocaleString() + " tokens"),
    metric("Draft accepted", since.draft_acceptance_rate != null ? Math.round(100 * since.draft_acceptance_rate) + "<small>%</small>" : NA, since.drafted_tokens != null ? `${since.accepted_draft_tokens} of ${since.drafted_tokens} drafted` : ""),
    metric("Prefix cache", cache.hit_rate != null ? Math.round(100 * cache.hit_rate) + "<small>% hits</small>" : NA, cache.reused_tokens != null ? cache.reused_tokens.toLocaleString() + " tokens reused" : ""),
  ].join("") : `<span class="muted">Waiting for the runtime's first status report…</span>`;
  const capacity = model.context, used = (prompt ?? 0) + (output ?? 0);
  $("#live-context").innerHTML = `
    <div class="bar"><span style="width:${100 * (prompt ?? 0) / capacity}%;background:var(--accent)"></span><span style="width:${100 * (output ?? 0) / capacity}%;background:var(--good)"></span></div>
    <div class="legend"><span><i style="background:var(--accent)"></i>prompt ${(prompt ?? 0).toLocaleString()}</span><span><i style="background:var(--good)"></i>generated ${(output ?? 0).toLocaleString()}</span>
      <span>${used.toLocaleString()} of ${capacity.toLocaleString()} tokens, latest request</span></div>
    <dl class="kv" style="margin-top:var(--sp-3)"><dt>KV cache</dt><dd>${esc(model.cache.k_cache_dtype)} · ${(model.cache.kv_bytes_per_token / 1024).toFixed(1)} KiB per token · ${(used * model.cache.kv_bytes_per_token / 2 ** 30).toFixed(2)} GiB for this request</dd></dl>`;
  const total = memory.total_bytes || 1;
  $("#live-memory").innerHTML = memory.total_bytes ? `
    <div class="bar"><span style="width:${100 * memory.wired_bytes / total}%;background:var(--accent)"></span><span style="width:${100 * memory.active_bytes / total}%;background:#4f8f7a"></span><span style="width:${100 * (memory.compressed_bytes || 0) / total}%;background:#8a7a4a"></span></div>
    <div class="legend"><span><i style="background:var(--accent)"></i>wired ${gib(memory.wired_bytes, 1)}</span><span><i style="background:#4f8f7a"></i>active ${gib(memory.active_bytes, 1)}</span><span><i style="background:#8a7a4a"></i>compressed ${gib(memory.compressed_bytes, 1)}</span></div>
    <dl class="kv" style="margin-top:var(--sp-3)"><dt>Available</dt><dd>${gib(memory.available_bytes, 1)} of ${gib(memory.total_bytes, 0)}${memory.swap_used_bytes ? " · swap " + gib(memory.swap_used_bytes, 2) : ""}</dd>
      <dt>Runtime</dt><dd>${splash.memory_bytes?.current_bytes != null ? gib(splash.memory_bytes.current_bytes, 1) + " in use · peak " + gib(splash.memory_bytes.peak_bytes, 1) : NA}${splash.memory_pressure && splash.memory_pressure !== "normal" ? ` · <span style="color:var(--warn)">pressure ${esc(splash.memory_pressure)}</span>` : ""}</dd>
      <dt>Thermal</dt><dd>${splash.thermal_state ? (splash.thermal_state === "nominal" ? "nominal" : `<span style="color:var(--warn)">${esc(splash.thermal_state)}</span>`) : NA}</dd></dl>` : `<span class="muted">Waiting for the first sample…</span>`;
  sparkline($("#spark-gpu"), S.series.gpu, "#b98cff", 100);
  $("#gpu-legend").innerHTML = `<span><i style="background:var(--cyber)"></i>GPU ${gpu.utilization_pct ?? "–"} %</span><span>model process CPU ${process.cpu_pct ?? "–"} %</span>${splash.scheduler ? `<span>scheduler: ${splash.scheduler.decoding || 0} decoding · ${splash.scheduler.prefilling || 0} prefilling · ${splash.scheduler.queued || 0} queued</span>` : ""}`;
  const tools = [...(live.tools?.timeline || [])].slice(-40).reverse();
  $("#live-tools").innerHTML = (live.tools?.active ? [`<div class="item"><span class="muted">running</span><span class="tag ${esc(live.tools.active.category)}">${esc(live.tools.active.category)}</span><b>${esc(live.tools.active.name)}</b><span></span><span>…</span></div>`] : [])
    .concat(tools.map((t) => `<div class="item"><span class="muted">${new Date(t.t * 1000).toLocaleTimeString()}</span><span class="tag ${esc(t.category)}">${esc(t.category)}</span><span>${esc(t.name)}</span><span class="muted">${t.ms != null ? t.ms + " ms" : ""}</span><span style="color:${t.ok === false ? "var(--bad)" : "var(--faint)"}">${t.ok === false ? "failed" : ""}</span></div>`)).join("") || `<span class="muted">No tool calls yet.</span>`;
}

/* --------------------------------------------------------------- settings */
function renderSettings() {
  const root = $("#view-settings"); if (!S.state) return;
  const permissions = S.state.permissions || {};
  const roots = S.settingsRoots;
  root.innerHTML = `
    <div class="page-head"><h1>Settings</h1></div>
    <div class="panel"><h2>Tool approval <span class="muted small">for every profile, in Zed and in chats</span></h2>
      <div class="seg" role="radiogroup" aria-label="Tool approval">${(permissions.modes || []).map((m) => `<button class="chip ${m.id === permissions.approval_mode ? "selected" : ""}"
        data-approval="${m.id}" role="radio" aria-checked="${m.id === permissions.approval_mode}">${esc(m.label)}</button>`).join("")}</div>
      <dl class="kv" style="margin-top:var(--sp-4)">${(permissions.modes || []).map((m) => `<dt${m.id === permissions.approval_mode ? ' style="color:var(--text)"' : ""}>${esc(m.label)}</dt><dd class="${m.id === permissions.approval_mode ? "" : "muted"}">${esc(m.description)}</dd>`).join("")}</dl>
      <p class="faint small" style="margin:var(--sp-3) 0 0">Takes effect for new agent sessions and the next chat turn.</p>
      ${S.confirmFullAuto ? `<div class="notice" style="margin-top:var(--sp-3)"><b>Turn on Full Auto?</b> The agent will run shell commands, edit files anywhere,
        use the browser and control the computer without asking, outside the sandbox.
        <div class="row" style="margin-top:var(--sp-2)"><button class="danger small-button" id="full-auto-yes">Turn on Full Auto</button>
        <button class="ghost small-button" id="full-auto-no">Cancel</button></div></div>` : ""}
    </div>
    <div class="panel section"><h2>Zed</h2>
      <dl class="kv"><dt>Agent entries</dt><dd>${S.catalog.profiles.length} DwarfStar agents, one per model and reasoning level, launched with <span class="mono">bin/dwarfstar-acp</span></dd>
        <dt>Install or update</dt><dd class="mono">bin/dwarfstar zed --apply</dd>
        <dt>Shared conversations</dt><dd>Chats → Zed lists DwarfStar threads from Zed; a chat's menu has Open in Zed. One client writes to a conversation at a time.</dd></dl>
    </div>
    <div class="panel section"><h2>Data</h2>
      ${roots ? `<dl class="kv">${roots.map(([k, v]) => `<dt>${esc(k)}</dt><dd class="mono small">${esc(v)}</dd>`).join("")}</dl>` : `<span class="muted">Loading…</span>`}
      <p class="faint small" style="margin:var(--sp-3) 0 0">Disk use and safe cleanup: <span class="mono">bin/dwarfstar storage</span> and <span class="mono">bin/dwarfstar cache clean</span> (dry run unless --apply).</p>
    </div>`;
  root.querySelectorAll("[data-approval]").forEach((b) => b.onclick = () => {
    if (b.dataset.approval === permissions.approval_mode) return;
    if (b.dataset.approval === "full_auto") { S.confirmFullAuto = true; renderSettings(); return; }
    setApproval(b.dataset.approval);
  });
  const yes = $("#full-auto-yes", root), no = $("#full-auto-no", root);
  if (yes) yes.onclick = () => setApproval("full_auto", "full_auto");
  if (no) no.onclick = () => { S.confirmFullAuto = false; renderSettings(); };
  if (!roots) api("/api/diagnostics").then((d) => {
    const names = { code: "Code", assets: "Models and runtime", data: "Chats and settings", cache: "Cache", logs: "Logs" };
    S.settingsRoots = Object.entries(d.locations || {}).filter(([k]) => names[k]).map(([k, v]) => [names[k], v.replace(/^\/Users\/[^/]+/, "~")]);
    if (S.view === "settings") renderSettings();
  });
}

/* ------------------------------------------------------------------- boot */
async function boot() {
  document.querySelectorAll("#nav button[data-view]").forEach((b) => b.onclick = () => show(b.dataset.view));
  $("#palette-open").onclick = () => window.Palette?.open();
  $("#nav-status").onclick = () => show("run");
  try { [S.catalog, S.state] = await Promise.all([api("/api/catalog"), api("/api/state")]); }
  catch { setTimeout(boot, 1500); return; }
  const selected = S.catalog.profiles.find((p) => p.id === S.state.selected_profile) || S.catalog.profiles.find((p) => p.default);
  S.model = selected.model; S.effort = selected.reasoning_effort;
  connect(1);
  const initial = location.hash.slice(1);
  show(["run", "chats", "models", "live", "visualizer", "diagnostics", "settings"].includes(initial) ? initial : (S.state.running_models || []).length ? "chats" : "run");
  window.addEventListener("hashchange", () => { const v = location.hash.slice(1); if (v && v !== S.view && document.querySelector("#view-" + v)) show(v); });
  setInterval(refreshState, 4000);
  firstRun();
}
/* First run (or a broken installation): if any setup check blocks a start, say so once on every page. */
async function firstRun() {
  const setup = await api("/api/setup").catch(() => null);
  if (!setup?.ok || !setup.summary.error) return;
  const blocking = setup.checks.filter((c) => c.level === "error");
  const banner = el(`<div class="notice setup-banner" role="alert"><b>Setup needs attention:</b> ${blocking.length} check${blocking.length === 1 ? "" : "s"} would block a model start
    (${esc(blocking.slice(0, 3).map((c) => c.name).join(", "))}${blocking.length > 3 ? ", …" : ""}). <button class="ghost small-button">Open Diagnostics</button></div>`);
  banner.querySelector("button").onclick = () => show("diagnostics");
  $("#main").prepend(banner);
}
window.DwarfStar = { S, api, esc, modelOf, runningModel, TONE, show, toast, gib, runningLine, liveModeSwitch, bindLiveModes, profileLabel };
boot();
