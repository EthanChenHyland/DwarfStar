"""The Advanced view's drawing code runs headless (tests/js/visualizer_smoke.mjs): ds3 aggregate and sampled, ds2 and
ds1 fallback, request view and export, leaving and re-entering the view, and replay, with no exception."""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class VisualizerSmokeTests(unittest.TestCase):
    def test_every_runtime_and_mode_draws_without_an_exception(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        result = subprocess.run([node, str(ROOT / "tests/js/visualizer_smoke.mjs")], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
