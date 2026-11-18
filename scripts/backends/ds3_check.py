"""Check a ds3 runtime's GET /telemetry against its contract, live or on recorded batches.

    ds3_check.py live PORT [--levels 2,3] [--tokens 160] [--effort low] [--second-consumer 1] [--out FILE]
        one temperature-0 generation per level on the Splash server at PORT while a consumer polls /telemetry at
        8 Hz, then check(): exits 1 when any invariant fails. The report (JSON) goes to stdout or FILE.

check(batches, status_before, status_after) verifies, for one generation that ran while nothing else did:
  * schema 2; sequence numbers strictly increase across batches; `next` never goes back; ring drops are reported
  * events are known kinds with bounded, numeric fields: layers <= router.layers <= 64, experts < router.experts,
    counts > 0, a sample's ids distinct and < experts, top_k ids per layer
  * routing: decode router steps == decode cycles; decode routes == sum(drafted + 1) * top_k * layers when every cycle
    ran one lane; every layer's cumulative routes are equal (each row routes top_k experts in every layer);
    sampled experts appear in the aggregate; samples carry the period they were taken at; samples_dropped == 0
  * PLE: the summed per-poll table deltas equal the /status streamed_table deltas (read rows, read bytes)
An event without its payload is one whose detail the engine no longer held when this consumer read it (the engine keeps
~4 MiB of recent detail, shared by every consumer): its counts still add up, but the per-layer, sample and PLE comparisons
need every payload, so they are skipped and the report says so instead of failing. With --second-consumer, another
consumer polls at level 2 throughout; both must get every event's detail.
Exported so tests can drive it with synthetic batches.
"""
from __future__ import annotations

import collections
import json
import sys
import threading
import time
import urllib.request

KINDS = {"decode_cycle", "router_aggregate", "router_sample", "ple_aggregate"}


def check(batches: list[dict], status_before: dict | None = None, status_after: dict | None = None) -> dict:
    problems: list[str] = []
    events = [e for b in batches for e in b.get("events", [])]
    for b in batches:
        if b.get("schema") != 2:
            problems.append(f"schema {b.get('schema')!r}, expected 2")
            break
    seqs = [e.get("seq") for e in events]
    if any(not isinstance(s, int) for s in seqs) or any(b <= a for a, b in zip(seqs, seqs[1:])):
        problems.append("sequence numbers do not strictly increase")
    nexts = [b.get("next", 0) for b in batches]
    if any(b < a for a, b in zip(nexts, nexts[1:])):
        problems.append("next went backwards")
    dropped = sum(b.get("dropped", 0) for b in batches)
    router = next((b["router"] for b in reversed(batches) if b.get("router", {}).get("available")), None)
    kinds = collections.Counter(e.get("kind") for e in events)
    for kind in kinds:
        if kind not in KINDS:
            problems.append(f"unknown event kind {kind!r}")
    shared = sum(1 for e in events if e.get("kind") in ("router_aggregate", "router_sample", "ple_aggregate") and not e.get("payload", True))
    report: dict = {"events": len(events), "kinds": dict(kinds), "ring_dropped": dropped, "other_consumer_events": shared}
    if shared:
        report["note"] = f"{shared} intervals arrived without detail: per-layer, sample and PLE comparisons skipped"

    cycles = [e for e in events if e.get("kind") == "decode_cycle"]
    report["decode_cycles"] = len(cycles)
    if router:
        layers, experts, top_k = router["layers"], router["experts"], router["top_k"]
        if not (0 < layers <= 64 and 0 < experts <= 512 and 0 < top_k <= 16):
            problems.append(f"router dimensions out of bounds: {layers} x {experts}, top {top_k}")
        totals = {0: [0] * layers, 1: [0] * layers}
        cells = {0: collections.Counter(), 1: collections.Counter()}
        steps, routes = [0, 0], [0, 0]
        for e in events:
            if e.get("kind") != "router_aggregate":
                continue
            phase = 1 if e.get("phase") == "prefill" else 0
            steps[phase] += e.get("steps", 0)
            routes[phase] += e.get("routes", 0)
            if not e.get("payload"):
                continue
            rows = e.get("layers") or []
            if len(rows) > layers:
                problems.append(f"aggregate seq {e['seq']}: {len(rows)} layers > {layers}")
            summed = 0
            for layer, row in enumerate(rows[:layers]):
                if len(row) % 2:
                    problems.append(f"aggregate seq {e['seq']} layer {layer}: odd pair list")
                    continue
                for expert, count in zip(row[0::2], row[1::2]):
                    if not (isinstance(expert, int) and 0 <= expert < experts and isinstance(count, int) and count > 0):
                        problems.append(f"aggregate seq {e['seq']} layer {layer}: bad cell {expert}:{count}")
                        continue
                    totals[phase][layer] += count
                    cells[phase][(layer, expert)] += count
                    summed += count
            if summed != e.get("routes"):
                problems.append(f"aggregate seq {e['seq']}: cells sum {summed} != routes {e.get('routes')}")
        report["router"] = {"steps": {"decode": steps[0], "prefill": steps[1]}, "routes": {"decode": routes[0], "prefill": routes[1]},
                            "mode": router.get("mode"), "samples_dropped": router.get("samples_dropped", 0)}
        for phase, name in ((0, "decode"), (1, "prefill")):
            if routes[phase] and not shared and len(set(totals[phase])) != 1:
                problems.append(f"{name}: per-layer route totals differ ({min(totals[phase])}..{max(totals[phase])})")
        if cycles:
            if steps[0] != len(cycles):
                problems.append(f"decode router steps {steps[0]} != decode cycles {len(cycles)}")
            if all(c.get("width") == 1 for c in cycles):
                rows_verified = sum(c["drafted"] + 1 for c in cycles)
                if routes[0] != rows_verified * top_k * layers:
                    problems.append(f"decode routes {routes[0]} != verify rows {rows_verified} x {top_k} x {layers}")
                report["router"]["verify_rows"] = rows_verified
        samples = [e for e in events if e.get("kind") == "router_sample" and e.get("payload", True)]
        report["router"]["samples"] = len(samples)
        for s in samples:
            rows = s.get("layers") or []
            if len(rows) != layers:
                problems.append(f"sample step {s.get('step')}: {len(rows)} layers, expected {layers}")
            for layer, ids in enumerate(rows[:layers]):
                if len(ids) != top_k or len(set(ids)) != len(ids) or any(not (0 <= x < experts) for x in ids):
                    problems.append(f"sample step {s.get('step')} layer {layer}: bad ids")
                elif not shared and any(cells[0][(layer, x)] == 0 for x in ids):
                    problems.append(f"sample step {s.get('step')} layer {layer}: an id the aggregate never counted")
            if s.get("every", 0) < 1 or s.get("step", 0) % s.get("every", 1):
                problems.append(f"sample step {s.get('step')} is not a multiple of its period {s.get('every')}")
        if router.get("samples_dropped", 0):
            problems.append(f"{router['samples_dropped']} samples dropped")
    ple = [e["table"] for e in events if e.get("kind") == "ple_aggregate" and e.get("table")]
    if ple and status_before and status_after and not shared:
        before, after = status_before.get("streamed_table") or {}, status_after.get("streamed_table") or {}
        read_rows, read_bytes = sum(t.get("read_rows", 0) for t in ple), sum(t.get("read_bytes", 0) for t in ple)
        status_rows = after.get("cached_rows", 0) - before.get("cached_rows", 0)
        status_bytes = after.get("read_bytes", 0) - before.get("read_bytes", 0)
        report["ple"] = {"read_rows": read_rows, "read_bytes": read_bytes, "status_read_bytes": status_bytes}
        if read_bytes != status_bytes:
            problems.append(f"PLE read bytes {read_bytes} != /status delta {status_bytes}")
        if before.get("evictions", 0) == after.get("evictions", 0) and read_rows != status_rows:
            problems.append(f"PLE read rows {read_rows} != /status cached-row delta {status_rows}")
    report["problems"] = problems
    report["ok"] = not problems
    return report


