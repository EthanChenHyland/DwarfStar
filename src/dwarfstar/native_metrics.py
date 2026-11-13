"""Passive scalar telemetry for the pinned DwarfStar build; never sends inference."""

from pathlib import Path
import datetime, json, os, re, shutil, subprocess, time

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
MAX_READ = 1024 * 1024


def atomic(path, value):
    path = Path(path)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value) + "\n")
    temp.chmod(0o600)
    temp.replace(path)


def stamp(line, reference):
    m = re.match(r"(\d{4}) (\d{2}:\d{2}:\d{2}) ", line)
    if not m:
        return None
    base = datetime.datetime.fromtimestamp(reference)
    candidates = []
    for year in (base.year - 1, base.year, base.year + 1):
        try:
            candidates.append(datetime.datetime.strptime(str(year) + m[1] + " " + m[2], "%Y%m%d %H:%M:%S").timestamp())
        except ValueError:
            pass
    return min(candidates, key=lambda t: abs(t - reference)) if candidates else None


class NativeMetrics:
    def __init__(self, value=None):
        self.value = dict(value or {})

    def feed(self, line, reference=None):
        line = ANSI.sub("", line)
        v = self.value
        # Only stable named native records. Never persist free-form output/errors/paths.
        m = re.search(r"ds4: Qwen3.8 mtp: (\d+) verify cycles, (\d+) drafts accepted \(([\d.]+)%\)", line)
        if m:
            cycles, accepted = int(m[1]), int(m[2])
            v.update(
                mtp_verify_cycles=cycles,
                mtp_accepted=accepted,
                mtp_native_percent=float(m[3]),
                mtp_ratio_percent=100 * accepted / cycles if cycles else None,
                mtp_scope="destroyed-session aggregate",
            )
            return
        if "ds4-server:" not in line:
            return
        m = re.search(r"vision embedding encoded; cache=(\d+) bytes", line)
        if m:
            v.update(
                vision_encoder_loaded=True, vision_embedding_cache_bytes=int(m[1]), vision_encode_count=v.get("vision_encode_count", 0) + 1
            )
            if reference:
                v["vision_last_encoded_at"] = stamp(line, reference)
            return
        if "vision embedding cache hit" in line:
            v.update(vision_encoder_loaded=True, vision_embedding_cache_hits=v.get("vision_embedding_cache_hits", 0) + 1)
            return
        m = re.search(r"kv cache hit text(?: RESPPROTO)? tokens=(\d+) text=\d+ quant=\d+ key=([\w-]+) load=([\d.]+) ms", line)
        if m:
            v["_pending_cache"] = {"checkpoint_source": "disk-text", "checkpoint_key": m[2], "checkpoint_restore_s": float(m[3]) / 1000}
            return
        m = re.search(r"\b(chat|completion) ctx=(\d+)\.\.(\d+):(\d+)(?: [A-Z_ ]+)? prompt start$", line)
        if m:
            aggregate = {k: x for k, x in v.items() if k.startswith(("mtp_", "vision_"))}
            cache = v.get("_pending_cache", {})
            self.value = v = {
                **aggregate,
                **cache,
                "request_sequence": v.get("request_sequence", 0) + 1,
                "phase": "PREFILL / WAITING_FIRST_TOKEN",
                "active": True,
                "reused_tokens": int(m[2]),
                "prompt_tokens": int(m[3]),
                "new_prefill_tokens": int(m[4]),
                "prefill_total": int(m[4]),
                "checkpoint_hit": int(m[2]) > 0,
                "generated_tokens": 0,
            }
            if reference:
                v["started_at"] = stamp(line, reference)
            return
        # Lines belonging to the current server request only; no prompt content is parsed.
        if not v.get("request_sequence"):
            return
        m = re.search(r"\bprefill chunk (\d+)/(\d+) \([\d.]+%\) chunk=([\d.]+) t/s avg=([\d.]+) t/s ([\d.]+)s$", line)
        if m:
            v.update(
                prefill_tokens=int(m[1]),
                prefill_total=int(m[2]),
                prefill_chunk_tps=float(m[3]),
                prefill_tps=float(m[4]),
                prefill_seconds=float(m[5]),
            )
        m = re.search(r"\bprompt done ([\d.]+)s$", line)
        if m:
            duration = float(m[1])
            v.update(prefill_seconds=duration, prefill_tokens=v.get("new_prefill_tokens"))
            if duration > 0:
                v["prefill_tps"] = v.get("new_prefill_tokens", 0) / duration
        m = re.search(r"gen=(\d+).* decoding chunk=([\d.]+) t/s avg=([\d.]+) t/s ([\d.]+)s$", line)
        if m:
            # Native flags are a fallback phase, not a streaming event or exact TTFT.
            v.update(
                active=True,
                phase="TOOL_CALL" if "DSML_START" in line else "REASONING" if "THINKING" in line else "OUTPUT",
                phase_source="native progress flags",
                generated_tokens=int(m[1]),
                decode_chunk_tps=float(m[2]),
                decode_tps=float(m[3]),
                decode_elapsed=float(m[4]),
            )
        m = re.search(r"gen=(\d+).* finish=(stop|length|tool_calls|error|cancelled)(?: .*?)? ([\d.]+)s$", line)
        if m:
            v.update(
                active=False,
                phase="ERROR" if m[2] in ("error", "cancelled") else "COMPLETE",
                generated_tokens=int(m[1]),
                finish_reason=m[2],
                request_seconds=float(m[3]),
            )
        if re.search(r"\b(chat|completion) ctx=.* (?:prefill failed|client disconnected|prefill SSE error failed)", line):
            v.update(active=False, phase="ERROR")


