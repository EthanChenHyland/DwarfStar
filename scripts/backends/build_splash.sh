#!/bin/sh
# Builds DwarfStar's Splash-m1 runtime: the pinned 1.6.1-m1 release with DwarfStar's patches.
#
#   build_splash.sh SOURCE_CHECKOUT RELEASE_DIST OUT_DIR [--telemetry | --ds3]
#
# SOURCE_CHECKOUT  a clone of https://github.com/paperniuk/splash (any state; its tracked files are reset to the tag)
# RELEASE_DIST     the unpacked release tarball's splash-1.6.1-m1-arm64-macos26 directory (Python, server, installer)
# OUT_DIR          the runtime to create (must not exist): the release, APFS-cloned, with the patched engine, shaders
#                  and metadata adapter (install/gguf.py)
# --telemetry      also build the ds2 advanced-telemetry variant: apply backends/splash-m1/patches/telemetry/*.patch
#                  to the source and overlay the patched server/*.py. Engine-only otherwise; ds1 (no flag) is unchanged.
# --ds3            the ds3 GPU-telemetry experiment: the ds2 telemetry patches, then backends/splash-m1/patches/ds3/*.patch
#                  (the MoE grouping kernel's router histogram page and its reader), with the same server overlay.
#
# Needs Xcode 26 with the Metal toolchain (xcodebuild -downloadComponent MetalToolchain). Prints the SHA-256 pins for
# config/runtimes.json.
set -eu
here="$(cd "$(dirname "$0")" && pwd -P)"
patches="$here/../../backends/splash-m1/patches"
telemetry=""; ds3=""
case "${4:-}" in
  --telemetry) telemetry="$patches/telemetry" ;;
  --ds3) telemetry="$patches/telemetry"; ds3="$patches/ds3" ;;
  "") ;;
  *) echo "error: unknown option ${4}" >&2; exit 1 ;;
esac
src="$(cd "$1" && pwd -P)"; dist="$(cd "$2" && pwd -P)"; out="$(cd "$(dirname "$3")" && pwd -P)/$(basename "$3")"
[ ! -e "$out" ] || { echo "error: $out exists" >&2; exit 1; }
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
xcrun -sdk macosx metal --version >/dev/null

cd "$src"
git checkout -q 1.6.1-m1 -- .
git diff --quiet HEAD -- . || { echo "error: could not reset $src to 1.6.1-m1" >&2; exit 1; }
rm -f runtime/engine/Telemetry.hpp runtime/ops/MoeTelemetry.hpp  # new files from the patches; removed so a re-run applies cleanly
for patch in "$patches"/*.patch; do git apply "$patch"; echo "applied $(basename "$patch")"; done
if [ -n "$telemetry" ]; then
  for patch in "$telemetry"/*.patch; do git apply "$patch"; echo "applied telemetry/$(basename "$patch")"; done
fi
if [ -n "$ds3" ]; then
  for patch in "$ds3"/*.patch; do git apply "$patch"; echo "applied ds3/$(basename "$patch")"; done
fi
make -j"$(sysctl -n hw.ncpu)" build/splash build/splash.metallib build/host-step.metallib >/dev/null

cp -cR "$dist" "$out"
cp build/splash build/splash.metallib build/host-step.metallib "$out/engine/"
cp install/gguf.py "$out/install/gguf.py"
if [ -n "$telemetry" ]; then
  # The telemetry server patch changes pure-Python server files the engine binary does not contain;
  # overlay them from the patched source onto the cloned release.
  for f in server/server.py server/backend.py server/runtime.py server/protocol.py; do cp "$f" "$out/$f"; done
fi
if [ -n "$ds3" ]; then
  # ds3 records its variant and names patches by directory; ds1 and ds2 keep their original format.
  printf '{\n  "base": "1.6.1-m1",\n  "patches": [%s],\n  "telemetry": true,\n  "variant": "ds3-gpu-telemetry"\n}\n' \
    "$(for p in "$patches"/*.patch "$telemetry"/*.patch "$ds3"/*.patch; do printf '"%s",' "$(basename "$(dirname "$p")")/$(basename "$p")"; done | sed 's/,$//; s#"patches/#"#g')" \
    > "$out/dwarfstar-build.json"
else
printf '{\n  "base": "1.6.1-m1",\n  "patches": [%s],\n  "telemetry": %s\n}\n' \
  "$(for p in "$patches"/*.patch ${telemetry:+"$telemetry"/*.patch}; do printf '"%s",' "$(basename "$p")"; done | sed 's/,$//')" \
  "$([ -n "$telemetry" ] && echo true || echo false)" > "$out/dwarfstar-build.json"
fi
cd "$out"
shasum -a 256 engine/splash engine/splash.metallib engine/host-step.metallib install/gguf.py
