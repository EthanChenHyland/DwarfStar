"""`dwarfstar setup`: bring this Mac's installation to the checkout's state, then say whether it is ready.

    dwarfstar setup              do what is needed (each step is skipped when already done)
    dwarfstar setup --dry-run    report what would be done; change nothing
    dwarfstar setup --rebuild-app   rebuild the Workstation app even when the installed one is current

Steps, in order: create the data, cache and logs roots and the per-model configuration (`generate`); apply the agent
approval patch if the installed agent is not patched; replace the DwarfStar entries in Zed's settings if they differ
(other settings are kept, and a dated backup is made); build and install the Workstation app if it is missing, built
from another checkout, or another version (the previous bundle goes to a rollback folder in the cache); then run the
doctor checks. It never downloads anything and never touches models, the runtime, chats, folders or settings: a
missing model or runtime is reported with where it belongs and how to get it. Running it twice changes nothing the
second time.
"""
from __future__ import annotations

import json
import plistlib
import subprocess
import sys
from pathlib import Path

from dwarfstar import __version__, catalog, diagnostics, paths

APP = Path.home() / "Applications" / "DwarfStar Workstation.app"


def app_status(app: Path = APP) -> dict:
    """Whether the installed Workstation app belongs to this checkout and this version."""
    try:
        info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return {"installed": False, "current": False, "why": "not installed"}
    reasons = []
    if info.get("CFBundleShortVersionString") != __version__:
        reasons.append(f"version {info.get('CFBundleShortVersionString')}, this checkout is {__version__}")
    if Path(info.get("DwarfStarHome", "")).resolve() != paths.CODE.resolve():
        reasons.append("built from another checkout")
    if not info.get("CFBundleIconFile"):
        reasons.append("built before the app icon")
    return {"installed": True, "current": not reasons, "why": "; ".join(reasons) or "current",
            "version": info.get("CFBundleShortVersionString"), "revision": info.get("DwarfStarRevision")}


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, cwd=paths.CODE)


def run(dry_run: bool = False, rebuild_app: bool = False) -> dict:
    steps = []

    def step(name: str, needed: bool, action, detail: str) -> None:
        if not needed:
            steps.append({"step": name, "result": "already done", "detail": detail})
            return
        if dry_run:
            steps.append({"step": name, "result": "would run", "detail": detail})
            return
        try:
            outcome = action()
            steps.append({"step": name, "result": "done", "detail": outcome or detail})
        except Exception as error:  # reported, and setup goes on to the checks that explain it
            steps.append({"step": name, "result": "failed", "detail": str(error)})

    # 1. roots and generated configuration (idempotent: rewrites only what differs)
    from dwarfstar import instance

    missing_roots = [str(r) for r in (paths.DATA, paths.CACHE, paths.LOGS) if not r.is_dir()]
    drift = {m: instance.check(m) for m in catalog.models()}

    def generate():
        for root in (paths.DATA, paths.CACHE, paths.LOGS):
            root.mkdir(parents=True, exist_ok=True)
        for model_id in catalog.models():
            instance.generate(model_id)
        return "roots and per-model configuration written"

    step("Configuration", bool(missing_roots) or any(drift.values()), generate,
         "roots exist; generated configuration matches the catalog")

    # 2. the agent's approval scoping
    from dwarfstar import agent_patch

    patch = agent_patch.report()
    step("Agent approval patch", not patch["ok"] and patch["state"] in ("stock", "legacy", "patched"),
         lambda: f"applied ({agent_patch.apply()['after']})", f"agent {patch['state']}; permission record {patch.get('runtime_record')}")

    # 3. Zed's agent entries
    from dwarfstar import zed

    zed_cli = paths.zed_cli()
    try:
        entries = zed.check() if zed_cli.exists() else None
    except (OSError, ValueError):
        entries = None
    step("Zed entries", bool(entries) and not entries["ok"], lambda: f"{len(zed.apply()['written'])} entries written (backup kept)",
         "Zed not installed" if not zed_cli.exists() else "seven entries match" if entries and entries["ok"] else "no Zed settings file yet")

    # 4. the Workstation app
    app = app_status()
    swiftc = Path("/usr/bin/swiftc").exists()

    def build_app():
        result = _run([sys.executable, str(paths.CODE / "app" / "workstation" / "build.py"), "--install", str(APP)])
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip().splitlines()[-1])
        built = json.loads(result.stdout[result.stdout.index("{"):])
        return f"installed {built['version']} ({built['revision']})" + (f"; previous bundle kept at {diagnostics.safe_path(built['rollback'])}" if built.get("rollback") else "")

    step("Workstation app", swiftc and (rebuild_app or not app["current"]), build_app,
         f"{app.get('version')} ({app.get('revision')}) is current" if app["current"] else ("needs the Xcode command-line tools" if not swiftc else app["why"]))

    # 5. the checks
    rows = diagnostics.checks()
    return {"version": __version__, "revision": diagnostics.version_info()["revision"], "dry_run": dry_run, "steps": steps,
            "checks": rows, "summary": diagnostics.summary(rows), "app": app_status()}


