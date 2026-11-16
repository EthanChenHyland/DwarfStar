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


class AgentToolLinkTests(unittest.TestCase):
    """A temporary instance must not re-point the checkout's agent-tools links at its own roots."""

    def test_environment_roots_never_repoint_existing_links(self):
        import os

        from dwarfstar import instance

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            code, production, scratch = root / "code", root / "production", root / "scratch"
            (code / "agent-tools").mkdir(parents=True)
            (production / "state").mkdir(parents=True)
            (code / "agent-tools" / "state").symlink_to(production / "state")
            with mock.patch.object(instance, "CODE", code), mock.patch.object(instance, "STATE", scratch / "state"), \
                    mock.patch.object(instance, "CACHE", scratch / "cache"), \
                    mock.patch.dict(os.environ, {"DWARFSTAR_DATA": str(scratch)}), \
                    mock.patch("dwarfstar.paths.omp_packages", return_value=root / "none"):
                instance.link_agent_tool_data()
            self.assertEqual(Path(os.readlink(code / "agent-tools" / "state")), production / "state")


class AppStatusTests(unittest.TestCase):
    def bundle(self, folder: Path, **info) -> Path:
        import plistlib

        app = folder / "DwarfStar Workstation.app"
        (app / "Contents").mkdir(parents=True)
        (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
        return app

    def test_current_stale_and_missing(self):
        from dwarfstar import __version__, paths, setup

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertFalse(setup.app_status(root / "absent.app")["installed"])
            good = self.bundle(root / "a", CFBundleShortVersionString=__version__, DwarfStarHome=str(paths.CODE), CFBundleIconFile="DwarfStar")
            self.assertTrue(setup.app_status(good)["current"])
            old = self.bundle(root / "b", CFBundleShortVersionString="3.0", DwarfStarHome="/elsewhere")
            status = setup.app_status(old)
            self.assertFalse(status["current"])
            for reason in ("version 3.0", "another checkout", "icon"):
                self.assertIn(reason, status["why"])
