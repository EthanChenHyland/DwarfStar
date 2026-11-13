#!/usr/bin/env python3
"""Build DwarfStar Workstation.app.

    build.py --output BINARY                 compile only
    build.py --bundle PATH.app               compile, assemble a new bundle at PATH (must not exist), ad-hoc sign

The bundle records the repository location (``DwarfStarHome``) so the app can
start the local service. It contains no model data and no user settings.
"""
import argparse
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
VERSION = "3.0"


def compile_to(output: Path) -> None:
    subprocess.run(["/usr/bin/swiftc", "-O", str(HERE / "Sources/main.swift"), "-o", str(output)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--bundle", type=Path)
    args = parser.parse_args()
    if args.output:
        compile_to(args.output)
        return
    app = args.bundle
    if app.exists():
        raise SystemExit(f"Refusing to overwrite existing bundle: {app}")
    with tempfile.TemporaryDirectory(prefix="dwarfstar-app-") as temporary:
        staged = Path(temporary) / app.name
        (staged / "Contents/MacOS").mkdir(parents=True)
        (staged / "Contents/Resources").mkdir()
        compile_to(staged / "Contents/MacOS/DwarfStarWorkstation")
        info = {
            "CFBundleDisplayName": "DwarfStar Workstation",
            "CFBundleName": "DwarfStar Workstation",
            "CFBundleExecutable": "DwarfStarWorkstation",
            "CFBundleIdentifier": "local.dwarfstar.Workstation",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION.split(".")[0],
            "LSMinimumSystemVersion": "14.0",
            "NSHighResolutionCapable": True,
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
            "DwarfStarHome": str(REPO),
        }
        with (staged / "Contents/Info.plist").open("wb") as stream:
            plistlib.dump(info, stream)
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(staged)], check=True)  # ad-hoc
        shutil.copytree(staged, app, symlinks=True)
    print(app)


if __name__ == "__main__":
    main()
