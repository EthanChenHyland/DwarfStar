"""Workstation service: one local process behind the DwarfStar Workstation.

It owns profile selection and the model lifecycle, runs the telemetry hub and
serves the user interface. Everything is bound to the loopback interface.

    dwarfstar serve [--port 8038] [--no-ui]
    dwarfstar state                 current selection and per-model status (JSON)
    dwarfstar switch PROFILE        make PROFILE's model the running model
    dwarfstar stop-all              stop whichever model is running

Model servers are only ever started and stopped through each model's own
controller (``dwarfstar runtime --model ID ...``), one model at a time.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from dwarfstar import catalog, telemetry
from dwarfstar.paths import CODE, DATA, child_environment

WEB_ROOT = CODE / "app" / "workstation" / "web"
STATE_FILE = DATA / "state" / "workstation.json"
PORT_CLOSE_TIMEOUT_S = catalog.policy()["lifecycle"]["port_close_timeout_s"]
CONTROLLER_TIMEOUT_S = 700


# ------------------------------------------------------------------ controller


def parse_controller_output(stdout: str) -> dict | None:
    """The controller's answer is its last JSON line; anything printed before it is ignored."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def controller(model_id: str, *arguments: str, timeout: float = CONTROLLER_TIMEOUT_S) -> dict:
    """Run one controller action for one model in its own process."""
    command = [sys.executable, "-B", "-m", "dwarfstar", "runtime", "--model", model_id, *arguments]
    env = {**os.environ, **child_environment()}
    env.pop("DWARFSTAR_MODEL", None)
    env.pop("DWARFSTAR_INSTANCE", None)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Controller timed out after {timeout:.0f}s"}
    parsed = parse_controller_output(result.stdout)
    if parsed is None:
        return {"ok": False, "error": (result.stderr or result.stdout or "controller produced no output")[-600:]}
    return parsed


def model_states(detail: bool = False) -> dict[str, dict]:
    states = {}
    for model_id in catalog.models():
        status = controller(model_id, "status", timeout=30)
        if not status.get("ok"):
            states[model_id] = {"ok": False, "running": False, "healthy": False, "error": status.get("error")}
            continue
        row = {"ok": True, "running": status["running"], "healthy": status["healthy"], "pid": status.get("pid"),
               "endpoint": status.get("endpoint"), "uptime_s": status.get("uptime_s"), "phase": status.get("phase"),
               "memory_plan": status.get("memory_plan"), "load_seconds": status.get("load_seconds"),
               "mtp_state": status.get("mtp_state"), "collection_errors": status.get("collection_errors", [])}
        if detail:
            row["request"] = status.get("request")
            row["native"] = status.get("native")
            row["resources"] = status.get("resources")
            row["log"] = status.get("log")
            row["permissions"] = status.get("permissions")
        states[model_id] = row
    return states


# ------------------------------------------------------------------- selection
#
# Two separate facts are persisted:
#   selected_profile  what the user has chosen in the UI (survives restarts; defaults to Scope)
#   active_profile    the profile the running model was started or last switched for; None when nothing runs
# Keeping them apart is what lets the UI show "Cyber LOW selected, Cyber MEDIUM running" truthfully and
# guarantees a stop leaves no stale reasoning state behind.


def _read_state() -> dict:
    try:
        value = json.loads(STATE_FILE.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(**fields) -> None:
    value = {**_read_state(), **fields, "updated": time.time()}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(value) + "\n")
    temporary.replace(STATE_FILE)


def stored_selection() -> str:
    selected = _read_state().get("selected_profile")
    try:
        catalog.profile(selected)
        return selected
    except (TypeError, catalog.CatalogError):
        return catalog.default_profile()["id"]


def store_selection(profile_id: str) -> None:
    _write_state(selected_profile=profile_id)


def active_profile() -> str | None:
    active = _read_state().get("active_profile")
    try:
        catalog.profile(active)
        return active
    except (TypeError, catalog.CatalogError):
        return None


def store_active(profile_id: str | None) -> None:
    _write_state(active_profile=profile_id)


