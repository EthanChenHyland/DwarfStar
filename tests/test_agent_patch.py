"""The agent bundle patch: exact snippet edits, legacy migration, refusal of unknown bundles, exact revert."""
import hashlib
import json
import unittest
from pathlib import Path
from unittest import mock

from dwarfstar import agent_patch


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class AgentPatchTests(unittest.TestCase):
    def setUp(self):
        real = agent_patch.spec()
        # A synthetic stock bundle that holds each snippet the real patch edits, exactly once.
        self.stock = "/*head*/" + "\n/*gap*/\n".join(p["old"] for p in real["patches"]) + "/*tail*/"
        patched = self.stock
        for p in real["patches"]:
            patched = patched.replace(p["old"], p["new"], 1)
        p3 = next(p for p in real["patches"] if p["id"] == "p3")
        legacy_p3 = ('requestPermission(e){if(process.env.DWARFSTAR_APPROVAL_MODE==="full_auto")return import("{home}/old/'
                     'acp/permission-policy.ts").then(m=>m.answerPermission(e));return this.#e.request("session/request_permission",e)}')
        legacy = patched.replace(p3["new"], legacy_p3.replace("{home}", str(Path.home())), 1)
        self.patched, self.legacy = patched, legacy
        self.data = {**real, "stock_sha256": sha(self.stock), "patched_sha256": sha(patched),
                     "legacy": [{"name": "v2", "sha256": sha(legacy), "p3_new": legacy_p3}]}

    def test_states(self):
        self.assertEqual(agent_patch.state(self.stock, self.data), "stock")
        self.assertEqual(agent_patch.state(self.patched, self.data), "patched")
        self.assertEqual(agent_patch.state(self.legacy, self.data), "legacy")
        first_old = self.data["patches"][0]["old"]
        self.assertEqual(agent_patch.state(self.stock.replace(first_old, "", 1), self.data), "unknown")
        # A bundle that differs elsewhere (a fresh install, another tool's unrelated edit) is still recognised.
        self.assertEqual(agent_patch.state("/*foreign edit*/" + self.stock, self.data), "stock")

    def test_stock_patches_and_reverts_exactly(self):
        patched = agent_patch.patched_text(self.stock, self.data)
        self.assertEqual(sha(patched), self.data["patched_sha256"])
        self.assertEqual(agent_patch.stock_text(patched, self.data), self.stock)

    def test_patch_is_idempotent(self):
        self.assertEqual(agent_patch.patched_text(self.patched, self.data), self.patched)

    def test_legacy_migrates_and_drops_the_retired_absolute_import(self):
        migrated = agent_patch.patched_text(self.legacy, self.data)
        self.assertEqual(migrated, self.patched)
        self.assertNotIn("/old/acp/permission-policy.ts", migrated)
        self.assertIn("process.env.DWARFSTAR_PERMISSION_MODULE", migrated)

    def test_unknown_bundle_is_refused_untouched(self):
        with self.assertRaises(ValueError):
            agent_patch.patched_text(self.stock.replace(self.data["patches"][1]["old"], "", 1), self.data)

    def test_bundle_with_unrelated_edit_patches_and_reverts(self):
        foreign = "/*another tool's edit*/" + self.stock
        patched = agent_patch.patched_text(foreign, self.data)
        self.assertEqual(agent_patch.state(patched, self.data), "patched")
        self.assertEqual(agent_patch.stock_text(patched, self.data), foreign)

    def test_the_patch_only_acts_under_dwarfstar_environment(self):
        for patch in agent_patch.spec()["patches"]:
            self.assertIn("process.env.DWARFSTAR_", patch["new"])

    def test_spec_carries_no_home_directory(self):
        self.assertNotIn(str(Path.home()), agent_patch.SPEC.read_text())

    def test_apply_writes_backup_bundle_and_runtime_record(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cli = root / "node_modules" / self.data["package"] / self.data["file"]
            cli.parent.mkdir(parents=True)
            cli.write_text(self.stock)
            with mock.patch.object(agent_patch, "spec", return_value=self.data), \
                    mock.patch.object(agent_patch, "bundle", return_value=cli), \
                    mock.patch.object(agent_patch.paths, "SHARED_CONFIG", root / "config"), \
                    mock.patch.object(agent_patch.paths, "BACKUPS", root / "backups"), \
                    mock.patch.object(agent_patch, "version_of", return_value=self.data["version"]):
                result = agent_patch.apply()
                self.assertEqual((result["before"], result["after"]), ("stock", "patched"))
                record = json.loads((root / "config" / "permission-runtime.json").read_text())
                self.assertEqual(record["cli_sha256"], hashlib.sha256(cli.read_bytes()).hexdigest())
                self.assertEqual(len(list((root / "backups" / "omp").iterdir())), 1)
                self.assertEqual(agent_patch.revert()["after"], "stock")
                self.assertEqual(cli.read_text(), self.stock)
                self.assertFalse((root / "config" / "permission-runtime.json").exists())


if __name__ == "__main__":
    unittest.main()
