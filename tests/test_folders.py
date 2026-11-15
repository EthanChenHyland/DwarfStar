"""Chat folders: organizational metadata only. CRUD, depth, safe deletion, archive/fork/search/bulk behaviour, Zed
neutrality, the schema 3 migration and backup/restore. Synthetic data in temporary roots; no real chat is touched."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from dwarfstar import backup, chats, migrations

SESSION = "01a1aaaa-0000-7000-8000-00000000f01d"


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.data = Path(self.folder.name) / "data"
        patcher = mock.patch.object(chats, "ROOT", self.data / "chats")
        patcher.start()
        self.addCleanup(patcher.stop)

    def chat(self, title="Chat", **fields):
        record = chats.new_record("scope-low", now=time.time() - 3600)
        record.update(title=title, **fields)
        chats.add_message(record, "user", f"about {title}")
        return chats._write(record)["id"]

    def test_create_rename_nest_and_limits(self):
        projects = chats.create_folder("Projects")
        dwarfstar = chats.create_folder("DwarfStar", projects["id"])
        self.assertEqual(chats.folder_path(dwarfstar["id"]), "Projects / DwarfStar")
        with self.assertRaisesRegex(chats.ChatError, "2 levels"):
            chats.create_folder("Too deep", dwarfstar["id"])
        with self.assertRaisesRegex(chats.ChatError, "already here"):
            chats.create_folder("projects")
        for bad in ("", "a/b", "x" * 41):
            with self.assertRaises(chats.ChatError):
                chats.create_folder(bad)
        chats.rename_folder(projects["id"], "Work")
        self.assertEqual(chats.folder_path(dwarfstar["id"]), "Work / DwarfStar")

    def test_moving_is_metadata_only(self):
        target = chats.create_folder("School")
        chat_id = self.chat("Logic", agent_session_id=SESSION, tags=["proofs"], pinned=True)
        workspace = self.data / "chats" / chat_id / "workspace" / ".omp-dwarfstar-scope-low-agent" / "sessions" / "-ws"
        workspace.mkdir(parents=True)
        session = workspace / f"2026_{SESSION}.jsonl"
        session.write_text('{"type":"session"}\n')
        before = chats.get(chat_id)
        stamp = session.stat().st_mtime_ns
        chats.set_folder(chat_id, target["id"])
        after = chats.get(chat_id)
        self.assertEqual(after["folder"], target["id"])
        for key in ("id", "agent_session_id", "updated", "created", "messages", "tags", "pinned", "profile", "compactions"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(session.stat().st_mtime_ns, stamp)  # the agent session file is untouched
        self.assertEqual(sorted(p.name for p in workspace.iterdir()), [session.name])  # no copy, no new session
        with self.assertRaisesRegex(chats.ChatError, "Unknown folder"):
            chats.set_folder(chat_id, "fmissing")

    def test_delete_never_deletes_chats(self):
        parent = chats.create_folder("Projects")
        child = chats.create_folder("BugMuseum", parent["id"])
        a, b, c = self.chat("A"), self.chat("B"), self.chat("C")
        chats.set_folder(a, parent["id"])
        chats.set_folder(b, child["id"])
        chats.set_folder(c, child["id"])
        chats.set_archived(c, True)
        with self.assertRaisesRegex(chats.ChatError, "subfolder"):
            chats.delete_folder(parent["id"])  # children need an explicit choice
        result = chats.delete_folder(parent["id"], subfolders=True)
        self.assertEqual(sorted(result["unfiled"]), sorted([a, b, c]))
        self.assertEqual(chats.load_folders(), [])
        for chat_id in (a, b, c):
            self.assertIsNone(chats.get(chat_id)["folder"])
        self.assertTrue(chats.get(c)["archived"])  # archived chats are moved, and stay archived

    def test_archive_keeps_the_folder(self):
        school = chats.create_folder("School")
        chat_id = self.chat("CS")
        chats.set_folder(chat_id, school["id"])
        chats.set_archived(chat_id, True)
        self.assertEqual(chats.list_chats(archived=True, folder=school["id"])[0]["id"], chat_id)
        self.assertEqual(chats.list_chats(folder=school["id"]), [])
        chats.set_archived(chat_id, False)
        self.assertEqual(chats.get(chat_id)["folder"], school["id"])
        self.assertEqual([r["id"] for r in chats.list_chats(folder=school["id"])], [chat_id])

    def test_fork_stays_in_the_folder_and_keeps_tags(self):
        work = chats.create_folder("Work")
        chat_id = self.chat("Plan", tags=["release"])
        chats.set_folder(chat_id, work["id"])
        fork = chats.fork(chat_id)
        same = chats.fork(chat_id, keep_messages=False)
        self.assertEqual((fork["folder"], fork["tags"]), (work["id"], ["release"]))
        self.assertEqual(same["folder"], work["id"])
        self.assertEqual(chats.get(chat_id)["folder"], work["id"])

    def test_filters_counts_search_and_orphans(self):
        projects = chats.create_folder("Projects")
        dwarfstar = chats.create_folder("DwarfStar", projects["id"])
        top, nested, loose = self.chat("Roadmap"), self.chat("Kernel notes"), self.chat("Groceries")
        chats.set_folder(top, projects["id"])
        chats.set_folder(nested, dwarfstar["id"])
        self.assertEqual({r["id"] for r in chats.list_chats(folder=projects["id"])}, {top, nested})  # subfolders included
        self.assertEqual([r["id"] for r in chats.list_chats(folder=dwarfstar["id"])], [nested])
        self.assertEqual([r["id"] for r in chats.list_chats(folder="unfiled")], [loose])
        self.assertEqual([r["id"] for r in chats.list_chats(query="kernel")], [nested])  # search reaches nested chats
        tree = chats.folder_tree()
        counts = {f["name"]: (f["chats"], f["total"]) for f in tree["folders"]}
        self.assertEqual((counts["Projects"], counts["DwarfStar"], tree["unfiled"]), ((1, 2), (1, 1), 1))
        folders = json.loads((self.data / "chats" / "folders.json").read_text())
        folders["folders"] = [f for f in folders["folders"] if f["id"] != dwarfstar["id"]]  # a folder vanishes underneath
        (self.data / "chats" / "folders.json").write_text(json.dumps(folders))
        self.assertIn(nested, [r["id"] for r in chats.list_chats(folder="unfiled")])  # its chat reads as unfiled

    def test_bulk_move(self):
        school = chats.create_folder("School")
        ids = [self.chat(f"T{i}") for i in range(3)]
        chats.bulk(ids[:2], "move", folder=school["id"])
        self.assertEqual({r["id"] for r in chats.list_chats(folder=school["id"])}, set(ids[:2]))
        chats.bulk(ids[:2], "move", folder=None)
        self.assertEqual(chats.list_chats(folder=school["id"]), [])
        with self.assertRaisesRegex(chats.ChatError, "Unknown folder"):
            chats.bulk(ids, "move", folder="fnope")

    def test_backup_restore_brings_back_organization(self):
        projects = chats.create_folder("Projects")
        dwarfstar = chats.create_folder("DwarfStar", projects["id"])
        chat_id = self.chat("Release notes", tags=["release"], pinned=True, starred=True)
        chats.set_folder(chat_id, dwarfstar["id"])
        with mock.patch.object(backup, "CODE", Path(self.folder.name) / "code"):
            archive = Path(backup.create(out=Path(self.folder.name) / "out", data=self.data)["backup"])
            chats.delete_folder(projects["id"], subfolders=True)
            later = chats.create_folder("Made later")
            chats.organize(chat_id, pinned=False, starred=False, tags=[])
            self.assertTrue(backup.restore(archive, data=self.data, check_service=False)["ok"])
        restored = chats.get(chat_id)
        self.assertEqual((restored["folder"], restored["tags"], restored["pinned"], restored["starred"]),
                         (dwarfstar["id"], ["release"], True, True))
        self.assertEqual(chats.folder_path(dwarfstar["id"]), "Projects / DwarfStar")
        self.assertIn(later["id"], {f["id"] for f in chats.load_folders()})  # folders made after the backup are kept


class TrashTests(FolderTests):
    def test_deleted_chats_wait_for_an_explicit_empty(self):
        keep, gone = self.chat("Keep"), self.chat("Gone")
        chats.delete(gone, gone)
        status = chats.trash_status()
        self.assertEqual(status["chats"], 1)
        with self.assertRaisesRegex(chats.ChatError, "empty trash"):
            chats.empty_trash("yes")
        self.assertEqual(chats.trash_status()["chats"], 1)
        self.assertEqual(chats.empty_trash("empty trash")["removed"], 1)
        self.assertEqual(chats.trash_status()["chats"], 0)
        self.assertEqual(chats.get(keep)["title"], "Keep")  # live chats are never touched


class FolderMigrationTests(unittest.TestCase):
    def test_older_chats_become_unfiled_and_nothing_else_changes(self):
        fixture = Path(__file__).parent / "fixtures" / "chat-records" / "v1-zed-old-workspace.json"
        before = json.loads(fixture.read_text())
        after, changed = migrations.migrate_record("chat", before)
        self.assertTrue(changed)
        self.assertEqual((after["schema"], after["folder"]), (3, None))
        for key in ("id", "agent_session_id", "workspace", "transcript", "shared", "messages", "created", "updated"):
            self.assertEqual(after.get(key), before.get(key), key)

    def test_rollback_keeps_the_folder_for_the_way_forward(self):
        record, _ = migrations.migrate_record("chat", {"schema": 2, "id": "c1", "messages": [], "folder": None})
        record["folder"] = "fabc"
        back, _ = migrations.rollback_record("chat", record)
        self.assertEqual((back["schema"], back["folder"]), (1, "fabc"))
        again, _ = migrations.migrate_record("chat", back)
        self.assertEqual(again["folder"], "fabc")


if __name__ == "__main__":
    unittest.main()