def consistent_active(running: list[str]) -> str | None:
    """The active profile only counts while its own model is the one running; anything else is stale."""
    active = active_profile()
    if active and catalog.profile(active)["model"] in running:
        return active
    if active and not running:
        store_active(None)
    return None


def lifecycle_log(record: dict) -> None:
    """Append-only lifecycle journal in the data root, so a failed switch can be diagnosed afterwards."""
    try:
        path = DATA / "logs" / "lifecycle.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as stream:
            stream.write(json.dumps({"t": time.time(), **record}, default=str) + "\n")
    except OSError:
        pass


class Lifecycle:
    """The model lifecycle state machine. The service holds the only authoritative copy of its state.

    One job runs at a time. A job is a switch to a profile or a stop. Requests that arrive while a job
    runs are answered from that job: the same request joins it, a different one is refused with the
    running job described, so the UI and the controller can never disagree about what is happening.
    """

    def __init__(self, notify=None):
        self.lock = threading.Lock()
        self.guard = threading.Lock()
        self.notify = notify or (lambda *a, **k: None)
        self.current: dict = {"busy": False, "steps": [], "job": 0}
        self.jobs = 0
        self.done = threading.Condition(self.guard)

    def _step(self, text: str, **fields) -> None:
        entry = {"t": time.time(), "text": text, **fields}
        self.current["steps"].append(entry)
        lifecycle_log({"job": self.current.get("job"), "step": text, **fields})
        self.notify("lifecycle", step=text, **fields)

    # -------------------------------------------------------------- job control

    def _begin(self, kind: str, target: str | None) -> dict | None:
        """Claim the machine for one job, or describe the job that already holds it."""
        with self.guard:
            if self.current.get("busy"):
                return {"ok": False, "busy": True, "error": f"A {self.current['kind']} is in progress"
                        + (f" for {self.current['target']}." if self.current.get("target") else "."),
                        "job": self.current["job"], "kind": self.current["kind"], "target": self.current.get("target")}
            self.jobs += 1
            self.current = {"busy": True, "job": self.jobs, "kind": kind, "target": target, "steps": [],
                            "started": time.time(), "result": None}
            self.lock.acquire()
            return None

    def _end(self, result: dict) -> dict:
        with self.guard:
            summary = {k: result.get(k) for k in ("ok", "error")}
            self.current.update(busy=False, finished=time.time(), result=summary)
            lifecycle_log({"job": self.current["job"], "result": summary})
            self.lock.release()
            self.done.notify_all()
        return {**result, "job": self.current["job"], "steps": self.current["steps"]}

    def wait(self, job: int, timeout: float = CONTROLLER_TIMEOUT_S * 2) -> dict:
        """Block until job `job` has finished and return its outcome."""
        with self.guard:
            self.done.wait_for(lambda: self.current.get("job") != job or not self.current.get("busy"), timeout)
            return {**(self.current.get("result") or {"ok": False, "error": "job outcome unavailable"}),
                    "job": job, "steps": self.current.get("steps", [])}

    def submit(self, kind: str, target: str | None = None, wait: bool = True) -> dict:
        """Start a job. With wait=False it runs in the background and the caller follows `lifecycle` state."""
        if kind == "switch":
            try:
                catalog.profile(target)
            except catalog.CatalogError as error:
                return {"ok": False, "error": str(error)}
        refused = self._begin(kind, target)
        if refused:
            if refused["kind"] == kind and refused.get("target") == target:
                return self.wait(refused["job"]) if wait else {"ok": True, "accepted": True, "joined": True, "job": refused["job"]}
            return refused
        job = self.current["job"]
        run = (lambda: self._end(self._switch(target))) if kind == "switch" else (lambda: self._end(self._stop_all()))
        if wait:
            return run()
        threading.Thread(target=run, name=f"lifecycle-{job}", daemon=True).start()
        return {"ok": True, "accepted": True, "job": job}

    # Backwards-compatible synchronous entry points (command line, tests, validation scripts).
    def switch(self, profile_id: str) -> dict:
        return self.submit("switch", profile_id, wait=True)

    def stop_all(self) -> dict:
        return self.submit("stop", None, wait=True)

    # -------------------------------------------------------------------- steps

    def _fail(self, message: str) -> dict:
        self._step("Failed: " + message, error=True)
        return {"ok": False, "error": message}

    def _stop_model(self, model_id: str) -> str | None:
        """Stop one model through its controller and wait until its process and port are gone. Returns an error or None."""
        name = catalog.model(model_id)["display_name"]
        self._step(f"Stopping {name}")
        before = controller(model_id, "status", timeout=30)
        stopped = controller(model_id, "stop")
        if not stopped.get("ok"):
            return stopped.get("error", "stop refused")
        deadline = time.time() + PORT_CLOSE_TIMEOUT_S
        while time.time() < deadline:
            after = controller(model_id, "status", timeout=30)
            if after.get("ok") and not after.get("running") and not after.get("healthy"):
                break
            time.sleep(0.5)
        else:
            return f"{name} did not exit within {PORT_CLOSE_TIMEOUT_S} s"
        self._step(f"{name} exited", pid=before.get("pid"))
        return None

    def _switch(self, profile_id: str) -> dict:
        profile = catalog.profile(profile_id)
        target = profile["model"]
        self._step(f"Selected {profile['label']}")
        store_selection(profile_id)
        states = model_states()
        broken = [m for m, s in states.items() if not s.get("ok")]
        if broken:
            return self._fail("Cannot read the status of: " + ", ".join(broken) + ". Nothing was changed.")
        if states[target]["running"] and not states[target]["healthy"]:
            return self._fail(f"{target} is running but not healthy. It was not restarted.")
        for other, state in states.items():
            if other != target and state["running"]:
                error = self._stop_model(other)
                if error:
                    return self._fail(error + " The selected model was not started.")
                store_active(None)
        if states[target]["running"]:
            self._step("Model already running; reasoning changes without a restart")
        else:
            requirement = controller(target, "requirement", timeout=30)
            if requirement.get("ok"):
                self._step(f"Memory check: needs {requirement['required_gib']:.1f} GiB, "
                           f"{requirement['available_gib']:.1f} GiB available", requirement=requirement)
            self._step(f"Starting {catalog.model(target)['display_name']}")
            started = controller(target, "start")
            if not started.get("ok"):
                # A start can be reported as failed while the server actually came up (for example a controller
                # that was interrupted after the server became healthy). Keep a healthy model rather than
                # killing it; only clean up a server that is running but unhealthy.
                after = controller(target, "status", timeout=30)
                if after.get("running") and after.get("healthy"):
                    self._step("Start reported an error but the model is healthy; keeping it",
                               controller_error=started.get("error"))
                else:
                    if after.get("running"):
                        controller(target, "stop")
                    return self._fail(started.get("error", "start failed"))
            else:
                self._step(f"Loaded in {started.get('load_seconds', 0):.1f} s", load_seconds=started.get("load_seconds"))
        health = {}
        for _attempt in range(catalog.policy()["lifecycle"]["health_attempts_after_start"]):
            health = controller(target, "health", timeout=30)
            if health.get("ok"):
                break
            time.sleep(1.0)
        if not health.get("ok"):
            controller(target, "stop")
            store_active(None)
            return self._fail("Health check failed after start; the model was stopped.")
        store_active(profile_id)
        self._step(f"Ready: {profile['label']}", ready=True)
        return {"ok": True, "profile": profile}

    def _stop_all(self) -> dict:
        results = {}
        for model_id, state in model_states().items():
            if state.get("running"):
                error = self._stop_model(model_id)
                results[model_id] = {"ok": error is None, **({"error": error} if error else {})}
        ok = all(r["ok"] for r in results.values())
        if ok:
            store_active(None)
            self._step("No model is running")
            return {"ok": True, "stopped": results}
        message = "Stop refused: " + "; ".join(r["error"] for r in results.values() if not r["ok"])
        self._step(message, error=True)
        return {"ok": False, "error": message, "stopped": results}


