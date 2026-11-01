#!/usr/bin/env python3
"""Explicit, sequential ACP benchmark client. Never supplies continuation prompts."""
from pathlib import Path
import argparse, contextlib, fcntl, hashlib, http.server, json, os, queue, shutil
import signal, subprocess, sys, threading, time, uuid
from records import ROOT, STORE, ORDER, NE, TERMINAL, registry, profiles, profile_id, create_run
from records import now, read, save, event, sha, manifest, records, metrics, summary, index_update, comparison, local, workspace_root

PYTHON = str(ROOT / '.venv/bin/python')
sys.path.insert(0,str(ROOT))
import runtime_control as runtime
import task_infrastructure as infra
from approval_policy import mode as approval_mode, resolve_request, counters as permission_counters
_save=save;_event=event
def save(path,value):
    try:_save(path,value)
    except OSError as e:print('DwarfStar persistence warning; workspace/live turn preserved: '+str(e),file=sys.stderr)
def event(path,value):
    try:_event(path,value)
    except OSError as e:print('DwarfStar journal warning; live turn preserved: '+str(e),file=sys.stderr)

def identity(pid): return runtime.identity(pid)
def alive(row):
    current=identity(row.get('pid')) if row else None
    return bool(current and all(current[k]==row.get(k) for k in ['pid','birth','argv_sha256']))
@contextlib.contextmanager
def lock(path):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    with Path(path).open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield f
def persist(row,store=STORE):
    try:save(Path(row['artifact_directory'])/'run.json',row)
    except OSError as e:
        print('Benchmark persistence warning (live workspace preserved): '+str(e),file=sys.stderr)
    try:
        if row.get('benchmark_id')!='interactive-task':index_update(row,store)
    except Exception as e:
        # Secondary-index failure must not destroy the authoritative record.
        event(Path(row['artifact_directory'])/'runner-events.jsonl',{'event':'index_error','error':str(e)})
    infra.safe_sync(row, infra.STATE if Path(store)==STORE or row.get('benchmark_id')=='interactive-task' else Path(store)/'orchestration')
def config_snapshot(kind):
    env=dict(os.environ,DWARFSTAR_PROFILE_ID=kind)
    result=subprocess.run([str(Path.home()/'.bun/bin/bun'),'--no-env-file',str(ROOT/'benchmark-runner/snapshot.ts')],env=env,text=True,capture_output=True,timeout=30)
    if result.returncode:raise RuntimeError('Read-only profile snapshot failed: '+result.stderr[-1500:])
    value=json.loads(result.stdout)
    if value['profile_id']!=kind or value['profile']!=profiles()[kind]['name']:raise RuntimeError('Selected profile does not match snapshot')
    return value
def verify_snapshot(expected,actual):
    checks=['profile','policy','model_id','quant','inference','feature_flags','provider_config_sha256','system_prompt','permission_runtime','harness_sha256']
    unavailable=[];mismatched=[]
    def check(a,b,path):
        if b==NE or b is None:unavailable.append(path);return
        if isinstance(a,dict) and isinstance(b,dict):
            for k in a:check(a[k],b.get(k),path+'.'+k)
        elif a!=b:mismatched.append(path)
    for key in checks:check(expected.get(key),actual.get(key),key)
    if mismatched:raise RuntimeError('Actual ACP config mismatch: '+', '.join(mismatched))
    return {'unobservable_fields':unavailable,'note':'Unavailable sandbox reads remain unknown; host snapshot/source hashes are independently checked before and after.'}
def runtime_idle():
    row=runtime.owned()
    if not row:return
    if any(r.get('active') or r.get('tool_execution_active') for r in runtime.requests(row)):
        raise RuntimeError('A user task is active; benchmark did not touch it. Start explicitly after it finishes.')
    r=runtime.run(['/usr/sbin/lsof','-nP','-a','-p',str(row['pid']),'-iTCP:8031','-sTCP:ESTABLISHED'])
    if r.returncode not in [0,1] or r.returncode==0 and r.stdout.strip():raise RuntimeError('Model has a connected client; benchmark admission refused without interrupting it.')
def ensure_runtime():
    runtime_idle();warm=bool(runtime.owned())
    if not warm:
        result=subprocess.run([PYTHON,'-B',str(ROOT/'runtime_control.py'),'start'],capture_output=True,text=True,timeout=630)
        receipt=json.loads(result.stdout)
        if not receipt.get('ok'):raise RuntimeError(receipt.get('error','Runtime start failed'))
    row=runtime.owned()
    if not row or not runtime.health()['ok']:raise RuntimeError('Owned model health/config mismatch; no restart attempted')
    return {'identity':row,'temperature':'warm server' if warm else 'cold server (started by explicit benchmark)', 'cache_policy':'Shared model cache retained, never cleared'}
def descendents(pid):
    table=[]
    text=subprocess.run(['/bin/ps','-axo','pid=,ppid='],capture_output=True,text=True).stdout
    for line in text.splitlines():
        try:a,b=map(int,line.split());table.append((a,b))
        except ValueError:pass
    found={pid};changed=True
    while changed:
        changed=False
        for a,b in table:
            if b in found and a not in found:found.add(a);changed=True
    return [v for p in found if p!=pid and (v:=identity(p))]

