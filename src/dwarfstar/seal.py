"""Installation seal and production baseline.

    dwarfstar seal            write <data>/DWARFSTAR-PRODUCTION-SEAL.json and the baseline it summarizes
    dwarfstar seal --verify   cheap check of the installation against the seal (no model is loaded)
    dwarfstar seal --verify --hash-models   also recompute every model file's SHA-256

The baseline lists every tracked product file with its SHA-256. The seal is a
small summary a later session can check in seconds: repository revision,
catalog and profile hashes, launcher hashes, runtime build hashes, model file
identities, context and cache settings, telemetry schema, benchmark index
hash, application bundle hash and known limitations.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from dwarfstar import __version__, catalog, instance, telemetry, zed
from dwarfstar.paths import CODE, SEAL_DIRECTORY, workstation_binary

SEAL = SEAL_DIRECTORY / "DWARFSTAR-PRODUCTION-SEAL.json"
BASELINE = SEAL_DIRECTORY / "DWARFSTAR-PRODUCTION-BASELINE.json"
LIMITATIONS = CODE / "docs" / "known-limitations.json"


def sha(path: Path) -> str:
    return instance.sha256_file(path)


def text_sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def git(*args: str) -> str:
    result = subprocess.run(["git", "-C", str(CODE), *args], capture_output=True, text=True, timeout=60)
    return result.stdout.strip()


def tracked_files() -> list[str]:
    listed = git("ls-files")
    return sorted(listed.splitlines()) if listed else []


def tree_sha(root: Path) -> str | None:
    if not root.is_dir():
        return None
    entries = {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()}
    return text_sha(entries)


def build() -> tuple[dict, dict]:
    files = {name: sha(CODE / name) for name in tracked_files() if (CODE / name).is_file()}
    baseline = {
        "schema": 1, "name": "DwarfStar Production Baseline", "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repository_revision": git("rev-parse", "HEAD") or None, "branch": git("rev-parse", "--abbrev-ref", "HEAD") or None,
        "tracked_files": files,
    }
    models = {}
    for model_id, model in catalog.models().items():
        manifest = instance.runtime_manifest(model_id)
        generated = instance.expected(model_id)
        models[model_id] = {
            "display_name": model["display_name"],
            "source": {k: model["source"][k] for k in ("repository", "revision") if k in model["source"]},
            "source_policy": model["source"].get("policy"),
            "context": model["context"], "max_output": model["max_output"], "port": model["port"],
            "reasoning_efforts": model["reasoning_efforts"],
            "cache": {k: model["cache"][k] for k in ("k_cache_dtype", "v_cache_dtype", "kv_bytes_per_token")},
            "runtime_build": model["runtime"], "runtime_binary_sha256": manifest["binary_sha256"],
            "overrides": model["overrides"], "memory_plan": catalog.memory_plan(model_id),
            "shader_set_sha256": text_sha(manifest["shader_sha256"]), "adapter_sha256": manifest["adapter_sha256"],
            "assets": {role: {"bytes": asset["bytes"], "sha256": asset["sha256"]} for role, asset in model["assets"].items()},
            "generated_config_sha256": {name: hashlib.sha256(text.encode()).hexdigest() for name, text in generated.items()},
            "generated_config_drift": instance.check(model_id),
        }
    app = workstation_binary()
    limitations = json.loads(LIMITATIONS.read_text()) if LIMITATIONS.is_file() else []
    seal = {
        "schema": 1, "name": "DwarfStar Production Seal", "product_version": __version__,
        "created_utc": baseline["created_utc"],
        "repository": {"revision": baseline["repository_revision"], "branch": baseline["branch"],
                       "tracked_files": len(files), "baseline_sha256": text_sha(files), "dirty": bool(git("status", "--porcelain"))},
        "catalog": {"models_sha256": {p.name: sha(p) for p in sorted((CODE / "config/models").glob("*.json"))},
                    "profiles_sha256": sha(CODE / "config/profiles.json"), "runtimes_sha256": sha(CODE / "config/runtimes.json"),
                    "runtime_policy_sha256": sha(CODE / "config/runtime-policy.json"), "runtime_policy_version": catalog.policy()["version"],
                    "default_profile": catalog.default_profile()["id"],
                    "profiles": {p["id"]: text_sha({k: p[k] for k in ("id", "model", "reasoning_effort", "label", "context")}) for p in catalog.profiles()}},
        "launchers": {name: sha(CODE / name) for name in ("bin/dwarfstar", "bin/dwarfstar-acp")},
        "models": models,
        "workstation": {"executable": str(app), "executable_sha256": sha(app) if app.is_file() else None,
                        "web_ui_sha256": tree_sha(CODE / "app/workstation/web")},
        "editor": {"entries_sha256": text_sha(zed.entries()), "installed_matches": _zed_ok()},
        "memory_policy": catalog.policy()["memory"],
        "telemetry": {"schema_version": telemetry.SCHEMA_VERSION, "event_port": telemetry.DEFAULT_EVENT_PORT,
                      "http_port": telemetry.DEFAULT_HTTP_PORT},
        "benchmark_index_sha256": sha(CODE / "research/benchmark-index.json") if (CODE / "research/benchmark-index.json").is_file() else None,
        "documentation": {name: sha(CODE / name) for name in ("README.md", "LICENSE", "docs/ARCHITECTURE.md", "docs/MODELS.md", "docs/RESEARCH-HISTORY.md") if (CODE / name).is_file()},
        "known_limitations": limitations,
    }
    return seal, baseline


def _zed_ok() -> bool | None:
    try:
        return zed.check()["ok"]
    except Exception:
        return None


def verify(hash_models: bool = False) -> dict:
    recorded = json.loads(SEAL.read_text())
    current, _ = build()
    problems = []
    for key in ("catalog", "launchers", "telemetry", "benchmark_index_sha256"):
        if recorded[key] != current[key]:
            problems.append(f"{key} differs from the seal")
    if recorded["repository"]["baseline_sha256"] != current["repository"]["baseline_sha256"]:
        problems.append("tracked product files differ from the baseline")
    if recorded["workstation"]["executable_sha256"] != current["workstation"]["executable_sha256"]:
        problems.append("Workstation executable differs from the seal")
    for model_id, row in recorded["models"].items():
        now = current["models"].get(model_id)
        if now is None:
            problems.append(f"{model_id}: missing from the catalog")
            continue
        for key in ("assets", "runtime_binary_sha256", "shader_set_sha256", "adapter_sha256", "context", "cache", "generated_config_sha256", "source", "overrides"):
            if row[key] != now[key]:
                problems.append(f"{model_id}: {key} differs from the seal")
        if now["generated_config_drift"]:
            problems.append(f"{model_id}: generated configuration drifted: {now['generated_config_drift']}")
        result = instance.verify_assets(model_id, full_hash=hash_models)
        if not result["verified"]:
            problems.append(f"{model_id}: model files: {result['errors']}")
    if current["catalog"]["default_profile"] != "scope-low":
        problems.append("default profile is not scope-low")
    return {"ok": not problems, "problems": problems, "sealed_utc": recorded["created_utc"], "model_hashes_recomputed": hash_models,
            "editor_entries_match": current["editor"]["installed_matches"]}


def main(argv: list[str]) -> int:
    if "--verify" in argv:
        result = verify(hash_models="--hash-models" in argv)
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    seal, baseline = build()
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(baseline, indent=1) + "\n")
    seal["baseline_file"] = {"path": BASELINE.name, "sha256": sha(BASELINE)}
    SEAL.write_text(json.dumps(seal, indent=2) + "\n")
    print(json.dumps({"ok": True, "seal": str(SEAL), "baseline": str(BASELINE), "tracked_files": seal["repository"]["tracked_files"],
                      "revision": seal["repository"]["revision"], "dirty": seal["repository"]["dirty"]}, indent=2))
    return 0