def state_document(lifecycle: Lifecycle | None = None, detail: bool = False) -> dict:
    states = model_states(detail=detail)
    selected = stored_selection()
    running = [m for m, s in states.items() if s.get("running")]
    requirements = {model_id: catalog.memory_plan(model_id) for model_id in catalog.models()}
    return {
        "ok": True,
        "selected_profile": selected,
        "active_profile": consistent_active(running),
        "default_profile": catalog.default_profile()["id"],
        "running_models": running,
        "models": states,
        "requirements": requirements,
        "lifecycle": lifecycle.current if lifecycle else None,
        "service": {"pid": os.getpid(), "time": time.time()},
    }


# ------------------------------------------------------------------------ http


class Service:
    def __init__(self, port: int, serve_ui: bool = True):
        from dwarfstar.telemetry.hub import Hub
        from dwarfstar.telemetry.system import Sampler

        self.port = port
        self.serve_ui = serve_ui
        self.hub = Hub()
        self.sampler = Sampler()
        self.lifecycle = Lifecycle(notify=self._notify)
        from dwarfstar.chats import Turns

        self.turns = Turns(notify=self._notify)
        self.state_cache: tuple[float, dict] | None = None
        self.system_cache: dict = {}
        self.stop_flag = threading.Event()

    def _notify(self, kind: str, **fields) -> None:
        self.state_cache = None
        self.hub.publish({"t": time.time(), "src": "service", "kind": kind, **fields})

    def state(self, detail: bool = False) -> dict:
        now = time.time()
        if not detail and self.state_cache and now - self.state_cache[0] < 1.5:
            return self.state_cache[1]
        document = state_document(self.lifecycle, detail=detail)
        if not detail:
            self.state_cache = (now, document)
        return document

    def _system_loop(self) -> None:
        """Sample host counters once a second, but only while somebody is watching."""
        while not self.stop_flag.is_set():
            if self.hub.wanted_level() > 0:
                pid = None
                try:
                    running = [s for s in self.state()["models"].values() if s.get("running")]
                    pid = running[0]["pid"] if running else None
                except Exception:
                    pass
                sample = self.sampler.sample(pid)
                self.system_cache = sample
                self.hub.publish({"t": sample["t"], "src": "service", "kind": "system", **{k: sample[k] for k in ("memory", "gpu", "process")}},
                                 record=False)
            self.stop_flag.wait(1.0)

    def run(self) -> None:
        self.hub.start()
        threading.Thread(target=self._system_loop, name="system-sampler", daemon=True).start()
        service = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # quiet: the hub is the log
                return

            def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, value, status: int = 200) -> None:
                self._send(status, json.dumps(value).encode())

            def _local_only(self) -> bool:
                host = (self.headers.get("Host") or "").split(":")[0]
                origin = self.headers.get("Origin")
                allowed = ("127.0.0.1", "localhost")
                if host not in allowed or (origin and urlsplit(origin).hostname not in allowed):
                    self._json({"ok": False, "error": "Loopback requests only"}, 403)
                    return False
                return True

            def do_GET(self):  # noqa: N802
                if not self._local_only():
                    return
                url = urlsplit(self.path)
                query = parse_qs(url.query)
                path = url.path
                try:
                    if path == "/api/events":
                        return self._events(int(query.get("level", ["1"])[0]))
                    if path == "/api/catalog":
                        return self._json(service.catalog_document())
                    if path == "/api/state":
                        return self._json(service.state(detail="detail" in query))
                    if path == "/api/live":
                        return self._json({"ok": True, "live": service.hub.snapshot(), "system": service.system_cache})
                    if path == "/api/heat":
                        return self._json({"ok": True, **service.hub.heat(query.get("model", [None])[0])})
                    if path == "/api/recent":
                        return self._json({"ok": True, "events": service.hub.recent(int(query.get("after", ["0"])[0]))})
                    if path == "/api/sessions":
                        from dwarfstar.telemetry.hub import list_sessions

                        return self._json({"ok": True, "sessions": list_sessions()})
                    if path.startswith("/api/sessions/"):
                        from dwarfstar.telemetry.hub import read_session

                        return self._json({"ok": True, "events": read_session(path.rsplit("/", 1)[1])})
                    if path == "/api/chats":
                        from dwarfstar import chats

                        flag = query.get("archived", ["0"])[0]
                        archived = None if flag == "all" else flag == "1"
                        return self._json({"ok": True, "chats": chats.list_chats(query.get("q", [None])[0], archived)})
                    if path.startswith("/api/chats/"):
                        from dwarfstar import chats

                        chat_id = path.split("/")[3]
                        return self._json({"ok": True, "chat": chats.get(chat_id), "progress": service.turns.progress(chat_id)})
                    if path == "/api/benchmarks":
                        return self._json(service.benchmarks_document())
                    if path == "/api/diagnostics":
                        return self._json(service.diagnostics_document())
                    if path.startswith("/api/"):
                        return self._json({"ok": False, "error": "Unknown endpoint"}, 404)
                    return self._static(path)
                except (BrokenPipeError, ConnectionResetError):
                    return
                except ValueError as error:  # unknown chat, malformed query
                    return self._json({"ok": False, "error": str(error)}, 404)
                except Exception as error:
                    return self._json({"ok": False, "error": str(error)}, 500)

            def do_POST(self):  # noqa: N802
                if not self._local_only():
                    return
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    return self._json({"ok": False, "error": "Invalid JSON"}, 400)
                path = urlsplit(self.path).path
                try:
                    if path == "/api/switch":
                        result = service.lifecycle.submit("switch", str(body.get("profile", "")), wait=body.get("wait", True) is not False)
                    elif path == "/api/stop":
                        result = service.lifecycle.submit("stop", None, wait=body.get("wait", True) is not False)
                    elif path == "/api/select":
                        catalog.profile(str(body.get("profile", "")))
                        store_selection(body["profile"])
                        result = {"ok": True, "selected_profile": body["profile"]}
                    elif path == "/api/chats":
                        from dwarfstar import chats

                        state = service.state()
                        profile_id = body.get("profile") or state.get("active_profile") or stored_selection()
                        result = {"ok": True, "chat": chats.summary(chats.create(str(profile_id)))}
                    elif path.startswith("/api/chats/"):
                        from dwarfstar import chats

                        parts = path.split("/")
                        chat_id, action = parts[3], (parts[4] if len(parts) > 4 else "")
                        if action == "messages":
                            result = service.turns.start(chat_id, body.get("text", ""), service.state()["running_models"])
                        elif action == "rename":
                            result = {"ok": True, "chat": chats.summary(chats.rename(chat_id, body.get("title", "")))}
                        elif action == "archive":
                            result = {"ok": True, "chat": chats.summary(chats.set_archived(chat_id, bool(body.get("archived", True))))}
                        elif action == "delete":
                            result = chats.delete(chat_id, str(body.get("confirm", "")))
                        else:
                            return self._json({"ok": False, "error": "Unknown endpoint"}, 404)
                    elif path == "/api/launch":
                        profile = catalog.profile(str(body.get("profile") or stored_selection()))
                        arguments = ["chat", "--profile", profile["id"]]
                        if body.get("folder"):
                            arguments += ["--folder", str(body["folder"])]
                        result = controller(profile["model"], *arguments)
                    else:
                        return self._json({"ok": False, "error": "Unknown endpoint"}, 404)
                    service.state_cache = None
                    return self._json(result, 200 if result.get("ok") else 409)
                except catalog.CatalogError as error:
                    return self._json({"ok": False, "error": str(error)}, 400)
                except ValueError as error:  # chats.ChatError and malformed requests
                    return self._json({"ok": False, "error": str(error)}, 400)
                except Exception as error:
                    return self._json({"ok": False, "error": str(error)}, 500)

            def _events(self, level: int) -> None:
                subscriber = service.hub.subscribe(level)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    hello = {"kind": "hello", "t": time.time(), "live": service.hub.snapshot(), "system": service.system_cache}
                    self.wfile.write(f"data: {json.dumps(hello)}\n\n".encode())
                    self.wfile.flush()
                    while not service.stop_flag.is_set():
                        try:
                            event = subscriber.queue.get(timeout=10)
                        except queue.Empty:
                            self.wfile.write(b": keep-alive\n\n")
                            self.wfile.flush()
                            continue
                        batch = [event]
                        while len(batch) < 64:
                            try:
                                batch.append(subscriber.queue.get_nowait())
                            except queue.Empty:
                                break
                        self.wfile.write("".join(f"data: {json.dumps(e, separators=(',', ':'))}\n\n" for e in batch).encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    service.hub.unsubscribe(subscriber)

            def _static(self, path: str) -> None:
                if not service.serve_ui:
                    return self._json({"ok": False, "error": "User interface disabled"}, 404)
                relative = "index.html" if path in ("", "/") else path.lstrip("/")
                target = (WEB_ROOT / relative).resolve()
                if not target.is_relative_to(WEB_ROOT.resolve()) or not target.is_file():
                    return self._json({"ok": False, "error": "Not found"}, 404)
                content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                self._send(200, target.read_bytes(), content_type + ("; charset=utf-8" if content_type.startswith("text/") or content_type.endswith(("javascript", "json")) else ""))

        server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        server.daemon_threads = True
        self.hub.publish({"t": time.time(), "src": "service", "kind": "service", "phase": "listening", "port": self.port})
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop_flag.set()
            self.hub.stop()
            server.server_close()

    # ---------------------------------------------------------------- documents

    def catalog_document(self) -> dict:
        document = catalog.summary()
        document["ok"] = True
        document["details"] = {}
        for model_id, model in catalog.models().items():
            document["details"][model_id] = {
                "architecture": model["architecture"],
                "assets": {role: {"file": Path(asset["path"]).name, "bytes": asset["bytes"], "sha256": asset["sha256"]}
                           for role, asset in model["assets"].items()},
                "sampling": model["sampling"],
                "context_note": model.get("context_note"),
                "measured": model.get("measured", {}),
                "provenance": model.get("provenance", {}),
                "source": model["source"],
                "runtime": catalog.runtime_pin(model_id),
            }
        document["telemetry"] = {"schema": telemetry.SCHEMA_VERSION, "event_port": telemetry.event_port()}
        return document

    def benchmarks_document(self) -> dict:
        path = CODE / "research" / "benchmark-index.json"
        try:
            return {"ok": True, **json.loads(path.read_text())}
        except (OSError, ValueError):
            return {"ok": False, "error": "Benchmark index has not been generated (run `dwarfstar bench-index`)."}

    def diagnostics_document(self) -> dict:
        from dwarfstar import instance, zed

        rows = {}
        for model_id in catalog.models():
            problems = []
            try:
                problems = instance.check(model_id)
            except Exception as error:
                problems = [str(error)]
            rows[model_id] = {
                "generated_config": "matches the catalog" if not problems else problems,
                "instance": str(instance.instance(model_id)),
                "runtime": catalog.runtime_pin(model_id)["id"],
            }
        try:
            editor = zed.check()
        except Exception as error:
            editor = {"ok": False, "error": str(error)}
        seal = DATA / "DWARFSTAR-PRODUCTION-SEAL.json"
        return {
            "ok": True,
            "code_root": str(CODE), "data_root": str(DATA),
            "models": rows, "editor_entries": editor,
            "seal": {"path": str(seal), "present": seal.is_file()},
            "telemetry": {"hub": self.hub.snapshot()["hub"], "sessions_directory": str(DATA / "telemetry" / "sessions")},
            "state": self.state(detail=True),
        }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="dwarfstar")
    parser.add_argument("command", choices=["serve", "switch", "stop-all", "state"])
    parser.add_argument("profile", nargs="?")
    parser.add_argument("--port", type=int, default=int(os.environ.get("DWARFSTAR_HTTP_PORT", telemetry.DEFAULT_HTTP_PORT)))
    parser.add_argument("--no-ui", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "serve":
        Service(args.port, serve_ui=not args.no_ui).run()
        return 0
    if args.command == "state":
        print(json.dumps(state_document(), indent=2))
        return 0
    lifecycle = Lifecycle(notify=lambda kind, **f: print(f.get("step", ""), file=sys.stderr, flush=True))
    result = lifecycle.switch(args.profile or stored_selection()) if args.command == "switch" else lifecycle.stop_all()
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1