class ACP:
    def __init__(self,row,cancel):
        self.row=row;self.folder=Path(row['artifact_directory']);self.cancel=cancel;self.store=Path(row.get('record_store',self.folder.parents[1]))
        self.stderr=(self.folder/'acp-stderr.log').open('w');self.sequence=0;self.messages=queue.Queue();self.session=None;self.cancel_sent=False;self.cancel_deadline=None;self.children={}
        # This exact existing launcher owns environment, tools, permissions and prompt.
        self.process=subprocess.Popen([PYTHON,'-B',str(ROOT/'scope_agent.py'),'--kind',row['profile_id'],'--workspace',row['workspace']],cwd=row['workspace'],env=dict(os.environ,DWARFSTAR_TASK_ID=row['run_id'],DWARFSTAR_APPROVAL_MODE=row.get('approval_mode',approval_mode())),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.stderr,text=True,bufsize=1,start_new_session=True)
        row['acp_process']=identity(self.process.pid);row['acp_pid']=self.process.pid
        self.journal=ROOT/'state/latency'/('client-'+str(self.process.pid)+'.jsonl')
        self.request_file=ROOT/'state/clients'/('request-'+str(self.process.pid)+'.json')
        row['telemetry_references']={'journal':str(self.journal),'scalar_request':str(self.request_file)}
        def reader():
            for line in self.process.stdout:
                try:self.messages.put(json.loads(line))
                except ValueError:event(self.folder/'runner-events.jsonl',{'event':'non_protocol_stdout','sample':line[:500]})
            self.messages.put({'__eof':True})
        threading.Thread(target=reader,daemon=True).start();self.last_scan=0
    def send(self,value):
        self.process.stdin.write(json.dumps(value)+'\n');self.process.stdin.flush()
    def tick(self):
        if self.cancel() and not self.cancel_sent:
            self.cancel_sent=True;self.cancel_deadline=time.monotonic()+15
            if self.session:self.send({'jsonrpc':'2.0','method':'session/cancel','params':{'sessionId':self.session}})
            event(self.folder/'runner-events.jsonl',{'event':'user_cancel_requested','automatic_restart':False})
        if self.cancel_deadline and time.monotonic()>self.cancel_deadline:raise InterruptedError('Cancellation grace ended; preserve partial evidence')
        if time.monotonic()-self.last_scan>2:
            self.last_scan=time.monotonic()
            for r in descendents(self.process.pid):self.children[r['pid']]=r
            scalar=read(self.request_file,{})
            timing=scalar.get('session_timing',{})
            # Exact existing harness labels only. No invented internal phase.
            self.row['phase']=timing.get('phase',NE)
            self.row['status']='WAITING_TOOL' if scalar.get('tool_execution_active') or (self.folder/'pending-permission.json').exists() else 'RUNNING'
            server=runtime.owned()
            if server:
                others=[r.get('client_pid') for r in runtime.requests(server) if isinstance(r.get('client_pid'),int) and r.get('client_pid')!=self.process.pid and (r.get('active') or r.get('tool_execution_active'))]
                if others:self.row['concurrent_user_activity_observed']=others
            if scalar.get('active_tool_name'):self.row['active_tool']=scalar['active_tool_name']
            state=(self.row['status'],self.row.get('phase'),self.row.get('active_tool'))
            if state!=getattr(self,'last_persisted_state',None):
                self.last_persisted_state=state
                persist(self.row,self.store)
    def confined(self,name):
        workspace=Path(self.row['workspace']).resolve();p=Path(name)
        p=(workspace/p if not p.is_absolute() else p).resolve()
        if self.row.get('approval_mode',approval_mode())!='full_auto' and not p.is_relative_to(workspace):raise ValueError('ACP filesystem request outside benchmark workspace')
        # Native harness already guards credentials. Client performs containment;
        # no new model-facing tool or permission is installed here.
        return p
    def callback(self,message):
        method=message['method'];params=message.get('params',{})
        try:
            if method=='session/request_permission':
                auto=resolve_request(message,self.row.get('approval_mode',approval_mode()),self.folder/'permission-events.jsonl')
                if auto is not None:
                    self.send(auto);return
                wait_start=time.time()
                self.row['manual_intervention_required']=True;self.row['status']='WAITING_TOOL'
                pending={'request_id':message['id'],'received_at':now(),'params':params,'note':'Sensitive native ACP request. Explicit Allow once or Reject required; no persistent grant.'}
                save(self.folder/'pending-permission.json',pending);save(self.folder/'run.json',self.row)
                options=params.get('options',[]);decision=self.folder/'permission-decision.json'
                while not self.cancel():
                    value=read(decision)
                    if value and value.get('request_id')==message['id']:
                        selected=next((x for x in options if x['optionId']==value.get('option_id') and x['kind'] in ['allow_once','reject_once']),None)
                        if selected:
                            event(self.folder/'permissions.jsonl',{'request':pending,'decision':value});decision.unlink();result={'outcome':{'outcome':'selected','optionId':selected['optionId']}};break
                    self.tick();time.sleep(.2)
                else:result={'outcome':{'outcome':'cancelled'}}
                (self.folder/'pending-permission.json').unlink(missing_ok=True)
                from approval_policy import record
                choice=next((x for x in options if x['optionId']==result['outcome'].get('optionId')),None)
                record({'event':'acp_permission_decision','automatic':False,'decision':'approved' if choice and choice['kind'].startswith('allow_') else 'denied','acp_approval_wait_s':time.time()-wait_start},self.folder/'permission-events.jsonl')
            elif method=='fs/read_text_file':
                p=self.confined(params['path']);content=p.read_text();lines=content.splitlines(keepends=True)
                start=max(0,int(params.get('line',1))-1);limit=params.get('limit');result={'content':''.join(lines[start:None if limit is None else start+int(limit)])}
            elif method=='fs/write_text_file':
                p=self.confined(params['path']);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(params['content']);result={}
            else:raise ValueError('Unsupported ACP client request '+method)
            self.send({'jsonrpc':'2.0','id':message['id'],'result':result})
        except (ValueError,OSError) as error:self.send({'jsonrpc':'2.0','id':message['id'],'error':{'code':-32603,'message':str(error)}})
    def request(self,method,params):
        self.sequence+=1;ident=self.sequence
        self.send({'jsonrpc':'2.0','id':ident,'method':method,'params':params})
        while True:
            self.tick()
            try:message=self.messages.get(timeout=.3)
            except queue.Empty:continue
            if message.get('__eof'):raise RuntimeError('ACP exited before terminal response')
            event(self.folder/'acp-events.jsonl',{'message':message})
            if 'method' in message and 'id' in message:self.callback(message)
            if message.get('id')==ident and 'method' not in message:
                if 'error' in message:raise RuntimeError(str(message['error']))
                return message.get('result',{})
    def initialize(self):
        init=self.request('initialize',{'protocolVersion':1,'clientCapabilities':{'fs':{'readTextFile':True,'writeTextFile':True},'terminal':False},'clientInfo':{'name':'DwarfStar benchmark','version':'1'}})
        save(self.folder/'acp-initialize.json',init)
        previous=self.row.get('resume_previous_session')
        self.row.pop('exact_load_failure',None)
        loaded=False
        if previous and init.get('agentCapabilities',{}).get('loadSession'):
            try:
                self.request('session/load',{'sessionId':previous,'cwd':self.row['workspace'],'mcpServers':[]})
                self.session=previous;session={'sessionId':previous};loaded=True
            except Exception as e:
                self.row['exact_load_failure']=str(e)
        if not loaded:
            session=self.request('session/new',{'cwd':self.row['workspace'],'mcpServers':[]});self.session=session['sessionId']
        if self.row.get('resumed'):
            self.row['resume_mode']='saved-session-load' if loaded else 'new-continuation-session'
            self.row.setdefault('resume_events',[]).append({'at':now(),'previous_acp_session_id':previous,'new_acp_session_id':self.session,'mode':self.row['resume_mode'],'workspace_preserved':True,'explicit_user_action':True,'reason':'Explicit user Resume after inactive/disconnected task','exact_load_failure':self.row.get('exact_load_failure')})
        self.row['acp_session_id']=self.session;self.row['acp_process']=identity(self.process.pid);save(self.folder/'acp-session.json',session)
        infra.safe_sync(self.row,infra.STATE if self.store==STORE else self.store/'orchestration')
        # Navigation metadata is supplemental and cannot enter ACP/model context.
        try:
            from zed_navigation import initialized
            initialized(dict(self.row))
        except Exception:pass
    def prompt(self,text):
        if self.row.get('resumed'):
            if self.row.get('continuation_submitted'):raise RuntimeError('Continuation already submitted')
            self.row['continuation_submitted']=True
            text=CONTINUATION if self.row.get('resume_mode')=='saved-session-load' else CONTINUATION+'\n\nOriginal request:\n'+text
        elif self.row['model_prompt_submissions']!=0:raise RuntimeError('One original task submission only')
        if self.cancel():raise InterruptedError('Cancelled before submission')
        self.row.setdefault('original_submitted_epoch',self.row.get('submitted_epoch',time.time()))
        self.row['submitted_at']=now();self.row['submitted_epoch']=time.time();self.row['model_prompt_submissions']=1
        save(self.folder/'run.json',self.row)
        return self.request('session/prompt',{'sessionId':self.session,'prompt':[{'type':'text','text':text}]})
    def close(self):
        # Only this benchmark ACP tree is owned. Never signal the loaded server.
        for r in descendents(self.process.pid):self.children[r['pid']]=r
        try:self.process.stdin.close()
        except (OSError,ValueError):pass
        try:self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if self.process.poll() is None:self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        signalled=[]
        for r in self.children.values():
            if alive(r):
                try:os.kill(r['pid'],signal.SIGTERM);signalled.append(r)
                except ProcessLookupError:pass
        until=time.monotonic()+3
        while time.monotonic()<until and any(alive(r) for r in signalled):time.sleep(.1)
        remaining=[r for r in signalled if alive(r)]
        # Owned benchmark descendants only; exact birth/argv identity checked again.
        for r in remaining:
            if alive(r):
                try:os.kill(r['pid'],signal.SIGKILL)
                except ProcessLookupError:pass
        save(self.folder/'process-cleanup.json',{'observed_descendants':list(self.children.values()),'signalled':signalled,'remaining_at_sigkill':remaining,'note':'Detached children are tracked while descendants; unknown external/orphan processes are never signalled.'})
        self.stderr.close()
        permission_journal=ROOT/'state/permissions'/('client-'+str(self.process.pid)+'.jsonl')
        if permission_journal.exists():shutil.copy2(permission_journal,self.folder/'native-permission-events.jsonl')
        if self.journal.exists():shutil.copy2(self.journal,self.folder/'telemetry.jsonl')
        if self.request_file.exists():shutil.copy2(self.request_file,self.folder/'last-request.json')

