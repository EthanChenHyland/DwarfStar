"""Render the DwarfStar app icon from its SVG source into an .icns (and the Workstation's web mark).

    python3 app/workstation/icon/build_icon.py [--out DIR]

The SVG (dwarfstar.svg) is the only source. AppKit renders it (render.swift, compiled on the fly; no third-party
tools), at every size an .icns holds: 16, 32, 128, 256 and 512 points at 1x and 2x. Images of 64 px and below use the
same art without the tick ring (the group id="detail"), which is noise at that size, and with a heavier orbit. iconutil packs the set into
DwarfStar.icns. app/workstation/build.py calls this, so a fresh app build always carries the current icon; nothing
rendered is committed.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "dwarfstar.svg"
# (file name in the .iconset, pixels)
SIZES = [("icon_16x16.png", 16), ("icon_16x16@2x.png", 32), ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
         ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256), ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
         ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024)]
SMALL = 64  # at or below this many pixels: no tick ring, and a heavier orbit so it survives downsampling


def small_variant(svg: str) -> str:
    """The art without the detail group (which holds no nested groups), with the orbit drawn heavier."""
    small, removed = re.subn(r'<g id="detail"[^>]*>.*?</g>', "", svg, count=1, flags=re.S)
    if not removed:
        raise ValueError('dwarfstar.svg has no <g id="detail"> group')
    return small.replace('stroke-width="11"', 'stroke-width="24"').replace('r="24" fill="#f2f6ff"', 'r="40" fill="#f2f6ff"')


def build(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        renderer = work / "render"
        subprocess.run(["/usr/bin/swiftc", "-O", str(HERE / "render.swift"), "-o", str(renderer)], check=True)
        svg = SOURCE.read_text()
        (work / "full.svg").write_text(svg)
        (work / "small.svg").write_text(small_variant(svg))
        iconset = work / "DwarfStar.iconset"
        iconset.mkdir()
        for name, pixels in SIZES:
            source = work / ("small.svg" if pixels <= SMALL else "full.svg")
            subprocess.run([str(renderer), str(source), str(pixels), str(iconset / name)], check=True)
        icns = out / "DwarfStar.icns"
        subprocess.run(["/usr/bin/iconutil", "-c", "icns", str(iconset), "-o", str(icns)], check=True)
        shutil.copy(iconset / "icon_32x32@2x.png", out / "mark-64.png")
    return icns


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=HERE / "build")
    args = parser.parse_args(argv)
    print(build(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
