/* Workstation chats: durable conversations with the running model.
 *
 * Everything is stored by the local service under the data root (/api/chats). A turn runs through the
 * same ACP agent as Zed, resuming the chat's agent session, so tools, Full Auto and the agent's own context
 * compaction apply. Only reply text and tool-call titles are shown; reasoning is never displayed. */
"use strict";
(function () {
  const D = window.DwarfStar, S = D.S, esc = D.esc;
  const C = { mounted: false, root: null, list: [], archived: false, query: "", current: null, chat: null, progress: null,
              poll: 0, renaming: false, confirmDelete: 0, searchTimer: 0, startedModel: null };

  const when = (t) => {
    const s = Date.now() / 1000 - t;
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago";
    return new Date(t * 1000).toLocaleDateString();
  };
  const profileName = (id) => (S.catalog.profiles.find((p) => p.id === id)?.label || id).replace("DwarfStar ", "");
  const tone = (model) => D.TONE[model] || "var(--accent)";

  function mount() {
    C.root = document.querySelector("#view-chats");
    if (!C.mounted) {
      C.root.innerHTML = `
        <h1>Chats</h1>
        <p class="lead">Conversations with the running model through the same agent as Zed: tools, Full Auto and automatic context compaction included. Chats are stored on this Mac only.</p>
        <div class="chat-layout">
          <aside class="chat-side panel tight" aria-label="Chats">
            <button class="primary" id="chat-new">New chat</button>
            <input id="chat-search" type="search" placeholder="Search chats" aria-label="Search chats" autocomplete="off">
            <div class="chat-filter" role="tablist">
              <button class="chip selected" data-archived="0" role="tab">Recent</button>
              <button class="chip" data-archived="1" role="tab">Archived</button>
            </div>
            <ul class="chat-list" id="chat-list"></ul>
          </aside>
          <section class="chat-main panel" id="chat-main" aria-live="polite"></section>
        </div>`;
      C.root.querySelector("#chat-new").onclick = newChat;
      C.root.querySelector("#chat-search").oninput = (event) => {
        clearTimeout(C.searchTimer);
        C.searchTimer = setTimeout(() => { C.query = event.target.value.trim(); loadList(); }, 200);
      };
      C.root.querySelectorAll("[data-archived]").forEach((b) => b.onclick = () => {
        C.archived = b.dataset.archived === "1";
        C.root.querySelectorAll("[data-archived]").forEach((x) => x.classList.toggle("selected", x === b));
        loadList();
      });
      C.mounted = true;
    }
    loadList();
    if (C.current) openChat(C.current); else renderMain();
  }

  function unmount() { clearTimeout(C.poll); C.poll = 0; }

  async function loadList() {
    const query = new URLSearchParams({ archived: C.archived ? "1" : "0", ...(C.query ? { q: C.query } : {}) });
    const result = await D.api("/api/chats?" + query);
    C.list = result.ok ? result.chats : [];
    renderList();
  }

  function renderList() {
    const node = C.root.querySelector("#chat-list");
    if (!C.list.length) {
      node.innerHTML = `<li class="chat-empty muted small">${C.query ? "No chats match." : C.archived ? "No archived chats." : "No chats yet."}</li>`;
      return;
    }
    node.innerHTML = C.list.map((c) => `
      <li><button class="chat-item ${c.id === C.current ? "active" : ""}" data-id="${esc(c.id)}" style="--tone:${tone(c.model)}">
        <span class="chat-item-title">${esc(c.title)}</span>
        <span class="chat-item-meta"><i></i>${esc(profileName(c.profile))} · ${when(c.updated)}</span>
        ${c.preview ? `<span class="chat-item-preview">${esc(c.preview)}</span>` : ""}
      </button></li>`).join("");
    node.querySelectorAll(".chat-item").forEach((b) => b.onclick = () => openChat(b.dataset.id));
  }

  async function newChat() {
    const profile = S.catalog.profiles.find((p) => p.model === S.model && p.reasoning_effort === S.effort);
    const result = await D.api("/api/chats", { profile: profile?.id });
    if (!result.ok) { D.toast(result.error); return; }
    C.archived = false; C.query = "";
    C.root.querySelector("#chat-search").value = "";
    C.root.querySelectorAll("[data-archived]").forEach((x) => x.classList.toggle("selected", x.dataset.archived === "0"));
    await openChat(result.chat.id);
    loadList();
    C.root.querySelector("#chat-input")?.focus();
  }

  async function openChat(id) {
    const result = await D.api("/api/chats/" + encodeURIComponent(id));
    if (!result.ok) { C.current = null; C.chat = null; renderMain(); return; }
    const changed = C.current !== id;
    C.current = id; C.chat = result.chat; C.progress = result.progress;
    if (changed) { C.renaming = false; C.confirmDelete = 0; }
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
      C.chat = result.chat; C.progress = result.progress;
      renderMessages();
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
      main.innerHTML = `<div class="chat-placeholder"><h2>No chat selected</h2><p class="muted">Start a new chat with the model and reasoning level chosen on the Run page, or open one from the list.</p></div>`;
      return;
    }
    const usage = chat.context || {}, pct = usage.window ? Math.min(100, 100 * (usage.tokens || 0) / usage.window) : 0;
    main.style.setProperty("--tone", tone(chat.model));
    main.innerHTML = `
      <header class="chat-head">
        <div class="chat-title-wrap" id="chat-title-wrap"></div>
        <div class="row chat-head-meta">
          <span class="pill" title="Model and reasoning level of this chat">${esc(profileName(chat.profile))}</span>
          <span class="chat-context" title="Context the agent is using (it compacts older turns automatically as this fills)">
            <span class="chat-context-bar"><i style="width:${pct.toFixed(1)}%"></i></span>
            ${(usage.tokens || 0).toLocaleString()} / ${Math.round((usage.window || 0) / 1024)}K
          </span>
          ${chat.compactions ? `<span class="muted small">${chat.compactions} compaction${chat.compactions === 1 ? "" : "s"}</span>` : ""}
          <span class="spacer"></span>
          <button class="ghost small-button" id="chat-archive">${chat.archived ? "Unarchive" : "Archive"}</button>
          <button class="danger small-button" id="chat-delete">${C.confirmDelete ? "Confirm delete" : "Delete"}</button>
        </div>
        <div class="faint small">Created ${new Date(chat.created * 1000).toLocaleString()} · updated ${when(chat.updated)}</div>
      </header>
      <div class="chat-messages" id="chat-messages"></div>
      <div class="chat-notice" id="chat-notice"></div>
      <form class="chat-composer" id="chat-composer">
        <textarea id="chat-input" rows="3" placeholder="Message ${esc(profileName(chat.profile))}…  (Enter to send, Shift+Enter for a new line)" aria-label="Message"></textarea>
        <button class="primary" id="chat-send" type="submit">Send</button>
      </form>`;
    renderTitle();
    renderMessages(fresh);
    renderComposerState();
    main.querySelector("#chat-archive").onclick = async () => {
      const result = await D.api(`/api/chats/${chat.id}/archive`, { archived: !chat.archived });
      if (!result.ok) return D.toast(result.error);
      D.toast(chat.archived ? "Chat restored" : "Chat archived");
      await openChat(chat.id); loadList();
    };
    main.querySelector("#chat-delete").onclick = async () => {
      if (!C.confirmDelete) {
        C.confirmDelete = setTimeout(() => { C.confirmDelete = 0; const b = main.querySelector("#chat-delete"); if (b) b.textContent = "Delete"; }, 4000);
        main.querySelector("#chat-delete").textContent = "Confirm delete";
        return;
      }
      clearTimeout(C.confirmDelete); C.confirmDelete = 0;
      const result = await D.api(`/api/chats/${chat.id}/delete`, { confirm: chat.id });
      if (!result.ok) return D.toast(result.error);
      D.toast("Chat deleted");
      C.current = null; C.chat = null; renderMain(); loadList();
    };
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

  function renderMessages(scroll = true) {
    const node = C.root.querySelector("#chat-messages");
    if (!node) return;
    const atBottom = node.scrollHeight - node.scrollTop - node.clientHeight < 40;
    const messages = C.chat.messages.map(message);
    if (C.progress) {
      const tools = (C.progress.tools || []).map(toolChip).join("");
      messages.push(`<div class="chat-msg assistant live"><div class="chat-msg-head">Answering <span class="muted">· ${esc(C.progress.phase || "starting")}</span></div>
        ${tools ? `<div class="chat-tools">${tools}</div>` : ""}<div class="chat-msg-text">${esc((C.progress.text || "").trimStart())}<span class="chat-cursor"></span></div></div>`);
    }
    node.innerHTML = messages.join("") || `<p class="muted chat-first">Say something to start. The first message becomes the title until you rename it.</p>`;
    if (scroll || atBottom) node.scrollTop = node.scrollHeight;
  }

  function toolChip(t) {
    const status = t.status === "completed" ? "pass" : t.status === "failed" ? "fail" : "";
    return `<span class="pill ${status}" title="${esc(t.title || "")}">${esc((t.title || t.kind || "tool").slice(0, 60))}</span>`;
  }

  function message(m) {
    const time = new Date(m.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (m.role === "error") return `<div class="chat-msg error"><div class="chat-msg-head">Not answered <span class="faint">${time}</span></div><div class="chat-msg-text">${esc(m.text)}</div></div>`;
    const who = m.role === "user" ? "You" : profileName(C.chat.profile);
    const tools = (m.tools || []).map(toolChip).join("");
    return `<div class="chat-msg ${m.role}"><div class="chat-msg-head">${esc(who)} <span class="faint">${time}</span></div>
      ${tools ? `<div class="chat-tools">${tools}</div>` : ""}<div class="chat-msg-text">${esc((m.text || "").trim()) || '<span class="faint">(no text reply)</span>'}</div></div>`;
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

  window.Chats = { mount, unmount, stateChanged };
})();
