#!/usr/bin/env python3
"""Check that two native runtime builds produce identical output for one model.

Runs a fixed set of temperature-0 prompts under each build and compares the
full reasoning and answer text, then reports decode speed side by side.

    runtime_parity.py MODEL RUNTIME_A RUNTIME_B OUT.json [--request BODY.json ...]
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts/validate"))
from api_client import chat  # noqa: E402

PROMPTS = [
    "What is 17 times 23? Reply with only the number.",
    "Write a Python function that returns the n-th Fibonacci number iteratively, with a docstring.",
    "Explain in three sentences why the sky is blue.",
    "List five common HTTP status codes with a one-line meaning for each.",
    "Rewrite this sentence in the passive voice: The committee approved the new budget on Tuesday.",
]


def dwarfstar(args: list[str], runtime_override: str) -> dict | list:
    env = {**os.environ, "DWARFSTAR_RUNTIME_OVERRIDE": runtime_override}
    result = subprocess.run([str(REPO / "bin/dwarfstar"), *args], env=env, capture_output=True, text=True, timeout=900)
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise SystemExit(f"dwarfstar {' '.join(args)} failed: {result.stdout[-400:]} {result.stderr[-400:]}")


def run_build(model: str, runtime: str, port: int, bodies: list[dict]) -> dict:
    override = f"{model}={runtime}"
    dwarfstar(["generate", "--model", model], override)
    started = dwarfstar(["runtime", "--model", model, "start"], override)
    if not started.get("ok"):
        raise SystemExit(f"start failed under {runtime}: {started}")
    rows = []
    try:
        for index, prompt in enumerate(PROMPTS):
            body = {"model": "qwen3.8-flash-next", "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 320, "temperature": 0.0, "reasoning_effort": "low"}
            rows.append({"name": f"prompt-{index}", **summarize(chat(port, body))})
        for index, body in enumerate(bodies):
            body = dict(body)
            body["temperature"] = 0.0
            body["max_completion_tokens"] = 400
            body.pop("max_tokens", None)
            rows.append({"name": f"request-{index}", **summarize(chat(port, body, timeout=1800))})
    finally:
        time.sleep(1)
        stopped = dwarfstar(["runtime", "--model", model, "stop"], override)
    return {"runtime": runtime, "load_seconds": started.get("load_seconds"), "rows": rows, "stopped": stopped.get("ok")}


def summarize(result: dict) -> dict:
    text = result["reasoning"] + "\n=====\n" + result["content"] + "\n=====\n" + ",".join(result["tool_calls"])
    return {
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "finish": result["finish_reason"],
        "completion_tokens": (result["usage"] or {}).get("completion_tokens"),
        "decode_tok_s": result["decode_tok_s"],
        "prefill_tok_s": result["prefill_tok_s"],
        "ttft_s": result["ttft_s"],
        "errors": result["errors"],
    }


def main() -> int:
    model, runtime_a, runtime_b, out = sys.argv[1:5]
    bodies = [json.loads(Path(p).read_text()) for p in sys.argv[5:] if not p.startswith("--")]
    sys.path.insert(0, str(REPO / "src"))
    from dwarfstar import catalog

    port = catalog.model(model)["port"]
    a = run_build(model, runtime_a, port, bodies)
    b = run_build(model, runtime_b, port, bodies)
    comparison = []
    for row_a, row_b in zip(a["rows"], b["rows"]):
        comparison.append({
            "name": row_a["name"], "identical": row_a["sha256"] == row_b["sha256"],
            "tokens": [row_a["completion_tokens"], row_b["completion_tokens"]],
            "decode_tok_s": [round(row_a["decode_tok_s"] or 0, 2), round(row_b["decode_tok_s"] or 0, 2)],
            "ttft_s": [round(row_a["ttft_s"] or 0, 3), round(row_b["ttft_s"] or 0, 3)],
        })
    mean = lambda rows: sum(r["decode_tok_s"] or 0 for r in rows) / max(1, len(rows))
    report = {
        "model": model, "builds": [runtime_a, runtime_b],
        "all_identical": all(row["identical"] for row in comparison),
        "mean_decode_tok_s": [round(mean(a["rows"]), 2), round(mean(b["rows"]), 2)],
        "load_seconds": [a["load_seconds"], b["load_seconds"]],
        "rows": comparison, "raw": [a, b],
    }
    Path(out).write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: report[k] for k in ("model", "builds", "all_identical", "mean_decode_tok_s", "load_seconds")}))
    for row in comparison:
        print("  ", row)
    return 0 if report["all_identical"] else 1


if __name__ == "__main__":
    sys.exit(main())
