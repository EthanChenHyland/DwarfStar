"""Where things live. Five roots, never mixed; only ``CODE`` is tracked by Git.

=========  ==============================================  ==========================================================
Root       Default                                         Holds
=========  ==============================================  ==========================================================
CODE       the repository (``~/Developer/DwarfStar``)      source, tests, scripts, docs, catalog
ASSETS     ``~/AI/DwarfStar``                              Models/ (production weights), Runtimes/ (the Splash
                                                           build), Manifests/ (hashes, seal, evidence), Sources/
                                                           (build and provenance inputs), Archive/
DATA       ``~/Library/Application Support/DwarfStar``    user data: chats, settings, per-model state and
                                                           generated configuration, telemetry sessions, backups
CACHE      ``~/Library/Caches/DwarfStar``                  anything recreatable: build scratch, agent and browser
                                                           caches, the runtime's SSD KV cache
LOGS       ``~/Library/Logs/DwarfStar``                    service, lifecycle and model-server logs (rotated)
=========  ==============================================  ==========================================================

Each root is resolved from, in order: its environment variable (``DWARFSTAR_ASSETS``, ``DWARFSTAR_DATA``,
``DWARFSTAR_CACHE``, ``DWARFSTAR_LOGS``), its key in ``config/local.json`` (``assets_root``, ``data_root``,
``cache_root``, ``logs_root``; machine-local, never in Git), then the default. Catalog paths are relative to their
root, so moving the model set means changing one setting.

Every running process serves one model. The model id is taken from the
``DWARFSTAR_MODEL`` environment variable, which the command-line entry point
sets before any model-bound module is imported.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[2]
SRC = CODE / "src"
LOCAL_CONFIG = CODE / "config" / "local.json"


def local_config() -> dict:
    """Machine-specific overrides. Missing file means defaults everywhere."""
    try:
        value = json.loads(LOCAL_CONFIG.read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{LOCAL_CONFIG} must contain a JSON object")
    return value


def _root(variable: str, setting: str, default: Path) -> Path:
    explicit = os.environ.get(variable) or local_config().get(setting)
    return Path(explicit).expanduser() if explicit else default


def data_root() -> Path:
    return _root("DWARFSTAR_DATA", "data_root", Path.home() / "Library/Application Support/DwarfStar")


ASSETS = _root("DWARFSTAR_ASSETS", "assets_root", Path.home() / "AI/DwarfStar")
DATA = data_root()
CACHE = _root("DWARFSTAR_CACHE", "cache_root", Path.home() / "Library/Caches/DwarfStar")
LOGS = _root("DWARFSTAR_LOGS", "logs_root", Path.home() / "Library/Logs/DwarfStar")

# ASSETS
MODELS = ASSETS / "Models"
RUNTIMES = ASSETS / "Runtimes"
MANIFESTS = ASSETS / "Manifests"
SOURCES = ASSETS / "Sources"
ARCHIVE = ASSETS / "Archive"
EVIDENCE = MANIFESTS / "evidence"
SEAL_DIRECTORY = MANIFESTS / "seal"
# DATA
INSTANCES = DATA / "instances"
SHARED_CONFIG = DATA / "config"
TELEMETRY = DATA / "telemetry"
CHATS = DATA / "chats"
STATE = DATA / "state"
BACKUPS = DATA / "backups"
# CACHE
BUILD = CACHE / "build"


def roots() -> dict[str, Path]:
    """Every root, for diagnostics and the storage report."""
    return {"code": CODE, "assets": ASSETS, "data": DATA, "cache": CACHE, "logs": LOGS}


def model_id() -> str:
    value = os.environ.get("DWARFSTAR_MODEL")
    if not value:
        raise RuntimeError("DWARFSTAR_MODEL is not set; run through the `dwarfstar` command")
    if not value.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"Invalid model id {value!r}")
    return value


def instance(model: str | None = None) -> Path:
    """Runtime root for one model: generated config, state, cache, scratch."""
    return INSTANCES / (model or model_id())


def _tool(name: str, setting: str, candidates: list[Path]) -> Path:
    configured = local_config().get(setting)
    if configured:
        return Path(configured).expanduser()
    for candidate in candidates:
        if candidate.exists():
            return candidate
    found = shutil.which(name)
    return Path(found) if found else candidates[0]


def bun() -> Path:
    return _tool("bun", "bun", [Path.home() / ".bun/bin/bun"])


def omp() -> Path:
    """The Oh My Pi agent CLI that provides the ACP agent."""
    return _tool("omp", "omp", [Path.home() / ".bun/bin/omp"])


def omp_packages() -> Path:
    configured = local_config().get("omp_packages")
    default = Path.home() / ".bun/install/global/node_modules/@oh-my-pi"
    return Path(configured).expanduser() if configured else default


def node() -> Path:
    return _tool("node", "node", [Path("/opt/homebrew/bin/node"), Path("/usr/local/bin/node")])


def zed_cli() -> Path:
    return _tool("zed", "zed_cli", [Path("/Applications/Zed.app/Contents/MacOS/cli")])


def python() -> str:
    return sys.executable


def extra_tool_paths() -> list[str]:
    """Optional directories appended to the agent's PATH (site-specific helper tools)."""
    return [str(Path(p).expanduser()) for p in local_config().get("extra_tool_paths", [])]


def extra_read_paths() -> list[Path]:
    """Optional directories the Safe Auto sandbox may read."""
    return [Path(p).expanduser() for p in local_config().get("extra_read_paths", [])]


def admission_lock() -> Path:
    """One exclusive lock shared by every heavyweight local runtime on this machine."""
    configured = local_config().get("admission_lock")
    if configured:
        return Path(configured).expanduser()
    path = STATE / "admission.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    return path


def metal_info() -> dict:
    """Metal device facts from the Workstation app (`--metal-info`), cached per boot in the data root."""
    import json as _json
    import subprocess as _subprocess

    cache = CACHE / "metal.json"
    try:
        boot = _subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, _subprocess.SubprocessError):
        boot = ""
    try:
        cached = _json.loads(cache.read_text())
        if cached.get("boot") == boot:
            return cached
    except (OSError, ValueError):
        pass
    binary = workstation_binary()
    if not binary.is_file():
        return {}
    try:
        value = _json.loads(_subprocess.run([str(binary), "--metal-info"], capture_output=True, text=True, timeout=20).stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError, _subprocess.SubprocessError):
        return {}
    if value.get("recommended_max_working_set_bytes"):
        value["boot"] = boot
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(_json.dumps(value) + "\n")
    return value


def workstation_binary() -> Path:
    configured = local_config().get("workstation_binary")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Applications/DwarfStar Workstation.app/Contents/MacOS/DwarfStarWorkstation"


def scratch_root() -> Path:
    configured = local_config().get("scratch_root")
    return Path(configured).expanduser() if configured else CACHE / "scratch"


def child_environment() -> dict[str, str]:
    """Variables every child process needs to find the same code, data and model."""
    env = {"DWARFSTAR_HOME": str(CODE), "DWARFSTAR_ASSETS": str(ASSETS), "DWARFSTAR_DATA": str(DATA),
           "DWARFSTAR_CACHE": str(CACHE), "DWARFSTAR_LOGS": str(LOGS), "DWARFSTAR_PYTHON": sys.executable}
    if os.environ.get("DWARFSTAR_MODEL"):
        env["DWARFSTAR_MODEL"] = os.environ["DWARFSTAR_MODEL"]
        env["DWARFSTAR_INSTANCE"] = str(instance())
    existing = os.environ.get("PYTHONPATH")
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + existing if existing else "")
    return env
