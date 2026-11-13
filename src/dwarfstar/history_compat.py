"""Read-only saved-chat compatibility. Never starts an agent or rewrites history."""

from pathlib import Path
import json, sqlite3

from dwarfstar import catalog as model_catalog
from dwarfstar.paths import CODE, INSTANCES, scratch_root


def namespaces():
    """Agent state directory suffix -> the profile name it belongs to (current and retired)."""
    mapping = dict(json.loads((CODE / "config/history-compatibility.json").read_text())["state_namespaces"])
    for row in model_catalog.profiles():
        mapping[row["state_namespace"]] = row["label"]
    return mapping


def sidebar():
    path = Path.home() / "Library/Application Support/Zed/db/0-stable/db.sqlite"
    if not path.exists():
        return []
    db = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)
    try:
        db.row_factory = sqlite3.Row
        return [
            {**dict(row), "thread_id": bytes(row["thread_id"]).hex()}
            for row in db.execute("SELECT * FROM sidebar_threads WHERE agent_id LIKE 'DwarfStar%'")
        ]
    finally:
        db.close()


def entries(path):
    # OMP files are append-only JSONL, including branches/recovery records.
    values = []
    with path.open(encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                values.append(json.loads(line))
            except json.JSONDecodeError:
                values.append({"type": "parse_error", "line": number, "raw": line.rstrip()})
    return values


def catalog():
    mapping = {"state_namespaces": namespaces()}
    rows = sidebar()
    from dwarfstar.shared_sessions import MOVED_WORKSPACES, _locate

    roots = {*MOVED_WORKSPACES.values(), scratch_root(), *INSTANCES.glob("*/scratch")}
    for row in rows:
        for path in (row.get("folder_paths") or "").split("\n"):
            p, _ = _locate(path) if path else (Path(), False)
            if path and p.is_dir():
                roots.add(p)
    sessions = {}
    seen = set()
    # Only read known DwarfStar agent session banks; do not scan models/caches.
    banks = set()
    for base in roots:
        if not base.is_dir():
            continue
        for work in [base, *[p for p in base.iterdir() if p.is_dir() and not p.is_symlink()]]:
            banks.update(work.glob(".omp-dwarfstar-*-agent/sessions"))
    for bank in sorted(banks):
        for file in sorted(bank.glob("*/*.jsonl")):
            real = file.resolve()
            if real in seen:
                continue
            seen.add(real)
            records = entries(file)
            header = next((v for v in records if v.get("type") == "session"), None)
            if not header:
                continue
            id = header.get("id")
            kind = bank.parent.name[len(".omp-dwarfstar-") : -len("-agent")]
            meta = next((v for v in rows if v.get("session_id") == id), {})
            title = (
                meta.get("title_override")
                or meta.get("title")
                or next((v.get("title") for v in records if v.get("type") == "session_info" and v.get("title")), None)
                or "Saved conversation"
            )
            sessions[id] = {
                "id": "session:" + id,
                "session_id": id,
                "title": title,
                "profile": mapping["state_namespaces"].get(kind, kind),
                "workspace": str(bank.parent.parent),
                "path": str(real),
                "status": "readable",
                "records": len(records),
                "sidebar_agent_id": meta.get("agent_id"),
            }
    result = list(sessions.values())
    for row in rows:
        if row.get("session_id") in sessions:
            continue
        result.append(
            {
                "id": "metadata:" + row["thread_id"],
                "session_id": row.get("session_id"),
                "title": row.get("title_override") or row.get("title") or "Empty conversation",
                "profile": row["agent_id"],
                "workspace": row.get("folder_paths") or "",
                "status": "source_missing" if row.get("session_id") else "metadata_only",
                "metadata": row,
            }
        )
    return sorted(result, key=lambda v: (v["workspace"], v["id"]), reverse=True)


def list_history():
    items = catalog()
    return {
        "ok": True,
        "read_only": True,
        "items": items,
        "readable_sessions": sum(v["status"] == "readable" for v in items),
        "missing_sources": sum(v["status"] == "source_missing" for v in items),
    }


def read_history(id):
    item = next((v for v in catalog() if v["id"] == id), None)
    if not item:
        raise ValueError("Saved conversation not found")
    lines = [
        item["title"],
        "Profile: " + item["profile"],
        "Workspace: " + item["workspace"],
        "Session: " + str(item.get("session_id") or "not recorded"),
        "READ ONLY — original files remain in place.",
        "",
    ]
    if item["status"] == "readable":
        lines.append("Full saved JSONL record, including every branch/tool/recovery event:")
        # Pretty-print every record; do not discard orphaned branches or reasoning.
        for value in entries(Path(item["path"])):
            message = value.get("message", {})
            role = message.get("role", value.get("type", "record"))
            lines.extend(["", "===== " + str(role).upper() + " =====", json.dumps(value, ensure_ascii=False, indent=2)])
    else:
        lines.append(
            "No transcript was recorded for this empty entry."
            if item["status"] == "metadata_only"
            else "The original session source/workspace is absent on disk. Its retained sidebar metadata is shown below; this reader cannot recreate absent messages."
        )
        lines.append(json.dumps(item["metadata"], ensure_ascii=False, indent=2))
    return {"ok": True, "read_only": True, "item": item, "text": "\n".join(lines)}
