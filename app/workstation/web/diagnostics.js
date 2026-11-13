/* Diagnostics: the running model, memory, health checks and recovery, in one place.
 * Four memory figures are kept apart on purpose: system RAM (what macOS has), DwarfStar's admission requirement
 * (what a start needs available), the GPU's Metal working-set limit (what the model's plan must fit) and the
 * measured peak (what a real run used). Paths are shown with the home directory as "~". */
"use strict";
(function () {
  const D = window.DwarfStar, S = D.S, esc = D.esc;
  const GIB = 2 ** 30;
  const level = { ok: ["pass", "Ready"], warn: ["warn", "Optional"], error: ["fail", "Blocker"] };
  const f = (value, digits = 2, unit = " GiB") => value == null || Number.isNaN(value) ? '<span class="faint">Not measured</span>' : Number(value).toFixed(digits) + unit;
  let doc = null, busy = false;

  async function mount() {
    const root = document.querySelector("#view-settings");
    if (!doc) root.innerHTML = `<h1>Diagnostics</h1><p class="lead">Collecting…</p>`;
    doc = await D.api("/api/diagnostics");
    render(root);
  }

  function pill(ok, yes = "Healthy", no = "Not healthy") { return `<span class="pill ${ok ? "pass" : "fail"}">${ok ? yes : no}</span>`; }

  function compositionBar(m, total) {
    const parts = [["Model weights", m.model_weights_gib, "var(--scope)"], ["MTP / draft", m.mtp_gib, "#7fd6a3"], ["KV cache", m.kv_gib, "#4fd695"],
                   ["Graph buffers", m.buffers_gib, "#ffc857"], ["Vision", m.projector_gib, "#e58f8f"], ["Safety reserve", m.system_reserve_gib, "#56606e"]];
    return `<div class="bar" title="Planned composition against the ${total.toFixed(2)} GiB Metal working set">${parts.map(([, g, c]) => `<span style="width:${100 * (g || 0) / total}%;background:${c}"></span>`).join("")}</div>
      <div class="legend">${parts.map(([l, g, c]) => `<span><i style="background:${c}"></i>${l} ${f(g)}</span>`).join("")}</div>`;
  }

  function render(root) {
    const r = doc.runtime, m = r.memory, sys = r.system_memory, s = doc.summary, catalogModel = r.model ? D.modelOf(r.model) : null;
    const backend = r.model ? doc.backends[r.model] : null, plan = catalogModel ? catalogModel.memory_plan : null;
    const measured = r.model ? (S.catalog.details[r.model].measured || {}) : {};
    const groups = {};
    doc.checks.forEach((c) => (groups[c.group] = groups[c.group] || []).push(c));
    const recovery = doc.recovery || {};
    const recoveredModels = Object.entries(recovery.models || {}).filter(([, v]) => v.ledger && v.ledger !== "none" && v.ledger !== "valid");
    root.innerHTML = `
      <div class="row" style="align-items:flex-end;margin-bottom:6px">
        <div><h1 style="margin-bottom:4px">Diagnostics</h1>
          <p class="lead" style="margin:0">Live state of the runtime and this installation. Nothing here changes a model.</p></div>
        <span class="spacer"></span>
        <button class="ghost small-button" id="diag-refresh">Refresh</button>
        <button class="ghost small-button" id="diag-copy">Copy diagnostics</button>
        <button class="ghost small-button" id="diag-reconcile" title="Re-check saved ownership, interrupted chats and open telemetry sessions against the real processes. Never stops a model.">Reconcile state</button>
      </div>
      <div class="row" style="margin:10px 0 16px">
        <span class="pill pass">${s.ok} ready</span><span class="pill ${s.warn ? "warn" : ""}">${s.warn} optional missing</span><span class="pill ${s.error ? "fail" : ""}">${s.error} blocking</span>
        <span class="muted small">updated ${new Date(doc.generated * 1000).toLocaleTimeString()}</span>
      </div>
      <div class="grid cols-2">
        <div class="panel"><h2>Running model</h2>${r.model ? `<dl class="kv">
            <dt>Model</dt><dd><b>${esc(r.display_name)}</b> ${pill(r.healthy)}</dd>
            <dt>Reasoning</dt><dd>${r.active_profile ? esc(r.active_profile.split("-").pop().toUpperCase()) : '<span class="faint">unknown (reconnected)</span>'}</dd>
            <dt>Backend</dt><dd>${esc(backend.kind === "native" ? "Native (ds4-unified)" : "Splash-m1 " + (backend.release || ""))} <span class="muted small">${esc(backend.speculative)}</span></dd>
            <dt>Process</dt><dd class="mono">pid ${r.pid} · port ${r.port}${r.uptime_s != null ? " · up " + Math.round(r.uptime_s / 60) + " min" : ""}</dd>
            <dt>Context</dt><dd>${r.context.used != null ? r.context.used.toLocaleString() + " used · " + r.context.remaining.toLocaleString() + " remaining" : '<span class="faint">no request yet</span>'} of ${r.context.window.toLocaleString()}</dd>
            <dt>Prefill chunk</dt><dd>${catalogModel.prefill_chunk} tokens${catalogModel.overrides?.prefill_chunk ? ' <span class="muted small">(model-specific)</span>' : ""}</dd>
            <dt>KV cache</dt><dd>K ${esc(catalogModel.cache.k_cache_dtype)} · V ${esc(catalogModel.cache.v_cache_dtype)}</dd>
          </dl>` : `<p class="muted">No model is running. Start one from Run.</p>`}</div>
        <div class="panel"><h2>Memory</h2>
          ${plan ? compositionBar(m, plan.metal_working_set_gib) : ""}
          <dl class="kv" style="margin-top:12px">
            <dt>1 · System RAM</dt><dd>${f(sys.available_gib)} available of ${f(sys.total_gib, 0)} · wired ${f(sys.wired_gib, 1)} · swap ${f(sys.swap_used_gib, 1)}</dd>
            <dt>2 · Admission</dt><dd>${plan ? `start needs ${f(plan.required_gib, 1)} available <span class="muted small">(plan ${f(plan.planned_gib)} + ${plan.system_reserve_gib} GiB reserve)</span>` : '<span class="faint">no model running</span>'}</dd>
            <dt>3 · Metal limit</dt><dd>${plan ? `${f(plan.metal_working_set_gib)} GPU working set · ${f(plan.metal_headroom_gib)} headroom for this plan` : "–"}</dd>
            <dt>4 · Measured peak</dt><dd>${measured.peak_wired_gib != null ? f(measured.peak_wired_gib) + " wired, whole machine" : f(null)}${m && m.runtime_reported_gib ? ` · runtime reports ${f(m.runtime_reported_gib)} planned now` : ""}</dd>
          </dl></div>
        <div class="panel"><h2>Chats and agent</h2><dl class="kv">
            <dt>Chats</dt><dd>${doc.chats.chats} stored${doc.chats.answering.length ? ` · ${doc.chats.answering.length} answering` : ""}</dd>
            ${doc.chats.latest ? `<dt>Latest</dt><dd>${esc(doc.chats.latest.title)} <span class="mono small faint">${esc(doc.chats.latest.id)}</span></dd>
            <dt>Agent session</dt><dd class="mono small">${esc(doc.chats.latest.agent_session_id || "not started")}</dd>
            <dt>Context / compactions</dt><dd>${(doc.chats.latest.context.tokens || 0).toLocaleString()} tokens · ${doc.chats.latest.compactions} compaction${doc.chats.latest.compactions === 1 ? "" : "s"}</dd>` : ""}
            <dt>Tool approval</dt><dd><b>${esc(doc.permissions.label)}</b> <span class="muted small">for new agent sessions</span></dd>
          </dl></div>
        <div class="panel"><h2>Telemetry and recovery</h2><dl class="kv">
            <dt>Telemetry</dt><dd>${doc.telemetry.subscribers} subscriber${doc.telemetry.subscribers === 1 ? "" : "s"} · detail level ${doc.telemetry.detail_level}${doc.telemetry.session ? ` · session <span class="mono small">${esc(doc.telemetry.session)}</span>` : " · no open session"}</dd>
            ${backend && !backend.telemetry.router ? `<dt>Not available</dt><dd class="small">Router, PLE and draft events are not reported by this backend; Live and Visualizer show only what it reports.</dd>` : ""}
            <dt>Last recovery</dt><dd>${recovery.t ? new Date(recovery.t * 1000).toLocaleTimeString() + " · " + (recoveredModels.length ? recoveredModels.map(([id, v]) => `${esc(id)}: ${esc(v.ledger)}`).join("; ") : "saved state matched the processes") : "pending"}</dd>
            ${(recovery.interrupted_chats || []).length ? `<dt>Interrupted chats</dt><dd>${recovery.interrupted_chats.length} marked; resend to continue</dd>` : ""}
            ${(recovery.telemetry_sessions_finalized || []).length ? `<dt>Sessions closed</dt><dd>${recovery.telemetry_sessions_finalized.length} left open by a crash</dd>` : ""}
          </dl></div>
      </div>
      <div class="panel" style="margin-top:14px"><h2>Health checks</h2>
        <table><tbody>${Object.entries(groups).map(([group, rows]) => rows.map((c, i) => `<tr>
          <td class="muted small" style="width:110px">${i === 0 ? esc(group) : ""}</td>
          <td style="width:30%">${esc(c.name)}</td>
          <td style="width:90px"><span class="pill ${level[c.level][0]}">${level[c.level][1]}</span></td>
          <td class="small"><span class="mono">${esc(c.detail)}</span>${c.remedy ? `<div class="muted">${esc(c.remedy)}</div>` : ""}</td></tr>`).join("")).join("")}</tbody></table></div>
      <div class="panel" style="margin-top:14px"><h2>Locations</h2><dl class="kv">${Object.entries(doc.locations).map(([k, v]) => `<dt>${esc(k.replaceAll("_", " "))}</dt><dd class="mono small">${esc(v)}</dd>`).join("")}</dl></div>`;
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
