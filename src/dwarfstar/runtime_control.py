#!$LOCAL_AI_SETUP/v6/.venv/bin/python
"""Independent pinned DwarfStar controller. Never operates another runtime."""
from pathlib import Path
import argparse,datetime,fcntl,hashlib,json,os,re,signal,socket,subprocess,sys,time,urllib.request,shutil,uuid
ROOT=Path(__file__).resolve().parent
STATE=ROOT/'state';STATE.mkdir(mode=0o700,exist_ok=True)
CONFIG=ROOT/'runtime.json'
from profiles import profile,registry
def config():return json.loads(CONFIG.read_text())
def run(args,**kw):return subprocess.run(args,text=True,capture_output=True,timeout=kw.pop('timeout',5),**kw)
def identity(pid):
    if not isinstance(pid,int) or pid<2:return None
    r=run(['/bin/ps','-p',str(pid),'-o','stat=,lstart=,command=']);v=r.stdout.strip().split(None,6)
    if r.returncode or len(v)!=7 or v[0].startswith('Z'):return None
    return {'pid':pid,'birth':' '.join(v[1:6]),'command':v[6],'argv_sha256':hashlib.sha256(v[6].encode()).hexdigest()}
def read(path,default=None):
    try:return json.loads(Path(path).read_text())
    except (OSError,ValueError):return default
def owned():
    row=read(STATE/'server.json');current=identity(row.get('pid')) if row else None
    if not current:return None
    if any(row.get(k)!=current[k] for k in ('pid','birth','argv_sha256')):raise RuntimeError('DwarfStar PID identity changed; no process will be signalled.')
    if not current['command'].startswith(str(ROOT/'ds4-server')+' '):raise RuntimeError('Ledger is not the isolated DwarfStar server.')
    return {**row,**current}
def health():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8031/v1/models',timeout=1) as stream:value=json.load(stream)
        item=next(v for v in value['data'] if v['id']=='qwen3.8-flash-next')
        return {'ok':item['context_length']==131072 and item['top_provider']['max_completion_tokens']==81920,'model':item}
    except Exception:return {'ok':False}
def occupied(port):
    with socket.socket() as s:s.settimeout(.15);return s.connect_ex(('127.0.0.1',port))==0
def exclude_other_models():
    ps=run(['/bin/ps','-axo','pid=,command=']).stdout.splitlines()
    for line in ps:
        words=line.strip().split(None,1)
        if len(words)!=2:continue
        pid,cmd=int(words[0]),words[1]
        if pid==os.getpid():continue
        exe=cmd.split(None,1)[0]
        if (Path(exe).name=='moespresso' and ' serve ' in cmd) or Path(exe).name=='llama-server' or (Path(exe).name.startswith('python') and ' -m mtplx.server.openai ' in cmd) or Path(exe).name=='ds4-server':
            raise RuntimeError(f'Another heavyweight runtime is running (PID {pid}). Stop it in its own application first; DwarfStar changed nothing.')
    for port in (8000,8010,8020,8021,8030,8031):
        if occupied(port):raise RuntimeError(f'Local port {port} is occupied. No process was stopped.')
def pinned():
    c=config();head=run(['/usr/bin/git','-C',str(ROOT/'runtime-src'),'rev-parse','HEAD']).stdout.strip()
    if head!=c['source']['commit']:raise RuntimeError('Source revision changed; refusing a moving/unreviewed build.')
    if run(['/usr/bin/git','-C',str(ROOT/'runtime-src'),'diff','--quiet','HEAD','--','.',':(exclude)ds4.c']).returncode:raise RuntimeError('Tracked native source/shaders changed; review/rebuild before starting.')
    if hashlib.sha256((ROOT/'runtime-src/ds4.c').read_bytes()).hexdigest()!=c['tokenizer_adapter_sha256']:raise RuntimeError('Reviewed Swift tokenizer adapter changed.')
    if hashlib.sha256((ROOT/'ds4-server').read_bytes()).hexdigest()!=c['server_sha256']:raise RuntimeError('DwarfStar binary changed; rebuild/review before starting.')
    assets=read(ROOT/'installation-evidence/assets.json')
    if not assets or not assets.get('verified'):raise RuntimeError('Complete verified DwarfStar assets are required.')
    for asset in assets['files']:
        info=Path(asset['path']).stat()
        if info.st_size!=asset['bytes'] or info.st_mtime_ns!=asset['mtime_ns']:raise RuntimeError('Asset identity changed; verify checksums before loading.')
    if c['context']!=131072 or c['max_output']!=81920:raise RuntimeError('The pinned 131072/81920 baseline was changed.')
    return c
