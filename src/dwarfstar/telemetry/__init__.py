"""Structured telemetry.

Producers (the runtime controller, the agent/tool layer and the native
runtime) send small JSON datagrams to a UDP port on the loopback interface.
The optional hub (``dwarfstar.telemetry.hub``) receives them, keeps a bounded
history and serves them to the Workstation.

Sending is fire-and-forget. With no hub listening the datagram is dropped by
the kernel, so an idle installation pays one failed ``sendto`` per event and
nothing else. Telemetry never blocks, never raises and never carries prompt
text, tool arguments, file contents or credentials.
"""
from __future__ import annotations

import json
import os
import socket
import time

SCHEMA_VERSION = 1
DEFAULT_EVENT_PORT = 8039  # UDP, producers -> hub
DEFAULT_HTTP_PORT = 8038  # HTTP + SSE, hub -> Workstation
DEFAULT_CONTROL_PORT = 8037  # UDP, hub -> native runtime (detail level heartbeats)
MAX_DATAGRAM = 8192

_socket: socket.socket | None = None


def event_port() -> int:
    return int(os.environ.get("DWARFSTAR_TELEMETRY_PORT", DEFAULT_EVENT_PORT))


def enabled() -> bool:
    return os.environ.get("DWARFSTAR_TELEMETRY", "1") != "0"


def emit(kind: str, **fields) -> None:
    """Send one event. Never blocks or raises."""
    global _socket
    if not enabled():
        return
    try:
        if _socket is None:
            _socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            _socket.setblocking(False)
        payload = {"v": SCHEMA_VERSION, "t": time.time(), "src": "controller", "kind": kind}
        if os.environ.get("DWARFSTAR_MODEL"):
            payload["model"] = os.environ["DWARFSTAR_MODEL"]
        payload.update(fields)
        data = json.dumps(payload, separators=(",", ":")).encode()
        if len(data) <= MAX_DATAGRAM:
            _socket.sendto(data, ("127.0.0.1", event_port()))
    except (OSError, ValueError, TypeError):
        pass
