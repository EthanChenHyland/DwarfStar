/* DwarfStar Workstation user interface.
 * Plain JavaScript, no dependencies, no network access beyond the local service. */
"use strict";

const S = {
  catalog: null, state: null, live: null, system: null, diagnostics: null,
  model: null, effort: null, view: "run", modelTab: null, busy: false,
  series: { tps: [], prefill: [], gpu: [], wired: [], ple: [], accept: [] },
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
const profileLabel = (id) => (S.catalog.profiles.find((p) => p.id === id)?.label || id).replace("DwarfStar ", "");
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
  $("#nav-model").textContent = running ? modelOf(running).display_name : "No model running";
  const phase = S.live?.request?.active ? S.live.request.phase : null;
  $("#nav-phase").textContent = busy ? "Switching…" : running ? (phase ? phase.replace("_", " ").toLowerCase() : state.healthy ? "Ready" : "Not healthy") : (S.connected ? "Offline" : "Connecting…");
}
function show(view) {
  S.view = view;
  if (location.hash.slice(1) !== view) history.replaceState(null, "", "#" + view);
  document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + view));
  connect(view === "visualizer" ? 2 : 1);
  if (view !== "chats") window.Chats?.unmount();
  ({ run: renderRun, chats: () => window.Chats?.mount(), models: renderModels, live: renderLive, visualizer: () => window.Visualizer?.mount(), settings: () => window.Diagnostics?.mount() })[view]?.();
  if (view !== "visualizer") window.Visualizer?.unmount();
  $("#main").scrollTop = 0;
}

