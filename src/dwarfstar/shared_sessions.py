"""One conversation, two clients: Workstation chats and Zed agent threads share the agent's own session.

The canonical transcript of a DwarfStar conversation is the agent's append-only session file
(`<workspace>/.omp-dwarfstar-<profile>-agent/sessions/<cwd>/<time>_<session id>.jsonl`). Both clients
drive the same ACP agent (`bin/dwarfstar-acp --profile <id>`), so a session is shared when both use the
same workspace and profile:

  * Zed lists a workspace's sessions with ACP `session/list` and reopens one with `session/load`.
  * The Workstation resumes a session with `session/resume`.
  * The agent holds a single-writer lock on the session file, so two clients can never append to it at
    once; the second one gets an error, which the Workstation reports instead of starting a new session.

What is shared and what is not, stated plainly:

  * Shared: the session file and therefore the agent's context, compactions and the visible transcript.
  * Not shared: Zed's sidebar title and folder grouping (Zed's own database, never written here) and the
    Workstation's tags, stars and pins (the chat record).
  * Never stored by DwarfStar: reasoning. The agent's session file holds the model's thinking blocks as
    part of its own context; this module reads only user text, visible reply text and tool-call names.

Zed's database is opened read-only.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from dwarfstar import catalog, paths

ZED_DATABASE = Path.home() / "Library/Application Support/Zed/db/0-stable/db.sqlite"
# Workspaces that moved during the 2026-11 storage consolidation: old prefix -> where they live now (read-only).
MOVED_WORKSPACES = {
    Path.home() / "Library/Application Support/DwarfStar Workstation/scratch": paths.ARCHIVE / "workstation-app-support-2026-11/scratch",
}


def session_files(workspace: Path, session_id: str) -> list[Path]:
    if not session_id or "/" in session_id or not workspace.is_dir():
        return []
    return sorted(workspace.glob(f".omp-dwarfstar-*-agent/sessions/*/*_{session_id}.jsonl"))


def session_file(workspace: Path, session_id: str) -> Path | None:
    found = session_files(workspace, session_id)
    return found[0] if found else None


def _records(path: Path) -> list[dict]:
    values = []
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                values.append(json.loads(line))
            except ValueError:
                continue
    return values


def _time(value: str | None) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def active_branch(records: list[dict]) -> list[dict]:
    """The entries on the path from the root to the newest entry. A rewound branch stays in the file (the agent never
    deletes) but is not part of the conversation as it now stands."""
    tree = [r for r in records if r.get("id") and r.get("type") not in ("session", "title")]
    if not tree:
        return []
    by_id = {r["id"]: r for r in tree}
    chain, cursor, seen = [], tree[-1], set()
    while cursor and cursor["id"] not in seen:
        seen.add(cursor["id"])
        chain.append(cursor)
        cursor = by_id.get(cursor.get("parentId"))
    return list(reversed(chain))


def _blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


def visible_transcript(path: Path) -> list[dict]:
    """Chat messages from an agent session file: user text, reply text and tool-call names. Consecutive assistant
    messages of one turn (tool call, then answer) become one reply. Thinking blocks and tool output are skipped."""
    messages: list[dict] = []
    for entry in active_branch(_records(path)):
        if entry.get("type") == "compaction":
            messages.append({"role": "notice", "text": "The agent compacted its active context here; the full transcript is kept.",
                             "t": _time(entry.get("timestamp"))})
            continue
        if entry.get("type") != "message":
            continue
        message = entry.get("message") or {}
        role, blocks = message.get("role"), _blocks(message.get("content"))
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if role == "user":
            messages.append({"role": "user", "text": text, "t": _time(entry.get("timestamp"))})
        elif role == "assistant":
            tools = [{"id": b.get("id"), "title": b.get("name"), "kind": "tool", "status": "completed"}
                     for b in blocks if b.get("type") == "toolCall"]
            if messages and messages[-1]["role"] == "assistant":
                messages[-1]["text"] += text
                messages[-1]["tools"] += tools
                messages[-1]["t"] = _time(entry.get("timestamp"))
            else:
                messages.append({"role": "assistant", "text": text, "tools": tools, "t": _time(entry.get("timestamp"))})
    for message in messages:
        if message["role"] == "assistant":
            message["text"] = message["text"].strip("\n")
    return messages


def session_header(path: Path) -> dict:
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if value.get("type") == "session":
                return value
    return {}


# --------------------------------------------------------------------- Zed


def zed_rows(database: Path | None = None) -> list[dict]:
    database = database or ZED_DATABASE
    if not database.exists():
        return []
    db = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        db.row_factory = sqlite3.Row
        columns = {row[1] for row in db.execute("PRAGMA table_info(sidebar_threads)")}
        if not {"session_id", "agent_id", "folder_paths"} <= columns:
            return []
        return [{**dict(row), "thread_id": bytes(row["thread_id"]).hex() if isinstance(row["thread_id"], bytes) else row["thread_id"]}
                for row in db.execute("SELECT * FROM sidebar_threads WHERE agent_id LIKE 'DwarfStar %'")]
    finally:
        db.close()


def _locate(folder: str) -> tuple[Path, bool]:
    """(where the workspace is now, whether it moved to the archive)."""
    path = Path(folder)
    if path.is_dir():
        return path, False
    for old, new in MOVED_WORKSPACES.items():
        if path.is_relative_to(old):
            return new / path.relative_to(old), True
    return path, False


def zed_threads(database: Path | None = None) -> list[dict]:
    """DwarfStar threads in Zed's sidebar with what the Workstation can do with each:
    continue (live workspace, current profile), read (archived or retired profile) or missing."""
    labels = {p["label"]: p["id"] for p in catalog.profiles()}
    linked = linked_sessions()
    rows = []
    for row in zed_rows(database):
        folders = [f for f in (row.get("folder_paths") or "").split("\n") if f]
        session_id = row.get("session_id")
        workspace, archived = _locate(folders[0]) if folders else (None, False)
        source = session_file(workspace, session_id) if workspace and session_id else None
        profile = labels.get(row["agent_id"])
        if not session_id:
            state = "empty"
        elif source is None:
            state = "missing"
        elif archived or not profile or len(folders) != 1:
            state = "readable"
        else:
            state = "continuable"
        rows.append({
            "thread_id": row["thread_id"], "session_id": session_id, "agent": row["agent_id"], "profile": profile,
            "title": row.get("title_override") or row.get("title") or "Untitled thread", "workspace": str(workspace) if workspace else None,
            "zed_folder": folders[0] if folders else None, "archived_workspace": archived, "state": state,
            "updated": row.get("updated_at"), "chat": linked.get(session_id), "source": str(source) if source else None,
            "why": {"empty": "Zed recorded no agent session for this thread.",
                    "missing": "The session file is not on this Mac any more.",
                    "readable": ("The workspace was archived during the storage consolidation; it can be read, not continued."
                                 if archived else "This thread used a retired profile or several folders; it can be read, not continued."),
                    "continuable": "Continue it in the Workstation; Zed and the Workstation then share one agent session."}[state],
        })
    return sorted(rows, key=lambda r: str(r.get("updated") or ""), reverse=True)


def linked_sessions() -> dict[str, str]:
    """Agent session id -> Workstation chat id for every chat that has a session."""
    result = {}
    for path in (paths.CHATS).glob("c*/chat.json"):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if record.get("agent_session_id"):
            result[record["agent_session_id"]] = record["id"]
    return result


def zed_agent_name(profile_id: str) -> str:
    return catalog.profile(profile_id)["label"]
