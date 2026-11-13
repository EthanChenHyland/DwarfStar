/* Workstation chats: durable conversations with the running model.
 *
 * Everything is stored by the local service under the data root (/api/chats). A turn runs through the
 * same ACP agent as Zed, resuming the chat's agent session, so tools, Full Auto and the agent's own context
 * compaction apply. Only reply text and tool-call titles are shown; reasoning is never displayed. */
"use strict";
(function () {
  const D = window.DwarfStar, S = D.S, esc = D.esc;
  const C = { mounted: false, root: null, list: [], tags: [], filter: "recent", tag: null, query: "", current: null, chat: null, progress: null,
              poll: 0, renaming: false, tagging: false, confirmDelete: 0, searchTimer: 0, startedModel: null, selecting: false, selected: new Set(),
              restored: false };

  const when = (t) => {
    const s = Date.now() / 1000 - t;
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago";
    return new Date(t * 1000).toLocaleDateString();
  };
  const profileName = (id) => (S.catalog.profiles.find((p) => p.id === id)?.label || id).replace("DwarfStar ", "");
  const tone = (model) => D.TONE[model] || "var(--accent)";

  const PIN = '<svg class="chat-mark" viewBox="0 0 16 16" aria-label="Pinned"><path d="M6 2.5h4l-.5 4 2 2v1h-7v-1l2-2zM8 9.5V14"/></svg>';
  const STAR_OUTLINE = '<svg class="chat-mark" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.2l1.7 3.6 3.9.5-2.9 2.7.8 3.9L8 11l-3.5 1.9.8-3.9-2.9-2.7 3.9-.5z"/></svg>';
  const STAR = '<svg class="chat-mark star" viewBox="0 0 16 16" aria-label="Starred"><path d="M8 2.2l1.7 3.6 3.9.5-2.9 2.7.8 3.9L8 11l-3.5 1.9.8-3.9-2.9-2.7 3.9-.5z"/></svg>';

  function mount() {
    C.root = document.querySelector("#view-chats");
    if (!C.mounted) {
      C.root.innerHTML = `
        <div class="page-head"><h1>Chats</h1></div>
        <div class="chat-layout">
          <aside class="chat-side panel tight" aria-label="Chats">
            <div class="row chat-side-top"><button class="primary" id="chat-new">New chat</button>
              <button class="quiet small-button" id="chat-select" title="Select several chats to archive or delete">Select</button></div>
            <input id="chat-search" type="search" placeholder="Search chats" aria-label="Search chats" autocomplete="off">
            <div class="chat-filter seg" role="tablist">
              <button class="chip selected" data-filter="recent" role="tab">Recent</button>
              <button class="chip" data-filter="starred" role="tab">Starred</button>
              <button class="chip" data-filter="archived" role="tab">Archived</button>
              <button class="chip" data-filter="zed" role="tab" title="DwarfStar threads in Zed's agent panel">Zed</button>
            </div>
            <div class="chat-tags" id="chat-tags"></div>
            <div class="chat-bulk" id="chat-bulk" hidden></div>
            <ul class="chat-list" id="chat-list"></ul>
          </aside>
          <section class="chat-main panel" id="chat-main" aria-live="polite"></section>
        </div>`;
      C.root.querySelector("#chat-new").onclick = newChat;
      C.root.querySelector("#chat-search").oninput = (event) => {
        clearTimeout(C.searchTimer);
        C.searchTimer = setTimeout(() => { C.query = event.target.value.trim(); loadList(); }, 200);
      };
      C.root.querySelectorAll("[data-filter]").forEach((b) => b.onclick = () => setFilter(b.dataset.filter));
      C.root.querySelector("#chat-select").onclick = () => { C.selecting = !C.selecting; C.selected.clear(); renderList(); renderBulk(); };
      C.mounted = true;
    }
    loadList();
    if (!C.current && !C.restored && S.state?.last_chat) C.current = S.state.last_chat;  // reopen where the user left off
    C.restored = true;
    if (C.current) openChat(C.current); else renderMain();
  }

  function setFilter(filter, tag = null) {
    C.filter = filter; C.tag = tag; C.selected.clear();
    C.root.querySelectorAll("[data-filter]").forEach((x) => x.classList.toggle("selected", x.dataset.filter === filter && !tag));
    loadList(); renderBulk();
  }

  function unmount() { clearTimeout(C.poll); C.poll = 0; }

  async function loadList() {
    if (C.filter === "zed") return loadZed();
    const query = new URLSearchParams({ archived: C.filter === "archived" ? "1" : "0", ...(C.filter === "starred" ? { starred: "1" } : {}),
                                        ...(C.tag ? { tag: C.tag } : {}), ...(C.query ? { q: C.query } : {}) });
    const result = await D.api("/api/chats?" + query);
    C.list = result.ok ? result.chats : [];
    C.tags = result.ok ? result.tags : [];
    renderList(); renderTags();
  }

  function renderTags() {
    const node = C.root.querySelector("#chat-tags");
    node.innerHTML = C.tags.map((t) => `<button class="chip tag ${C.tag === t.tag ? "selected" : ""}" data-tag="${esc(t.tag)}"
      title="${t.chats} chat${t.chats === 1 ? "" : "s"}">#${esc(t.tag)}</button>`).join("");
    node.querySelectorAll("[data-tag]").forEach((b) => b.onclick = () => C.tag === b.dataset.tag ? setFilter("recent") : setFilter("recent", b.dataset.tag));
  }

  /* Bulk mode: archive or restore at once; deleting needs the count typed out, and moves chats aside (recoverable from disk). */
  function renderBulk() {
    const bar = C.root.querySelector("#chat-bulk"), n = C.selected.size;
    C.root.querySelector("#chat-select").textContent = C.selecting ? "Done" : "Select";
    bar.hidden = !C.selecting;
    if (!C.selecting) return;
    const phrase = `delete ${n} chat${n === 1 ? "" : "s"}`, archived = C.filter === "archived";
    bar.innerHTML = `<div class="row small"><b>${n} selected</b><span class="spacer"></span>
        <button class="ghost small-button" id="bulk-all">${n === C.list.length && n ? "None" : "All"}</button>
        <button class="ghost small-button" id="bulk-archive" ${n ? "" : "disabled"}>${archived ? "Unarchive" : "Archive"}</button></div>
      <div class="row small"><input id="bulk-confirm" placeholder='Type "${phrase}"' aria-label="Delete confirmation" ${n ? "" : "disabled"}>
        <button class="danger small-button" id="bulk-delete" disabled>Delete</button></div>`;
    bar.querySelector("#bulk-all").onclick = () => { if (C.selected.size === C.list.length) C.selected.clear(); else C.list.forEach((c) => C.selected.add(c.id)); renderList(); renderBulk(); };
    bar.querySelector("#bulk-archive").onclick = () => bulk(archived ? "unarchive" : "archive");
    const confirm = bar.querySelector("#bulk-confirm"), del = bar.querySelector("#bulk-delete");
    confirm.oninput = () => { del.disabled = confirm.value.trim() !== phrase; };
    del.onclick = () => bulk("delete", confirm.value.trim());
  }

  async function bulk(action, confirm = "") {
    const ids = [...C.selected];
    const result = await D.api("/api/chats-bulk", { ids, action, confirm });
    if (!result.ok) return D.toast(result.error);
    D.toast(`${ids.length} chat${ids.length === 1 ? "" : "s"} ${action === "delete" ? "deleted" : action + "d"}`);
    if (ids.includes(C.current) && action !== "unarchive") { C.current = null; C.chat = null; renderMain(); }
    C.selected.clear(); loadList(); renderBulk();
  }

  /* Zed threads: the agent session file is the shared transcript. A thread in a live workspace with a current
     profile can be continued here (same session, no copy); others can only be read. */
  async function loadZed() {
    const result = await D.api("/api/zed-threads");
    C.zed = result.ok ? result.threads : [];
    C.tags = [];
    renderTags();
    const node = C.root.querySelector("#chat-list");
    if (!C.zed.length) { node.innerHTML = `<li class="chat-empty muted small">${result.ok ? "No DwarfStar threads in Zed." : esc(result.error)}</li>`; return; }
    const action = (t) => t.chat ? `<button class="ghost small-button" data-open="${esc(t.chat)}">Open chat</button>`
      : t.state === "continuable" ? `<button class="ghost small-button" data-link="${esc(t.session_id)}">Continue here</button>`
      : t.state === "readable" ? `<button class="ghost small-button" data-read="${esc(t.session_id)}">Read</button>` : "";
    node.innerHTML = C.zed.map((t) => `<li><div class="chat-item zed-thread" title="${esc(t.why)}">
        <span class="chat-item-title">${esc(t.title)}</span>
        <span class="chat-item-meta"><i></i>${esc(t.agent.replace("DwarfStar ", ""))} · ${esc(t.state)}${t.archived_workspace ? " · archived workspace" : ""}</span>
        <span class="row small">${action(t)}</span></div></li>`).join("");
    node.querySelectorAll("[data-open]").forEach((b) => b.onclick = () => openChat(b.dataset.open, true));
    node.querySelectorAll("[data-link]").forEach((b) => b.onclick = async () => {
      const linked = await D.api("/api/zed-threads/link", { session: b.dataset.link });
      if (!linked.ok) return D.toast(linked.error);
      D.toast("Linked: Zed and this chat now share one agent session");
      openChat(linked.chat.id, true);
    });
    node.querySelectorAll("[data-read]").forEach((b) => b.onclick = () => readZed(b.dataset.read));
  }

  async function readZed(session) {
    const result = await D.api("/api/zed-threads/read?session=" + encodeURIComponent(session));
    if (!result.ok) return D.toast(result.error);
    C.current = null; C.chat = null;
    const main = C.root.querySelector("#chat-main"), t = result.thread;
    main.innerHTML = `<header class="chat-head"><h2>${esc(t.title)}</h2><div class="faint small">${esc(t.agent)} · read only · ${esc(t.why)}</div></header>
      <div class="chat-messages" id="chat-messages">${result.messages.map((m, i) => message(m, i)).join("")}</div>`;
  }

  function renderList() {
    if (C.filter === "zed") return loadZed();
    const node = C.root.querySelector("#chat-list");
    if (!C.list.length) {
      node.innerHTML = `<li class="chat-empty muted small">${C.query || C.tag ? "No chats match." : C.filter === "archived" ? "No archived chats."
        : C.filter === "starred" ? "No starred chats." : "No chats yet. Start one with New chat."}</li>`;
      return;
    }
    node.innerHTML = C.list.map((c) => `
      <li class="${C.selecting ? "selecting" : ""}">${C.selecting ? `<input type="checkbox" data-pick="${esc(c.id)}" ${C.selected.has(c.id) ? "checked" : ""} aria-label="Select ${esc(c.title)}">` : ""}
        <button class="chat-item ${c.id === C.current ? "active" : ""}" data-id="${esc(c.id)}" style="--tone:${tone(c.model)}">
        <span class="chat-item-title">${esc(c.title)}${c.pinned ? PIN : ""}${c.starred ? STAR : ""}</span>
        <span class="chat-item-meta"><i></i>${esc(profileName(c.profile))} · ${when(c.updated)}${c.shared ? " · Zed" : ""}</span>
      </button></li>`).join("");
    node.querySelectorAll(".chat-item").forEach((b) => b.onclick = () => {
      if (C.selecting) { const box = node.querySelector(`[data-pick="${b.dataset.id}"]`); box.checked = !box.checked; box.onchange(); return; }
      openChat(b.dataset.id, true);
    });
    node.querySelectorAll("[data-pick]").forEach((box) => box.onchange = () => {
      if (box.checked) C.selected.add(box.dataset.pick); else C.selected.delete(box.dataset.pick);
      renderBulk();
    });
  }

  async function newChat() {
    const profile = S.catalog.profiles.find((p) => p.model === S.model && p.reasoning_effort === S.effort);
    const result = await D.api("/api/chats", { profile: profile?.id });
    if (!result.ok) { D.toast(result.error); return; }
    await showNew(result.chat.id);
  }

  async function showNew(id) {
    C.query = "";
    C.root.querySelector("#chat-search").value = "";
    setFilter("recent");
    await openChat(id, true);
    C.root.querySelector("#chat-input")?.focus();
  }

  /* Fork: a new chat with the same model and reasoning level. With the transcript (whole, or up to a message) the
   * agent receives it as context on the fork's first turn; without it, a fresh chat with the same settings. */
  async function fork(upto = null, messages = true) {
    const result = await D.api(`/api/chats/${C.chat.id}/fork`, { messages, ...(upto != null ? { upto } : {}) });
    if (!result.ok) return D.toast(result.error);
    D.toast(messages ? "Forked: the original chat is unchanged" : "New chat with the same model and reasoning");
    await showNew(result.chat.id);
  }

  async function organize(change) {
    const result = await D.api(`/api/chats/${C.chat.id}/organize`, change);
    if (!result.ok) return D.toast(result.error);
    Object.assign(C.chat, { pinned: result.chat.pinned, starred: result.chat.starred, tags: result.chat.tags });
    renderMain(false); loadList();
  }

  async function openChat(id, explicit = false) {
    const result = await D.api("/api/chats/" + encodeURIComponent(id) + (explicit ? "?open" : ""));
    if (!result.ok) { C.current = null; C.chat = null; renderMain(); return; }
    const changed = C.current !== id;
    C.current = id; C.chat = result.chat; C.progress = result.progress;
    if (changed) { C.renaming = false; C.tagging = false; C.confirmDelete = 0; }
    renderMain(changed);
    renderList();
    if (C.progress) follow();
  }

  /* While a turn runs, follow the service's record of it; the reply grows as the agent streams. */
  function follow() {
    clearTimeout(C.poll);
    C.poll = setTimeout(async () => {
      const id = C.current;
      const result = await D.api("/api/chats/" + encodeURIComponent(id));
      if (!result.ok || id !== C.current) return;
      const finished = C.progress && !result.progress;
      const key = JSON.stringify([result.chat.messages.length, result.progress]);
      const changed = key !== C.followKey;
      C.followKey = key; C.chat = result.chat; C.progress = result.progress;
      if (changed) renderMessages(false);  // rebuilding an unchanged reply would swallow clicks on its buttons
      renderComposerState();
      if (C.progress) follow();
      else if (finished) loadList();
    }, 700);
  }

  function modelRunning(chat) { return (S.state?.running_models || []).includes(chat.model); }

  function renderMain(fresh = true) {
    const main = C.root.querySelector("#chat-main");
    const chat = C.chat;
    if (!chat) {
      main.innerHTML = `<div class="chat-placeholder"><p class="muted">Select a chat, or start a new one with ${esc(profileName(S.state?.selected_profile || ""))}.</p>
        <button class="ghost small-button" id="chat-new-empty">New chat</button></div>`;
      main.querySelector("#chat-new-empty").onclick = newChat;
      return;
    }
    const usage = chat.context || {}, pct = usage.window ? Math.min(100, 100 * (usage.tokens || 0) / usage.window) : 0;
    const remaining = Math.max(0, (usage.window || 0) - (usage.tokens || 0));
    main.style.setProperty("--tone", tone(chat.model));
    main.innerHTML = `
      <header class="chat-head">
        <div class="row chat-title-row"><div class="chat-title-wrap" id="chat-title-wrap"></div>
          <button class="quiet small-button icon-toggle ${chat.starred ? "on" : ""}" id="chat-star" title="${chat.starred ? "Unstar" : "Star"}" aria-pressed="${!!chat.starred}">${chat.starred ? STAR : STAR_OUTLINE}</button>
          <details class="chat-menu"><summary aria-label="Chat actions">More</summary><div class="chat-menu-list panel tight">
            <button id="chat-pin">${chat.pinned ? "Unpin" : "Pin to top"}</button>
            <button id="chat-tag">Edit tags</button>
            <button id="chat-zed" title="Open this chat's workspace in Zed; the matching DwarfStar agent lists this conversation in its history">Open in Zed</button>
            <hr>
            <button id="chat-fork" title="Copy this conversation into a new chat; the agent gets the transcript as context">Fork conversation</button>
            <button id="chat-same" title="A new, empty chat with ${esc(profileName(chat.profile))}">New chat, same settings</button>
            <a href="/api/chats/${esc(chat.id)}/export?format=md" download>Export Markdown</a>
            <a href="/api/chats/${esc(chat.id)}/export?format=json" download>Export JSON</a>
            <hr>
            <button id="chat-archive">${chat.archived ? "Unarchive" : "Archive"}</button>
            <button id="chat-delete" class="destructive">Delete…</button>
          </div></details></div>
        <div class="row chat-head-meta">
          <span class="chat-profile" title="Model and reasoning level of this chat"><i></i>${esc(profileName(chat.profile))}</span>
          <span class="chat-context" title="Context the agent is using; it compacts older turns automatically as this fills. ${remaining.toLocaleString()} tokens remaining.">
            <span class="chat-context-bar"><i style="width:${pct.toFixed(1)}%"></i></span>
            ${Math.round((usage.tokens || 0) / 1024)}K of ${Math.round((usage.window || 0) / 1024)}K
          </span>
          ${chat.compactions ? `<span title="The agent condensed older turns to stay within the window; the full transcript is kept">${chat.compactions} compaction${chat.compactions === 1 ? "" : "s"}</span>` : ""}
          ${chat.shared ? `<span title="This chat and Zed's “${esc(chat.shared.agent)}” agent use one agent session, whose file is the transcript. One client writes at a time.">Shared with Zed</span>` : ""}
          ${chat.forked_from ? `<span>Forked from “${esc(chat.forked_from.title)}”</span>` : ""}
          ${(chat.tags || []).length ? `<span>${chat.tags.map((t) => "#" + esc(t)).join(" ")}</span>` : ""}
        </div>
        <div class="chat-tag-edit" id="chat-tag-edit"></div>
        <div id="chat-confirm"></div>
      </header>
      <div class="chat-messages" id="chat-messages"></div>
      <div class="chat-notice" id="chat-notice"></div>
      <form class="chat-composer" id="chat-composer">
        <div class="chat-input-wrap">
          <textarea id="chat-input" rows="3" placeholder="Message ${esc(profileName(chat.profile))}" aria-label="Message"></textarea>
          <div class="chat-input-foot"><span><i style="background:var(--tone)"></i>${esc(profileName(chat.profile))}</span>
            <a href="#settings" id="chat-approval" title="Tool approval for agent sessions; change in Settings">${esc(S.state?.permissions?.label || "")}</a>
            <span class="spacer"></span><span class="faint">Enter to send · Shift+Enter for a new line</span></div>
        </div>
        <button class="primary" id="chat-send" type="submit">Send</button>
      </form>`;
    renderTitle();
    renderTagEdit();
    renderMessages(fresh);
    renderComposerState();
    main.querySelector("#chat-star").onclick = () => organize({ starred: !chat.starred });
    main.querySelector("#chat-pin").onclick = () => organize({ pinned: !chat.pinned });
    main.querySelector("#chat-tag").onclick = () => { C.tagging = !C.tagging; renderTagEdit(); };
    main.querySelector("#chat-fork").onclick = () => fork();
    main.querySelector("#chat-same").onclick = () => fork(null, false);
    main.querySelector("#chat-zed").onclick = async () => {
      const result = await D.api(`/api/chats/${chat.id}/share-zed`, {});
      if (!result.ok) return D.toast(result.error);
      D.toast(result.how);
      await openChat(chat.id);
    };
    main.querySelector("#chat-archive").onclick = async () => {
      const result = await D.api(`/api/chats/${chat.id}/archive`, { archived: !chat.archived });
      if (!result.ok) return D.toast(result.error);
      D.toast(chat.archived ? "Chat restored" : "Chat archived");
      await openChat(chat.id); loadList();
    };
    main.querySelector("#chat-delete").onclick = () => {
      main.querySelector(".chat-menu").open = false;
      const box = main.querySelector("#chat-confirm");
      box.innerHTML = `<div class="notice bad row" style="gap:10px">Delete “${esc(chat.title)}”? It is moved to the chat trash.
        <span class="spacer"></span><button class="danger small-button" id="chat-delete-yes">Delete</button><button class="ghost small-button" id="chat-delete-no">Cancel</button></div>`;
      box.querySelector("#chat-delete-no").onclick = () => { box.innerHTML = ""; };
      box.querySelector("#chat-delete-yes").onclick = async () => {
        const result = await D.api(`/api/chats/${chat.id}/delete`, { confirm: chat.id });
        if (!result.ok) return D.toast(result.error);
        D.toast("Chat deleted");
        C.current = null; C.chat = null; renderMain(); loadList();
      };
    };
    main.querySelector("#chat-approval").onclick = (event) => { event.preventDefault(); D.show("settings"); };
    main.querySelectorAll(".chat-menu-list > button").forEach((b) => b.addEventListener("click", () => { if (b.id !== "chat-delete") main.querySelector(".chat-menu").open = false; }));
    const input = main.querySelector("#chat-input");
    input.onkeydown = (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); } };
    main.querySelector("#chat-composer").onsubmit = (event) => { event.preventDefault(); send(); };
  }

  function renderTitle() {
    const wrap = C.root.querySelector("#chat-title-wrap"), chat = C.chat;
    if (C.renaming) {
      wrap.innerHTML = `<input id="chat-title-input" class="chat-title-input" value="${esc(chat.title)}" maxlength="120" aria-label="Chat title">`;
      const input = wrap.querySelector("input");
      input.focus(); input.select();
      const done = async (save) => {
        if (!C.renaming) return;
        C.renaming = false;
        if (save && input.value.trim() && input.value.trim() !== chat.title) {
          const result = await D.api(`/api/chats/${chat.id}/rename`, { title: input.value });
          if (!result.ok) D.toast(result.error); else { chat.title = result.chat.title; loadList(); }
        }
        renderTitle();
      };
      input.onkeydown = (event) => { if (event.key === "Enter") done(true); if (event.key === "Escape") done(false); };
      input.onblur = () => done(true);
    } else {
      wrap.innerHTML = `<button class="chat-title" id="chat-title" title="Rename">${esc(chat.title)}</button>`;
      wrap.querySelector("button").onclick = () => { C.renaming = true; renderTitle(); };
    }
  }

  function renderTagEdit() {
    const node = C.root.querySelector("#chat-tag-edit"), chat = C.chat;
    if (!C.tagging) { node.innerHTML = ""; return; }
    node.innerHTML = `<input class="chat-title-input" id="chat-tag-input" value="${esc((chat.tags || []).join(", "))}" placeholder="Tags, separated by commas (e.g. work, pcap)" aria-label="Tags">`;
    const input = node.querySelector("input");
    input.focus();
    const save = async () => {
      if (!C.tagging) return;
      C.tagging = false;
      await organize({ tags: input.value.split(",").map((t) => t.trim()).filter(Boolean) });
    };
    input.onkeydown = (event) => { if (event.key === "Enter") save(); if (event.key === "Escape") { C.tagging = false; renderTagEdit(); } };
  }

  function renderMessages(scroll = true) {
    const node = C.root.querySelector("#chat-messages");
    if (!node) return;
    const atBottom = node.scrollHeight - node.scrollTop - node.clientHeight < 40;
    const messages = C.chat.messages.map(message);
    // "fork from here" on each reply: a new chat holding the transcript up to and including that reply
    if (C.progress) {
      const tools = (C.progress.tools || []).map(toolChip).join("");
      messages.push(`<div class="chat-msg assistant live"><div class="chat-msg-head">Answering <span class="muted">· ${esc(C.progress.phase || "starting")}</span></div>
        ${tools ? `<div class="chat-tools">${tools}</div>` : ""}<div class="chat-msg-text">${esc((C.progress.text || "").trimStart())}<span class="chat-cursor"></span></div></div>`);
    }
    const ask = C.progress?.permission;
    if (ask) messages.push(`<div class="chat-approval" role="alertdialog" aria-label="Tool approval"><b>Approve this tool call?</b>
      <div class="mono small" style="margin-top:4px">${esc(ask.title)}</div>
      <div class="muted small">Tool approval is set to ${esc(S.state?.permissions?.label || "Ask")}. Declined automatically after 10 minutes.</div>
      <div class="row">${ask.options.map((o) => `<button class="${o.kind.startsWith("allow") ? "primary" : "ghost"} small-button" data-permission="${esc(o.id)}">${esc(o.name)}</button>`).join("")}</div></div>`);
    node.innerHTML = messages.join("") || `<p class="muted chat-first">Say something to start. The first message becomes the title until you rename it.</p>`;
    if (scroll || atBottom) node.scrollTop = node.scrollHeight;
    node.querySelectorAll("[data-fork]").forEach((b) => b.onclick = () => fork(Number(b.dataset.fork)));
    node.querySelectorAll("[data-permission]").forEach((b) => b.onclick = async () => {
      node.querySelectorAll("[data-permission]").forEach((x) => (x.disabled = true));
      const result = await D.api(`/api/chats/${C.chat.id}/permission`, { option: b.dataset.permission });
      if (!result.ok) D.toast(result.error);
    });
  }

  /* Reply text: fenced code, inline code and bold only. Everything is escaped first; no links or HTML. */
  function richText(text) {
    const source = String(text || "").trim();
    if (!source) return '<span class="faint">(no text reply)</span>';
    return source.split(/(```[\s\S]*?```)/g).map((part) => {
      const fence = part.match(/^```([\w+-]*)\n?([\s\S]*?)```$/);
      if (fence) return `<pre class="chat-code"><code>${esc(fence[2].replace(/\n$/, ""))}</code></pre>`;
      return esc(part).replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
    }).join("");
  }

  function toolChip(t) {
    const status = t.status === "failed" ? "fail" : "";
    return `<span class="pill ${status}" title="${esc(t.title || "")}">${esc((t.title || t.kind || "tool").slice(0, 60))}</span>`;
  }

  function message(m, index) {
    const time = new Date(m.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (m.role === "notice") return `<div class="chat-notice-line muted small">${esc(m.text)}</div>`;
    if (m.role === "error") return `<div class="chat-msg error"><div class="chat-msg-head">Not answered <span class="faint">${time}</span></div><div class="chat-msg-text">${esc(m.text)}</div></div>`;
    const who = m.role === "user" ? "You" : C.chat ? profileName(C.chat.profile) : "Assistant";
    const list = m.tools || [];
    const failed = list.filter((t) => t.status === "failed").length;
    const tools = list.length > 2
      ? `<details class="chat-tool-group"><summary>${list.length} tool calls${failed ? ` · <span style="color:var(--bad)">${failed} failed</span>` : ""}</summary><div class="chat-tools">${list.map(toolChip).join("")}</div></details>`
      : list.length ? `<div class="chat-tools">${list.map(toolChip).join("")}</div>` : "";
    const forkHere = m.role === "assistant" && !C.progress && C.chat ? `<button class="chat-fork-here" data-fork="${index + 1}" title="New chat with the conversation up to here">Fork from here</button>` : "";
    return `<div class="chat-msg ${m.role}"><div class="chat-msg-head">${esc(who)} <span class="faint">${time}</span>${forkHere}</div>
      ${tools}<div class="chat-msg-text">${m.role === "assistant" ? richText(m.text) : esc((m.text || "").trim())}</div></div>`;
  }

  function renderComposerState() {
    const chat = C.chat, notice = C.root.querySelector("#chat-notice"), send = C.root.querySelector("#chat-send"), input = C.root.querySelector("#chat-input");
    if (!notice) return;
    const running = modelRunning(chat), busy = Boolean(C.progress), switching = S.state?.lifecycle?.busy;
    send.disabled = busy || !running || switching;
    input.disabled = !running;
    if (running) { notice.innerHTML = ""; return; }
    const profile = chat.profile;
    notice.innerHTML = `<div class="notice">This chat uses ${esc(profileName(profile))}, which is not running.
      <button class="ghost small-button" id="chat-start" ${switching ? "disabled" : ""}>${switching ? "Starting…" : "Start " + esc(profileName(profile))}</button></div>`;
    notice.querySelector("#chat-start").onclick = async () => {
      const result = await D.api("/api/switch", { profile, wait: false });
      if (!result.ok) return D.toast(result.error);
      D.toast("Starting " + profileName(profile) + "…");
      C.startedModel = chat.model;
      renderComposerState();
    };
  }

  async function send() {
    const input = C.root.querySelector("#chat-input"), text = input.value.trim(), chat = C.chat;
    if (!text || C.progress || !modelRunning(chat)) return;
    const result = await D.api(`/api/chats/${chat.id}/messages`, { text });
    if (!result.ok) { D.toast(result.error); return; }
    input.value = "";
    C.progress = { phase: "starting", text: "", tools: [] };
    C.chat.messages.push({ role: "user", text, t: Date.now() / 1000 });
    renderMessages(); renderComposerState();
    follow();
  }

  /* The Run page and the service own the model; refresh the composer when its state changes. */
  function stateChanged() { if (C.mounted && S.view === "chats" && C.chat) renderComposerState(); }

  window.Chats = { mount, unmount, stateChanged, current: () => C.chat };
})();
