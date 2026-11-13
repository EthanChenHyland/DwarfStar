"""Owner of one model's native server.

One controller implementation serves every model. The model is fixed for the
life of the process (``DWARFSTAR_MODEL``). The controller only ever signals
the exact process it started and recorded: it never kills by name or port and
never force-kills.

Starting a server is refused unless all of the following hold:

* no other heavyweight model runtime is running and the known ports are free;
* the pinned server binary and its shader sources match the recorded hashes;
* the model files are the verified files recorded in ``assets.json``;
* the generated configuration matches the catalog;
* enough memory is available for this model at its configured context.
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from dwarfstar import catalog, instance, paths
from dwarfstar.telemetry import emit

MODEL = paths.model_id()
ROOT = paths.instance(MODEL)
STATE = ROOT / "state"
STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
CONFIG = ROOT / "runtime.json"
KIND = catalog.runtime_pin(MODEL).get("kind", "native")
RUNTIME_DIR = instance.runtime_directory(MODEL)
# The long-lived server process: ds4-server for the native runtime. Splash's launcher prepares the model, then hands
# off to `python3 -u -P -m server.server` (re-parented to launchd), which supervises the engine and is the only
# Splash process that lives as long as the model; the controller owns that one.
SERVER = RUNTIME_DIR / ("ds4-server" if KIND == "native" else "python/bin/python3")


def is_server_command(command: str) -> bool:
    return command.startswith(str(SERVER) + " ") and (KIND == "native" or " -m server.server" in command)

# Ports of other local runtimes that may exist on the same machine. A listener
# on any of them, or on any catalog port, blocks a start.
FOREIGN_PORTS = (8000, 8010, 8020, 8021)
STARTUP_TIMEOUT_S = catalog.policy()["lifecycle"]["startup_timeout_s"]
SHUTDOWN_TIMEOUT_S = catalog.policy()["lifecycle"]["stop_timeout_s"]
GIB = 1 << 30


def config() -> dict:
    return json.loads(CONFIG.read_text())


def run(args, **kw):
    return subprocess.run(args, text=True, capture_output=True, timeout=kw.pop("timeout", 5), **kw)


def identity(pid):
    """Process identity that survives PID reuse: birth time plus a hash of the command line."""
    if not isinstance(pid, int) or pid < 2:
        return None
    result = run(["/bin/ps", "-p", str(pid), "-o", "stat=,lstart=,command="])
    fields = result.stdout.strip().split(None, 6)
    if result.returncode or len(fields) != 7 or fields[0].startswith("Z"):
        return None
    return {
        "pid": pid,
        "birth": " ".join(fields[1:6]),
        "command": fields[6],
        "argv_sha256": hashlib.sha256(fields[6].encode()).hexdigest(),
    }


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def save(path, value) -> None:
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def retire_ledger(row: dict, reason: str) -> None:
    """A recorded server whose process has exited no longer owns anything: keep it as last-stopped, drop the ledger."""
    try:
        save(STATE / "last-stopped.json", {**row, "stopped_at": row.get("stopped_at") or time.time(), "retired": reason})
        (STATE / "server.json").unlink(missing_ok=True)
    except OSError:
        pass


def owned():
    """The recorded server, if it is still the same process. Raises if the PID was reused."""
    row = read(STATE / "server.json")
    current = identity(row.get("pid")) if row else None
    if not current:
        if row:
            retire_ledger(row, reason="process gone")
        return None
    if any(row.get(key) != current[key] for key in ("pid", "birth", "argv_sha256")):
        raise RuntimeError("DwarfStar PID identity changed; no process will be signalled.")
    if not is_server_command(current["command"]):
        raise RuntimeError("Ledger is not the DwarfStar server.")
    return {**row, **current}


def health() -> dict:
    try:
        c = config()
        with urllib.request.urlopen(c["endpoint"] + "/models", timeout=1) as stream:
            value = json.load(stream)
        item = next(v for v in value["data"] if v["id"] == c["model_id"])
        ok = item["context_length"] == c["context"] and (
            KIND != "native" or item["top_provider"]["max_completion_tokens"] == c["max_output"])
        return {"ok": ok, "model": item}
    except Exception:
        return {"ok": False}


def occupied(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.15)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def heavyweight_processes() -> list[tuple[int, str]]:
    found = []
    for line in run(["/bin/ps", "-axo", "pid=,command="]).stdout.splitlines():
        words = line.strip().split(None, 1)
        if len(words) != 2:
            continue
        pid, command = int(words[0]), words[1]
        if pid == os.getpid():
            continue
        name = Path(command.split(None, 1)[0]).name
        if (
            name in ("ds4-server", "llama-server")
            or (name == "splash" and "/engine/splash" in command)
            or (name == "moespresso" and " serve " in command)
            or (name.startswith("python") and " -m mtplx.server.openai " in command)
        ):
            found.append((pid, name))
    return found


def exclude_other_models() -> None:
    for pid, _name in heavyweight_processes():
        raise RuntimeError(
            f"Another heavyweight runtime is running (PID {pid}). Stop it first; DwarfStar changed nothing."
        )
    ports = sorted({*FOREIGN_PORTS, *(m["port"] for m in catalog.models().values())})
    for port in ports:
        if occupied(port):
            raise RuntimeError(f"Local port {port} is occupied. No process was stopped.")


# ------------------------------------------------------------------ admission


def available_memory_gib() -> float:
    """Memory the kernel considers available, the same figure Activity Monitor's pressure gauge uses."""
    total = int(run(["/usr/sbin/sysctl", "-n", "hw.memsize"]).stdout)
    level = int(run(["/usr/sbin/sysctl", "-n", "kern.memorystatus_level"]).stdout)
    return total * level / 100 / GIB