def serve_owned():
    # Read-only existing exclusion lock: bytes/config/state of Qwen Workstation are never written.
    # Existing legacy launchers already honor this OS lock, preventing simultaneous loads.
    lab=open('$LOCAL_AI_SETUP/runtime/lab.lock','r')
    try:fcntl.flock(lab,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise RuntimeError('Another local runtime holds exclusive model admission. Stop it in its own application; nothing was changed.')
    os.set_inheritable(lab.fileno(),True)
    exclude_other_models();c=pinned();env=dict(os.environ)
    for key in list(env):
        if key.startswith(('DS4_','DSTAR_','MOESPRESSO_','MTPLX_','LLAMA_ARG_')):del env[key]
    env.update(c['environment']);os.chdir(ROOT)
    os.execve(str(ROOT/'ds4-server'),c['argv'],env)
def start():
    row=owned()
    if row:
        if not health()['ok']:raise RuntimeError('Existing owned DwarfStar is not healthy; no duplicate/restart was attempted.')
        return {'ok':True,'already_running':True,'pid':row['pid']}
    exclude_other_models();c=pinned()
    stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');log=STATE/f'server-{stamp}.log'
    started_at=time.time()
    with log.open('w') as stream:
        p=subprocess.Popen([sys.executable,'-B',str(Path(__file__)),'_serve'],stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
    deadline=time.monotonic()+600
    while time.monotonic()<deadline:
        if p.poll() is not None:
            tail=log.read_text(errors='replace')[-2200:]
            raise RuntimeError(f'DwarfStar startup exited {p.returncode}. Endpoint 127.0.0.1:8031.\n{tail}\nLog: {log}')
        current=identity(p.pid)
        if current and current['command'].startswith(str(ROOT/'ds4-server')+' '):
            ledger={**current,'log':str(log),'started_at':started_at}
            save(STATE/'server.json',ledger)
            if health()['ok']:
                ledger['mtp_block_loaded']='ds4: MTP block ' in native_log(ledger)
                save(STATE/'server.json',ledger)
                return {'ok':True,'pid':p.pid,'health':health(),'mtp_block_loaded':ledger['mtp_block_loaded']}
        time.sleep(.5)
    raise RuntimeError(f'Owned startup remains pending: {log}. No duplicate or forced kill was attempted.')
def native_log(row):
    if not row:return ''
    try:
        p=Path(row['log'])
        with p.open('rb') as f:f.seek(max(0,p.stat().st_size-131072));return f.read().decode(errors='replace')
    except OSError:return ''
def native_metrics(text):
    from telemetry import parse_native
    return parse_native(text)

def save(path,value):
    path=Path(path);temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value)+'\n');temp.chmod(0o600);temp.replace(path)
def requests(row):
    if not row:return []
    values=[]
    basic=read(STATE/'request.json',{})
    if basic.get('server_pid')==row['pid'] and basic.get('server_birth')==row['birth']:values.append(basic)
    for path in (STATE/'clients').glob('request-*.json'):
        if path.is_symlink() or path.stat().st_size>16384:continue
        value=read(path,{})
        if value.get('server_pid')!=row['pid'] or value.get('server_birth')!=row['birth']:continue
        client=identity(value.get('client_pid'))
        if not client or client['birth']!=value.get('client_birth'):continue
        values.append(value)
    return values
