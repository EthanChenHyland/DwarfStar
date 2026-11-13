"""Real-runtime lifecycle validation through the Workstation service, the way the UI drives it.

Every start and stop is submitted as an asynchronous job (`wait: false`) and followed through
`/api/state`, exactly like the window does. After each step the script checks the process table,
the controller's ownership ledger, the endpoint, the service's active profile, the profile's
reasoning configuration and the telemetry session.

    lifecycle_matrix.py matrix            each of the seven profiles: start from idle, check, stop, check
    lifecycle_matrix.py regression        cyber-medium -> stop -> cyber-low (the reported failure)
    lifecycle_matrix.py same-family       Swift LOW->MEDIUM->LOW and Cyber OFF->LOW->MEDIUM->XHIGH->LOW, hot and after stop
    lifecycle_matrix.py cross-family      Scope LOW -> Swift LOW -> Swift MEDIUM -> Cyber OFF/LOW/MEDIUM/XHIGH -> Scope LOW

Results are appended to <data>/evidence/production-validation/steps/09-lifecycle-<name>.json.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "src"))

from dwarfstar import catalog  # noqa: E402
from dwarfstar.paths import DATA, instance  # noqa: E402

SERVICE = "http://127.0.0.1:8038"
STEPS = DATA / "evidence" / "production-validation" / "steps"


def call(path: str, body: dict | None = None, timeout: float = 60) -> dict:
    request = urllib.request.Request(SERVICE + path, data=json.dumps(body).encode() if body is not None else None,
                                     method="POST" if body is not None else "GET", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        return json.loads(error.read() or b"{}")


def job(path: str, body: dict) -> dict:
    """Submit like the UI (no waiting on the request), then follow the service's record of the job."""
    started = time.time()
    accepted = call(path, {**body, "wait": False})
    if not accepted.get("ok"):
        return {"ok": False, "error": accepted.get("error"), "wall_s": 0}
    while True:
        time.sleep(0.5)
        current = call("/api/state")["lifecycle"]
        if current["job"] == accepted["job"] and not current["busy"]:
            return {**(current.get("result") or {}), "wall_s": round(time.time() - started, 2), "steps": [s["text"] for s in current["steps"]]}
        if time.time() - started > 900:
            return {"ok": False, "error": "job did not finish in 900 s"}


