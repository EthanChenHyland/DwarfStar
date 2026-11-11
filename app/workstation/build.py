#!/usr/bin/env python3
"""Build the unified DwarfStar Workstation. Does not install over an existing app, launch, or touch a model.

  build.py --output BINARY                 compile only (like the per-installation build.py)
  build.py --bundle PATH.app [--name N]    compile, assemble a new .app at PATH (must not exist), ad-hoc sign
"""
from pathlib import Path
import argparse, plistlib, shutil, subprocess, tempfile
root = Path(__file__).resolve().parent
src = root / 'Sources'
p = argparse.ArgumentParser(); g = p.add_mutually_exclusive_group(required=True)
g.add_argument('--output'); g.add_argument('--bundle'); p.add_argument('--name', default='DwarfStar Workstation'); a = p.parse_args()

def compile_to(out):
    with tempfile.TemporaryDirectory(prefix='dwarfstar-ui-build-') as temporary:
        main = Path(temporary) / 'main.swift'; main.write_bytes((src / 'DwarfStarWorkstation.swift').read_bytes())
        subprocess.run(['/usr/bin/swiftc', '-O', str(main), str(src / 'WorkstationCore.swift'), str(src / 'BenchmarkMonitor.swift'), '-o', str(out)], check=True)

if a.output:
    compile_to(a.output)
else:
    app = Path(a.bundle)
    if app.exists(): raise SystemExit(f'Refusing to overwrite existing bundle: {app}')
    with tempfile.TemporaryDirectory(prefix='dwarfstar-ui-bundle-') as temporary:
        staged = Path(temporary) / app.name
        (staged / 'Contents/MacOS').mkdir(parents=True); (staged / 'Contents/Resources').mkdir()
        compile_to(staged / 'Contents/MacOS/DwarfStarWorkstation')
        info = {'CFBundleDisplayName': a.name, 'CFBundleName': a.name, 'CFBundleExecutable': 'DwarfStarWorkstation',
                'CFBundleIdentifier': 'local.dwarfstar.Workstation.unified', 'CFBundlePackageType': 'APPL',
                'CFBundleShortVersionString': '2.0', 'CFBundleVersion': '2', 'LSMinimumSystemVersion': '14.0', 'NSHighResolutionCapable': True}
        with (staged / 'Contents/Info.plist').open('wb') as f: plistlib.dump(info, f)
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(staged)], check=True)  # ad-hoc, no identity, no settings change
        shutil.copytree(staged, app, symlinks=True)
    print(app)
