"""`dwarfstar release-check`: is this checkout fit to tag and release? Loads no model and changes nothing.

    dwarfstar release-check            every check; exit 1 if any fails
    dwarfstar release-check --json     the same as JSON

Checks: clean tree; branch and tag (an annotated v<version> tag at HEAD for a release); version consistency;
the production runtime pin and its installed hashes; the model manifest (files present at catalog sizes); doctor
(no blocking check); repository hygiene (no giant files, weights, secrets, home paths or chats); documentation
links; persistent data at the current schemas (dry run); the seal (current schema, same tree, verifies); canonical
roots (no override in effect). "warn" marks something to look at that does not block a release.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import re
from pathlib import Path

from dwarfstar import __version__, paths

CODE = paths.CODE


def _git(*args: str) -> tuple[int, str]:
    result = subprocess.run(["git", "-C", str(CODE), *args], capture_output=True, text=True, timeout=60)
    return result.returncode, result.stdout.strip()


def _row(name: str, level: str, detail: str) -> dict:
    return {"name": name, "level": level, "detail": detail}


def _validator(module: str):
    """Load scripts/validate/<module>.py (kept as standalone scripts so CI can run them without the package)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(module, CODE / "scripts" / "validate" / f"{module}.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def checks() -> list[dict]:
    rows = []
    _, status = _git("status", "--porcelain")
    rows.append(_row("Clean tree", "ok" if not status else "error",
                     "no uncommitted or untracked files" if not status else f"{len(status.splitlines())} uncommitted or untracked paths"))

    _, branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    code, tag = _git("describe", "--tags", "--exact-match", "HEAD")
    expected = f"v{__version__}"
    if code == 0 and tag == expected:
        kind = subprocess.run(["git", "-C", str(CODE), "cat-file", "-t", f"refs/tags/{tag}"], capture_output=True, text=True).stdout.strip()
        rows.append(_row("Branch and tag", "ok" if kind == "tag" else "error",
                         f"{branch} · {tag} at HEAD" + ("" if kind == "tag" else " is lightweight; a release tag must be annotated")))
    else:
        rows.append(_row("Branch and tag", "warn", f"{branch} · no {expected} tag at HEAD yet (tag it when this commit is the release)"))

    found = re.search(r'^version\s*=\s*"([^"]+)"', (CODE / "pyproject.toml").read_text(), re.M)
    project = found.group(1) if found else "unknown"
    rows.append(_row("Version", "ok" if project == __version__ else "error",
                     f"pyproject {project}, package {__version__}" + ("" if project == __version__ else ": they must match")))

    from dwarfstar import catalog, diagnostics

    production = ", ".join(sorted({m["runtime"] for m in catalog.models().values()}))
    doctor = diagnostics.checks()
    runtime_rows = [r for r in doctor if r["name"].startswith("Runtime ")]
    rows.append(_row("Runtime pin", "ok" if runtime_rows and all(r["level"] == "ok" for r in runtime_rows) else "error",
                     f"{production} for every model: " + "; ".join(r["detail"] for r in runtime_rows)))
    model_rows = [r for r in doctor if r["group"] == "Models" and not r["name"].endswith("generated configuration")]
    missing = [r["name"] for r in model_rows if r["level"] != "ok"]
    rows.append(_row("Model manifest", "ok" if not missing else "error",
                     f"{len(model_rows)} models present at catalog sizes" if not missing else "missing or wrong size: " + ", ".join(missing)))
    blocking = [r["name"] for r in doctor if r["level"] == "error"]
    optional = [r["name"] for r in doctor if r["level"] == "warn"]
    rows.append(_row("Doctor", "error" if blocking else "warn" if optional else "ok",
                     f"{len(doctor)} checks" + (f"; blocking: {', '.join(blocking)}" if blocking else "") + (f"; optional missing: {', '.join(optional)}" if optional else "")))

    hygiene = _validator("repo_hygiene")
    findings = hygiene.scan(hygiene.tracked())
    rows.append(_row("Repository hygiene", "ok" if not findings else "error",
                     "no giant files, weights, secrets, home paths or chats" if not findings else
                     "; ".join(f"{f['file']}: {f['check']} ({f['detail']})" for f in findings[:8])))

    broken = _validator("doc_links").check()
    rows.append(_row("Documentation links", "ok" if not broken else "error", "every relative link and anchor resolves" if not broken else "; ".join(broken[:8])))

    from dwarfstar import migrations

    try:
        pending = migrations.migrate_all(apply=False)
        rows.append(_row("Data migrations", "ok" if pending["ok"] and not pending["changed"] else "error" if not pending["ok"] else "warn",
                         f"{pending['current']} records at {pending['versions']}" + (f"; {len(pending['changed'])} would migrate (the service migrates at start)" if pending["changed"] else "")
                         + (f"; unreadable: {pending['errors']}" if pending["errors"] else "")))
    except OSError as error:
        rows.append(_row("Data migrations", "warn", f"data root not readable: {error}"))

    from dwarfstar import seal

    if not seal.SEAL.is_file():
        rows.append(_row("Seal", "error", f"no seal at {diagnostics.safe_path(seal.SEAL)}; run `bin/dwarfstar seal` on the release commit"))
    else:
        recorded = json.loads(seal.SEAL.read_text())
        result = seal.verify()
        _, tree = _git("rev-parse", "HEAD^{tree}")
        same_tree = recorded.get("schema", 1) >= seal.SCHEMA and recorded["repository"].get("tree") == tree
        level = "ok" if result["ok"] and same_tree else "error"
        detail = (f"schema {recorded.get('schema', 1)}, sealed {recorded['created_utc']}"
                  + ("" if same_tree else "; not sealed at this tree: run `bin/dwarfstar seal` on the release commit")
                  + ("".join("; " + p for p in result["problems"])))
        rows.append(_row("Seal", level, detail))

    overridden = [name for name in ("DWARFSTAR_ASSETS", "DWARFSTAR_DATA", "DWARFSTAR_CACHE", "DWARFSTAR_LOGS") if os.environ.get(name)]
    local = paths.local_config()
    pinned = [key for key in ("assets_root", "data_root", "cache_root", "logs_root") if local.get(key)]
    absent = [name for name, root in paths.roots().items() if not root.is_dir()]
    rows.append(_row("Canonical roots", "error" if absent else "warn" if overridden or pinned else "ok",
                     ", ".join(f"{name} {diagnostics.safe_path(root)}" for name, root in paths.roots().items())
                     + (f"; overridden by {', '.join(overridden + pinned)}" if overridden or pinned else "")
                     + (f"; missing: {', '.join(absent)}" if absent else "")))
    return rows


def main(argv: list[str]) -> int:
    rows = checks()
    failed = [r for r in rows if r["level"] == "error"]
    if "--json" in argv:
        print(json.dumps({"ok": not failed, "version": __version__, "checks": rows}, indent=1))
    else:
        mark = {"ok": "ok  ", "warn": "warn", "error": "FAIL"}
        for row in rows:
            print(f"{mark[row['level']]}  {row['name']:<20} {row['detail']}")
        print(f"\n{'ready to release' if not failed else str(len(failed)) + ' check(s) block a release'} (DwarfStar {__version__})")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
