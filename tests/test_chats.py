"""Workstation chats: storage, titles, archive/delete, and turns through a simulated ACP agent."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from dwarfstar import chats


class FakeAgent:
    """Speaks just enough ACP: records the methods called and streams a scripted turn."""

    calls: list = []
    total_tokens = 91000

    def __init__(self, profile_id, workspace):
        self.profile_id = profile_id
        workspace.mkdir(parents=True, exist_ok=True)

    def request(self, method, params, timeout, on_update=None):
        FakeAgent.calls.append((method, params.get("sessionId")))
        if method == "session/new":
            return {"sessionId": "agent-session-1"}
        if method == "session/prompt":
            for update in ({"sessionUpdate": "agent_thought_chunk", "content": {"text": "secret reasoning"}},
                           {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "$ ls", "kind": "execute", "status": "pending"},
                           {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed"},
                           {"sessionUpdate": "context_compaction", "summary": "older turns condensed"},
                           {"sessionUpdate": "agent_message_chunk", "content": {"text": "Hello "}},
                           {"sessionUpdate": "agent_message_chunk", "content": {"text": "there."}}):
                on_update(update)
            return {"stopReason": "end_turn", "usage": {"inputTokens": 10, "outputTokens": 3, "totalTokens": FakeAgent.total_tokens}}
        return {}

    def close(self):
        pass


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(chats, "ROOT", Path(self.directory.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.directory.cleanup)
        FakeAgent.calls = []
        FakeAgent.total_tokens = 91000

    def turn(self, turns, chat_id, text):
        turns.start(chat_id, text, ["cyber"], agent_factory=FakeAgent)
        deadline = time.time() + 5
        while turns.progress(chat_id) and time.time() < deadline:
            time.sleep(0.01)
        return chats.get(chat_id)

    def test_records_carry_the_required_metadata(self):
        record = chats.create("cyber-low")
        for key in ("id", "title", "created", "updated", "archived", "model", "reasoning", "context"):
            self.assertIn(key, record)
        self.assertEqual((record["model"], record["reasoning"], record["context"]["window"]), ("cyber", "low", 131072))
        self.assertTrue((Path(self.directory.name) / record["id"] / "chat.json").is_file())

    def test_turns_resume_the_same_agent_session_and_never_store_reasoning(self):
        turns = chats.Turns()
        chat = chats.create("cyber-low")
        first = self.turn(turns, chat["id"], "Please list the files in this folder")
        self.assertEqual(first["messages"][-1]["text"], "Hello there.")
        self.assertEqual(first["agent_session_id"], "agent-session-1")
        self.assertEqual(first["title"], "Please list the files in this folder")
        self.assertEqual(first["context"]["tokens"], 91000)
        self.assertEqual(first["compactions"], 1)
        self.assertEqual(first["messages"][-1]["tools"][0]["status"], "completed")
        self.assertNotIn("secret reasoning", str(first))
        second = self.turn(turns, chat["id"], "And now?")
        self.assertIn(("session/resume", "agent-session-1"), FakeAgent.calls)
        self.assertEqual(len(second["messages"]), 4)  # the full transcript stays, whatever the agent compacted

    def test_compaction_is_read_from_the_agents_own_session_record(self):
        """The agent compacts inside its session; the chat keeps its id, the session and the full transcript."""
        turns = chats.Turns()
        chat = chats.create("cyber-low")
        first = self.turn(turns, chat["id"], "Inspect the logs and remember the error count")
        workspace = Path(self.directory.name) / chat["id"] / "workspace"
        bank = workspace / ".omp-dwarfstar-cyber-low-agent" / "sessions" / "-chat"
        bank.mkdir(parents=True)
        record = bank / "2026-11-12T00-00-00-000Z_agent-session-1.jsonl"
        record.write_text("\n".join(json.dumps(e) for e in (
            {"type": "session", "id": "agent-session-1"}, {"type": "message", "role": "user"},
            {"type": "compaction", "summary": "older turns condensed; error count 3 kept"}, {"type": "message", "role": "assistant"})) + "\n")
        FakeAgent.total_tokens = 12000  # the agent's active context after compaction is smaller
        second = self.turn(turns, chat["id"], "What was the error count?")
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["agent_session_id"], first["agent_session_id"])
        self.assertIn(("session/resume", "agent-session-1"), FakeAgent.calls)
        self.assertEqual(second["compactions"], 1)
        self.assertLess(second["context"]["tokens"], first["context"]["tokens"])
        self.assertEqual([m["role"] for m in second["messages"]], ["user", "assistant", "user", "assistant"])
        self.assertEqual(second["messages"][1]["tools"][0]["title"], "$ ls")  # tool history survives in the transcript
        self.assertNotIn("secret reasoning", json.dumps(second))

    def test_the_agent_keeps_automatic_compaction_enabled(self):
        from dwarfstar.profiles import BASE_AGENT_CONFIG

        self.assertNotIn("compaction", BASE_AGENT_CONFIG)  # the agent's default (compaction.enabled: true) applies

    def test_manual_titles_are_never_overwritten(self):
        turns = chats.Turns()
        chat = chats.create("cyber-low")
        chats.rename(chat["id"], "My research notes")
        self.assertEqual(self.turn(turns, chat["id"], "first message")["title"], "My research notes")

    def test_archive_search_and_confirmed_delete(self):
        a, b = chats.create("cyber-low"), chats.create("scope-low")
        chats.rename(a["id"], "Packet capture review")
        chats.set_archived(b["id"], True)
        self.assertEqual([c["id"] for c in chats.list_chats()], [a["id"]])
        self.assertEqual([c["id"] for c in chats.list_chats(archived=True)], [b["id"]])
        self.assertEqual([c["id"] for c in chats.list_chats(query="packet")], [a["id"]])
        chats.set_archived(b["id"], False)
        self.assertEqual(len(chats.list_chats()), 2)
        with self.assertRaises(chats.ChatError):
            chats.delete(a["id"], confirm="yes")
        chats.delete(a["id"], confirm=a["id"])
        self.assertEqual([c["id"] for c in chats.list_chats()], [b["id"]])
        self.assertTrue(any((Path(self.directory.name) / ".deleted").iterdir()))

    def test_a_chat_needs_its_own_model_running(self):
        chat = chats.create("swift-medium")
        with self.assertRaises(chats.ChatError) as caught:
            chats.Turns().start(chat["id"], "hello", ["cyber"], agent_factory=FakeAgent)
        self.assertIn("Swift", str(caught.exception))

    def test_ids_cannot_escape_the_chat_directory(self):
        with self.assertRaises(chats.ChatError):
            chats.get("../../etc/passwd")


if __name__ == "__main__":
    unittest.main()
