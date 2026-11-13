"""Health checks and the Diagnostics document.

`checks()` is the one list of installation checks. The first-run Setup page and
`dwarfstar doctor` show all of them; Diagnostics shows them next to the live
runtime state. Every check is cheap: model files are checked by size against
the catalog (hashing ~180 GB is a separate, explicit action).

Levels: "ok" (green, ready), "warn" (yellow, an optional feature is missing),
"error" (red, something blocks use). Every non-ok check carries a remedy.
Paths are reported with the home directory written as "~".
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

from dwarfstar import catalog
from dwarfstar.paths import CODE, DATA, local_config

GIB = 2**30


def safe_path(value) -> str:
    text = str(value)
    home = str(Path.home())
    return "~" + text[len(home):] if text.startswith(home) else text


def _check(group: str, name: str, level: str, detail: str, remedy: str = "") -> dict:
    return {"group": group, "name": name, "level": level, "detail": detail, "remedy": remedy if level != "ok" else ""}


def _guard(group: str, name: str, function) -> list[dict]:
    try:
        result = function()
        return result if isinstance(result, list) else [result]
    except Exception as error:  # a broken check is itself a finding, never a crash
        return [_check(group, name, "error", f"Check failed: {type(error).__name__}: {error}", "Run `bin/dwarfstar doctor` for details.")]


# ------------------------------------------------------------------- checks


def _machine() -> list[dict]:
    rows = []
    arm = platform.machine() == "arm64" and sys.platform == "darwin"
    rows.append(_check("Machine", "Apple Silicon Mac", "ok" if arm else "error", f"{platform.system()} {platform.machine()}",
                       "DwarfStar's runtime uses Metal on Apple Silicon; other machines are not supported."))
    memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / GIB
    plans = {m: catalog.memory_plan(m)["required_gib"] for m in catalog.models()}
    fits = [m for m, need in plans.items() if need <= memory]
    level = "ok" if len(fits) == len(plans) else "warn" if fits else "error"
    rows.append(_check("Machine", "Unified memory", level, f"{memory:.0f} GiB; " + ", ".join(f"{m} needs {need:.1f} GiB" for m, need in plans.items()),
                       "Models whose requirement exceeds this Mac's memory cannot start; 64 GiB runs all three."))
    metal, source = catalog.metal_working_set_gib()
    over = [m for m in catalog.models() if catalog.memory_plan(m)["metal_headroom_gib"] < 0]
    rows.append(_check("Machine", "Metal working set", "error" if over else "ok", f"{metal:.2f} GiB ({source})",
                       "These models' plans exceed this GPU's working set: " + ", ".join(over)))
    return rows


def _software() -> list[dict]:
    from dwarfstar import paths

    rows = [_check("Software", "Python", "ok" if sys.version_info >= (3, 10) else "error", platform.python_version(),
                   "Install Python 3.10 or newer (for example `brew install python@3.12`).")]
    for name, function, level, remedy in (
        ("Bun", paths.bun, "error", "Install Bun (https://bun.sh); the agent runs on it."),
        ("Oh My Pi agent (omp)", paths.omp, "error", "Install the Oh My Pi agent CLI with Bun, then run `bin/dwarfstar generate`."),
        ("Node.js", paths.node, "warn", "Install Node 20+ for the browser tools (`brew install node`)."),
    ):
        path = function()
        rows.append(_check("Software", name, "ok" if path.exists() else level, safe_path(path), remedy))
    vendor = CODE / "acp" / "vendor" / "omp"
    rows.append(_check("Software", "Agent sources link", "ok" if vendor.exists() else "error", safe_path(vendor),
                       "Run `bin/dwarfstar generate` to link acp/vendor/omp to the installed agent."))
    swiftc = shutil.which("swiftc")
    rows.append(_check("Software", "Swift compiler", "ok" if swiftc else "warn", swiftc or "not found",
                       "Install the Xcode command-line tools (`xcode-select --install`) to build the Workstation app."))
    return rows


def _installation() -> list[dict]:
    from dwarfstar import instance

    rows = []
    local = CODE / "config" / "local.json"
    rows.append(_check("Installation", "Local configuration", "ok" if local.is_file() else "warn", safe_path(local),
                       "Copy config/local.example.json to config/local.json and set data_root."))
    writable = DATA.is_dir() and os.access(DATA, os.W_OK)
    rows.append(_check("Installation", "Data root", "ok" if writable else "error", safe_path(DATA),
                       "Create the data root named in config/local.json and make it writable."))
    pins = catalog.load()["runtimes"]["runtimes"]
    for runtime_id in sorted({m["runtime"] for m in catalog.models().values()}):
        binary = DATA / pins[runtime_id]["directory"] / "ds4-server"
        if not binary.is_file():
            rows.append(_check("Installation", f"Runtime {runtime_id}", "error", f"missing {safe_path(binary)}",
                               "Build the runtime from the pinned ds4 commit with native/ (see docs/REPRODUCIBILITY.md)."))
            continue
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        ok = digest == pins[runtime_id]["binary_sha256"]
        rows.append(_check("Installation", f"Runtime {runtime_id}", "ok" if ok else "error",
                           f"{safe_path(binary)} · SHA-256 {'matches the pin' if ok else 'differs from the pin'}",
                           "Rebuild the runtime; the controller refuses an unpinned binary."))
    for model_id, model in catalog.models().items():
        missing, wrong = [], []
        for role, asset in model["assets"].items():
            path = DATA / asset["path"]
            if not path.is_file():
                missing.append(role)
            elif path.stat().st_size != asset["bytes"]:
                wrong.append(role)
        level = "error" if missing or wrong else "ok"
        detail = "main, PLE, MTP and vision files present at catalog sizes" if level == "ok" else \
            "; ".join(filter(None, [("missing: " + ", ".join(missing)) if missing else "", ("wrong size: " + ", ".join(wrong)) if wrong else ""]))
        rows.append(_check("Models", model["display_name"], level, detail,
                           f"Place the files named in config/models/{model_id}.json under {safe_path(DATA)}/models/{model_id}/. "
                           "DwarfStar never downloads models on its own."))
        drift = instance.check(model_id)
        rows.append(_check("Models", f"{model['short_name']} generated configuration", "ok" if not drift else "error",
                           "matches the catalog" if not drift else "; ".join(drift), f"Run `bin/dwarfstar generate --model {model_id}`."))
    return rows


def _integrations() -> list[dict]:
    from dwarfstar import paths, zed

    rows = []
    zed_cli = paths.zed_cli()
    rows.append(_check("Integrations", "Zed", "ok" if zed_cli.exists() else "warn", safe_path(zed_cli),
                       "Install Zed to use the seven agent entries; the Workstation's own chats work without it."))
    if zed_cli.exists():
        try:
            entries = zed.check()
            rows.append(_check("Integrations", "Zed agent entries", "ok" if entries["ok"] else "warn",
                               "seven entries match the catalog" if entries["ok"] else f"missing {entries['missing']}, obsolete {entries['obsolete']}",
                               "Run `bin/dwarfstar zed --apply` (unrelated Zed settings are kept)."))
        except (OSError, ValueError):
            rows.append(_check("Integrations", "Zed agent entries", "warn", "no Zed settings file", "Open Zed once, then run `bin/dwarfstar zed --apply`."))
    capabilities = {}
    try:
        capabilities = json.loads((CODE / "agent-tools/state/capabilities.json").read_text())
    except (OSError, ValueError):
        pass
    browser = capabilities.get("browser_executable")
    rows.append(_check("Integrations", "Agent browser", "ok" if browser and Path(browser).exists() else "warn",
                       (capabilities.get("browser_version") or safe_path(browser)) if browser else "not detected",
                       "Run `(cd agent-tools && npm ci && npx playwright install chromium)`; the agent detects it on its next session."))
    kimicu = json.loads((CODE / "acp/kimicu.json").read_text())
    command = Path(kimicu.get("command", ""))
    rows.append(_check("Integrations", "KimiCU computer use", "ok" if kimicu.get("enabled") and command.exists() else "warn",
                       safe_path(command) if command.exists() else "not installed",
                       "Install KimiCU at /Applications/KimiCU.app and grant it Accessibility and Screen Recording; the Mac must be unlocked for GUI actions."))
    app = Path.home() / "Applications/DwarfStar Workstation.app"
    rows.append(_check("Integrations", "Workstation app", "ok" if app.is_dir() else "warn", safe_path(app),
                       'Build it: `python3 app/workstation/build.py --bundle "$HOME/Applications/DwarfStar Workstation.app"`.'))
    return rows


def checks() -> list[dict]:
    rows = []
    for group, function in (("Machine", _machine), ("Software", _software), ("Installation", _installation), ("Integrations", _integrations)):
        rows += _guard(group, group, function)
    return rows


def summary(rows: list[dict]) -> dict:
    counts = {level: sum(1 for r in rows if r["level"] == level) for level in ("ok", "warn", "error")}
    return {**counts, "ready": counts["error"] == 0}


# ---------------------------------------------------------------- document


def runtime_section(state: dict, live: dict, system: dict) -> dict:
    """The running model as the controller, the runtime's own plan and the machine see it. RAM and Metal stay separate."""
    running = state.get("running_models") or []
    model_id = running[0] if running else None
    memory = (system or {}).get("memory") or {}
    section = {
        "model": model_id, "display_name": catalog.model(model_id)["display_name"] if model_id else None,
        "active_profile": state.get("active_profile"), "selected_profile": state.get("selected_profile"),
        "system_memory": {
            "total_gib": round(memory.get("total_bytes", 0) / GIB, 2) if memory else None,
            "available_gib": round(memory.get("available_bytes", 0) / GIB, 2) if memory else None,
            "wired_gib": round(memory.get("wired_bytes", 0) / GIB, 2) if memory else None,
            "compressed_gib": round(memory.get("compressed_bytes", 0) / GIB, 2) if memory else None,
            "swap_used_gib": round(memory.get("swap_used_bytes", 0) / GIB, 2) if memory else None,
        },
    }
    if model_id:
        row = state["models"][model_id]
        model = catalog.model(model_id)
        plan = catalog.memory_plan(model_id)
        mtp_gib = model["assets"]["mtp"]["bytes"] / GIB
        runtime_plan = row.get("memory_plan") or {}
        used = ((live or {}).get("context") or {}).get("used")
        section.update({
            "pid": row.get("pid"), "port": model["port"], "healthy": row.get("healthy"), "phase": row.get("phase"),
            "uptime_s": row.get("uptime_s"), "load_seconds": row.get("load_seconds"),
            "memory": {
                "model_weights_gib": round(plan["resident_model_gib"] - mtp_gib, 2),
                "mtp_gib": round(mtp_gib, 2),
                "kv_gib": plan["kv_gib"], "buffers_gib": plan["buffers_gib"], "projector_gib": plan["projector_gib"],
                "planned_gib": plan["planned_gib"], "runtime_reported_gib": runtime_plan.get("planned_gib"),
                "metal_working_set_gib": plan["metal_working_set_gib"], "metal_headroom_gib": plan["metal_headroom_gib"],
                "system_reserve_gib": plan["system_reserve_gib"],
            },
            "context": {"used": used, "window": model["context"], "remaining": model["context"] - used if used is not None else None},
        })
    return section


