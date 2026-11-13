"""Move everything the 2026-11 cleanup retires into one quarantine folder, prove nothing needs it, then purge it.

The candidates come from the before-cleanup inventory (Manifests/storage/inventory-before-cleanup.json): large files
whose action is a deletion, plus the directories listed below. Each is renamed (same volume, instant, reversible) into
<assets>/.quarantine/ under its path relative to the home folder, so the old paths are gone while the cold-start test
runs. --restore puts everything back; --purge deletes the quarantine after that test has passed.

    quarantine_retired.py                 dry run
    quarantine_retired.py --apply         move the candidates into the quarantine
    quarantine_retired.py --restore       move them back
    quarantine_retired.py --purge         delete the quarantine (prints bytes reclaimed)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

HOME = Path.home()
ASSETS = HOME / "AI/DwarfStar"
QUARANTINE = ASSETS / ".quarantine"
INVENTORY = ASSETS / "Manifests/storage/inventory-before-cleanup.json"
LEGACY = HOME / "local-ai-setup"
DIRECTORIES = [  # retired as a whole
    LEGACY / "dwarfstar-data/runtime",                       # native ds4 runtime builds
    LEGACY / "dwarfstar-data/instances/scope/cache",         # native KV checkpoints
    LEGACY / "dwarfstar-data/instances/swift/cache",
    LEGACY / "dwarfstar-data/instances/cyber/cache",
    LEGACY / "dwarfstar-data/build/cyber-calibration",       # abandoned calibration scratch
    LEGACY / "dwarfstar-data/build/ds4-src",                 # native build tree
    LEGACY / "dwarfstar-data/backends/splash-m1/artifacts",  # emptied by the migration (old links only)
    LEGACY / "dwarfstar-data/backends/splash-m1/splash-m1",  # evaluation wrapper of the release runtime
    LEGACY / "challengers/dwarfstar-cyber/build",            # Cyber conversion scratch, venv and fixtures
    LEGACY / "challengers/dwarfstar-paperniuk/cache",        # native KV caches
    LEGACY / "challengers/dwarfstar-swift15/cache",
    LEGACY / "challengers/dwarfstar-cyber/source",           # Cyber source checkpoint (metadata kept in Sources/cyber)
]
DELETE_ACTIONS = ("delete", "delete after cold-start proof", "delete binary fixtures; reports kept in Manifests/evidence/legacy")


def candidates() -> list[Path]:
    rows = json.loads(INVENTORY.read_text())["large_files"]
    files = [Path(r["path"]) for r in rows if r["action"] in DELETE_ACTIONS]
    chosen = [d for d in DIRECTORIES if d.exists() or d.is_symlink()]
    covered = lambda p: any(p == d or d in p.parents for d in chosen)  # noqa: E731
    return chosen + sorted({f for f in files if (f.exists() or f.is_symlink()) and not covered(f)})


def size(path: Path) -> int:
    if path.is_symlink():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def main(argv: list[str]) -> int:
    if "--restore" in argv:
        moved = json.loads((QUARANTINE / "moved.json").read_text())
        for original in moved:
            source = QUARANTINE / Path(original).relative_to(HOME)
            Path(original).parent.mkdir(parents=True, exist_ok=True)
            os.rename(source, original)
            print("restored", original)
        return 0
    if "--purge" in argv:
        total = size(QUARANTINE)
        shutil.rmtree(QUARANTINE)
        print(json.dumps({"purged": str(QUARANTINE), "bytes": total}))
        return 0
    chosen = candidates()
    total = 0
    for path in chosen:
        bytes_ = size(path)
        total += bytes_
        print(f"{bytes_ / 2**30:9.2f} GiB  {path}")
    print(f"{total / 2**30:9.2f} GiB in {len(chosen)} items")
    if "--apply" not in argv:
        return 0
    moved = []
    for path in chosen:
        target = QUARANTINE / path.relative_to(HOME)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(path, target)
        moved.append(str(path))
    (QUARANTINE / "moved.json").write_text(json.dumps(moved, indent=1) + "\n")
    print(f"quarantined {len(moved)} items")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
