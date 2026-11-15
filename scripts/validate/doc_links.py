"""Every relative link in README.md and docs/*.md resolves to a tracked file (and every #anchor to a heading).

    doc_links.py      exit 1 on a broken link; external (http/https/mailto) links are not fetched
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)|!\[[^\]]*\]\(([^)\s]+)\)")


def slug(heading: str) -> str:
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    return {slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+)$", path.read_text(encoding="utf-8"), re.M)}


def check(root: Path = ROOT) -> list[str]:
    broken = []
    for doc in [root / "README.md", *sorted((root / "docs").glob("*.md"))]:
        text = re.sub(r"```.*?```", "", doc.read_text(encoding="utf-8"), flags=re.S)  # code blocks are not links
        for match in LINK.finditer(text):
            target = match.group(1) or match.group(2)
            if re.match(r"^(https?:|mailto:)", target):
                continue
            file_part, _, anchor = target.partition("#")
            destination = (doc.parent / file_part).resolve() if file_part else doc
            if file_part and not destination.exists():
                broken.append(f"{doc.relative_to(root)}: {target} (no such file)")
            elif anchor and destination.suffix == ".md" and slug(anchor) not in anchors(destination):
                broken.append(f"{doc.relative_to(root)}: {target} (no such heading)")
    return broken


def main() -> int:
    broken = check()
    for line in broken:
        print(line)
    print(f"{len(broken)} broken link(s)")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
