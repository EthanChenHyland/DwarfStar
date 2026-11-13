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
    <data>/chats/<id>/workspace/    the agent's workspace and session bank for this chat (or the record's
                                    `workspace`, for a chat continuing a Zed thread in its project folder)
    <data>/chats/.deleted/          deleted chats, moved aside rather than erased

Only visible reply text and tool-call titles are stored. Reasoning (chain of
thought) is never stored or shown.

A shared chat (`"transcript": "agent_session"`, see shared_sessions.py) is one whose agent session is also
used from Zed. Its messages are read from the agent's session file, the one canonical transcript; the
record keeps only Workstation-side state (title, tags, errors and notices).
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

from dwarfstar import catalog, shared_sessions
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
    return _shared_view(record)


def workspace_of(record: dict) -> Path:
    return Path(record["workspace"]) if record.get("workspace") else _path(record["id"]).parent / "workspace"


def _shared_view(record: dict) -> dict:
    """For a shared chat: messages from the agent session file, plus the Workstation's own errors and notices and a
    message the agent has not written yet (a turn that is starting)."""
    if record.get("transcript") != "agent_session":
        return record
    source = shared_sessions.session_file(workspace_of(record), record.get("agent_session_id"))
    if source is None:
        record["transcript_status"] = "The agent session file for this chat is missing; showing the last copy the Workstation saw."
        return record
    derived = [{**m, "source": "agent"} for m in shared_sessions.visible_transcript(source)]
    newest = max((m.get("t") or 0 for m in derived), default=0)
    local = [m for m in record["messages"] if m.get("source") != "agent" and
             (m["role"] in ("error", "notice") or (m["role"] == "user" and (m.get("t") or 0) > newest))]
    record["messages"] = sorted(derived + local, key=lambda m: m.get("t") or 0)
    context = shared_sessions.context_tokens(source)
    if context:
        record["context"]["tokens"] = context
    record.pop("transcript_status", None)
    return record


def link_zed_thread(session_id: str, database: Path | None = None) -> dict:
    """Continue a Zed thread in the Workstation: a chat on the thread's own agent session and project folder. Nothing
    is copied; an already linked thread returns its chat."""
    thread = next((t for t in shared_sessions.zed_threads(database) if t["session_id"] == session_id), None)
    if thread is None:
        raise ChatError("No DwarfStar thread in Zed has that session")
    if thread["chat"]:
        return get(thread["chat"])
    if thread["state"] != "continuable":
        raise ChatError(thread["why"])
    record = new_record(thread["profile"])
    record.update(title=thread["title"][:120], title_manual=True, workspace=thread["workspace"], agent_session_id=session_id,
                  transcript="agent_session", shared={"client": "zed", "agent": thread["agent"], "thread": thread["thread_id"],
                                                      "linked": time.time()})
    _write(record)
    return get(record["id"])


def share_with_zed(chat_id: str, launch: bool = True) -> dict:
    """Make a chat's agent session the canonical transcript and open its workspace in Zed, where the matching DwarfStar
    agent lists the session in its history. Refused when the session does not hold every message the chat shows
    (a fork's copied messages): Zed could not show them, and the chat would silently lose them."""
    record = get(chat_id)
    if not record.get("agent_session_id"):
        raise ChatError("Send a first message, so the chat has an agent session to share")
    workspace = workspace_of(record)
    source = shared_sessions.session_file(workspace, record["agent_session_id"])
    if source is None:
        raise ChatError("This chat's agent session file is missing")
    if record.get("transcript") != "agent_session":
        session_turns = sum(m["role"] == "user" for m in shared_sessions.visible_transcript(source))
        chat_turns = sum(m["role"] == "user" for m in record["messages"])
        if session_turns < chat_turns:
            raise ChatError(f"The agent session holds {session_turns} of this chat's {chat_turns} messages (the rest were copied "
                            "into a fork), so Zed could not show the whole conversation.")
        record.update(transcript="agent_session", shared={"client": "zed", "agent": shared_sessions.zed_agent_name(record["profile"]),
                                                          "linked": time.time()})
        _write(record)
    agent = record["shared"]["agent"]
    if launch:
        subprocess.run(["open", "-a", "Zed", str(workspace)], check=False)
    return {"ok": True, "chat": chat_id, "workspace": str(workspace), "session": record["agent_session_id"], "zed_agent": agent,
            "how": f"In Zed's agent panel, choose \"{agent}\" and open this conversation from its history. One client at a time: "
                   "while one is answering, the other cannot write to the session."}


