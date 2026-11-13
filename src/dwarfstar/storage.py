"""What DwarfStar keeps on this Mac, by category, and safe cleanup of what is recreatable.

    dwarfstar storage [--json]                 bytes per category, unreferenced large files, the largest items
    dwarfstar storage --empty-chat-trash [--apply]
                                               chats deleted in the Workstation are moved to <data>/chats/.deleted;
                                               this removes them for good (dry run unless --apply)
    dwarfstar cache status                     what the cache root holds
    dwarfstar cache clean [--browsers] [--apply]
                                               remove recreatable cache contents (dry run unless --apply); the agent's
                                               browsers only with --browsers, since reinstalling them downloads ~400 MB

Nothing here touches production models, the runtime, chats (except an explicit --empty-chat-trash), settings,
manifests, sources, the archive or logs. Categories follow paths.py's roots.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from dwarfstar import catalog, paths

GIB = 2**30
LARGE = 100 << 20


def tree_bytes(path: Path) -> int:
    if path.is_symlink() or not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def production_files() -> dict[Path, str]:
    files = {}
    for model_id, model in catalog.models().items():
        for role, asset in model["assets"].items():
            files[(paths.ASSETS / asset["path"]).resolve()] = f"{model_id} {role}"
    return files


def report() -> dict:
    produced = production_files()
    pinned = {(paths.ASSETS / pin["directory"]).resolve() for pin in catalog.load()["runtimes"]["runtimes"].values()}
    categories = {
        "production models": sum(path.stat().st_size for path in produced if path.is_file()),
        "production runtime": sum(tree_bytes(path) for path in pinned),
        "user data": tree_bytes(paths.DATA) - tree_bytes(paths.CHATS / ".deleted"),
        "deleted chats (recoverable)": tree_bytes(paths.CHATS / ".deleted"),
        "cache": tree_bytes(paths.CACHE),
        "logs": tree_bytes(paths.LOGS),
        "sources": tree_bytes(paths.SOURCES),
        "manifests and evidence": tree_bytes(paths.MANIFESTS),
        "tools": tree_bytes(paths.ASSETS / "Tools"),
        "archive": tree_bytes(paths.ARCHIVE),
    }
    orphans, largest = [], []
    known = {paths.MODELS, paths.RUNTIMES, paths.SOURCES, paths.MANIFESTS, paths.ARCHIVE, paths.ASSETS / "Tools"}
    for child in sorted(paths.ASSETS.iterdir()) if paths.ASSETS.is_dir() else ():
        if child not in known and not child.name.startswith("."):
            orphans.append({"path": str(child), "bytes": tree_bytes(child), "why": "unknown folder in the assets root"})
    for item in paths.MODELS.rglob("*") if paths.MODELS.is_dir() else ():
        if item.is_file() and item.stat().st_size >= LARGE and item.resolve() not in produced:
            orphans.append({"path": str(item), "bytes": item.stat().st_size, "why": "not referenced by the catalog"})
    for item in paths.RUNTIMES.iterdir() if paths.RUNTIMES.is_dir() else ():
        if item.resolve() not in pinned:
            orphans.append({"path": str(item), "bytes": tree_bytes(item), "why": "runtime not pinned in config/runtimes.json"})
    for root in (paths.ASSETS, paths.DATA, paths.CACHE, paths.LOGS):
        for item in root.rglob("*") if root.is_dir() else ():
            try:
                if item.is_file() and not item.is_symlink() and item.stat().st_size >= LARGE:
                    largest.append((item.stat().st_size, str(item)))
            except OSError:
                continue
    largest.sort(reverse=True)
    categories["unreferenced"] = sum(row["bytes"] for row in orphans)
    return {"roots": {name: str(path) for name, path in paths.roots().items()}, "bytes": categories,
            "total_bytes": sum(v for k, v in categories.items() if k != "unreferenced"),
            "unreferenced": orphans, "largest": [{"path": p, "bytes": b} for b, p in largest[:10]],
            "deleted_chats": sorted(p.name for p in (paths.CHATS / ".deleted").iterdir()) if (paths.CHATS / ".deleted").is_dir() else []}


def cache_entries(browsers: bool = False) -> list[tuple[Path, int, bool]]:
    """(path, bytes, removed by clean) for each top-level entry of the cache root."""
    rows = []
    for item in sorted(paths.CACHE.iterdir()) if paths.CACHE.is_dir() else ():
        removable = item.name != "agent-tools" or browsers
        rows.append((item, tree_bytes(item), removable))
    return rows


def clean_cache(apply: bool, browsers: bool = False) -> dict:
    removed, kept = [], []
    for item, size, removable in cache_entries(browsers):
        if not item.resolve().is_relative_to(paths.CACHE.resolve()):
            raise RuntimeError(f"refusing a path outside the cache root: {item}")
        (removed if removable else kept).append({"path": str(item), "bytes": size})
        if removable and apply:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink(missing_ok=True)
    return {"applied": apply, "reclaimed_bytes": sum(r["bytes"] for r in removed), "removed": removed, "kept": kept}


def empty_chat_trash(apply: bool) -> dict:
    trash = paths.CHATS / ".deleted"
    entries = [{"path": str(p), "bytes": tree_bytes(p)} for p in sorted(trash.iterdir())] if trash.is_dir() else []
    if apply:
        for row in entries:
            shutil.rmtree(row["path"])
    return {"applied": apply, "reclaimed_bytes": sum(r["bytes"] for r in entries), "chats": entries}


def _print_report(value: dict) -> None:
    for name, size in value["bytes"].items():
        print(f"{name:<30} {size / GIB:10.2f} GiB")
    print(f"{'total':<30} {value['total_bytes'] / GIB:10.2f} GiB")
    if value["unreferenced"]:
        print("\nunreferenced:")
        for row in value["unreferenced"]:
            print(f"  {row['bytes'] / GIB:8.2f} GiB  {row['path']}  ({row['why']})")
    print("\nlargest files:")
    for row in value["largest"]:
        print(f"  {row['bytes'] / GIB:8.2f} GiB  {row['path']}")


def main(argv: list[str]) -> int:
    if argv and argv[0] == "cache":
        action = argv[1] if len(argv) > 1 else "status"
        if action == "status":
            for item, size, removable in cache_entries(browsers="--browsers" in argv):
                print(f"{size / 2**20:10.1f} MiB  {item}{'' if removable else '  (kept; --browsers to include)'}")
            return 0
        if action == "clean":
            result = clean_cache(apply="--apply" in argv, browsers="--browsers" in argv)
            print(json.dumps(result, indent=1))
            if not result["applied"]:
                print("dry run: nothing removed; add --apply to remove the entries listed under removed")
            return 0
        print("usage: dwarfstar cache status | clean [--browsers] [--apply]")
        return 2
    if "--empty-chat-trash" in argv:
        print(json.dumps(empty_chat_trash(apply="--apply" in argv), indent=1))
        return 0
    value = report()
    if "--json" in argv:
        print(json.dumps(value, indent=1))
    else:
        _print_report(value)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
