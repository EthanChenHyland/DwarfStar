"""Workstation chats: durable conversations with the running model.

A chat talks to the model through the same ACP agent the editor uses
(`bin/dwarfstar-acp --profile <id>`), so tools, Full Auto, recovery and the
agent's native context compaction behave exactly as they do in Zed. Each turn
starts the agent, resumes the chat's agent session (or creates it on the first
turn), sends the message and records the reply. When the agent compacts its
active context, the chat continues in the same agent session; the full
transcript kept here is never compacted.

Storage, under the data root and never in the repository:

    <data>/chats/<id>/chat.json     the chat record (schema 1, see `new_record`)
    <data>/chats/<id>/workspace/    the agent's workspace and session bank for this chat
    <data>/chats/.deleted/          deleted chats, moved aside rather than erased

Only visible reply text and tool-call titles are stored. Reasoning (chain of
thought) is never stored or shown.
"""
from __future__ import annotations

import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

from dwarfstar import catalog
from dwarfstar.paths import CODE, DATA

SCHEMA = 1
ROOT = DATA / "chats"
ID_PATTERN = re.compile(r"^c\d{14}-[0-9a-f]{6}$")
TURN_TIMEOUT_S = 3600
PERMISSION_TIMEOUT_S = 600  # an unanswered tool approval is declined after this
TITLE_WORDS = 7
TAG_PATTERN = re.compile(r"^[\w][\w .+-]{0,31}$")
FORK_PREFACE_CHARS = 24000  # transcript carried into a fork's first turn (the agent session itself cannot be copied)


class ChatError(ValueError):
    pass


# --------------------------------------------------------------------- store


def _path(chat_id: str) -> Path:
    if not isinstance(chat_id, str) or not ID_PATTERN.match(chat_id):
        raise ChatError("Unknown chat")
    return ROOT / chat_id / "chat.json"