def status():
    from telemetry import native_snapshot,process_resources,assemble
    c=config();errors=[]
    try:row=owned()
    except Exception as e:row=None;errors.append('Process identity collection error: '+type(e).__name__)
    h=health() if row else {'ok':False}
    if row and not h['ok']:errors.append('Health collection error: model endpoint unavailable or configuration mismatch')
    native,log_errors=native_snapshot(row or read(STATE/'last-stopped.json'),STATE/'telemetry-log-cursor.json');errors+=log_errors
    try:clients=requests(row)
    except Exception as e:clients=[];errors.append('ACP metadata collection error: '+type(e).__name__)
    resource,resource_errors=process_resources(row['pid'] if row else None,ROOT,STATE/'telemetry-cache-size.json');errors+=resource_errors
    try:request,phase=assemble(c,row,h,native,clients,resource)
    except Exception as e:request,phase={},'IDLE';errors.append('Request aggregation collection error: '+type(e).__name__)
    if native.get('log_catching_up'):errors.append('Native log catch-up pending; bounded incremental read')
    if request.get('telemetry_error'):errors.append('ACP telemetry collection error')
    if request.get('active') and time.time()-request.get('observed_at',0)>8:errors.append('ACP metadata collection error: stale receipt; no model stall or completion inferred')
    last_native={k:v for k,v in native.items() if k.startswith('mtp_')} if not row else {}
    mtp_state='MTP enabled · native block loaded' if row and row.get('mtp_block_loaded') else 'MTP configured · native adaptive' if row else 'MTP configured · runtime offline'
    tooling=read(ROOT/'agent-tools/state/capabilities.json',{})
    from approval_policy import status as permission_status
    return {'ok':True,'permissions':permission_status(),'running':bool(row),'healthy':h['ok'],'pid':row['pid'] if row else None,'uptime_s':resource.get('process_uptime_s'),'endpoint':c['endpoint'],'configuration':c,'native':native if row else {},'last_session_native':last_native,'mtp_state':mtp_state,'request':request,'phase':phase,'resources':resource,'collection_errors':errors,'tooling':tooling,'log':row['log'] if row else (read(STATE/'last-stopped.json',{}) or {}).get('log')}

def stop():
    row=owned()
    if not row:return {'ok':True,'already_offline':True}
    if any(r.get('active') or r.get('tool_execution_active') for r in requests(row)):raise RuntimeError('DwarfStar request/tool is active; finish/cancel it first. Nothing stopped.')
    connections=run(['/usr/sbin/lsof','-nP','-a','-p',str(row['pid']),'-iTCP:8031','-sTCP:ESTABLISHED'])
    if connections.returncode not in (0,1):raise RuntimeError('Cannot verify client connections; refusing stop.')
    if connections.returncode==0 and connections.stdout.strip():raise RuntimeError('A client is connected. Finish the turn before stopping.')
    owned();os.kill(row['pid'],signal.SIGTERM);deadline=time.monotonic()+60
    while time.monotonic()<deadline and identity(row['pid']):time.sleep(.2)
    if identity(row['pid']) or occupied(8031):raise RuntimeError('Shutdown pending; no forced kill attempted.')
    save(STATE/'last-stopped.json',{**row,'stopped_at':time.time()});(STATE/'server.json').unlink()
    return {'ok':True,'stopped':row['pid']}
def launch_workspace(kind,folder=None):
    selected=profile(kind)
    start()
    scratch=Path(config()['scratch_directory'])
    root=Path(folder).expanduser().resolve() if folder else scratch/(datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    if folder:assert root.is_dir()
    else:root.mkdir(parents=True)
    from preflight import require
    require(root,kind)
    cli='/Applications/Zed.app/Contents/MacOS/cli'
    subprocess.Popen([cli,'-n',str(root)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    profile_name=selected['name']
    return {'ok':True,'workspace':str(root),'profile':profile_name,'policy':selected['policy'],'instruction':'Agent Panel → top-right + → External Agents → '+profile_name}
def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','stop','restart','status','health','logs','chat','history','_serve']);parser.add_argument('--kind',choices=list(registry()['profiles']),default=registry()['default']);parser.add_argument('--folder');parser.add_argument('--history-id');a=parser.parse_args()
    if a.action=='_serve':serve_owned();return
    if a.action=='history':
        from history_compat import list_history,read_history
        value=read_history(a.history_id) if a.history_id else list_history()
    elif a.action in ('status','health','logs'):
        value=status() if a.action=='status' else health() if a.action=='health' else {'ok':True,'log':status()['log']}
    else:
        with (STATE/'control.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if a.action=='restart':stop();value=start()
            elif a.action=='start':value=start()
            elif a.action=='stop':value=stop()
            else:value=launch_workspace(a.kind,a.folder)
    print(json.dumps(value),flush=True)
if __name__=='__main__':
    try:main()
    except Exception as e:print(json.dumps({'ok':False,'error':str(e)}),flush=True);sys.exit(1)
