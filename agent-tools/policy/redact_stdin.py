"""Redact one tool result read from stdin; the workspace root is argv[1]. Fails closed in the caller."""

import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from policy.redaction import emit, redact_obj, root_path, secret_values

root = root_path(sys.argv[1])
value = json.loads(sys.stdin.read())
emit(redact_obj(value, secret_values(root)))
