#!/bin/sh
# Cheap local validation: no model is loaded. Run from anywhere.
set -e
here="$(cd "$(dirname "$0")/../.." && pwd -P)"
cd "$here"
python="${DWARFSTAR_PYTHON:-$(command -v python3.12 || command -v python3)}"
export PYTHONPATH="$here/src" PYTHONDONTWRITEBYTECODE=1
echo "== unit tests"
"$python" -B -m unittest discover -s tests -t . -q
echo "== python syntax"
"$python" -B - <<'PY'
import ast, pathlib
files = [p for root in ("src", "agent-tools", "benchmark-runner", "scripts", "tests", "app") for p in pathlib.Path(root).rglob("*.py") if "node_modules" not in p.parts]
for path in files:
    ast.parse(path.read_text(), str(path))
print(len(files), "files parse")
PY
if command -v uvx >/dev/null 2>&1; then echo "== lint"; uvx --quiet ruff check .; fi
echo "== catalog and generated configuration"
bin/dwarfstar models >/dev/null && echo "catalog valid"
"$python" -B -c "
from dwarfstar import catalog, instance
for model in catalog.models():
    problems = instance.check(model)
    print(model, 'generated configuration matches' if not problems else problems)
    assert not problems
"
echo "== benchmark index is reproducible"
bin/dwarfstar bench-index --check
echo "== JavaScript and TypeScript syntax"
for file in agent-tools/bin/*.mjs agent-tools/lib/*.mjs app/workstation/web/*.js; do node --check "$file"; done
if [ -x "$HOME/.bun/bin/bun" ]; then
  out="$(mktemp -d)"
  for file in acp/*.ts agent-tools/extension.ts benchmark-runner/snapshot.ts; do "$HOME/.bun/bin/bun" build --no-bundle "$file" --outdir "$out" >/dev/null; done
  rm -rf "$out"; echo "typescript transpiles"
fi
echo "ALL CHECKS PASSED"