def memory_requirement(model_id: str | None = None) -> dict:
    """What a model needs before it may start, by the shared formula in config/runtime-policy.json."""
    model_id = model_id or MODEL
    model = catalog.model(model_id)
    plan = catalog.memory_plan(model_id)
    return {
        "model": model_id,
        "display_name": model["display_name"],
        "context": model["context"],
        **plan,
        "context_gib": round(plan["kv_gib"] + plan["buffers_gib"], 2),
    }


def admit_memory() -> dict:
    need = memory_requirement(MODEL)
    if need["metal_headroom_gib"] < 0:
        raise RuntimeError(
            f"{catalog.model(MODEL)['short_name']}'s plan ({need['planned_gib']:.2f} GiB) exceeds this GPU's Metal working set "
            f"({need['metal_working_set_gib']:.2f} GiB); it would fail at depth. Lower its prefill chunk or context. Nothing was started."
        )
    available = available_memory_gib()
    need["available_gib"] = round(available, 1)
    need["ok"] = available >= need["required_gib"]
    if not need["ok"]:
        short = catalog.model(MODEL)["short_name"]
        raise RuntimeError(
            f"{short} requires approximately {need['required_gib']:.1f} GiB available for the configured "
            f"{catalog.context_label(need['context'])} context; {available:.1f} GiB is currently available. "
            "Close other applications or stop another model, then try again. Nothing was started."
        )
    return need


# ---------------------------------------------------------------------- start


