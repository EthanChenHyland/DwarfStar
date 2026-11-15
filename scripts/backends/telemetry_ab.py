"""Overhead and equivalence A/B for Splash runtime builds (the advanced-telemetry experiment).

    telemetry_ab.py serve RUNTIME_DIR MODEL PORT     start RUNTIME_DIR's server for MODEL (scope|swift|cyber) on PORT with
                                                      production's exact arguments and a cloned Splash HOME; waits healthy
    telemetry_ab.py stop PORT                         stop the server listening on PORT (its process group)
    telemetry_ab.py bench LABEL PORT [--runs N] [--level L] [--poll HZ] [--effort low] [--out DIR]
                                                      the workload below, N times; with --level, a background consumer polls
                                                      GET /telemetry?since=..&level=L at HZ, as the Workstation would

Workload per run, temperature 0, one request at a time; each run's prompts start with a run marker so no run reuses
another run's prefix, while run i is identical across builds (pairwise output equivalence):
  short    a 40-token question, 256 output tokens   -> TTFT, decode
  prefill  ~8K-token document + question, 32 tokens -> prefill rate
  decode   a long-answer question, 640 tokens       -> decode rate, draft acceptance
Rates are the engine's own (Splash /status metric deltas around each request), not client timing. Output text is
hashed per test and run. Results: OUT/telemetry-ab-LABEL.json.

Never touches the production instance: the server runs from RUNTIME_DIR on PORT with a copy of the production Splash
HOME under <cache>/telemetry-experiment/home-<build>.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "validate"))
sys.path.insert(0, str(HERE.parents[1] / "src"))

from api_client import chat  # noqa: E402
from production_validation import depth_document  # noqa: E402

from dwarfstar import catalog, paths  # noqa: E402

SHORT = "Explain in about 150 words why a hash map gives average O(1) lookup, and name one case where it degrades."
LONG = ("Write a careful, detailed explanation of how a B-tree keeps itself balanced during insertion and deletion, "
        "with a worked example of splitting a full node and of merging two underfull nodes. Use plain prose.")


def get(port: int, path: str, timeout: float = 10) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as response:
        return json.load(response)


def serve(runtime: Path, model_id: str, port: int) -> int:
    model = catalog.model(model_id)
    pin = catalog.load()["runtimes"]["runtimes"]
    asset = lambda role: str(paths.ASSETS / model["assets"][role]["path"])  # noqa: E731
    home = paths.CACHE / "telemetry-experiment" / f"home-{runtime.name}"
    if not home.exists():
        home.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["cp", "-cR", str(paths.DATA / next(iter(pin.values()))["home"]), str(home)], check=True)
    argv = [str(runtime / "python/bin/python3"), "-u", str(runtime / "install/launcher.py"), "serve",
            "--model", model["splash_model_name"], "--model-dir", asset("main"), "--mmproj", asset("vision"),
            "--draft-model", str(Path(asset("draft")).parent), "--offline", "--no-webui", "--host", "127.0.0.1",
            "--port", str(port), "--max-context", str(model["context"]), "--served-model-name", "qwen3.8-flash-next"]
    log = paths.LOGS / "telemetry-experiment" / f"{runtime.name}-{model_id}-{port}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(argv, stdout=log.open("w"), stderr=subprocess.STDOUT, start_new_session=True,
                               env={**os.environ, "HOME": str(home)})
    started = time.time()
    while time.time() - started < 600:
        if process.poll() is not None:
            raise SystemExit(f"server exited {process.returncode}; log {log}")
        try:
            if get(port, "/status", 5).get("ready"):
                print(json.dumps({"pid": process.pid, "port": port, "runtime": str(runtime), "model": model_id,
                                  "load_s": round(time.time() - started, 1), "log": str(log)}))
                return 0
        except Exception:
            pass
        time.sleep(1)
    raise SystemExit("server did not become ready in 600 s")


def stop(port: int) -> int:
    pids = subprocess.run(["lsof", "-tiTCP:%d" % port, "-sTCP:LISTEN"], capture_output=True, text=True).stdout.split()
    for pid in pids:
        try:
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
        except ProcessLookupError:
            pass
    for _ in range(90):
        if not subprocess.run(["lsof", "-tiTCP:%d" % port, "-sTCP:LISTEN"], capture_output=True, text=True).stdout:
            print(json.dumps({"stopped": port}))
            return 0
        time.sleep(1)
    raise SystemExit(f"port {port} still listening")


class Consumer(threading.Thread):
    """Polls /telemetry like the Workstation would; counts what it receives and how long each poll takes."""

    def __init__(self, port: int, level: int, hz: float):
        super().__init__(daemon=True)
        self.port, self.level, self.interval = port, level, 1.0 / hz
        self.since, self.polls, self.events, self.dropped, self.poll_ms, self.errors = 0, 0, 0, 0, [], 0
        self.capabilities = None
        self.stop_flag = threading.Event()

    def run(self):
        while not self.stop_flag.is_set():
            started = time.perf_counter()
            try:
                batch = get(self.port, f"/telemetry?since={self.since}&level={self.level}", 5)
                self.since = batch.get("next", self.since)
                self.events += len(batch.get("events", []))
                self.dropped = batch.get("dropped", self.dropped)
                self.capabilities = batch.get("capabilities", self.capabilities)
                self.polls += 1
                self.poll_ms.append((time.perf_counter() - started) * 1000)
            except Exception:
                self.errors += 1
            self.stop_flag.wait(max(0.0, self.interval - (time.perf_counter() - started)))


def metrics(port: int) -> dict:
    status = get(port, "/status")
    m = status["metrics"]
    return {"prefill_tokens": m["prefill_input_tokens"], "prefill_ms": m["prefill_wall_ms"],
            "decode_tokens": m["decode_output_tokens"], "decode_ms": m["decode_wall_ms"],
            "drafted": m["drafted_tokens"], "accepted": m["accepted_draft_tokens"],
            "peak_bytes": status["memory_actual"]["peak_bytes"], "thermal": status["thermal_state"]}


def measured(port: int, name: str, messages: list[dict], max_tokens: int, effort: str) -> dict:
    before = metrics(port)
    result = chat(port, {"model": "qwen3.8-flash-next", "messages": messages, "max_tokens": max_tokens,
                         "temperature": 0.0, "reasoning_effort": effort})
    after = metrics(port)
    d = {k: after[k] - before[k] for k in ("prefill_tokens", "prefill_ms", "decode_tokens", "decode_ms", "drafted", "accepted")}
    text = (result.get("content") or "") + "\x00" + str(result.get("reasoning_chars"))
    return {"test": name, "ok": not result["errors"], "ttft_s": result["ttft_s"], "wall_s": round(result["total_s"], 3),
            "completion_tokens": (result.get("usage") or {}).get("completion_tokens"),
            "prefill_tok_s": round(d["prefill_tokens"] * 1000 / d["prefill_ms"], 2) if d["prefill_ms"] else None,
            "decode_tok_s": round(d["decode_tokens"] * 1000 / d["decode_ms"], 3) if d["decode_ms"] else None,
            "decode_ms": round(d["decode_ms"], 2), "decode_tokens": d["decode_tokens"],
            "drafted": d["drafted"], "accepted": d["accepted"],
            "output_sha256": hashlib.sha256(text.encode()).hexdigest()[:16], "peak_gib": round(after["peak_bytes"] / 2**30, 3),
            "thermal": after["thermal"]}


def bench(label: str, port: int, runs: int, level: int | None, hz: float, effort: str, out: Path) -> int:
    document, _ = depth_document(6000)
    consumer = Consumer(port, level, hz) if level is not None else None
    if consumer:
        consumer.start()
    rows = []
    for run in range(runs):
        marker = f"Run marker R{run:02d}."
        rows.append({"run": run, **measured(port, "short", [{"role": "user", "content": f"{marker} {SHORT}"}], 256, effort)})
        rows.append({"run": run, **measured(port, "prefill", [{"role": "user", "content": f"{marker}\n{document}\n\nHow many entries mention the loft? Answer with one number."}], 32, effort)})
        rows.append({"run": run, **measured(port, "decode", [{"role": "user", "content": f"{marker} {LONG}"}], 640, effort)})
        print(json.dumps(rows[-3:]), flush=True)
    summary = {}
    for name in ("short", "prefill", "decode"):
        subset = [r for r in rows if r["test"] == name]
        decode = [r for r in subset if r["decode_tok_s"]]
        summary[name] = {
            "ttft_s_median": sorted(r["ttft_s"] for r in subset)[len(subset) // 2],
            "prefill_tok_s_median": sorted(r["prefill_tok_s"] for r in subset if r["prefill_tok_s"])[len(subset) // 2] if name == "prefill" else None,
            "decode_tok_s_aggregate": round(sum(r["decode_tokens"] for r in decode) * 1000 / sum(r["decode_ms"] for r in decode), 3) if decode else None,
            "acceptance": round(sum(r["accepted"] for r in subset) / max(1, sum(r["drafted"] for r in subset)), 4),
            "wall_s_total": round(sum(r["wall_s"] for r in subset), 3),
        }
    if consumer:
        consumer.stop_flag.set()
        consumer.join(5)
    result = {"label": label, "port": port, "runs": runs, "level": level, "poll_hz": hz if consumer else None,
              "effort": effort, "rows": rows, "summary": summary,
              "consumer": {"polls": consumer.polls, "events": consumer.events, "dropped": consumer.dropped,
                           "errors": consumer.errors, "capabilities": consumer.capabilities,
                           "poll_ms_median": sorted(consumer.poll_ms)[len(consumer.poll_ms) // 2] if consumer.poll_ms else None}
              if consumer else None,
              "peak_gib": max(r["peak_gib"] for r in rows), "thermal": sorted({r["thermal"] for r in rows})}
    out.mkdir(parents=True, exist_ok=True)
    (out / f"telemetry-ab-{label}.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({"label": label, "summary": summary, "consumer": result["consumer"], "peak_gib": result["peak_gib"]}, indent=1))
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "serve":
        return serve(Path(argv[1]), argv[2], int(argv[3]))
    if argv[0] == "stop":
        return stop(int(argv[1]))
    if argv[0] == "bench":
        options = dict(zip(argv[3::2], argv[4::2]))
        return bench(argv[1], int(argv[2]), int(options.get("--runs", 3)),
                     int(options["--level"]) if "--level" in options else None, float(options.get("--poll", 8)),
                     options.get("--effort", "low"), Path(options.get("--out", paths.ASSETS / "Manifests/telemetry-experiment")))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
