/* Diagnostics: the running model, memory, health checks and recovery, in one place.
 * Four memory figures are kept apart on purpose: system RAM (what macOS has), DwarfStar's admission requirement
 * (what a start needs available), the GPU's Metal working-set limit (what the model's plan must fit) and the
 * measured peak (what a real run used). Paths are shown with the home directory as "~". */
"use strict";
(function () {
  const D = window.DwarfStar, S = D.S, esc = D.esc;
  const GIB = 2 ** 30;
  const level = { ok: ["ok", "Ready"], warn: ["warn", "Optional, missing"], error: ["bad", "Blocks a start"] };
  const f = (value, digits = 2, unit = " GiB") => value == null || Number.isNaN(value) ? '<span class="faint">Not measured</span>' : Number(value).toFixed(digits) + unit;
  let doc = null, busy = false;

  async function mount() {
    const root = document.querySelector("#view-diagnostics");
    if (!doc) root.innerHTML = `<h1>Diagnostics</h1><p class="lead">Collecting…</p>`;
    doc = await D.api("/api/diagnostics");
    render(root);
  }

  function compositionBar(m, total) {
    const parts = [["Model weights", m.model_weights_gib, "var(--scope)"], ["Draft model", m.draft_gib, "#6fb59a"], ["KV cache", m.kv_gib, "#3f8f73"],
                   ["Graph buffers", m.buffers_gib, "#c9a75a"], ["Vision", m.projector_gib, "#c98a8a"], ["Reserve", m.system_reserve_gib, "#3a4352"]];
    return `<div class="bar" title="Planned composition against the ${total.toFixed(2)} GiB Metal working set">${parts.map(([, g, c]) => `<span style="width:${100 * (g || 0) / total}%;background:${c}"></span>`).join("")}</div>
      <div class="legend">${parts.map(([l, g, c]) => `<span><i style="background:${c}"></i>${l} ${f(g)}</span>`).join("")}</div>`;
  }

  function render(root) {
    const r = doc.runtime, m = r.memory, sys = r.system_memory, s = doc.summary, catalogModel = r.model ? D.modelOf(r.model) : null;
    const backend = r.model ? doc.backends[r.model] : null, plan = catalogModel ? catalogModel.memory_plan : null;
    const measured = r.model ? (S.catalog.details[r.model].measured || {}) : {};
    const splash = r.model ? (S.state?.models?.[r.model]?.splash || {}) : {};
    const since = splash.since_start || {}, cache = splash.prefix_cache || {};
    const last = r.model ? (S.state?.models?.[r.model]?.last_request || {}) : {};
    const ctx = r.model ? (r.context.used || (last.input_tokens != null ? last.input_tokens + (last.output_tokens || 0) : null)) : null;
    const recovery = doc.recovery || {};
    const recoveredModels = Object.entries(recovery.models || {}).filter(([, v]) => v.ledger && v.ledger !== "none" && v.ledger !== "valid");
    const attention = doc.checks.filter((c) => c.level !== "ok");
    const metric = (label, value, sub = "") => `<div class="metric"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;
    const na = '<span class="faint">–</span>';
    const checkRow = (c, i, rows, group) => `<tr><td class="muted small" style="width:110px">${i === 0 ? esc(group) : ""}</td>
      <td style="width:30%"><span class="status ${level[c.level][0]}">${esc(c.name)}</span></td>
      <td class="small"><span class="mono">${esc(c.detail)}</span>${c.level !== "ok" ? `<div style="color:var(--${c.level === "error" ? "bad" : "warn"})">${level[c.level][1]}${c.remedy ? " · " + esc(c.remedy) : ""}</div>` : ""}</td></tr>`;
    const groups = {};
    doc.checks.forEach((c) => (groups[c.group] = groups[c.group] || []).push(c));
    root.innerHTML = `
      <div class="page-head"><h1>Diagnostics</h1><span class="spacer"></span>
        <button class="quiet small-button" id="diag-reconcile" title="Re-check saved ownership, interrupted chats and open telemetry sessions against the real processes. Never stops a model.">Reconcile state</button>
        <button class="ghost small-button" id="diag-copy">Copy diagnostics</button>
        <button class="ghost small-button" id="diag-refresh">Refresh</button></div>
      <div class="panel"><div class="metrics">
        ${metric("Model", r.model ? esc(r.display_name.replace("DwarfStar ", "")) : '<span class="faint">None</span>', r.model ? `<span class="status ${r.healthy ? "ok" : "bad"}">${r.healthy ? "Healthy" : "Not healthy"}</span>` : "stopped")}
        ${metric("Reasoning", r.active_profile ? esc(r.active_profile.split("-").pop().toUpperCase()) : na, r.model && !r.active_profile ? "unknown after reconnect" : "")}
        ${metric("Runtime", "Splash-m1", esc(Object.values(doc.backends)[0]?.release || ""))}
        ${metric("Context", ctx ? ctx.toLocaleString() : na, r.model ? (ctx ? "latest request, of " : "window ") + r.context.window.toLocaleString() + " tokens" : "")}
        ${metric("Checks", s.error ? `<span style="color:var(--bad)">${s.error} blocking</span>` : s.warn ? `<span style="color:var(--warn)">${s.warn} optional</span>` : "All passed", `${s.ok} of ${doc.checks.length} ready · ${new Date(doc.generated * 1000).toLocaleTimeString()}`)}
      </div></div>
      ${attention.length ? `<div class="panel section" style="border-color:${s.error ? "rgba(255,107,107,.45)" : "rgba(255,200,87,.4)"}"><h2>Needs attention</h2>
        <table><tbody>${attention.map((c, i) => checkRow(c, 1, attention, c.group)).join("")}</tbody></table></div>` : ""}
      <div class="panel section"><h2>Memory ${plan ? `<span class="muted small">planned against the ${f(plan.metal_working_set_gib)} Metal working set</span>` : ""}</h2>
        ${plan ? compositionBar(m, plan.metal_working_set_gib) : ""}
        <dl class="kv" style="margin-top:var(--sp-3)">
          <dt>System RAM</dt><dd>${f(sys.available_gib)} available of ${f(sys.total_gib, 0)} · wired ${f(sys.wired_gib, 1)} · swap ${f(sys.swap_used_gib, 1)}</dd>
          ${plan ? `<dt>Start needs</dt><dd>${f(plan.required_gib, 1)} available <span class="muted small">(plan ${f(plan.planned_gib)} + ${plan.system_reserve_gib} GiB reserve)</span></dd>
          <dt>Metal headroom</dt><dd>${f(plan.metal_headroom_gib)} for this plan</dd>` : ""}
          ${splash.memory_bytes?.current_bytes != null ? `<dt>Runtime in use</dt><dd>${f(splash.memory_bytes.current_bytes / GIB, 1)} · peak ${f(splash.memory_bytes.peak_bytes / GIB, 1)}</dd>` : ""}
          ${measured.peak_wired_gib != null ? `<dt>Measured peak</dt><dd>${f(measured.peak_wired_gib)} wired, whole machine</dd>` : ""}
        </dl></div>
      <div class="grid cols-2 section">
        <div class="panel"><h2>Performance <span class="faint small">since the model started</span></h2>${r.model ? `<dl class="kv">
            <dt>First token</dt><dd>${splash.ttft_ms?.p50 != null ? (splash.ttft_ms.p50 / 1000).toFixed(2) + " s median · p95 " + (splash.ttft_ms.p95 / 1000).toFixed(2) + " s" : na}</dd>
            <dt>Prefill</dt><dd>${since.prefill_tokens_per_second != null ? Math.round(since.prefill_tokens_per_second) + " tok/s" : na}</dd>
            <dt>Decode</dt><dd>${since.decode_tokens_per_second != null ? since.decode_tokens_per_second.toFixed(1) + " tok/s" : na}</dd>
            <dt>Draft accepted</dt><dd>${since.draft_acceptance_rate != null ? Math.round(100 * since.draft_acceptance_rate) + " % (" + since.accepted_draft_tokens + " of " + since.drafted_tokens + ")" : na}</dd>
            <dt>Prefix cache</dt><dd>${cache.hit_rate != null ? Math.round(100 * cache.hit_rate) + " % hits · " + (cache.reused_tokens || 0).toLocaleString() + " tokens reused" : na}</dd>
            <dt>Not reported</dt><dd class="muted">router, experts, PLE reads</dd>
          </dl>` : `<p class="muted" style="margin:0">Appears while a model runs.</p>`}</div>
        <div class="panel"><h2>System</h2><dl class="kv">
            ${r.model ? `<dt>Process</dt><dd class="mono">pid ${r.pid} · port ${r.port}${r.uptime_s != null ? " · up " + Math.round(r.uptime_s / 60) + " min" : ""}</dd>
            <dt>Thermal</dt><dd>${esc(splash.thermal_state || "–")}${splash.memory_pressure && splash.memory_pressure !== "normal" ? ` · memory pressure ${esc(splash.memory_pressure)}` : ""}</dd>
            <dt>Scheduler</dt><dd>${splash.scheduler ? `${splash.active_requests || 0} active · ${splash.scheduler.queued || 0} queued` : na}</dd>` : ""}
            <dt>Chats</dt><dd>${doc.chats.chats} stored${doc.chats.answering.length ? ` · ${doc.chats.answering.length} answering` : ""}</dd>
            <dt>Tool approval</dt><dd>${esc(doc.permissions.label)}</dd>
            <dt>Telemetry</dt><dd>${doc.telemetry.subscribers} subscriber${doc.telemetry.subscribers === 1 ? "" : "s"}${doc.telemetry.session ? ` · session <span class="mono small">${esc(doc.telemetry.session)}</span>` : ""}</dd>
            <dt>Last recovery</dt><dd>${recovery.t ? new Date(recovery.t * 1000).toLocaleTimeString() + " · " + (recoveredModels.length ? recoveredModels.map(([id, v]) => `${esc(id)}: ${esc(v.ledger)}`).join("; ") : "state matched the processes") : "pending"}${(recovery.interrupted_chats || []).length ? ` · ${recovery.interrupted_chats.length} interrupted chat${recovery.interrupted_chats.length === 1 ? "" : "s"} marked` : ""}</dd>
          </dl></div>
      </div>
      <details class="disclosure section"><summary>All ${doc.checks.length} health checks</summary>
        <div class="panel" style="margin-top:var(--sp-2)"><table><tbody>${Object.entries(groups).map(([group, rows]) => rows.map((c, i) => checkRow(c, i, rows, group)).join("")).join("")}</tbody></table></div></details>
      <details class="disclosure" style="margin-top:var(--sp-3)"><summary>Locations</summary>
        <div class="panel" style="margin-top:var(--sp-2)"><dl class="kv">${Object.entries(doc.locations).map(([k, v]) => `<dt>${esc(k.replaceAll("_", " "))}</dt><dd class="mono small">${esc(v)}</dd>`).join("")}</dl></div></details>`;
    root.querySelector("#diag-refresh").onclick = mount;
    root.querySelector("#diag-copy").onclick = copy;
    root.querySelector("#diag-reconcile").onclick = reconcile;
  }

  async function copy() {
    const text = JSON.stringify(doc, null, 2);
    try { await navigator.clipboard.writeText(text); D.toast("Diagnostics copied"); }
    catch {  // WebKit without clipboard permission: select the text so ⌘C works
      const area = Object.assign(document.createElement("textarea"), { value: text });
      area.style.cssText = "position:fixed;inset:20% 20%;z-index:50;background:#0b0e13;color:#dbe3ee;font:12px var(--mono)";
      document.body.append(area); area.select();
      D.toast("Press ⌘C to copy, then Esc");
      area.onkeydown = (event) => { if (event.key === "Escape" || ((event.metaKey || event.ctrlKey) && event.key === "c")) setTimeout(() => area.remove(), 50); };
      area.onblur = () => area.remove();
    }
  }

  async function reconcile() {
    if (busy) return;
    busy = true;
    const result = await D.api("/api/diagnostics/reconcile", {});
    busy = false;
    D.toast(result.ok ? "Saved state reconciled with the running processes" : result.error);
    mount();
  }

  window.Diagnostics = { mount };
})();
