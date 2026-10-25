"""Local benchmark records, registry and reports. No provider or harness imports."""
from pathlib import Path
import datetime, hashlib, json, os, shutil, time, uuid

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT))
from approval_policy import mode as approval_mode, counters as permission_counters
STORE = ROOT / 'benchmarks'
WORKSPACES = Path.home() / 'Library/Application Support/DwarfStar Workstation/benchmarks/workspaces'
NE = 'Not exposed'
ORDER = ['vanilla', 'scope-vanilla', 'scope']
TERMINAL = {'COMPLETE', 'FAILED', 'CANCELLED'}

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(data): return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()
def read(path, default=None):
    try: return json.loads(Path(path).read_text())
    except (OSError, ValueError): return default
def save(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with tmp.open('x') as stream:
        os.chmod(tmp, 0o600); json.dump(data, stream, indent=2); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    tmp.replace(path)
def event(path, data):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    with os.fdopen(fd,'a') as stream: stream.write(json.dumps({'at': now(), **data}) + '\n')
def identifier(text):
    import re
    if not isinstance(text, str) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,100}', text): raise ValueError('Invalid identifier')
    return text
def local(base, name):
    p = (Path(base) / name).resolve()
    if not p.is_relative_to(Path(base).resolve()): raise ValueError('Path escapes its declared root')
    return p
def profiles(): return read(ROOT / 'acp/profiles.json')['profiles']
def workspace_root(store=STORE): return WORKSPACES if Path(store).resolve()==STORE.resolve() else Path(store)/'workspaces'
def profile_id(value):
    value = {'scope-coding': 'scope'}.get(value, value)
    if value not in profiles(): raise ValueError('Unknown profile')
    return value
def registry(store=STORE):
    raw = read(Path(store) / 'registry.json')
    if not raw or raw.get('schema') != 1: raise ValueError('Invalid registry')
    output = {}
    for row in raw['benchmarks']:
        row = dict(row); key = identifier(row['id'])
        if key in output: raise ValueError('Duplicate benchmark ID')
        if 'prompt' in row: prompt = row['prompt']
        else: prompt = local(store, row['prompt_file']).read_text().removesuffix('\n')
        if not isinstance(prompt, str) or not prompt.strip(): raise ValueError('Empty prompt')
        row.update(prompt=prompt, prompt_sha256=sha(prompt))
        if row.get('fixture'):
            row['fixture'] = str(local(store, row['fixture']))
            if not Path(row['fixture']).is_dir(): raise ValueError('Missing fixture directory')
        output[key] = row
    return output
def manifest(root):
    root = Path(root); rows = []
    # Inventory every file, including task-local state; never follow symlinks.
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(files + [d for d in dirs if (Path(base)/d).is_symlink()]):
            p = Path(base)/name; rel = str(p.relative_to(root))
            if p.is_symlink(): rows.append({'path': rel, 'symlink': os.readlink(p)}); continue
            if not p.is_file(): continue
            h = hashlib.sha256()
            with p.open('rb') as f:
                for part in iter(lambda: f.read(1024*1024), b''): h.update(part)
            rows.append({'path': rel, 'bytes': p.stat().st_size, 'sha256': h.hexdigest()})
    return sorted(rows, key=lambda r:r['path'])
