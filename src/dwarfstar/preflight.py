"""Read-only build admission. Does not start/stop/query a model server."""
from pathlib import Path
import argparse,json,os,hashlib,subprocess
ap=argparse.ArgumentParser();ap.add_argument('--baseline',required=True);ap.add_argument('--manifest',required=True);ap.add_argument('--root',required=True);a=ap.parse_args();root=Path(a.root).resolve();baseline=json.loads(Path(a.baseline).read_text());manifest=json.loads(Path(a.manifest).read_text())
if root!=Path('$LOCAL_AI_SETUP/challengers/dwarfstar-swift15'):raise SystemExit('Incorrect isolated root')
bundle=Path(a.manifest).resolve().parent
for relative,expected in manifest['toolchain']['source_hashes'].items():
 if hashlib.sha256((bundle/relative).read_bytes()).hexdigest()!=expected:raise SystemExit('Frozen toolchain changed: '+relative)
for relative,expected in [('source-tensor-manifest.json',manifest['source_tensor_manifest_sha256']),('production-matched-layout.json',manifest['quant_recipe']['reference_sha256']),('mtp-architecture-delta.json',manifest['mtp_architecture_delta_sha256'])]:
 if hashlib.sha256((bundle/relative).read_bytes()).hexdigest()!=expected:raise SystemExit('Frozen build specification changed: '+relative)
for path,expected in baseline['protected'].items():
 if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=expected:raise SystemExit('Production changed: '+path)
for path,target in baseline['symlinks'].items():
 if not Path(path).is_symlink() or os.readlink(path)!=target:raise SystemExit('Production symlink changed')
for asset in baseline['model_and_history_stats']:
 st=Path(asset['path']).stat()
 if (st.st_size,st.st_ino,st.st_mtime_ns)!=(asset['current_size'],asset['current_inode'],asset['current_mtime_ns']):raise SystemExit('Protected asset changed: '+asset['path'])
free=os.statvfs(root if root.exists() else root.parent).f_bavail*os.statvfs(root if root.exists() else root.parent).f_frsize
if free<600*2**30:raise SystemExit('At least600GiB free is required')
print(json.dumps({'admitted':True,'free_GiB':free/2**30,'root':str(root),'no_server_lifecycle_actions':True}))
