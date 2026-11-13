"""The canonical model and profile catalog.

Everything that describes a model or a selectable profile lives in ``config/``:

* ``config/models/<id>.json`` holds the static facts of one model family.
* ``config/profiles.json`` lists the selectable profiles. A profile is a model
  plus a reasoning effort and nothing else.
* ``config/runtimes.json`` pins the native runtime builds.
* ``config/runtime-policy.json`` is the one runtime policy shared by every model:
  context, output ceiling, prefill chunk, sampling, KV cache, capabilities,
  agent policy, lifecycle and the memory formula. A model file may override a
  shared value only with a written reason (``override_reasons``).

Per-model runtime configuration, agent provider files and editor entries are
generated from this catalog (see ``dwarfstar.instance`` and ``dwarfstar.zed``).
Nothing else is a source of truth.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from dwarfstar.paths import CODE, DATA

CONFIG = CODE / "config"
EFFORTS = ("off", "low", "medium", "xhigh")
ASSET_ROLES = ("main", "ple", "mtp", "vision")


class CatalogError(ValueError):
    pass


def _runtime_overrides() -> dict[str, str]:
    """Local choice of native runtime build per model, for example to fall back to the reference build.

    Read from ``runtime_overrides`` in ``config/local.json`` and from the
    ``DWARFSTAR_RUNTIME_OVERRIDE`` environment variable (``model=runtime,...``).
    """
    import os

    from dwarfstar.paths import local_config

    # The unit tests set DWARFSTAR_IGNORE_LOCAL_OVERRIDES so this machine's own choice never changes what they test.
    overrides = {} if os.environ.get("DWARFSTAR_IGNORE_LOCAL_OVERRIDES") == "1" else dict(local_config().get("runtime_overrides", {}))
    for pair in filter(None, os.environ.get("DWARFSTAR_RUNTIME_OVERRIDE", "").split(",")):
        model_id, _, runtime_id = pair.partition("=")
        overrides[model_id.strip()] = runtime_id.strip()
    return overrides


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise CatalogError(f"Cannot read {path.relative_to(CODE)}: {error}") from error


def _apply_policy(raw: dict, defaults: dict) -> dict:
    """Shared defaults first, then the model's own values. Every deviation from the policy must say why."""
    import copy

    reasons = raw.get("override_reasons", {})
    overrides = {}
    for key, value in defaults.items():
        if key not in raw:
            continue
        if raw[key] == value:
            raise CatalogError(f"{raw['id']}: {key} repeats the shared default; remove it from the model file")
        if not str(reasons.get(key, "")).strip():
            raise CatalogError(f"{raw['id']}: overriding the shared {key} needs a reason in override_reasons")
        overrides[key] = {"value": raw[key], "default": value, "reason": reasons[key]}
    stray = set(reasons) - set(overrides)
    if stray:
        raise CatalogError(f"{raw['id']}: override_reasons for settings it does not override: {sorted(stray)}")
    return {**copy.deepcopy(defaults), **raw, "overrides": overrides}


def _apply_backend(model: dict, runtimes: dict) -> None:
    """Facts that depend on the inference backend (memory, cache, extra assets, telemetry) come from the model's
    backend block when a non-native backend is selected. The product layer above never changes."""
    runtime = runtimes["runtimes"].get(model["runtime"], {})
    kind = runtime.get("kind", "native")
    model["backend"] = {"id": model["runtime"], "kind": kind, "release": runtime.get("release"),
                        "telemetry": {"router": True, "ple": True, "mtp": True, "requests": True, "memory_plan": True},
                        "speculative": "native MTP head", "extra_assets": {}}
    if kind == "native":
        return
    block = model.get("backends", {}).get(model["runtime"])
    if block is None:
        raise CatalogError(f"{model['id']}: no backend block for {model['runtime']}")
    model["memory"] = {**model["memory"], **block["memory"]}
    model["cache"] = {**model["cache"], **block["cache"]}
    model["backend"].update(telemetry=block["telemetry"], speculative=block["speculative"], extra_assets=block["assets"],
                            model_name=block["model_name"], status=block.get("status"))