def create_run(benchmark, kind, group, store=STORE):
    store = Path(store); kind = profile_id(kind)
    if store.resolve()==STORE.resolve() and any((p/'.git').exists() for p in workspace_root(store).resolve().parents):
        raise ValueError('Benchmark workspace root must not inherit a parent Git repository')
    ident = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:10]+'-'+kind
    folder = store / benchmark['id'] / ident; folder.mkdir(mode=0o700,parents=True, exist_ok=False)
    workspace = workspace_root(store)/(ident+'-'+benchmark['id']); workspace.mkdir(parents=True, exist_ok=False)
    if benchmark.get('fixture'):
        source = Path(benchmark['fixture'])
        for p in source.rglob('*'):
            if p.is_symlink(): raise ValueError('Fixtures must not contain symlinks')
        shutil.copytree(source, workspace, dirs_exist_ok=True)
    initial = manifest(workspace); save(folder/'initial-workspace.json', initial)
    (folder/'prompt.txt').write_text(benchmark['prompt'])
    row = {'schema':1,'run_id':ident,'group_id':group,'benchmark_id':benchmark['id'],
           'benchmark':benchmark,'prompt_sha256':benchmark['prompt_sha256'],'profile_id':kind,
           'profile_name':profiles()[kind]['name'],'policy':profiles()[kind]['policy'],
           'created_at':now(),'status':'QUEUED','phase':NE,'workspace':str(workspace),
           'artifact_directory':str(folder),'report_path':str(folder/'summary.md'),
           'initial_workspace_sha256':sha(json.dumps(initial,sort_keys=True)),
           'manual_intervention_required':False,'model_prompt_submissions':0,
           'approval_mode':approval_mode(),'permission_metrics':permission_counters([])}
    save(folder/'run.json',row); return row
def records(path):
    result=[]
    try:
        with Path(path).open() as stream:
            for line in stream:
                try: result.append(json.loads(line))
                except ValueError: pass
    except OSError: pass
    return result
def metrics(journal, kind, agent_end=None, submitted=None):
    summaries=[x['summary'] for x in journal if x.get('event')=='task_summary']
    s=summaries[-1] if summaries else {}
    requests=[r for r in journal if r.get('event')=='request_end']
    def total(field):
        return sum(r[field] for r in requests) if requests and all(isinstance(r.get(field),(int,float)) for r in requests) else NE
    tools=[r for r in journal if r.get('event')=='tool_start']
    first=next((r for r in journal if r.get('event')=='first_generated_item'),{})
    def elapsed(r): return max(0,r['timestamp']-submitted) if submitted is not None and isinstance(r.get('timestamp'),(int,float)) else NE
    result={'agent_start':s.get('task_start_timestamp',NE),'agent_end':s.get('task_end_timestamp',agent_end or NE),
      'total_wall_s':s.get('total_wall_s',NE),'time_to_final_s':s.get('final_completion_elapsed_s',NE),
      'time_to_first_model_token_s':elapsed(first),'time_to_first_tool_s':elapsed(tools[0]) if tools else NE,
      'time_to_functional_s':s.get('first_functional_elapsed_s',NE),
      'functional_to_final_tail_s':s.get('functional_to_final_tail_s',NE),
      'reasoning_s':s.get('total_reasoning_s',NE),'prefill_s':s.get('total_prefill_s',NE),
      'decode_s':s.get('total_decode_output_s',NE),'tool_s':s.get('total_tool_execution_s',NE),
      'harness_idle_s':s.get('total_harness_transport_idle_s',NE),'model_requests':s.get('model_request_count',NE),
      'tool_calls':s.get('tool_call_count',NE),'browser_tool_calls':s.get('browser_tool_call_count',NE),
      'shell_tool_calls':s.get('shell_tool_call_count',NE),'recoveries':s.get('recovery_count',NE),
      'infrastructure_holds':s.get('hold_count',NE),'stall_count':NE,
      'interrupted_requests':sum(r.get('classification') in ['MODEL_STREAM_INTERRUPTED','ACP_TRANSPORT_INTERRUPTED','TOOL_RESULT_INTERRUPTED'] for r in requests) if requests else NE,
      'finish_nudge':s.get('finish_nudge_sent',NE),'termination_state':s.get('terminal_state',NE),
      'tool_errors':sum(r.get('classification')=='TOOL_EXECUTION_ERROR' for r in journal if r.get('event')=='tool_end') if s else NE,
      'input_tokens':total('input_tokens'),'output_tokens':total('output_tokens'),'reasoning_tokens':NE,
      'cache':s.get('cache_prefix',NE),
      'hygiene':{'original_bytes':s.get('total_original_tool_result_bytes',NE),'model_visible_bytes':s.get('total_model_visible_tool_result_bytes',NE),'reduction_percent':s.get('result_reduction_percent',NE)} if kind=='scope' else 'Not applicable / disabled',
      'timing_notes':s.get('timing_notes',NE)}
    # An absent observation is never a measured zero or a diagnosed failed product.
    return {k:(NE if v is None else v) for k,v in result.items()}
