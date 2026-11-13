"""Prefill-chunk sweep for one model on the native runtime, outside production.

Starts the model's own generated server command on a spare port with only `--prefill-chunk` changed, records the
runtime's memory plan (graph buffers grow with the chunk), prefill rate on ~8K and ~32K-token prompts (one output
token, temperature 0) and peak wired memory, then stops it. Production settings and the running-model ledger are
never touched; refuse to run while any model server is up.

    DWARFSTAR_MODEL=scope prefill_sweep.py 1024 2048 4096 [--out DIR]
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

from api_client import chat  # noqa: E402
from production_validation import WiredSampler, depth_document  # noqa: E402

from dwarfstar import catalog, runtime_control  # noqa: E402

PORT = 8041
PLAN = re.compile(r"memory: KV ([\d.]+) GiB .*?\+ buffers ([\d.]+) GiB \+ resident model ([\d.]+) GiB = ([\d.]+) GiB planned")


def server_up() -> bool:
    return bool(runtime_control.heavyweight_processes())


def run(chunk: int, out: Path, served: str) -> dict:
    pinned = runtime_control.pinned()  # the same verified argv, environment and directory a production start uses
    argv = list(pinned["argv"])
    argv[argv.index("--prefill-chunk") + 1] = str(chunk)
    argv[argv.index("--port") + 1] = str(PORT)
    log = out / f"server-chunk{chunk}.log"
    started = time.time()
    with log.open("wb") as stream:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DS4_", "DSTAR_"))} | pinned["environment"]
        process = subprocess.Popen(argv, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True, env=env,
                                   cwd=runtime_control.RUNTIME_DIR)
    try:
        while time.time() - started < 300:
            if process.poll() is not None:
                return {"chunk": chunk, "ok": False, "error": "server exited: " + log.read_text(errors="replace")[-400:]}
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/v1/models", timeout=2)
                break
            except OSError:
                time.sleep(1)
        load_s = time.time() - started
        text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", log.read_text(errors="replace"))
        plan = PLAN.search(text)
        rows = {}
        with WiredSampler() as wired:
            for name, words in (("prefill8", 5600), ("prefill32", 22400)):
                prompt, _ = depth_document(words)
                # a unique first line: otherwise the runtime restores the (deterministic) document from its disk KV checkpoints
                prompt = f"Sweep run {os.urandom(8).hex()} at chunk {chunk}.\n" + prompt
                body = {"model": served, "messages": [{"role": "user", "content": prompt}], "max_tokens": 1,
                        "temperature": 0.0, "reasoning_effort": "low"}
                result = chat(PORT, body, timeout=1800)
                usage = result.get("usage") or {}
                cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                rows[name] = {"prompt_tokens": usage.get("prompt_tokens"), "cached_tokens": cached, "ttft_s": result["ttft_s"], "wall_s": round(result["total_s"], 2),
                              "prefill_tok_s": round(usage.get("prompt_tokens", 0) / result["total_s"], 1) if result["total_s"] else None,
                              "errors": result["errors"]}
        return {"chunk": chunk, "ok": all(not r["errors"] for r in rows.values()), "load_s": round(load_s, 1),
                "plan_gib": dict(zip(("kv", "buffers", "resident", "planned"), map(float, plan.groups()))) if plan else None,
                "peak_wired_gib": round(wired.peak_gib, 2), **rows}
    finally:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(60)
        except subprocess.TimeoutExpired:
            process.kill()
        time.sleep(3)


def main(argv: list[str]) -> int:
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else Path(".")
    chunks = [int(a) for a in argv if a.isdigit()]
    out.mkdir(parents=True, exist_ok=True)
    if server_up():
        print("A model server is running; stop it first.", file=sys.stderr)
        return 2
    model = catalog.model(os.environ["DWARFSTAR_MODEL"])
    results = [run(chunk, out, model["served_model_id"]) for chunk in chunks]
    record = {"model": model["id"], "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "production_chunk": model["prefill_chunk"],
              "results": results}
    (out / f"prefill-sweep-{model['id']}.json").write_text(json.dumps(record, indent=1) + "\n")
    print(json.dumps(record, indent=1))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
