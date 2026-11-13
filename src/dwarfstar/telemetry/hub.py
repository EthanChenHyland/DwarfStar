"""Telemetry hub: receives producer datagrams, keeps a live picture, serves consumers.

One hub runs inside the Workstation service. It

* receives JSON datagrams from the controller, the agent layer and the native
  runtime on a loopback UDP port;
* turns the native runtime's progress records into structured request events;
* maintains a compact live snapshot (request phase, token counts, rates,
  context occupancy, MTP, n-gram disk reads, router heat, tool timeline);
* fans events out to subscribers (the Workstation's event stream);
* records a bounded history per session for replay;
* tells the native runtime how much detail to produce by sending heartbeats
  only while a subscriber wants it. No subscriber means no heartbeat, which
  means the runtime computes nothing extra.
"""
from __future__ import annotations

import collections
import json
import queue
import re
import socket
import threading
import time
from pathlib import Path

from dwarfstar import telemetry
from dwarfstar.paths import TELEMETRY

RING = 4000
SESSION_EVENT_LIMIT = 20000
SESSION_FILE_LIMIT = 30
TICK_FANOUT_HZ = 30.0
ROUTER_RECORD_INTERVAL_S = 0.25
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

RE_START = re.compile(r"\b(chat|completion) ctx=(\d+)\.\.(\d+):(\d+)(?: ([A-Z_ ]+))? prompt start$")
RE_PREFILL = re.compile(r"\bprefill chunk (\d+)/(\d+) \([\d.]+%\) chunk=([\d.]+) t/s avg=([\d.]+) t/s ([\d.]+)s$")
RE_PROMPT_DONE = re.compile(r"\bprompt done ([\d.]+)s$")
RE_DECODE = re.compile(r"gen=(\d+)(.*?) decoding chunk=([\d.]+) t/s avg=([\d.]+) t/s ([\d.]+)s$")
RE_FINISH = re.compile(r"gen=(\d+).*? finish=(stop|length|tool_calls|error|cancelled)(?: .*?)? ([\d.]+)s$")
RE_KV_HIT = re.compile(r"kv cache hit \w+(?: RESPPROTO)? tokens=(\d+)")
RE_KV_STORE = re.compile(r"kv cache stored tokens=(\d+) .*?reason=(\w+).*?size=([\d.]+) MiB")
RE_TOOLS = re.compile(r"tool calls ctx=.*? n=(\d+) .*?names=\[([^\]]*)\]")
RE_VISION = re.compile(r"vision embedding (encoded|cache hit)")


class Subscriber:
    def __init__(self, level: int):
        self.level = level
        self.queue: queue.Queue = queue.Queue(maxsize=2000)
        self.dropped = 0

    def offer(self, event: dict) -> None:
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            self.dropped += 1