def parse_native(text):
    reducer = NativeMetrics()
    for line in text.splitlines():
        reducer.feed(line)
    return reducer.value


def native_snapshot(row, cache_path):
    """Bounded incremental reads. Cursor persists across short-lived status invocations."""
    if not row or not row.get("log"):
        return {}, []
    try:
        path = Path(row["log"])
        stat = path.stat()
        key = [row["pid"], row["birth"], str(path), stat.st_dev, stat.st_ino]
        try:
            cache = json.loads(Path(cache_path).read_text())
        except (OSError, ValueError):
            cache = {}
        if cache.get("key") != key or cache.get("offset", 0) > stat.st_size:
            cache = {"key": key, "offset": 0, "metrics": {}}
        with path.open("rb") as stream:
            stream.seek(cache["offset"])
            data = stream.read(MAX_READ)
            read_end = stream.tell()
        boundary = data.rfind(b"\n")
        reducer = NativeMetrics(cache.get("metrics"))
        if boundary >= 0:
            lines = data[:boundary].split(b"\n")
            if cache.pop("skip_line", False):
                lines = lines[1:]
            events = []
            journal_error = None
            for line in lines:
                if len(line) > 16384:
                    continue
                text = line.decode(errors="replace")
                previous_sequence = reducer.value.get("request_sequence")
                reducer.feed(text, stat.st_mtime)
                event = (
                    "native_request_start"
                    if reducer.value.get("request_sequence") != previous_sequence
                    else "native_prefill_complete"
                    if re.search(r"\bprompt done [\d.]+s$", text)
                    else "native_request_end"
                    if re.search(r"\bfinish=(stop|length|tool_calls|error|cancelled)\b", text)
                    else None
                )
                if event:
                    safe_keys = [
                        "request_sequence",
                        "started_at",
                        "reused_tokens",
                        "prompt_tokens",
                        "new_prefill_tokens",
                        "prefill_seconds",
                        "generated_tokens",
                        "request_seconds",
                        "finish_reason",
                        "checkpoint_hit",
                        "checkpoint_source",
                        "checkpoint_restore_s",
                    ]
                    events.append(
                        {
                            "schema": 1,
                            "event_id": str(row["pid"])
                            + ":"
                            + str(stat.st_ino)
                            + ":"
                            + str(reducer.value.get("request_sequence"))
                            + ":"
                            + event,
                            "event": event,
                            "server_pid": row["pid"],
                            "server_birth": row["birth"],
                            "observed_at": time.time(),
                            "native_timestamp": stamp(ANSI.sub("", text), stat.st_mtime),
                            "timestamp_precision": "native log: one second",
                            "source": "exact runtime scalar fields; timestamps limited by log precision",
                            "session_id": None,
                            "client_request_id": None,
                            **{k: reducer.value[k] for k in safe_keys if k in reducer.value},
                        }
                    )
            # Reuse the existing bounded cursor/status collector. No second monitor.
            # Request IDs are not emitted by this native build; never invent a match.
            if events:
                journal = Path(cache_path).parent / "latency" / ("native-" + str(row["pid"]) + ".jsonl")
                journal.parent.mkdir(mode=0o700, exist_ok=True)
                try:
                    fd = os.open(journal, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                    with os.fdopen(fd, "a") as stream:
                        stream.write("".join(json.dumps(e) + "\n" for e in events))
                except OSError:
                    journal_error = "Native latency journal write error"

            cache["offset"] += boundary + 1
        elif len(data) >= MAX_READ:
            cache["offset"] += len(data)
            cache["skip_line"] = True
        cache["metrics"] = reducer.value
        atomic(cache_path, cache)
        value = {k: v for k, v in reducer.value.items() if not k.startswith("_")}
        value["log_catching_up"] = stat.st_size > read_end
        return value, [journal_error] if boundary >= 0 and journal_error else []
    except Exception as error:
        return {}, ["Native log collection error: " + type(error).__name__]


def command(args):
    r = subprocess.run(args, text=True, capture_output=True, timeout=2)
    if r.returncode:
        raise RuntimeError("OS query failed")
    return r.stdout


def process_resources(pid, root, cache_path):
    values = {}
    errors = []

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
        count = lambda name: int(re.search(name + r":\s*(\d+)", text)[1])
        free = count("Pages free")
        inactive = count("Pages inactive")
        spec = count("Pages speculative")
        return {"free_memory_bytes": free * page, "available_memory_approx_bytes": (free + inactive + spec) * page}

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
            cache = {
                "measured_at": time.time(),
                "cache_disk_bytes": sum(p.stat().st_blocks * 512 for p in (Path(root) / "cache").rglob("*") if p.is_file()),
            }
            atomic(cache_path, cache)
        return {"cache_disk_bytes": cache["cache_disk_bytes"]}

    collect("Swap", swap)
    collect("Memory", memory)
    collect("Disk", lambda: {"free_disk_bytes": shutil.disk_usage(root).free})
    collect("Cache", disk_cache)
    if pid:
        collect("Process", process)
    return values, errors


def assemble(c, row, health, native, clients, resources, now=None):
    """Keep exact provider usage distinct from native generation counters and old sessions."""
    now = time.time() if now is None else now
    if not row:
        return {}, "IDLE"
    selected = (
        max(clients, key=lambda x: (bool(x.get("active") or x.get("tool_execution_active")), x.get("started_at", 0))) if clients else {}
    )
    request = dict(selected)
    if not request and not native.get("request_sequence"):
        return {}, "IDLE"
    active_clients = sum(bool(x.get("active")) for x in clients)
    others = [x for x in clients if x is not selected]
    isolated = all(
        not x.get("active")
        and not x.get("tool_execution_active")
        and isinstance(x.get("ended_at"), (int, float))
        and x["ended_at"] < request.get("started_at", 0) - 1
        for x in others
    )
    # There is no native request ID in these log lines. Do not blend multiple clients.
    matched = not clients or (
        isolated
        and native.get("started_at") is not None
        and request.get("started_at") is not None
        and native["started_at"] >= request["started_at"] - 1
        and (request.get("active") or native["started_at"] <= request.get("ended_at", request.get("observed_at", now)))
    )
    if clients and request.get("active"):
        matched = matched and bool(native.get("active"))
        if str(request.get("phase", "")).startswith("PREFILL") and not str(native.get("phase", "")).startswith("PREFILL"):
            matched = False
    if matched and native.get("request_sequence"):
        for key in [
            "prompt_tokens",
            "new_prefill_tokens",
            "prefill_tokens",
            "prefill_total",
            "prefill_seconds",
            "prefill_tps",
            "reused_tokens",
            "checkpoint_hit",
            "checkpoint_source",
            "checkpoint_restore_s",
            "generated_tokens",
            "decode_tps",
            "decode_chunk_tps",
            "decode_elapsed",
        ]:
            if key in native:
                # API accounting wins for prompt/cache counts; native rate remains native.
                target = "input_tokens" if key == "prompt_tokens" else key
                if target not in request or request[target] is None:
                    request[target] = native[key]
        if not clients:
            request.update(
                active=native.get("active", False),
                phase=native.get("phase", "IDLE"),
                phase_source="native log",
                started_at=native.get("started_at"),
                elapsed_s=native.get("request_seconds"),
                profile=None,
            )
    if request.get("active") and request.get("started_at") and not (clients and isinstance(request.get("elapsed_s"), (int, float))):
        request["elapsed_s"] = max(0, now - request["started_at"])
    if request.get("input_tokens") is not None:
        requested = request.get("requested_output", c["max_output"])
        if requested is not None:
            request["effective_output"] = min(requested, max(0, c["context"] - request["input_tokens"]))
    if not request.get("active") and request.get("outcome") in ("error", "aborted", "shutdown"):
        request["phase"] = "ERROR"
    if request.get("tool_execution_active"):
        request["phase"] = "TOOL_CALL"
    if isinstance(request.get("session_timing"), dict):
        request["session_timing"] = dict(request["session_timing"])
        request["session_timing"].update(
            current_prefill_s=native.get("prefill_seconds") if matched else None,
            prefill_source="exact native duration, conservatively matched" if matched else "Not exposed: native record unmatched",
            total_prefill_s=None,
            total_prefill_source="Not exposed: no native session/request IDs; cumulative client attribution cannot be proved",
        )
    request["native_matched"] = matched
    request["active_client_count"] = active_clients
    return request, request.get("phase", "IDLE")
