"""One-time inventory and classification of the 2026-11 legacy setup tree (~/local-ai-setup) and the canonical roots.

Every file of 50 MB or more is listed with its size, category, whether the catalog references it, whether it is
recoverable, a known SHA-256 where the catalog or a conversion receipt recorded one, and the planned action. Smaller
files are summarised per directory. Nothing is changed.

Categories: A active production · B user data · C rebuild/provenance source · D native rollback (retired) ·
E recoverable cache · F scratch/abandoned build · G duplicate · H archive (kept: history, evidence, ambiguous)

    legacy_inventory.py OUT.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from dwarfstar import catalog, paths  # noqa: E402

HOME = Path.home()
LEGACY = HOME / "local-ai-setup"
ROOTS = [LEGACY, paths.ASSETS, paths.DATA, paths.CACHE, paths.LOGS, HOME / "Library/Application Support/DwarfStar Workstation"]
BIG = 50 << 20

# (path fragment, category, recoverable, action, why) — first match wins
RULES = [
    ("/AI/DwarfStar/Models/", "A", "from Hugging Face sources and the repack tool", "keep", "production model asset"),
    ("/AI/DwarfStar/Runtimes/", "A", "scripts/backends/build_splash.sh", "keep", "production runtime"),
    ("/AI/DwarfStar/Sources/", "C", "GitHub release and source", "keep", "Splash build inputs"),
    ("/AI/DwarfStar/Manifests/", "H", "", "keep", "manifests and evidence"),
    ("/source/Qwen3.8-Flash-Next-CYBERSECURITY-NVFP4/", "C", "Hugging Face dealignai/...NVFP4@15ef113c (public)", "delete after cold-start proof", "Cyber source checkpoint, not loaded in production"),
    ("/Swift1.5-GSQ-RCO-Q2_0-main.gguf", "D", "lossless from Models/Swift part 1 (remove split keys)", "delete", "native-only layout of the Swift weights; tensors identical to the production part 1"),
    ("/DwarfStarCyber-Q2_0-main.gguf", "D", "lossless from Models/Cyber part 1 (remove split keys)", "delete", "native-only layout of the Cyber weights; tensors identical to the production part 1"),
    ("MTP-block", "D", "upstream repo or the conversion recipe", "delete", "native MTP sidecar; Splash uses the DFlash2 draft"),
    ("/mmproj-Qwen3.8-Flash-Next-Q8_0.gguf", "D", "ISTA-DASLab GGUF repo", "delete", "native Q8_0 projector; Splash uses the BF16 one"),
    ("/dwarfstar-data/runtime/", "D", "tag rollback/native-default-20261113", "delete", "native ds4 runtime"),
    (".kv", "E", "regenerated on use (native only)", "delete", "native KV checkpoint state"),
    ("/cache/native-kv/", "E", "regenerated on use (native only)", "delete", "native KV cache"),
    ("/agent-tools/cache/", "G", "agent-tools installer", "delete", "duplicate Playwright browser cache of an old installation"),
    ("/build/cyber-calibration/", "F", "", "delete", "abandoned Cyber calibration scratch"),
    ("/build/ds4-src/", "F", "github.com/paperniuk/ds4 at the pinned commit", "delete", "native runtime build tree"),
    ("/dwarfstar-cyber/build/", "F", "conversion recipe", "delete", "Cyber conversion scratch and fixtures"),
    ("/benchmarks/reviews/", "F", "", "delete binary fixtures; reports kept in Manifests/evidence/legacy", "benchmark fixtures"),
    ("/preserved-history/", "H", "", "archive", "earlier setup history preserved by a previous cleanup"),
    ("/DwarfStar Workstation/", "H", "", "archive", "old Workstation scratch workspaces and evidence (may hold user work)"),
    ("/v6/", "H", "", "relocate", "local helper tools on the agent's PATH"),
]


def classify(path: str) -> dict:
    for fragment, category, recoverable, action, why in RULES:
        if fragment in path:
            return {"category": category, "recoverable": recoverable or None, "action": action, "why": why}
    return {"category": "H", "recoverable": None, "action": "review (kept)", "why": "not classified with confidence"}


def main(out: str) -> int:
    assets = {}
    for model_id, model in catalog.models().items():
        for role, asset in model["assets"].items():
            assets[str((paths.ASSETS / asset["path"]).resolve())] = {"model": model_id, "role": role, "sha256": asset["sha256"]}
    rows, small = [], defaultdict(lambda: [0, 0])
    seen = set()
    for root in ROOTS:
        if not root.exists():
            continue
        for directory, folders, files in os.walk(root):
            folders[:] = [f for f in folders if f not in (".git", "node_modules")]
            for name in files:
                path = Path(directory, name)
                if path.is_symlink():
                    continue
                try:
                    info = path.stat()
                except OSError:
                    continue
                if (info.st_dev, info.st_ino) in seen:
                    continue
                seen.add((info.st_dev, info.st_ino))
                if info.st_size >= BIG:
                    text = str(path)
                    reference = assets.get(str(path.resolve()))
                    row = {"path": text, "bytes": info.st_size, "type": path.suffix or "file",
                           "referenced_by_catalog": bool(reference), "active_runtime": "/Runtimes/" in text,
                           **classify(text), "sha256": reference["sha256"] if reference else None}
                    if reference:
                        row.update(category="A", action="keep", why=f"{reference['model']} {reference['role']}")
                    rows.append(row)
                else:
                    key = str(Path(directory).relative_to(root).parts[0]) if Path(directory) != root else "."
                    small[f"{root}/{key}"][0] += info.st_size
                    small[f"{root}/{key}"][1] += 1
    totals = defaultdict(int)
    for row in rows:
        totals[row["category"]] += row["bytes"]
    record = {"recorded": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "roots": [str(r) for r in ROOTS], "large_files": sorted(rows, key=lambda r: -r["bytes"]),
              "small_files_by_directory": {k: {"bytes": v[0], "files": v[1]} for k, v in sorted(small.items(), key=lambda kv: -kv[1][0])},
              "bytes_by_category_large_files": dict(sorted(totals.items())), "bytes_small_files": sum(v[0] for v in small.values())}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(record, indent=1) + "\n")
    gib = 2**30
    for category, value in sorted(totals.items()):
        print(f"{category}  {value / gib:9.2f} GiB")
    print(f"small files {record['bytes_small_files'] / gib:.2f} GiB; {len(rows)} large files -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
