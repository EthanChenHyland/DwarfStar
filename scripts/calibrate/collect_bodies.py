#!/usr/bin/env python3
"""Collect calibration requests by running short, benign agent sessions.

Each session works on a throwaway workspace and saves the exact request bodies
the agent sent (system prompt, tool schemas, tool calls and results). These
are calibration inputs only: none of the tasks is used by any test suite.

    collect_bodies.py PROFILE OUT_DIR
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts/validate"))
from acp_client import run_session  # noqa: E402

TASKS = [
    {
        "name": "inventory-total",
        "files": {"inventory.py": 'ITEMS = [\n    {"name": "bolt", "price": 0.25, "quantity": 400},\n    {"name": "hinge", "price": 3.5, "quantity": 12},\n]\n'},
        "turns": ["Add a function total_value(items) to inventory.py that returns the sum of price times quantity, "
                  "add a small unittest file test_inventory.py for it, run the tests and tell me the result."],
    },
    {
        "name": "summarise-readme",
        "files": {"README.md": "# Tide Tables\n\nA tiny library that parses harbour tide tables.\n\n## Install\n\npip install tidetables\n\n## Usage\n\nCall parse(path) to get a list of (time, height) tuples.\n\n## Limits\n\nOnly the 2019 CSV layout is supported.\n",
                  "notes/todo.txt": "support the 2021 layout\nadd a command line tool\n"},
        "turns": ["List the files in this project, read README.md and notes/todo.txt, then write SUMMARY.md with exactly three bullet points describing the project."],
    },
    {
        "name": "wordcount",
        "files": {"sample.txt": "the quick brown fox jumps over the lazy dog\nthe dog sleeps\n"},
        "turns": ["Create wordcount.py that prints the number of words in a file given as its first argument, run it on sample.txt and report the number.",
                  "Now also make it print the number of lines, and run it again."],
    },
    {
        "name": "temperature-bug",
        "files": {"temperature.py": "def c_to_f(c):\n    return c * 9 / 5 - 32\n\n\ndef f_to_c(f):\n    return (f - 32) * 5 / 9\n",
                  "test_temperature.py": "import unittest\nfrom temperature import c_to_f, f_to_c\n\n\nclass T(unittest.TestCase):\n    def test_boiling(self):\n        self.assertEqual(c_to_f(100), 212)\n\n    def test_round_trip(self):\n        self.assertAlmostEqual(f_to_c(c_to_f(37.5)), 37.5)\n\n\nif __name__ == '__main__':\n    unittest.main()\n"},
        "turns": ["Run the unit tests, find the bug, fix it with the smallest change and run the tests again."],
    },
    {
        "name": "node-squares",
        "files": {},
        "turns": ["Write squares.mjs, a Node script that prints the squares of 1 through 10 on one line separated by spaces, then run it and show me the output."],
    },
    {
        "name": "explain",
        "files": {"shapes.py": "class Shape:\n    def area(self):\n        raise NotImplementedError\n\n\nclass Circle(Shape):\n    def __init__(self, r):\n        self.r = r\n\n    def area(self):\n        return 3.14159 * self.r ** 2\n"},
        "turns": ["Read shapes.py and explain in four sentences what it does. Do not change any file."],
    },
]


def main() -> int:
    profile, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    summary = []
    for task in TASKS:
        workspace = out / "workspaces" / task["name"]
        workspace.mkdir(parents=True, exist_ok=True)
        for name, text in task["files"].items():
            path = workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        subprocess.run(["git", "init", "-q", "."], cwd=workspace, check=False)
        dump = out / "bodies" / task["name"]
        dump.mkdir(parents=True, exist_ok=True)
        result = run_session(out / "sessions" / task["name"], [REPO / "bin/dwarfstar-acp", "--profile", profile], workspace,
                             [{"text": text, "timeout": 420} for text in task["turns"]],
                             extra_env={"DWARFSTAR_WIRE_DUMP_DIR": str(dump)})
        row = {"task": task["name"], "requests": result["requests"], "bodies": len(list(dump.glob("*.json"))),
               "exception": result.get("exception"), "replies": [turn["reply"][:120] for turn in result["turns"]]}
        summary.append(row)
        print(json.dumps(row), flush=True)
    (out / "collect-summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
