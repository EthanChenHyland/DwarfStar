"""Browser metadata in the existing task process registry; not another monitor."""

from pathlib import Path
import json, os, sys
from dwarfstar.task_infrastructure import Store, fingerprint, matches, parent_pid
from dwarfstar.runtime_control import identity


def register(task_id, parent, profile, engine, headed, state=None):
    store = Store(state)
    row = store.get(task_id)
    if not row:
        raise ValueError("Browser owner task is not registered")
    # Register each missing parent edge up to a previously proven task root.
    chain = []
    pid = parent
    known = {p["pid"]: p for p in row.get("processes", []) if matches(p)}
    while pid not in known:
        current = fingerprint(pid)
        if not current or len(chain) > 30:
            raise ValueError("Browser owner is not a task descendant")
        chain.append(current)
        pid = parent_pid(pid)
    parent_row = known[pid]
    for current in reversed(chain):
        parent_row = store.register(task_id, current["pid"], "task-descendant", parent_row)
    row = store.discover(task_id)
    found = [p for p in row["processes"] if p.get("automation_browser") and p.get("browser_profile") == str(Path(profile).resolve())]
    if not found:
        raise ValueError("Browser profile process not observed")
    for p in found:
        p.update(browser_engine=engine, browser_headed=headed)
    # Use the existing store lock/update, preserving other processes and ports.
    from dwarfstar.task_infrastructure import lock, atomic

    with lock(store.root / "tasks.lock"):
        row = store.get(task_id)
        by_id = {(p["pid"], p["birth"], p["argv_sha256"]): p for p in found}
        row["processes"] = [by_id.get((p["pid"], p["birth"], p["argv_sha256"]), p) for p in row["processes"]]
        atomic(store.path(task_id), row)
    return {"task_id": task_id, "profile": profile, "processes": found}


if __name__ == "__main__":
    try:
        d = json.load(sys.stdin)
        print(
            json.dumps(
                {
                    "ok": True,
                    **register(d["task_id"], d["parent"], d["profile"], d["engine"], d["headed"], os.environ.get("DWARFSTAR_STATE_ROOT")),
                }
            )
        )
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        raise SystemExit(1)