def chat_section(turns) -> dict:
    from dwarfstar import chats

    rows = chats.list_chats(archived=None)
    current = rows[0] if rows else None
    active = [chat_id for chat_id in list(getattr(turns, "active", {}))] if turns else []
    detail = chats.get(current["id"]) if current else None
    return {
        "chats": len(rows), "answering": active,
        "latest": {"id": current["id"], "title": current["title"], "profile": current["profile"], "context": current["context"],
                   "compactions": (detail or {}).get("compactions", 0), "agent_session_id": (detail or {}).get("agent_session_id")} if current else None,
    }


def document(state: dict, live: dict, system: dict, hub: dict, turns=None) -> dict:
    from dwarfstar.approval_policy import status as permission_status

    rows = checks()
    return {
        "ok": True, "generated": time.time(),
        "runtime": runtime_section(state, live, system),
        "chats": chat_section(turns),
        "permissions": permission_status(),
        "telemetry": {"session": hub.get("session"), "subscribers": hub.get("subscribers"), "detail_level": hub.get("detail_level"),
                      "events": hub.get("events")},
        "lifecycle": state.get("lifecycle"),
        "checks": rows, "summary": summary(rows),
        "locations": {"code": safe_path(CODE), "data": safe_path(DATA), "chats": safe_path(DATA / "chats"),
                      "telemetry_sessions": safe_path(DATA / "telemetry" / "sessions"), "lifecycle_log": safe_path(DATA / "logs" / "lifecycle.jsonl"),
                      "local_config": "present" if local_config() else "absent"},
        "seal": {"present": (DATA / "DWARFSTAR-PRODUCTION-SEAL.json").is_file()},
    }


def main(argv: list[str]) -> int:
    """`dwarfstar doctor`: print every check with its remedy; exit 1 when something blocks use."""
    rows = checks()
    marks = {"ok": "✓", "warn": "!", "error": "✗"}
    for row in rows:
        print(f"{marks[row['level']]} {row['group']:<12} {row['name']:<34} {row['detail']}")
        if row["remedy"]:
            print(f"  {'':<12} → {row['remedy']}")
    result = summary(rows)
    print(f"\n{result['ok']} ready · {result['warn']} optional missing · {result['error']} blocking")
    return 0 if result["ready"] else 1