@lru_cache(maxsize=1)
def load() -> dict:
    policy_file = _read(CONFIG / "runtime-policy.json")
    models = {}
    for path in sorted((CONFIG / "models").glob("*.json")):
        model = _read(path)
        if model.get("id") != path.stem:
            raise CatalogError(f"{path.name}: id must equal the file name")
        models[model["id"]] = _apply_policy(model, policy_file["model_defaults"])
    runtimes = _read(CONFIG / "runtimes.json")
    for model_id, runtime_id in _runtime_overrides().items():
        if model_id in models:
            models[model_id]["runtime"] = runtime_id
    for model in models.values():
        _apply_backend(model, runtimes)
    profile_file = _read(CONFIG / "profiles.json")
    models = dict(sorted(models.items(), key=lambda item: item[1].get("order", 99)))
    catalog = {"models": models, "profile_file": profile_file, "runtimes": runtimes, "policy": policy_file}
    _validate(catalog)
    return catalog


def _validate(catalog: dict) -> None:
    models = catalog["models"]
    if not models:
        raise CatalogError("No models in config/models")
    ports = [m["port"] for m in models.values()]
    if len(set(ports)) != len(ports):
        raise CatalogError("Every model needs its own endpoint port")
    if sum(bool(m.get("default")) for m in models.values()) != 1:
        raise CatalogError("Exactly one model must be the default")
    for model in models.values():
        if model["runtime"] not in catalog["runtimes"]["runtimes"]:
            raise CatalogError(f"{model['id']}: unknown runtime {model['runtime']}")
        if not 4096 <= model["context"] <= 262144 or not 0 < model["max_output"] < model["context"]:
            raise CatalogError(f"{model['id']}: invalid context/output")
        for effort in model["reasoning_efforts"]:
            if effort not in EFFORTS:
                raise CatalogError(f"{model['id']}: unknown reasoning effort {effort}")
        for role in ASSET_ROLES:
            if role not in model["assets"]:
                raise CatalogError(f"{model['id']}: missing asset {role}")
    seen = set()
    for row in catalog["profile_file"]["profiles"]:
        if row["id"] in seen:
            raise CatalogError(f"Duplicate profile {row['id']}")
        seen.add(row["id"])
        if set(row) - {"id", "model", "reasoning_effort", "policy"}:
            raise CatalogError(f"{row['id']}: a profile may only set model, reasoning_effort and policy")
        model = models.get(row["model"])
        if model is None:
            raise CatalogError(f"{row['id']}: unknown model {row['model']}")
        if row["reasoning_effort"] not in model["reasoning_efforts"]:
            raise CatalogError(f"{row['id']}: {model['id']} does not support effort {row['reasoning_effort']}")
        if row["id"] != f"{model['id']}-{row['reasoning_effort']}":
            raise CatalogError(f"{row['id']}: profile ids are <model>-<effort>")
    default = profile(catalog["profile_file"]["default"], catalog)
    if not models[default["model"]].get("default"):
        raise CatalogError("The default profile must belong to the default model")


def models() -> dict[str, dict]:
    return load()["models"]


def model(model_id: str) -> dict:
    try:
        return models()[model_id]
    except KeyError:
        raise CatalogError(f"Unknown model {model_id!r}") from None


def policy() -> dict:
    return load()["policy"]


def metal_working_set_gib() -> tuple[float, str]:
    """The GPU's recommendedMaxWorkingSetSize: measured on this machine when possible, else the policy's reference."""
    from dwarfstar.paths import metal_info

    info = metal_info()
    if info.get("recommended_max_working_set_bytes"):
        return info["recommended_max_working_set_bytes"] / 2**30, "measured on this machine"
    return float(policy()["memory"]["metal_working_set_reference_gib"]), "reference value (M1 Max, 64 GiB)"


