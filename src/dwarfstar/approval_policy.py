"""DwarfStar permission policy. ACP approval is not macOS authorization.

The installed ACP transport and benchmark client both call this resolver. Tool
categories are deliberately irrelevant in Full Auto; only protocol shape and
the permission options offered by the agent are validated.
"""

from pathlib import Path
import hashlib, json, os, sys, time

from dwarfstar.paths import SHARED_CONFIG

CONFIG = SHARED_CONFIG / "approval.json"
MODES = {"full_auto": "Full Auto", "safe_auto": "Safe Auto", "ask": "Ask"}


def mode(value=None):
    selected = value or os.environ.get("DWARFSTAR_APPROVAL_MODE")
    if selected is None:
        selected = json.loads(CONFIG.read_text()).get("approval_mode") if CONFIG.exists() else "full_auto"
    if selected not in MODES:
        raise ValueError("Invalid DwarfStar approval mode")
    return selected


def status():
    selected = mode()
    return {
        "approval_mode": selected,
        "label": MODES[selected],
        "default": selected,
        "scope": "New DwarfStar ACP sessions; historical sessions unchanged",
        "os_authorization": "External OS authorization required when enforced by macOS",
    }


def validate(params):
    if not isinstance(params, dict) or not isinstance(params.get("sessionId"), str) or not params["sessionId"]:
        raise ValueError("Malformed ACP permission session")
    call = params.get("toolCall")
    if not isinstance(call, dict) or not isinstance(call.get("toolCallId"), str) or not call["toolCallId"]:
        raise ValueError("Malformed ACP permission tool call")
    options = params.get("options")
    if not isinstance(options, list) or not options:
        raise ValueError("Missing ACP permission options")
    ids = set()
    for option in options:
        if not isinstance(option, dict) or not all(isinstance(option.get(k), str) and option[k] for k in ("optionId", "name", "kind")):
            raise ValueError("Malformed ACP permission option")
        if option["kind"] not in ("allow_once", "allow_always", "reject_once", "reject_always") or option["optionId"] in ids:
            raise ValueError("Invalid ACP permission option")
        ids.add(option["optionId"])
    return options


def record(value, journal=None):
    path = journal or os.environ.get("DWARFSTAR_PERMISSION_JOURNAL")
    if not path:
        return
    # No commands, credentials, rawInput, page content or personal browser data.
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("a") as stream:
            stream.write(json.dumps({"timestamp": time.time(), **value}) + "\n")
    except OSError:
        pass  # A logging failure must not introduce a permission wait.


def resolve_request(message, selected=None, journal=None):
    if not isinstance(message, dict) or message.get("method") != "session/request_permission":
        return None
    if message.get("jsonrpc") != "2.0" or type(message.get("id")) not in (str, int):
        raise ValueError("Malformed JSON-RPC permission request")
    options = validate(message.get("params"))
    selected = mode(selected)
    if selected != "full_auto":
        return None
    start = time.perf_counter()
    approved = next((o for o in options if o["kind"] == "allow_once"), None) or next(
        (o for o in options if o["kind"] == "allow_always"), None
    )
    if approved is None:
        raise ValueError("ACP permission request offers no approval option")
    result = {"jsonrpc": "2.0", "id": message["id"], "result": {"outcome": {"outcome": "selected", "optionId": approved["optionId"]}}}
    record(
        {
            "event": "acp_permission_decision",
            "approval_mode": selected,
            "tool_call_id_sha256": hashlib.sha256(message["params"]["toolCall"]["toolCallId"].encode()).hexdigest(),
            "automatic": True,
            "decision": "approved",
            "option_kind": approved["kind"],
            "acp_approval_wait_s": 0,
            "decision_processing_s": time.perf_counter() - start,
        },
        journal,
    )
    return result


def counters(paths):
    rows = []
    for path in set(map(str, paths)):
        try:
            for line in Path(path).read_text().splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
        except FileNotFoundError:
            pass
    requests = [r for r in rows if r.get("event") == "acp_permission_decision"]
    return {
        "permission_request_count": len(requests),
        "automatically_approved_count": sum(r.get("automatic") is True and r.get("decision") == "approved" for r in requests),
        "denied_count": sum(r.get("decision") == "denied" for r in requests),
        "external_os_authorization_count": sum(r.get("event") == "external_os_authorization" for r in rows),
        "total_acp_approval_wait_s": sum(r.get("acp_approval_wait_s", 0) for r in requests),
    }


if __name__ == "__main__":
    try:
        data = json.load(sys.stdin)
        value = resolve_request(data["message"], data.get("approval_mode")) if "message" in data else status()
        print(json.dumps({"ok": True, "value": value}))
    except (ValueError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        sys.exit(1)
