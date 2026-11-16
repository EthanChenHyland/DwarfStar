/* First-run setup: walks the doctor's checks (the same rows as `bin/dwarfstar doctor` and Diagnostics) one group at a
 * time, green / yellow / red, each problem with its remedy. It changes nothing on this Mac and never downloads:
 * every fix is a command the user runs or a file they place, then "Check again". The last steps choose the tool
 * approval preset (Full Auto still needs its own confirmation) and mark setup finished. */
"use strict";
(function () {
  const D = window.DwarfStar, esc = D.esc;
  const GROUPS = [
    ["Machine", "This Mac", "DwarfStar runs three local models on Apple Silicon with 64 GiB of unified memory."],
    ["Software", "Software", "Tools the agent and the app run on. Nothing here is installed for you; each fix is a command to run in Terminal."],
    ["Installation", "Folders and runtime", "Where DwarfStar keeps models, chats, caches and logs, and the pinned inference runtime."],
    ["Models", "Models", "Model files are large and are never downloaded automatically. A missing model blocks only that model; the others still run."],
    ["Integrations", "Integrations", "Optional: Zed's agent entries, the agent's browser, computer use, and this app's bundle."],
  ];
  const LEVEL = { ok: ["ready", "Ready"], warn: ["busy", "Optional"], error: ["bad", "Needs a fix"] };
  const W = { step: 0, doc: null, root: null, loading: false, confirmFullAuto: false };
  const steps = () => ["welcome", ...GROUPS.map((g) => g[0]), "approval", "done"];

  async function load() {
    W.loading = true; render();
    W.doc = await D.api("/api/setup").catch(() => null);
    W.loading = false; render();
  }
  function mount() {
    W.root = document.querySelector("#view-setup");
    if (!W.doc) load(); else render();
  }
  function rows(group) { return (W.doc?.checks || []).filter((c) => c.group === group); }
  function worst(group) {
    const levels = rows(group).map((c) => c.level);
    return levels.includes("error") ? "error" : levels.includes("warn") ? "warn" : "ok";
  }
  /* Remedies carry shell commands in backticks; show them as code with a copy button. Everything else is escaped text. */
  function remedy(text) {
    return esc(text).replace(/; /g, ";<br>").replace(/`([^`]+)`/g, (_, command) =>
      `<code class="setup-cmd">${command}</code><button class="quiet small-button setup-copy" data-copy="${command}" title="Copy">Copy</button>`);
  }
  function checkList(group) {
    return `<div class="setup-checks">${rows(group).map((c) => `<div class="setup-check ${LEVEL[c.level][0]}">
      <span class="dot ${LEVEL[c.level][0]}" aria-hidden="true"></span>
      <div><div><b>${esc(c.name)}</b> <span class="faint small">${LEVEL[c.level][1]}</span></div>
        <div class="small muted mono-wrap">${esc(c.detail)}</div>
        ${c.remedy ? `<div class="small setup-remedy">${remedy(c.remedy)}</div>` : ""}</div></div>`).join("")}</div>`;
  }
  function pills() {
    return `<ol class="setup-steps">${steps().map((id, i) => {
      const group = GROUPS.find((g) => g[0] === id), name = group ? group[1] : { welcome: "Start", approval: "Tool approval", done: "Finish" }[id];
      const tone = group && W.doc ? LEVEL[worst(id)][0] : "";
      return `<li class="${i === W.step ? "current" : ""}"><button class="quiet" data-step="${i}">${tone ? `<span class="dot ${tone}"></span>` : ""}${esc(name)}</button></li>`;
    }).join("")}</ol>`;
  }
  function body() {
    const id = steps()[W.step], s = W.doc?.summary;
    if (!W.doc) return `<p class="lead">${W.loading ? "Checking this Mac…" : "The DwarfStar service did not answer the setup checks."}</p>`;
    if (id === "welcome") {
      return `<div class="row about-row" style="margin-bottom:8px">${typeof PRODUCT_MARK === "undefined" ? "" : PRODUCT_MARK}<b>Set up DwarfStar</b></div><p class="lead">This checks what DwarfStar needs on this Mac, one area at a time. It only reads: nothing is installed, changed or downloaded. Where something is missing, it says exactly what to run or where to put a file, then you check again.</p>
        <div class="setup-summary"><span><span class="dot ready"></span>${s.ok} ready</span><span><span class="dot busy"></span>${s.warn} optional missing</span><span><span class="dot bad"></span>${s.error} need a fix</span></div>
        <p class="small muted">The same checks run in a terminal with <code>bin/dwarfstar doctor</code>, and stay available under Diagnostics.</p>`;
    }
    if (id === "approval") {
      const p = D.S.state?.permissions || {};
      return `<p class="lead">How the agent's tools ask before they act. This applies to Workstation chats and Zed alike, and you can change it any time in Settings.</p>
        <div class="seg" role="radiogroup" aria-label="Tool approval">${(p.modes || []).map((m) => `<button class="chip ${m.id === p.approval_mode ? "selected" : ""}" data-approval="${m.id}" role="radio" aria-checked="${m.id === p.approval_mode}">${esc(m.label)}</button>`).join("")}</div>
        <dl class="kv" style="margin-top:var(--sp-4)">${(p.modes || []).map((m) => `<dt>${esc(m.label)}</dt><dd class="${m.id === p.approval_mode ? "" : "muted"}">${esc(m.description)}</dd>`).join("")}</dl>
        ${W.confirmFullAuto ? `<div class="notice bad" style="margin-top:var(--sp-4)"><b>Full Auto runs every tool without asking, outside any sandbox.</b> Text in a page, file or screenshot can steer the agent's actions with no approval step.
          <div class="row" style="margin-top:8px"><button class="danger small-button" id="setup-full-auto">Turn on Full Auto</button><button class="ghost small-button" id="setup-full-auto-cancel">Keep the current preset</button></div></div>` : ""}`;
    }
    if (id === "done") {
      const blocking = (W.doc.checks || []).filter((c) => c.level === "error");
      return `<p class="lead">${blocking.length ? `${blocking.length} check${blocking.length === 1 ? "" : "s"} still need a fix: ${esc(blocking.map((c) => c.name).join(", "))}. Models whose checks pass can start now; the rest stay unavailable until fixed.` : "Everything DwarfStar needs is in place."}</p>
        <p class="small muted">${W.doc.completed ? "Setup was finished before on this Mac; finishing again only records the time." : "Finishing records that setup was seen, so this page does not open on its own again. Diagnostics keeps the same checks."}</p>
        <button class="primary" id="setup-finish">Finish and go to Run</button>`;
    }
    const group = GROUPS.find((g) => g[0] === id);
    return `<p class="lead">${esc(group[2])}</p>${checkList(id)}`;
  }
  function render() {
    if (!W.root) return;
    const last = steps().length - 1;
    W.root.innerHTML = `<div class="page-head"><h1>Setup</h1><span class="spacer"></span>
        <button class="ghost small-button" id="setup-recheck" ${W.loading ? "disabled" : ""}>${W.loading ? "Checking…" : "Check again"}</button></div>
      ${pills()}<div class="panel setup-body">${body()}</div>
      <div class="row" style="margin-top:12px"><button class="ghost" id="setup-back" ${W.step === 0 ? "disabled" : ""}>Back</button><span class="spacer"></span>
        ${W.step < last ? `<button class="primary" id="setup-next">Next</button>` : ""}</div>`;
    const on = (sel, fn) => { const node = W.root.querySelector(sel); if (node) node.onclick = fn; };
    on("#setup-recheck", load);
    on("#setup-back", () => { W.step = Math.max(0, W.step - 1); render(); });
    on("#setup-next", () => { W.step = Math.min(last, W.step + 1); render(); });
    on("#setup-finish", finish);
    on("#setup-full-auto", () => setApproval("full_auto", "full_auto"));
    on("#setup-full-auto-cancel", () => { W.confirmFullAuto = false; render(); });
    W.root.querySelectorAll("[data-step]").forEach((b) => b.onclick = () => { W.step = +b.dataset.step; render(); });
    W.root.querySelectorAll("[data-copy]").forEach((b) => b.onclick = () => navigator.clipboard.writeText(b.dataset.copy).then(() => D.toast("Copied"), () => D.toast("Copy failed")));
    W.root.querySelectorAll("[data-approval]").forEach((b) => b.onclick = () => {
      if (b.dataset.approval === D.S.state?.permissions?.approval_mode) return;
      if (b.dataset.approval === "full_auto") { W.confirmFullAuto = true; render(); return; }
      setApproval(b.dataset.approval);
    });
  }
  async function setApproval(mode, confirm) {
    const result = await D.api("/api/permissions", { mode, ...(confirm ? { confirm } : {}) });
    W.confirmFullAuto = false;
    if (result.ok && D.S.state) D.S.state.permissions = result.permissions;
    D.toast(result.ok ? `Tool approval: ${result.permissions.label}` : result.error);
    render();
  }
  async function finish() {
    const result = await D.api("/api/setup/complete", {});
    if (!result.ok) return D.toast(result.error || "Could not record setup");
    W.doc = result;
    document.querySelector(".setup-banner")?.remove();
    D.show("run");
  }
  window.Setup = { mount, open: () => { W.step = 0; W.doc = null; D.show("setup"); } };
})();