def servers() -> list[tuple[int, str]]:
    rows = subprocess.run(["/bin/ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout.splitlines()
    return [(int(r.split(None, 1)[0]), r.split(None, 1)[1]) for r in rows if "/ds4-server " in r]


def port_open(port: int) -> bool:
    import socket

    with socket.socket() as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def provider_body(profile_id: str) -> dict:
    """The reasoning parameters the profile's generated provider file sends on every request."""
    model_id = catalog.profile(profile_id)["model"]
    text = (instance(model_id) / "acp/providers" / f"{profile_id}.yml").read_text()
    body = {"reasoning_effort": re.search(r"^\s+reasoning_effort: (\w+)", text, re.M)[1]}
    kwargs = re.search(r"chat_template_kwargs: \{([^}]*)\}", text)[1]
    body["chat_template_kwargs"] = {k.strip(): (v.strip() == "true" if v.strip() in ("true", "false") else v.strip())
                                    for k, v in (item.split(":") for item in kwargs.split(","))}
    return body


def reasoning_probe(profile_id: str) -> dict:
    """One-token request carrying the profile's own reasoning parameters: proves the runtime accepts that configuration."""
    model = catalog.model(catalog.profile(profile_id)["model"])
    extra = provider_body(profile_id)
    body = {"model": model["served_model_id"], "messages": [{"role": "user", "content": "Say OK."}], "max_tokens": 1, **extra}
    request = urllib.request.Request(f"http://127.0.0.1:{model['port']}/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            reply = json.loads(response.read())
        return {"ok": bool(reply.get("choices")), "sent": extra}
    except urllib.error.HTTPError as error:
        return {"ok": False, "sent": extra, "error": error.read()[:200].decode(errors="replace")}


def check_running(profile_id: str, expect_pid: int | None = None) -> dict:
    model_id = catalog.profile(profile_id)["model"]
    model = catalog.model(model_id)
    state = call("/api/state")
    row = state["models"][model_id]
    procs = servers()
    ledger = json.loads((instance(model_id) / "state/server.json").read_text()) if (instance(model_id) / "state/server.json").is_file() else {}
    checks = {
        "one_server": len(procs) == 1,
        "server_is_model": bool(procs) and model["assets"]["main"]["path"].split("/")[-1] in procs[0][1],
        "pid_matches": bool(procs) and row.get("pid") == procs[0][0] == ledger.get("pid"),
        "healthy": row.get("healthy") is True,
        "running_models": state["running_models"] == [model_id],
        "active_profile": state.get("active_profile") == profile_id,
        "selected_profile": state.get("selected_profile") == profile_id,
        "lifecycle_idle": state["lifecycle"]["busy"] is False,
        "context": f"--ctx {model['context']} " in (procs[0][1] if procs else ""),
    }
    if expect_pid is not None:
        checks["no_restart"] = row.get("pid") == expect_pid
    probe = reasoning_probe(profile_id)
    checks["reasoning_config_accepted"] = probe["ok"]
    return {"profile": profile_id, "pid": row.get("pid"), "ok": all(checks.values()), "checks": checks, "reasoning_sent": probe["sent"],
            **({"probe_error": probe["error"]} if not probe["ok"] else {})}


def check_stopped(model_id: str, old_pid: int | None) -> dict:
    port = catalog.model(model_id)["port"]
    state = call("/api/state")
    live = call("/api/live")
    checks = {
        "old_pid_gone": old_pid is None or all(pid != old_pid for pid, _ in servers()),
        "no_server": not servers(),
        "port_closed": not port_open(port),
        "ledger_cleared": not (instance(model_id) / "state/server.json").exists(),
        "health_cleared": state["models"][model_id]["healthy"] is False,
        "nothing_running": state["running_models"] == [],
        "active_profile_cleared": state.get("active_profile") is None,
        "lifecycle_idle": state["lifecycle"]["busy"] is False,
        "telemetry_session_closed": live["live"]["hub"].get("session") is None,
    }
    return {"model": model_id, "ok": all(checks.values()), "checks": checks}


class Run:
    def __init__(self, name: str):
        self.name = name
        self.rows: list[dict] = []

    def switch(self, profile_id: str, expect_pid: int | None = None) -> dict:
        result = job("/api/switch", {"profile": profile_id})
        row = {"action": f"start {profile_id}" if expect_pid is None else f"hot switch {profile_id}", "job_ok": result.get("ok"),
               "error": result.get("error"), "wall_s": result.get("wall_s"), "steps": result.get("steps")}
        if result.get("ok"):
            time.sleep(0.5)
            row.update(check_running(profile_id, expect_pid))
        else:
            row["ok"] = False
        self.rows.append(row)
        print(f"{row['action']:<32} {'PASS' if row['ok'] else 'FAIL'} {row.get('wall_s')}s pid={row.get('pid')} "
              f"{'' if row['ok'] else json.dumps(row.get('checks') or row.get('error'))}", flush=True)
        return row

    def stop(self) -> dict:
        state = call("/api/state")
        running = state["running_models"]
        old_pid = state["models"][running[0]]["pid"] if running else None
        result = job("/api/stop", {})
        row = {"action": "stop", "job_ok": result.get("ok"), "error": result.get("error"), "wall_s": result.get("wall_s")}
        time.sleep(1.0)
        row.update(check_stopped(running[0] if running else "scope", old_pid))
        row["ok"] = bool(result.get("ok")) and row["ok"]
        self.rows.append(row)
        print(f"{'stop':<32} {'PASS' if row['ok'] else 'FAIL'} {row.get('wall_s')}s {'' if row['ok'] else json.dumps(row['checks'])}", flush=True)
        return row

    def save(self) -> None:
        STEPS.mkdir(parents=True, exist_ok=True)
        record = {"step": f"09-lifecycle-{self.name}", "kind": "lifecycle", "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "passed": sum(1 for r in self.rows if r["ok"]), "total": len(self.rows), "rows": self.rows}
        (STEPS / f"09-lifecycle-{self.name}.json").write_text(json.dumps(record, indent=1) + "\n")
        print(f"{self.name}: {record['passed']}/{record['total']} passed", flush=True)


def main(argv: list[str]) -> int:
    name = argv[0] if argv else "matrix"
    run = Run(name)
    if call("/api/state")["running_models"]:
        run.stop()
    if name == "matrix":
        for profile in catalog.profiles():
            run.switch(profile["id"])
            run.stop()
    elif name == "regression":
        run.switch("cyber-medium")
        run.stop()
        run.switch("cyber-low")
        run.stop()
    elif name == "same-family":
        first = run.switch("swift-low")
        run.switch("swift-medium", expect_pid=first.get("pid"))
        run.switch("swift-low", expect_pid=first.get("pid"))
        run.stop()
        run.switch("swift-medium")
        run.stop()
        first = run.switch("cyber-off")
        for effort in ("low", "medium", "xhigh", "low"):
            run.switch(f"cyber-{effort}", expect_pid=first.get("pid"))
        run.stop()
        run.switch("cyber-xhigh")
        run.stop()
    elif name == "cross-family":
        for profile in ("scope-low", "swift-low", "swift-medium", "cyber-off", "cyber-low", "cyber-medium", "cyber-xhigh", "scope-low"):
            run.switch(profile)
        run.stop()
    run.save()
    return 0 if all(r["ok"] for r in run.rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
