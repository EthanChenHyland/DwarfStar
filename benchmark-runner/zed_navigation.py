"""Workspace navigation only. Never speaks ACP or selects an External Agent."""
from pathlib import Path
import json, os, subprocess, threading, time
from records import NE, TERMINAL, save, read, now
from app_navigation import open_target
CLI='/Applications/Zed.app/Contents/MacOS/cli'
LIMITATION='The benchmark owns a standalone ACP connection. Zed exposes no supported CLI to attach this live connection/session. Workspace only; do not start another External Agent for the same benchmark.'
def target(row):
    workspace=Path(row['workspace'])
    if not workspace.is_dir() or workspace.is_symlink():raise ValueError('Benchmark workspace no longer exists')
    if row['status']=='QUEUED' and not row.get('acp_session_id'):raise ValueError('Queued benchmark is not initialized')
    return workspace.resolve()
def mapping(row):
    return {'schema':1,'run_id':row['run_id'],'workspace':str(Path(row['workspace']).resolve()),'acp_session_id':row.get('acp_session_id',NE),'profile':row['profile_name'],'zed_workspace_id':NE,'zed_window_id':NE,'navigation':'Supported CLI exact directory path; Zed handles existing project matching','exact_acp_attachment_supported':False,'limitation':LIMITATION}
def open_zed(row,run=subprocess.run):
    start=time.perf_counter();result={**mapping(row),'navigation_started_at':now(),'model_requests':0,'acp_sessions_created':0}
    try:
        path=target(row)
        version=run([CLI,'--version'],capture_output=True,text=True,timeout=5)
        result['zed_version']=version.stdout.strip()
        # No -n (duplicates), -a/-e (frontmost workspace), agent/session command,
        # DB access, settings change, or window-title guessing.
        navigation=open_target('zed',target=str(path),run=run)
        result.update(ok=navigation['ok'],returncode=0 if navigation['ok'] else 1)
        if not navigation['ok']:result['error']='Zed workspace open failed'
    except Exception as e:result.update(ok=False,error=type(e).__name__+': '+str(e))
    result['navigation_wall_s']=time.perf_counter()-start;result['navigation_ended_at']=now()
    # Supplemental monitor receipt ONLY, never run.json, index or timing files.
    try:save(Path(row['artifact_directory'])/'monitor/zed-navigation.json',result)
    except Exception:pass
    return result
def initialized(row):
    """Run separately from agent execution; failures have no orchestration path."""
    def work():
        try:
            folder=Path(row['artifact_directory'])/'monitor';save(folder/'zed-target.json',mapping(row))
            group=read(Path(row['artifact_directory']).parent.parent/'groups'/row.get('group_id','')/'group.json',{})
            if group.get('monitor_options',{}).get('open_zed',False):auto_open_once(row)
        except Exception:pass
    threading.Thread(target=work,daemon=True).start()
def auto_open_once(row,opener=open_zed):
    try:
        folder=Path(row['artifact_directory'])/'monitor';folder.mkdir(exist_ok=True)
        fd=os.open(folder/'auto-open-once.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:json.dump({'attempted_at':now(),'run_id':row['run_id']},f)
        return opener(row)
    except Exception:return {'ok':False,'monitor_only':True}