def find_entrypoint(row):
    workspace=Path(row['workspace']);b=row['benchmark']
    expected=b.get('expected_entrypoint')
    if expected and local(workspace,expected).is_file():return local(workspace,expected)
    candidates=[p for p in workspace.rglob('*.html') if not p.is_symlink() and not any(x.startswith('.') or x=='node_modules' for x in p.relative_to(workspace).parts)]
    candidates.sort(key=lambda p:(p.name!='index.html',len(p.parts),str(p)))
    return candidates[0] if candidates else None
def evaluate(row):
    folder=Path(row['artifact_directory']);workspace=Path(row['workspace']);entry=find_entrypoint(row)
    if not entry:return {'entrypoint':NE,'primary_validation':'NO DISCOVERED HTML ENTRYPOINT','note':'Missing or non-static project is unassessed, not a diagnosed engineering defect.'}
    browser=row['benchmark'].get('browser',{})
    if not browser.get('enabled'):return {'entrypoint':str(entry),'primary_validation':NE}
    before=manifest(workspace);save(folder/'evaluation-workspace-before.json',before)
    output=folder/'evaluation';output.mkdir(exist_ok=True);temp=output/'tmp';temp.mkdir(exist_ok=True)
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self,*a,**kw):super().__init__(*a,directory=str(workspace),**kw)
        def log_message(self,*a):pass
        def send_head(self):
            # Do not serve symlink escapes or ACP/history dotfiles.
            p=Path(self.translate_path(self.path)).resolve()
            if not p.is_relative_to(workspace.resolve()) or any(x.startswith('.') for x in p.relative_to(workspace.resolve()).parts):self.send_error(403);return None
            return super().send_head()
    task=infra.Store();registered=bool(task.get(row['run_id']))
    reservation=task.reserve(row['run_id'],'post-completion read-only evaluator',process=infra.fingerprint(os.getpid())) if registered else None
    try:server=http.server.ThreadingHTTPServer(('127.0.0.1',reservation['port'] if reservation else 0),Handler)
    except OSError:
        # Unrelated bind race: never kill a listener, use another kernel-assigned port.
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    from urllib.parse import quote
    target=browser.get('target',str(entry.relative_to(workspace)))
    target_path=local(workspace,target)
    url='http://127.0.0.1:'+str(server.server_port)+'/'+quote(str(target_path.relative_to(workspace)))
    plan={**browser,'entrypoint':str(entry),'url':url,'output':str(output)};save(output/'plan.json',plan)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    env=dict(os.environ,TMPDIR=str(temp),DWARFSTAR_TASK_ID=row['run_id'],DWARFSTAR_WORKSPACE=str(workspace),DWARFSTAR_STATE_ROOT=str(infra.STATE));child=None
    try:
        with (output/'evaluator.log').open('w') as log:
            child=subprocess.Popen(['/opt/homebrew/bin/node',str(ROOT/'benchmark-runner/evaluate.mjs'),str(output/'plan.json')],cwd=folder,env=env,stdout=log,stderr=log,start_new_session=True)
            if registered:
                try:task.register(row['run_id'],child.pid,'evaluator',root=True)
                except Exception:pass
            try:
                deadline=time.monotonic()+100
                while child.poll() is None:
                    if registered:
                        try:task.discover(row['run_id'])
                        except Exception:pass
                    if time.monotonic()>deadline:raise subprocess.TimeoutExpired('evaluator',100)
                    time.sleep(.2)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGTERM)
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=5)
        value=read(output/'validation.json',{'primary_validation':'EVALUATOR FAILED','exit_code':child.returncode})
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
        if registered:
            try:task.cleanup(row['run_id'])
            except Exception:pass
    after=manifest(workspace);save(folder/'evaluation-workspace-after.json',after)
    value['workspace_unchanged']=before==after;value['model_turns_added']=0
    if before!=after:value['integrity_error']='Workspace changed during external evaluation; preserve and inspect'
    return value