def status_lines(result: dict) -> list[str]:
    rows = {r["name"]: r for r in result["checks"]}
    level = lambda row: {"ok": "ready", "warn": "optional", "error": "needs a fix"}[row["level"]] if row else "unknown"  # noqa: E731
    lines = [f"DwarfStar {result['version']}" + (f" ({result['revision']})" if result["revision"] else "")]
    runtime = next((r for r in result["checks"] if r["name"].startswith("Runtime ")), None)
    lines.append(f"  {'Runtime':<13} {level(runtime)}  {runtime['name'].removeprefix('Runtime ') if runtime else ''}")
    for model in catalog.models().values():
        row = rows.get(model["display_name"])
        lines.append(f"  {model['short_name']:<13} {level(row)}" + ("" if not row or row["level"] == "ok" else f"  {row['detail']}"))
    app = result["app"]
    lines.append(f"  {'Workstation':<13} " + (f"installed {app['version']} ({app['revision']})" if app["current"] else f"not current: {app['why']}"))
    zed_row = rows.get("Zed agent entries")
    lines.append(f"  {'Zed':<13} " + ("configured, seven entries" if zed_row and zed_row["level"] == "ok" else (zed_row["detail"] if zed_row else "not installed")))
    from dwarfstar import chats

    try:
        tree = chats.folder_tree()
        count = sum(1 for _ in chats.ROOT.glob("c*/chat.json"))
        lines.append(f"  {'Chats':<13} preserved: {count} chat{'s' if count != 1 else ''}, {len(tree['folders'])} folder{'s' if len(tree['folders']) != 1 else ''}")
    except OSError:
        lines.append(f"  {'Chats':<13} none yet")
    summary = result["summary"]
    lines.append(f"  {'Doctor':<13} {summary['ok']}/{summary['ok'] + summary['warn'] + summary['error']} ready"
                 + (f", {summary['warn']} optional missing" if summary["warn"] else "") + (f", {summary['error']} blocking" if summary["error"] else ""))
    return lines


def main(argv: list[str]) -> int:
    result = run(dry_run="--dry-run" in argv, rebuild_app="--rebuild-app" in argv)
    if "--json" in argv:
        print(json.dumps(result, indent=1))
    else:
        for row in result["steps"]:
            print(f"{row['result']:<13} {row['step']:<22} {row['detail']}")
        print()
        print("\n".join(status_lines(result)))
        blocking = [r for r in result["checks"] if r["level"] == "error"]
        for row in blocking:
            print(f"\n✗ {row['name']}: {row['detail']}\n  → {row['remedy']}")
    return 1 if result["summary"]["error"] or any(s["result"] == "failed" for s in result["steps"]) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
