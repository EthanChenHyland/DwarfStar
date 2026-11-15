"""Backup and restore on a synthetic data root: exact logical recovery, safety backup, refusal of damaged archives."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from dwarfstar import backup, chats

SESSION = "01a1aaaa-0000-7000-8000-00000000abcd"


class BackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        self.data, self.out, self.code = root / "data", root / "out", root / "code"
        (self.code / "config").mkdir(parents=True)
        patches = [mock.patch.object(chats, "ROOT", self.data / "chats"), mock.patch.object(backup, "CODE", self.code)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        record = chats.new_record("cyber-low")
        record.update(title="Packet review", title_manual=True, tags=["pcap"], starred=True, agent_session_id=SESSION)
        chats.add_message(record, "user", "What ports are open?")
        chats.add_message(record, "assistant", "22 and 443.", tools=[{"id": "t1", "title": "$ nmap", "kind": "execute"}])
        chats._write(record)
        self.chat_id = record["id"]
        workspace = self.data / "chats" / self.chat_id / "workspace"
        session = workspace / ".omp-dwarfstar-cyber-low-agent" / "sessions" / "-ws" / f"2026_{SESSION}.jsonl"
        session.parent.mkdir(parents=True)
        session.write_text('{"type":"session","id":"%s"}\n' % SESSION)
        (session.parent / f".2026_{SESSION}.jsonl.lock.os").write_text("lock")
        (workspace / "report.md").write_text("agent wrote this for the user\n")
        for scratch in (".dwarfstar-cyber-low-tmp/bun-cache/x", ".omp-dwarfstar-cyber-low-agent/agent.db", ".dwarfstar-tooling-logs/a.log"):
            (workspace / scratch).parent.mkdir(parents=True, exist_ok=True)
            (workspace / scratch).write_text("recreatable")
        (self.data / "config").mkdir(parents=True)
        (self.data / "config" / "approval.json").write_text(json.dumps({"schema": 1, "approval_mode": "ask"}))
        (self.data / "config" / "permission-runtime.json").write_text("{}")
        (self.data / "state").mkdir()
        (self.data / "state" / "workstation.json").write_text(json.dumps({"schema": 1, "selected_profile": "cyber-low", "last_chat": self.chat_id}))
        (self.code / "config" / "local.json").write_text(json.dumps({"extra_tool_paths": []}))

    def snapshot(self) -> dict:
        record = json.loads((self.data / "chats" / self.chat_id / "chat.json").read_text())
        workspace = self.data / "chats" / self.chat_id / "workspace"
        return {"record": record, "session": sorted(p.read_text() for p in workspace.rglob("*.jsonl")),
                "report": (workspace / "report.md").read_text(),
                "approval": json.loads((self.data / "config" / "approval.json").read_text()),
                "state": json.loads((self.data / "state" / "workstation.json").read_text())}

    def make(self) -> Path:
        result = backup.create(out=self.out, data=self.data)
        self.assertTrue(result["ok"])
        return Path(result["backup"])

    def test_contents_and_exclusions(self):
        names = set(zipfile.ZipFile(self.make()).namelist())
        self.assertIn(f"chats/{self.chat_id}/chat.json", names)
        self.assertIn(f"chats/{self.chat_id}/workspace/report.md", names)
        self.assertTrue(any(n.endswith(f"{SESSION}.jsonl") for n in names))
        self.assertIn("config/approval.json", names)
        self.assertIn("code/config/local.json", names)
        self.assertIn("catalog/models.json", names)
        for excluded in ("bun-cache", "agent.db", "tooling-logs", "permission-runtime.json", ".lock"):
            self.assertFalse(any(excluded in n for n in names), excluded)
        self.assertEqual(Path(self.make()).stat().st_mode & 0o777, 0o600)

    def test_backup_change_restore_recovers_exactly(self):
        before = self.snapshot()
        archive = self.make()
        # damage the state: edit the chat, delete its session and the user's file, change settings
        chats.rename(self.chat_id, "Something else")
        workspace = self.data / "chats" / self.chat_id / "workspace"
        for path in workspace.rglob("*.jsonl"):
            path.unlink()
        (workspace / "report.md").unlink()
        (self.data / "config" / "approval.json").write_text(json.dumps({"schema": 1, "approval_mode": "full_auto"}))
        planned = backup.plan(archive, data=self.data)
        self.assertEqual(planned["chats_replaced"], [self.chat_id])
        result = backup.restore(archive, data=self.data, check_service=False)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(Path(result["safety_backup"]).is_file())  # the damaged state was kept, not destroyed

    def test_restore_into_an_empty_root_and_merge_keeps_other_chats(self):
        archive = self.make()
        other = chats.new_record("scope-low")
        other["title"] = "Made after the backup"
        chats._write(other)
        result = backup.restore(archive, data=self.data, check_service=False)
        self.assertTrue(result["ok"])
        self.assertTrue((self.data / "chats" / other["id"] / "chat.json").is_file())  # merge never deletes

    def test_local_configuration_is_never_overwritten(self):
        archive = self.make()
        (self.code / "config" / "local.json").write_text('{"changed": true}')
        self.assertIn("code/config/local.json", backup.plan(archive, data=self.data)["changes"]["skip"])
        backup.restore(archive, data=self.data, check_service=False)
        self.assertEqual(json.loads((self.code / "config" / "local.json").read_text()), {"changed": True})

    def test_damaged_or_unsafe_archives_are_refused(self):
        archive = self.make()
        tampered = self.out / "tampered.zip"
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(tampered, "w") as target:
            for item in source.infolist():
                blob = source.read(item)
                target.writestr(item, blob.replace(b"22 and 443", b"23 and 443") if item.filename.endswith("chat.json") else blob)
        with self.assertRaisesRegex(ValueError, "checksum"):
            backup.read(tampered)
        unsafe = self.out / "unsafe.zip"
        with zipfile.ZipFile(unsafe, "w") as target:
            target.writestr("manifest.json", json.dumps({"kind": "DwarfStar backup", "format": 1, "files": [
                {"path": "../escape.txt", "bytes": 1, "sha256": "x"}]}))
            target.writestr("../escape.txt", "x")
        with self.assertRaises(ValueError):
            backup.read(unsafe)
        newer = self.out / "newer.zip"
        with zipfile.ZipFile(newer, "w") as target:
            target.writestr("manifest.json", json.dumps({"kind": "DwarfStar backup", "format": 1, "files": [], "schemas": {"chat": 99}}))
        with self.assertRaisesRegex(ValueError, "newer"):
            backup.read(newer)


if __name__ == "__main__":
    unittest.main()