def summary(record: dict) -> dict:
    last = next((m for m in reversed(record["messages"]) if m["role"] in ("user", "assistant")), None)
    return {k: record[k] for k in ("id", "title", "created", "updated", "archived", "profile", "model", "reasoning", "context")} | {
        "messages": len(record["messages"]), "preview": (last or {}).get("text", "")[:140], "pinned": bool(record.get("pinned")),
        "starred": bool(record.get("starred")), "tags": list(record.get("tags") or []), "forked_from": record.get("forked_from"),
        "shared": bool(record.get("shared"))}


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
        record.update(title=(source["title"] + " (fork)")[:120], title_manual=True,
                      messages=[{k: v for k, v in m.items() if k != "source"} for m in messages],
                      forked_from={"id": chat_id, "title": source["title"], "messages": len(messages)})
    return _write(record)


def transcript_preface(record: dict) -> str:
    """The conversation so far (newest kept when long), as context for a fresh agent session."""
    lines, size = [], 0
    for message in reversed([m for m in record["messages"][:-1] if m["role"] in ("user", "assistant")]):
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


def fork_preface(record: dict) -> str:
    """For a fork's first turn: the copied transcript as context for its fresh agent session."""
    if record.get("agent_session_id") or not record.get("forked_from"):
        return ""
    return transcript_preface(record)


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
            cwd=str(workspace), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, start_new_session=True,
            env={**os.environ, "DWARFSTAR_TASK_ID": "chat-" + workspace.parent.name + "-" + secrets.token_hex(3)},
        )
        self.messages: queue.Queue = queue.Queue()
        self.sequence = 0
        self.stderr_tail: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stderr(self) -> None:
        for line in self.process.stderr:  # kept short: only to say why the agent refused to start
            self.stderr_tail = (self.stderr_tail + [line.rstrip()])[-20:]

    def exit_reason(self) -> str:
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        time.sleep(0.1)
        line = next((l for l in reversed(self.stderr_tail) if re.match(r"^\w+(Error|Exception): ", l)), "")
        if "already owns this workspace" in line:
            return ("This conversation's workspace is open in another DwarfStar agent (for example Zed's agent panel). "
                    "Close that thread or project in Zed, then send again.")
        return line.split(": ", 1)[1] if line else ""

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
                reason = self.exit_reason()
                raise ChatError(reason or "The agent exited during " + method)
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


def session_folder_name(workspace: Path) -> str:
    """The agent files a session under its working directory relative to home, '/' written as '-'."""
    try:
        relative = workspace.resolve().relative_to(Path.home().resolve())
    except ValueError:
        relative = workspace.resolve()
    return "-" + str(relative).strip("/").replace("/", "-")


def relocate_agent_session(workspace: Path, session_id: str | None) -> list[str]:
    """A chat whose folder moved (a data-root migration) keeps its agent session under the old path's name, where the
    agent no longer looks. Move that session's files into the folder for the current path; nothing else changes."""
    if not session_id:
        return []
    moved = []
    for sessions in workspace.glob(".omp-dwarfstar-*-agent/sessions"):
        target = sessions / session_folder_name(workspace)
        for path in sessions.glob(f"*/*_{session_id}.jsonl*"):  # the session and its lock file
            if path.parent != target:
                target.mkdir(parents=True, exist_ok=True)
                path.rename(target / path.name)
                if not path.name.startswith("."):
                    moved.append(path.name)
        for path in target.glob(f"*_{session_id}.jsonl"):
            _set_session_cwd(path, workspace)
    return moved