class Hub:
    def __init__(self, event_port: int | None = None, control_port: int | None = None, record: bool = True):
        self.event_port = event_port or telemetry.event_port()
        self.control_port = control_port or telemetry.DEFAULT_CONTROL_PORT
        self.lock = threading.Lock()
        self.ring: collections.deque = collections.deque(maxlen=RING)
        self.subscribers: list[Subscriber] = []
        self.sequence = 0
        self.stop_flag = threading.Event()
        self.record = record
        self.session_id: str | None = None
        self.session_file = None
        self.session_events = 0
        self.last_tick_fanout = 0.0
        self.pending_tick: dict | None = None
        self.last_router_record = 0.0
        self.live = self._fresh_live()
        self.stopped_at: dict[str, float] = {}
        self.stopped_models: set[str] = set()
        self.router_heat: dict[str, list[list[int]]] = {}
        self.counts = collections.Counter()
        self.started = time.time()
        self.socket: socket.socket | None = None
        self.control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", self.event_port))
        self.socket.settimeout(0.5)
        threading.Thread(target=self._receive_loop, name="telemetry-receive", daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, name="telemetry-heartbeat", daemon=True).start()

    def stop(self) -> None:
        self.stop_flag.set()
        self._close_session()
        for handle in (self.socket, self.control):
            try:
                if handle is not None:
                    handle.close()
            except OSError:
                pass

    # ------------------------------------------------------------ subscribers

    def subscribe(self, level: int = 1) -> Subscriber:
        subscriber = Subscriber(max(1, min(2, level)))
        with self.lock:
            self.subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        with self.lock:
            if subscriber in self.subscribers:
                self.subscribers.remove(subscriber)

    def wanted_level(self) -> int:
        with self.lock:
            return max((s.level for s in self.subscribers), default=0)

    def _heartbeat_loop(self) -> None:
        """While somebody is watching, keep the native runtime's detail level alive."""
        while not self.stop_flag.is_set():
            level = self.wanted_level()
            if level:
                try:
                    payload = json.dumps({"level": level, "ttl_ms": 3000}).encode()
                    self.control.sendto(payload, ("127.0.0.1", self.control_port))
                except OSError:
                    pass
            self.stop_flag.wait(1.0)

    # --------------------------------------------------------------- receive

    def _receive_loop(self) -> None:
        while not self.stop_flag.is_set():
            try:
                data, _ = self.socket.recvfrom(65535)
            except socket.timeout:
                self._flush_tick(force=True)
                continue
            except OSError:
                break
            try:
                event = json.loads(data)
            except ValueError:
                self.counts["malformed"] += 1
                continue
            if isinstance(event, dict) and isinstance(event.get("kind"), str):
                try:
                    self.ingest(event)
                except Exception:  # a bad event must never stop the hub
                    self.counts["ingest_error"] += 1

    def ingest(self, event: dict) -> None:
        kind = event["kind"]
        self.counts[kind] += 1
        if kind == "log":
            for derived in self._from_native_record(event):
                self.publish(derived)
            return
        if kind == "tick":
            self._tick(event)
            return
        if kind == "tool":
            self._tool(event)
        elif kind == "runtime":
            self._runtime(event)
        elif kind == "agent":
            self.live["agent"] = {k: event.get(k) for k in ("phase", "profile", "effort", "approval_mode")}
        self.publish(event)

    def publish(self, event: dict, record: bool = True) -> None:
        with self.lock:
            self.sequence += 1
            event = {"id": self.sequence, **event}
            self.ring.append(event)
            subscribers = list(self.subscribers)
        for subscriber in subscribers:
            if event.get("kind") != "tick" or subscriber.level >= 1:
                subscriber.offer(event)
        if record:
            self._record(event)

    # --------------------------------------------------------------- reducers

    def _fresh_live(self) -> dict:
        return {
            "model": None, "runtime": {"phase": "offline"}, "request": {"phase": "IDLE", "active": False},
            "context": {"capacity": None, "prompt": 0, "generated": 0, "used": 0},
            "rates": {"prefill_tps": None, "decode_tps": None, "live_tps": None, "ttft_s": None},
            "mtp": {"cycles": 0, "proposed": 0, "accepted": 0, "rejected": 0, "tokens": 0},
            "ple": {"reads": 0, "bytes": 0, "bytes_per_s": 0.0, "reads_per_s": 0.0},
            "kv": {"hits": 0, "stores": 0, "last_restore_tokens": None},
            "tools": {"active": None, "count": 0, "timeline": []},
            "vision": {"encodes": 0, "cache_hits": 0},
            "agent": {},
            "request_count": 0,
        }

    def _runtime(self, event: dict) -> None:
        phase = event.get("phase")
        if phase in ("starting",):
            self.live = self._fresh_live()
            self.router_heat.pop(event.get("model", ""), None)
        self.live["model"] = event.get("model")
        self.live["runtime"] = {k: event.get(k) for k in ("phase", "pid", "load_s", "memory_plan", "admission") if k in event}
        if phase in ("stopped", "start_failed"):
            self.stopped_at[event.get("model") or ""] = float(event.get("t") or time.time())
            self.stopped_models.add(event.get("model") or "")
            self._close_session()
        elif phase in ("starting", "running"):
            self.stopped_at.pop(event.get("model") or "", None)
            self.stopped_models.discard(event.get("model") or "")

    def _from_native_record(self, event: dict) -> list[dict]:
        line = ANSI.sub("", event.get("line", ""))
        base = {"t": event.get("t"), "src": "native", "model": event.get("model")}
        live, request = self.live, self.live["request"]
        live["model"] = event.get("model") or live["model"]
        out: list[dict] = []
        match = RE_KV_HIT.search(line)
        if match:
            live["kv"]["hits"] += 1
            live["kv"]["last_restore_tokens"] = int(match[1])
            out.append({**base, "kind": "kv", "phase": "hit", "tokens": int(match[1])})
        match = RE_KV_STORE.search(line)
        if match:
            live["kv"]["stores"] += 1
            out.append({**base, "kind": "kv", "phase": "store", "tokens": int(match[1]), "reason": match[2], "mib": float(match[3])})
        match = RE_VISION.search(line)
        if match:
            key = "encodes" if match[1] == "encoded" else "cache_hits"
            live["vision"][key] += 1
            out.append({**base, "kind": "vision", "phase": match[1]})
        match = RE_START.search(line)
        if match:
            reused, prompt, new = int(match[2]), int(match[3]), int(match[4])
            live["request_count"] += 1
            # Native log lines can arrive after the controller reported the stop; they must not reopen a session.
            # A stopped model produces nothing new until it reports starting again; anything later is a delayed record.
            model = event.get("model") or ""
            if model not in self.stopped_models and float(event.get("t") or 0) > self.stopped_at.get(model, -1):
                self._open_session(event.get("model"))
            live["request"] = request = {
                "phase": "PREFILL", "active": True, "sequence": live["request_count"], "started": event.get("t"),
                "reused_tokens": reused, "prompt_tokens": prompt, "new_prefill_tokens": new, "prefill_done": 0,
                "generated": 0, "tools": "TOOLS" in (match[5] or ""),
            }
            live["context"].update(prompt=prompt, generated=0, used=prompt)
            live["rates"].update(prefill_tps=None, decode_tps=None, live_tps=None, ttft_s=None)
            out.append({**base, "kind": "request", "phase": "start", **{k: request[k] for k in ("sequence", "reused_tokens", "prompt_tokens", "new_prefill_tokens", "tools")}})
            return out
        match = RE_PREFILL.search(line)
        if match:
            request.update(phase="PREFILL", prefill_done=int(match[1]), prefill_total=int(match[2]))
            live["rates"]["prefill_tps"] = float(match[4])
            out.append({**base, "kind": "request", "phase": "prefill", "done": int(match[1]), "total": int(match[2]),
                        "chunk_tps": float(match[3]), "avg_tps": float(match[4]), "seconds": float(match[5])})
        match = RE_PROMPT_DONE.search(line)
        if match and request.get("active"):
            seconds = float(match[1])
            request.update(phase="DECODE", prefill_seconds=seconds, prefill_done=request.get("new_prefill_tokens", 0))
            if seconds > 0 and request.get("new_prefill_tokens"):
                live["rates"]["prefill_tps"] = request["new_prefill_tokens"] / seconds
            live["rates"]["ttft_s"] = seconds
            out.append({**base, "kind": "request", "phase": "prefill_done", "seconds": seconds, "prefill_tps": live["rates"]["prefill_tps"]})
        match = RE_DECODE.search(line)
        if match:
            flags = match[2]
            phase = "TOOL_CALL" if "DSML_START" in flags else "REASONING" if "THINKING" in flags else "OUTPUT"
            request.update(phase=phase, generated=int(match[1]))
            live["rates"]["decode_tps"] = float(match[4])
            live["context"].update(generated=int(match[1]), used=request.get("prompt_tokens", 0) + int(match[1]))
            out.append({**base, "kind": "request", "phase": "decode", "generated": int(match[1]), "chunk_tps": float(match[3]),
                        "avg_tps": float(match[4]), "state": phase})
        match = RE_TOOLS.search(line)
        if match:
            names = [name.strip() for name in match[2].split(",") if name.strip()]
            out.append({**base, "kind": "request", "phase": "tool_calls", "count": int(match[1]), "names": names})
        match = RE_FINISH.search(line)
        if match:
            generated, finish, seconds = int(match[1]), match[2], float(match[3])
            request.update(phase="ERROR" if finish in ("error", "cancelled") else "COMPLETE", active=False,
                           generated=generated, finish=finish, seconds=seconds)
            live["context"].update(generated=generated, used=request.get("prompt_tokens", 0) + generated)
            live["rates"]["live_tps"] = None
            out.append({**base, "kind": "request", "phase": "finish", "generated": generated, "finish": finish, "seconds": seconds,
                        "decode_tps": live["rates"]["decode_tps"], "prefill_tps": live["rates"]["prefill_tps"]})
        return out

    def _tick(self, event: dict) -> None:
        live = self.live
        live["model"] = event.get("model") or live["model"]
        committed = int(event.get("n", 0))
        proposed, accepted = int(event.get("dp", 0)), int(event.get("da", 0))
        mtp = live["mtp"]
        mtp["cycles"] += 1
        mtp["proposed"] += proposed
        mtp["accepted"] += accepted
        mtp["rejected"] += max(0, proposed - accepted)
        mtp["tokens"] += committed
        reads, size = event.get("ple", [0, 0])
        ple = live["ple"]
        ple["reads"] += reads
        ple["bytes"] += size
        milliseconds = float(event.get("ms") or 0.0)
        if milliseconds > 0:
            ple["bytes_per_s"] = 0.8 * ple["bytes_per_s"] + 0.2 * (size / milliseconds * 1000.0)
            ple["reads_per_s"] = 0.8 * ple["reads_per_s"] + 0.2 * (reads / milliseconds * 1000.0)
            instant = committed / milliseconds * 1000.0
            previous = live["rates"]["live_tps"]
            live["rates"]["live_tps"] = instant if previous is None else 0.85 * previous + 0.15 * instant
        position = int(event.get("pos", 0))
        live["context"]["used"] = max(live["context"].get("used", 0), position + committed)
        rows = event.get("r")
        if rows:
            width = int(event.get("k", 10))
            heat = self.router_heat.setdefault(event.get("model") or "", [])
            while len(heat) < len(rows):
                heat.append([0] * 512)
            for layer, row in enumerate(rows):
                for index in range(0, min(len(row), 2 * width), 2):
                    expert = row[index]
                    if 0 <= expert < 512:
                        heat[layer][expert] += 1
        # Recorded history keeps every tick's counters but only a sampled set of router rows.
        now = time.time()
        if rows and now - self.last_router_record >= ROUTER_RECORD_INTERVAL_S:
            self.last_router_record = now
            self._record(event)
        else:
            self._record({k: v for k, v in event.items() if k not in ("r", "k")})
        with self.lock:
            self.sequence += 1
            self.pending_tick = {"id": self.sequence, **event}
        self._flush_tick()

    def _flush_tick(self, force: bool = False) -> None:
        """Fan ticks out at a bounded rate: the newest tick wins, counters above stay exact."""
        now = time.time()
        with self.lock:
            tick = self.pending_tick
            if tick is None or (not force and now - self.last_tick_fanout < 1.0 / TICK_FANOUT_HZ):
                return
            self.pending_tick = None
            self.last_tick_fanout = now
            self.ring.append(tick)
            subscribers = list(self.subscribers)
        for subscriber in subscribers:
            if subscriber.level >= 2 or "r" not in tick:
                subscriber.offer(tick)
            else:
                subscriber.offer({k: v for k, v in tick.items() if k not in ("r", "k")})

    def _tool(self, event: dict) -> None:
        tools = self.live["tools"]
        entry = {k: event.get(k) for k in ("t", "name", "category", "phase", "ok", "ms", "call")}
        if event.get("phase") == "start":
            tools["active"] = entry
        else:
            tools["count"] += 1
            if tools["active"] and tools["active"].get("call") == event.get("call"):
                tools["active"] = None
            tools["timeline"].append(entry)
            del tools["timeline"][:-200]

    # ----------------------------------------------------------------- record

    def _open_session(self, model: str | None) -> None:
        if not self.record or self.session_file is not None:
            return
        directory = TELEMETRY / "sessions"
        directory.mkdir(parents=True, exist_ok=True)
        self.session_id = time.strftime("%Y%m%d-%H%M%S") + "-" + (model or "model")
        self.session_file = (directory / (self.session_id + ".jsonl")).open("a")
        self.session_events = 0
        files = sorted(directory.glob("*.jsonl"))
        for old in files[:-SESSION_FILE_LIMIT]:
            old.unlink(missing_ok=True)

    def _close_session(self) -> None:
        if self.session_file is not None:
            try:
                self.session_file.close()
            except OSError:
                pass
        self.session_file = None
        self.session_id = None

    def _record(self, event: dict) -> None:
        if self.session_file is None or self.session_events >= SESSION_EVENT_LIMIT:
            return
        try:
            self.session_file.write(json.dumps(event, separators=(",", ":")) + "\n")
            self.session_events += 1
            if event.get("kind") != "tick":
                self.session_file.flush()
        except OSError:
            pass

    # ------------------------------------------------------------------ views

    def snapshot(self) -> dict:
        mtp = dict(self.live["mtp"])
        mtp["acceptance_pct"] = round(100.0 * mtp["accepted"] / mtp["proposed"], 1) if mtp["proposed"] else None
        return {
            **{k: v for k, v in self.live.items() if k != "mtp"},
            "mtp": mtp,
            "hub": {"uptime_s": round(time.time() - self.started, 1), "subscribers": len(self.subscribers),
                    "events": dict(self.counts), "session": self.session_id, "detail_level": self.wanted_level()},
        }

    def heat(self, model: str | None = None) -> dict:
        model = model or self.live.get("model") or ""
        return {"model": model, "layers": self.router_heat.get(model, [])}

    def recent(self, after: int = 0, limit: int = 500) -> list[dict]:
        with self.lock:
            return [event for event in self.ring if event["id"] > after][-limit:]


def list_sessions() -> list[dict]:
    directory = TELEMETRY / "sessions"
    rows = []
    for path in sorted(directory.glob("*.jsonl"), reverse=True) if directory.is_dir() else []:
        rows.append({"id": path.stem, "bytes": path.stat().st_size, "modified": path.stat().st_mtime})
    return rows


def read_session(session_id: str, limit: int = SESSION_EVENT_LIMIT) -> list[dict]:
    if not re.fullmatch(r"[\w.-]+", session_id):
        raise ValueError("Invalid session id")
    path = Path(TELEMETRY / "sessions" / (session_id + ".jsonl"))
    events = []
    with path.open() as stream:
        for line in stream:
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
            if len(events) >= limit:
                break
    return events
