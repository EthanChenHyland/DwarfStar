"""Passive status of a running model server: process and machine resources, and the request the agent reports.

Nothing here sends inference. Request facts come from the agent's own receipts (one small JSON file per ACP client in
the model's state directory, written by acp/telemetry.ts); the runtime's memory plan comes from its /status endpoint
(runtime_control.memory_plan)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path


def atomic(path, value) -> None:
    path = Path(path)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value) + "\n")
    temp.chmod(0o600)
    temp.replace(path)


def command(args) -> str:
    result = subprocess.run(args, text=True, capture_output=True, timeout=2)
    if result.returncode:
        raise RuntimeError("OS query failed")
    return result.stdout


def process_resources(pid, cache_directory: Path, cache_path: Path) -> tuple[dict, list[str]]:
    """Swap, available memory, free disk, the runtime's SSD cache size (sampled at most every 30 s) and, with a pid,
    the server process's RSS, CPU and uptime. Each collector fails on its own and is reported, never guessed."""
    values: dict = {}
    errors: list[str] = []

    def collect(label, fn):
        try:
            values.update(fn())
        except Exception as error:
            errors.append(label + " collection error: " + type(error).__name__)

    def swap():
        m = re.search(r"used = ([\d.]+)([MG])", command(["/usr/sbin/sysctl", "vm.swapusage"]))
        if not m:
            raise ValueError("swap format")
        return {"swap_bytes": float(m[1]) * (1024**2 if m[2] == "M" else 1024**3)}

    def memory():
        text = command(["/usr/bin/vm_stat"])
        page = int(re.search(r"page size of (\d+)", text)[1])
        count = lambda name: int(re.search(name + r":\s*(\d+)", text)[1])  # noqa: E731
        free, inactive, speculative = count("Pages free"), count("Pages inactive"), count("Pages speculative")
        return {"free_memory_bytes": free * page, "available_memory_approx_bytes": (free + inactive + speculative) * page}

    def process():
        parts = command(["/bin/ps", "-p", str(pid), "-o", "rss=,pcpu=,etime="]).split()
        if len(parts) != 3:
            raise ValueError("process format")
        elapsed = parts[2]
        days, elapsed = elapsed.split("-", 1) if "-" in elapsed else ("0", elapsed)
        digits = list(map(int, elapsed.split(":")))
        duration = int(days) * 86400 + sum(x * 60**i for i, x in enumerate(reversed(digits)))
        return {"rss_bytes": int(parts[0]) * 1024, "cpu_percent": float(parts[1]), "process_uptime_s": duration}

    def disk_cache():
        try:
            cache = json.loads(Path(cache_path).read_text())
        except (OSError, ValueError):
            cache = {}
        if time.time() - cache.get("measured_at", 0) > 30:
            files = Path(cache_directory).rglob("*") if Path(cache_directory).is_dir() else ()
            cache = {"measured_at": time.time(), "cache_disk_bytes": sum(p.stat().st_blocks * 512 for p in files if p.is_file())}
            atomic(cache_path, cache)
        return {"cache_disk_bytes": cache["cache_disk_bytes"]}

    collect("Swap", swap)
    collect("Memory", memory)
    collect("Disk", lambda: {"free_disk_bytes": shutil.disk_usage(Path.home()).free})
    collect("Cache", disk_cache)
    if pid:
        collect("Process", process)
    return values, errors


def assemble(config: dict, row: dict | None, clients: list[dict], now: float | None = None) -> tuple[dict, str]:
    """The request to show: the active client's receipt, else the most recent one. Receipts from several clients are
    never blended."""
    now = time.time() if now is None else now
    if not row or not clients:
        return {}, "IDLE"
    selected = max(clients, key=lambda x: (bool(x.get("active") or x.get("tool_execution_active")), x.get("started_at", 0)))
    request = dict(selected)
    if request.get("active") and request.get("started_at") and not isinstance(request.get("elapsed_s"), (int, float)):
        request["elapsed_s"] = max(0, now - request["started_at"])
    if request.get("input_tokens") is not None:
        requested = request.get("requested_output", config["max_output"])
        if requested is not None:
            request["effective_output"] = min(requested, max(0, config["context"] - request["input_tokens"]))
    if not request.get("active") and request.get("outcome") in ("error", "aborted", "shutdown"):
        request["phase"] = "ERROR"
    if request.get("tool_execution_active"):
        request["phase"] = "TOOL_CALL"
    request["active_client_count"] = sum(bool(x.get("active")) for x in clients)
    return request, request.get("phase", "IDLE")


def splash_live(status: dict) -> dict:
    """What Splash measured itself, from one read of /status (about 2 ms, 17 KB). Rates are Splash's cumulative values
    since the server started; nothing is estimated here, and a missing field stays missing."""
    if not isinstance(status, dict) or not status.get("ready"):
        return {}
    metrics, cache = status.get("metrics") or {}, status.get("cache") or {}
    pick = lambda d, *keys: {k: d[k] for k in keys if isinstance(d, dict) and k in d}  # noqa: E731
    return {
        "source": f"splash /status schema {status.get('schema_version')}",
        "requests": pick(status.get("requests") or {}, "submitted", "completed", "cancelled", "failed"),
        "active_requests": len(status.get("active_requests") or []),
        "ttft_ms": pick(metrics.get("ttft_ms") or {}, "p50", "p95", "samples"),
        "inter_token_ms": pick(metrics.get("itl_ms") or {}, "p50", "p95", "samples"),
        "since_start": pick(metrics, "prefill_input_tokens", "prefill_tokens_per_second", "decode_output_tokens",
                            "decode_tokens_per_second", "drafted_tokens", "accepted_draft_tokens", "draft_acceptance_rate"),
        "prefix_cache": pick(cache, "hits", "cold_misses", "hit_rate", "reused_tokens", "kv_hit_tokens"),
        "scheduler": pick(status.get("scheduler") or {}, "queued", "prefilling", "decoding"),
        "memory_bytes": pick(status.get("memory_actual") or {}, "current_bytes", "peak_bytes"),
        "memory_pressure": status.get("memory_pressure"),
        "thermal_state": status.get("thermal_state"),
    }