def _set_session_cwd(path: Path, workspace: Path) -> None:
    """The session record's header names its working directory; after a move it must name the current one. Only that
    field of the "session" line changes; every other line is kept byte for byte."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, line in enumerate(lines[:5]):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") != "session":
            continue
        if entry.get("cwd") != str(workspace):
            entry["cwd"] = str(workspace)
            lines[index] = json.dumps(entry, separators=(",", ":"), ensure_ascii=False) + "\n"
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text("".join(lines), encoding="utf-8")
            temporary.replace(path)
        break


def open_session(agent: AgentSession, record: dict, workspace: Path) -> tuple[str, bool]:
    """Resume the chat's agent session so the agent's own (compacted) context carries over; create it the first time.
    Returns (session id, resumed). A session that exists but cannot be resumed is never replaced silently: the caller
    records the change in the chat and hands the new session the transcript."""
    params = {"cwd": str(workspace), "mcpServers": []}
    if record.get("agent_session_id"):
        relocate_agent_session(workspace, record["agent_session_id"])
        for method in ("session/resume", "session/load"):
            try:
                agent.request(method, {**params, "sessionId": record["agent_session_id"]}, 180)
                return record["agent_session_id"], True
            except ChatError as error:
                if re.search(r"\block\b", str(error), re.I):
                    raise ChatError("This conversation's agent session is in use by another client (for example Zed). "
                                    "Finish or close it there, then send again.") from error
                continue
        if record.get("transcript") == "agent_session":
            raise ChatError("The agent could not resume this shared session; a new session would split the conversation "
                            "between Zed and the Workstation, so nothing was sent.")
    return agent.request("session/new", params, 180)["sessionId"], False


def probe_resume(chat_id: str, agent_factory=None) -> dict:
    """Check, without sending anything, that the agent resumes this chat's own session (no fallback to a new one).
    The chat's model must be running; the chat is not modified."""
    record = get(chat_id)
    workspace = workspace_of(record)
    if not record.get("agent_session_id"):
        return {"ok": False, "chat": chat_id, "error": "no agent session yet"}
    agent = (agent_factory or AgentSession)(record["profile"], workspace)
    try:
        agent.request("initialize", {"protocolVersion": 1, "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                     "terminal": False}, "clientInfo": {"name": "DwarfStar Workstation", "version": "1"}}, 180)
        moved = relocate_agent_session(workspace, record["agent_session_id"])
        agent.request("session/resume", {"cwd": str(workspace), "mcpServers": [], "sessionId": record["agent_session_id"]}, 180)
        return {"ok": True, "chat": chat_id, "session": record["agent_session_id"], "relocated": moved}
    except ChatError as error:
        return {"ok": False, "chat": chat_id, "session": record["agent_session_id"], "error": str(error)}
    finally:
        agent.close()


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
        workspace = workspace_of(record)
        agent = None
        error = None
        result: dict = {}
        try:
            agent = agent_factory(record["profile"], workspace)
            agent.on_permission = lambda params: self.ask_permission(chat_id, params)
            agent.request("initialize", {"protocolVersion": 1, "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                         "terminal": False}, "clientInfo": {"name": "DwarfStar Workstation", "version": "1"}}, 180)
            session_id, resumed = open_session(agent, record, workspace)
            if record.get("agent_session_id") and not resumed:
                # The agent lost this chat's session: say so in the chat and give the new session the transcript.
                previous = record["agent_session_id"]
                record = get(chat_id)
                preface = transcript_preface(record)  # before the notice: the last message is this turn's own
                add_message(record, "notice", "The agent could not resume this chat's earlier session, so this reply "
                                              "comes from a new agent session that was given the conversation so far.",
                            previous_session=previous, new_session=session_id)
                _write(record)
                text = preface + text
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
        source = shared_sessions.session_file(workspace, record.get("agent_session_id"))
        context = shared_sessions.context_tokens(source) if source else None
        if context or usage.get("totalTokens"):
            record["context"]["tokens"] = context or int(usage["totalTokens"])
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
