"""Host-side task bookkeeping. No provider, prompt, tool or runtime policy changes.

All process claims require a verified parent edge. Commands are stored as hashes,
not arguments. A separate holder keeps the workspace flock across launcher exec.
"""

from pathlib import Path
import argparse, contextlib, datetime, fcntl, hashlib, json, os, signal, socket
import subprocess, sys, time, uuid

from dwarfstar import paths

CODE = paths.CODE
ROOT = paths.instance()
STATE = ROOT / "state/orchestration"
TERMINAL = {"COMPLETE", "COMPLETED", "CANCELLED", "FAILED"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def ident(value):
    import re

    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,120}", value):
        raise ValueError("Invalid task ID")
    return value


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("x") as f:
            os.chmod(tmp, 0o600)
            json.dump(value, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


@contextlib.contextmanager
def lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield f


def native_birth(pid):
    """Darwin PROC_PIDTBSDINFO start timeval (SDK proc_info.h), microsecond identity."""
    if sys.platform != "darwin":
        return None
    import ctypes

    class BSD(ctypes.Structure):
        _fields_ = [
            ("prefix", ctypes.c_uint32 * 12),
            ("comm", ctypes.c_char * 16),
            ("name", ctypes.c_char * 32),
            ("middle", ctypes.c_uint32 * 6),
            ("sec", ctypes.c_uint64),
            ("usec", ctypes.c_uint64),
        ]

    try:
        info = BSD()
        lib = ctypes.CDLL("/usr/lib/libproc.dylib")
        n = lib.proc_pidinfo(int(pid), 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        return str(info.sec) + ":" + str(info.usec) if n == ctypes.sizeof(info) else None
    except (OSError, TypeError, ValueError):
        return None


def fingerprint(pid, include_cwd=True):
    from dwarfstar.runtime_control import identity

    first = native_birth(pid)
    row = identity(pid)
    if not row:
        return None
    if first != native_birth(pid):
        return None
    cwd = None
    try:
        if not include_cwd:
            raise ValueError("not requested")
        p = subprocess.run(["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], text=True, capture_output=True, timeout=2)
        cwd = next((x[1:] for x in p.stdout.splitlines() if x.startswith("n")), None)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return {k: row[k] for k in ("pid", "birth", "argv_sha256")} | {
        "native_birth": first,
        "cwd": cwd,
        "executable": Path(row["command"].split()[0]).name,
    }


def matches(row):
    if not row:
        return False
    cur = fingerprint(row.get("pid"), include_cwd=not bool(row.get("native_birth")))
    return bool(
        cur
        and all(cur.get(k) == row.get(k) for k in ("pid", "birth", "argv_sha256"))
        and (
            cur.get("native_birth") == row["native_birth"]
            if row.get("native_birth")
            else not row.get("cwd") or cur.get("cwd") == row["cwd"]
        )
    )


def parent_pid(pid):
    try:
        return int(subprocess.check_output(["/bin/ps", "-p", str(pid), "-o", "ppid="], text=True).strip())
    except (ValueError, subprocess.SubprocessError):
        return None


def children(pid):
    data = subprocess.run(["/bin/ps", "-axo", "pid=,ppid="], capture_output=True, text=True, timeout=3).stdout
    table = []
    for line in data.splitlines():
        try:
            table.append(tuple(map(int, line.split())))
        except ValueError:
            pass
    return [p for p, q in table if q == pid]


def browser_process(command):
    return any(name in command for name in ("Google Chrome", "Chromium", "chrome-headless-shell", "chrome_crashpad_handler"))


def automation_profile(command, row, parent=None):
    """Prove an isolated task profile; a Chrome name or shared bundle ID is not ownership."""
    import re

    match = re.search(r"--user-data-dir(?:=|\s+)(.+?)(?=\s+--|$)", command)
    # A browser MAIN process without --user-data-dir is never automation-owned,
    # even if a stale/forged record labels it so. Only actual helper processes
    # may inherit the verified parent's isolated profile.
    helper = "--type=" in command or "/Helpers/" in command or "chrome_crashpad_handler" in command
    value = match.group(1).strip("\"'") if match else (parent or {}).get("browser_profile") if helper else None
    if not value:
        return None
    path = Path(value).resolve()
    personal = [Path.home() / "Library/Application Support/Google", Path.home() / "Library/Application Support/Chromium"]
    if any(path.is_relative_to(p.resolve()) for p in personal):
        return None
    roots = [row.get("workspace"), row.get("artifact_directory")]
    return str(path) if any(root and path.is_relative_to(Path(root).resolve()) for root in roots) else None


def executable_path(pid):
    if sys.platform != "darwin":
        return None
    import ctypes

    buffer = ctypes.create_string_buffer(4096)
    return buffer.value.decode() if ctypes.CDLL("/usr/lib/libproc.dylib").proc_pidpath(int(pid), buffer, len(buffer)) > 0 else None


class Store:
    def __init__(self, root=None):
        self.root = Path(STATE if root is None else root)

    def path(self, key):
        return self.root / "tasks" / (ident(key) + ".json")

    def get(self, key):
        return read(self.path(key))

    def update(self, key, **fields):
        with lock(self.root / "tasks.lock"):
            row = self.get(key) or {"schema": 1, "task_id": key, "created_at": now(), "processes": [], "ports": [], "resume_events": []}
            row.update(fields, updated_at=now())
            atomic(self.path(key), row)
            return row

    def all(self):
        rows = []
        errors = []
        for p in sorted((self.root / "tasks").glob("*.json")):
            try:
                row = read(p)
                if not isinstance(row, dict) or row.get("schema") != 1 or row.get("task_id") != p.stem:
                    raise ValueError("Invalid task record")
                rows.append(row)
            except (OSError, ValueError) as e:
                errors.append({"path": str(p), "error": str(e)})
        rows.sort(key=lambda x: x.get("updated_at", x.get("created_at", "")), reverse=True)
        return rows, errors

    def create(self, key, workspace, profile, **fields):
        if self.path(key).exists():
            old = self.get(key)
            if old["workspace"] != str(Path(workspace).resolve()) or old["profile_id"] != profile:
                raise ValueError("Task identity/profile/workspace mismatch")
        return self.update(key, workspace=str(Path(workspace).resolve()), profile_id=profile, **fields)

    def register(self, key, pid, role, parent=None, root=False):
        current = fingerprint(pid)
        if not current:
            raise ValueError("Process already exited")
        if not root:
            if not matches(parent) or parent_pid(pid) != parent["pid"]:
                raise ValueError("No verified task-owned parent edge")
            # Verify again after observing child to close the ancestry/PID race.
            if not matches(parent):
                raise ValueError("Parent identity changed")
        row = self.get(key)
        from dwarfstar.runtime_control import identity

        command = identity(pid)["command"]
        browser = browser_process(command)
        browser_profile = automation_profile(command, row, parent) if browser else None
        if browser and not browser_profile:
            raise ValueError("Unowned/personal browser cannot enter task cleanup registry")
        entry = {
            **current,
            "task_id": key,
            "run_id": row.get("run_id"),
            "workspace": row["workspace"],
            "profile_id": row["profile_id"],
            "parent_pid": parent_pid(pid),
            "parent_identity": parent,
            "role": role,
            "root_role": role if root else parent.get("root_role", parent.get("role", role)),
            "registered_at": now(),
            "alive": True,
            "temporary": True,
            "cleanup_status": "pending",
            "ports": [],
        }
        if browser:
            entry.update(
                automation_browser=True,
                browser_profile=browser_profile,
                executable_path=executable_path(pid),
                browser_mode="headless" if "--headless" in command or "chrome-headless-shell" in command else "headed/background",
                purpose="Task browser automation; never user-facing navigation",
            )
        with lock(self.root / "tasks.lock"):
            row = self.get(key)
            old = row.get("processes", [])
            if any(x["pid"] == pid and x["birth"] == entry["birth"] and x["argv_sha256"] == entry["argv_sha256"] for x in old):
                return entry
            row["processes"] = old + [entry]
            row["updated_at"] = now()
            atomic(self.path(key), row)
        return entry

    def discover(self, key):
        row = self.get(key)
        known = row.get("processes", [])
        pending = [x for x in known if matches(x)]
        seen = {(x["pid"], x["birth"], x["argv_sha256"]) for x in known}
        for parent in pending:
            for pid in children(parent["pid"]):
                if pid == os.getpid():
                    continue  # The host-side lock holder is infrastructure, not a task tool.
                try:
                    fp = fingerprint(pid)
                    if not fp or (pid, fp["birth"], fp["argv_sha256"]) in seen:
                        continue
                    child = self.register(key, pid, "task-descendant", parent)
                    pending.append(child)
                    seen.add((pid, child["birth"], child["argv_sha256"]))
                except (ValueError, OSError):
                    pass
        return self.get(key)

    def cleanup(self, key, root_role=None):
        row = self.get(key)
        out = []
        for p in reversed(row.get("processes", [])):
            if p.get("role") == "acp" or not p.get("temporary") or root_role and p.get("root_role") != root_role:
                continue
            status = "dead-or-identity-mismatch"
            from dwarfstar.runtime_control import identity

            current = identity(p.get("pid"))
            if (
                current
                and browser_process(current["command"])
                and (not p.get("automation_browser") or not automation_profile(current["command"], row, p))
            ):
                continue  # Never signal normal Chrome, even if it was launched by a task.
            if matches(p):
                try:
                    os.kill(p["pid"], signal.SIGTERM)
                    status = "SIGTERM"
                except ProcessLookupError:
                    pass
            out.append({**p, "cleanup_status": status, "alive": matches(p)})
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and any(matches(p) for p in out):
            time.sleep(0.05)
        for p in out:
            if p["cleanup_status"] == "SIGTERM" and matches(p):
                try:
                    os.kill(p["pid"], signal.SIGKILL)
                    p["cleanup_status"] = "SIGKILL"
                except ProcessLookupError:
                    pass
            p["alive"] = matches(p)
        with lock(self.root / "tasks.lock"):
            row = self.get(key)
            lookup = {(p["pid"], p["birth"], p["argv_sha256"]): p for p in out}
            row["processes"] = [
                {**lookup.get((p["pid"], p["birth"], p["argv_sha256"]), p), "alive": matches(p)} for p in row.get("processes", [])
            ]
            atomic(self.path(key), row)
        if root_role is None:
            self.release(key)
        return out

    def reserve(self, key, purpose, preferred=0, process=None):
        if not self.get(key):
            raise ValueError("Unknown task")
        with lock(self.root / "ports.lock"):
            ports = read(self.root / "ports.json", {"schema": 1, "reservations": []})
            used = {x["port"] for x in ports["reservations"] if not x.get("released_at")}
            while True:
                s = socket.socket()
                try:
                    try:
                        s.bind(("127.0.0.1", preferred if preferred not in used else 0))
                    except OSError:
                        s.bind(("127.0.0.1", 0))
                    port = s.getsockname()[1]
                    if port not in used:
                        break
                finally:
                    if "port" not in locals() or port in used:
                        s.close()
                preferred = 0
            try:
                item = {
                    "task_id": key,
                    "run_id": self.get(key).get("run_id"),
                    "port": port,
                    "host": "127.0.0.1",
                    "purpose": purpose,
                    "process": process,
                    "reserved_at": now(),
                    "released_at": None,
                    "kind": "advisory; bind must still handle unrelated races",
                }
                ports["reservations"].append(item)
                atomic(self.root / "ports.json", ports)
            finally:
                s.close()
            self.update(key, ports=[x for x in ports["reservations"] if x["task_id"] == key])
            return item

    def release(self, key):
        with lock(self.root / "ports.lock"):
            ports = read(self.root / "ports.json", {"schema": 1, "reservations": []})
            before = json.dumps(ports, sort_keys=True)
            for p in ports["reservations"]:
                if p["task_id"] == key and not p.get("released_at"):
                    p["released_at"] = now()
            if json.dumps(ports, sort_keys=True) != before:
                atomic(self.root / "ports.json", ports)
        latest = [p for p in ports["reservations"] if p["task_id"] == key]
        if self.get(key).get("ports") != latest:
            self.update(key, ports=latest)

    def observe_ports(self, key):
        row = self.get(key)
        live = [x for x in row.get("processes", []) if matches(x)]
        if not live:
            return
        data = subprocess.run(
            ["/usr/sbin/lsof", "-nP", "-a", "-p", ",".join(str(x["pid"]) for x in live), "-iTCP", "-sTCP:LISTEN", "-Fpn"],
            text=True,
            capture_output=True,
            timeout=3,
        ).stdout
        pid = None
        observed = []
        for line in data.splitlines():
            if line.startswith("p"):
                pid = int(line[1:])
            if line.startswith("n") and pid:
                import re

                m = re.search(r"^(?:n127\.0\.0\.1|n\[::1\]):(\d+)$", line)
                if m and (p := next((p for p in live if p["pid"] == pid), None)) and matches(p):
                    observed.append((int(m[1]), p))
        if not observed:
            return
        with lock(self.root / "ports.lock"):
            ports = read(self.root / "ports.json", {"schema": 1, "reservations": []})
            before = json.dumps(ports, sort_keys=True)
            for port, p in observed:
                existing = next((x for x in ports["reservations"] if not x.get("released_at") and x["port"] == port), None)
                if existing:
                    if existing["task_id"] == key:
                        existing["process"] = p
                else:
                    ports["reservations"].append(
                        {
                            "task_id": key,
                            "port": port,
                            "process": p,
                            "purpose": "observed task listener",
                            "reserved_at": now(),
                            "released_at": None,
                        }
                    )
            if json.dumps(ports, sort_keys=True) != before:
                atomic(self.root / "ports.json", ports)
        latest = [p for p in ports["reservations"] if p["task_id"] == key]
        if row.get("ports") != latest:
            self.update(key, ports=latest)

    def classify(self, row):
        roots = [p for p in row.get("processes", []) if p.get("role") == "acp"]
        live = any(matches(p) for p in roots) or matches(row.get("acp_process"))
        launch = row.get("launcher")
        if launch:
            from dwarfstar.runtime_control import identity

            current = identity(launch.get("pid"))
            live = live or bool(
                current
                and current["birth"] == launch.get("birth")
                and (not launch.get("native_birth") or native_birth(launch["pid"]) == launch["native_birth"])
            )
        if live:
            return "active/recoverable"
        if row.get("orchestration_owner") and not row.get("finished_at") and matches(row["orchestration_owner"]):
            return "active/recoverable"
        if row.get("status") in {"COMPLETE", "COMPLETED"}:
            return "completed"
        if row.get("status") == "CANCELLED":
            return "cancelled"
        if row.get("status") == "FAILED":
            return "failed"
        if any(matches(p) for p in row.get("processes", [])):
            return "stale/orphaned"
        return "inactive/unfinished"

    def reconcile(self, cleanup=False):
        rows, errors = self.all()
        out = []
        for row in rows:
            state = self.classify(row)
            if cleanup and state != "active/recoverable":
                try:
                    self.cleanup(row["task_id"])
                except Exception as e:
                    errors.append({"task_id": row["task_id"], "error": str(e)})
            processes = [
                {**p, "alive": matches(p), "orphaned": state != "active/recoverable" and matches(p)} for p in row.get("processes", [])
            ]
            out.append(
                {
                    **row,
                    "processes": processes,
                    "classification": state,
                    "resume_enabled": state in {"inactive/unfinished", "failed", "cancelled"} and Path(row["workspace"]).is_dir(),
                }
            )
        # Stale reservations are released only when neither their owner nor their
        # recorded listener is live. No occupying external process is signalled.
        if cleanup:
            try:
                ports = read(self.root / "ports.json", {"reservations": []})
            except (OSError, ValueError) as e:
                errors.append({"path": str(self.root / "ports.json"), "error": str(e)})
                ports = {"reservations": []}
            for key in {p["task_id"] for p in ports["reservations"] if not p.get("released_at")}:
                owner = next((x for x in out if x["task_id"] == key), None)
                reservations = [p for p in ports["reservations"] if p["task_id"] == key and not p.get("released_at")]
                if owner and owner["classification"] != "active/recoverable" and not any(matches(p.get("process")) for p in reservations):
                    self.release(key)
        # Preserve live/advisory reservations; release only owners positively inactive.
        return {"ok": not errors, "tasks": out, "errors": errors}


def safe_sync(row, store=STATE):
    """Supplemental state write must never fail a live benchmark turn."""
    try:

        def redacted(value):
            if isinstance(value, dict):
                return {k: redacted(v) for k, v in value.items() if k != "command"}
            if isinstance(value, list):
                return [redacted(v) for v in value]
            return value

        fields = redacted({k: v for k, v in row.items() if k not in {"benchmark"}})
        fields["original_prompt_reference"] = str(Path(row["artifact_directory"]) / "prompt.txt")
        fields["run_id"] = row["run_id"]
        fields["zed_workspace_mapping"] = {"workspace": row["workspace"], "acp_session_id": row.get("acp_session_id")}
        if row.get("group_id"):
            fields["benchmark_sequence_reference"] = str(
                Path(row["artifact_directory"]).parents[1] / "groups" / row["group_id"] / "group.json"
            )
        task = Store(store)
        old = task.get(row["run_id"]) or {}  # noqa: F841 (kept from the tested implementation)
        # Guard owns these arrays; run snapshots must not erase them.
        for k in ("processes", "ports", "launcher"):
            fields.pop(k, None)
        if row.get("benchmark_id") == "interactive-task":
            fields["run_id"] = None
        task.create(
            row["run_id"], row["workspace"], row["profile_id"], **{k: v for k, v in fields.items() if k not in ("workspace", "profile_id")}
        )
    except Exception as e:
        print("DwarfStar supplemental task-state warning: " + str(e), file=sys.stderr)


RELEASE_WAIT_S = 6


def admit(workspace, key, profile, metrics, journal, state=None):
    """Before exec, hand the workspace lock to an isolated passive owner holder."""
    task = Store(state)
    workspace = str(Path(workspace).resolve())
    p = task.root / "workspace-locks" / digest(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    f = p.open("a")
    # The previous owner's guard notices its agent exited within one 2 s poll and then releases the lock, so a turn
    # that follows another at once waits briefly. A live owner (an editor's open agent) keeps it and is refused.
    until = time.monotonic() + RELEASE_WAIT_S
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= until:
                f.close()
                raise RuntimeError("A live ACP agent already owns this workspace; duplicate session refused")
            time.sleep(0.1)
    from dwarfstar.runtime_control import identity

    launch = identity(os.getpid())
    launch.pop("command", None)
    launch["native_birth"] = native_birth(os.getpid())
    for other in task.all()[0]:
        if other.get("workspace") == workspace and other["task_id"] != key and task.classify(other) == "active/recoverable":
            f.close()
            raise RuntimeError("Workspace has another registered live agent")
    task.create(
        key,
        workspace,
        profile,
        status="STARTING",
        launcher=launch,
        telemetry_references={"journal": str(journal), "scalar_request": str(metrics)},
    )
    ready = task.root / "ready" / (key + "-" + uuid.uuid4().hex + ".json")
    child = subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(Path(__file__)),
            "_guard",
            key,
            "--fd",
            str(f.fileno()),
            "--ready",
            str(ready),
            "--state-root",
            str(task.root),
        ],
        pass_fds=(f.fileno(),),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    until = time.monotonic() + 3
    while not ready.exists() and child.poll() is None and time.monotonic() < until:
        time.sleep(0.025)
    f.close()
    if not ready.exists():
        raise RuntimeError("Workspace ownership holder unavailable; no ACP started")
    ready.unlink()
    return key


def guard(key, fd, ready, state=None):
    holder = os.fdopen(fd, "a")
    task = Store(state)
    row = task.get(key)
    launch = row["launcher"]
    root = None
    last_sig = None
    offset = 0
    atomic(ready, {"ready": True, "pid": os.getpid()})
    from dwarfstar.runtime_control import identity

    while True:
        ended = False
        try:
            current = identity(launch["pid"])
            ended = (
                not current
                or current["birth"] != launch["birth"]
                or bool(launch.get("native_birth") and native_birth(launch["pid"]) != launch["native_birth"])
            )
            # One known exec handoff only. Never accept arbitrary subsequent argv changes.
            if not ended and root is None and "omp" in current["command"] and " acp " in current["command"]:
                root = task.register(key, launch["pid"], "acp", root=True)
                task.update(key, status="RUNNING", acp_process=root)
            if root and matches(root):
                task.discover(key)
                task.observe_ports(key)
            row = task.get(key)
            scalar = read(row["telemetry_references"]["scalar_request"], {}) or {}
            timing = scalar.get("session_timing", {})
            sig = (timing.get("session_id"), timing.get("recoveries"), timing.get("last_classification"))
            if sig != last_sig:
                task.update(
                    key,
                    acp_session_id=timing.get("session_id"),
                    elapsed_timing=timing,
                    recovery_count=timing.get("recoveries"),
                    phase=timing.get("phase"),
                )
                last_sig = sig
            journal = Path(row["telemetry_references"]["journal"])
            if journal.exists():
                with journal.open("rb") as f:
                    f.seek(offset)
                    data = f.read(262144)
                    end = data.rfind(b"\n")
                    if end >= 0:
                        offset += end + 1
                        for line in data[:end].splitlines():
                            try:
                                e = json.loads(line)
                            except ValueError:
                                continue
                            if e.get("event") in {
                                "request_start",
                                "first_functional_state",
                                "recovery_started",
                                "finish_nudge_sent",
                                "task_summary",
                            }:
                                updates = {"last_transition": e}
                                if e["event"] == "request_start":
                                    updates["status"] = "RUNNING"
                                if e["event"] == "first_functional_state":
                                    updates["first_functional"] = e
                                if e["event"] == "finish_nudge_sent":
                                    updates["finish_nudge"] = e
                                if e["event"] == "task_summary":
                                    terminal = e.get("summary", {}).get("terminal_state")
                                    updates["last_summary"] = e.get("summary")
                                    if not row.get("run_id"):
                                        updates["status"] = (
                                            "COMPLETE"
                                            if terminal == "completed_turn"
                                            else "CANCELLED"
                                            if terminal == "cancelled"
                                            else "INACTIVE"
                                        )
                                task.update(key, **updates)
            if ended:
                break
        except Exception as e:
            # Collector/registry loss must not signal or stop the agent.
            try:
                atomic(task.root / "guard-warnings" / (key + ".json"), {"at": now(), "error": str(e)})
            except Exception:
                pass
        if ended:
            break
        time.sleep(2)
    try:
        row = task.get(key)
        if row.get("status") not in TERMINAL:
            task.update(key, status="INACTIVE", disconnected_at=now())
        task.cleanup(key, root_role="acp")
        if not row.get("run_id"):
            task.release(key)
    except Exception:
        pass
    holder.close()


def retention(root=ROOT / "benchmarks", days=30, apply=False):
    """Only explicitly registered disposable files outside benchmark evidence trees."""
    root = Path(root)
    catalog = read(root / "temporary-artifacts.json", {"artifacts": []})
    out = []
    protected = []

    def references(value):
        if isinstance(value, dict):
            for v in value.values():
                references(v)
        elif isinstance(value, list):
            for v in value:
                references(v)
        elif isinstance(value, str) and value.startswith("/"):
            protected.append(Path(value).resolve())

    index = read(root / "index.json", {})
    references(index)
    for run in index.get("runs", []):
        for path in root.glob("*/" + ident(run["run_id"]) + "/run.json"):
            protected.append(path.parent.resolve())
            references(read(path, {}))
    for item in catalog["artifacts"]:
        p = Path(item["path"])
        base = root / "disposable"
        if p.is_symlink() or not p.exists() or not p.is_file() or not p.resolve().is_relative_to(base.resolve()):
            continue
        rel = p.relative_to(root)
        if any((root / Path(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts))):
            continue
        if any(p.resolve() == x or p.resolve().is_relative_to(x) for x in protected):
            continue
        if any(word in p.name.lower() for word in ["final", "comparison", "config-snapshot", "summary"]):
            continue
        if not item.get("disposable") or item.get("final_evidence") or item.get("active"):
            continue
        # No indexed run artifact may be registered for retention at all.
        if time.time() - p.stat().st_mtime < max(1, days) * 86400:
            continue
        out.append({"path": str(p), "bytes": p.stat().st_size, "removed": apply})
        if apply:
            p.unlink()
    return {
        "ok": True,
        "apply": apply,
        "minimum_age_days": max(1, days),
        "candidates": out,
        "recovered_bytes": sum(x["bytes"] for x in out) if apply else 0,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "action",
        choices=["tasks", "state", "processes", "ports", "port", "release", "reconcile", "preflight", "retention", "resume", "_guard"],
    )
    p.add_argument("task", nargs="?")
    p.add_argument("--workspace")
    p.add_argument("--profile", default="scope")
    p.add_argument("--preferred", type=int, default=0)
    p.add_argument("--purpose", default="temporary localhost")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--fd", type=int)
    p.add_argument("--ready")
    p.add_argument("--state-root")
    a = p.parse_args()
    s = Store()
    if a.action == "_guard":
        guard(a.task, a.fd, a.ready, a.state_root)
        return
    if a.action == "preflight":
        from dwarfstar.preflight import check

        value = check(a.workspace or str(ROOT / "state"), a.profile)
    elif a.action == "retention":
        value = retention(days=a.days, apply=a.apply)
    elif a.action == "resume":
        sys.path.insert(0, str(CODE / "benchmark-runner"))
        from runner import resume_task

        value = resume_task(a.task)
    elif a.action == "port":
        value = s.reserve(a.task, a.purpose, a.preferred)
    elif a.action == "release":
        s.release(a.task)
        value = {"ok": True}
    else:
        value = s.reconcile(cleanup=a.action == "reconcile")
        if a.task:
            value["tasks"] = [r for r in value["tasks"] if r["task_id"] == a.task]
        if a.action == "processes":
            value = {"ok": value["ok"], "processes": [p for r in value["tasks"] for p in r.get("processes", [])], "errors": value["errors"]}
        if a.action == "ports":
            value = {"ok": True, **read(s.root / "ports.json", {"reservations": []})}
    print(json.dumps(value))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)
