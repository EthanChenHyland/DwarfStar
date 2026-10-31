"""DwarfStar development boundary; no shared benchmark/runtime policy changes."""
from pathlib import Path
import json, os, sys

ROOT = Path(__file__).resolve().parent
SHARED = ROOT.parents[2]
sys.path.insert(0, str(SHARED / 'v7/tools'))
from boundary_v2 import policy, check_workspace as strict_check_workspace
sys.path.insert(0,str(ROOT.parent))
from approval_policy import mode

def check_workspace(workspace):
    # OMP's content-addressed image store creates an extension alias by hardlink.
    # Materialize only complete, verified intra-store alias groups. External
    # links and every other workspace path still face the original strict check.
    import hashlib, re, shutil, tempfile
    root=Path(workspace).resolve()
    for blobs in root.glob('.omp-dwarfstar-*-agent/blobs'):
        if blobs.is_symlink() or blobs.parent.is_symlink() or not blobs.is_dir():continue
        groups={}
        for path in blobs.iterdir():
            if path.is_symlink() or not path.is_file():continue
            st=path.stat()
            if st.st_nlink>1:groups.setdefault((st.st_dev,st.st_ino),[]).append(path)
        for paths in groups.values():
            if not all(re.fullmatch(r'[0-9a-f]{64}(?:\.(?:png|jpg|jpeg|webp))?',p.name) for p in paths):continue
            st=paths[0].stat()
            if st.st_nlink!=len(paths):continue
            with paths[0].open('rb') as stream:
                digest=hashlib.sha256()
                for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
            if any(p.name.split('.')[0]!=digest.hexdigest() for p in paths):continue
            for path in paths:
                before=path.stat()
                if (before.st_dev,before.st_ino)!=(st.st_dev,st.st_ino):raise ValueError('Image blob identity changed')
                fd,tmp=tempfile.mkstemp(prefix='.independent-image-',dir=blobs);os.close(fd)
                try:
                    shutil.copy2(path,tmp)
                    if path.stat().st_ino!=before.st_ino:raise ValueError('Image blob identity changed')
                    os.replace(tmp,path)
                finally:Path(tmp).unlink(missing_ok=True)
    return strict_check_workspace(root)

def tooling_policy(workspace, read_extra=()):
    if mode()=='full_auto':return '(version 1)\n(allow default)\n'
    # Retain workspace/symlink checks, private-home reads and file-write denial.
    rules = policy(workspace, [8030], read_extra=[ROOT, *read_extra])
    rules = rules.replace('(deny network*)', '''(deny network*)
(allow network-outbound)
(allow network-bind (local tcp "localhost:*"))
(allow network-inbound (local tcp "localhost:*"))''')
    # Keep launcher/security code immutable to the model. Reusable utility scripts,
    # package manifests/dependencies and caches are the approved external writes.
    for name in ['node_modules','cache','state','utilities']:
        rules += '(allow file-write* (subpath ' + json.dumps(str(ROOT/name)) + '))\n'
    for name in ['package.json','package-lock.json','pnpm-lock.yaml','bun.lock','bun.lockb']:
        rules += '(allow file-write* (literal ' + json.dumps(str(ROOT/name)) + '))\n'
    # Explicitly keep real browser profiles and credential stores inaccessible.
    for path in ['.ssh', '.aws', '.azure', '.config/gcloud', '.kube', '.netrc', '.npmrc', '.git-credentials',
                 'Library/Keychains', 'Library/Application Support/Google',
                 'Library/Application Support/Chromium', 'Library/Application Support/Firefox',
                 'Library/Safari', 'Library/Containers/com.apple.Safari']:
        rules += '(deny file-read* file-write* (subpath ' + json.dumps(str(Path.home() / path)) + '))\n'
    rules += '(deny file-read* file-write* (subpath "/Library/Keychains"))\n'
    rules += '(deny process-exec (literal "/usr/bin/security") (literal "/opt/homebrew/bin/brew"))\n'
    return rules

def environment(workspace, temp):
    """No inherited account tokens, proxy credentials, personal npmrc or shell rc."""
    bundled_pnpm = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback'
    optional = ':'+str(bundled_pnpm) if (bundled_pnpm/'pnpm').exists() else ''
    result={
        'DWARFSTAR_TOOLING_ACTIVE': '1', 'DWARFSTAR_AGENT_TOOLS': str(ROOT),
        'DWARFSTAR_WORKSPACE': str(workspace),
        'DWARFSTAR_APPROVAL_MODE':mode(), 'V7_SEATBELT_ACTIVE':'0' if mode()=='full_auto' else '1',
        'PATH': str(ROOT/'bin') + ':' + str(Path.home()/'.bun/bin') + ':/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin' + optional,
        'NODE_PATH': str(ROOT/'node_modules'),
        'npm_config_userconfig': str(ROOT/'.npmrc'), 'npm_config_globalconfig': '/dev/null',
        'npm_config_cache': str(ROOT/'cache/npm'), 'npm_config_registry': 'https://registry.npmjs.org/',
        'PUPPETEER_SKIP_DOWNLOAD': 'true', 'PUPPETEER_CACHE_DIR': str(ROOT/'cache/browsers'),
        'PLAYWRIGHT_BROWSERS_PATH': str(ROOT/'cache/browsers'),
        'BUN_INSTALL_CACHE_DIR': str(ROOT/'cache/bun'), 'COREPACK_HOME': str(ROOT/'cache/corepack'),
        'PIP_CACHE_DIR': str(ROOT/'cache/pip'), 'PIP_CONFIG_FILE': '/dev/null',
        'UV_CACHE_DIR': str(ROOT/'cache/uv'),
        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0',
        'GIT_SSH_COMMAND': '/usr/bin/false', 'GIT_ASKPASS': '/usr/bin/false',
        'MAC_CHROMIUM_TMPDIR': str(temp),
    }
    if mode()=='full_auto':
        # These are DwarfStar-imposed Safe Auto blocks, not OS authorization.
        # Full Auto must not silently substitute a failing SSH/askpass command.
        for key in ('GIT_CONFIG_NOSYSTEM','GIT_CONFIG_GLOBAL','GIT_TERMINAL_PROMPT','GIT_SSH_COMMAND','GIT_ASKPASS'):result.pop(key,None)
    return result

if __name__ == '__main__':
    # Infrastructure validation entry point. Never launches a model or ACP.
    workspace = Path(sys.argv[1]).resolve()
    temp = workspace/'.tooling-tmp'; temp.mkdir(exist_ok=True)
    env = {'HOME':str(Path.home()), 'LANG':'en_US.UTF-8', 'TMPDIR':str(temp),
           'PYTHONDONTWRITEBYTECODE':'1', **environment(workspace,temp)}
    os.chdir(workspace)
    command=sys.argv[2:]
    if mode()=='full_auto':os.execvpe(command[0],command,env)
    else:os.execve('/usr/bin/sandbox-exec', ['sandbox-exec','-p',tooling_policy(workspace),*command], env)
