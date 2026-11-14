"""Backup and restore of DwarfStar's small, irreplaceable state.

    dwarfstar backup [--out DIR] [--include-trash]
        writes DwarfStar-Backup-<UTC>.zip (default DIR: <assets>/Backups, outside the data root)
    dwarfstar restore BACKUP.zip               validate it and show what a restore would change (nothing is written)
    dwarfstar restore BACKUP.zip --apply       restore: the current state is backed up first, the backup's files are
                                               merged in (nothing absent from the backup is deleted), then migrated
                                               to the current schema and every restored chat is checked to load

In a backup (paths relative to the data root unless noted):
  chats/<id>/chat.json                       every chat record
  chats/<id>/workspace/.omp-dwarfstar-*-agent/sessions/**
                                             the agent's session files: the canonical transcript of shared chats
  chats/<id>/workspace/<visible files>       files the agent wrote for you in the chat's workspace
  config/approval.json, state/workstation.json
  code/config/local.json                     the checkout's optional local configuration
  catalog/                                   runtimes.json, runtime-policy.json, profiles.json and the model files'
                                             names, sizes and SHA-256 (provenance references, not the files)
  manifest.json                              versions, schemas and every file's size and SHA-256
Never: model weights, runtimes, caches, logs, telemetry, deleted chats (unless --include-trash), the agent's
scratch, caches and databases, or permission-runtime.json (it describes this machine's agent bundle; run
`dwarfstar agent-patch --apply` after restoring on another machine).

A backup holds your chats as they are; keep it as you would the chats. The archive is written readable by you only.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

from dwarfstar import __version__, catalog, migrations, paths
from dwarfstar.paths import CODE

FORMAT = 1
NAME_PREFIX = "DwarfStar-Backup-"
STATE_FILES = ("config/approval.json", "state/workstation.json")
SKIP_WORKSPACE_DIRS = ("node_modules",)


def default_directory() -> Path:
    return paths.ASSETS / "Backups"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _workspace_files(workspace: Path) -> list[Path]:
    """The workspace files a backup keeps: agent session files and visible files; never scratch, caches or databases."""
    keep = []
    if not workspace.is_dir():
        return keep
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        parts = path.relative_to(workspace).parts
        top = parts[0]
        if top.startswith(".omp-dwarfstar-") and top.endswith("-agent"):
            if len(parts) > 2 and parts[1] == "sessions":
                keep.append(path)
            continue
        if top.startswith(".") or any(part in SKIP_WORKSPACE_DIRS for part in parts):
            continue
        keep.append(path)
    return keep


def collect(data: Path | None = None, include_trash: bool = False) -> list[tuple[str, Path]]:
    """(archive name, source file) for everything a backup holds from the data root and the checkout."""
    data = data or paths.DATA
    files = []
    chats = data / "chats"
    folders = sorted(chats.glob("c*")) if chats.is_dir() else []
    if include_trash and (chats / ".deleted").is_dir():
        folders += sorted((chats / ".deleted").iterdir())
    for folder in folders:
        record = folder / "chat.json"
        if not record.is_file():
            continue
        files.append((str(PurePosixPath(*record.relative_to(data).parts)), record))
        for path in _workspace_files(folder / "workspace"):
            files.append((str(PurePosixPath(*path.relative_to(data).parts)), path))
    for name in STATE_FILES:
        if (data / name).is_file():
            files.append((name, data / name))
    local = CODE / "config" / "local.json"
    if local.is_file():
        files.append(("code/config/local.json", local))
    return files


def _catalog_snapshot() -> dict[str, bytes]:
    snapshot = {f"catalog/{name}": (CODE / "config" / name).read_bytes()
                for name in ("runtimes.json", "runtime-policy.json", "profiles.json") if (CODE / "config" / name).is_file()}
    models = {model_id: {"display_name": m["display_name"], "source": m.get("source", {}), "runtime": m.get("runtime"),
                         "assets": {role: {"path": a["path"], "bytes": a["bytes"], "sha256": a["sha256"]} for role, a in m["assets"].items()}}
              for model_id, m in catalog.models().items()}
    snapshot["catalog/models.json"] = (json.dumps(models, indent=1) + "\n").encode()
    return snapshot


def _git_revision() -> str | None:
    try:
        return subprocess.run(["git", "-C", str(CODE), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip() or None
    except OSError:
        return None


def create(out: Path | None = None, include_trash: bool = False, data: Path | None = None, now: float | None = None) -> dict:
    data = data or paths.DATA
    out = out or default_directory()
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now or time.time()))
    target = out / f"{NAME_PREFIX}{stamp}.zip"
    entries, contents = [], {}
    for name, source in collect(data, include_trash):
        contents[name] = source.read_bytes()
    contents.update(_catalog_snapshot())
    for name, blob in sorted(contents.items()):
        entries.append({"path": name, "bytes": len(blob), "sha256": _sha(blob)})
    chat_ids = sorted({e["path"].split("/")[1] for e in entries if e["path"].startswith("chats/") and e["path"].endswith("/chat.json")})
    manifest = {
        "format": FORMAT, "kind": "DwarfStar backup", "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now or time.time())),
        "dwarfstar_version": __version__, "repository_revision": _git_revision(),
        "schemas": migrations.CURRENT, "runtime": {m: catalog.model(m)["runtime"] for m in catalog.models()},
        "chats": len(chat_ids), "files": entries, "total_bytes": sum(e["bytes"] for e in entries),
        "excluded": "model weights, runtimes, caches, logs, telemetry, deleted chats unless included, agent scratch and "
                    "databases, permission-runtime.json",
    }
    temporary = target.with_suffix(".zip.partial")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=1) + "\n")
        for name, blob in sorted(contents.items()):
            archive.writestr(name, blob)
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    return {"ok": True, "backup": str(target), "chats": len(chat_ids), "files": len(entries), "bytes": target.stat().st_size}


def _safe(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and bool(path.parts) and "\\" not in name


def read(backup: Path) -> tuple[dict, dict[str, bytes]]:
    """The manifest and the files, after checking the format, every path and every checksum. Raises ValueError."""
    try:
        archive = zipfile.ZipFile(backup)
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError(f"not a DwarfStar backup: {error}") from error
    with archive:
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, ValueError) as error:
            raise ValueError("the backup has no readable manifest") from error
        if manifest.get("kind") != "DwarfStar backup" or manifest.get("format") != FORMAT:
            raise ValueError(f"unsupported backup format {manifest.get('format')!r}")
        listed = {e["path"]: e for e in manifest["files"]}
        names = set(archive.namelist()) - {"manifest.json"}
        if names != set(listed):
            raise ValueError("the backup's files do not match its manifest")
        files = {}
        for name, entry in listed.items():
            if not _safe(name):
                raise ValueError(f"unsafe path in backup: {name}")
            blob = archive.read(name)
            if len(blob) != entry["bytes"] or _sha(blob) != entry["sha256"]:
                raise ValueError(f"checksum mismatch: {name}")
            files[name] = blob
    for kind, version in (manifest.get("schemas") or {}).items():
        if kind in migrations.CURRENT and version > migrations.CURRENT[kind]:
            raise ValueError(f"made by a newer DwarfStar ({kind} schema {version}); this one reads up to {migrations.CURRENT[kind]}")
    return manifest, files


def _destination(name: str, data: Path) -> Path | None:
    if name.startswith("catalog/"):
        return None  # provenance, not restored
    if name.startswith("code/"):
        return CODE / name[len("code/"):]
    return data / name


def plan(backup: Path, data: Path | None = None) -> dict:
    data = data or paths.DATA
    manifest, files = read(backup)
    changes = {"add": [], "replace": [], "same": [], "skip": []}
    for name, blob in sorted(files.items()):
        target = _destination(name, data)
        if target is None:
            continue
        if name == "code/config/local.json" and target.is_file() and target.read_bytes() != blob:
            changes["skip"].append(name)  # never overwrite this checkout's local configuration
        elif not target.exists():
            changes["add"].append(name)
        elif target.read_bytes() == blob:
            changes["same"].append(name)
        else:
            changes["replace"].append(name)
    chats = lambda rows: sorted({n.split("/")[1] for n in rows if n.startswith("chats/") and n.endswith("/chat.json")})  # noqa: E731
    return {"ok": True, "backup": str(backup), "created_utc": manifest["created_utc"], "dwarfstar_version": manifest["dwarfstar_version"],
            "chats_added": chats(changes["add"]), "chats_replaced": chats(changes["replace"]),
            "files": {k: len(v) for k, v in changes.items()}, "changes": changes}


def _answering_chats() -> list:
    """Chats the running Workstation service is answering; restoring under them could lose a reply."""
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:8038/api/state", timeout=2) as stream:
            return json.load(stream).get("answering_chats") or []
    except Exception:
        return []


def restore(backup: Path, data: Path | None = None, check_service: bool = True) -> dict:
    data = data or paths.DATA
    if check_service and _answering_chats():
        raise ValueError("the Workstation is answering a chat; wait for it to finish (or stop the model), then restore")
    steps = plan(backup, data)
    _, files = read(backup)
    safety = create(out=data / "backups" / "pre-restore", data=data) if (data / "chats").is_dir() or any(
        (data / n).is_file() for n in STATE_FILES) else None
    staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=data))
    try:
        writes = []
        for name in steps["changes"]["add"] + steps["changes"]["replace"]:
            staged = staging / name
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(files[name])
            if _sha(staged.read_bytes()) != _sha(files[name]):
                raise ValueError(f"staging failed: {name}")
            writes.append((staged, _destination(name, data)))
        for staged, target in writes:  # each file replaced atomically; a failure leaves the safety backup to return to
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    migrated = migrations.migrate_all(apply=True, root=data)
    from dwarfstar import chats as chat_store

    unreadable = []
    for chat_id in steps["chats_added"] + steps["chats_replaced"]:
        try:
            with _chat_root(chat_store, data):
                chat_store.get(chat_id)
        except Exception as error:  # reported; the safety backup holds the state before the restore
            unreadable.append(f"{chat_id}: {error}")
    return {"ok": not unreadable and migrated["ok"], "restored": steps["files"], "chats_added": steps["chats_added"],
            "chats_replaced": steps["chats_replaced"], "safety_backup": safety["backup"] if safety else None,
            "migrations": migrated["changed"], "unreadable": unreadable}


class _chat_root:
    """Point the chat store at `data` while checking restored chats (tests restore into temporary roots)."""

    def __init__(self, store, data: Path):
        self.store, self.root = store, data / "chats"

    def __enter__(self):
        self.saved, self.store.ROOT = self.store.ROOT, self.root

    def __exit__(self, *exc):
        self.store.ROOT = self.saved


def main(argv: list[str]) -> int:
    try:
        if argv and argv[0] == "restore":
            if len(argv) < 2:
                print("usage: dwarfstar restore BACKUP.zip [--apply]")
                return 2
            backup = Path(argv[1]).expanduser()
            result = restore(backup) if "--apply" in argv else plan(backup)
            if "--apply" not in argv:
                result.pop("changes")
                result["note"] = "dry run: nothing written; add --apply to restore (the current state is backed up first)"
        else:
            out = Path(argv[argv.index("--out") + 1]).expanduser() if "--out" in argv else None
            result = create(out=out, include_trash="--include-trash" in argv)
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, indent=1))
        return 1
    print(json.dumps(result, indent=1))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
