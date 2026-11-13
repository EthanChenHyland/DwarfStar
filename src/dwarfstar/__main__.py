"""Command-line entry point.

    dwarfstar models                       list models and profiles
    dwarfstar generate [--model ID]        write per-model runtime configuration from the catalog
    dwarfstar assets [--model ID] [--hash] verify model files against the catalog
    dwarfstar runtime --model ID ACTION    start | stop | restart | status | health | logs | requirement
    dwarfstar acp --profile ID [--cwd DIR] run the ACP agent for a profile (what an editor launches)
    dwarfstar zed [--apply]                print or install the generated editor entries
    dwarfstar serve                        Workstation service: API, telemetry hub and user interface
    dwarfstar switch PROFILE               stop any other model, start the profile's model
    dwarfstar seal [--verify]              write or check the installation seal
    dwarfstar doctor                       installation and health checks with remedies
    dwarfstar storage [--json]            disk use by category, unreferenced large files (--empty-chat-trash [--apply])
    dwarfstar cache status | clean        recreatable cache; clean is a dry run unless --apply (--browsers to include them)
"""
from __future__ import annotations

import json
import os
import sys


def _bind_model(model_id: str) -> None:
    os.environ["DWARFSTAR_MODEL"] = model_id


def _option(argv: list[str], *names: str) -> str | None:
    for index, word in enumerate(argv):
        for name in names:
            if word == name and index + 1 < len(argv):
                return argv[index + 1]
            if word.startswith(name + "="):
                return word.split("=", 1)[1]
    return None


def _strip(argv: list[str], *names: str) -> list[str]:
    result, skip = [], False
    for word in argv:
        if skip:
            skip = False
        elif word in names:
            skip = True
        elif not any(word.startswith(name + "=") for name in names):
            result.append(word)
    return result


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    command, rest = argv[0], argv[1:]
    from dwarfstar import catalog

    if command == "models":
        print(json.dumps(catalog.summary(), indent=2))
        return 0

    if command in ("generate", "assets"):
        from dwarfstar import instance

        selected = _option(rest, "--model")
        results = []
        for model_id in [selected] if selected else list(catalog.models()):
            if command == "generate":
                results.append(instance.generate(model_id))
            else:
                results.append(instance.verify_assets(model_id, full_hash="--hash" in rest))
        print(json.dumps(results, indent=2))
        return 0 if all(r.get("verified", True) for r in results) else 1

    if command == "runtime":
        model_id = _option(rest, "--model")
        if not model_id:
            profile_id = _option(rest, "--profile", "--kind")
            model_id = catalog.profile(profile_id)["model"] if profile_id else None
        if not model_id:
            print(json.dumps({"ok": False, "error": "dwarfstar runtime needs --model"}))
            return 2
        catalog.model(model_id)
        _bind_model(model_id)
        from dwarfstar import runtime_control

        runtime_control.cli(_strip(rest, "--model"))
        return 0

    if command == "acp":
        profile_id = _option(rest, "--profile", "--kind")
        if not profile_id:
            print("dwarfstar acp needs --profile", file=sys.stderr)
            return 2
        _bind_model(catalog.profile(profile_id)["model"])
        from dwarfstar import agent

        agent.main(rest)
        return 0

    if command == "zed":
        from dwarfstar import zed

        return zed.main(rest)

    if command in ("serve", "switch", "stop-all", "state"):
        from dwarfstar import service

        return service.main([command, *rest])

    if command == "seal":
        from dwarfstar import seal

        return seal.main(rest)

    if command == "doctor":
        from dwarfstar import diagnostics

        return diagnostics.main(rest)

    if command in ("storage", "cache"):
        from dwarfstar import storage

        return storage.main(rest if command == "storage" else ["cache", *rest])

    if command == "bench-index":
        from dwarfstar import benchindex

        return benchindex.main(rest)

    print(f"Unknown command {command!r}\n\n{__doc__.strip()}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
