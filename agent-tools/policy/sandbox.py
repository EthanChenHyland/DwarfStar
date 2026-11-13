"""Safe Auto sandbox profile and workspace boundary check (macOS seatbelt).

Used only when the approval mode is not Full Auto. Writes are confined to the
workspace, the network is denied except the listed loopback ports, and the
user's home directory is unreadable except for the tool locations listed here.
"""

from pathlib import Path
import json, os, sys

sys.path.insert(0, os.environ.get("DWARFSTAR_HOME", str(Path(__file__).resolve().parents[2])) + "/src")
from dwarfstar import paths


def sb_quote(x):
    return json.dumps(str(x))


def sandbox_profile(root, ports=(), read_extra=()):
    root = Path(root).resolve()
    home = Path.home()
    allowed = [
        root,
        *paths.extra_read_paths(),
        home / ".cache/codex-runtimes",
        paths.omp_packages().parent,
        paths.bun().parent,
        Path("/opt/homebrew"),
        *map(Path, read_extra),
    ]
    # OS write/net boundary applies to every child process, independent of command spelling.
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        "(allow file-write* (subpath " + sb_quote(root) + "))",
        '(allow file-write* (literal "/dev/null"))',
        "(deny network*)",
        "(allow network-bind (local unix-socket))",
        "(allow network-inbound (local unix-socket))",
        "(deny appleevent-send)",
        '(deny process-exec (literal "/usr/bin/osascript") (literal "/bin/launchctl") (literal "/usr/bin/sudo"))',
        "(deny file-read* (subpath " + sb_quote(home) + "))",
        "(allow file-read-metadata (subpath " + sb_quote(home) + "))",
    ]
    for p in allowed:
        lines.append("(allow file-read* (subpath " + sb_quote(p.resolve()) + "))")
    for parent in sorted({q for p in allowed for q in Path(p).resolve().parents if q.is_relative_to(home)} | {home}, key=str):
        lines.append("(allow file-read* (literal " + sb_quote(parent) + "))")
    for p in [home / "Library/Fonts", home / "Library/Preferences/.GlobalPreferences.plist"]:
        lines.append("(allow file-read* (subpath " + sb_quote(p) + "))")
    for port in ports:
        port = int(port)
        if port < 1 or port > 65535:
            raise ValueError("Invalid allowed loopback port")
        lines.append('(allow network-outbound (remote tcp "localhost:' + str(port) + '"))')
    return "\n".join(lines) + "\n"


def check_workspace(root):
    root = Path(root).resolve()
    bad = []
    for p in root.rglob("*"):
        if p.is_symlink():
            if not p.resolve().is_relative_to(root):
                bad.append(str(p.relative_to(root)) + ": external symlink")
        elif p.is_file() and p.stat().st_nlink > 1:
            bad.append(str(p.relative_to(root)) + ": shared inode")
        if len(bad) >= 20:
            break
    if bad:
        raise ValueError("Workspace boundary requires independent files: " + ", ".join(bad))
    return True


def policy(root, ports=(), read_extra=()):
    check_workspace(root)
    return sandbox_profile(root, ports, read_extra) + "(deny file-link)\n"
