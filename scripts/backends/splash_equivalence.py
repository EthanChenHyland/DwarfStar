"""Behavioural equivalence of two Splash-m1 builds (or one build's output for the record).

Starts a Splash runtime directory's launcher serving one local model, with deterministic decoding (`--verify-rows all`,
temperature 0), sends a fixed prompt set, records each reply's text, reasoning length and token counts, and stops it.
Run it once per runtime and compare the two JSON files with --compare.

    splash_equivalence.py run RUNTIME_DIR HOME_DIR OUT.json -- <serve arguments without --port/--host>
    splash_equivalence.py compare A.json B.json
    splash_equivalence.py ab LABEL RUNTIME_DIR HOME_DIR OUT_DIR -- <serve arguments>   (backend_ab.py's workload)
    splash_equivalence.py probe RUNTIME_DIR HOME_DIR OUT.json IMAGE -- <serve arguments>
        Splash's memory plan in the catalog's fields, the load time and one vision question about IMAGE
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "validate"))
from api_client import chat  # noqa: E402

PORT = 8042
PROMPTS = [
    ("plain", "What is the capital of Australia? Answer in one sentence.", "none", 64),
    ("arithmetic", "Compute 37 * 43 and show the steps briefly.", "none", 128),
    ("code", "Write a Python function that reverses the words of a sentence. Code only.", "none", 160),
    ("unicode", "Translate 'good morning, friend' into French, German and Japanese.", "none", 96),
    ("reasoning", "If a shop sells 3 pens for $2, how much do 12 pens cost? Answer in one sentence.", "low", 400),
    ("long_context", None, "none", 48),
]


def long_prompt() -> str:
    lines = [f"Record {i}: sensor {i % 17} reported {((i * 7919) % 1000) / 10:.1f} degrees." for i in range(1, 900)]
    lines.insert(613, "Record 614b: the maintenance code for the pump room is 5281.")
    return "\n".join(lines) + "\n\nWhat is the maintenance code for the pump room? Answer with the number only."


def probe(out: Path, image: Path, model: str, ready: float) -> int:
    import base64  # noqa: PLC0415

    plan = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/status").read())["memory_plan"]
    memory = plan["model"]["memory"]
    gib = 2**30
    buffers = sum(memory.get(k, 0) for k in ("lane_state_bytes", "shared_prefill_bytes", "shared_decode_bytes", "pipeline_reserve_bytes",
                                            "runtime_overhead_reserve_bytes", "streamed_table_bytes", "state_staging_bytes"))
    kv = plan["model"]["kv_page_bytes"] / plan["model"]["kv_page_tokens"] * 131072
    url = "data:image/png;base64," + base64.b64encode(image.read_bytes()).decode()
    body = {"model": model, "temperature": 0.0, "max_tokens": 120, "reasoning_effort": "none",
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}},
                                                      {"type": "text", "text": "Describe the shapes, their colours and any text in this image in one sentence."}]}]}
    result = chat(PORT, body, timeout=600)
    record = {"ready_s": round(ready, 1),
              "memory": {"resident_model_gib": round((memory["target_weights_bytes"] + memory["draft_weights_bytes"]) / gib, 2),
                         "target_weights_gib": round(memory["target_weights_bytes"] / gib, 2),
                         "draft_gib": round(memory["draft_weights_bytes"] / gib, 2), "kv_gib": round(kv / gib, 2),
                         "buffers_gib": round(buffers / gib, 2), "projector_gib": round(memory["vision_weights_bytes"] / gib, 2)},
              "kv_bytes_per_token": round(plan["model"]["kv_page_bytes"] / plan["model"]["kv_page_tokens"]),
              "vision": {"answer": result["content"].strip(), "errors": result["errors"]}}
    out.write_text(json.dumps(record, indent=1) + "\n")
    print(json.dumps(record, indent=1))
    return 0 if not result["errors"] else 1


def run(runtime: Path, home: Path, out: Path, serve: list[str], ab_label: str | None = None, image: Path | None = None) -> int:
    home.mkdir(parents=True, exist_ok=True)
    argv = [str(runtime / "python/bin/python3"), "-u", str(runtime / "install/launcher.py"), "serve", *serve,
            "--offline", "--no-webui", "--host", "127.0.0.1", "--port", str(PORT), "--verify-rows", "all"]
    log = out.with_suffix(".log")
    started = time.time()
    with log.open("wb") as stream:
        process = subprocess.Popen(argv, stdout=stream, stderr=subprocess.STDOUT, env={**os.environ, "HOME": str(home)},
                                   start_new_session=True)
    try:
        while True:
            if process.poll() is not None:
                print(log.read_text(errors="replace")[-1500:])
                return 1
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/v1/models", timeout=2)
                break
            except OSError:
                time.sleep(1)
            if time.time() - started > 600:
                print("not ready in 600 s")
                return 1
        ready = time.time() - started
        model = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/v1/models").read())["data"][0]["id"]
        if image:
            return probe(out, image, model, ready)
        if ab_label:
            import backend_ab  # noqa: PLC0415

            return backend_ab.main([ab_label, str(PORT), model, "--out", str(out.parent)])
        rows = []
        for name, text, effort, tokens in PROMPTS:
            body = {"model": model, "temperature": 0.0, "max_tokens": tokens,
                    "messages": [{"role": "user", "content": text or long_prompt()}],
                    "reasoning_effort": effort,
                    "chat_template_kwargs": {"enable_thinking": effort != "none", **({"reasoning_effort": effort} if effort != "none" else {})}}
            result = chat(PORT, body, timeout=900)
            usage = result.get("usage") or {}
            rows.append({"name": name, "content": result["content"], "reasoning": result["reasoning"],
                         "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                         "decode_tok_s": result.get("decode_tok_s"), "errors": result["errors"]})
            print(f"{name:<13} {usage.get('prompt_tokens')}->{usage.get('completion_tokens')}  {result['content'].strip()[:90]!r}", flush=True)
        status = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/status").read())
        out.write_text(json.dumps({"runtime": str(runtime), "serve": serve, "ready_s": round(ready, 1),
                                   "memory_plan": status.get("memory_plan"), "rows": rows}, indent=1, ensure_ascii=False) + "\n")
        return 0
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(60)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
        time.sleep(3)


def compare(a: Path, b: Path) -> int:
    first, second = json.loads(a.read_text()), json.loads(b.read_text())
    same = 0
    for x, y in zip(first["rows"], second["rows"]):
        equal = (x["content"], x["reasoning"], x["prompt_tokens"], x["completion_tokens"]) == \
                (y["content"], y["reasoning"], y["prompt_tokens"], y["completion_tokens"])
        same += equal
        print(f"{x['name']:<13} {'identical' if equal else 'DIFFERENT'}")
        if not equal:
            print("   A:", repr(x["content"][:160]))
            print("   B:", repr(y["content"][:160]))
    print(f"{same}/{len(first['rows'])} identical")
    return 0 if same == len(first["rows"]) else 1


if __name__ == "__main__":
    if sys.argv[1] == "compare":
        sys.exit(compare(Path(sys.argv[2]), Path(sys.argv[3])))
    split = sys.argv.index("--")
    if sys.argv[1] == "probe":
        sys.exit(run(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[split + 1:], image=Path(sys.argv[5])))
    if sys.argv[1] == "ab":
        out_dir = Path(sys.argv[5])
        out_dir.mkdir(parents=True, exist_ok=True)
        sys.exit(run(Path(sys.argv[3]), Path(sys.argv[4]), out_dir / f"backend-ab-{sys.argv[2]}.json", sys.argv[split + 1:],
                     ab_label=sys.argv[2]))
    sys.exit(run(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[split + 1:]))