def summary(row):
    m=row.get('metrics',{}); validation=row.get('validation',{})
    text=f"# {row['profile_name']} — {row['benchmark']['title']}\n\nStatus: **{row['status']}**\n\nRun: `{row['run_id']}`\n\n"
    text+=f"Workspace: `{row['workspace']}`\n\nPrompt SHA256: `{row['prompt_sha256']}`\n\n"
    text+=f"ACP session: `{row.get('acp_session_id',NE)}` · Policy: {row['policy']}\n\nManual intervention required: {row.get('manual_intervention_required',NE)}\n\n"
    if 'approval_mode' in row:
        text+='Approval mode: '+row['approval_mode']+'\n\n'+json.dumps(row.get('permission_metrics',{}),indent=2)+'\n\n'
    if row.get('postprocessing_resumed'):
        text+='**Resumed evidence collection only.** The original completed ACP turn and its metrics were preserved; no agent continuation was started.\n\n'
    elif row.get('resumed'):
        text+='**Resumed — interrupted execution.** Metrics below describe the final segment; prior segments and session IDs are preserved in attempts and run.json.\n\n'
        text+='```json\n'+json.dumps(row.get('resume_events',[]),indent=2)+'\n```\n\n'
    text+='| Metric | Value |\n|---|---|\n'+''.join(f'| {k} | {str(v).replace(chr(10)," ").replace("|","/")} |\n' for k,v in m.items() if not isinstance(v,(dict,list)))
    text+='\n## Product evidence\n\n'+json.dumps(validation,indent=2)+'\n\n'
    text+='Agent completion is frozen before evaluator activity. Post-run evidence is never returned to the agent. Completion records orchestration, not acceptance or subjective visual quality.\n'
    if row.get('error'): text+='\nError: '+str(row['error'])+'\n'
    Path(row['report_path']).write_text(text)
def index_update(row,store=STORE):
    # Caller holds the runner lock. Index loss/corruption cannot remove run records.
    path=Path(store)/'index.json'; old=read(path)
    if not isinstance(old,dict) or not isinstance(old.get('runs'),list):
        if path.exists():shutil.copy2(path,path.with_name('index.corrupt-'+uuid.uuid4().hex+'.json'))
        old={}
    entries=(old or {}).get('runs',[])
    if not isinstance(entries,list): entries=[]
    m=row.get('metrics',{})
    item={k:row.get(k) for k in ['run_id','created_at','benchmark_id','profile_id','profile_name','status','report_path','workspace']}
    item.update(wall_time_s=m.get('total_wall_s',NE),time_to_functional_s=m.get('time_to_functional_s',NE),time_to_final_s=m.get('time_to_final_s',NE),screenshot_path=row.get('validation',{}).get('screenshot_path'))
    entries=[e for e in entries if e.get('run_id')!=row['run_id']]+[item]
    save(path,{'schema':1,'runs':entries})
def delta(a,b,key):
    x=a.get('metrics',{}).get(key);y=b.get('metrics',{}).get(key)
    if not all(isinstance(z,(int,float)) and not isinstance(z,bool) for z in [x,y]):return NE
    return {'absolute':y-x,'percent':100*(y-x)/x if x else NE,'direction':'second minus first'}
