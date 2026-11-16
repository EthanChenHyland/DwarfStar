#!/usr/bin/env python3
"""Build DwarfStar Workstation.app.

    build.py --output BINARY                 compile only
    build.py --bundle PATH.app               compile, assemble a new bundle at PATH (must not exist), ad-hoc sign
    build.py --install PATH.app              build, verify, then replace PATH safely: the previous bundle is moved to
                                             <cache>/build/app-rollback/ (printed), never left beside the app

The bundle records the repository location (``DwarfStarHome``) and revision so the app can start the local service
and a bug report names the build. Its icon is rendered from app/workstation/icon/dwarfstar.svg at build time. It
contains no model data and no user settings.
"""
import json
import argparse
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
# The bundle carries the product version (src/dwarfstar/__init__.py), so a bug report from the app names the release.
VERSION = next(line.split('"')[1] for line in (REPO / "src/dwarfstar/__init__.py").read_text().splitlines() if line.startswith("__version__"))


def revision() -> str:
    result = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return result.stdout.strip() or "unknown"


def compile_to(output: Path) -> None:
    subprocess.run(["/usr/bin/swiftc", "-O", str(HERE / "Sources/main.swift"), "-o", str(output)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--bundle", type=Path)
    group.add_argument("--install", type=Path)
    args = parser.parse_args()
    if args.output:
        compile_to(args.output)
        return
    if args.install:
        print(json.dumps(install(args.install), indent=1))
        return
    app = args.bundle
    if app.exists():
        raise SystemExit(f"Refusing to overwrite existing bundle: {app}")
    assemble(app)
    print(app)


def verify(app: Path) -> dict:
    """What a correct bundle has: executable, Info.plist with this version, the icon, a valid signature."""
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    problems = []
    if info.get("CFBundleShortVersionString") != VERSION:
        problems.append(f"version {info.get('CFBundleShortVersionString')} is not {VERSION}")
    if not (app / "Contents/MacOS" / info.get("CFBundleExecutable", "?")).is_file():
        problems.append("executable missing")
    if not (app / "Contents/Resources" / (info.get("CFBundleIconFile", "?") + ".icns")).is_file():
        problems.append("icon missing")
    signed = subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(app)], capture_output=True, text=True)
    if signed.returncode:
        problems.append("signature: " + signed.stderr.strip())
    return {"ok": not problems, "problems": problems, "version": info.get("CFBundleShortVersionString"),
            "revision": info.get("DwarfStarRevision"), "identifier": info.get("CFBundleIdentifier")}


def install(app: Path) -> dict:
    """Build beside nothing, verify, then swap: the old bundle goes to a rollback folder, the new one into place."""
    import sys
    import time

    sys.path.insert(0, str(REPO / "src"))
    from dwarfstar import paths

    rollback_root = paths.CACHE / "build" / "app-rollback"
    rollback_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dwarfstar-install-", dir=rollback_root) as temporary:
        fresh = Path(temporary) / app.name
        assemble(fresh)
        checked = verify(fresh)
        if not checked["ok"]:
            raise SystemExit("new bundle failed verification: " + "; ".join(checked["problems"]))
        previous = None
        if app.exists():
            previous = rollback_root / f"{app.stem}-{time.strftime('%Y%m%d-%H%M%S')}.app"
            shutil.move(str(app), str(previous))
        app.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(fresh), str(app))
    # LaunchServices picks up the new icon and version
    subprocess.run(["/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister", "-f", str(app)],
                   capture_output=True)
    return {"ok": True, "installed": str(app), **verify(app), "rollback": str(previous) if previous else None}


def assemble(app: Path) -> None:
    sys_path = str(HERE / "icon")
    import sys

    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    import build_icon

    with tempfile.TemporaryDirectory(prefix="dwarfstar-app-") as temporary:
        staged = Path(temporary) / app.name
        (staged / "Contents/MacOS").mkdir(parents=True)
        (staged / "Contents/Resources").mkdir()
        compile_to(staged / "Contents/MacOS/DwarfStarWorkstation")
        icon = build_icon.build(Path(temporary) / "icon")
        shutil.copy(icon, staged / "Contents/Resources/DwarfStar.icns")
        info = {
            "CFBundleDisplayName": "DwarfStar Workstation",
            "CFBundleName": "DwarfStar Workstation",
            "CFBundleExecutable": "DwarfStarWorkstation",
            "CFBundleIdentifier": "local.dwarfstar.Workstation",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "CFBundleIconFile": "DwarfStar",
            "CFBundleInfoDictionaryVersion": "6.0",
            "LSApplicationCategoryType": "public.app-category.developer-tools",
            "LSArchitecturePriority": ["arm64"],
            "LSMinimumSystemVersion": "14.0",
            "NSHighResolutionCapable": True,
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
            "DwarfStarHome": str(REPO),
            "DwarfStarRevision": revision(),
        }
        with (staged / "Contents/Info.plist").open("wb") as stream:
            plistlib.dump(info, stream)
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(staged)], check=True)  # ad-hoc
        shutil.copytree(staged, app, symlinks=True)


if __name__ == "__main__":
    main()
