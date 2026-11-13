#!/usr/bin/env python3
"""Run a calibration capture: replay benign requests against a capture-mode server.

Starts the telemetry-capable runtime directly (not through the controller,
because capture needs a small prefill chunk and an extra environment variable),
replays every request body and plain document, then stops the server so it
writes per-layer statistics to OUT_DIR.

    capture.py MODEL OUT_DIR --bodies DIR [--bodies DIR ...] [--documents FILE ...] [--max-new 160]

Nothing but second moments and a bounded sample of activations is written.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts/validate"))
from api_client import chat  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model")
    parser.add_argument("out", type=Path)
    parser.add_argument("--bodies", action="append", default=[], type=Path)
    parser.add_argument("--documents", nargs="*", default=[], type=Path)
    parser.add_argument("--max-new", type=int, default=160)
    parser.add_argument("--context", type=int, default=32768)
    parser.add_argument("--chunk", type=int, default=64)
    parser.add_argument("--runtime", default="ds4-unified")
    args = parser.parse_args()

    os.environ["DWARFSTAR_MODEL"] = args.model
    os.environ["DWARFSTAR_RUNTIME_OVERRIDE"] = f"{args.model}={args.runtime}"
    from dwarfstar import instance, paths, runtime_control

    if runtime_control.heavyweight_processes():
        raise SystemExit("Another model runtime is running; stop it first.")
    args.out.mkdir(parents=True, exist_ok=True)
    argv, environment = instance.server_command(args.model)

    def setting(flag: str, value: str) -> None:
        argv[argv.index(flag) + 1] = value

    setting("--ctx", str(args.context))
    setting("--prefill-chunk", str(args.chunk))
    setting("--kv-disk-dir", str(args.out / "kv-scratch"))
    setting("--tokens", str(min(4096, args.context // 4)))
    (args.out / "kv-scratch").mkdir(exist_ok=True)
    port = int(argv[argv.index("--port") + 1])
    env = {"PATH": "/usr/bin:/bin", **environment, "DS4_QWEN4_CALIB_DIR": str(args.out / "stats")}
    lock = open(paths.admission_lock(), "r")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    log = (args.out / "server.log").open("w")
    server = subprocess.Popen(argv, env=env, cwd=str(Path(argv[0]).parent), stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=1).read()
                break
            except OSError:
                if server.poll() is not None:
                    raise SystemExit("capture server exited; see server.log")
                time.sleep(1)
        rows = []
        requests = []
        for directory in args.bodies:
            for path in sorted(directory.rglob("*.json")):
                requests.append((str(path.relative_to(directory)), json.loads(path.read_text())))
        for path in args.documents:
            text = path.read_text(errors="replace")[:24000]
            body = {"model": "qwen3.8-flash-next", "reasoning_effort": "low",
                    "messages": [{"role": "user", "content": "Read the following file carefully.\n\n" + text +
                                  "\n\nNow summarise what it does in five sentences."}]}
            requests.append(("document:" + path.name, body))
        for name, body in requests:
            body = dict(body)
            body.pop("max_tokens", None)
            body["max_completion_tokens"] = args.max_new
            body["temperature"] = 0.8
            started = time.time()
            result = chat(port, body, timeout=3600)
            usage = result["usage"] or {}
            row = {"name": name, "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                   "cached": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"), "seconds": round(time.time() - started, 1),
                   "finish": result["finish_reason"], "errors": result["errors"]}
            rows.append(row)
            print(json.dumps(row), flush=True)
        (args.out / "capture-requests.json").write_text(json.dumps(rows, indent=1) + "\n")
    finally:
        server.send_signal(signal.SIGTERM)
        try:
            server.wait(timeout=600)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()
    written = sorted((args.out / "stats").glob("calib-layer-*.bin"))
    print(json.dumps({"ok": bool(written), "layers": len(written), "stats": str(args.out / "stats")}))
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
