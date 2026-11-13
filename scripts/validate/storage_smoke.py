"""End-to-end check that every model and the user's chats work from the canonical storage layout.

Through the running Workstation service: the existing chats are listed with their organization; Scope, Swift and
Cyber each start (one at a time, which also exercises model switching), answer a tiny question, and Scope answers an
image question; every chat of the model running last must resume its own agent session (probed without sending
anything, so user chats are not modified); then the model stops. Every resolved path is checked to lie under a canonical
root and nowhere else.

    storage_smoke.py LABEL        writes <assets>/Manifests/evidence/storage/LABEL.json
"""
from __future__ import annotations

import base64
import json
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from dwarfstar import catalog, instance, paths  # noqa: E402

SERVICE = "http://127.0.0.1:8038"


def api(path: str, body: dict | None = None, timeout: float = 900) -> dict:
    request = urllib.request.Request(SERVICE + path, data=json.dumps(body).encode() if body is not None else None,
                                     method="POST" if body is not None else "GET", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:  # the service explains a refusal in the body
        return json.loads(error.read() or b"{}")


def ask(model_id: str, content, effort: str = "none") -> str:
    model = catalog.model(model_id)
    body = {"model": model["served_model_id"], "temperature": 0, "max_tokens": 60, "reasoning_effort": effort,
            "chat_template_kwargs": {"enable_thinking": False}, "messages": [{"role": "user", "content": content}]}
    request = urllib.request.Request(f"http://127.0.0.1:{model['port']}/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.loads(response.read())["choices"][0]["message"]["content"].strip()


def canonical(path: str) -> bool:
    resolved = Path(path).resolve()
    return any(resolved.is_relative_to(root.resolve()) for root in paths.roots().values())


def main(label: str) -> int:
    record: dict = {"label": label, "recorded": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "checks": {}}
    checks = record["checks"]
    chats = api("/api/chats?archived=all")["chats"]
    checks["chats_listed"] = {"ok": bool(chats), "count": len(chats),
                              "chats": [{k: c.get(k) for k in ("id", "title", "pinned", "starred", "tags", "archived", "messages")} for c in chats]}
    resolved = {}
    for model_id in catalog.models():
        argv, env = instance.server_command(model_id)
        files = [*catalog.asset_paths(model_id).values(), Path(argv[0]), Path(env["HOME"])]
        resolved[model_id] = {str(p): canonical(str(p)) for p in files}
    checks["paths_canonical"] = {"ok": all(all(v.values()) for v in resolved.values()), "paths": resolved}
    image = "data:image/png;base64," + base64.b64encode((paths.EVIDENCE / "splash-convergence/vision-probe.png").read_bytes()).decode()
    for model_id in ("scope", "swift", "cyber"):
        started = time.time()
        switch = api("/api/switch", {"profile": f"{model_id}-low"})
        row = {"switch_ok": bool(switch.get("ok")), "error": switch.get("error"), "switch_s": round(time.time() - started, 1)}
        if switch.get("ok"):
            row["answer"] = ask(model_id, "What is 7 times 6? Answer with the number only.")
            row["ok"] = "42" in row["answer"]
            if model_id == "scope":
                row["vision"] = ask(model_id, [{"type": "image_url", "image_url": {"url": image}},
                                               {"type": "text", "text": "What shapes and text are in this image? One sentence."}])
                row["ok"] = row["ok"] and "circle" in row["vision"].lower() and "42" in row["vision"]
        else:
            row["ok"] = False
        checks[f"{model_id}_tiny"] = row
        print(model_id, json.dumps(row), flush=True)
    # Every Cyber chat (Cyber runs last) must resume its own agent session from its new folder; nothing is sent.
    from dwarfstar import chats as chat_store

    probes = [chat_store.probe_resume(c["id"]) for c in chats if c["model"] == "cyber" and c.get("messages")]
    checks["chats_resume_own_session"] = {"ok": bool(probes) and all(p["ok"] for p in probes), "probes": probes}
    print("chats", json.dumps(probes), flush=True)
    # One agent tool turn, in a temporary chat that is deleted afterwards
    temp = api("/api/chats", {"profile": "cyber-low"})["chat"]
    api(f"/api/chats/{temp['id']}/messages", {"text": "Use the shell tool to run `echo storage-$((6*7))` and tell me exactly what it printed."})
    while api(f"/api/chats/{temp['id']}")["progress"]:
        time.sleep(2)
    last = api(f"/api/chats/{temp['id']}")["chat"]["messages"][-1]
    tools = [(t.get("title"), t.get("status")) for t in last.get("tools", [])]
    checks["tool_turn"] = {"ok": last["role"] == "assistant" and "storage-42" in last["text"] and bool(tools), "tools": tools,
                           "reply": last["text"].strip()[:160]}
    api(f"/api/chats/{temp['id']}/delete", {"confirm": temp["id"]})
    print("tool", json.dumps(checks["tool_turn"]), flush=True)
    stop = api("/api/stop", {})
    checks["stop"] = {"ok": bool(stop.get("ok")), "running_after": api("/api/state")["running_models"]}
    record["ok"] = all(c.get("ok") for c in checks.values())
    out = paths.EVIDENCE / "storage" / f"{label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1) + "\n")
    print(json.dumps({"ok": record["ok"], "failed": [k for k, v in checks.items() if not v.get("ok")], "record": str(out)}))
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "check"))
