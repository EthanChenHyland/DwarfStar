"""Minimal ACP client for tests: one session, a list of prompt turns, a full record.

The client advertises no file-system or terminal capabilities, so the agent
must use its own tools. Permission requests are counted (Full Auto expects
none) and answered with the product's own resolver so a test never hangs.

    from acp_client import run_session
    result = run_session(out_dir, ["bin/dwarfstar-acp", "--profile", "cyber-low"], workspace, [{"text": "..."}])
"""
from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))


def _descendants(pid: int) -> list[str]:
    rows = subprocess.run(["/bin/ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True).stdout.splitlines()
    table = []
    for row in rows:
        parts = row.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            table.append((int(parts[0]), int(parts[1]), row))
    found, changed = {pid}, True
    while changed:
        changed = False
        for child, parent, _ in table:
            if parent in found and child not in found:
                found.add(child)
                changed = True
    return [row for child, _, row in table if child in found]


def run_session(out, launcher, workspace, turns, turn_timeout=900, extra_env=None, task_id=None):
    from dwarfstar.approval_policy import resolve_request

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    workspace = Path(workspace)
    wire = out / "wire-receipt.jsonl"
    wire.unlink(missing_ok=True)
    env = {
        "HOME": str(Path.home()),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "en_US.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "DWARFSTAR_WIRE_RECEIPT": str(wire),
        "DWARFSTAR_TASK_ID": task_id or f"test-{out.name}-{int(time.time())}",
    }
    for key in ("DWARFSTAR_DATA", "DWARFSTAR_TELEMETRY", "DWARFSTAR_TELEMETRY_PORT"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    env.update(extra_env or {})
    stderr = (out / "acp-stderr.log").open("w")
    launched = time.time()
    command = [str(part) for part in launcher] + ["--workspace", str(workspace)]
    if not os.path.isabs(command[0]) and (REPO / command[0]).exists():
        command[0] = str(REPO / command[0])
    process = subprocess.Popen(command, cwd=str(workspace), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=stderr, text=True, bufsize=1, start_new_session=True)
    messages: queue.Queue = queue.Queue()
    events = (out / "acp-events.jsonl").open("w")
    lock = threading.Lock()

    def log(record):
        with lock:
            events.write(json.dumps({"t": time.time(), **record}) + "\n")
            events.flush()

    def reader():
        for line in process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                message = {"__non_protocol": line[:2000]}
            log({"received": message})
            messages.put(message)
        messages.put({"__eof": True})

    threading.Thread(target=reader, daemon=True).start()
    permission_requests, other_requests, updates, sequence = [], [], [], [0]

    def send(value):
        log({"sent": value})
        process.stdin.write(json.dumps(value) + "\n")
        process.stdin.flush()

    def request(method, params, timeout):
        sequence[0] += 1
        ident = sequence[0]
        send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                message = messages.get(timeout=0.5)
            except queue.Empty:
                continue
            if message.get("__eof"):
                raise RuntimeError("ACP agent exited during " + method)
            if message.get("method") == "session/update":
                updates.append((message["params"].get("update", {}), time.time()))
                continue
            if "method" in message and "id" in message:
                if message["method"] == "session/request_permission":
                    permission_requests.append({"t": time.time(), "message": message})
                    send(resolve_request(message, "full_auto", str(out / "client-permission-fallbacks.jsonl")))
                else:
                    other_requests.append(message["method"])
                    send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "not supported by the test client"}})
                continue
            if message.get("id") == ident and "method" not in message:
                return {"__error": message["error"]} if "error" in message else message.get("result", {})
        return {"__error": f"timeout after {timeout}s"}

    result = {"launcher": command, "workspace": str(workspace), "acp_pid": process.pid, "turns": []}
    try:
        capabilities = {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}
        init = request("initialize", {"protocolVersion": 1, "clientCapabilities": capabilities,
                                      "clientInfo": {"name": "dwarfstar test client", "version": "1"}}, 180)
        result["initialize_ok"] = "__error" not in init
        session = request("session/new", {"cwd": str(workspace), "mcpServers": []}, 180)
        session_id = session.get("sessionId")
        result["session_id"] = session_id
        result["config_options"] = {o.get("id"): o.get("currentValue") for o in session.get("configOptions", []) or []}
        ps = subprocess.run(["/bin/ps", "eww", "-p", str(process.pid), "-o", "command="], capture_output=True, text=True).stdout
        environment = {}
        for key in ("DWARFSTAR_APPROVAL_MODE", "DWARFSTAR_PERMISSION_POLICY", "DWARFSTAR_PROFILE_ID", "DWARFSTAR_MODEL"):
            environment[key] = ps.split(key + "=", 1)[1].split(" ", 1)[0] if key + "=" in ps else None
        environment["thinking_flag"] = ps.split("--thinking ", 1)[1].split(" ", 1)[0] if "--thinking " in ps else None
        environment["approval_flag"] = "yolo" if "--approval-mode yolo" in ps else "always-ask" if "--approval-mode always-ask" in ps else None
        result["launch_environment"] = environment
        if not session_id:
            raise RuntimeError("no session: " + json.dumps(session)[:500])
        for index, turn in enumerate(turns):
            content = [{"type": "text", "text": turn["text"]}]
            if turn.get("image"):
                data = base64.b64encode(Path(turn["image"]).read_bytes()).decode()
                content.append({"type": "image", "mimeType": "image/png", "data": data})
            first, started = len(updates), time.time()
            stop = request("session/prompt", {"sessionId": session_id, "prompt": content}, turn.get("timeout", turn_timeout))
            ended = time.time()
            chunk = [update for update, _ in updates[first:]]
            text = "".join((u.get("content") or {}).get("text", "") for u in chunk if u.get("sessionUpdate") == "agent_message_chunk")
            thought = "".join((u.get("content") or {}).get("text", "") for u in chunk if u.get("sessionUpdate") == "agent_thought_chunk")
            calls: dict = {}
            for update in chunk:
                if update.get("sessionUpdate") in ("tool_call", "tool_call_update"):
                    call = calls.setdefault(update.get("toolCallId"), {"toolCallId": update.get("toolCallId")})
                    for key in ("title", "kind", "status"):
                        if update.get(key) is not None:
                            call[key] = update[key]
            result["turns"].append({
                "index": index, "prompt": turn["text"], "stop": stop, "elapsed_s": ended - started, "reply": text,
                "thought_chars": len(thought), "thought_tail": thought[-400:], "tool_calls": list(calls.values()),
            })
            (out / "run.json").write_text(json.dumps(result, indent=1) + "\n")
    except Exception as error:  # the record is the result; never lose it
        result["exception"] = repr(error)
    finally:
        try:
            process.stdin.close()
        except Exception:
            pass
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        time.sleep(0.5)
        result["descendants_alive_after"] = _descendants(process.pid)[1:]
        result["agent_exit"] = process.returncode
        result["wall_s"] = time.time() - launched
        events.close()
    rows = [json.loads(line) for line in wire.read_text().splitlines()] if wire.exists() else []
    result["wire"] = rows
    result["efforts_on_wire"] = sorted({str(row.get("reasoning_effort")) for row in rows})
    result["requests"] = len(rows)
    result["client_permission_requests"] = len(permission_requests)
    result["client_other_requests"] = other_requests
    (out / "run.json").write_text(json.dumps(result, indent=1) + "\n")
    return result