/* -------------------------------------------------------------------- run */
function renderRun() {
  const root = $("#view-run");
  if (!S.catalog || !S.state) { root.innerHTML = "<h1>Run</h1><p class='lead'>Connecting to the local service…</p>"; return; }
  const model = modelOf(S.model), running = runningModel(), state = S.state.models[S.model];
  const profile = profileOf(S.model, S.effort);
  const isRunning = running === S.model, busy = S.state.lifecycle?.busy || S.busy;
  const activeProfile = S.state.active_profile, isActive = isRunning && activeProfile === profile.id;
  const requirement = S.state.requirements[S.model];
  const available = S.system?.memory?.available_bytes ? S.system.memory.available_bytes / 2 ** 30 : null;
  const cards = S.catalog.models.map((m) => `
    <button class="model-card ${m.id === S.model ? "selected" : ""}" style="--tone:${TONE[m.id]}" data-model="${m.id}" aria-pressed="${m.id === S.model}">
      <div class="name">${esc(m.display_name.replace("DwarfStar ", ""))}
        ${m.default ? '<span class="badge default">Default</span>' : ""}${running === m.id ? '<span class="badge live">Running</span>' : ""}</div>
      <div class="meta">${esc(m.quantization.label)} · ${m.context_label} context · ${(m.disk_bytes / 2 ** 30).toFixed(0)} GiB on disk</div>
      <div class="meta">${esc(m.summary)}</div>
    </button>`).join("");
  const chips = model.reasoning_efforts.map((e) => `<button class="chip ${e === S.effort ? "selected" : ""}" data-effort="${e}" aria-pressed="${e === S.effort}">${e.toUpperCase()}</button>`).join("");
  const overrideNote = Object.entries(model.overrides || {}).map(([k, o]) => `${k === "prefill_chunk" ? "Prefill chunk" : k} ${o.value} instead of the shared ${o.default}: ${o.reason}`).join(" ");
  const steps = (S.state.lifecycle?.steps || []).map((s) => `<li class="${s.error ? "error" : s.ready ? "ready" : ""}">${new Date(s.t * 1000).toLocaleTimeString()}  ${esc(s.text)}</li>`).join("");
  const permissions = S.state.permissions || {};
  // Switching stops the running model first, so its planned memory counts as available for the target.
  const freed = running && !isRunning ? (S.state.requirements?.[running]?.planned_gib || 0) : 0;
  const short = available != null && !isRunning && available + freed < requirement.required_gib;
  root.innerHTML = `
    <h1>Run</h1>
    <p class="lead">Choose a model and a reasoning level. One model runs at a time; switching stops the current one first.</p>
    <h2>Model</h2>
    <div class="grid cols-3" style="margin-bottom:18px">${cards}</div>
    <div class="panel" style="--tone:${TONE[S.model]}">
      <div class="row">
        <div><h2>Reasoning</h2><div class="chips">${chips}</div></div>
        <div style="margin-left:26px"><h2>Context</h2><div style="font-size:17px;font-weight:600">${model.context_label}
          <span class="muted small" style="font-weight:400">· up to ${ktok(model.max_output)} output</span></div></div>
        <div class="spacer"></div>
        ${isActive
          ? `<button class="ghost" id="open-zed">Open a workspace in Zed</button><button class="danger" id="stop" ${busy ? "disabled" : ""}>Stop model</button>`
          : isRunning
            ? `<button class="primary" id="launch" ${busy ? "disabled" : ""}>${busy ? "Working…" : "Use " + esc(profile.label.replace("DwarfStar ", "")) + " (no restart)"}</button><button class="danger" id="stop" ${busy ? "disabled" : ""}>Stop model</button>`
            : `<button class="primary" id="launch" ${busy ? "disabled" : ""}>${busy ? "Working…" : (running ? "Switch to " : "Launch ") + esc(profile.label.replace("DwarfStar ", ""))}</button>`}
      </div>
      ${stageBar()}
      ${S.pendingSwitch ? switchSummary(profile, running, requirement, available, freed) : ""}
      ${overrideNote ? `<div class="notice info" style="margin-top:14px">${esc(overrideNote)}</div>` : ""}
      ${short ? `<div class="notice" style="margin-top:14px">${esc(model.display_name.replace("DwarfStar ", ""))} requires approximately ${requirement.required_gib.toFixed(1)} GiB available for the configured ${model.context_label} context; ${available.toFixed(1)} GiB is currently available${freed ? ` after stopping the running model (${(available + freed).toFixed(1)} GiB)` : ""}.</div>` : ""}
    </div>
    <div class="grid cols-4" style="margin:14px 0" id="run-stats"></div>
    <div class="grid cols-2">
      <div class="panel"><h2>Runtime</h2>
        <dl class="kv">
          <dt>Status</dt><dd>${isRunning ? (state.healthy ? '<span class="pill pass">Ready</span>' : '<span class="pill fail">Not healthy</span>') : '<span class="pill">Offline</span>'}
            ${isRunning && activeProfile && !isActive ? `<span class="muted small"> · active profile ${esc(profileLabel(activeProfile))}</span>` : ""}
            ${running && !isRunning ? `<span class="muted small"> · ${esc(modelOf(running).display_name)} is running</span>` : ""}</dd>
          <dt>Endpoint</dt><dd class="mono">${esc(state.endpoint || "–")}</dd>
          <dt>Process</dt><dd class="mono">${isRunning ? "pid " + state.pid + (state.uptime_s != null ? " · up " + fmtDuration(state.uptime_s) : "") : "–"}</dd>
          <dt>Load time</dt><dd>${isRunning && state.load_seconds != null ? state.load_seconds.toFixed(1) + " s" : "–"}</dd>
          <dt>Memory plan</dt><dd>${requirement.planned_gib.toFixed(2)} GiB planned · needs ${requirement.required_gib.toFixed(1)} GiB available</dd>
        </dl>
        ${steps ? `<h2 style="margin-top:14px">Last switch</h2><ul class="steps">${steps}</ul>` : ""}
      </div>
      <div class="panel"><h2>Agent connection</h2>
        <dl class="kv">
          <dt>Profile</dt><dd><b>${esc(profile.label)}</b></dd>
          <dt>In Zed</dt><dd>Agent Panel → <b>+</b> → External Agents → <b>${esc(profile.label)}</b></dd>
          <dt>Launcher</dt><dd class="mono">dwarfstar-acp --profile ${esc(profile.id)}</dd>
          <dt>Tools</dt><dd>Workspace · Shell · Browser · Computer use · Vision</dd>
        </dl>
        <h2 style="margin-top:14px">Tool approval</h2>
        <div class="chips" role="radiogroup" aria-label="Tool approval">${(permissions.modes || []).map((m) => `<button class="chip ${m.id === permissions.approval_mode ? "selected" : ""}"
          data-approval="${m.id}" role="radio" aria-checked="${m.id === permissions.approval_mode}" title="${esc(m.description)}">${esc(m.label)}</button>`).join("")}</div>
        <p class="muted small" style="margin:8px 0 0">${esc(permissions.description || "")} Applies to new agent sessions and the next chat turn; same for every profile.</p>
        ${S.confirmFullAuto ? `<div class="notice" style="margin-top:10px"><b>Turn on Full Auto?</b> The agent will run shell commands, edit files anywhere,
          use the browser and control the computer without asking, outside the sandbox.
          <div class="row" style="margin-top:8px"><button class="danger small-button" id="full-auto-yes">Turn on Full Auto</button>
          <button class="ghost small-button" id="full-auto-no">Cancel</button></div></div>` : ""}
        <p class="muted small" style="margin:12px 0 0">The reasoning level is fixed per agent entry. Choosing another level here changes which entry to open; the running model does not restart.</p>
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
  root.querySelectorAll("[data-approval]").forEach((b) => b.onclick = () => {
    if (b.dataset.approval === permissions.approval_mode) return;
    if (b.dataset.approval === "full_auto") { S.confirmFullAuto = true; renderRun(); return; }
    setApproval(b.dataset.approval);
  });
  const yes = $("#full-auto-yes", root), no = $("#full-auto-no", root);
  if (yes) yes.onclick = () => setApproval("full_auto", "full_auto");
  if (no) no.onclick = () => { S.confirmFullAuto = false; renderRun(); };
  if (stop) stop.onclick = () => lifecycle("/api/stop", {});
  if (zed) zed.onclick = async () => { const r = await api("/api/launch", { profile: profile.id }); toast(r.ok ? r.instruction : r.error); };
  renderRunStats();
}
async function setApproval(mode, confirm) {
  const result = await api("/api/permissions", { mode, ...(confirm ? { confirm } : {}) });
  S.confirmFullAuto = false;
  toast(result.ok ? `Tool approval: ${result.permissions.label}` : result.error);
  await refreshState(); S.renderedKey = null; renderRun();
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
  const isRunning = runningModel() === S.model, live = isRunning ? S.live : null;
  const tps = live?.rates?.live_tps ?? live?.rates?.decode_tps;
  const memory = S.system?.memory;
  const headroom = memory?.available_bytes != null ? memory.available_bytes / 2 ** 30 : null;
  node.innerHTML = `
    <div class="stat"><div class="label">Decode</div><div class="value">${num(tps ?? measured.decode_tok_s, 1, "tok/s")}</div><div class="sub">${tps != null ? "live" : measured.decode_tok_s != null ? "measured" : ""}</div></div>
    <div class="stat"><div class="label">Time to first token</div><div class="value">${num(measured.ttft_s, 2, "s")}</div><div class="sub">short prompt, measured</div></div>
    <div class="stat"><div class="label">MTP acceptance</div><div class="value">${num(live?.mtp?.acceptance_pct ?? measured.mtp_acceptance_pct, 1, "%")}</div><div class="sub">${live?.mtp?.proposed ? live.mtp.accepted + " of " + live.mtp.proposed + " drafts" : "measured"}</div></div>
    <div class="stat"><div class="label">Memory available</div><div class="value">${num(headroom, 1, "GiB")}</div><div class="sub">${memory?.swap_used_bytes != null ? "swap " + (memory.swap_used_bytes / 2 ** 30).toFixed(1) + " GiB" : ""}</div></div>`;
}

/* ----------------------------------------------------------------- models */
function renderModels() {
  const root = $("#view-models"); if (!S.catalog) return;
  S.modelTab = S.modelTab || S.model;
  const m = modelOf(S.modelTab), d = S.catalog.details[S.modelTab], measured = d.measured || {};
  const assets = d.assets, memory = m.memory;
  const total = m.disk_bytes;
  const order = [["main", "Main model"], ["ple", "PLE (n-gram) sidecar"], ["mtp", "MTP block"], ["vision", "Vision projector"]];
  const colors = { main: TONE[m.id], ple: "#3d6f8f", mtp: "#7fd6a3", vision: "#e58f8f" };
  const plan = m.memory_plan, planned = plan.planned_gib, limit = plan.metal_working_set_gib;
  const state = S.state?.models[m.id];
  const runningPlan = state?.memory_plan || {};
  const memParts = [["Resident model", plan.resident_model_gib, TONE[m.id]], ["KV cache", plan.kv_gib, "#4fd695"], ["Graph buffers", plan.buffers_gib, "#ffc857"], ["Vision projector", plan.projector_gib || 0, "#e58f8f"]];
  const policy = S.catalog.policy, overrides = Object.entries(m.overrides || {});
  const settingName = { prefill_chunk: "Prefill chunk", context: "Context", max_output: "Output ceiling", runtime: "Runtime build" };
  const availableNow = S.system?.memory?.available_bytes != null ? S.system.memory.available_bytes / 2 ** 30 : null;
  const quality = (measured.quality || []).map((q) => `<tr><td>${esc(q.name)}</td><td>${esc(q.result)}</td><td class="muted">${esc(q.note || "")}</td></tr>`).join("");
  root.innerHTML = `
    <h1>Models</h1>
    <p class="lead">Static facts come from the model catalog. Speeds and quality results are measured on this machine; anything not measured says so.</p>
    <div class="tabs">${S.catalog.models.map((x) => `<button class="chip ${x.id === S.modelTab ? "selected" : ""}" style="--tone:${TONE[x.id]}" data-tab="${x.id}">${esc(x.display_name.replace("DwarfStar ", ""))}</button>`).join("")}</div>
    <div class="grid cols-2">
      <div class="panel"><h2>Identity</h2><dl class="kv">
        <dt>Display name</dt><dd><b>${esc(m.display_name)}</b> ${m.default ? '<span class="badge default">Default</span>' : ""}</dd>
        <dt>Family</dt><dd>${esc(m.family)}</dd>
        <dt>Architecture</dt><dd>${esc(d.architecture.name)}<div class="muted small">${esc(d.architecture.type)} · ${d.architecture.layers} layers · ${d.architecture.experts_per_layer} experts per layer, ${d.architecture.experts_active} active per token</div></dd>
        <dt>Source</dt><dd class="mono">${esc(d.source.repository)}</dd>
        <dt>Revision</dt><dd class="mono">${esc(d.source.revision)}</dd>
        <dt>Derivation</dt><dd>${esc(d.source.derivation || "")}</dd>
        <dt>Reasoning</dt><dd>${m.reasoning_efforts.map((e) => `<span class="pill">${e.toUpperCase()}</span>`).join(" ")}</dd>
      </dl></div>
      <div class="panel"><h2>Quantization</h2><dl class="kv">
        <dt>Label</dt><dd><b>${esc(m.quantization.label)}</b></dd>
        <dt>Routed experts</dt><dd>${esc(m.quantization.experts)}</dd>
        <dt>Dense trunk</dt><dd>${esc(m.quantization.dense)}</dd>
        <dt>PLE</dt><dd>${esc(m.quantization.ple)}</dd>
        <dt>MTP</dt><dd>${esc(m.quantization.mtp)}</dd>
        <dt>Vision</dt><dd>${esc(m.quantization.vision)}</dd>
        <dt>KV cache</dt><dd>K ${esc(m.cache.k_cache_dtype)} · V ${esc(m.cache.v_cache_dtype)} · ${(m.cache.kv_bytes_per_token / 1024).toFixed(1)} KiB per token<div class="muted small">${esc(m.cache.policy)}</div></dd>
      </dl></div>
      <div class="panel"><h2>Storage</h2>
        <div class="bar">${order.map(([k]) => `<span style="width:${100 * assets[k].bytes / total}%;background:${colors[k]}"></span>`).join("")}</div>
        <div class="legend">${order.map(([k, label]) => `<span><i style="background:${colors[k]}"></i>${label} ${gib(assets[k].bytes)}</span>`).join("")}</div>
        <dl class="kv" style="margin-top:12px"><dt>Total on disk</dt><dd><b>${gib(total)}</b></dd>
          <dt>Resident</dt><dd>Main model, MTP block and projector are resident in unified memory. The PLE sidecar stays on disk and is read row by row.</dd></dl>
        <details style="margin-top:10px"><summary>Files and hashes</summary>
          <table style="margin-top:8px"><tbody>${order.map(([k, label]) => `<tr><td>${label}</td><td class="mono">${esc(assets[k].file)}<div class="faint">${esc(assets[k].sha256)}</div></td></tr>`).join("")}</tbody></table></details>
      </div>
      <div class="panel"><h2>Memory</h2>
        <div class="bar">${memParts.map(([, g, c]) => `<span style="width:${100 * g / limit}%;background:${c}"></span>`).join("")}</div>
        <div class="legend">${memParts.map(([l, g, c]) => `<span><i style="background:${c}"></i>${l} ${g.toFixed(2)} GiB</span>`).join("")}</div>
        <dl class="kv" style="margin-top:12px">
          <dt>Planned working set</dt><dd><b>${planned.toFixed(2)} GiB</b> <span class="muted small">model + KV + buffers + projector</span></dd>
          <dt>Metal limit</dt><dd>${limit.toFixed(2)} GiB <span class="muted small">GPU recommended working set, ${esc(plan.metal_working_set_source)}; ${plan.metal_headroom_gib.toFixed(2)} GiB headroom</span></dd>
          <dt>Start requires</dt><dd>${plan.required_gib.toFixed(1)} GiB available <span class="muted small">plan + ${plan.system_reserve_gib} GiB shared system reserve</span></dd>
          <dt>Available now</dt><dd>${availableNow != null ? availableNow.toFixed(1) + " GiB of unified memory" : NA}</dd>
          <dt>Measured peak</dt><dd>${measured.peak_wired_gib != null ? measured.peak_wired_gib.toFixed(2) + " GiB system wired (whole machine)" : NA}</dd>
          <dt>Now</dt><dd>${state?.running ? `running · runtime reports ${runningPlan.planned_gib ?? "–"} GiB planned` : "not running"}</dd>
        </dl>
      </div>
      <div class="panel"><h2>Runtime policy</h2>
        <p class="muted small" style="margin:0 0 10px">Every model runs the same DwarfStar runtime policy (version ${esc(policy.version)}). Only the settings below differ, each for a stated reason.</p>
        <dl class="kv">
          <dt>Common</dt><dd>${(policy.model_defaults.context / 1024)}K context · K/V ${esc(policy.model_defaults.cache.k_cache_dtype)}/${esc(policy.model_defaults.cache.v_cache_dtype)} · prefill ${policy.model_defaults.prefill_chunk} · output ${policy.model_defaults.max_output.toLocaleString()} · MTP on · vision resident · Full Auto · telemetry on demand</dd>
          <dt>This model</dt><dd>${overrides.length ? overrides.map(([k, o]) => `<b>${esc(settingName[k] || k)} ${esc(String(o.value))}</b> <span class="muted small">(shared ${esc(String(o.default))})</span><div class="muted small">${esc(o.reason)}</div>`).join("") : "No overrides: the shared policy unchanged."}</dd>
          <dt>Model-specific</dt><dd class="small">Weights and quantization, MTP head, vision projector, resident memory, reasoning levels (${m.reasoning_efforts.map((e) => e.toUpperCase()).join(", ")}), endpoint port ${m.port}</dd>
        </dl>
      </div>
      <div class="panel"><h2>Context</h2><dl class="kv">
        <dt>Configured</dt><dd><b>${m.context.toLocaleString()} tokens</b> (${m.context_label})</dd>
        <dt>Tested depth</dt><dd>${measured.tested_context_tokens != null ? measured.tested_context_tokens.toLocaleString() + " tokens" : NA}</dd>
        <dt>Output ceiling</dt><dd>${m.max_output.toLocaleString()} tokens</dd>
        <dt>Current use</dt><dd>${state?.running && S.live?.context?.used != null ? S.live.context.used.toLocaleString() + " tokens" : "–"}</dd>
        <dt>Prefill chunk</dt><dd>${m.prefill_chunk} tokens${m.overrides?.prefill_chunk ? ' <span class="muted small">(model-specific; see Runtime policy)</span>' : ""}</dd>
      </dl></div>
      <div class="panel"><h2>Speed</h2><dl class="kv">
        <dt>Cold load</dt><dd>${num(measured.cold_load_s, 1, " s")}</dd>
        <dt>Warm load</dt><dd>${num(measured.warm_load_s, 1, " s")}</dd>
        <dt>Time to first token</dt><dd>${num(measured.ttft_s, 2, " s")}</dd>
        <dt>Prefill</dt><dd>${num(measured.prefill_tok_s, 0, " tok/s")}</dd>
        <dt>Decode</dt><dd>${num(measured.decode_tok_s, 1, " tok/s")}</dd>
        <dt>Decode, long context</dt><dd>${num(measured.decode_long_tok_s, 1, " tok/s")} ${measured.decode_long_context ? `<span class="muted small">at ${esc(measured.decode_long_context)}</span>` : ""}</dd>
      </dl></div>
      <div class="panel"><h2>MTP (speculative decoding)</h2><dl class="kv">
        <dt>Enabled</dt><dd>Yes, native adaptive depth</dd>
        <dt>Sidecar format</dt><dd>${esc(m.quantization.mtp)}</dd>
        <dt>Acceptance</dt><dd>${num(measured.mtp_acceptance_pct, 1, " %")}</dd>
        <dt>Speed benefit</dt><dd>${measured.mtp_benefit ? esc(measured.mtp_benefit) : NA}</dd>
      </dl></div>
      <div class="panel"><h2>Capabilities</h2>${m.capabilities.map((c) => `<span class="cap">${esc(CAPABILITY[c] || c)}</span>`).join("")}</div>
    </div>
    <div class="panel" style="margin-top:14px"><h2>Measured quality</h2>
      ${quality ? `<table><thead><tr><th>Measurement</th><th>Result</th><th>Notes</th></tr></thead><tbody>${quality}</tbody></table>` : `<p class="muted">No results recorded.</p>`}
      <p class="small muted" style="margin:10px 0 0">Raw results and how they were measured: docs/BENCHMARKS.md in the repository.</p>
    </div>`;
  root.querySelectorAll("[data-tab]").forEach((b) => b.onclick = () => { S.modelTab = b.dataset.tab; renderModels(); });
}
const CAPABILITY = { acp: "ACP agent", full_auto: "Full Auto", native_tool_calls: "Native tool calls", workspace: "Workspace files", shell: "Shell", browser: "Browser automation", computer_use: "Computer use (KimiCU)", vision: "Vision", mtp: "MTP", compaction: "Compaction", recovery: "Recovery", telemetry: "Telemetry" };

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
function renderLive() {
  const root = $("#view-live"); if (!S.catalog) return;
  const running = runningModel(), live = S.live || {}, request = live.request || {}, rates = live.rates || {}, mtp = live.mtp || {}, ple = live.ple || {};
  const model = running ? modelOf(running) : null, system = S.system || {}, memory = system.memory || {}, gpu = system.gpu || {}, process = system.process || {};
  if (!root.dataset.built) {
    root.dataset.built = "1";
    root.innerHTML = `<h1>Live</h1><p class="lead">Measurements from the running model and this machine. Rates come from the runtime's own counters; nothing is estimated.</p>
      <div id="live-head"></div>
      <div class="grid cols-4" id="live-stats" style="margin:14px 0"></div>
      <div class="grid cols-2">
        <div class="panel"><h2>Generation rate</h2><canvas class="spark" id="spark-tps"></canvas><div class="legend"><span><i style="background:var(--accent)"></i>tokens per second (per verify cycle, smoothed)</span></div></div>
        <div class="panel"><h2>MTP acceptance</h2><canvas class="spark" id="spark-accept"></canvas><div class="legend" id="mtp-legend"></div></div>
        <div class="panel"><h2>Context</h2><div id="live-context"></div></div>
        <div class="panel"><h2>PLE disk reads</h2><canvas class="spark" id="spark-ple"></canvas><div class="legend" id="ple-legend"></div></div>
        <div class="panel"><h2>Unified memory</h2><div id="live-memory"></div></div>
        <div class="panel"><h2>Processor</h2><canvas class="spark" id="spark-gpu"></canvas><div class="legend" id="gpu-legend"></div></div>
      </div>
      <div class="panel" style="margin-top:14px"><h2>Tool activity</h2><div class="timeline" id="live-tools"></div></div>`;
  }
  $("#live-head").innerHTML = running
    ? `<div class="panel tight row"><b>${esc(model.display_name)}</b><span class="pill">${esc(live.agent?.profile || "no agent session")}</span>
        <span class="pill ${request.active ? "pass" : ""}">${esc((request.phase || "IDLE").replace("_", " "))}</span>
        <span class="muted small">request ${request.sequence ?? "–"}</span><span class="spacer"></span>
        <span class="muted small">${S.connected ? "telemetry connected" : "telemetry reconnecting…"}</span></div>`
    : `<div class="notice info">No model is running. Start one from the Run page; live values appear here as soon as it serves a request.</div>`;
  $("#live-stats").innerHTML = `
    <div class="stat"><div class="label">Prompt tokens</div><div class="value">${request.prompt_tokens?.toLocaleString() ?? "–"}</div><div class="sub">${request.reused_tokens ? request.reused_tokens.toLocaleString() + " reused from cache" : ""}</div></div>
    <div class="stat"><div class="label">Output tokens</div><div class="value">${(request.generated ?? 0).toLocaleString()}</div><div class="sub">${request.finish ? "finish: " + esc(request.finish) : ""}</div></div>
    <div class="stat"><div class="label">Prefill</div><div class="value">${num(rates.prefill_tps, 0, "tok/s")}</div><div class="sub">${request.prefill_seconds != null ? request.prefill_seconds.toFixed(2) + " s to first token" : ""}</div></div>
    <div class="stat"><div class="label">Decode</div><div class="value">${num(rates.live_tps ?? rates.decode_tps, 1, "tok/s")}</div><div class="sub">${rates.decode_tps != null ? "request average " + rates.decode_tps.toFixed(1) : ""}</div></div>`;
  sparkline($("#spark-tps"), S.series.tps, "#5cc8ff");
  sparkline($("#spark-accept"), S.series.accept, "#4fd695", 100);
  sparkline($("#spark-ple"), S.series.ple, "#ffb454");
  sparkline($("#spark-gpu"), S.series.gpu, "#b98cff", 100);
  $("#mtp-legend").innerHTML = `<span>${mtp.cycles ?? 0} verify cycles</span><span>${mtp.proposed ?? 0} drafts proposed</span><span>${mtp.accepted ?? 0} accepted</span><span>${mtp.rejected ?? 0} rejected</span><span><b>${plain(mtp.acceptance_pct)} %</b></span>`;
  $("#ple-legend").innerHTML = `<span>${((ple.bytes_per_s || 0) / 1024).toFixed(0)} KiB/s</span><span>${(ple.reads ?? 0).toLocaleString()} row reads</span><span>${((ple.bytes ?? 0) / 2 ** 20).toFixed(1)} MiB this session</span><span>process disk read ${process.disk_read_bytes_per_s != null ? (process.disk_read_bytes_per_s / 2 ** 20).toFixed(2) + " MiB/s" : "–"}</span>`;
  const capacity = model?.context, used = live.context?.used ?? 0;
  $("#live-context").innerHTML = model ? `
    <div class="bar"><span style="width:${100 * (request.prompt_tokens ?? 0) / capacity}%;background:var(--accent)"></span><span style="width:${100 * (request.generated ?? 0) / capacity}%;background:var(--good)"></span></div>
    <div class="legend"><span><i style="background:var(--accent)"></i>prompt ${(request.prompt_tokens ?? 0).toLocaleString()}</span><span><i style="background:var(--good)"></i>generated ${(request.generated ?? 0).toLocaleString()}</span>
      <span>${used.toLocaleString()} of ${capacity.toLocaleString()} (${(100 * used / capacity).toFixed(1)} %)</span></div>
    <dl class="kv" style="margin-top:10px"><dt>KV cache</dt><dd>K ${model.cache.k_cache_dtype} · V ${model.cache.v_cache_dtype} · ${(used * model.cache.kv_bytes_per_token / 2 ** 30).toFixed(2)} of ${model.memory.kv_gib.toFixed(2)} GiB filled (allocated up front)</dd>
      <dt>Disk checkpoints</dt><dd>${live.kv?.hits ?? 0} restored · ${live.kv?.stores ?? 0} stored</dd></dl>` : `<span class="muted">–</span>`;
  const total = memory.total_bytes || 1;
  $("#live-memory").innerHTML = memory.total_bytes ? `
    <div class="bar"><span style="width:${100 * memory.wired_bytes / total}%;background:#5cc8ff"></span><span style="width:${100 * memory.active_bytes / total}%;background:#4fd695"></span><span style="width:${100 * (memory.compressed_bytes || 0) / total}%;background:#ffc857"></span></div>
    <div class="legend"><span><i style="background:#5cc8ff"></i>wired ${gib(memory.wired_bytes, 1)}</span><span><i style="background:#4fd695"></i>active ${gib(memory.active_bytes, 1)}</span><span><i style="background:#ffc857"></i>compressed ${gib(memory.compressed_bytes, 1)}</span></div>
    <dl class="kv" style="margin-top:10px"><dt>Available</dt><dd>${gib(memory.available_bytes, 1)} of ${gib(memory.total_bytes, 0)}</dd><dt>Swap used</dt><dd>${gib(memory.swap_used_bytes, 2)}</dd>
      <dt>GPU memory in use</dt><dd>${gib(gpu.in_use_bytes, 1)}</dd><dt>Model process</dt><dd>${process.footprint_bytes != null ? gib(process.footprint_bytes, 1) + " footprint" : "–"}</dd></dl>` : `<span class="muted">Waiting for the first sample…</span>`;
  $("#gpu-legend").innerHTML = `<span><i style="background:var(--cyber)"></i>GPU ${gpu.utilization_pct ?? "–"} %</span><span>model process CPU ${process.cpu_pct ?? "–"} %</span>`;
  const tools = [...(live.tools?.timeline || [])].slice(-40).reverse();
  $("#live-tools").innerHTML = (live.tools?.active ? [`<div class="item"><span class="muted">running</span><span class="tag ${esc(live.tools.active.category)}">${esc(live.tools.active.category)}</span><b>${esc(live.tools.active.name)}</b><span></span><span>…</span></div>`] : [])
    .concat(tools.map((t) => `<div class="item"><span class="muted">${new Date(t.t * 1000).toLocaleTimeString()}</span><span class="tag ${esc(t.category)}">${esc(t.category)}</span><span>${esc(t.name)}</span><span class="muted">${t.ms != null ? t.ms + " ms" : ""}</span><span>${t.ok === false ? "✗" : "✓"}</span></div>`)).join("") || `<span class="muted">No tool calls in this session yet.</span>`;
}

/* ------------------------------------------------------------------- boot */
async function boot() {
  document.querySelectorAll("#nav button[data-view]").forEach((b) => b.onclick = () => show(b.dataset.view));
  $("#palette-open").onclick = () => window.Palette?.open();
  try { [S.catalog, S.state] = await Promise.all([api("/api/catalog"), api("/api/state")]); }
  catch { setTimeout(boot, 1500); return; }
  const selected = S.catalog.profiles.find((p) => p.id === S.state.selected_profile) || S.catalog.profiles.find((p) => p.default);
  S.model = selected.model; S.effort = selected.reasoning_effort;
  connect(1);
  const initial = location.hash.slice(1);
  show(["run", "chats", "models", "live", "visualizer", "settings"].includes(initial) ? initial : "run");
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
  banner.querySelector("button").onclick = () => show("settings");
  $("#main").prepend(banner);
}
window.DwarfStar = { S, api, esc, modelOf, runningModel, TONE, show, toast, gib };
boot();
