#!/bin/sh
# Builds DwarfStar's Splash-m1 runtime: the pinned 1.6.1-m1 release with DwarfStar's patches.
#
#   build_splash.sh SOURCE_CHECKOUT RELEASE_DIST OUT_DIR
#
# SOURCE_CHECKOUT  a clone of https://github.com/paperniuk/splash (any state; its tracked files are reset to the tag)
# RELEASE_DIST     the unpacked release tarball's splash-1.6.1-m1-arm64-macos26 directory (Python, server, installer)
# OUT_DIR          the runtime to create (must not exist): the release, APFS-cloned, with the patched engine, shaders
#                  and metadata adapter (install/gguf.py)
#
# Needs Xcode 26 with the Metal toolchain (xcodebuild -downloadComponent MetalToolchain). Prints the SHA-256 pins for
# config/runtimes.json.
set -eu
here="$(cd "$(dirname "$0")" && pwd -P)"
patches="$here/../../backends/splash-m1/patches"
src="$(cd "$1" && pwd -P)"; dist="$(cd "$2" && pwd -P)"; out="$(cd "$(dirname "$3")" && pwd -P)/$(basename "$3")"
[ ! -e "$out" ] || { echo "error: $out exists" >&2; exit 1; }
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
xcrun -sdk macosx metal --version >/dev/null

cd "$src"
git checkout -q 1.6.1-m1 -- .
git diff --quiet HEAD -- . || { echo "error: could not reset $src to 1.6.1-m1" >&2; exit 1; }
for patch in "$patches"/*.patch; do git apply "$patch"; echo "applied $(basename "$patch")"; done
make -j"$(sysctl -n hw.ncpu)" build/splash build/splash.metallib build/host-step.metallib >/dev/null

cp -cR "$dist" "$out"
cp build/splash build/splash.metallib build/host-step.metallib "$out/engine/"
cp install/gguf.py "$out/install/gguf.py"
printf '{\n  "base": "1.6.1-m1",\n  "patches": [%s]\n}\n' \
  "$(for p in "$patches"/*.patch; do printf '"%s",' "$(basename "$p")"; done | sed 's/,$//')" > "$out/dwarfstar-build.json"
cd "$out"
shasum -a 256 engine/splash engine/splash.metallib engine/host-step.metallib install/gguf.py
