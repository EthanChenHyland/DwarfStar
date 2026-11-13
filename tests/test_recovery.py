"""Crash and restart recovery: saved state is reconciled with reality, and nothing valid is killed."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("DWARFSTAR_MODEL", "scope")
from dwarfstar import chats, runtime_control  # noqa: E402
from dwarfstar.telemetry import hub  # noqa: E402


class TelemetryRecoveryTests(unittest.TestCase):
    def test_sessions_left_open_by_a_crash_are_finalized_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "20261113-000000-scope.jsonl").write_text(json.dumps({"kind": "tick"}) + "\n")
            (root / "20261113-000100-cyber.jsonl").write_text(json.dumps({"kind": "session_end", "reason": "model stopped"}) + "\n")
            self.assertEqual(hub.finalize_open_sessions(root), ["20261113-000000-scope"])
            last = json.loads((root / "20261113-000000-scope.jsonl").read_text().splitlines()[-1])
            self.assertEqual(last["kind"], "session_end")
            self.assertEqual(hub.finalize_open_sessions(root), [])


class ChatRecoveryTests(unittest.TestCase):
    def test_a_chat_interrupted_mid_reply_is_marked_and_kept(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(chats, "ROOT", Path(directory)):
            broken, done = chats.create("scope-low"), chats.create("cyber-low")
            chats._write(chats.add_message(chats.get(broken["id"]), "user", "hello"))
            record = chats.add_message(chats.get(done["id"]), "user", "hi")
            chats._write(chats.add_message(record, "assistant", "hello"))
            self.assertEqual(chats.recover_interrupted(), [broken["id"]])
            messages = chats.get(broken["id"])["messages"]
            self.assertEqual([m["role"] for m in messages], ["user", "error"])
            self.assertTrue(messages[-1]["interrupted"])
            self.assertEqual(len(chats.get(done["id"])["messages"]), 2)
            self.assertEqual(chats.recover_interrupted(), [])  # idempotent


class ReconcileTests(unittest.TestCase):
    """runtime_control.reconcile() with the process table and endpoint replaced by fakes."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.state = Path(self.directory.name)
        # Splash's long-lived server process, as the launcher leaves it after its re-exec
        self.argv = [str(runtime_control.SERVER), "-u", "-P", "-m", "server.server"]
        self.config = {"argv": self.argv, "context": 131072, "endpoint": "http://127.0.0.1:8030/v1", "port": 8030}
        self.signals = []

    def tearDown(self):
        self.directory.cleanup()

    def run_reconcile(self, ledger=None, processes=None, healthy=True, listener=None):
        processes = processes or {}
        if ledger is not None:
            (self.state / "server.json").write_text(json.dumps(ledger))

        def identity(pid):
            command = processes.get(pid)
            if command is None:
                return None
            return {"pid": pid, "birth": "b", "command": command, "argv_sha256": hashlib.sha256(command.encode()).hexdigest()}

        with mock.patch.object(runtime_control, "STATE", self.state), \
             mock.patch.object(runtime_control, "config", return_value=self.config), \
             mock.patch.object(runtime_control, "identity", identity), \
             mock.patch.object(runtime_control, "heavyweight_processes",
                               return_value=[(pid, Path(cmd.split()[0]).name) for pid, cmd in processes.items()]), \
             mock.patch.object(runtime_control, "health", return_value={"ok": healthy}), \
             mock.patch.object(runtime_control, "listening_pid", return_value=listener), \
             mock.patch.object(runtime_control, "emit"), \
             mock.patch.object(runtime_control.os, "kill", side_effect=lambda *a: self.signals.append(a)):
            return runtime_control.reconcile()

    def test_a_ledger_whose_process_exited_is_retired(self):
        result = self.run_reconcile(ledger={"pid": 41, "birth": "b", "argv_sha256": "x"})
        self.assertEqual(result["ledger"], "retired: process gone")
        self.assertFalse((self.state / "server.json").exists())
        self.assertTrue((self.state / "last-stopped.json").exists())

    def test_a_reused_pid_is_never_signalled(self):
        result = self.run_reconcile(ledger={"pid": 42, "birth": "b", "argv_sha256": "old"}, processes={42: "/usr/bin/some-other-tool --x"})
        self.assertIn("another process", result["ledger"])
        self.assertEqual(self.signals, [])

    def test_a_valid_running_model_is_kept(self):
        command = " ".join(self.argv)
        ledger = {"pid": 43, "birth": "b", "argv_sha256": hashlib.sha256(command.encode()).hexdigest()}
        result = self.run_reconcile(ledger=ledger, processes={43: command})
        self.assertEqual(result["ledger"], "valid")
        self.assertTrue((self.state / "server.json").exists())
        self.assertEqual(self.signals, [])

    def test_an_orphaned_server_of_this_model_is_adopted_not_restarted(self):
        result = self.run_reconcile(processes={44: " ".join(self.argv)}, listener=44)
        self.assertEqual((result["ledger"], result["adopted"]), ("adopted", 44))
        self.assertEqual(json.loads((self.state / "server.json").read_text())["pid"], 44)
        self.assertEqual(self.signals, [])

    def test_an_unhealthy_orphan_is_not_adopted(self):
        result = self.run_reconcile(processes={45: " ".join(self.argv)}, healthy=False, listener=45)
        self.assertIsNone(result["adopted"])

    def test_a_server_on_another_port_is_not_adopted(self):
        result = self.run_reconcile(processes={47: " ".join(self.argv)}, listener=None)
        self.assertIsNone(result["adopted"])
        self.assertEqual(self.signals, [])

    def test_a_foreign_server_is_reported_and_left_alone(self):
        foreign = "/opt/other/ds4-server -m /elsewhere/other.gguf --port 9999"
        result = self.run_reconcile(processes={46: foreign})
        self.assertEqual([f["pid"] for f in result["foreign"]], [46])
        self.assertEqual(self.signals, [])
        self.assertFalse((self.state / "server.json").exists())


if __name__ == "__main__":
    unittest.main()
