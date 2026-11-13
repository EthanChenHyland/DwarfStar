"""Unified production validation: the small, budgeted runtime pass.

Every step drives the product the way a user does: models are switched through
the Workstation service (``POST /api/switch``), agents run through
``bin/dwarfstar-acp --profile``, and telemetry is read from the service's own
event stream. Each step writes one JSON file; ``summarize`` turns them into
``<data>/evidence/production-validation/validation.json`` for the benchmark index.

    production_validation.py switch PROFILE
    production_validation.py smoke STEP PROFILE
    production_validation.py depth STEP PROFILE WORDS
    production_validation.py acp STEP PROFILE SCENARIO
    production_validation.py overhead STEP PROFILE
    production_validation.py summarize
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE))

from api_client import chat  # noqa: E402
from dwarfstar import catalog  # noqa: E402
from dwarfstar.paths import EVIDENCE, scratch_root  # noqa: E402

OUT = EVIDENCE / "production-validation"
STEPS = OUT / "steps"
SERVICE = "http://127.0.0.1:8038"


def write_step(step: str, record: dict) -> Path:
    STEPS.mkdir(parents=True, exist_ok=True)
    path = STEPS / f"{step}.json"
    record = {"step": step, "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **record}
    path.write_text(json.dumps(record, indent=1) + "\n")
    print(json.dumps({k: v for k, v in record.items() if k not in ("raw", "telemetry_events", "turns")}, indent=1)[:4000])
    return path


def service(method: str, path: str, body: dict | None = None, timeout: float = 900) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(SERVICE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        return json.loads(error.read() or b"{}")


def switch(profile: str) -> dict:
    started = time.time()
    result = service("POST", "/api/switch", {"profile": profile})
    result["switch_wall_s"] = round(time.time() - started, 2)
    return result


def running_status(model: str) -> dict:
    out = subprocess.run([str(REPO / "bin/dwarfstar"), "runtime", "--model", model, "status"], capture_output=True, text=True, timeout=60)
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": out.stderr[-400:]}


# ------------------------------------------------------------------ sampling


class WiredSampler:
    """System wired memory every two seconds (the figure the Metal working set shows up in)."""

    def __init__(self):
        self.peak_gib = 0.0
        self.samples = 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def loop(self):
        while not self.stop.is_set():
            try:
                text = subprocess.run(["/usr/bin/vm_stat"], capture_output=True, text=True, timeout=10).stdout
                page = int(text.split("page size of ")[1].split(" ")[0])
                wired = int([line for line in text.splitlines() if line.startswith("Pages wired down")][0].split(":")[1].strip(" ."))
                self.peak_gib = max(self.peak_gib, wired * page / 2**30)
                self.samples += 1
            except Exception:
                pass
            self.stop.wait(2.0)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=5)


class EventWatcher:
    """Subscribes to the Workstation event stream like the visualizer does and counts what arrives."""

    def __init__(self, level: int = 2):
        self.level = level
        self.counts: dict[str, int] = {}
        self.tick_fields = {"router_rows": 0, "mtp_proposed": 0, "mtp_accepted": 0, "ple_reads": 0, "ple_bytes": 0, "ticks": 0}
        self.tools: list[dict] = []
        self.request_phases: dict[str, int] = {}
        self.max_context = 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.error = None

    def loop(self):
        try:
            with urllib.request.urlopen(f"{SERVICE}/api/events?level={self.level}", timeout=30) as stream:
                for raw in stream:
                    if self.stop.is_set():
                        return
                    line = raw.decode(errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:])
                    kind = event.get("kind", "?")
                    self.counts[kind] = self.counts.get(kind, 0) + 1
                    if kind == "tick":
                        f = self.tick_fields
                        f["ticks"] += 1
                        f["router_rows"] += 1 if event.get("r") else 0
                        f["mtp_proposed"] += int(event.get("dp", 0))
                        f["mtp_accepted"] += int(event.get("da", 0))
                        reads, size = event.get("ple", [0, 0])
                        f["ple_reads"] += reads
                        f["ple_bytes"] += size
                        self.max_context = max(self.max_context, int(event.get("pos", 0)) + int(event.get("n", 0)))
                    elif kind == "request":
                        phase = event.get("phase", "?")
                        self.request_phases[phase] = self.request_phases.get(phase, 0) + 1
                    elif kind == "tool":
                        self.tools.append({k: event.get(k) for k in ("name", "category", "phase", "ok", "ms")})
        except Exception as error:  # the stream ends when the watcher is closed
            if not self.stop.is_set():
                self.error = repr(error)

    def __enter__(self):
        self.thread.start()
        time.sleep(1.5)  # let the hub's heartbeat raise the runtime's detail level
        return self

    def __exit__(self, *exc):
        time.sleep(1.5)
        self.stop.set()

    def summary(self) -> dict:
        return {"event_counts": self.counts, "tick": self.tick_fields, "request_phases": self.request_phases,
                "tool_events": self.tools, "max_context_seen": self.max_context, "error": self.error}


# --------------------------------------------------------------------- steps


def body(profile_id: str, messages: list[dict], max_tokens: int) -> dict:
    profile = catalog.profile(profile_id)
    model = catalog.model(profile["model"])
    return {"model": model["served_model_id"], "messages": messages, "max_tokens": max_tokens, "temperature": 0.0,
            "reasoning_effort": profile["reasoning_effort"]}


def port_of(profile_id: str) -> int:
    return catalog.model(catalog.profile(profile_id)["model"])["port"]


def smoke(step: str, profile_id: str) -> None:
    question = "What is 17 + 26? Reply with only the number."
    with WiredSampler() as wired, EventWatcher() as watcher:
        result = chat(port_of(profile_id), body(profile_id, [{"role": "user", "content": question}], 512), timeout=600)
    model = catalog.profile(profile_id)["model"]
    status = running_status(model)
    write_step(step, {
        "kind": "smoke", "profile": profile_id, "model": model, "task": "short arithmetic prompt, temperature 0",
        "acceptance": "43" in result["content"] and not result["errors"], "answer": result["content"].strip()[:200],
        "ttft_s": result["ttft_s"], "decode_tok_s": result["decode_tok_s"], "tokens": (result["usage"] or {}).get("completion_tokens"),
        "context": catalog.model(model)["context"], "peak_wired_gib": round(wired.peak_gib, 2),
        "memory_plan": status.get("memory_plan"), "telemetry": watcher.summary(), "errors": result["errors"],
    })


def depth_document(words: int) -> tuple[str, dict]:
    """Benign synthetic warehouse log with one needle fact (same generator as the 2026-11-12 Cyber depth test)."""
    rng = random.Random(words)
    items = ["copper wire", "oak planks", "glass jars", "wool blankets", "brass hinges", "paper reams", "clay pots", "steel bolts", "cotton rope", "tin cans"]
    staff = ["Alma", "Bruno", "Chen", "Dara", "Elif", "Femi", "Greta", "Hugo", "Ines", "Jonas"]
    rooms = ["north bay", "south bay", "loft", "cold store", "yard shed", "east annex"]
    paragraphs, count, index = [], 0, 0
    while count < words:
        index += 1
        sentence = (f"Entry {index}. On day {rng.randint(1, 365)} {rng.choice(staff)} moved {rng.randint(2, 400)} units of {rng.choice(items)} "
                    f"from the {rng.choice(rooms)} to the {rng.choice(rooms)}. The count was checked twice and the shelf label was updated to "
                    f"{rng.choice('ABCDEFGH')}{rng.randint(10, 99)}. Weather at the dock was {rng.choice(['clear', 'windy', 'rainy', 'foggy', 'cold'])}, "
                    f"and the forklift log shows {rng.randint(1, 12)} trips.")
        paragraphs.append(sentence)
        count += len(sentence.split())
    needle = "Special note: the archive room key code is 4719, and only Greta may change it."
    position = len(paragraphs) * 3 // 5
    paragraphs.insert(position, needle)
    question = ("Here is a warehouse log.\n\n" + "\n".join(paragraphs) +
                "\n\nQuestion: according to the log, what is the archive room key code, and who may change it? "
                "Answer that in the first sentence. Then, in about 150 words, describe in plain prose what kinds of items, rooms and weather appear in the log.")
    return question, {"words": count, "entries": index, "needle_entry": position}


def depth(step: str, profile_id: str, words: int) -> None:
    question, meta = depth_document(words)
    with WiredSampler() as wired, EventWatcher(level=1) as watcher:
        result = chat(port_of(profile_id), body(profile_id, [{"role": "user", "content": question}], 1500), timeout=7200)
    model = catalog.profile(profile_id)["model"]
    usage = result["usage"] or {}
    write_step(step, {
        "kind": "depth", "profile": profile_id, "model": model, "task": f"needle question over a ~{usage.get('prompt_tokens', 0) // 1000}K-token document",
        "document": meta, "prompt_tokens": usage.get("prompt_tokens"), "tokens": usage.get("completion_tokens"),
        "acceptance": "4719" in result["content"] and "Greta" in result["content"] and not result["errors"],
        "answer_head": result["content"].strip()[:300], "finish_reason": result["finish_reason"],
        "ttft_s": result["ttft_s"], "prefill_tok_s": result["prefill_tok_s"], "decode_tok_s": result["decode_tok_s"], "wall_s": result["total_s"],
        "context": catalog.model(model)["context"], "peak_wired_gib": round(wired.peak_gib, 2),
        "memory_plan": running_status(model).get("memory_plan"), "telemetry": watcher.summary(), "errors": result["errors"],
    })


def overhead(step: str, profile_id: str) -> None:
    """Same deterministic short workload with no telemetry subscriber, then with a full-detail visualizer subscriber."""
    messages = [{"role": "user", "content": "List the first twelve prime numbers, separated by commas, and nothing else."}]
    port = port_of(profile_id)
    chat(port, body(profile_id, messages, 400), timeout=600)  # warm
    runs = {"closed": [], "visualizer": []}
    for _ in range(2):
        runs["closed"].append(chat(port, body(profile_id, messages, 400), timeout=600))
        with EventWatcher(level=2) as watcher:
            runs["visualizer"].append(chat(port, body(profile_id, messages, 400), timeout=600))
    summary = {}
    for name, rows in runs.items():
        decode = [r["decode_tok_s"] for r in rows if r["decode_tok_s"]]
        summary[name] = {"decode_tok_s": decode, "mean_decode_tok_s": round(sum(decode) / len(decode), 2) if decode else None,
                         "ttft_s": [round(r["ttft_s"] or 0, 3) for r in rows], "identical_output": len({r["content"] for r in rows}) == 1}
    closed, open_ = summary["closed"]["mean_decode_tok_s"], summary["visualizer"]["mean_decode_tok_s"]
    delta = round((open_ - closed) / closed * 100, 2) if closed and open_ else None
    model = catalog.profile(profile_id)["model"]
    write_step(step, {
        "kind": "overhead", "profile": profile_id, "model": model, "task": "visualizer closed vs open, 2 runs each, temperature 0",
        "runs": summary, "decode_delta_pct": delta, "acceptance": delta is not None and abs(delta) < 5.0,
        "outputs_identical_across_modes": runs["closed"][0]["content"] == runs["visualizer"][0]["content"],
        "telemetry": watcher.summary(), "decode_tok_s": open_, "context": catalog.model(model)["context"],
    })


# ----------------------------------------------------------------- agent runs

SCENARIOS = {
    "tools": {
        "files": {"data.txt": "INFO start\nERROR disk\nINFO ok\nERROR net\nWARN slow\nERROR auth\nINFO done\n"},
        "prompt": ("Do these steps with your tools. 1) Use the shell to count the lines in data.txt that contain ERROR. "
                   "2) Write a file named result.txt containing only that number. 3) Reply with only the number."),
        "check": lambda ws, reply: (ws / "result.txt").is_file() and (ws / "result.txt").read_text().strip() == "3" and "3" in reply,
    },
    "factual": {
        "files": {},
        "prompt": "Which TCP port does HTTPS use by default? Reply with only the number.",
        "check": lambda ws, reply: reply.strip().strip(".") == "443",
    },
    "coding": {
        "files": {
            "slug.py": "def slugify(text):\n    return text.lower().replace(' ', '_')\n",
            "test_slug.py": ("import unittest\nfrom slug import slugify\n\n\nclass T(unittest.TestCase):\n"
                             "    def test_basic(self):\n        self.assertEqual(slugify('Hello World'), 'hello-world')\n\n"
                             "    def test_strip(self):\n        self.assertEqual(slugify('  Many   Spaces  '), 'many-spaces')\n\n\n"
                             "if __name__ == '__main__':\n    unittest.main()\n"),
        },
        "prompt": ("The tests in test_slug.py fail. Fix slug.py so that `python3 -m unittest test_slug` passes. "
                   "Do not change the test file. Run the tests to confirm, then reply DONE."),
        "check": lambda ws, reply: subprocess.run(["/usr/bin/python3", "-B", "-m", "unittest", "test_slug"], cwd=ws, capture_output=True).returncode == 0
        and "self.assertEqual(slugify('Hello World'), 'hello-world')" in (ws / "test_slug.py").read_text(),
    },
    "tool": {
        "files": {},
        "prompt": ("Use the shell to run `python3 -c \"print(6*7)\"`, then create a file named answer.txt containing exactly the printed "
                   "output. Reply with only the number."),
        "check": lambda ws, reply: (ws / "answer.txt").is_file() and (ws / "answer.txt").read_text().strip() == "42" and "42" in reply,
    },
    "review": {
        "files": {
            "app.py": ("import sqlite3\nfrom pathlib import Path\n\nBASE = Path('/srv/files')\n\n\n"
                       "def find_user(db, name):\n    cursor = db.execute(\"SELECT id, email FROM users WHERE name = '\" + name + \"'\")\n"
                       "    return cursor.fetchone()\n\n\ndef read_upload(filename):\n    return (BASE / filename).read_text()\n\n\n"
                       "def add(a, b):\n    return a + b\n"),
        },
        "prompt": ("Review app.py for security defects. For each defect give the function name, the kind of vulnerability and a "
                   "one-line fix. Do not modify any file."),
        "check": lambda ws, reply: ("find_user" in reply and "inject" in reply.lower()
                                    and "read_upload" in reply and "travers" in reply.lower()),
    },
}


def acp(step: str, profile_id: str, scenario_name: str) -> None:
    from acp_client import run_session

    scenario = SCENARIOS[scenario_name]
    workspace = scratch_root() / "production-validation" / step
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    for name, text in scenario["files"].items():
        (workspace / name).write_text(text)
    out = OUT / "sessions" / step
    with WiredSampler() as wired, EventWatcher(level=2) as watcher:
        result = run_session(out, [str(REPO / "bin/dwarfstar-acp"), "--profile", profile_id], workspace,
                             [{"text": scenario["prompt"]}], turn_timeout=900, task_id=f"production-validation-{step}")
    turn = (result.get("turns") or [{}])[0]
    reply = turn.get("reply", "") or ""
    try:
        accepted = bool(scenario["check"](workspace, reply))
    except Exception as error:
        accepted = False
        reply += f"\n[check error: {error!r}]"
    model = catalog.profile(profile_id)["model"]
    tool_calls = turn.get("tool_calls") or []
    write_step(step, {
        "kind": "acp", "profile": profile_id, "model": model, "scenario": scenario_name, "task": f"ACP {scenario_name} task, first attempt, Full Auto",
        "acceptance": accepted, "reply": reply.strip()[:600], "wall_s": round(turn.get("elapsed_s") or 0, 1),
        "tool_calls": [{k: c.get(k) for k in ("title", "kind", "status")} for c in tool_calls],
        "efforts_on_wire": result.get("efforts_on_wire"), "requests": result.get("requests"),
        "permission_requests": result.get("client_permission_requests"), "agent_exit": result.get("agent_exit"),
        "context": catalog.model(model)["context"], "peak_wired_gib": round(wired.peak_gib, 2),
        "telemetry": watcher.summary(), "exception": result.get("exception"),
    })


# ------------------------------------------------------------------ summary


def summarize() -> None:
    rows = []
    for path in sorted(STEPS.glob("*.json")):
        data = json.loads(path.read_text())
        if data.get("kind") == "lifecycle":
            rows.append({"model": "all", "profile": None, "task": f"Lifecycle {data['step'].replace('09-lifecycle-', '')} through the Workstation service",
                         "acceptance": data["passed"] == data["total"], "result": f"{data['passed']} of {data['total']} steps passed",
                         "step": data["step"]})
            continue
        if data.get("kind") == "switch":
            for item in data.get("sequence", []):
                rows.append({"model": item.get("model"), "profile": item.get("profile"), "task": "Workstation switch " + item.get("from", "idle") + " -> " + item["profile"],
                             "acceptance": item.get("ok"), "result": item.get("result"), "wall_s": item.get("switch_wall_s")})
            continue
        telemetry = data.get("telemetry") or {}
        tick = telemetry.get("tick") or {}
        mtp = round(tick["mtp_accepted"] / tick["mtp_proposed"] * 100, 1) if tick.get("mtp_proposed") else None
        result = data.get("answer") or data.get("answer_head") or data.get("reply") or ""
        if data.get("kind") == "overhead":
            result = f"decode {data['runs']['closed']['mean_decode_tok_s']} tok/s closed vs {data['runs']['visualizer']['mean_decode_tok_s']} tok/s open ({data['decode_delta_pct']} %)"
        rows.append({"model": data.get("model"), "profile": data.get("profile"), "task": data.get("task"), "acceptance": data.get("acceptance"),
                     "result": result[:160], "wall_s": data.get("wall_s"), "ttft_s": data.get("ttft_s"), "decode_tok_s": data.get("decode_tok_s"),
                     "context": {131072: "128K", 65536: "64K"}.get(data.get("context"), data.get("context")), "mtp_acceptance_pct": mtp,
                     "tokens": data.get("tokens") or data.get("prompt_tokens"), "step": data["step"]})
    document = {"schema": 1, "name": "DwarfStar unified production validation", "date": time.strftime("%Y-%m-%d"),
                "kv_cache": "F16 K and V for every model (the runtime's Qwen4Exp graph has no quantized KV path)",
                "rows": rows, "passed": sum(1 for r in rows if r["acceptance"]), "total": len(rows)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "validation.json").write_text(json.dumps(document, indent=1) + "\n")
    print(json.dumps({"passed": document["passed"], "total": document["total"]}))


def main(argv: list[str]) -> int:
    command = argv[0]
    if command == "switch":
        before = service("GET", "/api/state", timeout=120).get("running_models") or []
        result = switch(argv[1])
        record_path = STEPS / "00-switching.json"
        record = json.loads(record_path.read_text()) if record_path.is_file() else {"step": "00-switching", "kind": "switch", "sequence": []}
        steps = [s["text"] for s in result.get("steps", [])]
        record["sequence"].append({"profile": argv[1], "model": catalog.profile(argv[1])["model"], "from": ",".join(before) or "idle",
                                   "ok": bool(result.get("ok")), "switch_wall_s": result.get("switch_wall_s"), "steps": steps,
                                   "restarted": any(s.startswith("Starting") for s in steps),
                                   "result": "ready" if result.get("ok") else result.get("error")})
        STEPS.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps(record, indent=1) + "\n")
        print(json.dumps({k: result.get(k) for k in ("ok", "error", "switch_wall_s")} | {"steps": [s["text"] for s in result.get("steps", [])]}, indent=1))
        return 0 if result.get("ok") else 1
    if command == "smoke":
        smoke(argv[1], argv[2])
    elif command == "depth":
        depth(argv[1], argv[2], int(argv[3]))
    elif command == "acp":
        acp(argv[1], argv[2], argv[3])
    elif command == "overhead":
        overhead(argv[1], argv[2])
    elif command == "summarize":
        summarize()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
