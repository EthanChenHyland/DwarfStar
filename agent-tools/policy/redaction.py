"""Secret discovery and fail-closed redaction of tool results.

The base functions find likely credentials in a workspace and redact them, and their
common encodings, from text. The strengthened variants add private keys, quoted
assignments and sensitive JSON keys; they are the ones exported as ``redact``,
``redact_obj`` and ``secret_values``.
"""

from pathlib import Path
import base64, json, os, re, urllib.parse

TOKEN_PATTERNS = [
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:sk|ghp|github_pat|hf)[-_][A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|password|access[_-]?token|auth[_-]?token)\s*[=:]\s*[\"\']?([^\s\"\',;]+)"),
]


def _base_secret_values(root):
    values = set()
    for p in Path(root).rglob("*"):
        if (
            p.is_symlink()
            or not p.resolve().is_relative_to(Path(root).resolve())
            or not p.is_file()
            or ".git" in p.parts
            or any(t in p.parts for t in [".venv", "node_modules", ".aci", ".tmp"])
        ):
            continue
        if p.suffix not in [".env", ".json", ".yaml", ".yml", ".toml", ".txt", ".py", ".md"] and p.name != ".env":
            continue
        if p.stat().st_size > 250000:
            continue
        try:
            s = p.read_text()
        except (UnicodeError, OSError):
            continue
        for pattern in TOKEN_PATTERNS:
            for m in pattern.finditer(s):
                value = m[1] if m.lastindex else m[0]
                if value not in ["true", "false", "null", "[REDACTED]"] and len(value) >= 12:
                    values.add(value)
    return values


def _base_redact(text, values=()):
    text = str(text)
    variants = set(values)
    for s in values:
        variants |= {base64.b64encode(s.encode()).decode(), s.encode().hex(), urllib.parse.quote(s, safe="")}
    for s in sorted(variants, key=len, reverse=True):
        text = text.replace(s, "[REDACTED]")

    # Redact printable encoded blobs containing a known value, including whole .env lines.
    def encoded(m):
        try:
            raw = base64.b64decode(m[0] + "=" * ((-len(m[0])) % 4), validate=True).decode()
            if any(v in raw for v in values) or any(p.search(raw) for p in TOKEN_PATTERNS):
                return "[REDACTED ENCODED]"
        except (ValueError, UnicodeError):
            pass
        return m[0]

    text = re.sub(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{24,}={0,2}", encoded, text)
    for pattern in TOKEN_PATTERNS:
        text = pattern.sub(lambda m: m[0].replace(m[1], "[REDACTED]") if m.lastindex else "[REDACTED]", text)
    return text


def root_path(value=None):
    return Path(value or os.environ.get("V7_WORKSPACE") or os.getcwd()).resolve()


def emit(x):
    print(json.dumps(x, ensure_ascii=False, default=str))


SENSITIVE_KEY = re.compile(
    r"(?i)(?:^|[_-])(?:api[_-]?key|secret|password|passwd|access[_-]?token|auth[_-]?token|authorization|credential)(?:$|[_-])"
)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----[\s\S]+?-----END (?:[A-Z]+ )?PRIVATE KEY-----")
QUOTED_ASSIGN = re.compile(
    r"""(?i)\b(?:api[_-]?key|secret|password|passwd|access[_-]?token|auth[_-]?token|authorization|credential)["']?\s*[=:]\s*["']?([^\s"',;}]+)"""
)


def secure_values(root):
    values = set()
    root = Path(root).resolve()
    excluded = {".git", ".venv", "node_modules", ".aci", ".tmp", ".measure", ".omp-agent", ".omp-root", ".xdg"}

    def files():
        for directory, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if name not in excluded and not (Path(directory) / name).is_symlink()]
            for name in names:
                yield Path(directory) / name

    for p in files():
        if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(root):
            continue
        if p.stat().st_size > 250000:
            continue
        try:
            text = p.read_text()
        except (UnicodeError, OSError):
            continue
        for pattern in [*TOKEN_PATTERNS, QUOTED_ASSIGN, PRIVATE_KEY]:
            for m in pattern.finditer(text):
                value = m[1] if m.lastindex else m[0]
                if len(value) >= 6 and value not in ["[REDACTED]", "example", "false"]:
                    values.add(value)
        if p.suffix == ".json":
            try:

                def walk(x):
                    if isinstance(x, dict):
                        for k, v in x.items():
                            if SENSITIVE_KEY.search(k) and isinstance(v, str) and v:
                                values.add(v)
                            walk(v)
                    elif isinstance(x, list):
                        for v in x:
                            walk(v)

                walk(json.loads(text))
            except ValueError:
                pass
    return values


def secure_redact(text, values=()):
    clean = PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", _base_redact(text, values))
    return QUOTED_ASSIGN.sub(lambda m: m[0].replace(m[1], "[REDACTED]"), clean)


def secure_obj(x, values=()):
    if isinstance(x, str):
        return secure_redact(x, values)
    if isinstance(x, dict):
        return {k: "[REDACTED]" if SENSITIVE_KEY.search(str(k)) and isinstance(v, str) else secure_obj(v, values) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [secure_obj(v, values) for v in x]
    return x


redact = secure_redact
redact_obj = secure_obj
secret_values = secure_values