def pinned() -> dict:
    """Refuse to load anything that is not exactly the reviewed installation."""
    problems = instance.check(MODEL)
    if problems:
        raise RuntimeError(
            "Generated configuration differs from the catalog (" + "; ".join(problems) + "). "
            f"Run `dwarfstar generate --model {MODEL}`. Nothing was started."
        )
    c = config()
    if KIND != "native":
        engine = RUNTIME_DIR / "engine"
        if hashlib.sha256((engine / "splash").read_bytes()).hexdigest() != c["server_sha256"] or any(
                hashlib.sha256((engine / name).read_bytes()).hexdigest() != digest for name, digest in c["shader_sha256"].items()):
            raise RuntimeError("Splash engine or shaders differ from the pinned release; reinstall it before starting.")
        missing = [f"{role}: {path}" for role, path in c["model_files"].items() if not Path(path).is_file()]
        if missing:
            raise RuntimeError("Model files are missing; nothing was started: " + "; ".join(missing))
        instance.assets_current(MODEL)
        return c
    if hashlib.sha256(SERVER.read_bytes()).hexdigest() != c["server_sha256"]:
        raise RuntimeError("DwarfStar binary changed; rebuild/review before starting.")
    metal = SERVER.parent / "metal"
    shaders = {p.name for p in metal.iterdir() if p.is_file()}
    if shaders != set(c["shader_sha256"]) or any(
        hashlib.sha256((metal / name).read_bytes()).hexdigest() != digest for name, digest in c["shader_sha256"].items()
    ):
        raise RuntimeError("Shader sources changed; review before starting.")
    missing = [f"{role}: {path}" for role, path in c["model_files"].items() if not Path(path).is_file()]
    if missing:
        raise RuntimeError("Model files are missing; nothing was started: " + "; ".join(missing))
    instance.assets_current(MODEL)
    return c


def serve_owned() -> None:
    """Child entry point: take the machine-wide admission lock, then become the server."""
    lock = open(paths.admission_lock(), "r")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("Another local runtime holds exclusive model admission. Stop it first; nothing was changed.")
    os.set_inheritable(lock.fileno(), True)
    exclude_other_models()
    c = pinned()
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("DS4_", "DSTAR_", "MOESPRESSO_", "MTPLX_", "LLAMA_ARG_")):
            del env[key]
    env.update(c["environment"])
    env.update(native_telemetry_environment())
    os.chdir(RUNTIME_DIR)
    os.execve(str(SERVER), c["argv"], env)


def native_telemetry_environment() -> dict[str, str]:
    """Variables understood only by the telemetry-capable runtime build; ignored by the reference builds."""
    from dwarfstar import telemetry

    if not telemetry.enabled():
        return {}
    return {
        "DS4_TELEMETRY_UDP": str(telemetry.event_port()),
        "DS4_TELEMETRY_CONTROL": str(int(os.environ.get("DWARFSTAR_TELEMETRY_CONTROL", telemetry.DEFAULT_CONTROL_PORT))),
        "DS4_TELEMETRY_MODEL": MODEL,
    }


def start() -> dict:
    row = owned()
    if row:
        if not health()["ok"]:
            raise RuntimeError("Existing owned DwarfStar is not healthy; no duplicate/restart was attempted.")
        return {"ok": True, "already_running": True, "pid": row["pid"]}
    exclude_other_models()
    c = pinned()
    admission = admit_memory()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    log = STATE / f"server-{stamp}.log"
    started_at = time.time()
    emit("runtime", phase="starting", admission=admission)
    command = [sys.executable, "-B", "-m", "dwarfstar", "runtime", "--model", MODEL, "_serve"]
    with log.open("w") as stream:
        child = subprocess.Popen(
            command, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
            env={**os.environ, **paths.child_environment()},
        )
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if child.poll() is not None:
            tail = log.read_text(errors="replace")[-2200:]
            emit("runtime", phase="start_failed")
            raise RuntimeError(f"DwarfStar startup exited {child.returncode}. Endpoint {c['endpoint']}.\n{tail}\nLog: {log}")
        current = identity(child.pid)
        if current and is_server_command(current["command"]):
            ledger = {**current, "log": str(log), "started_at": started_at, "model": MODEL}
            save(STATE / "server.json", ledger)
            if health()["ok"]:
                ledger["mtp_block_loaded"] = "ds4: MTP block " in native_log(ledger)
                ledger["load_seconds"] = time.time() - started_at
                save(STATE / "server.json", ledger)
                emit("runtime", phase="running", pid=child.pid, load_s=round(ledger["load_seconds"], 2),
                     memory_plan=memory_plan(ledger))
                return {
                    "ok": True, "pid": child.pid, "health": health(),
                    "mtp_block_loaded": ledger["mtp_block_loaded"], "load_seconds": ledger["load_seconds"],
                    "admission": admission,
                }
        time.sleep(0.5)
    raise RuntimeError(f"Owned startup remains pending: {log}. No duplicate or forced kill was attempted.")