def execute_run(row,cancel,store=STORE,factory=ACP,prepare=ensure_runtime,snapshot=config_snapshot,evaluator=evaluate):
    folder=Path(row['artifact_directory']);client=None;end=None
    row.update(status='STARTING',orchestration_started_at=now(),orchestration_owner=infra.fingerprint(os.getpid()),record_store=str(store));persist(row,store)
    try:
        if cancel():raise InterruptedError('Cancelled before startup')
        info=prepare();row['server_temperature']=info['temperature'];row['runtime_identity']=info['identity'];row['cache_policy']=info.get('cache_policy',NE)
        if factory is ACP:
            from preflight import require
            row['preflight']=require(row['workspace'],row['profile_id'],browser_required=bool(row['benchmark'].get('browser',{}).get('enabled')))
        expected=snapshot(row['profile_id']);save(folder/'config-snapshot.json',expected)
        row.update(model_identifier=expected['model_id'],system_prompt_source_sha256=expected['system_prompt']['sha256'],feature_flags=expected['feature_flags'])
        client=factory(row,cancel);persist(row,store);client.initialize()
        # No hidden config drift between snapshot and the single task submission.
        verify_snapshot(expected,snapshot(row['profile_id']))
        result=client.prompt(row['benchmark']['prompt']);end=now()
        row['agent_terminal_at']=end;row['acp_terminal_response']=result
        row['status']='CANCELLED' if cancel() or result.get('stopReason')=='cancelled' else 'COMPLETE' if result.get('stopReason')=='end_turn' else 'FAILED'
    except InterruptedError as e:row.update(status='CANCELLED',error=str(e),agent_terminal_at=now())
    except Exception as e:row.update(status='CANCELLED' if cancel() else 'FAILED',error=str(e),agent_terminal_at=now())
    finally:
        if client:
            try:client.close()
            except Exception as e:row.update(cleanup_error=str(e));row['status']='CANCELLED' if cancel() else 'FAILED'
        if Path(store)==STORE:
            try:infra.Store().cleanup(row['run_id'])
            except Exception as e:row['registry_cleanup_warning']=str(e)
        journal=records(folder/'telemetry.jsonl');snaps=[r for r in journal if r.get('event')=='benchmark_snapshot']
        row['metrics']=metrics(journal,row['profile_id'],end,row.get('submitted_epoch'))
        if 'approval_mode' in row:
            row['permission_metrics']=permission_counters([folder/'permission-events.jsonl',folder/'native-permission-events.jsonl'])
            row['metrics'].update(row['permission_metrics'])
        if snaps:
            actual=snaps[-1];save(folder/'actual-config-snapshot.json',actual)
            row['effective_system_prompt_sha256']=actual.get('effective_system_prompt_sha256',NE)
            row['tool_schema_sha256']=actual.get('tool_schema_sha256',NE);row['capability_context_sha256']=actual.get('capability_context_sha256',NE)
            try:
                row['actual_snapshot_verification']=verify_snapshot(read(folder/'config-snapshot.json'),actual['config'])
                verify_snapshot(read(folder/'config-snapshot.json'),snapshot(row['profile_id']))
                row['actual_config_verified']=True
            except Exception as e:row.update(status='FAILED',config_error=str(e),actual_config_verified=False)
        else:row['actual_config_verified']=NE
        if row['status']=='COMPLETE' and row['metrics']['termination_state']!='completed_turn':
            row.update(status='FAILED',error='ACP returned but passive task summary is missing or not completed_turn')
        row['phase']='IDLE';row['agent_collection_finished_at']=now();persist(row,store)
        # ACP is CLOSED before evaluation: errors cannot reach another agent turn.
        if row['status']=='COMPLETE':
            try:row['validation']=evaluator(row)
            except Exception as e:row['validation']={'primary_validation':'EVALUATION UNAVAILABLE','error':str(e)}
        row['evaluator_finished_at']=now();row['finished_at']=now()
        save(folder/'project-manifest.json',manifest(row['workspace']))
        save(folder/'timing-summary.json',row['metrics']);summary(row);persist(row,store)
    return row

