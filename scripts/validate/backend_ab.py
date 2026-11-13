"""Backend A/B: the same small workload against any OpenAI-compatible endpoint (native DwarfStar or a candidate).

    backend_ab.py LABEL PORT MODEL_ID [--effort low] [--out DIR]

Workload (temperature 0, one request at a time):
  short     a 40-token question, 256 output tokens          -> TTFT, decode
  prefill8  ~8K-token document + needle question, 600 tokens -> prefill rate, TTFT, needle answer
  prefill32 ~32K-token document + needle question, 600 tokens -> prefill rate at depth
  repeat    turn 1: ~8K-token prefix + question A
            turn 2: same prefix, turn-1 answer, question B   -> TTFT and reused tokens on a repeated prefix
Wired memory is sampled every second throughout. Results go to OUT/backend-ab-LABEL.json.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

from api_client import chat  # noqa: E402
from production_validation import WiredSampler, depth_document  # noqa: E402

SHORT = "Explain in about 150 words why a hash map gives average O(1) lookup, and name one case where it degrades."


def body(model: str, messages: list[dict], max_tokens: int, effort: str) -> dict:
    return {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.0, "reasoning_effort": effort}


def row(name: str, result: dict) -> dict:
    usage = result.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    return {"test": name, "ok": not result["errors"] and bool(result["content"] or result["reasoning_chars"]),
            "prompt_tokens": usage.get("prompt_tokens"), "cached_tokens": details.get("cached_tokens"),
            "completion_tokens": usage.get("completion_tokens"), "ttft_s": result["ttft_s"], "wall_s": round(result["total_s"], 3),
            "prefill_tok_s": result["prefill_tok_s"], "decode_tok_s": result["decode_tok_s"],
            "answer_head": result["content"].strip()[:160], "reasoning_chars": result["reasoning_chars"], "errors": result["errors"]}


def main(argv: list[str]) -> int:
    label, port, model = argv[0], int(argv[1]), argv[2]
    effort = argv[argv.index("--effort") + 1] if "--effort" in argv else "low"
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else Path(".")
    rows = []
    with WiredSampler() as wired:
        rows.append(row("short", chat(port, body(model, [{"role": "user", "content": SHORT}], 256, effort), timeout=900)))
        for name, words in (("prefill8", 5600), ("prefill32", 22400)):
            document, _ = depth_document(words)
            rows.append(row(name, chat(port, body(model, [{"role": "user", "content": document}], 600, effort), timeout=3600)))
        prefix, _ = depth_document(5700)  # a different document from prefill8, so turn 1 is not itself a cache hit
        prefix = prefix.split("\n\nQuestion:")[0]
        first = [{"role": "user", "content": prefix + "\n\nQuestion: who moved the most units of copper wire in one entry? Answer in one sentence."}]
        turn1 = chat(port, body(model, first, 600, effort), timeout=3600)
        rows.append(row("repeat-turn1", turn1))
        second = first + [{"role": "assistant", "content": turn1["content"]},
                          {"role": "user", "content": "And which room appears most often as a destination? One sentence."}]
        rows.append(row("repeat-turn2", chat(port, body(model, second, 600, effort), timeout=3600)))
    record = {"label": label, "port": port, "model": model, "effort": effort, "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "peak_wired_gib": round(wired.peak_gib, 2), "rows": rows}
    out.mkdir(parents=True, exist_ok=True)
    (out / f"backend-ab-{label}.json").write_text(json.dumps(record, indent=1) + "\n")
    for r in rows:
        print(f"{label:<14} {r['test']:<13} ok={r['ok']} prompt={r['prompt_tokens']} cached={r['cached_tokens']} ttft={r['ttft_s'] and round(r['ttft_s'], 2)}s "
              f"prefill={r['prefill_tok_s'] and round(r['prefill_tok_s'])} tok/s decode={r['decode_tok_s'] and round(r['decode_tok_s'], 1)} tok/s wall={r['wall_s']}s")
    print(f"{label:<14} peak wired {record['peak_wired_gib']} GiB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
