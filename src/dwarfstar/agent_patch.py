"""DwarfStar's scoped edits to the Oh My Pi agent bundle, applied from data in the repository.

    dwarfstar agent-patch            report the installed bundle's state (stock, patched, legacy, unknown)
    dwarfstar agent-patch --apply    patch a stock bundle, or migrate a legacy one; then write permission-runtime.json
    dwarfstar agent-patch --revert   restore the stock bundle from the edits (exactly: the stock hash is verified)

The edits are four exact snippet replacements in the agent's dist/cli.js (acp/omp-patch.json), each found exactly
once. They take effect only under the environment bin/dwarfstar-acp sets (DWARFSTAR_PERMISSION_POLICY,
DWARFSTAR_APPROVAL_MODE, DWARFSTAR_PERMISSION_MODULE); any other use of the agent behaves exactly as upstream.
Nothing is downloaded. A bundle in a state this file does not describe is never modified.

permission-runtime.json (in the data root's config) records the patched bundle's hash; the ACP launcher refuses to
start an agent whose bundle no longer matches it, so an agent update cannot silently drop the approval scoping.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

from dwarfstar import paths
from dwarfstar.paths import CODE

SPEC = CODE / "acp" / "omp-patch.json"
RUNTIME_RECORD = "permission-runtime.json"
POLICY = "dwarfstar-approval-v2"
MODULE_VARIABLE = "DWARFSTAR_PERMISSION_MODULE"


def spec() -> dict:
    return json.loads(SPEC.read_text())


def bundle(packages: Path | None = None) -> Path:
    data = spec()
    root = (packages or paths.omp_packages()).parent  # .../node_modules/@oh-my-pi -> .../node_modules
    return root / data["package"] / data["file"]


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _legacy(data: dict) -> list[dict]:
    home = str(Path.home())
    return [{**row, "p3_new": row["p3_new"].replace("{home}", home)} for row in data.get("legacy", [])]


def state(text: str, data: dict | None = None) -> str:
    """stock, patched, legacy or unknown, judged by the snippets themselves (each exactly once), so a bundle that differs
    elsewhere -- a fresh install, or one carrying another tool's unrelated edit -- is still recognised. The whole-file
    hashes in the spec are the reference states seen on the development machine, reported but not required."""
    data = data or spec()
    patches = data["patches"]
    if all(text.count(p["new"]) == 1 for p in patches) and not any(text.count(p["old"]) for p in patches):
        return "patched"
    others = [p for p in patches if p["id"] != "p3"]
    for row in _legacy(data):
        if text.count(row["p3_new"]) == 1 and all(text.count(p["new"]) == 1 for p in others):
            return "legacy"
    if all(text.count(p["old"]) == 1 for p in patches) and "DWARFSTAR_" not in text:
        return "stock"
    return "unknown"


def _replace(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"{label}: expected the snippet exactly once, found {text.count(old)}")
    return text.replace(old, new, 1)


def patched_text(text: str, data: dict | None = None) -> str:
    """The patched bundle from a stock or legacy one. Raises ValueError for anything else."""
    data = data or spec()
    current = state(text, data)
    if current == "patched":
        return text
    if current == "legacy":
        legacy = next(row for row in _legacy(data) if text.count(row["p3_new"]) == 1)
        p3 = next(p for p in data["patches"] if p["id"] == "p3")
        text = _replace(text, legacy["p3_new"], p3["new"], "legacy p3")
    elif current == "stock":
        for patch in data["patches"]:
            text = _replace(text, patch["old"], patch["new"], patch["id"])
    else:
        raise ValueError("the agent bundle holds neither the stock nor a known DwarfStar form of every snippet; it is left untouched")
    if state(text, data) != "patched":
        raise ValueError("patching did not produce the expected snippets; the bundle is left untouched")
    return text


def stock_text(text: str, data: dict | None = None) -> str:
    data = data or spec()
    text = patched_text(text, data)  # normalise a legacy bundle first
    for patch in reversed(data["patches"]):
        text = _replace(text, patch["new"], patch["old"], patch["id"])
    if state(text, data) != "stock":
        raise ValueError("reverting did not reproduce the stock snippets")
    return text


def version_of(cli: Path) -> str | None:
    try:
        return json.loads((cli.parent.parent / "package.json").read_text()).get("version")
    except (OSError, ValueError):
        return None


def _write_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".dwarfstar-tmp")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, path.stat().st_mode & 0o777)
    temporary.replace(path)


def write_runtime_record(cli: Path, data: dict) -> dict:
    record = {
        "schema_version": 1,
        "policy": POLICY,
        "patch": data["name"],
        "omp_version": data["version"],
        "cli": str(cli),
        "cli_sha256": hashlib.sha256(cli.read_bytes()).hexdigest(),
        "module_variable": MODULE_VARIABLE,
        "scope": "DwarfStar sessions only (bin/dwarfstar-acp environment); other Oh My Pi uses keep upstream behaviour",
        "written_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    target = paths.SHARED_CONFIG / RUNTIME_RECORD
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=1) + "\n")
    return record


def report() -> dict:
    data = spec()
    cli = bundle()
    if not cli.is_file():
        return {"ok": False, "state": "missing", "cli": str(cli),
                "remedy": f"Install the agent: bun add -g {data['package']}@{data['version']}, then bin/dwarfstar agent-patch --apply"}
    if version_of(cli) != data["version"]:
        return {"ok": False, "state": "unsupported version", "version": version_of(cli), "expected": data["version"], "cli": str(cli),
                "remedy": f"The edits are written for {data['package']} {data['version']}; install that version or update acp/omp-patch.json"}
    text = cli.read_text(encoding="utf-8")
    current = state(text, data)
    reference = {data["stock_sha256"]: "stock reference", data["patched_sha256"]: "patched reference"}.get(digest(text), "differs from the references elsewhere")
    record_path = paths.SHARED_CONFIG / RUNTIME_RECORD
    try:
        record = json.loads(record_path.read_text())
    except (OSError, ValueError):
        record = None
    recorded = bool(record) and record.get("cli_sha256") == hashlib.sha256(cli.read_bytes()).hexdigest()
    return {"ok": current == "patched" and recorded, "state": current, "version": data["version"], "cli": str(cli),
            "whole_file": reference, "runtime_record": "matches" if recorded else ("stale" if record else "missing")}


def apply() -> dict:
    data = spec()
    cli = bundle()
    if version_of(cli) != data["version"]:
        raise ValueError(f"agent {version_of(cli)} installed; the edits are written for {data['version']}")
    text = cli.read_text(encoding="utf-8")
    before = state(text, data)
    patched = patched_text(text, data)
    if patched != text:
        backups = paths.BACKUPS / "omp"
        backups.mkdir(parents=True, exist_ok=True)
        copy = backups / f"cli-{digest(text)[:16]}.js"
        if not copy.exists():
            shutil.copy2(cli, copy)  # the bundle as it was, for an exact manual restore
        _write_atomic(cli, patched)
    record = write_runtime_record(cli, data)
    return {"ok": True, "before": before, "after": state(cli.read_text(encoding="utf-8"), data), "record": record}


def revert() -> dict:
    data = spec()
    cli = bundle()
    text = cli.read_text(encoding="utf-8")
    before = state(text, data)
    if before != "stock":
        _write_atomic(cli, stock_text(text, data))
    record = paths.SHARED_CONFIG / RUNTIME_RECORD
    record.unlink(missing_ok=True)  # the launcher must not trust an unpatched bundle
    return {"ok": True, "before": before, "after": state(cli.read_text(encoding="utf-8"), data)}


def main(argv: list[str]) -> int:
    try:
        result = apply() if "--apply" in argv else revert() if "--revert" in argv else report()
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, indent=1))
        return 1
    print(json.dumps(result, indent=1))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
