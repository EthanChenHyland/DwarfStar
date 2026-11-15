"""First-run setup: when the wizard opens on its own, and that a missing model's remedy names every expected file."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dwarfstar import catalog, chats, diagnostics, paths, service


class SetupDocumentTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        data = Path(self.folder.name)
        patches = [mock.patch.object(chats, "ROOT", data / "chats"), mock.patch.object(service, "STATE_FILE", data / "state" / "workstation.json"),
                   mock.patch.object(diagnostics, "checks", return_value=[])]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_new_installation_opens_setup_once(self):
        self.assertTrue(service.setup_document()["first_run"])
        service._write_state(setup_completed=1.0)
        document = service.setup_document()
        self.assertEqual((document["first_run"], document["completed"]), (False, 1.0))

    def test_an_installation_in_use_is_not_interrupted(self):
        chats._write(chats.new_record("scope-low"))
        self.assertFalse(service.setup_document()["first_run"])


class MissingModelRemedyTests(unittest.TestCase):
    def test_the_remedy_names_each_expected_file_and_never_downloads(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(paths, "ASSETS", Path(folder)):
            rows = [r for r in diagnostics._installation() if r["group"] == "Models" and r["name"] == catalog.models()["scope"]["display_name"]]
        self.assertEqual(rows[0]["level"], "error")
        for asset in catalog.models()["scope"]["assets"].values():
            self.assertIn(Path(asset["path"]).name, rows[0]["remedy"])
            self.assertIn(f"{asset['bytes']:,} bytes", rows[0]["remedy"])
        self.assertIn("never downloads", rows[0]["remedy"])


if __name__ == "__main__":
    unittest.main()