def native_log(row) -> str:
    if not row or not row.get("log"):
        return ""
    try:
        path = Path(row["log"])
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 131072))
            return stream.read().decode(errors="replace")
    except OSError:
        return ""


def memory_plan(row) -> dict:
    """The runtime's own memory plan, parsed from the first lines of its log."""
    import re

    if not row:
        return {}
    if KIND != "native":
        return splash_memory_plan()
    if not row.get("log"):
        return {}
    try:
        with Path(row["log"]).open("rb") as stream:
            head = stream.read(16384).decode(errors="replace")
    except OSError:
        return {}
    head = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", head)
    plan = {}
    match = re.search(
        r"memory: KV ([\d.]+) GiB \(raw ([\d.]+) \+ compressed ([\d.]+)\) \+ buffers ([\d.]+) GiB "
        r"\+ resident model ([\d.]+) GiB = ([\d.]+) GiB planned", head)
    if match:
        keys = ("kv_gib", "kv_raw_gib", "kv_indexer_gib", "buffers_gib", "resident_model_gib", "planned_gib")
        plan.update({key: float(value) for key, value in zip(keys, match.groups())})
    match = re.search(r"kv_raw_f32=(on|off)", head)
    if match:
        plan["kv_cache_dtype"] = "F32" if match[1] == "on" else "F16"
    match = re.search(r"n-grams: ([\d.]+) GiB, disk reads only", head)
    if match:
        plan["ple_disk_gib"] = float(match[1])
    match = re.search(r"MTP block .* \((\d+) tensors, ([\d.]+) GiB\)", head)
    if match:
        plan["mtp_gib"] = float(match[2])
    return plan


def splash_memory_plan() -> dict:
    """Splash reports its own plan on /status; mapped to the same fields as the native log line."""
    try:
        with urllib.request.urlopen(config()["endpoint"].removesuffix("/v1") + "/status", timeout=2) as stream:
            plan = json.load(stream)["memory_plan"]
    except Exception:
        return {}
    memory, budget = plan["model"]["memory"], plan["budget"]
    buffers = sum(memory.get(k, 0) for k in ("lane_state_bytes", "shared_prefill_bytes", "shared_decode_bytes", "pipeline_reserve_bytes",
                                            "runtime_overhead_reserve_bytes", "streamed_table_bytes", "state_staging_bytes"))
    kv = plan["model"]["kv_page_bytes"] / plan["model"]["kv_page_tokens"] * config()["context"]
    resident = memory["target_weights_bytes"] + memory["draft_weights_bytes"]
    return {"kv_gib": round(kv / GIB, 2), "buffers_gib": round(buffers / GIB, 2), "resident_model_gib": round(resident / GIB, 2),
            "projector_gib": round(memory["vision_weights_bytes"] / GIB, 2),
            "planned_gib": round((kv + buffers + resident + memory["vision_weights_bytes"]) / GIB, 2),
            "kv_cache_dtype": plan["model"]["kv_format"].upper(), "backend_budget_gib": round(budget["hard_budget_bytes"] / GIB, 2)}


def requests(row) -> list:
    if not row:
        return []
    values = []
    basic = read(STATE / "request.json", {})
    if basic.get("server_pid") == row["pid"] and basic.get("server_birth") == row["birth"]:
        values.append(basic)
    clients = STATE / "clients"
    for path in clients.glob("request-*.json") if clients.is_dir() else ():
        if path.is_symlink() or path.stat().st_size > 16384:
            continue
        value = read(path, {})
        if value.get("server_pid") != row["pid"] or value.get("server_birth") != row["birth"]:
            continue
        client = identity(value.get("client_pid"))
        if not client or client["birth"] != value.get("client_birth"):
            continue
        values.append(value)
    return values


