"""Repository hygiene: what must never be committed. Runs on the tracked files only (git ls-files), no network.

    repo_hygiene.py            scan; exit 1 on any finding

Checks:
  large      a tracked file over 2 MiB (screenshots under docs/assets may be up to 2 MiB; nothing larger is code)
  artifact   model weights, checkpoints, archives, databases or backups by extension
  secret     private keys, API tokens and credential-shaped assignments
  home       a literal home-directory path (/Users/<name>/ or /home/<name>/) in a text file
  chat       Workstation chat records or DwarfStar backups

A line containing "hygiene: allow" is exempt: the marker is the review record, visible where the exemption is.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LARGE_BYTES = 2 * 1024 * 1024
ARTIFACT = re.compile(r"\.(gguf|safetensors|bin|pt|pth|ckpt|npy|npz|onnx|mlmodel|sqlite|db|zip|tar|tgz|gz|xz|7z|dmg|pem|key|p12)$", re.I)
SECRETS = [
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{32,}\b")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}\b")),
    ("Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("credential assignment", re.compile(r"(?i)\b(?:api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*['\"][A-Za-z0-9/+_.-]{20,}['\"]")),
]
HOME_PATH = re.compile(r"/(?:Users|home)/(?!<|\{|\$|name\b|you\b|me\b|user\b|USER\b|runner\b)[A-Za-z0-9._-]+/")
CHAT = re.compile(r"(^|/)chats/c\d{14}-[0-9a-f]{6}/|DwarfStar-Backup-\d{8}-\d{6}")
# Lines that document these patterns rather than contain them.
SELF = {"scripts/validate/repo_hygiene.py", "tests/test_repo_hygiene.py"}


def tracked() -> list[str]:
    result = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True)
    return [p for p in result.stdout.decode().split("\0") if p]


def scan(files: list[str], root: Path = ROOT) -> list[dict]:
    findings = []
    for name in files:
        path = root / name
        if not path.is_file() or path.is_symlink():
            continue
        size = path.stat().st_size
        if size > LARGE_BYTES:
            findings.append({"check": "large", "file": name, "detail": f"{size / 2**20:.1f} MiB"})
        if ARTIFACT.search(name):
            findings.append({"check": "artifact", "file": name, "detail": "weights, archive, database or key by extension"})
        if CHAT.search(name):
            findings.append({"check": "chat", "file": name, "detail": "a chat record or backup is tracked"})
        if name in SELF or size > LARGE_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary (screenshots): size and extension checks above cover them
        for number, line in enumerate(text.splitlines(), 1):
            if "hygiene: allow" in line:  # an explicit, reviewed exemption on the line itself (synthetic fixtures)
                continue
            for label, pattern in SECRETS:
                if pattern.search(line):
                    findings.append({"check": "secret", "file": f"{name}:{number}", "detail": label})
            if HOME_PATH.search(line):
                findings.append({"check": "home", "file": f"{name}:{number}", "detail": HOME_PATH.search(line).group(0)})
    return findings


def main() -> int:
    files = tracked()
    findings = scan(files)
    for row in findings:
        print(f"{row['check']:9} {row['file']}  {row['detail']}")
    print(f"{len(files)} tracked files scanned; {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
