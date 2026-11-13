"""Zed and Workstation share one agent session: discovery, the derived transcript, linking and refusals."""
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from dwarfstar import chats, paths, shared_sessions

SESSION = "01a1d2cf-7fbb-7498-82d6-158e93e1ac0b"


def write_session(workspace: Path, session_id: str = SESSION, namespace: str = "cyber-low", rewound: bool = False) -> Path:
    folder = workspace / f".omp-dwarfstar-{namespace}-agent/sessions/{chats.session_folder_name(workspace)}"
    folder.mkdir(parents=True)
    rows = [
        {"type": "title", "title": ""},
        {"type": "session", "id": session_id, "cwd": str(workspace), "timestamp": "2026-11-13T02:05:59Z"},
        {"type": "model_change", "id": "a", "parentId": None, "timestamp": "2026-11-13T02:06:00Z"},
        {"type": "message", "id": "u1", "parentId": "a", "timestamp": "2026-11-13T02:06:01Z",
         "message": {"role": "user", "content": [{"type": "text", "text": "Print the date"}]}},
        {"type": "message", "id": "a1", "parentId": "u1", "timestamp": "2026-11-13T02:06:02Z",
         "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "SECRET REASONING"},
                                                      {"type": "toolCall", "id": "c1", "name": "bash", "arguments": {"command": "date"}}]}},
        {"type": "message", "id": "r1", "parentId": "a1", "timestamp": "2026-11-13T02:06:03Z",
         "message": {"role": "toolResult", "content": [{"type": "text", "text": "TOOL OUTPUT"}]}},
        {"type": "message", "id": "a2", "parentId": "r1", "timestamp": "2026-11-13T02:06:04Z",
         "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "more"}, {"type": "text", "text": "\n\nIt is Thursday."}]}},
    ]
    if rewound:  # a branch the user rewound away from, then a new turn from the first reply
        rows += [
            {"type": "message", "id": "u2", "parentId": "a2", "timestamp": "2026-11-13T02:07:00Z",
             "message": {"role": "user", "content": [{"type": "text", "text": "abandoned question"}]}},
            {"type": "message", "id": "u3", "parentId": "a2", "timestamp": "2026-11-13T02:08:00Z",
             "message": {"role": "user", "content": "kept question"}},
            {"type": "compaction", "id": "k1", "parentId": "u3", "timestamp": "2026-11-13T02:08:01Z"},
        ]
    path = folder / f"2026-11-13T02-05-59-248Z_{session_id}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def zed_database(path: Path, rows: list[tuple]) -> Path:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE sidebar_threads (thread_id BLOB, session_id TEXT, agent_id TEXT, title TEXT, title_override TEXT,"
               " updated_at TEXT, folder_paths TEXT, archived INTEGER)")
    db.executemany("INSERT INTO sidebar_threads VALUES (?,?,?,?,NULL,?,?,0)", rows)
    db.commit()
    db.close()
    return path


class SharedSessionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for target, value in ((chats, "ROOT"), (paths, "CHATS")):
            patcher = mock.patch.object(target, value, self.root / "chats")
            patcher.start()
            self.addCleanup(patcher.stop)
        self.workspace = self.root / "project"
        self.workspace.mkdir()

    def test_transcript_has_visible_text_and_tool_names_only(self):
        messages = shared_sessions.visible_transcript(write_session(self.workspace))
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[1]["text"], "It is Thursday.")
        self.assertEqual([t["title"] for t in messages[1]["tools"]], ["bash"])
        dumped = json.dumps(messages)
        self.assertNotIn("SECRET REASONING", dumped)
        self.assertNotIn("TOOL OUTPUT", dumped)

    def test_transcript_follows_the_active_branch(self):
        messages = shared_sessions.visible_transcript(write_session(self.workspace, rewound=True))
        texts = [m["text"] for m in messages]
        self.assertIn("kept question", texts)
        self.assertNotIn("abandoned question", texts)
        self.assertEqual(messages[-1]["role"], "notice")

    def test_zed_threads_states(self):
        write_session(self.workspace)
        archived_old = Path.home() / "Library/Application Support/DwarfStar Workstation/scratch/x"
        database = zed_database(self.root / "zed.sqlite", [
            (b"\x01", SESSION, "DwarfStar Cyber LOW", "Live", "2026-11-13", str(self.workspace)),
            (b"\x02", "gone", "DwarfStar Cyber LOW", "Gone", "2026-11-12", str(self.root / "missing")),
            (b"\x03", None, "DwarfStar Cyber MEDIUM", "", "2026-11-11", str(self.workspace)),
            (b"\x04", "old", "DwarfStar Coding 128K", "Retired", "2026-10-01", str(archived_old)),
        ])
        threads = {t["title"]: t for t in shared_sessions.zed_threads(database)}
        self.assertEqual(threads["Live"]["state"], "continuable")
        self.assertEqual(threads["Live"]["profile"], "cyber-low")
        self.assertEqual(threads["Gone"]["state"], "missing")
        self.assertEqual(threads["Untitled thread"]["state"], "empty")
        self.assertEqual(threads["Retired"]["state"], "missing")
        self.assertTrue(threads["Retired"]["archived_workspace"])

    def test_link_uses_the_same_session_and_workspace_without_copying(self):
        write_session(self.workspace)
        database = zed_database(self.root / "zed.sqlite", [(b"\x01", SESSION, "DwarfStar Cyber LOW", "Live", "t", str(self.workspace))])
        record = chats.link_zed_thread(SESSION, database)
        self.assertEqual(record["agent_session_id"], SESSION)
        self.assertEqual(chats.workspace_of(record), self.workspace)
        self.assertEqual(record["transcript"], "agent_session")
        stored = json.loads((self.root / "chats" / record["id"] / "chat.json").read_text())
        self.assertEqual(stored["messages"], [])  # nothing copied: messages come from the session file
        self.assertEqual([m["role"] for m in record["messages"]], ["user", "assistant"])
        again = chats.link_zed_thread(SESSION, database)
        self.assertEqual(again["id"], record["id"])  # one chat per session

    def test_turn_on_linked_chat_resumes_and_never_forks(self):
        from tests.test_chats import FakeAgent

        write_session(self.workspace)
        database = zed_database(self.root / "zed.sqlite", [(b"\x01", SESSION, "DwarfStar Cyber LOW", "Live", "t", str(self.workspace))])
        record = chats.link_zed_thread(SESSION, database)
        seen = []

        class Agent(FakeAgent):
            def __init__(self, profile_id, workspace):
                seen.append(workspace)

            def request(self, method, params, timeout, on_update=None):
                if method == "session/resume":
                    raise chats.ChatError("Session publish lock unavailable for x: another writer holds the publish lock")
                if method == "session/new":
                    raise AssertionError("a shared chat must never start a new session")
                return {}

        turns = chats.Turns()
        turns._run(record["id"], "hello", Agent)
        self.assertEqual(seen, [self.workspace])
        after = chats.get(record["id"])
        self.assertEqual(after["agent_session_id"], SESSION)
        self.assertIn("in use by another client", after["messages"][-1]["text"])

    def test_share_refuses_when_session_lacks_forked_messages(self):
        chat_workspace = self.root / "chats"
        record = chats.new_record("cyber-low")
        record["agent_session_id"] = SESSION
        for text in ("one", "two", "three"):
            chats.add_message(record, "user", text)
        chats._write(record)
        write_session(chats.workspace_of(record))
        with self.assertRaises(chats.ChatError):
            chats.share_with_zed(record["id"], launch=False)
        self.assertTrue(chat_workspace.exists())

    def test_share_marks_chat_and_names_the_zed_agent(self):
        record = chats.new_record("cyber-low")
        record["agent_session_id"] = SESSION
        chats.add_message(record, "user", "Print the date")
        chats._write(record)
        write_session(chats.workspace_of(record))
        result = chats.share_with_zed(record["id"], launch=False)
        self.assertEqual(result["zed_agent"], "DwarfStar Cyber LOW")
        self.assertEqual(chats.get(record["id"])["transcript"], "agent_session")


if __name__ == "__main__":
    unittest.main()