def _write(record: dict) -> dict:
    path = _path(record["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return record


def new_record(profile_id: str, now: float | None = None) -> dict:
    profile = catalog.profile(profile_id)
    model = catalog.model(profile["model"])
    now = now or time.time()
    return {
        "schema": SCHEMA,
        "id": "c" + time.strftime("%Y%m%d%H%M%S", time.localtime(now)) + "-" + secrets.token_hex(3),
        "title": "New chat",
        "title_manual": False,
        "created": now,
        "updated": now,
        "archived": False,
        "profile": profile_id,
        "model": model["id"],
        "reasoning": profile["reasoning_effort"],
        "context": {"tokens": 0, "window": model["context"]},
        "agent_session_id": None,
        "compactions": 0,
        "pinned": False,
        "starred": False,
        "tags": [],
        "messages": [],
    }


def create(profile_id: str) -> dict:
    return _write(new_record(profile_id))


def get(chat_id: str) -> dict:
    try:
        record = json.loads(_path(chat_id).read_text())
    except OSError as error:
        raise ChatError("Unknown chat") from error
    if record.get("schema") != SCHEMA:
        raise ChatError(f"Unsupported chat schema {record.get('schema')}")
    return record


def summary(record: dict) -> dict:
    last = next((m for m in reversed(record["messages"]) if m["role"] in ("user", "assistant")), None)
    return {k: record[k] for k in ("id", "title", "created", "updated", "archived", "profile", "model", "reasoning", "context")} | {
        "messages": len(record["messages"]), "preview": (last or {}).get("text", "")[:140], "pinned": bool(record.get("pinned")),
        "starred": bool(record.get("starred")), "tags": list(record.get("tags") or []), "forked_from": record.get("forked_from")}


def list_chats(query: str | None = None, archived: bool | None = False, tag: str | None = None, starred: bool = False) -> list[dict]:
    """Pinned chats first, then most recently updated. Search covers titles, tags and message text."""
    rows = []
    for path in ROOT.glob("c*/chat.json"):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if archived is not None and bool(record.get("archived")) != archived:
            continue
        if tag and tag not in (record.get("tags") or []):
            continue
        if starred and not record.get("starred"):
            continue
        if query:
            needle = query.lower()
            haystack = "\n".join([record.get("title", ""), " ".join(record.get("tags") or []),
                                  *(m.get("text", "") for m in record.get("messages", []))]).lower()
            if needle not in haystack:
                continue
        rows.append(summary(record))
    return sorted(rows, key=lambda row: (row["pinned"], row["updated"]), reverse=True)


def all_tags() -> list[dict]:
    counts: dict[str, int] = {}
    for path in ROOT.glob("c*/chat.json"):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not record.get("archived"):
            for tag in record.get("tags") or []:
                counts[tag] = counts.get(tag, 0) + 1
    return [{"tag": t, "chats": n} for t, n in sorted(counts.items(), key=lambda item: item[0].lower())]


def organize(chat_id: str, pinned: bool | None = None, starred: bool | None = None, tags: list | None = None) -> dict:
    """Pin, star or tag a chat. Organizing does not count as activity, so the chat keeps its place in Recent."""
    record = get(chat_id)
    if pinned is not None:
        record["pinned"] = bool(pinned)
    if starred is not None:
        record["starred"] = bool(starred)
    if tags is not None:
        clean = []
        for tag in tags:
            tag = " ".join(str(tag).split())
            if not TAG_PATTERN.match(tag):
                raise ChatError(f"A tag is 1-32 letters, digits, spaces or .+-_: {tag!r}")
            if tag not in clean:
                clean.append(tag)
        record["tags"] = clean[:12]
    return _write(record)


def fork(chat_id: str, upto: int | None = None, keep_messages: bool = True) -> dict:
    """A new chat with the same model and reasoning level. With messages, the transcript up to `upto` (exclusive) is
    copied and handed to the agent as context on the fork's first turn; the original chat is untouched."""
    source = get(chat_id)
    record = new_record(source["profile"])
    if keep_messages:
        record["tags"] = list(source.get("tags") or [])
        messages = [m for m in source["messages"][:upto] if m["role"] in ("user", "assistant")]
        record.update(title=(source["title"] + " (fork)")[:120], title_manual=True, messages=[dict(m) for m in messages],
                      forked_from={"id": chat_id, "title": source["title"], "messages": len(messages)})
    return _write(record)


def fork_preface(record: dict) -> str:
    """For a fork's first turn: the copied transcript, newest kept when it is long, as context for a fresh agent session."""
    if record.get("agent_session_id") or not record.get("forked_from"):
        return ""
    lines, size = [], 0
    for message in reversed(record["messages"][:-1]):
        line = f"{'User' if message['role'] == 'user' else 'Assistant'}: {message['text']}"
        if size + len(line) > FORK_PREFACE_CHARS:
            lines.append("[earlier messages omitted]")
            break
        lines.append(line)
        size += len(line)
    if not lines:
        return ""
    return ("This conversation continues an earlier one. Its transcript so far, for context:\n\n" + "\n\n".join(reversed(lines))
            + "\n\n---\n\n")


def export(chat_id: str, fmt: str) -> tuple[str, str, str]:
    """(filename, media type, body). Markdown holds what the chat shows: messages and tool titles, never reasoning."""
    record = get(chat_id)
    stem = re.sub(r"[^\w.-]+", "-", record["title"]).strip("-")[:60] or record["id"]
    if fmt == "json":
        return f"{stem}.json", "application/json", json.dumps(record, indent=1, ensure_ascii=False) + "\n"
    if fmt != "md":
        raise ChatError("Export as md or json")
    model = catalog.profile(record["profile"])["label"] if record["profile"] else record["model"]
    out = [f"# {record['title']}", "", f"- Model: {model}", f"- Created: {time.strftime('%Y-%m-%d %H:%M', time.localtime(record['created']))}",
           f"- Chat id: `{record['id']}`"]
    if record.get("tags"):
        out.append("- Tags: " + ", ".join(record["tags"]))
    for message in record["messages"]:
        heading = {"user": "You", "assistant": "Assistant", "error": "Error"}.get(message["role"], message["role"])
        out += ["", f"## {heading}", "", message.get("text", "")]
        for call in message.get("tools") or []:
            out.append(f"- tool: {call.get('title') or call.get('kind') or 'call'} ({call.get('status', 'done')})")
    return f"{stem}.md", "text/markdown; charset=utf-8", "\n".join(out) + "\n"


def bulk(chat_ids: list, action: str, confirm: str = "") -> dict:
    """Archive, unarchive or delete several chats. Deleting needs the confirmation text "delete N chats"."""
    ids = [str(i) for i in chat_ids][:500]
    for chat_id in ids:
        get(chat_id)  # every id must exist before anything changes
    if action in ("archive", "unarchive"):
        for chat_id in ids:
            set_archived(chat_id, action == "archive")
    elif action == "delete":
        expected = f"delete {len(ids)} chat{'s' if len(ids) != 1 else ''}"
        if confirm != expected:
            raise ChatError(f'Type "{expected}" to confirm')
        for chat_id in ids:
            delete(chat_id, chat_id)
    else:
        raise ChatError("Unknown bulk action")
    return {"ok": True, "action": action, "chats": ids}


def rename(chat_id: str, title: str) -> dict:
    title = " ".join(str(title).split())[:120]
    if not title:
        raise ChatError("A title cannot be empty")
    record = get(chat_id)
    record.update(title=title, title_manual=True, updated=time.time())
    return _write(record)


def set_archived(chat_id: str, archived: bool) -> dict:
    record = get(chat_id)
    record.update(archived=bool(archived), updated=time.time())
    return _write(record)


def delete(chat_id: str, confirm: str) -> dict:
    """Delete only with the chat's own id as confirmation. The chat is moved aside, not erased."""
    if confirm != chat_id:
        raise ChatError("Deleting a chat needs confirmation")
    source = _path(chat_id).parent
    if not source.is_dir():
        raise ChatError("Unknown chat")
    target = ROOT / ".deleted" / f"{chat_id}-{int(time.time())}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    return {"ok": True, "deleted": chat_id}


def auto_title(text: str) -> str:
    words = re.sub(r"\s+", " ", text).strip().split(" ")
    title = " ".join(words[:TITLE_WORDS])
    return (title + ("…" if len(words) > TITLE_WORDS else ""))[:80] or "New chat"


def add_message(record: dict, role: str, text: str, **fields) -> dict:
    record["messages"].append({"role": role, "text": text, "t": time.time(), **fields})
    if role == "user" and not record["title_manual"] and record["title"] == "New chat":
        record["title"] = auto_title(text)
    record["updated"] = time.time()
    return record


# --------------------------------------------------------------------- turns


def recover_interrupted() -> list[str]:
    """Called when the service starts, before any turn can run: a chat whose transcript ends with the user's message
    lost its reply to a crash. Say so in the chat; the agent session itself is intact and the next message resumes it."""
    recovered = []
    for path in ROOT.glob("c*/chat.json"):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        messages = record.get("messages") or []
        if messages and messages[-1].get("role") == "user":
            add_message(record, "error", "Interrupted: DwarfStar stopped before this reply finished. Send the message again to continue; "
                                         "the conversation and the agent's session are intact.", interrupted=True)
            _write(record)
            recovered.append(record["id"])
    return recovered


class AgentSession:
    """One short-lived ACP agent process for one turn of one chat."""

    on_permission = None  # set by Turns: asks the user and returns the chosen optionId (or None to decline)

    def __init__(self, profile_id: str, workspace: Path):
        workspace.mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            [str(CODE / "bin/dwarfstar-acp"), "--profile", profile_id, "--workspace", str(workspace)],
            cwd=str(workspace), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, start_new_session=True,
            env={**os.environ, "DWARFSTAR_TASK_ID": "chat-" + workspace.parent.name + "-" + secrets.token_hex(3)},
        )
        self.messages: queue.Queue = queue.Queue()
        self.sequence = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.process.stdout:
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                continue
        self.messages.put({"__eof": True})

    def send(self, value: dict) -> None:
        self.process.stdin.write(json.dumps(value) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict, timeout: float, on_update=None) -> dict:
        from dwarfstar.approval_policy import resolve_request

        self.sequence += 1
        ident = self.sequence
        self.send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                message = self.messages.get(timeout=0.5)
            except queue.Empty:
                continue
            if message.get("__eof"):
                raise ChatError("The agent exited during " + method)
            if message.get("method") == "session/update":
                if on_update:
                    on_update(message["params"].get("update", {}))
                continue
            if "method" in message and "id" in message:
                if message["method"] == "session/request_permission":
                    # The installation's approval mode decides. Full Auto approves at once; otherwise the chat shows the
                    # request with the agent's own options and waits for the user (declined after PERMISSION_TIMEOUT_S).
                    answer = resolve_request(message)
                    if answer is None and self.on_permission:
                        choice = self.on_permission(message["params"])
                        if choice:
                            answer = {"jsonrpc": "2.0", "id": message["id"], "result": {"outcome": {"outcome": "selected", "optionId": choice}}}
                    self.send(answer or {"jsonrpc": "2.0", "id": message["id"], "result": {"outcome": {"outcome": "cancelled"}}})
                else:
                    self.send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "not supported"}})
                continue
            if message.get("id") == ident:
                if "error" in message:
                    raise ChatError(f"{method}: {message['error'].get('message', message['error'])}")
                return message.get("result", {})
        raise ChatError(f"{method} timed out after {timeout:.0f} s")

    def close(self) -> None:
        try:
            self.process.stdin.close()
            self.process.wait(timeout=20)
        except Exception:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()