def status() -> dict:
    from dwarfstar.approval_policy import status as permission_status
    from dwarfstar.native_metrics import assemble, native_snapshot, process_resources

    c = config()
    errors = []
    try:
        row = owned()
    except Exception as error:
        row = None
        errors.append("Process identity collection error: " + type(error).__name__)
    h = health() if row else {"ok": False}
    if row and not h["ok"]:
        errors.append("Health collection error: model endpoint unavailable or configuration mismatch")
    native, log_errors = native_snapshot(row or read(STATE / "last-stopped.json"), STATE / "telemetry-log-cursor.json")
    errors += log_errors
    try:
        clients = requests(row)
    except Exception as error:
        clients = []
        errors.append("ACP metadata collection error: " + type(error).__name__)
    resource, resource_errors = process_resources(row["pid"] if row else None, ROOT, STATE / "telemetry-cache-size.json")
    errors += resource_errors
    try:
        request, phase = assemble(c, row, h, native, clients, resource)
    except Exception as error:
        request, phase = {}, "IDLE"
        errors.append("Request aggregation collection error: " + type(error).__name__)
    if native.get("log_catching_up"):
        errors.append("Native log catch-up pending; bounded incremental read")
    if request.get("telemetry_error"):
        errors.append("ACP telemetry collection error")
    if request.get("active") and time.time() - request.get("observed_at", 0) > 8:
        errors.append("ACP metadata collection error: stale receipt; no model stall or completion inferred")
    last_native = {k: v for k, v in native.items() if k.startswith("mtp_")} if not row else {}
    if row and row.get("mtp_block_loaded"):
        mtp_state = "MTP enabled · native block loaded"
    elif row:
        mtp_state = "MTP configured · native adaptive"
    else:
        mtp_state = "MTP configured · runtime offline"
    tooling = read(paths.CODE / "agent-tools/state/capabilities.json", {})
    last = read(STATE / "last-stopped.json", {}) or {}
    return {
        "ok": True,
        "model_key": MODEL,
        "permissions": permission_status(),
        "running": bool(row),
        "healthy": h["ok"],
        "pid": row["pid"] if row else None,
        "uptime_s": resource.get("process_uptime_s"),
        "endpoint": c["endpoint"],
        "configuration": c,
        "native": native if row else {},
        "last_session_native": last_native,
        "mtp_state": mtp_state,
        "request": request,
        "phase": phase,
        "resources": resource,
        "memory_plan": memory_plan(row),
        "load_seconds": (row or last).get("load_seconds"),
        "collection_errors": errors,
        "tooling": tooling,
        "log": row["log"] if row else last.get("log"),
    }


def stop() -> dict:
    row = owned()
    if not row:
        return {"ok": True, "already_offline": True}
    if any(r.get("active") or r.get("tool_execution_active") for r in requests(row)):
        raise RuntimeError("DwarfStar request/tool is active; finish/cancel it first. Nothing stopped.")
    port = config()["port"]
    connections = run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(row["pid"]), f"-iTCP:{port}", "-sTCP:ESTABLISHED"])
    if connections.returncode not in (0, 1):
        raise RuntimeError("Cannot verify client connections; refusing stop.")
    if connections.returncode == 0 and connections.stdout.strip():
        raise RuntimeError("A client is connected. Finish the turn before stopping.")
    owned()
    emit("runtime", phase="stopping", pid=row["pid"])
    os.kill(row["pid"], signal.SIGTERM)
    deadline = time.monotonic() + SHUTDOWN_TIMEOUT_S
    while time.monotonic() < deadline and identity(row["pid"]):
        time.sleep(0.2)
    if identity(row["pid"]) or occupied(port):
        raise RuntimeError("Shutdown pending; no forced kill attempted.")
    # A concurrent status call may already have retired the ledger once the process was gone; that is the same
    # outcome, not a failed stop.
    save(STATE / "last-stopped.json", {**row, "stopped_at": time.time()})
    (STATE / "server.json").unlink(missing_ok=True)
    emit("runtime", phase="stopped")
    return {"ok": True, "stopped": row["pid"]}


