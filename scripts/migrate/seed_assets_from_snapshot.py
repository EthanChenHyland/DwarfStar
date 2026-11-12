#!/usr/bin/env python3
"""Record verified model-file identity from a migration snapshot instead of re-hashing.

A snapshot taken with ``--hash-models`` already holds a SHA-256 for every model
file. For each catalog asset whose file still has the snapshot's size, mtime and
inode and whose snapshot digest equals the catalog digest, write the instance's
``assets.json``. Anything that does not match is left for ``dwarfstar assets --hash``.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from dwarfstar import catalog, instance  # noqa: E402
from dwarfstar.paths import DATA  # noqa: E402

snapshot = json.loads(Path(sys.argv[1]).read_text())
by_inode = {}
for record in snapshot["installations"].values():
    for asset in record.get("model_assets", []):
        if "sha256" in asset:
            by_inode[asset["inode"]] = asset
report = {}
for model_id, model in catalog.models().items():
    rows, errors = [], []
    for role, asset in model["assets"].items():
        path = DATA / asset["path"]
        info = os.stat(path)
        seen = by_inode.get(info.st_ino)
        if not seen or seen["bytes"] != info.st_size or seen["mtime_ns"] != info.st_mtime_ns:
            errors.append(f"{role}: not covered by the snapshot")
        elif seen["sha256"] != asset["sha256"] or info.st_size != asset["bytes"]:
            errors.append(f"{role}: snapshot digest differs from the catalog")
        else:
            rows.append({"role": role, "path": str(path), "bytes": info.st_size, "mtime_ns": info.st_mtime_ns, "sha256": asset["sha256"]})
    result = {"schema": 1, "model": model_id, "verified": not errors, "verified_at": time.time(),
              "mechanism": "SHA-256 from migration snapshot " + snapshot["created_utc"] + "; size, mtime and inode unchanged",
              "files": rows, "errors": errors}
    if not errors:
        instance.atomic_write(instance.instance(model_id) / "assets.json", json.dumps(result, indent=2) + "\n")
    report[model_id] = {"verified": not errors, "errors": errors}
print(json.dumps(report, indent=2))
