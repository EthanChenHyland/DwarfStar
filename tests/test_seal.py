"""Seal semantics: product versus documentation differences, file-level reporting, schema 1 compatibility."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dwarfstar import seal

FILES = {"src/dwarfstar/service.py": "a1", "config/profiles.json": "b1", "docs/WORKSTATION.md": "c1", "tests/test_x.py": "d1",
         "README.md": "e1", "app/workstation/web/app.js": "f1"}


def built(files: dict, tree: str = "tree-1", commit: str = "commit-1") -> tuple[dict, dict]:
    product = {k: v for k, v in files.items() if seal.is_product(k)}
    current = {
        "schema": seal.SCHEMA, "created_utc": "2026-11-13T00:00:00Z",
        "repository": {"tree": tree, "product_sha256": seal.text_sha(product), "baseline_sha256": seal.text_sha(files), "dirty": False,
                       "metadata": {"commit": commit, "branch": "main", "tag": None}},
        "schemas": {"seal": seal.SCHEMA, "data.chat": 2},
        "catalog": {"default_profile": "scope-low"}, "launchers": {"l": 1}, "telemetry": {"t": 1}, "benchmark_index_sha256": "bench",
        "workstation": {"executable_sha256": "app"}, "editor": {"installed_matches": True},
        "models": {"scope": {k: k for k in ("assets", "runtime_binary_sha256", "shader_set_sha256", "adapter_sha256", "context", "cache",
                                           "generated_config_sha256", "source", "overrides")} | {"generated_config_drift": []}},
    }
    return current, {"tracked_files": files}


class SealTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        for name, value in (("SEAL", root / "seal.json"), ("BASELINE", root / "baseline.json")):
            patcher = mock.patch.object(seal, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        assets = mock.patch.object(seal.instance, "verify_assets", return_value={"verified": True, "errors": []})
        assets.start()
        self.addCleanup(assets.stop)

    def write(self, recorded: dict, files: dict) -> None:
        seal.SEAL.write_text(json.dumps(recorded))
        seal.BASELINE.write_text(json.dumps({"tracked_files": files}))

    def verify(self, current_files: dict, **kwargs) -> dict:
        with mock.patch.object(seal, "build", return_value=built(current_files, **kwargs)):
            return seal.verify()

    def test_classification(self):
        self.assertTrue(seal.is_product("src/dwarfstar/service.py"))
        self.assertTrue(seal.is_product("backends/splash-m1/patches/telemetry/0004-telemetry-server.patch"))
        for name in ("docs/WORKSTATION.md", "tests/test_seal.py", "README.md", ".github/workflows/ci.yml", "research/benchmark-index.json",
                     "ADVANCED-TELEMETRY-REPORT.md"):
            self.assertFalse(seal.is_product(name), name)

    def test_identical_tree_verifies(self):
        recorded, _ = built(FILES)
        self.write(recorded, FILES)
        result = self.verify(FILES)
        self.assertTrue(result["ok"], result)
        self.assertIn("git tree matches the seal", result["notes"])

    def test_a_report_committed_after_sealing_is_a_note_not_a_failure(self):
        recorded, _ = built(FILES)
        self.write(recorded, FILES)
        later = {**FILES, "docs/NEW-REPORT.md": "z1", "docs/WORKSTATION.md": "c2"}
        result = self.verify(later, tree="tree-2", commit="commit-2")
        self.assertTrue(result["ok"], result)
        note = next(n for n in result["notes"] if "not product" in n)
        self.assertIn("changed: docs/WORKSTATION.md", note)
        self.assertIn("added: docs/NEW-REPORT.md", note)
        self.assertTrue(any("git tree differs" in n and "commit-1" in n for n in result["notes"]))

    def test_product_differences_fail_and_are_named(self):
        recorded, _ = built(FILES)
        self.write(recorded, FILES)
        later = {k: v for k, v in FILES.items() if k != "config/profiles.json"} | {"src/dwarfstar/service.py": "a2", "src/dwarfstar/new.py": "n"}
        result = self.verify(later)
        self.assertFalse(result["ok"])
        problem = next(p for p in result["problems"] if "product files" in p)
        for expected in ("changed: src/dwarfstar/service.py", "added: src/dwarfstar/new.py", "removed: config/profiles.json"):
            self.assertIn(expected, problem)

    def test_same_tree_on_another_commit_verifies(self):
        recorded, _ = built(FILES, commit="commit-1")
        self.write(recorded, FILES)
        self.assertTrue(self.verify(FILES, commit="commit-9")["ok"])

    def test_a_schema_1_seal_is_still_read(self):
        recorded, _ = built(FILES)
        legacy = copy.deepcopy(recorded)
        legacy["schema"] = 1
        legacy.pop("schemas")
        legacy["repository"] = {"revision": "commit-1", "branch": "main", "tracked_files": len(FILES),
                                "baseline_sha256": seal.text_sha(FILES), "dirty": False}
        self.write(legacy, FILES)
        self.assertTrue(self.verify(FILES)["ok"])
        result = self.verify({**FILES, "docs/REPORT.md": "r"})
        self.assertTrue(result["ok"], result)  # the failure the old seal raised for a later report is now a note
        self.assertEqual(result["seal_schema"], 1)
        self.assertFalse(self.verify({**FILES, "src/dwarfstar/service.py": "zz"})["ok"])

    def test_a_newer_seal_is_refused(self):
        recorded, _ = built(FILES)
        recorded["schema"] = seal.SCHEMA + 1
        self.write(recorded, FILES)
        result = self.verify(FILES)
        self.assertFalse(result["ok"])
        self.assertIn("newer DwarfStar", result["problems"][0])

    def test_writing_a_seal_prints_its_summary(self):
        import contextlib
        import io

        recorded, baseline = built(FILES)
        recorded["repository"].update(tracked_files=len(FILES), product_files=4)
        with mock.patch.object(seal, "build", return_value=(recorded, baseline)):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(seal.main([]), 0)
        summary = json.loads(output.getvalue())
        self.assertEqual((summary["schema"], summary["revision"], summary["tree"]), (seal.SCHEMA, "commit-1", "tree-1"))
        self.assertTrue(seal.SEAL.is_file() and seal.BASELINE.is_file())

    def test_a_changed_data_schema_fails(self):
        recorded, _ = built(FILES)
        recorded["schemas"] = {"seal": seal.SCHEMA, "data.chat": 1}
        self.write(recorded, FILES)
        self.assertTrue(any("data schemas" in p for p in self.verify(FILES)["problems"]))


if __name__ == "__main__":
    unittest.main()