def memory_plan(model_id: str) -> dict:
    """The one memory formula. Absolute numbers differ by model; the method does not."""
    memory = model(model_id)["memory"]
    reserve = float(policy()["memory"]["system_reserve_gib"])
    planned = memory["resident_model_gib"] + memory["kv_gib"] + memory["buffers_gib"] + memory["projector_gib"]
    metal, metal_source = metal_working_set_gib()
    return {
        "resident_model_gib": memory["resident_model_gib"],
        "kv_gib": memory["kv_gib"],
        "buffers_gib": memory["buffers_gib"],
        "projector_gib": memory["projector_gib"],
        "planned_gib": round(planned, 2),
        "system_reserve_gib": reserve,
        "required_gib": round(planned + reserve, 2),
        "metal_working_set_gib": round(metal, 2),
        "metal_working_set_source": metal_source,
        "metal_headroom_gib": round(metal - planned, 2),
        "prefill_chunk": model(model_id)["prefill_chunk"],
        "context": model(model_id)["context"],
    }


def runtime_pin(model_id: str) -> dict:
    runtimes = load()["runtimes"]
    pin = dict(runtimes["runtimes"][model(model_id)["runtime"]])
    pin["id"] = model(model_id)["runtime"]
    pin["source"] = runtimes["source"]
    pin["name"] = runtimes["name"]
    return pin


def label(model_id: str, effort: str) -> str:
    """The one user-facing name of a profile, used by the Workstation and the editor."""
    return f"{model(model_id)['display_name']} {effort.upper()}"


def profile(profile_id: str, catalog: dict | None = None) -> dict:
    catalog = catalog or load()
    for row in catalog["profile_file"]["profiles"]:
        if row["id"] == profile_id:
            owner = catalog["models"][row["model"]]
            effort = row["reasoning_effort"]
            return {
                **row,
                "label": f"{owner['display_name']} {effort.upper()}",
                "default": row["id"] == catalog["profile_file"]["default"],
                "state_namespace": owner.get("state_namespaces", {}).get(effort, row["id"]),
                "context": owner["context"],
                "max_output": owner["max_output"],
                "port": owner["port"],
            }
    raise CatalogError(f"Unknown profile {profile_id!r}")


def profiles(model_id: str | None = None) -> list[dict]:
    rows = [profile(row["id"]) for row in load()["profile_file"]["profiles"]]
    return [row for row in rows if model_id in (None, row["model"])]


def default_profile() -> dict:
    return profile(load()["profile_file"]["default"])


def asset_path(model_id: str, role: str) -> Path:
    return DATA / model(model_id)["assets"][role]["path"]


def asset_paths(model_id: str) -> dict[str, Path]:
    return {role: asset_path(model_id, role) for role in ASSET_ROLES}


def disk_bytes(model_id: str) -> int:
    return sum(asset["bytes"] for asset in model(model_id)["assets"].values())


def context_label(tokens: int) -> str:
    return f"{tokens // 1024}K"


def summary() -> dict:
    """Compact, path-free description for user interfaces."""
    rows = []
    for item in models().values():
        rows.append({
            "id": item["id"],
            "family": item["family"],
            "display_name": item["display_name"],
            "default": bool(item.get("default")),
            "context": item["context"],
            "context_label": context_label(item["context"]),
            "max_output": item["max_output"],
            "port": item["port"],
            "reasoning_efforts": item["reasoning_efforts"],
            "profiles": [p["id"] for p in profiles(item["id"])],
            "disk_bytes": disk_bytes(item["id"]),
            "memory": item["memory"],
            "memory_plan": memory_plan(item["id"]),
            "prefill_chunk": item["prefill_chunk"],
            "overrides": item["overrides"],
            "quantization": item["quantization"],
            "source": {k: item["source"][k] for k in ("repository", "revision", "derivation") if k in item["source"]},
            "capabilities": item["capabilities"],
            "cache": item["cache"],
            "backend": {k: item["backend"][k] for k in ("id", "kind", "release", "telemetry", "speculative")},
            "summary": item["summary"],
        })
    shared = policy()
    return {"default_profile": default_profile()["id"], "models": rows, "profiles": profiles(),
            "policy": {"version": shared["version"], "model_defaults": {k: v for k, v in shared["model_defaults"].items() if k not in ("agent",)},
                       "runtime": shared["runtime"], "lifecycle": shared["lifecycle"], "memory": shared["memory"]}}