def _get(port: int, path: str, timeout: float = 10) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as response:
        return json.loads(response.read())


def _post(port: int, path: str, body: dict, timeout: float = 600) -> dict:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                     headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def live(port: int, level: int, tokens: int, model: str, effort: str = "low", second: bool = False) -> dict:
    """One generation at `level` while a consumer polls at 8 Hz; returns check()'s report plus timings."""
    since = [_get(port, f"/telemetry?level={level}").get("next", 0)]
    time.sleep(3.2)  # let a higher level from an earlier consumer lapse
    since = [_get(port, f"/telemetry?since={since[0]}&level={level}").get("next", since[0])]
    before = _get(port, "/status")
    batches, poll_ms, stop = [], [], threading.Event()

    def consume():
        while not stop.is_set():
            started = time.monotonic()
            batch = _get(port, f"/telemetry?since={since[0]}&level={level}", 5)
            poll_ms.append((time.monotonic() - started) * 1000)
            batches.append(batch)
            since[0] = batch.get("next", since[0])
            stop.wait(0.125)

    other_batches: list[dict] = []

    def other():  # a second viewer at level 2, e.g. a browser tab beside the Workstation
        cursor = since[0]
        while not stop.is_set():
            batch = _get(port, f"/telemetry?since={cursor}&level=2", 5)
            other_batches.append(batch)
            cursor = batch.get("next", cursor)
            stop.wait(0.2)

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    second_thread = threading.Thread(target=other, daemon=True) if second else None
    if second_thread:
        second_thread.start()
    _post(port, "/v1/chat/completions", {"model": model, "max_tokens": tokens, "temperature": 0, "reasoning_effort": effort,
                                         "messages": [{"role": "user", "content": "Name five prime numbers and say briefly why each is prime."}]})
    time.sleep(1.2)  # the last interval's aggregate
    stop.set()
    thread.join()
    if second_thread:
        second_thread.join()
    after = _get(port, "/status")
    report = check(batches, before, after)
    if second_thread:
        other_report = check(other_batches, before, after)
        report["second_consumer"] = {key: other_report.get(key) for key in ("ok", "problems", "other_consumer_events", "events")}
        if not other_report["ok"] or other_report["other_consumer_events"] or report["other_consumer_events"]:
            report["problems"].append("a consumer missed detail while another polled")
            report["ok"] = False
    poll_ms.sort()
    report.update(level=level, effort=effort, polls=len(poll_ms), poll_ms_p50=round(poll_ms[len(poll_ms) // 2], 2) if poll_ms else None,
                  poll_ms_max=round(poll_ms[-1], 2) if poll_ms else None,
                  capabilities=batches[-1].get("capabilities") if batches else None)
    return report


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "live":
        print(__doc__, file=sys.stderr)
        return 2
    port = int(argv[1])
    options = dict(zip(argv[2::2], argv[3::2]))
    levels = [int(x) for x in options.get("--levels", "2,3").split(",")]
    reports = [live(port, level, int(options.get("--tokens", 160)), options.get("--model", "qwen3.8-flash-next"),
                    options.get("--effort", "low"), options.get("--second-consumer") == "1") for level in levels]
    text = json.dumps(reports, indent=1)
    if "--out" in options:
        with open(options["--out"], "w") as handle:
            handle.write(text)
    print(text)
    return 0 if all(r["ok"] for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