def open_session(agent: AgentSession, record: dict, workspace: Path) -> str:
    """Resume the chat's agent session so the agent's own (compacted) context carries over; create it the first time."""
    params = {"cwd": str(workspace), "mcpServers": []}
    if record.get("agent_session_id"):
        for method in ("session/resume", "session/load"):
            try:
                agent.request(method, {**params, "sessionId": record["agent_session_id"]}, 180)
                return record["agent_session_id"]
            except ChatError:
                continue
    return agent.request("session/new", params, 180)["sessionId"]


def agent_compactions(workspace: Path, session_id: str | None) -> int | None:
    """How many times the agent has compacted this chat's active context, from the agent's own session record.

    The agent keeps one append-only JSONL file per session under its workspace
    (`.omp-dwarfstar-<profile>-agent/sessions/<cwd>/<time>_<session id>.jsonl`); each automatic
    compaction is an entry of type "compaction". ACP does not report compactions as updates, so this
    file is the authoritative source. None when no record exists yet.
    """
    if not session_id:
        return None
    count, found = 0, False
    for path in workspace.glob(f".omp-dwarfstar-*-agent/sessions/*/*_{session_id}.jsonl"):
        found = True
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if '"compaction"' not in line:
                    continue
                try:
                    count += json.loads(line).get("type") == "compaction"
                except ValueError:
                    continue
    return count if found else None


