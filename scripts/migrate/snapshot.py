#!/usr/bin/env python3
"""Transactional migration snapshot.

Records everything needed to prove what the installation looked like before a
restructuring step, and to put it back: registries, launchers, app bundles,
editor entries, runtime configs, model hashes and source-tree hashes.

The snapshot is local evidence. It is written under the data root and is never
tracked by git (it contains machine paths and a copy of the editor settings).

    snapshot.py --legacy-root DIR --out DIR [--hash-models] [--label TEXT]

`--hash-models` computes full SHA-256 digests for every model asset. Without
it, size, mtime and inode are recorded and previously recorded digests are
carried over where the size and mtime still match.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

MODEL_SUFFIXES = (".gguf", ".safetensors")
INSTALLATIONS = {
    "scope": "challengers/dwarfstar-paperniuk",
    "swift": "challengers/dwarfstar-swift15",
    "cyber": "challengers/dwarfstar-cyber",
}
CONFIG_FILES = (
    "runtime.json",
    "acp/profiles.json",
    "acp/models.yml",
    "acp/approval.json",
    "acp/permission-runtime.json",
    "acp/profile-integrity.json",
    "manifests/installation/assets.json",
    "installation-evidence/assets.json",
)


def sha256_file(path: Path, chunk: int = 8 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def stat_record(path: Path) -> dict:
    info = path.stat()
    return {
        "path": str(path),
        "bytes": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "inode": info.st_ino,
        "symlink_target": os.readlink(path) if path.is_symlink() else None,
    }


def tree_hash(root: Path, skip: tuple[str, ...] = ()) -> dict:
    """Hash every regular file under root; return the per-file map and a digest of it."""
    files = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if any(part in skip for part in path.relative_to(root).parts):
            continue
        if path.is_symlink():
            files[relative] = "symlink:" + os.readlink(path)
        elif path.is_file():
            files[relative] = sha256_file(path)
    combined = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {"root": str(root), "files": len(files), "tree_sha256": combined, "entries": files}


def run(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=120)
    return (result.stdout + result.stderr).strip()


def model_assets(root: Path) -> list[Path]:
    found = []
    for directory in ("models", "source"):
        base = root / directory
        if base.exists():
            found += [p for p in sorted(base.rglob("*")) if p.suffix in MODEL_SUFFIXES and not p.is_dir()]
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--legacy-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--hash-models", action="store_true")
    parser.add_argument("--hash-sources", action="store_true", help="also hash source checkpoints (slow)")
    parser.add_argument("--label", default="pre-migration")
    parser.add_argument("--apps", nargs="*", type=Path, default=[])
    parser.add_argument("--zed-settings", type=Path, default=Path.home() / ".config/zed/settings.json")
    parser.add_argument("--evidence-index", type=Path, help="JSON list of canonical evidence paths to record")
    args = parser.parse_args()

    legacy = args.legacy_root.resolve()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    copies = out / "copies"
    copies.mkdir(exist_ok=True)
    snapshot: dict = {
        "schema": 1,
        "label": args.label,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "legacy_root": str(legacy),
        "installations": {},
    }

    for name, relative in INSTALLATIONS.items():
        root = legacy / relative
        record: dict = {"root": str(root), "exists": root.is_dir()}
        if not root.is_dir():
            snapshot["installations"][name] = record
            continue
        record["configs"] = {}
        for config in CONFIG_FILES:
            path = root / config
            if path.is_file():
                target = copies / name / config
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                record["configs"][config] = {"sha256": sha256_file(path), "copy": str(target.relative_to(out))}
        providers = root / "acp/providers"
        if providers.is_dir():
            record["providers"] = {p.name: sha256_file(p) for p in sorted(providers.iterdir()) if p.is_file()}
        record["launchers"] = {
            p.name: {"sha256": sha256_file(p), "text": p.read_text(errors="replace")}
            for p in sorted((root / "bin").iterdir())
            if p.is_file()
        } if (root / "bin").is_dir() else {}
        server = root / "ds4-server"
        if server.is_file():
            record["server"] = {**stat_record(server), "sha256": sha256_file(server)}
        code = {}
        for path in sorted(root.glob("*.py")):
            code[path.name] = sha256_file(path)
        for directory in ("acp", "prompts", "profiles", "workstation", "tokenizer"):
            base = root / directory
            if base.is_dir():
                for path in sorted(base.rglob("*")):
                    if path.is_file() and path.stat().st_size < (64 << 20):
                        code[path.relative_to(root).as_posix()] = sha256_file(path)
        record["code_sha256"] = code
        assets = []
        for path in model_assets(root):
            is_source = "source" in path.relative_to(root).parts
            entry = stat_record(path)
            if args.hash_models and (not is_source or args.hash_sources):
                entry["sha256"] = sha256_file(path)
                print(f"hashed {path.name} {entry['sha256'][:16]}", flush=True)
            assets.append(entry)
        record["model_assets"] = assets
        record["model_asset_bytes"] = sum(a["bytes"] for a in assets)
        if (root / ".git").exists():
            record["git"] = {"head": run(["git", "rev-parse", "HEAD"], root), "status": run(["git", "status", "--short"], root)[:20000]}
        nested = root / "runtime-src"
        if (nested / ".git").exists():
            record["runtime_src_git"] = {
                "head": run(["git", "rev-parse", "HEAD"], nested),
                "status": run(["git", "status", "--short"], nested),
                "diff_sha256": hashlib.sha256(run(["git", "diff"], nested).encode()).hexdigest(),
            }
        snapshot["installations"][name] = record

    workstation = legacy / "workstation"
    if workstation.is_dir():
        snapshot["workstation_source"] = tree_hash(workstation, skip=("legacy-apps", "state"))
        registry = workstation / "registry.json"
        if registry.is_file():
            shutil.copy2(registry, copies / "workstation-registry.json")
        legacy_apps = workstation / "legacy-apps"
        if legacy_apps.is_dir():
            snapshot["legacy_app_bundles"] = [
                {k: v for k, v in tree_hash(p).items() if k != "entries"} for p in sorted(legacy_apps.iterdir()) if p.is_dir()
            ]

    snapshot["app_bundles"] = []
    for app in args.apps:
        if app.is_dir():
            record = tree_hash(app)
            executable = next(iter(sorted((app / "Contents/MacOS").iterdir())), None)
            snapshot["app_bundles"].append({
                "path": str(app),
                "files": record["files"],
                "tree_sha256": record["tree_sha256"],
                "executable_sha256": sha256_file(executable) if executable else None,
                "entries": record["entries"],
            })

    if args.zed_settings.is_file():
        shutil.copy2(args.zed_settings, copies / "zed-settings.json")
        settings = json.loads(args.zed_settings.read_text())
        snapshot["zed"] = {
            "path": str(args.zed_settings),
            "sha256": sha256_file(args.zed_settings),
            "agent_servers": settings.get("agent_servers", {}),
        }

    snapshot["git_status"] = {
        "legacy_root_is_repository": (legacy / ".git").exists(),
        "note": run(["git", "-C", str(legacy), "status", "--short", "--branch"])[:2000],
    }
    if args.evidence_index and args.evidence_index.is_file():
        paths = json.loads(args.evidence_index.read_text())
        snapshot["canonical_evidence"] = [{"path": p, "exists": Path(p).exists()} for p in paths]

    target = out / "snapshot.json"
    target.write_text(json.dumps(snapshot, indent=1) + "\n")
    (out / "snapshot.sha256").write_text(sha256_file(target) + "  snapshot.json\n")
    print(json.dumps({"ok": True, "snapshot": str(target), "sha256": sha256_file(target)}))


if __name__ == "__main__":
    sys.exit(main())
