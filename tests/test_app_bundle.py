"""The app icon source and the bundle metadata: one version everywhere, an icon the build can always regenerate."""
import importlib.util
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from dwarfstar import __version__

ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "app" / "workstation" / "icon"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IconSourceTests(unittest.TestCase):
    def test_the_svg_is_the_canonical_square_source(self):
        root = ET.fromstring((ICON / "dwarfstar.svg").read_text())
        self.assertEqual(root.get("viewBox"), "0 0 1024 1024")
        self.assertIsNotNone(root.find(".//{http://www.w3.org/2000/svg}g[@id='detail']"))
        self.assertNotIn("<text", (ICON / "dwarfstar.svg").read_text())  # no lettering in the icon

    def test_the_small_variant_is_valid_and_drops_only_the_detail(self):
        builder = load(ICON / "build_icon.py", "build_icon")
        small = builder.small_variant((ICON / "dwarfstar.svg").read_text())
        root = ET.fromstring(small)
        self.assertIsNone(root.find(".//{http://www.w3.org/2000/svg}g[@id='detail']"))
        self.assertIn('stroke-width="24"', small)
        names = [name for name, _ in builder.SIZES]
        for size in (16, 32, 128, 256, 512):
            self.assertIn(f"icon_{size}x{size}.png", names)
            self.assertIn(f"icon_{size}x{size}@2x.png", names)

    def test_no_rendered_icon_is_committed(self):
        import subprocess

        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "app/workstation/icon"], capture_output=True, text=True).stdout.split()
        self.assertEqual(sorted(Path(t).name for t in tracked), ["build_icon.py", "dwarfstar.svg", "render.swift"])


class BundleMetadataTests(unittest.TestCase):
    def test_the_bundle_version_is_the_product_version(self):
        build = load(ROOT / "app" / "workstation" / "build.py", "workstation_build")
        self.assertEqual(build.VERSION, __version__)
        source = (ROOT / "app" / "workstation" / "build.py").read_text()
        for key in ('"CFBundleIconFile": "DwarfStar"', '"CFBundleIdentifier": "local.dwarfstar.Workstation"', '"DwarfStarRevision"'):
            self.assertIn(key, source)


if __name__ == "__main__":
    unittest.main()
