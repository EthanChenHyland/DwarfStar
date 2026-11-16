"""Installation seal and production baseline.

    dwarfstar seal            write <assets>/Manifests/seal/DWARFSTAR-PRODUCTION-SEAL.json and the baseline it summarizes
    dwarfstar seal --verify   cheap check of the installation against the seal (no model is loaded)
    dwarfstar seal --verify --hash-models   also recompute every model file's SHA-256

What a seal binds (schema 2). Identity is content, not history: the repository's git tree hash and a hash of the
product files (code, configuration, launchers, app, agent and runtime patches), plus the pinned runtime hashes, the
model manifest (every file's size and SHA-256), generated configuration, the persistent data schemas and the
Workstation executable. The commit, branch and tag are recorded as metadata only, so the same tree sealed on two
commits verifies the same. Documentation, tests, CI and research evidence are in the baseline and in the tree hash,
but a difference there is reported as a note, not a failure: a report committed after sealing does not break the
seal, and verify names exactly which files differ.

The baseline lists every tracked file with its SHA-256; verify compares it with the checkout and lists changed,
added and removed files. Schema 1 seals (before 1.0.0) remain readable: they bound every tracked file through one
hash, and verify applies the same product/notes split to them using the baseline.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from dwarfstar import __version__, catalog, instance, migrations, telemetry, zed
from dwarfstar.paths import CODE, SEAL_DIRECTORY, workstation_binary

SEAL = SEAL_DIRECTORY / "DWARFSTAR-PRODUCTION-SEAL.json"
BASELINE = SEAL_DIRECTORY / "DWARFSTAR-PRODUCTION-BASELINE.json"
LIMITATIONS = CODE / "docs" / "known-limitations.json"
SCHEMA = 2
# Tracked paths whose differences are notes, not seal failures: documentation, tests, CI and research evidence.
NOT_PRODUCT_PREFIXES = ("docs/", "tests/", "research/", ".github/", "scripts/validate/")
NOT_PRODUCT_FILES = ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore")


def is_product(name: str) -> bool:
    return not name.startswith(NOT_PRODUCT_PREFIXES) and name not in NOT_PRODUCT_FILES and not name.endswith("-REPORT.md")


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


def file_diff(before: dict, after: dict) -> dict:
    """Changed, added and removed paths between two {path: sha256} maps."""
    return {"changed": sorted(k for k in before.keys() & after.keys() if before[k] != after[k]),
            "added": sorted(after.keys() - before.keys()), "removed": sorted(before.keys() - after.keys())}


def _split(diff: dict) -> tuple[dict, dict]:
    product = {kind: [n for n in names if is_product(n)] for kind, names in diff.items()}
    other = {kind: [n for n in names if not is_product(n)] for kind, names in diff.items()}
    return product, other


def _describe(diff: dict, limit: int = 12) -> str:
    parts = []
    for kind in ("changed", "added", "removed"):
        names = diff[kind]
        if names:
            parts.append(f"{kind}: " + ", ".join(names[:limit]) + (f" (+{len(names) - limit} more)" if len(names) > limit else ""))
    return "; ".join(parts)


def build() -> tuple[dict, dict]:
    files = {name: sha(CODE / name) for name in tracked_files() if (CODE / name).is_file()}
    product = {name: digest for name, digest in files.items() if is_product(name)}
    tag = subprocess.run(["git", "-C", str(CODE), "describe", "--tags", "--exact-match", "HEAD"], capture_output=True, text=True).stdout.strip()
    baseline = {
        "schema": SCHEMA, "name": "DwarfStar Production Baseline", "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
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
        "schema": SCHEMA, "name": "DwarfStar Production Seal", "product_version": __version__,
        "created_utc": baseline["created_utc"],
        "repository": {"tree": git("rev-parse", "HEAD^{tree}") or None, "product_sha256": text_sha(product),
                       "product_files": len(product), "tracked_files": len(files), "baseline_sha256": text_sha(files),
                       "dirty": bool(git("status", "--porcelain")),
                       "metadata": {"commit": baseline["repository_revision"], "branch": baseline["branch"], "tag": tag or None}},
        "schemas": {"seal": SCHEMA, **{f"data.{kind}": version for kind, version in migrations.CURRENT.items()}},
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
    schema = recorded.get("schema", 1)
    if schema > SCHEMA:
        return {"ok": False, "problems": [f"the seal has schema {schema}, written by a newer DwarfStar (this one reads up to {SCHEMA})"],
                "notes": [], "seal_schema": schema}
    current, current_baseline = build()
    problems, notes = [], []
    for key in ("catalog", "launchers", "telemetry"):
        if recorded[key] != current[key]:
            problems.append(f"{key} differs from the seal")
    if recorded.get("benchmark_index_sha256") != current["benchmark_index_sha256"]:
        notes.append("research/benchmark-index.json differs from the seal (evidence, not product)")
    if schema >= 2 and recorded.get("schemas") != current["schemas"]:
        problems.append(f"data schemas differ from the seal: sealed {recorded.get('schemas')}, now {current['schemas']}")
    sealed_files = json.loads(BASELINE.read_text()).get("tracked_files") if BASELINE.is_file() else None
    if recorded["repository"]["baseline_sha256"] != current["repository"]["baseline_sha256"]:
        if sealed_files is None or text_sha(sealed_files) != recorded["repository"]["baseline_sha256"]:
            problems.append("tracked files differ from the seal, and the baseline that lists them is missing or does not match it")
        else:
            product, other = _split(file_diff(sealed_files, current_baseline["tracked_files"]))
            if any(product.values()):
                problems.append("product files differ from the seal: " + _describe(product))
            if any(other.values()):
                notes.append("documentation, tests or evidence differ from the seal (not product): " + _describe(other))
    if schema >= 2 and recorded["repository"]["product_sha256"] != current["repository"]["product_sha256"] and \
            not any("product files differ" in p for p in problems):
        problems.append("product files differ from the seal")
    if schema >= 2:
        same_tree = recorded["repository"]["tree"] == current["repository"]["tree"]
        notes.append("git tree matches the seal" if same_tree else
                     f"git tree differs from the seal (sealed commit {str(recorded['repository']['metadata']['commit'])[:12]}, "
                     f"now {str(current['repository']['metadata']['commit'])[:12]})")
    if current["repository"]["dirty"]:
        notes.append("the checkout has uncommitted changes")
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
    return {"ok": not problems, "problems": problems, "notes": notes, "seal_schema": schema, "sealed_utc": recorded["created_utc"],
            "model_hashes_recomputed": hash_models, "editor_entries_match": current["editor"]["installed_matches"]}


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
    repository = seal["repository"]
    print(json.dumps({"ok": True, "seal": str(SEAL), "baseline": str(BASELINE), "schema": seal["schema"], "tree": repository["tree"],
                      "revision": repository["metadata"]["commit"], "tag": repository["metadata"]["tag"],
                      "tracked_files": repository["tracked_files"], "product_files": repository["product_files"],
                      "dirty": repository["dirty"]}, indent=2))
    return 0
