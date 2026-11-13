"""Generated per-model runtime roots.

The catalog is the source of truth. For each model this module derives a
runtime root under ``<data>/instances/<model>/`` containing:

* ``runtime.json``            server command line, environment and limits
* ``acp/profiles.json``       the model's selectable profiles, agent-facing
* ``acp/providers/<id>.yml``  one provider file per profile (pins the effort)
* ``assets.json``             verified identity of the model files in use
* ``state/ cache/ home/ scratch/ benchmarks/``  mutable local state

Generated files are never edited by hand: ``check`` reports drift and
``generate`` rewrites them.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from dwarfstar import catalog
from dwarfstar.paths import CODE, DATA, RUNTIMES, instance, scratch_root

MUTABLE_DIRECTORIES = ("state", "cache/native-kv", "home", "scratch", "benchmarks", "acp/providers")


def sha256_file(path: Path, chunk: int = 8 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text)
    temporary.chmod(mode)
    temporary.replace(path)


# ---------------------------------------------------------------- runtime pins


def runtime_directory(model_id: str) -> Path:
    return DATA / catalog.runtime_pin(model_id)["directory"]


def runtime_manifest(model_id: str) -> dict:
    pin = catalog.runtime_pin(model_id)
    if pin.get("kind", "native") != "native":
        # Non-native backends are pinned in config/runtimes.json; the controller re-checks the installed files.
        return {"schema": 1, "runtime": pin["id"], "binary_sha256": pin["binary_sha256"], "shader_sha256": pin.get("shader_sha256", {})}
    path = runtime_directory(model_id) / "manifest.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        raise RuntimeError(f"Native runtime is not installed: {path}. Run `dwarfstar runtime-install`.") from None


def install_runtime(runtime_id: str, binary: Path, shaders: Path, provenance: str) -> dict:
    """Copy a built server and its shader sources into the data root and record their hashes."""
    pins = catalog.load()["runtimes"]["runtimes"]
    pin = pins[runtime_id]
    digest = sha256_file(binary)
    if digest != pin["binary_sha256"]:
        raise RuntimeError(f"{binary} is {digest}, but the catalog pins {pin['binary_sha256']} for {runtime_id}")
    target = DATA / pin["directory"]
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, target / "ds4-server")
    (target / "ds4-server").chmod(0o755)
    metal = target / "metal"
    if metal.exists():
        shutil.rmtree(metal)
    shutil.copytree(shaders, metal, symlinks=False)
    manifest = {
        "schema": 1,
        "runtime": runtime_id,
        "binary_sha256": digest,
        "shader_sha256": {p.name: sha256_file(p) for p in sorted(metal.iterdir()) if p.is_file()},
        "installed_at": time.time(),
        "provenance": provenance,
    }
    atomic_write(target / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    return manifest


# ------------------------------------------------------------- generated files


def splash_command(model_id: str) -> tuple[list[str], dict[str, str]]:
    """Splash-m1 serving one DwarfStar model: the same files, port, served id and context as the native runtime."""
    model = catalog.model(model_id)
    pin = catalog.runtime_pin(model_id)
    root = DATA / pin["directory"]
    extra = model["backend"]["extra_assets"]
    argv = [
        str(root / "python/bin/python3"), "-u", str(root / "install/launcher.py"), "serve",
        "--model", model["backend"]["model_name"],
        "--model-dir", str(catalog.asset_path(model_id, "main")),
        "--mmproj", str(DATA / extra["vision"]["path"]),
        "--draft-model", str((DATA / extra["draft"]["path"]).parent),
        "--offline", "--no-webui",
        "--host", "127.0.0.1", "--port", str(model["port"]),
        "--max-context", str(model["context"]),
        "--served-model-name", model["served_model_id"],
    ]
    return argv, {"HOME": str(DATA / pin["home"])}


def server_command(model_id: str) -> tuple[list[str], dict[str, str]]:
    if catalog.runtime_pin(model_id).get("kind", "native") == "splash":
        return splash_command(model_id)
    model = catalog.model(model_id)
    root = instance(model_id)
    assets = catalog.asset_paths(model_id)
    argv = [
        str(runtime_directory(model_id) / "ds4-server"),
        "-m", str(assets["main"]),
        "--mtp",
        "--mtp-timing",
        "--ctx", str(model["context"]),
        "--prefill-chunk", str(model["prefill_chunk"]),
        "--host", "127.0.0.1",
        "--port", str(model["port"]),
        "--kv-disk-dir", str(root / "cache/native-kv"),
        "--kv-disk-space-mb", str(model["cache"]["disk_checkpoints"]["budget_mb"]),
        "--kv-cache-continued-interval-tokens", str(model["cache"]["disk_checkpoints"]["continued_interval_tokens"]),
        "--vision", str(assets["vision"]),
        "--tokens", str(model["max_output"]),
    ]
    environment = {
        "HOME": str(root / "home"),
        "DS4_QWEN_NGRAM_GGUF": str(assets["ple"]),
        "DS4_QWEN_MTP_GGUF": str(assets["mtp"]),
        **catalog.policy()["runtime"]["environment"],
    }
    environment.update(model.get("runtime_environment", {}))
    return argv, environment


def runtime_config(model_id: str) -> dict:
    model = catalog.model(model_id)
    pin = catalog.runtime_pin(model_id)
    manifest = runtime_manifest(model_id)
    argv, environment = server_command(model_id)
    return {
        "generated_from": "config/models/" + model_id + ".json",
        "model_key": model_id,
        "source": pin["source"],
        "runtime": pin["name"],
        "runtime_build": pin["id"],
        "model": model["display_name"],
        "model_id": model["served_model_id"],
        "quant": model["quantization"]["label"],
        "endpoint": f"http://127.0.0.1:{model['port']}/v1",
        "port": model["port"],
        "context": model["context"],
        "max_output": model["max_output"],
        "thinking": True,
        "reasoning_effort": "per profile",
        "reasoning_efforts": model["reasoning_efforts"],
        "mtp": "native enabled; default adaptive depth; no depth/acceptance tuning" if model["backend"]["kind"] == "native" else model["backend"]["speculative"],
        "sampling": model["sampling"],
        "argv": argv,
        "environment": environment,
        "server_sha256": manifest["binary_sha256"],
        "shader_sha256": manifest["shader_sha256"],
        "output_budgeting": "native server_decode_budget uses exact session context room; no client arithmetic patch",
        "model_files": {role: str(path) for role, path in catalog.asset_paths(model_id).items()}
        | {f"backend_{role}": str(DATA / asset["path"]) for role, asset in model["backend"]["extra_assets"].items()},
        "backend": {k: model["backend"][k] for k in ("id", "kind", "release")},
        "scratch_directory": str(scratch_root()),
        "memory": model["memory"],
    }


def provider_yaml(profile_id: str) -> str:
    profile = catalog.profile(profile_id)
    model = catalog.model(profile["model"])
    effort = profile["reasoning_effort"]
    sampling = model["sampling"]
    lines = [
        "providers:",
        "  dwarfstar-local:",
        f"    baseUrl: http://127.0.0.1:{model['port']}/v1",
        "    api: openai-completions",
        "    auth: none",
        "    models:",
        f"      - id: {model['served_model_id']}",
        f"        name: {profile['label']}",
        f"        contextWindow: {model['context']}",
        f"        maxContextWindow: {model['context']}",
        f"        maxTokens: {model['max_output']}",
    ]
    if effort == "off":
        lines += ["        reasoning: false"]
    else:
        lines += [
            "        reasoning: true",
            "        thinking:",
            "          mode: effort",
            f"          efforts: [{effort}]",
            f"          defaultLevel: {effort}",
        ]
    lines += [
        "        compat:",
        "          supportsReasoningEffort: true",
        "          reasoningContentField: reasoning_content",
        "          replayReasoningContent: true",
        "          supportsDeveloperRole: false",
        "          supportsForcedToolChoice: false",
        "          disableReasoningOnToolChoice: false",
        "          disableReasoningWithTools: false",
        "          thinkingFormat: qwen-chat-template",
        "          qwenTemplateReasoningEffort: true",
        "          extraBody:",
    ]
    if effort == "off":
        lines += ["            reasoning_effort: none", "            chat_template_kwargs: {enable_thinking: false}"]
    else:
        lines += [
            f"            reasoning_effort: {effort}",
            f"            chat_template_kwargs: {{enable_thinking: true, reasoning_effort: {effort}}}",
        ]
    lines += [
        f"            temperature: {float(sampling['temperature'])}",
        f"            top_p: {sampling['top_p']}",
        f"            top_k: {sampling['top_k']}",
        f"            min_p: {float(sampling['min_p'])}",
        "        imageInputDecoder: stb",
        "        input: [text, image]",
        "        cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}",
    ]
    return "\n".join(lines) + "\n"


def agent_profiles(model_id: str) -> dict:
    """The agent-facing profile registry for one model (read by the ACP extensions)."""
    model = catalog.model(model_id)
    rows = catalog.profiles(model_id)
    agent = model["agent"]
    return {
        "generated_from": "config/profiles.json",
        "default": rows[0]["id"],
        "active_profiles": [row["id"] for row in rows],
        "profiles": {
            row["id"]: {
                "name": row["label"],
                "model": model_id,
                "policy": row.get("policy", agent["policy"]),
                "prompt": agent["prompt"],
                "prompt_mode": agent["prompt_mode"],
                "features": agent["features"],
                "reasoning_effort": row["reasoning_effort"],
                "provider_config": f"acp/providers/{row['id']}.yml",
                "state_namespace": row["state_namespace"],
            }
            for row in rows
        },
    }


def expected(model_id: str) -> dict[str, str]:
    """Relative path -> exact content of every generated file for a model."""
    files = {
        "runtime.json": json.dumps(runtime_config(model_id), indent=2) + "\n",
        "acp/profiles.json": json.dumps(agent_profiles(model_id), indent=2) + "\n",
    }
    rows = catalog.profiles(model_id)
    for row in rows:
        files[f"acp/providers/{row['id']}.yml"] = provider_yaml(row["id"])
    files["acp/models.yml"] = provider_yaml(rows[0]["id"])
    return files


def link_agent_tool_data() -> None:
    """agent-tools keeps its mutable state and browser cache under the data root (machine-local, not in Git)."""
    for name in ("state", "cache"):
        target = DATA / name / "agent-tools"
        target.mkdir(parents=True, exist_ok=True)
        link = CODE / "agent-tools" / name
        if not link.exists() and not link.is_symlink():
            link.symlink_to(target)
    from dwarfstar.paths import omp_packages

    vendor = CODE / "acp" / "vendor" / "omp"  # the ACP transport imports the installed Oh My Pi sources
    if not vendor.exists() and not vendor.is_symlink() and omp_packages().is_dir():
        vendor.parent.mkdir(exist_ok=True)
        vendor.symlink_to(omp_packages())


def generate(model_id: str) -> dict:
    link_agent_tool_data()
    root = instance(model_id)
    for name in MUTABLE_DIRECTORIES:
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "state").chmod(0o700)
    written = []
    for relative, text in expected(model_id).items():
        path = root / relative
        if not path.exists() or path.read_text() != text:
            atomic_write(path, text)
            written.append(relative)
    stale = [p.name for p in (root / "acp/providers").glob("*.yml") if f"acp/providers/{p.name}" not in expected(model_id)]
    for name in stale:
        (root / "acp/providers" / name).unlink()
    return {"model": model_id, "root": str(root), "written": written, "removed": stale}


def check(model_id: str) -> list[str]:
    """Names of generated files that are missing or differ from the catalog."""
    root = instance(model_id)
    problems = []
    for relative, text in expected(model_id).items():
        path = root / relative
        if not path.is_file():
            problems.append(relative + ": missing")
        elif path.read_text() != text:
            problems.append(relative + ": differs from the catalog")
    return problems


# --------------------------------------------------------------------- assets


def verify_assets(model_id: str, full_hash: bool = False) -> dict:
    """Check the model files against the catalog and record their identity.

    Size is always checked. With ``full_hash`` the SHA-256 is recomputed;
    otherwise a previously verified file is accepted while its size and
    modification time are unchanged.
    """
    model = catalog.model(model_id)
    manifest_path = instance(model_id) / "assets.json"
    try:
        previous = {row["role"]: row for row in json.loads(manifest_path.read_text())["files"]}
    except (OSError, ValueError, KeyError):
        previous = {}
    rows, errors = [], []
    for role, asset in model["assets"].items():
        path = DATA / asset["path"]
        if not path.is_file():
            errors.append(f"{role}: missing {asset['path']}")
            continue
        info = path.stat()
        if info.st_size != asset["bytes"]:
            errors.append(f"{role}: size {info.st_size} differs from catalog {asset['bytes']}")
            continue
        old = previous.get(role, {})
        unchanged = old.get("bytes") == info.st_size and old.get("mtime_ns") == info.st_mtime_ns and old.get("sha256") == asset["sha256"]
        if full_hash or not unchanged:
            digest = sha256_file(path)
            if digest != asset["sha256"]:
                errors.append(f"{role}: SHA-256 {digest} differs from catalog")
                continue
        rows.append({"role": role, "path": str(path), "bytes": info.st_size, "mtime_ns": info.st_mtime_ns, "sha256": asset["sha256"]})
    result = {"schema": 1, "model": model_id, "verified": not errors, "verified_at": time.time(), "files": rows, "errors": errors}
    if not errors:
        atomic_write(manifest_path, json.dumps(result, indent=2) + "\n")
    return result


def assets_current(model_id: str) -> None:
    """Cheap pre-start check: the verified files are still the same files."""
    manifest_path = instance(model_id) / "assets.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        raise RuntimeError(f"Model files are not verified yet. Run `dwarfstar assets --model {model_id}`.") from None
    if not manifest.get("verified"):
        raise RuntimeError("Model files failed verification; nothing was started.")
    configured = sorted(str(p) for p in catalog.asset_paths(model_id).values())
    if sorted(row["path"] for row in manifest["files"]) != configured:
        raise RuntimeError("Verified model files differ from the catalog; verify again.")
    for row in manifest["files"]:
        info = os.stat(row["path"])
        if info.st_size != row["bytes"] or info.st_mtime_ns != row["mtime_ns"]:
            raise RuntimeError(f"Model file changed since verification: {row['path']}")


def code_reference(relative: str) -> Path:
    return CODE / relative