def group_path(group,store=STORE):
    from records import identifier
    return Path(store)/'groups'/identifier(group)
def find_run(run_id,store=STORE):
    from records import identifier
    identifier(run_id)
    paths=list(Path(store).glob('*/'+run_id+'/run.json'))
    if len(paths)!=1:raise ValueError('Unknown or ambiguous run ID')
    return read(paths[0])

CONTINUATION='The previous agent session ended unexpectedly. Continue the existing task from the current workspace state. Inspect existing files and progress first, preserve completed work, and finish the original request without rebuilding from scratch.'

def resume_claim(row,store=STORE):
    """Validate explicit resume under group control lock; no model work here."""
    if row['status']=='COMPLETE' and not row.get('resume_evaluation_only'):raise ValueError('Completed task cannot resume')
    if alive(row.get('acp_process')):raise ValueError('A live ACP already owns the task')
    task=infra.Store(infra.STATE if Path(store)==STORE else Path(store)/'orchestration')
    saved=task.get(row['run_id'])
    if saved and task.classify(saved)=='active/recoverable':raise ValueError('Task still has a live ACP launcher')
    if saved and alive(saved.get('resume_worker')):raise ValueError('A resume worker is already starting')
    workspace=Path(row['workspace'])
    if not workspace.is_dir():raise ValueError('Original workspace is missing; resume will not recreate it')
    # Never consume stale flag/metadata until ownership admission succeeds.
    fd=(task.root/'workspace-locks'/infra.digest(str(workspace.resolve())))
    fd.parent.mkdir(parents=True,exist_ok=True)
    with fd.open('a') as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Workspace still owned by an ACP; resume refused')
    return True

def prepare_resume(row):
    folder=Path(row['artifact_directory']);number=len(row.get('resume_attempts',[]))+1
    attempt=folder/'attempts'/('before-resume-'+str(number));attempt.mkdir(parents=True,exist_ok=False)
    save(attempt/'run.json',row)
    # Move only replaceable segment receipts; no workspace/history/model files touched.
    for name in ['acp-events.jsonl','acp-stderr.log','acp-initialize.json','acp-session.json','telemetry.jsonl','last-request.json','process-cleanup.json','timing-summary.json','summary.md','actual-config-snapshot.json','config-snapshot.json','evaluation','evaluation-workspace-before.json','evaluation-workspace-after.json','project-manifest.json','pending-permission.json','permission-decision.json']:
        p=folder/name
        if p.exists():p.rename(attempt/name)
    row.setdefault('resume_attempts',[]).append({'at':now(),'prior_record':str(attempt/'run.json'),'prior_status':row['status'],'prior_session':row.get('acp_session_id'),'prior_metrics':row.get('metrics',{}),'reason':'Explicit user Resume','workspace_preserved':True})
    row.update(resumed=True,resume_previous_session=row.get('acp_session_id'),continuation_submitted=False,status='QUEUED',interrupted=True)
    row.pop('finished_at',None);row.pop('error',None)
    return row

def resume_task(key,store=STORE):
    """Only this explicit CLI/UI action authorizes a continuation."""
    with lock(Path(store)/'control.lock'):
        try:row=find_run(key,store)
        except ValueError:
            task=infra.Store().get(key)
            if not task:raise ValueError('Unknown task')
            if task.get('run_id'):raise ValueError('Task run metadata is missing; preserve and inspect')
            row=read(task.get('resume_execution_record','')) if task.get('resume_execution_record') else None
            row=row or interactive_resume_row(task)
        if row['status']=='COMPLETE' and row.get('agent_collection_finished_at') and not row.get('finished_at'):row['resume_evaluation_only']=True
        resume_claim(row,store)
        group=None
        if row.get('group_id'):
            folder=group_path(row['group_id'],store);group=read(folder/'group.json')
            if alive(group.get('worker')):raise ValueError('Benchmark worker is still live')
            # No other row may have a live owner, even if its stale status says failed.
            for key2 in group['run_ids']:
                if alive(find_run(key2,store).get('acp_process')):raise ValueError('Another group ACP is still live')
            # A queued row can resume the sequence only if no earlier unfinished row exists.
            position=group['run_ids'].index(key)
            for before in group['run_ids'][:position]:
                if find_run(before,store)['status'] not in TERMINAL:raise ValueError('Resume the earlier unfinished comparison row first')
        if row.get('resume_evaluation_only'):
            row.update(status='QUEUED',resumed=True,postprocessing_resumed=True)
            row.setdefault('resume_events',[]).append({'at':now(),'mode':'evaluation-only; no agent started','previous_acp_session_id':row.get('acp_session_id'),'new_acp_session_id':None,'workspace_preserved':True,'explicit_user_action':True})
        elif not row.get('model_prompt_submissions') and not row.get('acp_session_id'):
            row.update(status='QUEUED',sequence_resumed=True,sequence_resume_requested_at=now())
        else:row=prepare_resume(row)
        persist(row,store)
        _save(Path(row['artifact_directory'])/'run.json',row) # Required durable admission, before any worker/model work.
        if group:
            (folder/'cancel.flag').unlink(missing_ok=True)
            group.update(status='QUEUED',resumed=True,resume_target=key,resume_requested_at=now())
            group.pop('worker',None)
            _save(folder/'group.json',group)
            args=[PYTHON,'-B',str(Path(__file__)),'_worker',group['group_id']]
        else:
            folder=Path(row['artifact_directory']);args=[PYTHON,'-B',str(Path(__file__)),'_resume-task',row['run_id']]
        with (folder/'resume-worker.log').open('a') as log:
            child=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        receipt=identity(child.pid)
        if group:group['worker']=receipt;save(folder/'group.json',group)
        else:infra.Store().update(key,resume_worker=receipt,resume_execution_record=str(folder/'run.json'),status='QUEUED')
        return {'ok':True,'task_id':row['run_id'],'workspace':row['workspace'],'profile_id':row['profile_id'],'resumed':True,'worker':receipt,'mode':'Saved-session load if supported; explicit continuation fallback otherwise'}

