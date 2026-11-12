"""ACP agent launcher for one profile.

This is what an editor runs. It never loads a model: the model's server must
already be running and healthy (start it from the Workstation or with
``dwarfstar runtime --model <id> start``). It prepares a per-workspace agent
state directory, then replaces itself with the agent process.

The profile decides the model and the reasoning effort. Tools, permissions,
compaction, recovery and telemetry are the same for every profile.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from dwarfstar import paths, runtime_control, telemetry
from dwarfstar.approval_policy import mode as approval_mode
from dwarfstar.profiles import agent_config, profile, registry

CODE = paths.CODE
ROOT = paths.instance()
sys.path.insert(0, str(CODE / "agent-tools"))
from boundary import check_workspace, environment, tooling_policy  # noqa: E402

SERVED_MODEL = "dwarfstar-local/qwen3.8-flash-next"
AGENT_TOOLS = "read,write,edit,bash"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dwarfstar acp")
    parser.add_argument("--profile", "--kind", dest="profile", choices=list(registry()["profiles"]), required=True)
    parser.add_argument("--workspace", "--cwd")
    args = parser.parse_args(argv)
    workspace = Path(args.workspace or os.getcwd()).resolve()
    assert workspace.is_dir() and workspace not in [Path.home(), Path("/"), CODE, paths.DATA]
    check_workspace(workspace)

    selected = profile(args.profile)
    effort = selected["reasoning_effort"]
    namespace = selected.get("state_namespace", args.profile)
    selected_mode = approval_mode()

    permission_runtime = json.loads((paths.SHARED_CONFIG / "permission-runtime.json").read_text())
    cli_digest = hashlib.sha256(paths.omp().resolve().read_bytes()).hexdigest()
    assert cli_digest == permission_runtime["cli_sha256"], (
        "The agent CLI changed: verify the scoped ACP permission patch before enabling workspace-safe auto approval."
    )
    row = runtime_control.owned()
    assert row and runtime_control.health()["ok"], "Start the model in DwarfStar Workstation first; ACP never loads a second model."

    from dwarfstar.preflight import require

    require(workspace, args.profile)

    agent = workspace / (".omp-dwarfstar-" + namespace + "-agent")
    agent.mkdir(exist_ok=True)
    shutil.copy2(ROOT / selected["provider_config"], agent / "models.yml")
    (agent / "config.yml").write_text(agent_config(args.profile))
    metrics = ROOT / "state/clients" / ("request-" + str(os.getpid()) + ".json")
    metrics.parent.mkdir(mode=0o700, exist_ok=True)
    temp = workspace / (".dwarfstar-" + namespace + "-tmp")
    temp.mkdir(exist_ok=True)
    xdg = temp / "xdg"
    xdg.mkdir(exist_ok=True)

    search_path = [*paths.extra_tool_paths(), "/opt/homebrew/bin", "/usr/bin", "/bin"]
    env = {
        "HOME": str(Path.home()),
        "PATH": ":".join(search_path),
        "LANG": "en_US.UTF-8",
        "TMPDIR": str(temp),
        "XDG_DATA_HOME": str(xdg),
        "XDG_STATE_HOME": str(xdg),
        "XDG_CACHE_HOME": str(xdg),
        "PI_CONFIG_DIR": os.path.relpath(workspace / (".dwarfstar-" + namespace + "-root"), Path.home()),
        "PI_CODING_AGENT_DIR": str(agent),
        "V7_WORKSPACE": str(workspace),
        "V7_SEATBELT_ACTIVE": "1",
        "V7_ACI": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "BUN_RUNTIME_TRANSPILER_CACHE_PATH": str(temp / "bun-cache"),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "DWARFSTAR_REQUEST_FILE": str(metrics),
        "DWARFSTAR_CLIENT_BIRTH": runtime_control.identity(os.getpid())["birth"],
    }
    env.update(environment(workspace, temp))
    env.update(paths.child_environment())
    env["DWARFSTAR_PROFILE_ID"] = args.profile
    env["DWARFSTAR_TELEMETRY_PORT"] = str(telemetry.event_port())
    env["DWARFSTAR_TELEMETRY"] = "1" if telemetry.enabled() else "0"
    env["DWARFSTAR_APPROVAL_MODE"] = selected_mode
    env["DWARFSTAR_PERMISSION_POLICY"] = "full-auto-v1" if selected_mode == "full_auto" else "workspace-safe-v1"
    env["V7_SEATBELT_ACTIVE"] = "0" if selected_mode == "full_auto" else "1"
    env["DWARFSTAR_PERMISSION_JOURNAL"] = str(ROOT / "state/permissions" / ("client-" + str(os.getpid()) + ".jsonl"))
    latency = ROOT / "state/latency" / ("client-" + str(os.getpid()) + ".jsonl")
    latency.parent.mkdir(mode=0o700, exist_ok=True)
    env["DWARFSTAR_LATENCY_FILE"] = str(latency)

    extensions = [
        CODE / "agent-tools/extension.ts",
        *([CODE / "acp/workspace-guard.ts"] if selected_mode != "full_auto" else []),
        CODE / "acp/telemetry.ts",
        CODE / "acp/kimicu.ts",
    ]
    read_extra = [CODE / "acp", CODE / "prompts", CODE / "src", ROOT / "acp", ROOT / "state/server.json", ROOT / "runtime.json"]
    # Workstation-local scalar telemetry is the only write outside the selected workspace.

    cmd = [
        str(paths.bun()), "--no-env-file", str(paths.omp()), "acp",
        "--cwd", str(workspace),
        "--model", SERVED_MODEL,
        "--thinking", effort,
        "--no-title", "--no-rules", "--no-skills", "--no-extensions", "--no-lsp",
        "--tools", AGENT_TOOLS,
    ]
    for extension in extensions:
        cmd += ["-e", str(extension)]
    prompt_path = CODE / selected["prompt"]
    if selected["prompt_mode"] == "custom":
        cmd += ["--system-prompt", prompt_path.read_text().rstrip("\n")]
    else:
        cmd += ["--system-prompt-template", str(prompt_path)]
    cmd += ["--approval-mode", "always-ask" if selected_mode == "ask" else "yolo"]
    rules = tooling_policy(workspace, read_extra=read_extra)
    for path in [metrics, Path(str(metrics) + ".tmp"), latency]:
        rules += "(allow file-write* (literal " + json.dumps(str(path)) + "))\n"

    # Local version/path refresh only; connectivity probes are explicit, not per-turn.
    try:
        sandbox = ["/usr/bin/sandbox-exec", "-p", rules] if selected_mode != "full_auto" else []
        subprocess.run(
            [*sandbox, str(paths.node()), str(CODE / "agent-tools/bin/capabilities.mjs")],
            cwd=workspace, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass  # Tool inventory failure never blocks an ACP session.

    # Host-side admission/ownership only. No added tools, model messages or sandbox grants.
    from dwarfstar.task_infrastructure import admit

    key = os.environ.get("DWARFSTAR_TASK_ID") or ("chat-" + uuid.uuid4().hex)
    admit(workspace, key, args.profile, metrics, latency)
    env["DWARFSTAR_TASK_ID"] = key
    env["DWARFSTAR_STATE_ROOT"] = str(ROOT / "state/orchestration")
    for key in ("DWARFSTAR_WIRE_RECEIPT", "DWARFSTAR_WIRE_DUMP_DIR"):  # opt-in test aids, off by default
        if os.environ.get(key):
            env[key] = os.environ[key]
    telemetry.emit("agent", phase="session_start", profile=args.profile, effort=effort, approval_mode=selected_mode)
    if selected_mode == "full_auto":
        os.execve(cmd[0], cmd, env)
    else:
        os.execve("/usr/bin/sandbox-exec", ["sandbox-exec", "-p", rules, *cmd], env)
