"""Persistent data migrations on synthetic fixtures of every earlier chat shape (no real chats)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dwarfstar import chats, migrations

FIXTURES = Path(__file__).parent / "fixtures" / "chat-records"
KEEP = ("role", "text", "t", "tools", "stop", "usage")


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class ChatMigrationTests(unittest.TestCase):
    def test_every_fixture_reaches_the_current_schema(self):
        for path in sorted(FIXTURES.glob("*.json")):
            record, _ = migrations.migrate_record("chat", json.loads(path.read_text()))
            self.assertEqual(record["schema"], chats.SCHEMA, path.name)
            for key in ("title", "title_manual", "archived", "pinned", "starred", "tags", "compactions", "agent_session_id",
                        "context", "created", "updated", "messages"):
                self.assertIn(key, record, f"{path.name}: {key}")

    def test_messages_survive_exactly(self):
        for path in sorted(FIXTURES.glob("*.json")):
            before = json.loads(path.read_text())
            after, _ = migrations.migrate_record("chat", before)
            self.assertEqual(len(after["messages"]), len(before["messages"]), path.name)
            for old, new in zip(before["messages"], after["messages"]):
                for key in KEEP:
                    self.assertEqual(old.get(key), new.get(key), f"{path.name}: {key}")

    def test_reasoning_is_never_carried_into_the_workstation_record(self):
        after, changed = migrations.migrate_record("chat", fixture("v1-pre-zed-with-reasoning.json"))
        self.assertTrue(changed)
        text = json.dumps(after)
        self.assertNotIn("SECRET CHAIN OF THOUGHT", text)
        self.assertNotIn("MORE SECRET", text)

    def test_organization_session_fork_and_zed_links_survive(self):
        organized, _ = migrations.migrate_record("chat", fixture("v1-pre-zed-with-reasoning.json"))
        self.assertEqual((organized["pinned"], organized["starred"], organized["tags"], organized["archived"]),
                         (True, True, ["pcap", "review"], True))
        self.assertEqual(organized["agent_session_id"], "01a1aaaa-0000-7000-8000-000000000002")
        fork, _ = migrations.migrate_record("chat", fixture("v1-fork.json"))
        self.assertEqual(fork["forked_from"]["id"], "c20261105100000-bbbbbb")
        zed, _ = migrations.migrate_record("chat", fixture("v1-zed-old-workspace.json"))
        self.assertEqual((zed["transcript"], zed["shared"]["client"]), ("agent_session", "zed"))
        self.assertEqual(zed["workspace"], fixture("v1-zed-old-workspace.json")["workspace"])  # never rewritten

    def test_defaults_for_fields_earlier_builds_omitted(self):
        early, _ = migrations.migrate_record("chat", fixture("v0-noschema.json"))
        self.assertEqual((early["pinned"], early["starred"], early["tags"], early["archived"], early["agent_session_id"]),
                         (False, False, [], False, None))

    def test_idempotent(self):
        for path in sorted(FIXTURES.glob("*.json")):
            once, _ = migrations.migrate_record("chat", json.loads(path.read_text()))
            twice, changed = migrations.migrate_record("chat", once)
            self.assertEqual(once, twice)
            self.assertFalse(changed)

    def test_newer_schema_is_refused(self):
        with self.assertRaises(migrations.SchemaError):
            migrations.migrate_record("chat", {"schema": chats.SCHEMA + 1, "id": "x", "messages": []})


class StoreMigrationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.data = Path(self.folder.name)
        for path in FIXTURES.glob("*.json"):
            record = json.loads(path.read_text())
            target = self.data / "chats" / record["id"] / "chat.json"
            target.parent.mkdir(parents=True)
            shutil.copy(path, target)
        (self.data / "state").mkdir()
        (self.data / "state" / "workstation.json").write_text(json.dumps({"selected_profile": "cyber-low", "last_chat": "c1"}))
        (self.data / "config").mkdir()
        (self.data / "config" / "permission-runtime.json").write_text(json.dumps(
            {"policy": "dwarfstar-approval-v2", "cli_sha256": "ab", "reversal_legacy_record": "/old/path"}))

    def test_dry_run_writes_nothing(self):
        before = {p: p.read_bytes() for p in self.data.rglob("*.json")}
        result = migrations.migrate_all(apply=False, root=self.data)
        self.assertEqual(len(result["changed"]), 7)
        self.assertEqual(before, {p: p.read_bytes() for p in self.data.rglob("*.json")})
        self.assertFalse((self.data / "backups").exists())

    def test_apply_backs_up_then_migrates_and_is_idempotent(self):
        result = migrations.migrate_all(apply=True, root=self.data)
        self.assertTrue(result["ok"])
        backup = Path(result["backup"])
        self.assertEqual(len(list(backup.rglob("*.json"))), 7)  # every original, at its path under the data root
        for path in (self.data / "chats").glob("c*/chat.json"):
            self.assertEqual(json.loads(path.read_text())["schema"], chats.SCHEMA)
        state = json.loads((self.data / "state" / "workstation.json").read_text())
        self.assertEqual((state["schema"], state["selected_profile"], state["last_chat"]), (1, "cyber-low", "c1"))
        permission = json.loads((self.data / "config" / "permission-runtime.json").read_text())
        self.assertNotIn("reversal_legacy_record", permission)
        again = migrations.migrate_all(apply=True, root=self.data)
        self.assertEqual((again["changed"], again["backup"]), ([], None))

    def test_malformed_and_future_records_are_reported_and_untouched(self):
        broken = self.data / "chats" / "c20261101100000-ffffff" / "chat.json"
        broken.parent.mkdir()
        broken.write_text("{not json")
        future = self.data / "chats" / "c20261101100001-ffffff" / "chat.json"
        future.parent.mkdir()
        future.write_text(json.dumps({"schema": 99, "id": "c20261101100001-ffffff", "messages": []}))
        result = migrations.migrate_all(apply=True, root=self.data)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["errors"]), 2)
        self.assertEqual(broken.read_text(), "{not json")
        self.assertEqual(json.loads(future.read_text())["schema"], 99)

    def test_rollback_writes_what_the_previous_release_reads_and_comes_back_losslessly(self):
        migrations.migrate_all(apply=True, root=self.data)
        forward = {p: json.loads(p.read_text()) for p in (self.data / "chats").glob("c*/chat.json")}
        result = migrations.migrate_all(apply=True, root=self.data, rollback=True)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["changed"]), 5)
        for path, record in forward.items():
            back = json.loads(path.read_text())
            self.assertEqual(back["schema"], 1)  # the previous release's reader accepts exactly schema 1
            self.assertEqual({k: v for k, v in back.items() if k != "schema"}, {k: v for k, v in record.items() if k != "schema"})
        migrations.migrate_all(apply=True, root=self.data)
        self.assertEqual({p: json.loads(p.read_text()) for p in forward}, forward)

    def test_chat_store_reads_migrated_fixtures(self):
        with mock.patch.object(chats, "ROOT", self.data / "chats"):
            rows = chats.list_chats(archived=None)
            self.assertEqual(len(rows), 5)
            record = chats.get("c20261110100000-cccccc")
            self.assertNotIn("SECRET", json.dumps(record))


if __name__ == "__main__":
    unittest.main()