def interactive_resume_row(task):
    workspace=Path(task['workspace']);session=task.get('acp_session_id');prompt=None;reference=None
    # Session banks remain untouched. Read the initial user task, never raw history into UI.
    candidates=list((workspace/('.omp-dwarfstar-'+task['profile_id']+'-agent')/'sessions').glob('**/*'+str(session)+'.jsonl')) if session else []
    for p in candidates:
        for e in records(p):
            m=e.get('message',{})
            if e.get('type')=='message' and m.get('role')=='user' and m.get('attribution','user')=='user':
                content=m.get('content');prompt=content if isinstance(content,str) else '\n'.join(x.get('text','') for x in content or [] if x.get('type')=='text');reference=str(p)
        if prompt:break
    if not prompt:raise ValueError('Original task/session reference is unavailable; refusing a fabricated continuation')
    folder=infra.STATE/'executions'/task['task_id'];folder.mkdir(parents=True,exist_ok=True)
    existing=read(folder/'run.json')
    if existing:return existing
    (folder/'prompt.txt').write_text(prompt)
    return {'schema':1,'run_id':task['task_id'],'benchmark_id':'interactive-task','benchmark':{'id':'interactive-task','title':'Explicit resumed coding task','prompt':prompt,'browser':{'enabled':False}},'profile_id':task['profile_id'],'profile_name':profiles()[task['profile_id']]['name'],'policy':profiles()[task['profile_id']]['policy'],'workspace':task['workspace'],'artifact_directory':str(folder),'report_path':str(folder/'summary.md'),'status':task.get('status','INACTIVE'),'acp_session_id':session,'acp_process':task.get('acp_process'),'model_prompt_submissions':1,'prompt_sha256':sha(prompt),'original_prompt_reference':reference,'created_at':task['created_at']}
def recover_dead(group,store=STORE):
    folder=group_path(group['group_id'],store)
    if group['status'] in TERMINAL or alive(group.get('worker')):return group
    if time.time()-group.get('created_epoch',0)<10:return group
    group.update(status='INACTIVE',error='Benchmark worker exited. Explicit Resume required; queued/completed rows preserved.',interrupted_at=now())
    for ident in group['run_ids']:
        row=find_run(ident,store)
        if row['status']=='COMPLETE' and row.get('agent_collection_finished_at') and not row.get('finished_at'):
            row.update(status='INACTIVE',resume_evaluation_only=True,error='Agent completed; post-completion evaluation was interrupted. Explicit Resume will finish evidence without another ACP task.');persist(row,store)
        if row['status'] not in TERMINAL and row['status']!='QUEUED':
            row.update(status='INACTIVE',error=group['error'],interrupted_at=now())
            telemetry=row.get('telemetry_references',{}).get('journal')
            if telemetry and Path(telemetry).is_file():shutil.copy2(telemetry,Path(row['artifact_directory'])/'telemetry.jsonl')
            summary(row);persist(row,store)
        if alive(row.get('acp_process')):
            group['orphaned_benchmark_acp']=row['acp_process'];group['error']='Worker exited but its benchmark ACP is still live. Evidence preserved; do not start another workload. Explicit cancellation required.'
    save(folder/'group.json',group);return group
def status(store=STORE):
    group=read(Path(store)/'active.json')
    group=read(group_path(group['group_id'],store)/'group.json') if group else None
    if group:group=recover_dead(group,store)
    index=read(Path(store)/'index.json',{})
    recent=index.get('runs',[])[-6:][::-1] if isinstance(index,dict) and isinstance(index.get('runs',[]),list) else []
    rows=[find_run(r,store) for r in group['run_ids']] if group else []
    return {'ok':True,'registry':[{'id':r['id'],'title':r['title']} for r in registry(store).values()],
            'profiles':[{'id':k,**v} for k,v in profiles().items()], 'default_profile':'scope',
            'group':group,'runs':rows,'recent':recent,'comparison_report':group.get('comparison_report') if group else None}
