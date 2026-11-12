"""Agent-facing view of the current model's profiles.

Reads the generated ``acp/profiles.json`` of the current model's runtime root,
so the Python launcher and the TypeScript agent extensions see the same data.
"""
from __future__ import annotations

import json

from dwarfstar.paths import instance

BASE_AGENT_CONFIG = (
    "memory:\n  backend: off\nautolearn:\n  enabled: false\ntools:\n  async: false\n"
    "task:\n  maxConcurrency: 1\n  maxRecursionDepth: 1\n"
)


def registry() -> dict:
    return json.loads((instance() / "acp/profiles.json").read_text())


def profile(profile_id: str) -> dict:
    value = registry()["profiles"][profile_id]
    return {**value, "id": profile_id}


def agent_config(profile_id: str) -> str:
    """Keep the agent's native defaults unless a profile explicitly overrides async compaction."""
    overrides = profile(profile_id).get("omp_settings", {})
    if not isinstance(overrides, dict) or set(overrides) - {"compaction.asyncEnabled"}:
        raise ValueError("Unsupported profile OMP setting")
    if not overrides:
        return BASE_AGENT_CONFIG
    enabled = overrides["compaction.asyncEnabled"]
    if not isinstance(enabled, bool):
        raise ValueError("Async compaction must be boolean")
    return BASE_AGENT_CONFIG + "compaction:\n  asyncEnabled: " + str(enabled).lower() + "\n"
