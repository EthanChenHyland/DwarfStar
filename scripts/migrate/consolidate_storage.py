"""One-time consolidation of the 2026-11 storage layout into DwarfStar's canonical roots (src/dwarfstar/paths.py).

Every step is a rename on the same APFS volume: no data is copied, so no second copy of a 30-40 GB model ever exists.
Each moved file's byte count is checked after the move, and model files are re-hashed against the SHA-256 the
catalog or the conversion receipts recorded. Nothing is deleted here; retired files are handled separately.

    consolidate_storage.py                 dry run: print the plan and what is already done
    consolidate_storage.py --apply         perform it and write <ASSETS>/Manifests/migration/storage-consolidation.json

LEGACY is the old setup root (the directory that held challengers/ and dwarfstar-data/).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HOME = Path.home()
LEGACY = Path(os.environ.get("DWARFSTAR_LEGACY_ROOT", HOME / "local-ai-setup"))
OLD = LEGACY / "dwarfstar-data"
CH = LEGACY / "challengers"
ASSETS, DATA, CACHE, LOGS = HOME / "AI/DwarfStar", HOME / "Library/Application Support/DwarfStar", \
    HOME / "Library/Caches/DwarfStar", HOME / "Library/Logs/DwarfStar"

# (source, destination, expected SHA-256 or None): model files are re-hashed after the move.
PLAN = [
    (CH / "dwarfstar-paperniuk/models/Q2_0/Qwen3.8-Flash-Next-GSQ-RCO-Q2_0-00001-of-00002.gguf",
     ASSETS / "Models/Scope/Qwen3.8-Flash-Next-GSQ-RCO-Q2_0-00001-of-00002.gguf", "69820c02ec7d0b45ef2ebb19d6620299db749fe2aded7f39f93c6b88b199b720"),
    (CH / "dwarfstar-paperniuk/models/Q2_0/Qwen3.8-Flash-Next-GSQ-RCO-Q2_0-00002-of-00002.gguf",
     ASSETS / "Models/Scope/Qwen3.8-Flash-Next-GSQ-RCO-Q2_0-00002-of-00002.gguf", "316b46f3a2dbd68c900f43136ab9449f9dcc3725dfd8c794847c204bc161e113"),
    (OLD / "backends/splash-m1/artifacts/swift/Swift1.5-GSQ-RCO-Q2_0-00001-of-00002.gguf",
     ASSETS / "Models/Swift/Swift1.5-GSQ-RCO-Q2_0-00001-of-00002.gguf", "0322b94eebdf63436a634afca6d926cfd48458b4a20e67dbc2f2144f04dda4d5"),
    (CH / "dwarfstar-swift15/models/Swift1.5-PLE-IQ4_NL.gguf",  # becomes part 2 itself: no link
     ASSETS / "Models/Swift/Swift1.5-GSQ-RCO-Q2_0-00002-of-00002.gguf", "a3755824b9931b70ea2a33fa8054f3c1f445835e61266733ac5a9ebefb596b1f"),
    (OLD / "backends/splash-m1/artifacts/cyber/DwarfStarCyber-Q2_0-00001-of-00002.gguf",
     ASSETS / "Models/Cyber/DwarfStarCyber-Q2_0-00001-of-00002.gguf", "3aefd0a8db4fb60f1d83b02d735d4470c27ed1230ab4bd63d98225be7fdd7b43"),
    (CH / "dwarfstar-cyber/models/DwarfStarCyber-PLE-IQ4_NL.gguf",
     ASSETS / "Models/Cyber/DwarfStarCyber-Q2_0-00002-of-00002.gguf", "0737e0e8fcd16583462e36b8ba5dcf1cdf33f101364e1d49a06a7fa1e170198c"),
    (CH / "dwarfstar-cyber/models/mmproj-DwarfStarCyber-F32.gguf",
     ASSETS / "Models/Cyber/mmproj-DwarfStarCyber-F32.gguf", "159ec18133749e1cb5a781ae496b8da2a35b7d16d0972c94dd63c6942d141a56"),
    (OLD / "backends/splash-m1/drafts/Qwen3.8-Flash-Next-DFlash2", ASSETS / "Models/Shared/Qwen3.8-Flash-Next-DFlash2", None),
    (OLD / "backends/splash-m1/artifacts/mmproj-Qwen3.8-Flash-Next-BF16.gguf",
     ASSETS / "Models/Shared/mmproj-Qwen3.8-Flash-Next-BF16.gguf", "b1a82259702816a5330d7bd7607cd9676b11780e79ff7348c21103ff3ce49bd0"),
    (OLD / "backends/splash-m1/1.6.1-m1-ds1", ASSETS / "Runtimes/splash-m1-1.6.1-ds1", None),
    (OLD / "backends/splash-m1/1.6.1-m1", ASSETS / "Sources/splash-m1/1.6.1-m1-release", None),
    (OLD / "build/splash-src", ASSETS / "Sources/splash-m1/splash-src", None),
    (OLD / "evidence", ASSETS / "Manifests/evidence", None),
    (OLD / "migration/GIT-HISTORY-REBUILD.json", ASSETS / "Manifests/migration/GIT-HISTORY-REBUILD.json", None),
    (OLD / "migration/zed-backups", DATA / "backups/zed", None),
    (OLD / "chats", DATA / "chats", None),
    (OLD / "state", DATA / "state", None),
    (OLD / "config", DATA / "config", None),
    (OLD / "telemetry", DATA / "telemetry", None),
    (OLD / "backends/splash-m1/home", DATA / "runtime/splash-home", None),
    (OLD / "cache/agent-tools", CACHE / "agent-tools", None),
    (OLD / "logs/lifecycle.jsonl", LOGS / "lifecycle.jsonl", None),
    (OLD / "logs", LOGS / "archive/2026-11", None),
    (OLD / "backends/splash-m1/runs", LOGS / "archive/2026-11/splash-runs", None),
]
for model in ("scope", "swift", "cyber"):
    PLAN.append((OLD / "instances" / model / "state", DATA / "instances" / model / "state", None))


def tree_bytes(path: Path) -> int:
    if path.is_symlink():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(16 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    rows = []
    for source, target, expected in PLAN:
        state = "done" if target.exists() and not source.exists() else "pending" if source.exists() else "missing"
        if state == "pending" and target.exists():
            state = "conflict: both exist"
        rows.append({"source": str(source), "target": str(target), "state": state, "expected_sha256": expected,
                     "bytes": tree_bytes(source if source.exists() else target) if state != "missing" else None})
        print(f"{state:<20} {rows[-1]['bytes'] or 0:>15,}  {source}  ->  {target}")
    if not apply:
        return 0
    if any(r["state"].startswith("conflict") for r in rows):
        print("refusing: a target already exists for a pending source")
        return 1
    for row in rows:
        if row["state"] != "pending":
            continue
        source, target = Path(row["source"]), Path(row["target"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.stat().st_dev != target.parent.stat().st_dev:
            raise SystemExit(f"refusing a cross-volume move (would copy): {source}")
        row["bytes"] = tree_bytes(source)  # measured now: an earlier step may have moved part of this tree
        os.rename(source, target)  # same volume: a rename, nothing copied
        moved = tree_bytes(target)
        if moved != row["bytes"]:
            raise SystemExit(f"byte count changed moving {source}: {row['bytes']} -> {moved}")
        row.update(state="moved", moved_at=time.time())
    hashed = [r for r in rows if r["expected_sha256"] and Path(r["target"]).is_file()]
    with ThreadPoolExecutor(4) as pool:
        for row, digest in zip(hashed, pool.map(lambda r: sha256(Path(r["target"])), hashed)):
            row["sha256"] = digest
            row["sha256_ok"] = digest == row["expected_sha256"]
            print(f"{'sha256 ok' if row['sha256_ok'] else 'SHA-256 MISMATCH'}  {row['target']}")
    receipt = ASSETS / "Manifests/migration/storage-consolidation.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({"recorded": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "steps": rows}, indent=1) + "\n")
    print(f"receipt: {receipt}")
    return 0 if all(r.get("sha256_ok", True) for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
