"""Telemetry hub: receives producer datagrams, keeps a live picture, serves consumers.

One hub runs inside the Workstation service. It

* receives JSON datagrams from the controller and the agent layer on a loopback UDP port;
* maintains a compact live snapshot (runtime phase, request, context, tool timeline; and, for sessions recorded
  from the retired native runtime, its verify cycles, n-gram reads and router heat, so they still replay);
* fans events out to subscribers (the Workstation's event stream);
* records a bounded history per session (one per model start) for replay.
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
    def __init__(self, event_port: int | None = None, record: bool = True):
        self.event_port = event_port or telemetry.event_port()
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

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self.record:
            self.finalized = finalize_open_sessions()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", self.event_port))
        self.socket.settimeout(0.5)
        threading.Thread(target=self._receive_loop, name="telemetry-receive", daemon=True).start()

    def stop(self) -> None:
        self.stop_flag.set()
        self._close_session()
        try:
            if self.socket is not None:
                self.socket.close()
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
            if phase == "running":
                self._open_session(event.get("model"))

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

    def _close_session(self, reason: str = "model stopped") -> None:
        if self.session_file is not None:
            try:
                self.session_file.write(json.dumps({"t": time.time(), "kind": "session_end", "reason": reason}) + "\n")
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


def finalize_open_sessions(directory: Path | None = None) -> list[str]:
    """A session file whose last record is not `session_end` was left open by a crash (service, Workstation or
    machine). Close it with a recovered marker so replay and history know where it really ended."""
    directory = directory or TELEMETRY / "sessions"
    finalized = []
    for path in sorted(directory.glob("*.jsonl"))[-SESSION_FILE_LIMIT:]:
        try:
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 4096))
                lines = [line for line in stream.read().splitlines() if line.strip()]
            last = json.loads(lines[-1]) if lines else {}
        except (OSError, ValueError):
            continue
        if last.get("kind") != "session_end":
            with path.open("a") as stream:
                stream.write(json.dumps({"t": time.time(), "kind": "session_end", "reason": "recovered after an unclean shutdown"}) + "\n")
            finalized.append(path.stem)
    return finalized


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