def start(benchmark,selected,continue_after_failure=True,store=STORE,open_zed=False):
    store=Path(store);selected=[profile_id(v) for v in selected]
    if len(selected)!=len(set(selected)):raise ValueError('Each selected profile runs once per group')
    with lock(store/'control.lock'):
        old=read(store/'active.json')
        if old:
            old=read(group_path(old['group_id'],store)/'group.json');old=recover_dead(old,store)
            if old.get('orphaned_benchmark_acp') and alive(old['orphaned_benchmark_acp']):raise RuntimeError(old['error'])
            if old['status'] not in TERMINAL:raise RuntimeError('A benchmark group is already queued/running')
        definition=registry(store)[benchmark];ident=time.strftime('%Y%m%d-%H%M%S',time.gmtime())+'-'+uuid.uuid4().hex[:10]
        folder=group_path(ident,store);folder.mkdir(parents=True,exist_ok=False)
        rows=[create_run(definition,p,ident,store) for p in selected]
        for row in rows:persist(row,store)
        group={'schema':1,'group_id':ident,'benchmark_id':benchmark,'profile_order':selected,'run_ids':[r['run_id'] for r in rows],
               'status':'QUEUED','created_epoch':time.time(),'created_at':now(),'continue_after_failure':continue_after_failure,'monitor_options':{'open_zed':bool(open_zed)}}
        _save(folder/'group.json',group);_save(store/'active.json',{'group_id':ident})
        with (folder/'worker.log').open('w') as log:
            child=subprocess.Popen([PYTHON,'-B',str(Path(__file__).resolve()),'_worker',ident],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        # Worker reads after this exact launch identity is persisted.
        group['worker']=identity(child.pid);save(folder/'group.json',group)
        return {'ok':True,'group_id':ident,'run_ids':group['run_ids'],'status':'QUEUED'}
def finalize_only(row,store=STORE):
    try:row['validation']=evaluate(row);row.update(status='COMPLETE',phase='IDLE',finished_at=now())
    except Exception as e:row.update(status='FAILED',error='Resumed evaluation: '+str(e),finished_at=now())
    save(Path(row['artifact_directory'])/'project-manifest.json',manifest(row['workspace']));summary(row);persist(row,store)
    return row
def worker(ident,store=STORE,execute=execute_run):
    folder=group_path(ident,store);cancel=lambda:(folder/'cancel.flag').exists()
    with lock(Path(store)/'execution.lock'):
        # Parent launch receipt may still be being committed (no model work here).
        for _ in range(100):
            group=read(folder/'group.json')
            if group.get('worker',{}).get('pid')==os.getpid():break
            time.sleep(.03)
        if group.get('worker',{}).get('pid')!=os.getpid():raise RuntimeError('Worker identity receipt mismatch')
        group.update(status='RUNNING',started_at=now(),worker=identity(os.getpid()));save(folder/'group.json',group);rows=[];server_identity=None
        for ident in group['run_ids']:
            row=find_run(ident,store)
            if row['status'] in TERMINAL:
                rows.append(row);continue
            if row['status']=='INACTIVE' and group.get('resume_target')!=ident:
                group.update(status='INACTIVE',error='Unfinished row needs explicit Resume');save(folder/'group.json',group);return
            group['active_index']=group['run_ids'].index(ident);group['completed_run_ids']=[r['run_id'] for r in rows if r['status'] in TERMINAL];group['queued_run_ids']=group['run_ids'][group['active_index']+1:];save(folder/'group.json',group)
            if server_identity and not alive(server_identity):group['infrastructure_fault']='Server identity changed/exited; no automatic restart.'
            if cancel() or group.get('infrastructure_fault') or (rows and rows[-1]['status']=='FAILED' and not group['continue_after_failure']):
                row.update(status='CANCELLED',error='Group cancelled / configured stop after failure',finished_at=now());summary(row);persist(row,store)
            elif row.get('resume_evaluation_only'):
                # Original model turn is complete: resume only detached evidence collection.
                row=finalize_only(row,store)
            else:row=execute(row,cancel,store)
            server_identity=server_identity or row.get('runtime_identity')
            rows.append(row)
        if len(rows)>=2:
            try:group['comparison_report']=comparison(rows,folder)
            except Exception as e:group['report_error']=str(e)
        group.update(status='CANCELLED' if cancel() else 'FAILED' if any(r['status']=='FAILED' for r in rows) else 'COMPLETE',finished_at=now(),completed_run_ids=[r['run_id'] for r in rows if r['status']=='COMPLETE'],queued_run_ids=[],active_index=None)
        save(folder/'group.json',group)
def cancel_group(ident,store=STORE):
    folder=group_path(ident,store);group=read(folder/'group.json')
    if not group:raise ValueError('Unknown group')
    orphan=group.get('orphaned_benchmark_acp')
    if orphan and alive(orphan):
        children=descendents(orphan['pid'])
        if alive(orphan):os.kill(orphan['pid'],signal.SIGTERM)
        for child in children:
            if alive(child):
                try:os.kill(child['pid'],signal.SIGTERM)
                except ProcessLookupError:pass
        event(folder/'orphan-cancel.jsonl',{'event':'explicit_orphan_cancellation','acp':orphan,'children':children})
        return {'ok':True,'owned_orphan_cancel_requested':True}
    if group['status'] in TERMINAL:return {'ok':True,'already_terminal':True}
    save(folder/'cancel.flag',{'requested_at':now()});return {'ok':True,'cancellation_requested':True,'preserves_workspaces':True}
def approval(run_id,allow,store=STORE):
    row=find_run(run_id,store);folder=Path(row['artifact_directory']);pending=read(folder/'pending-permission.json')
    if not pending:raise ValueError('No pending sensitive request')
    kind='allow_once' if allow else 'reject_once';option=next(x for x in pending['params']['options'] if x['kind']==kind)
    save(folder/'permission-decision.json',{'request_id':pending['request_id'],'option_id':option['optionId'],'at':now()})
    return {'ok':True,'decision':kind}
def cleanup(run_id,store=STORE):
    # Explicit opt-in only, after complete artifact capture. Partial runs stay.
    row=find_run(run_id,store);folder=Path(row['artifact_directory']);workspace=Path(row['workspace'])
    if row['status']!='COMPLETE' or not (folder/'project-manifest.json').is_file():raise ValueError('Only captured complete workspaces can be disposed')
    if workspace.is_symlink() or workspace.resolve().parent!=workspace_root(store).resolve():raise ValueError('Not an owned benchmark workspace')
    if manifest(workspace)!=read(folder/'project-manifest.json'):raise ValueError('Workspace changed after capture; refusing removal')
    archive=folder/'project.tar.gz'
    if archive.exists():raise ValueError('Archive already exists; inspect before cleanup')
    import tarfile
    with tarfile.open(archive,'w:gz') as tar:tar.add(workspace,arcname=workspace.name,recursive=True)
    # Verify archive members and bytes before touching the sole source copy.
    expected={x['path']:x for x in read(folder/'project-manifest.json')}
    with tarfile.open(archive) as tar:
        for rel,r in expected.items():
            member=tar.getmember(workspace.name+'/'+rel)
            if 'symlink' in r:
                if member.linkname!=r['symlink']:raise ValueError('Archive link mismatch')
            elif sha(tar.extractfile(member).read())!=r['sha256']:raise ValueError('Archive checksum mismatch')
    if manifest(workspace)!=read(folder/'project-manifest.json'):raise ValueError('Workspace changed during archive; refusing removal')
    shutil.rmtree(workspace);row.update(workspace_disposed_at=now(),project_archive=str(archive));persist(row,store)
    return {'ok':True,'archive':str(archive)}
def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    for action in ['run','compare']:
        p=sub.add_parser(action);p.add_argument('benchmark');p.add_argument('--profile',default='scope');p.add_argument('--profiles',default=','.join(ORDER));p.add_argument('--stop-on-failure',action='store_true');p.add_argument('--dry-run',action='store_true');p.add_argument('--quiet',action='store_true');p.add_argument('--open-zed',action='store_true')
    sub.add_parser('status');sub.add_parser('list');sub.add_parser('monitor-status')
    sub.add_parser('system-status');sub.add_parser('system-startup')
    p=sub.add_parser('resume');p.add_argument('task')
    p=sub.add_parser('_resume-task');p.add_argument('task')
    p=sub.add_parser('watch');p.add_argument('group');p.add_argument('--quiet',action='store_true')
    p=sub.add_parser('cancel');p.add_argument('group')
    p=sub.add_parser('approve');p.add_argument('run_id');p.add_argument('--allow-once',action='store_true')
    p=sub.add_parser('report');p.add_argument('run_ids',nargs='+')
    p=sub.add_parser('cleanup');p.add_argument('run_id')
    p=sub.add_parser('_worker');p.add_argument('group')
    a=parser.parse_args()
    if a.action in ['run','compare']:
        selected=[profile_id(a.profile)] if a.action=='run' else [profile_id(x) for x in a.profiles.split(',')]
        if a.benchmark not in registry():raise ValueError('Unknown benchmark')
        if a.dry_run:
            value={'ok':True,'dry_run':True,'benchmark_id':a.benchmark,'prompt_sha256':registry()[a.benchmark]['prompt_sha256'],'profiles':selected,'sequential':True,'snapshots':[config_snapshot(k) for k in selected],'model_requests':0,'workspace_created':False}
        else:
            value=start(a.benchmark,selected,not a.stop_on_failure,open_zed=a.open_zed)
            if not a.quiet:
                try:
                    print(json.dumps(value),flush=True)
                    from live_monitor import follow_group
                    follow_group(value['group_id'],sink=lambda line:print(line,file=sys.stderr,flush=True))
                except (Exception,KeyboardInterrupt):pass
                return
    elif a.action in ['list','status']:value=status()
    elif a.action=='monitor-status':
        from live_monitor import passive_catalog
        value={'ok':True,**passive_catalog(),'registry':[{'id':r['id'],'title':r['title']} for r in registry().values()]}
        value['comparison_report']=value.get('group',{}).get('comparison_report')
    elif a.action in ['system-status','system-startup']:
        from preflight import integrity
        if a.action=='system-startup':
            # No native model work; reconcile confidently owned dead-task children only.
            infra.atomic(infra.STATE/'startup-integrity.json',integrity())
        value={'ok':True,**infra.Store().reconcile(cleanup=a.action=='system-startup')}
        for key,name in [('integrity','startup-integrity.json'),('preflight','last-preflight.json')]:
            try:value[key]=infra.read(infra.STATE/name,{})
            except (OSError,ValueError) as e:value[key]={};value['errors'].append({'error':name+': '+str(e)})
        # Legacy benchmark results are discovered read-only, never rewritten at startup.
        from live_monitor import passive_catalog
        try:
            catalog=passive_catalog()
            if a.action=='system-startup' and catalog.get('group') and catalog['group'].get('status') not in TERMINAL:
                recover_dead(catalog['group']);catalog=passive_catalog()
        except Exception as e:
            catalog={};value['errors'].append({'error':'Benchmark catalog: '+str(e)})
        known={r['task_id'] for r in value['tasks']}
        for row in catalog.get('runs',[]):
            if row['run_id'] in known:continue
            state='active/recoverable' if alive(row.get('acp_process')) else 'completed' if row['status']=='COMPLETE' else row['status'].lower() if row['status'] in TERMINAL else 'inactive/unfinished'
            value['tasks'].append({**row,'task_id':row['run_id'],'classification':state,'resume_enabled':state in ['inactive/unfinished','failed','cancelled'] and Path(row['workspace']).is_dir()})
    elif a.action=='resume':value=resume_task(a.task)
    elif a.action=='_resume-task':
        with lock(STORE/'execution.lock'):
            for _ in range(100):
                task=infra.Store().get(a.task) or {}
                if task.get('resume_worker',{}).get('pid')==os.getpid():break
                time.sleep(.03)
            if task.get('resume_worker',{}).get('pid')!=os.getpid():raise RuntimeError('Resume worker ownership receipt unavailable; no ACP started')
            row=read(infra.STATE/'executions'/a.task/'run.json')
            if row.get('resume_evaluation_only'):finalize_only(row)
            else:execute_run(row,lambda:False)
        return
    elif a.action=='watch':
        from live_monitor import follow_group
        follow_group(a.group,quiet=a.quiet);return
    elif a.action=='cancel':value=cancel_group(a.group)
    elif a.action=='approve':value=approval(a.run_id,a.allow_once)
    elif a.action=='cleanup':
        with lock(STORE/'execution.lock'):value=cleanup(a.run_id)
    elif a.action=='report':
        with lock(STORE/'execution.lock'):
            value={'ok':True,'report':comparison([find_run(r) for r in a.run_ids],STORE/'reports'/uuid.uuid4().hex)}
    else:worker(a.group);return
    print(json.dumps(value))
if __name__=='__main__':
    try:main()
    except Exception as e:print(json.dumps({'ok':False,'error':str(e)}));sys.exit(1)
