"""User-facing application navigation, never automation-browser navigation."""
from pathlib import Path
import argparse, json, subprocess, os
from urllib.parse import urlsplit, unquote, quote
from task_infrastructure import Store, matches

ZED='/Applications/Zed.app/Contents/MacOS/cli'
CONTROLLER=Path(__file__).resolve().parent/'workstation/DwarfStarWorkstation'
LIMITATION='Workspace/file navigation only. Zed has no supported CLI to attach the standalone ACP conversation. No task or agent is started.'

def default_open_plan(target):
    result=subprocess.run([str(CONTROLLER),'--default-open-plan',target],capture_output=True,text=True,timeout=8)
    if result.returncode:raise ValueError('System default application could not be resolved')
    value=json.loads(result.stdout)
    if not value.get('ok'):raise ValueError(value.get('error','System application unavailable'))
    return value

def browser_target(workspace, task_id=None, artifact=None):
    root=Path(workspace).resolve()
    if not root.is_dir(): raise ValueError('Workspace missing')
    if task_id:
        store=Store(os.environ.get('DWARFSTAR_STATE_ROOT'));row=store.get(task_id)
        if row and Path(row['workspace']).resolve()==root:
            # Refresh only listeners of already-owned task processes. No bind,
            # server launch, inference request or arbitrary localhost guessing.
            store.observe_ports(task_id);row=store.get(task_id)
            candidates=[p for p in row.get('ports',[]) if not p.get('released_at') and matches(p.get('process'))
                        and p.get('port') not in (8000,8010,8020,8021,8030,8031,8032)]
            # 8000 is a legitimate task development port too; the model endpoints
            # above are excluded, but an explicitly owned 8000 listener is safe.
            candidates += [p for p in row.get('ports',[]) if p.get('port')==8000 and not p.get('released_at') and matches(p.get('process'))]
            if candidates:
                suffix=''
                if artifact:
                    path=(root/artifact).resolve()
                    if not path.is_relative_to(root):raise ValueError('Artifact outside selected workspace')
                    suffix=quote(str(path.relative_to(root)))
                return 'http://localhost:'+str(candidates[-1]['port'])+'/'+suffix
    path=(root/artifact).resolve() if artifact else root/'index.html'
    if not path.is_file():raise ValueError('No live task-owned web server or standalone HTML artifact')
    if path.suffix.lower() not in ('.html','.htm'):raise ValueError('Browser action requires an HTML artifact')
    return path.as_uri()

def open_target(action, target=None, workspace=None, task_id=None, artifact=None, run=subprocess.run):
    if action=='browser':
        if target:
            u=urlsplit(target)
            if u.scheme in ('http','https') and u.netloc: resolved=target
            elif u.scheme=='file':
                if u.netloc not in ('','localhost'):raise ValueError('Remote file URL is unsupported')
                path=Path(unquote(u.path)).resolve()
                if not path.is_file() or path.suffix.lower() not in ('.html','.htm'):raise ValueError('Browser action requires an existing HTML artifact')
                resolved=path.as_uri()
            elif not u.scheme:
                path=Path(target).expanduser().resolve()
                if not path.is_file() or path.suffix.lower() not in ('.html','.htm'):raise ValueError('Browser action requires an existing HTML artifact')
                resolved=path.as_uri()
            else:raise ValueError('Unsupported browser URL scheme')
        else: resolved=browser_target(workspace,task_id,artifact)
        plan=default_open_plan(resolved);args=plan['command']
    elif action in ('zed','file-zed'):
        path=Path(target or workspace).expanduser().resolve()
        if not path.exists() or action=='file-zed' and not path.is_file():raise ValueError('Zed target missing')
        resolved=str(path);args=[ZED,resolved]
    elif action=='workspace':
        path=Path(target or workspace).resolve()
        if not path.is_dir():raise ValueError('Workspace missing')
        resolved=str(path);args=['/usr/bin/open','-a','Finder',resolved]
    else:raise ValueError('Unknown navigation action')
    result=run(args,capture_output=True,text=True,timeout=15)
    return {'ok':result.returncode==0,'action':action,'target':resolved,'command':args,
            'route':'macOS default application' if action=='browser' else 'Supported Zed CLI' if action in ('zed','file-zed') else 'Finder',
            'model_requests':0,'acp_sessions_created':0,'task_restarted':False,
            'limitation':LIMITATION if action in ('zed','file-zed') else '',
            **({'default_application':plan['default_application'],'headless_bundle_collision':plan['headless_bundle_collision']} if action=='browser' else {}),
            **({'error':result.stderr[-500:]} if result.returncode else {})}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['browser','zed','file-zed','workspace']);p.add_argument('--target');p.add_argument('--workspace');p.add_argument('--task-id');p.add_argument('--artifact');a=p.parse_args()
    try:print(json.dumps(open_target(a.action,a.target,a.workspace,a.task_id,a.artifact)))
    except (ValueError,OSError,subprocess.SubprocessError) as e:print(json.dumps({'ok':False,'error':str(e)}));raise SystemExit(1)