def comparison(rows,folder):
    if len(rows)<2 or len({r['benchmark_id'] for r in rows})!=1 or len({r['prompt_sha256'] for r in rows})!=1:
        raise ValueError('Select at least two runs of one identical benchmark prompt')
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    by={r['profile_id']:r for r in rows};derived={}
    for label,a,b,keys in [('Scope prompt effect','vanilla','scope-vanilla',['time_to_functional_s','time_to_final_s','tool_calls','recoveries']),('Production harness effect','scope-vanilla','scope',['time_to_final_s','functional_to_final_tail_s','recoveries']),('Full-stack delta','vanilla','scope',['time_to_functional_s','time_to_final_s','tool_calls'])]:
        if a in by and b in by:
            derived[label]={'first':a,'second':b,'deltas':{k:delta(by[a],by[b],k) for k in keys},'reliability':{p:{k:by[p].get('metrics',{}).get(k,NE) for k in ['termination_state','recoveries','infrastructure_holds','finish_nudge','hygiene']} for p in [a,b]}}
    result={'schema':1,'benchmark_id':rows[0]['benchmark_id'],'created_at':now(),'runs':[r['run_id'] for r in rows],
            'derived':derived,'limitations':['Single ordered comparisons are evidence, not causal proof. Shared cache, warm/cold state and order are recorded, not equalized.','Not exposed is missing, not zero. Reasoning overlaps decode; residual is not native kernel/transport time.','Agent completion and external evaluation are separate. No subjective visual grading.']}
    tooling=[read(Path(r['artifact_directory'])/'config-snapshot.json',{}).get('agent_tools') for r in rows]
    result['fairness']={'identical_initial_workspace':len({r.get('initial_workspace_sha256') for r in rows})==1,
                        'concurrent_user_activity_observed':any(r.get('concurrent_user_activity_observed') for r in rows),
                        'shared_tool_package_snapshots_equal':len({json.dumps(t,sort_keys=True) for t in tooling})==1,
                        'actual_config_verification':[r.get('actual_config_verified',NE) for r in rows]}
    result['fairness']['resumed_runs']=[{'run_id':r['run_id'],'resume_events':r.get('resume_events',[]),'attempts':r.get('resume_attempts',[])} for r in rows if r.get('resumed')]
    if result['fairness']['resumed_runs']:result['limitations'].append('Resumed: interrupted runs include explicit continuation/session-load. Tabulated metrics are final-segment metrics, with prior segments preserved; these are not uninterrupted comparisons.')
    if result['fairness']['concurrent_user_activity_observed']:result['limitations'].append('Another live user client was observed; this comparison is contaminated by concurrent activity.')
    if not result['fairness']['identical_initial_workspace']:result['limitations'].append('Initial fixture hashes differ; this is not an identical-input comparison.')
    fields=[('Status','status'),('Time to first tool action','time_to_first_tool_s'),('Time to first token','time_to_first_model_token_s'),('Time to functional','time_to_functional_s'),('Time to final','time_to_final_s'),('Functional → final tail','functional_to_final_tail_s'),('Reasoning time','reasoning_s'),('Prefill time','prefill_s'),('Decode time (overlaps reasoning)','decode_s'),('Tool time','tool_s'),('Harness/idle time','harness_idle_s'),('Model requests','model_requests'),('Tool calls','tool_calls'),('Recoveries','recoveries'),('Finish nudge','finish_nudge'),('Console errors','console_errors'),('Primary validation','primary_validation')]
    text='# DwarfStar comparison — '+rows[0]['benchmark']['title']+'\n\n| Metric | '+' | '.join(r['profile_name'] for r in rows)+' |\n|---|'+'---|'*len(rows)+'\n'
    for label,key in fields:
        values=[r.get('metrics',{}).get(key,r.get('validation',{}).get(key,r.get(key,NE))) for r in rows]
        text+='| '+label+' | '+' | '.join(str(v).replace('|','/').replace('\n',' ') for v in values)+' |\n'
    text+='\n## Configuration and fairness\n\n```json\n'+json.dumps(result['fairness'],indent=2)+'\n```\n\n'
    for r in rows: text+=f"- {r['run_id']}: {r.get('server_temperature',NE)}; initial workspace `{r.get('initial_workspace_sha256',NE)}`; [snapshot]({Path(r['artifact_directory'])/'config-snapshot.json'}); [run report]({r['report_path']})\n"
    for label,value in derived.items():text+='\n## '+label+'\n\n```json\n'+json.dumps(value,indent=2)+'\n```\n'
    text+='\n## Screenshots\n\n'
    for r in rows:
        screenshot=r.get('validation',{}).get('screenshot_path')
        if screenshot:text+=f"- [{r['profile_name']}]({screenshot})\n"
    text+='\n'+'\n'.join(result['limitations'])+'\n'
    save(folder/'comparison.json',result);(folder/'comparison.md').write_text(text);return str(folder/'comparison.md')