def reconcile() -> dict:
    """Make the saved ownership record agree with the processes that actually exist. Never signals a process.

    * A ledger whose process has exited is retired (kept as last-stopped).
    * A ledger whose PID now belongs to a different process (PID reuse) is retired as invalid.
    * A running ds4-server whose command line is exactly this model's generated command, with no valid
      ledger (for example after the controller or service crashed during a start), is adopted: it is
      healthy-checked and recorded, so the Workstation reconnects instead of starting a duplicate.
    * Any other ds4-server is reported as foreign and left alone.
    """
    c = config()
    report = {"ok": True, "model": MODEL, "ledger": "none", "adopted": None, "foreign": []}
    row = read(STATE / "server.json")
    if row:
        current = identity(row.get("pid"))
        if current is None:
            retire_ledger(row, reason="process gone at reconcile")
            report["ledger"] = "retired: process gone"
        elif any(row.get(key) != current[key] for key in ("pid", "birth", "argv_sha256")) or \
                not is_server_command(current["command"]):
            retire_ledger(row, reason="identity mismatch at reconcile (PID reused)")
            report["ledger"] = "retired: PID now belongs to another process"
        else:
            report["ledger"] = "valid"
            return report
    expected = hashlib.sha256(" ".join(c["argv"]).encode()).hexdigest()
    for pid, name in heavyweight_processes():
        current = identity(pid)
        if not current or name != "ds4-server":
            continue
        if current["argv_sha256"] == expected and health()["ok"]:
            logs = sorted(STATE.glob("server-*.log"), key=lambda path: path.stat().st_mtime)
            save(STATE / "server.json", {**current, "log": str(logs[-1]) if logs else None, "started_at": None, "model": MODEL,
                                         "adopted_at": time.time()})
            emit("runtime", phase="running", pid=pid, adopted=True)
            report.update(ledger="adopted", adopted=pid)
        elif KIND == "native" and current["argv_sha256"] != expected:
            report["foreign"].append({"pid": pid, "command": current["command"][:160]})
    return report


def launch_workspace(profile_id: str, folder: str | None = None) -> dict:
    """Start the model if needed and open a workspace in the editor for the selected profile."""
    from dwarfstar.preflight import require
    from dwarfstar.profiles import profile

    selected = profile(profile_id)
    start()
    scratch = Path(config()["scratch_directory"])
    if folder:
        root = Path(folder).expanduser().resolve()
        assert root.is_dir()
    else:
        root = scratch / (datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        root.mkdir(parents=True)
    require(root, profile_id)
    subprocess.Popen([str(paths.zed_cli()), "-n", str(root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    name = selected["name"]
    return {
        "ok": True, "workspace": str(root), "profile": name, "policy": selected["policy"],
        "instruction": "Agent Panel → top-right + → External Agents → " + name,
    }


ACTIONS = ("start", "stop", "restart", "status", "health", "logs", "chat", "history", "requirement", "reconcile", "_serve")


def main(argv: list[str] | None = None) -> None:
    from dwarfstar.profiles import registry

    parser = argparse.ArgumentParser(prog="dwarfstar runtime")
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("--profile", "--kind", dest="profile", choices=list(registry()["profiles"]), default=registry()["default"])
    parser.add_argument("--folder")
    parser.add_argument("--history-id")
    args = parser.parse_args(argv)
    if args.action == "_serve":
        serve_owned()
        return
    if args.action == "history":
        from dwarfstar.history_compat import list_history, read_history

        value = read_history(args.history_id) if args.history_id else list_history()
    elif args.action == "reconcile":
        value = reconcile()
    elif args.action == "requirement":
        value = {"ok": True, **memory_requirement(), "available_gib": round(available_memory_gib(), 1)}
    elif args.action in ("status", "health", "logs"):
        value = status() if args.action == "status" else health() if args.action == "health" else {"ok": True, "log": status()["log"]}
    else:
        with (STATE / "control.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.action == "restart":
                stop()
                value = start()
            elif args.action == "start":
                value = start()
            elif args.action == "stop":
                value = stop()
            else:
                value = launch_workspace(args.profile, args.folder)
    print(json.dumps(value), flush=True)


def cli(argv: list[str] | None = None) -> None:
    try:
        main(argv)
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}), flush=True)
        sys.exit(1)