class Turns:
    """Runs chat turns in the background; one turn per chat at a time. Live progress is kept in memory."""

    def __init__(self, notify=None):
        self.lock = threading.Lock()
        self.active: dict[str, dict] = {}
        self.answers: dict[str, tuple] = {}  # chat id -> (threading.Event, [choice])
        self.notify = notify or (lambda *a, **k: None)

    def progress(self, chat_id: str) -> dict | None:
        with self.lock:
            value = self.active.get(chat_id)
            return dict(value) if value else None

    def start(self, chat_id: str, text: str, running_models: list[str], agent_factory=AgentSession) -> dict:
        text = str(text).strip()
        if not text:
            raise ChatError("Write a message first")
        record = get(chat_id)
        if record["model"] not in running_models:
            name = catalog.model(record["model"])["display_name"]
            raise ChatError(f"This chat uses {name}. Start it from the Run view to continue.")
        with self.lock:
            if chat_id in self.active:
                raise ChatError("This chat is already answering")
            self.active[chat_id] = {"started": time.time(), "text": "", "tools": [], "phase": "starting"}
        record = _write(add_message(record, "user", text))
        prompt = fork_preface(record) + text
        threading.Thread(target=self._run, args=(chat_id, prompt, agent_factory), name=f"chat-{chat_id}", daemon=True).start()
        return {"ok": True, "accepted": True}

    def ask_permission(self, chat_id: str, params: dict) -> str | None:
        """Show a tool approval in the chat and wait for the user's choice. Only the tool title and kind are shown."""
        call = params.get("toolCall") or {}
        options = [{"id": o["optionId"], "name": o["name"], "kind": o["kind"]} for o in params.get("options") or []]
        event, choice = threading.Event(), [None]
        with self.lock:
            if chat_id not in self.active:
                return None
            self.answers[chat_id] = (event, choice)
            self.active[chat_id]["permission"] = {"title": call.get("title") or call.get("kind") or "tool call", "kind": call.get("kind"),
                                                  "options": options, "asked": time.time()}
            self.active[chat_id]["phase"] = "waiting for your approval"
        self.notify("chat", chat=chat_id, phase="permission")
        event.wait(PERMISSION_TIMEOUT_S)
        with self.lock:
            self.answers.pop(chat_id, None)
            if chat_id in self.active:
                self.active[chat_id].pop("permission", None)
                self.active[chat_id]["phase"] = "using tools"
        return choice[0] if choice[0] in {o["id"] for o in options} else None

    def answer_permission(self, chat_id: str, option_id: str | None) -> dict:
        with self.lock:
            pending = self.answers.get(chat_id)
            if not pending:
                raise ChatError("No approval is waiting in this chat")
            pending[1][0] = option_id
            pending[0].set()
        return {"ok": True}

    def _update(self, chat_id: str, update: dict) -> None:
        kind = update.get("sessionUpdate")
        with self.lock:
            live = self.active.get(chat_id)
            if live is None:
                return
            if kind == "agent_message_chunk":
                live["text"] += (update.get("content") or {}).get("text", "")
                live["phase"] = "answering"
            elif kind == "agent_thought_chunk":
                live["phase"] = "thinking"  # reasoning text itself is never kept
            elif kind in ("tool_call", "tool_call_update"):
                calls = {c["id"]: c for c in live["tools"]}
                call = calls.setdefault(update.get("toolCallId"), {"id": update.get("toolCallId")})
                for key in ("title", "kind", "status"):
                    if update.get(key) is not None:
                        call[key] = update[key]
                live["tools"] = list(calls.values())
                live["phase"] = "using tools"
            elif kind and "compact" in kind.lower():
                live["compacted"] = live.get("compacted", 0) + 1

    def _run(self, chat_id: str, text: str, agent_factory) -> None:
        record = get(chat_id)
        workspace = _path(chat_id).parent / "workspace"
        agent = None
        error = None
        result: dict = {}
        try:
            agent = agent_factory(record["profile"], workspace)
            agent.on_permission = lambda params: self.ask_permission(chat_id, params)
            agent.request("initialize", {"protocolVersion": 1, "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                         "terminal": False}, "clientInfo": {"name": "DwarfStar Workstation", "version": "1"}}, 180)
            session_id = open_session(agent, record, workspace)
            with self.lock:
                self.active[chat_id]["phase"] = "thinking"
            result = agent.request("session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
                                   TURN_TIMEOUT_S, on_update=lambda update: self._update(chat_id, update))
            record = get(chat_id)
            record["agent_session_id"] = session_id
        except Exception as exception:  # the chat must always end in a recorded state
            error = str(exception)
            record = get(chat_id)
        finally:
            if agent:
                agent.close()
        live = self.progress(chat_id) or {}
        usage = result.get("usage") or {}
        if usage.get("totalTokens"):
            record["context"]["tokens"] = int(usage["totalTokens"])
        recorded = agent_compactions(workspace, record.get("agent_session_id"))
        record["compactions"] = recorded if recorded is not None else record.get("compactions", 0) + live.get("compacted", 0)
        if error:
            add_message(record, "error", error)
        else:
            add_message(record, "assistant", live.get("text", ""), tools=live.get("tools", []), stop=result.get("stopReason"),
                        usage={k: usage.get(k) for k in ("inputTokens", "outputTokens", "totalTokens") if k in usage})
        _write(record)
        with self.lock:
            self.active.pop(chat_id, None)
        self.notify("chat", chat=chat_id, phase="finished", ok=error is None)
