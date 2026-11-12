"""Editor entries generated from the catalog.

Zed's ACP ``thinking`` option cannot change the model's reasoning effort,
because each provider file pins one effort. The editor therefore gets one
explicit external-agent entry per profile, all launching through the same
``bin/dwarfstar-acp`` script with a ``--profile`` argument.

    dwarfstar zed            print the generated entries
    dwarfstar zed --check    report whether the editor settings match
    dwarfstar zed --apply    replace every DwarfStar entry with the generated set
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from dwarfstar import catalog
from dwarfstar.paths import CODE, DATA

SETTINGS = Path.home() / ".config/zed/settings.json"
PREFIX = "DwarfStar "


def entries() -> dict[str, dict]:
    launcher = str(CODE / "bin/dwarfstar-acp")
    result = {}
    for profile in catalog.profiles():
        result[profile["label"]] = {
            "type": "custom",
            "command": launcher,
            "args": ["--profile", profile["id"]],
            "env": {},
            "default_config_options": {"thinking": profile["reasoning_effort"]},
        }
    return result


def current(settings: Path = SETTINGS) -> dict:
    return json.loads(settings.read_text()).get("agent_servers", {})


def check(settings: Path = SETTINGS) -> dict:
    ours = {name: value for name, value in current(settings).items() if name.startswith(PREFIX)}
    expected = entries()
    return {
        "ok": ours == expected,
        "missing": sorted(set(expected) - set(ours)),
        "obsolete": sorted(set(ours) - set(expected)),
        "different": sorted(name for name in set(ours) & set(expected) if ours[name] != expected[name]),
    }


def apply(settings: Path = SETTINGS) -> dict:
    """Replace all DwarfStar agent entries; leave every other setting untouched. Keeps a dated backup."""
    document = json.loads(settings.read_text())
    before = dict(document.get("agent_servers", {}))
    kept = {name: value for name, value in before.items() if not name.startswith(PREFIX)}
    document["agent_servers"] = {**kept, **entries()}
    backup_dir = DATA / "migration" / "zed-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / ("settings-" + time.strftime("%Y%m%dT%H%M%S") + ".json")
    shutil.copy2(settings, backup)
    temporary = settings.with_name(settings.name + ".dwarfstar-tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n")
    temporary.replace(settings)
    return {
        "ok": True,
        "backup": str(backup),
        "removed": sorted(name for name in before if name.startswith(PREFIX) and name not in entries()),
        "written": sorted(entries()),
        "untouched": sorted(kept),
    }


def main(argv: list[str]) -> int:
    if "--apply" in argv:
        print(json.dumps(apply(), indent=2))
        return 0
    if "--check" in argv:
        result = check()
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    print(json.dumps({"agent_servers": entries()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
