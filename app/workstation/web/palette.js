/* Command palette (⌘K or Ctrl+K): every view, every profile, stop, new chat, diagnostics. Type to filter,
 * arrows to move, Enter to run, Esc to close. Switching from here goes through the same pre-switch summary. */
"use strict";
(function () {
  const D = window.DwarfStar, S = D.S, esc = D.esc;
  let node = null, items = [], index = 0;

  function commands() {
    const views = [["run", "Run"], ["chats", "Chats"], ["models", "Models"], ["live", "Live"], ["visualizer", "Visualizer"], ["settings", "Diagnostics"]]
      .map(([id, label]) => ({ group: "Go to", label, run: () => D.show(id) }));
    const running = D.runningModel(), active = S.state?.active_profile;
    const profiles = (S.catalog?.profiles || []).map((p) => ({
      group: "Model", label: (p.id === active ? "Running: " : running === p.model ? "Use " : "Start ") + p.label.replace("DwarfStar ", ""),
      hint: p.id === active ? "active" : running === p.model ? "no restart" : running ? "stops " + D.modelOf(running).display_name.replace("DwarfStar ", "") : "",
      disabled: p.id === active,
      run: () => { S.model = p.model; S.effort = p.reasoning_effort; D.show("run"); document.querySelector("#launch")?.click(); },
    }));
    const actions = [
      { group: "Action", label: "Stop model", disabled: !running, run: () => { D.show("run"); document.querySelector("#stop")?.click(); } },
      { group: "Action", label: "New chat", hint: "with the model chosen on Run", run: () => { D.show("chats"); setTimeout(() => document.querySelector("#chat-new")?.click(), 50); } },
      { group: "Action", label: "Search chats", run: () => { D.show("chats"); setTimeout(() => document.querySelector("#chat-search")?.focus(), 50); } },
      { group: "Action", label: "Copy diagnostics", run: () => { D.show("settings"); setTimeout(() => document.querySelector("#diag-copy")?.click(), 400); } },
      { group: "Action", label: "Reconcile state", hint: "never stops a model", run: () => { D.show("settings"); setTimeout(() => document.querySelector("#diag-reconcile")?.click(), 400); } },
    ];
    return [...views, ...profiles, ...actions];
  }

  function open() {
    if (node) return close();
    node = document.createElement("div");
    node.className = "palette-backdrop";
    node.innerHTML = `<div class="palette panel" role="dialog" aria-label="Command palette">
      <input id="palette-input" placeholder="Type a command, view or model…" autocomplete="off" aria-label="Command">
      <ul id="palette-list" role="listbox"></ul><div class="faint small palette-foot">↑↓ move · Enter run · Esc close</div></div>`;
    document.body.append(node);
    const input = node.querySelector("input");
    input.oninput = () => { index = 0; render(input.value); };
    input.onkeydown = (event) => {
      if (event.key === "ArrowDown") { index = Math.min(items.length - 1, index + 1); render(input.value, false); event.preventDefault(); }
      else if (event.key === "ArrowUp") { index = Math.max(0, index - 1); render(input.value, false); event.preventDefault(); }
      else if (event.key === "Enter") { run(items[index]); event.preventDefault(); }
      else if (event.key === "Escape") close();
    };
    node.onclick = (event) => { if (event.target === node) close(); };
    render(""); input.focus();
  }

  function render(query, rebuild = true) {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    if (rebuild) items = commands().filter((c) => words.every((w) => (c.group + " " + c.label + " " + (c.hint || "")).toLowerCase().includes(w)));
    const list = node.querySelector("#palette-list");
    list.innerHTML = items.map((c, i) => `<li role="option" aria-selected="${i === index}" class="${i === index ? "active" : ""} ${c.disabled ? "disabled" : ""}" data-i="${i}">
      <span class="faint small">${esc(c.group)}</span><b>${esc(c.label)}</b><span class="muted small">${esc(c.hint || "")}</span></li>`).join("") || `<li class="muted">No match</li>`;
    list.querySelectorAll("[data-i]").forEach((li) => li.onclick = () => run(items[+li.dataset.i]));
    list.querySelector(".active")?.scrollIntoView({ block: "nearest" });
  }

  function run(command) { if (!command || command.disabled) return; close(); command.run(); }
  function close() { node?.remove(); node = null; }

  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); open(); }
  });
  window.Palette = { open };
})();
