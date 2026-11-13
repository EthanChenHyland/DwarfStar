"""Benchmark index: one compact file generated from the canonical reports.

The Workstation never parses raw evidence. This module reads the canonical
JSON reports (which stay on the local machine), extracts the measured rows
and writes ``research/benchmark-index.json``. Running it again on the same
evidence reproduces the same file.

    dwarfstar bench-index [--evidence-root DIR] [--check]

The evidence root defaults to ``legacy_root`` in ``config/local.json``. A
report that is missing is listed under ``missing`` and its rows are omitted.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dwarfstar.paths import CODE, DATA, local_config

OUTPUT = CODE / "research" / "benchmark-index.json"
SWIFT = "challengers/dwarfstar-swift15/benchmarks/reviews/"
SCOPE = "challengers/dwarfstar-paperniuk/benchmarks/reviews/"
CYBER = "challengers/dwarfstar-cyber/benchmarks/reviews/"


def entry(experiment, date, model, profile, task, report, **fields) -> dict:
    row = {"experiment": experiment, "date": date, "model": model, "profile": profile, "task": task,
           "acceptance": None, "result": None, "wall_s": None, "reasoning_s": None, "tokens": None, "ttft_s": None,
           "decode_tok_s": None, "context": None, "mtp_acceptance_pct": None, "report": report}
    row.update(fields)
    for key in ("wall_s", "reasoning_s", "ttft_s", "decode_tok_s", "mtp_acceptance_pct"):
        if isinstance(row[key], (int, float)):
            row[key] = round(float(row[key]), 2)
    return row


def metrics_fields(metrics: dict) -> dict:
    cycles, accepted = metrics.get("MTP_verify_cycles"), metrics.get("MTP_draft_events_accepted")
    decode_s, tokens = metrics.get("decode_s"), metrics.get("native_generated_tokens") or metrics.get("generated_tokens")
    return {
        "wall_s": metrics.get("wall_s"), "reasoning_s": metrics.get("reasoning_s"), "tokens": metrics.get("generated_tokens"),
        "ttft_s": metrics.get("task_TTFT_s"),
        "decode_tok_s": tokens / decode_s if isinstance(decode_s, (int, float)) and decode_s > 0 and tokens else None,
        "mtp_acceptance_pct": 100.0 * accepted / cycles if cycles and accepted is not None else None,
    }


def swift_fast_lane(data: dict, report: str) -> list[dict]:
    rows = []
    for run in data["runs"]:
        if run["classification"] == "INVALID_TASK":
            continue
        swift = run["label"] == "swift"
        rows.append(entry("Swift LOW frozen low-risk suite", "2026-11-02", "swift" if swift else "scope",
                          "swift-low" if swift else "scope-low", run["task"], report,
                          acceptance=run["classification"].endswith("ACCEPTED"), result="one natural attempt",
                          context="128K", **metrics_fields(run.get("metrics", {}))))
    return rows


def swift_rematch(data: dict, report: str) -> list[dict]:
    rows = []
    for task in data["task_results"]:
        run = task.get("MEDIUM") or {}
        rows.append(entry("Swift MEDIUM frozen rematch", "2026-11-11", "swift", "swift-medium", task["task"], report,
                          acceptance=str(run.get("classification", "")).endswith("ACCEPTED"), result=task.get("category"),
                          context="128K", **metrics_fields(run.get("metrics", {}))))
    medians = data["medians"]
    for label, profile, model in (("LOW", "swift-low", "swift"), ("MEDIUM", "swift-medium", "swift"), ("Scope_matched", "scope-low", "scope")):
        m = medians[label]
        rows.append(entry("Swift MEDIUM frozen rematch", "2026-11-11", model, profile, "median of cohort (" + label.replace("_", " ") + ")", report,
                          result=f"{data['MEDIUM_accepted']} of {data['eligible_tasks']} accepted" if label == "MEDIUM" else None,
                          wall_s=m["wall_s"], reasoning_s=m["reasoning_s"], tokens=round(m["generated_tokens"]), context="128K"))
    return rows


def swift_vs_production(data: dict, report: str) -> list[dict]:
    rows = []
    for run in data["runs"]:
        production = run["profile"] == "production"
        mtp = run.get("MTP") or {}
        cycles, accepted = mtp.get("native_verify_cycles") or mtp.get("verify_cycles"), mtp.get("native_accepted_draft_events") or mtp.get("accepted_draft_events")
        rows.append(entry("Swift LOW against Scope, matched tasks", "2026-11-01", "scope" if production else "swift",
                          "scope-low" if production else "swift-low", run["task"], report, result=run.get("status"),
                          wall_s=run.get("adjusted_wall_s"), reasoning_s=run.get("observed_reasoning_s"),
                          tokens=run.get("native_generated_tokens"), ttft_s=run.get("TTFT_s"),
                          decode_tok_s=run.get("native_decode_tps_completed_requests"), context="128K",
                          mtp_acceptance_pct=100.0 * accepted / cycles if cycles and accepted is not None else None))
    return rows


def scope_mtp_audit(data: dict, report: str) -> list[dict]:
    overall = data["overall"]
    return [
        entry("Scope decode and MTP audit", "2026-11-11", "scope", "scope-low", "synthetic paired decode, MTP on", report,
              result=f"{overall['valid_pairs']} of {overall['planned_pairs']} pairs parity-qualified", decode_tok_s=overall["on"]["median"]),
        entry("Scope decode and MTP audit", "2026-11-11", "scope", "scope-low", "synthetic paired decode, MTP off", report,
              result="reference", decode_tok_s=overall["off"]["median"]),
        entry("Scope decode and MTP audit", "2026-11-11", "scope", "scope-low", "MTP speed ratio (median)", report,
              result=f"+{(overall['speed_ratio']['median'] - 1) * 100:.2f} % decode"),
    ]


def scope_latency(data: dict, report: str) -> list[dict]:
    return [entry("Scope latency decomposition", "2026-11-03", "scope", "scope-low", "six profiling tasks", report,
                  acceptance=data["acceptance_passes"] == data["valid_runs"],
                  result=f"{data['classification'].title()}; inference is {data['inference_envelope_fraction'] * 100:.1f} % of warm wall")]


def scope_prefix(data: dict, report: str) -> list[dict]:
    baseline = data["metrics"]["baseline"]
    return [entry("Scope stable-prefix restore", "2026-11-03", "scope", "scope-low", "first-token time without restore (median)", report,
                  result=data["classification"].title(), ttft_s=baseline["first_TTFT_s"]["median"])]


def cyber_validation(data: dict, report: str) -> list[dict]:
    rows = []
    for name, record in data["records"].items():
        verify = record.get("verify") or {}
        passed = record.get("pass") if isinstance(record.get("pass"), bool) else (all(v for v in verify.values() if isinstance(v, bool)) if verify else None)
        model = "swift" if name.startswith("swift") else "cyber"
        profile = record.get("profile") or name.split("/")[0]
        rows.append(entry("Cyber agent and tool validation", "2026-11-12", model, "swift-medium" if model == "swift" else profile,
                          record.get("test") or name.split("/")[-1], report, acceptance=passed,
                          result=str(record.get("result") or record.get("status") or "")[:80] or None,
                          wall_s=record.get("wall_s") or record.get("elapsed_s"), context="64K" if model == "cyber" else "128K"))
    return rows


def cyber_build(data: dict, report: str) -> list[dict]:
    runtime = data["runtime"]
    rows = []
    for context, memory in runtime["memory"].items():
        rows.append(entry("Cyber context and memory", "2026-11-12", "cyber", "cyber-low", f"context {int(context) // 1024}K", report,
                          acceptance="fails" not in memory["result"], result=f"{memory['result']}; peak wired {memory['peak_wired_gib']} GiB",
                          context=f"{int(context) // 1024}K"))
    rows.append(entry("Cyber runtime bring-up", "2026-11-12", "cyber", "cyber-low", "short prompts", report, result=runtime["coherence_short_prompts"],
                      ttft_s=sum(runtime["ttft_s"]) / 2, decode_tok_s=sum(runtime["decode_tok_s_short"]) / 2,
                      mtp_acceptance_pct=sum(runtime["mtp_acceptance_pct"]) / 2, context="64K"))
    rows.append(entry("Cyber runtime bring-up", "2026-11-12", "cyber", "cyber-low", "about 60K tokens of context", report,
                      result="document question answered", decode_tok_s=sum(runtime["decode_tok_s_at_60k"]) / 2, context="64K"))
    return rows


def runtime_parity(directory: Path) -> list[dict]:
    rows = []
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text())
        raw = data["raw"][1]["rows"]
        ttft = [r["ttft_s"] for r in raw if r["name"].startswith("prompt") and r["ttft_s"]]
        rows.append(entry("Unified runtime parity", "2026-11-12", data["model"], f"{data['model']}-low", "five short prompts and one agent request, temperature 0",
                          "evidence/runtime-parity/" + path.name, acceptance=data["all_identical"],
                          result=f"token-identical to {data['builds'][0]}; reference {data['mean_decode_tok_s'][0]} tok/s",
                          decode_tok_s=data["mean_decode_tok_s"][1], ttft_s=sum(ttft) / len(ttft) if ttft else None))
    return rows


def opening_probe(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    rows = []
    for name, row in data["models"].items():
        model = name.split("_")[0]
        label = "production sampling" if "_" not in name else "min_p " + name.rsplit("_", 1)[1]
        rows.append(entry("Opening-token probe", data["date"], model, f"{model}-low", f"8.3K-token agent request, 80 samples, {label}",
                          "evidence/opening-probe/" + path.name, acceptance=row["early_stop"] == 0,
                          result=f"{row['early_stop']} of {row['samples']} early stops; {row['standard_openings']} standard openings", context="8.3K prompt"))
    return rows


SOURCES = [
    (SWIFT + "swift15-low-risk-fast-lane-20261102T193427Z/SWIFT15-LOW-RISK-FAST-LANE.json", swift_fast_lane),
    (SWIFT + "swift15-medium-rematch-20261111T201510Z/SWIFT15-MEDIUM-REMATCH.json", swift_rematch),
    (SWIFT + "swift15-vs-production-20261101T090657Z/SWIFT15-VS-PRODUCTION-BENCHMARK.json", swift_vs_production),
    (SCOPE + "scope-decode-mtp-audit-20261111T100558Z/SCOPE-DECODE-MTP-AUDIT.json", scope_mtp_audit),
    (SCOPE + "scope-latency-decomposition-20261103T084340Z/SCOPE-LATENCY-DECOMPOSITION.json", scope_latency),
    (SCOPE + "scope-stable-prefix-reuse-20261103T094049Z/SCOPE-STABLE-PREFIX-REUSE.json", scope_prefix),
    (CYBER + "cyber-agent-tool-validation-20261111T222332Z/validation.json", cyber_validation),
    (CYBER + "dwarfstar-cyber-complete-build-20261112T150844Z/DWARFSTAR-CYBER-COMPLETE-BUILD.json", cyber_build),
]


def build(evidence_root: Path) -> dict:
    entries, missing, sources = [], [], []
    for relative, extractor in SOURCES:
        path = evidence_root / relative
        if not path.is_file():
            missing.append(relative)
            continue
        try:
            rows = extractor(json.loads(path.read_text()), relative)
        except (KeyError, TypeError, ValueError) as error:
            missing.append(f"{relative} (unreadable: {type(error).__name__} {error})")
            continue
        entries += rows
        sources.append({"report": relative, "rows": len(rows)})
    for name, function, target in (("runtime-parity", runtime_parity, DATA / "evidence/runtime-parity"),
                                   ("opening-probe", opening_probe, DATA / "evidence/opening-probe/opening-probe-20261112.json"),
                                   ("production-validation", production_validation, DATA / "evidence/production-validation/validation.json")):
        if target.exists():
            rows = function(target)
            entries += rows
            sources.append({"report": "evidence/" + name, "rows": len(rows)})
        else:
            missing.append("evidence/" + name)
    entries.sort(key=lambda row: (row["date"] or "", row["experiment"], row["model"] or "", row["task"] or ""), reverse=True)
    highlights = json.loads((CODE / "research/highlights.json").read_text())
    dates = sorted({row["date"] for row in entries if row["date"]})
    return {"schema": 1, "generated": dates[-1] if dates else None, "evidence": "Canonical reports on the local machine; paths are relative to the evidence root.",
            "sources": sources, "missing": missing, "highlights": highlights, "entries": entries}


def production_validation(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    rows = []
    for row in data.get("rows", []):
        rows.append(entry("Unified production validation", data.get("date"), row.get("model"), row.get("profile"), row.get("task"),
                          "evidence/production-validation/validation.json",
                          **{k: row.get(k) for k in ("acceptance", "result", "wall_s", "ttft_s", "decode_tok_s", "context", "mtp_acceptance_pct", "tokens")}))
    return rows


def main(argv: list[str]) -> int:
    root = None
    if "--evidence-root" in argv:
        root = Path(argv[argv.index("--evidence-root") + 1])
    root = root or Path(local_config().get("legacy_root", DATA))
    index = build(root)
    text = json.dumps(index, indent=1) + "\n"
    if "--check" in argv:
        same = OUTPUT.is_file() and OUTPUT.read_text() == text
        print(json.dumps({"ok": same, "entries": len(index["entries"]), "missing": index["missing"]}))
        return 0 if same else 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text)
    print(json.dumps({"ok": True, "entries": len(index["entries"]), "sources": index["sources"], "missing": index["missing"], "output": str(OUTPUT.relative_to(CODE))}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
