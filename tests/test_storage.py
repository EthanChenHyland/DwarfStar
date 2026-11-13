"""Storage report and cleanup: categories from the roots; cleanup stays inside the cache root; logs rotate."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dwarfstar import paths, service, storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_cache_clean_is_a_dry_run_and_keeps_browsers_unless_asked(self):
        cache = self.root / "cache"
        (cache / "scratch").mkdir(parents=True)
        (cache / "scratch" / "x.bin").write_bytes(b"x" * 100)
        (cache / "agent-tools").mkdir()
        (cache / "agent-tools" / "browser").write_bytes(b"b" * 50)
        with mock.patch.object(paths, "CACHE", cache):
            dry = storage.clean_cache(apply=False)
            self.assertEqual((dry["reclaimed_bytes"], (cache / "scratch" / "x.bin").exists()), (100, True))
            done = storage.clean_cache(apply=True)
            self.assertEqual(done["reclaimed_bytes"], 100)
            self.assertFalse((cache / "scratch").exists())
            self.assertTrue((cache / "agent-tools" / "browser").exists())
            self.assertEqual(storage.clean_cache(apply=True, browsers=True)["reclaimed_bytes"], 50)

    def test_chat_trash_is_emptied_only_with_apply(self):
        trash = self.root / "chats" / ".deleted" / "c1"
        trash.mkdir(parents=True)
        (trash / "chat.json").write_text("{}")
        with mock.patch.object(paths, "CHATS", self.root / "chats"):
            self.assertEqual(len(storage.empty_chat_trash(apply=False)["chats"]), 1)
            self.assertTrue(trash.exists())
            storage.empty_chat_trash(apply=True)
            self.assertFalse(trash.exists())

    def test_the_lifecycle_log_rotates_and_keeps_a_bounded_history(self):
        log = self.root / "lifecycle.jsonl"
        for round_ in range(5):
            log.write_text("x" * 20)
            self.assertTrue(service.rotate_log(log, limit=10, keep=3))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["lifecycle.1.jsonl", "lifecycle.2.jsonl", "lifecycle.3.jsonl"])
        log.write_text("small")
        self.assertFalse(service.rotate_log(log, limit=10, keep=3))

    def test_the_report_names_production_files_and_flags_unreferenced_ones(self):
        value = storage.report()
        self.assertIn("production models", value["bytes"])
        self.assertEqual(set(value["roots"]), {"code", "assets", "data", "cache", "logs"})


if __name__ == "__main__":
    unittest.main()
