"""Versioned persistent data and its forward migrations.

    dwarfstar migrate            report what would change (dry run)
    dwarfstar migrate --apply    migrate; the original of every changed file is copied first to
                                 <data>/backups/migrations/<UTC time>/, keeping its path under the data root
    dwarfstar migrate --rollback [--apply]
                                 write the versions the previous release reads (ROLLBACK), before running an
                                 older checkout on this data; this release migrates them forward again at start

Each store records its version under "schema". A migration is a pure function from one version of a record to the
next; MIGRATIONS lists them in order, so version N reaches CURRENT by applying N->N+1 repeatedly. Migrations are
idempotent (a current record is unchanged) and never drop user content: chats keep every message, its role, text, time
and tool titles, their agent session, organization, fork and Zed links. The one thing a migration removes is
reasoning text that an earlier build may have left in a message: the Workstation never stores or shows reasoning.
A record written by a newer DwarfStar (a version above CURRENT) or one that is not valid JSON is reported and left
untouched. The service migrates at start, so an old record is never read in an old shape.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Callable

from dwarfstar import paths

# Message fields that carry reasoning in some builds' records; never part of the Workstation transcript.
REASONING_KEYS = ("reasoning", "reasoning_content", "thinking", "thought", "thoughts")


# ------------------------------------------------------------------ chats

def _chat_0_to_1(record: dict) -> dict:
    """Before schema numbering: the earliest records had id, title and messages only."""
    return {**record, "schema": 1}


def _chat_1_to_2(record: dict) -> dict:
    """Schema 2 names every field the Workstation relies on, with the defaults earlier builds implied by omission:
    organization (pins, stars, tags, archive), the manual-title flag, compactions and context, the agent session link.
    Zed links (workspace, transcript, shared) and fork metadata are kept as they are. Reasoning is removed from messages."""
    out = dict(record)
    out.setdefault("title", "New chat")
    out.setdefault("title_manual", False)
    out.setdefault("archived", False)
    out.setdefault("pinned", False)
    out.setdefault("starred", False)
    out.setdefault("tags", [])
    out.setdefault("compactions", 0)
    out.setdefault("agent_session_id", None)
    out.setdefault("context", {"tokens": 0, "window": 131072})
    created = out.get("created") or out.get("updated") or 0.0
    out.setdefault("created", created)
    out.setdefault("updated", created)
    messages = []
    for message in out.get("messages") or []:
        if isinstance(message, dict):
            messages.append({k: v for k, v in message.items() if k not in REASONING_KEYS})
    out["messages"] = messages
    out["schema"] = 2
    return out


def _chat_2_to_3(record: dict) -> dict:
    """Schema 3 adds folders: a chat names its folder (None is Unfiled). Nothing else changes: id, agent session,
    transcript, Zed links, compactions and timestamps are kept as they are."""
    return {**record, "folder": record.get("folder"), "schema": 3}


# ------------------------------------------------------- workstation state

def _state_0_to_1(record: dict) -> dict:
    """The selection file had no version; it keeps every key it has."""
    return {**record, "schema": 1}


# ------------------------------------------------------- permission record

def _permission_0_to_1(record: dict) -> dict:
    """Records written before agent-patch (2026-11) had no version and a field naming the retired tree."""
    out = {k: v for k, v in record.items() if k not in ("schema_version", "reversal_legacy_record")}
    out["schema"] = 1
    return out


Migration = Callable[[dict], dict]
MIGRATIONS: dict[str, list[Migration]] = {
    "chat": [_chat_0_to_1, _chat_1_to_2, _chat_2_to_3],  # index N migrates version N to N+1
    "workstation_state": [_state_0_to_1],
    "approval": [],
    "permission_runtime": [_permission_0_to_1],
    "folders": [],
}
CURRENT = {kind: len(steps) for kind, steps in MIGRATIONS.items()}
CURRENT["approval"] = 1  # born versioned
CURRENT["folders"] = 1   # born versioned (1.0.0)


# The versions the previous release (before 1.0.0's versioning) reads, where they differ from CURRENT. Its chat reader
# accepts only schema 1. Chat schemas 2 and 3 add only fields that reader ignores (defaults, the folder), so going back
# is the version number alone and loses nothing: a later forward migration finds the folder still there.
ROLLBACK = {"chat": 1}


def _chat_2_to_1(record: dict) -> dict:
    return {**record, "schema": 1}


def _chat_3_to_2(record: dict) -> dict:
    return {**record, "schema": 2}


DOWNGRADES: dict[str, dict[int, Migration]] = {"chat": {3: _chat_3_to_2, 2: _chat_2_to_1}}  # version N -> N-1


class SchemaError(ValueError):
    pass


def version(record: dict) -> int:
    value = record.get("schema", record.get("schema_version", 0))
    if not isinstance(value, int) or value < 0:
        raise SchemaError(f"invalid schema {value!r}")
    return value


def migrate_record(kind: str, record: dict) -> tuple[dict, bool]:
    """The record at the current version, and whether anything changed. Raises SchemaError for a newer version."""
    if not isinstance(record, dict):
        raise SchemaError("not a JSON object")
    start = version(record)
    if start > CURRENT[kind]:
        raise SchemaError(f"{kind} schema {start} was written by a newer DwarfStar (this one reads up to {CURRENT[kind]})")
    current = record
    for step in MIGRATIONS[kind][start:]:
        current = step(current)
    if version(current) != CURRENT[kind]:
        current = {**current, "schema": CURRENT[kind]}
    return current, current != record


def rollback_record(kind: str, record: dict) -> tuple[dict, bool]:
    """The record at the version the previous release reads (bringing an older one to CURRENT first)."""
    current, _ = migrate_record(kind, record)
    target = ROLLBACK.get(kind, CURRENT[kind])
    while version(current) > target:
        current = DOWNGRADES[kind][version(current)](current)
    return current, current != record


def stores(root: Path | None = None) -> list[tuple[str, Path]]:
    data = root or paths.DATA
    found = [("chat", path) for path in sorted((data / "chats").glob("c*/chat.json"))]
    for kind, path in (("workstation_state", data / "state" / "workstation.json"),
                       ("approval", data / "config" / "approval.json"),
                       ("permission_runtime", data / "config" / "permission-runtime.json"),
                       ("folders", data / "chats" / "folders.json")):
        if path.is_file():
            found.append((kind, path))
    return found


def _write_atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".migrating")
    temporary.write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n")
    os.chmod(temporary, path.stat().st_mode & 0o777)
    temporary.replace(path)


def migrate_all(apply: bool = False, root: Path | None = None, rollback: bool = False) -> dict:
    data = root or paths.DATA
    convert = rollback_record if rollback else migrate_record
    targets = {**CURRENT, **ROLLBACK} if rollback else CURRENT
    changed, current, errors = [], 0, []
    backup = None
    for kind, path in stores(data):
        relative = str(path.relative_to(data))
        try:
            record = json.loads(path.read_text())
            migrated, differs = convert(kind, record)
        except (OSError, ValueError) as error:
            errors.append({"file": relative, "error": str(error)})
            continue
        if not differs:
            current += 1
            continue
        changed.append({"file": relative, "kind": kind, "from": version(record), "to": targets[kind]})
        if apply:
            if backup is None:
                backup = (paths.BACKUPS if root is None else data / "backups") / "migrations" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            copy = backup / relative
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, copy)
            _write_atomic(path, migrated)
            if json.loads(path.read_text()) != migrated:  # what is on disk is what was meant
                raise RuntimeError(f"{relative}: the migrated record did not read back")
    return {"ok": not errors, "applied": apply, "rollback": rollback, "changed": changed, "current": current,
            "errors": errors, "backup": str(backup) if backup else None, "versions": targets}


def main(argv: list[str]) -> int:
    result = migrate_all(apply="--apply" in argv, rollback="--rollback" in argv)
    print(json.dumps(result, indent=1))
    if not result["applied"] and result["changed"]:
        print("dry run: nothing written; add --apply to write (originals are backed up first)")
    if result["applied"] and result["rollback"]:
        print("rolled back: stop this release's service before starting an older one (it migrates forward at start)")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
