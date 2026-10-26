"""Bounded infrastructure admission; never contacts a generation endpoint."""
from pathlib import Path
import concurrent.futures, hashlib, json, os, shutil, socket, subprocess, tempfile, time, signal, sys
from task_infrastructure import ROOT, STATE, atomic, read, lock
from approval_policy import mode as approval_mode

def writable(path):
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='.preflight-',dir=path):pass
    return True
def probe(command,timeout=4):
    from task_infrastructure import fingerprint,matches,children
    sys.path.insert(0,str(ROOT/'agent-tools'))
    from boundary import tooling_policy,environment
    try:
        with tempfile.TemporaryDirectory(prefix='dwarfstar-preflight-') as folder:
            workspace=Path(folder).resolve()
            rules=tooling_policy(workspace,read_extra=[ROOT/'infrastructure-browser-probe.mjs'])
            env={'HOME':str(Path.home()),'LANG':'en_US.UTF-8','TMPDIR':str(workspace),**environment(workspace,workspace)}
            p=subprocess.Popen([*(['/usr/bin/sandbox-exec','-p',rules] if approval_mode()!='full_auto' else []),*command],cwd=workspace,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
            try:out,err=p.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Only descendants of this exact launched probe. No name/port kills.
                known=[];pending=[fingerprint(p.pid)]
                for parent in pending:
                    if not matches(parent):continue
                    for pid in children(parent['pid']):
                        child=fingerprint(pid)
                        if child and matches(parent):known.append(child);pending.append(child)
                for child in reversed(known):
                    if matches(child):
                        try:os.kill(child['pid'],signal.SIGTERM)
                        except ProcessLookupError:pass
                if p.poll() is None:p.terminate()
                try:p.communicate(timeout=1)
                except subprocess.TimeoutExpired:p.kill();p.communicate()
                for child in known:
                    if matches(child):
                        try:os.kill(child['pid'],signal.SIGKILL)
                        except ProcessLookupError:pass
                return {'ok':False,'detail':'Bounded sandbox probe timeout'}
            detail=out.strip()[:240] if command[-1].endswith('infrastructure-browser-probe.mjs') else 'Sandbox probe succeeded' if p.returncode==0 else err.strip().split('\n')[0][:160]
            return {'ok':p.returncode==0,'detail':detail}
    except (OSError,subprocess.TimeoutExpired) as e:return {'ok':False,'detail':type(e).__name__}
def environment_probes(refresh=False):
    """At most one cached local/browser/network probe per 10 minutes. Never inference.
    Browser launch uses the existing library with ephemeral profile; no web suite.
    """
    cache=STATE/'environment-preflight.json'
    with lock(STATE/'environment-preflight.lock'):
        old=read(cache,{})
        if not refresh and time.time()-old.get('epoch',0)<600:return {**old,'cached':True}
        commands={
            'Shell internet':['/usr/bin/curl','-I','--silent','--fail','--max-time','3','https://registry.npmjs.org/'],
            'Node internet':['/opt/homebrew/bin/node','--input-type=module','-e',"const r=await fetch('https://registry.npmjs.org/',{method:'HEAD',signal:AbortSignal.timeout(3000)});process.exit(r.ok?0:1)"],
            'Browser probe':['/opt/homebrew/bin/node',str(ROOT/'infrastructure-browser-probe.mjs')]}
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures={k:pool.submit(probe,c,8 if k=='Browser probe' else 4) for k,c in commands.items()}
            values={k:f.result() for k,f in futures.items()}
        browser=values.pop('Browser probe')
        try:details=json.loads(browser['detail']) if browser['ok'] else {}
        except ValueError:details={}
        for k in ['Chrome launch','Screenshot','Browser internet']:
            values[k]={'ok':bool(browser['ok'] and details.get(k)), 'detail':'cached lightweight browser receipt' if browser['ok'] else browser['detail']}
        result={'epoch':time.time(),'checks':values,'cached':False,'source':'unchanged DwarfStar OS sandbox; no model requests'};atomic(cache,result);return result

def check(workspace,profile='scope',require_model=True,browser_required=False,internet_required=False,overrides=None,probes=None):
    import runtime_control as runtime
    from profiles import registry
    checks=[];overrides=overrides or {};started=time.monotonic()
    def add(name,fn,required=True):
        try:
            value=overrides[name] if name in overrides else fn()
            ok=value.get('ok',False) if isinstance(value,dict) else bool(value)
            detail=value.get('detail','') if isinstance(value,dict) else ''
        except Exception as e:ok=False;detail=str(e)[:220]
        checks.append({'name':name,'status':'Ready' if ok else 'Failed' if required else 'Warning','required':required,'detail':detail})
    c=runtime.config();profiles=registry()['profiles']
    add('Profile',lambda:profile in profiles)
    add('Model',lambda:{'ok':bool(runtime.owned()) and runtime.health()['ok'],'detail':'Expected owned qwen3.8-flash-next endpoint 127.0.0.1:8030, 131072/81920; offline/mismatch blocks admission'},require_model)
    add('Model path',lambda:all(Path(p).is_file() for p in [c['argv'][c['argv'].index('-m')+1],*c['environment'].values()] if '/' in str(p) and str(p).endswith('.gguf')))
    add('Runtime',lambda:Path(c['argv'][0]).is_file() and c['runtime']=='Paperniuk DwarfStar')
    add('Context/output',lambda:c['context']==131072 and c['max_output']==81920)
    add('ACP',lambda:Path.home().joinpath('.bun/bin/omp').exists() and Path.home().joinpath('.bun/bin/bun').exists())
    def permission():
        v=read(ROOT/'acp/permission-runtime.json');return v['policy']=='dwarfstar-approval-v2' and hashlib.sha256(Path(v['cli']).read_bytes()).hexdigest()==v['cli_sha256']
    add('Permissions',permission)
    add('Provider',lambda:(ROOT/'acp/models.yml').is_file())
    add('Tools',lambda:all((ROOT/p).is_file() for p in ['agent-tools/extension.ts','agent-tools/shell.py','agent-tools/bin/browser.mjs']))
    add('Capability context',lambda:isinstance(read(ROOT/'agent-tools/state/capabilities.json'),dict))
    add('Workspace',lambda:writable(workspace))
    add('State store',lambda:writable(STATE) and writable(STATE/'tasks'))
    add('Benchmark storage',lambda:writable(ROOT/'benchmarks'))
    add('Telemetry',lambda:writable(ROOT/'state/clients') and writable(ROOT/'state/latency'),False)
    import sys
    sys.path.insert(0,str(ROOT/'agent-tools'))
    from boundary import environment
    path=environment(Path(workspace),Path(workspace))['PATH']
    for name,exe,required in [('Shell','zsh',True),('Git','git',True),('Node','node',True),('npm','npm',True),('npx','npx',True),('pnpm','pnpm',False),('Bun','bun',True)]:
        add(name,lambda exe=exe:bool(shutil.which(exe,path=path)),required)
    caps=read(ROOT/'agent-tools/state/capabilities.json',{})
    for title,k in [('Playwright','playwright'),('Puppeteer','puppeteer')]:
        add(title,lambda k=k:bool(caps.get(k)) and (ROOT/'agent-tools/node_modules'/k/'package.json').is_file(),browser_required)
    add('Chrome path',lambda:Path(caps.get('browser_executable','/nonexistent')).is_file(),browser_required)
    def bind():
        with socket.socket() as s:s.bind(('127.0.0.1',0))
        return True
    add('Localhost bind',bind)
    add('Port allocator',lambda:writable(STATE) and isinstance(read(STATE/'ports.json',{'reservations':[]}).get('reservations'),list))
    add('Process ownership',lambda:writable(STATE/'workspace-locks'))
    env=probes if probes is not None else environment_probes()
    for name in ['Shell internet','Node internet','Browser internet','Chrome launch','Screenshot']:
        add(name,lambda name=name:env['checks'].get(name,{'ok':False,'detail':'No recent receipt'}),internet_required if 'internet' in name else browser_required)
    for name in ['recovery_enabled','tool_result_hygiene_enabled','finish_nudge_enabled']:
        checks.append({'name':name,'status':'Not required' if profiles.get(profile,{}).get('features',{}).get(name) is False else 'Ready','required':False,'detail':'Existing profile policy; not changed by preflight'})
    result={'schema':1,'ok':not any(x['status']=='Failed' for x in checks),'profile_id':profile,'workspace':str(workspace),'checks':checks,
            'checked_epoch':time.time(),'probe_epoch':env.get('epoch'),'cached_probes':env.get('cached',False),'elapsed_s':time.monotonic()-started,'model_messages_added':0}
    try:atomic(STATE/'last-preflight.json',result)
    except OSError:pass
    return result
def require(workspace,profile,**kw):
    result=check(workspace,profile,**kw)
    if not result['ok']:raise RuntimeError('Preflight failed: '+'; '.join(x['name']+': '+x['detail'] for x in result['checks'] if x['status']=='Failed'))
    return result
def integrity():
    from task_infrastructure import Store
    errors=[];checks=[]
    targets={'Profiles':ROOT/'acp/profiles.json','Runtime config':ROOT/'runtime.json','Permission config':ROOT/'acp/permission-runtime.json','Benchmark registry':ROOT/'benchmarks/registry.json','Benchmark index':ROOT/'benchmarks/index.json','Process/port registry':STATE/'ports.json'}
    for name,path in targets.items():
        try:
            v=read(path)
            if v is None and name not in ['Benchmark index','Process/port registry']:raise ValueError('Missing file')
            if v is not None and not isinstance(v,dict):raise ValueError('Expected object')
            if name=='Profiles':
                if v['default']!='scope-vanilla' or v.get('active_profiles')!=['scope-vanilla'] or set(v['profiles'])!={'scope','vanilla','scope-vanilla','scope-vanilla-no-async'}:raise ValueError('Production profile selection differs')
                for p in v['profiles'].values():(ROOT/p['prompt']).read_text()
            if name=='Permission config' and (v.get('policy')!='dwarfstar-approval-v2' or hashlib.sha256(Path(v['cli']).read_bytes()).hexdigest()!=v['cli_sha256']):raise ValueError('Permission policy/CLI hash differs')
            if name=='Benchmark registry':
                sys.path.insert(0,str(ROOT/'benchmark-runner'))
                from records import registry as benchmark_registry
                benchmark_registry()
            if name=='Benchmark index' and v is not None and (not isinstance(v.get('runs'),list) or not all(isinstance(x,dict) and isinstance(x.get('run_id'),str) for x in v['runs'])):raise ValueError('Invalid benchmark index rows')
            if name=='Process/port registry' and v is not None and not isinstance(v.get('reservations'),list):raise ValueError('Invalid port registry')
            checks.append({'name':name,'status':'Ready'})
        except Exception as e:checks.append({'name':name,'status':'Failed','detail':str(e)});errors.append(str(path)+': '+str(e))
    try:
        import runtime_control as runtime
        c=runtime.config()
        for p in [c['argv'][0],c['argv'][c['argv'].index('-m')+1],'/Applications/Zed.app/Contents/MacOS/cli']:
            if not Path(p).exists():raise ValueError('Missing '+p)
        writable(ROOT/'state/latency');writable(STATE)
    except Exception as e:errors.append(str(e))
    rows,bad=Store().all();errors.extend(str(x) for x in bad)
    return {'ok':not errors,'checks':checks,'errors':errors,'task_count':len(rows),'automatic_model_resume':False}
