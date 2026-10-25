"""Read-only projection of existing benchmark receipts. No ACP/provider imports."""
from pathlib import Path
from collections import deque
import argparse, json, os, re, select, stat, sys, threading, time
from records import ROOT, STORE, NE, TERMINAL, read

PRIVATE=re.compile(r'(^|/)(?:\.[^/]+|node_modules|__pycache__|cache|vendor)(/|$)|(?:credential|password|secret|token|private.key|id_rsa|id_ed25519|cookies)',re.I)
def safe_relative(root,value):
    try:
        base=Path(root).resolve();p=Path(value);p=p if p.is_absolute() else base/p
        if p.is_symlink():return None
        rel=str(p.resolve().relative_to(base))
        if rel=='.' or len(rel)>200 or PRIVATE.search(rel):return None
        if any((base/Path(*Path(rel).parts[:i])).is_symlink() for i in range(1,len(Path(rel).parts)+1)):return None
        return rel
    except (OSError,ValueError):return None
def bounded_json(path,limit=2*1024*1024):
    try:
        p=Path(path)
        if p.is_symlink() or p.stat().st_size>limit:return {}
        value=json.loads(p.read_text());return value if isinstance(value,dict) else {}
    except (OSError,ValueError,TypeError):return {}
def inspect_file(workspace,name,offset=0,length=32768):
    rel=safe_relative(workspace,name)
    if rel is None:raise ValueError('Only non-sensitive workspace-local regular files can be inspected')
    base=os.open(Path(workspace).resolve(),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in Path(rel).parts[:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=base);os.close(base);base=child
        fd=os.open(Path(rel).name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=base)
    finally:os.close(base)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):raise ValueError('Not a regular source file')
        offset=max(0,int(offset));length=max(1,min(int(length),65536));os.lseek(fd,offset,os.SEEK_SET);data=os.read(fd,length)
        try:
            if b'\0' in data:raise ValueError('binary')
            # Page boundaries can split UTF-8; only boundary bytes are replaced.
            text=data.decode('utf-8')
        except (UnicodeDecodeError,ValueError):
            return {'path':rel,'binary':True,'text':'Binary/non-UTF-8 file — source preview unavailable','bytes':info.st_size,'readonly':True}
        return {'path':rel,'binary':False,'text':text,'offset':offset,'next_offset':offset+len(data),'has_more':offset+len(data)<info.st_size,'bytes':info.st_size,'readonly':True}
    finally:os.close(fd)

class Tail:
    """Bounded incremental JSONL. Oversized lines are discarded, not accumulated."""
    def __init__(self):self.offset=0;self.identity=None;self.buffer=b'';self.skipping=False;self.limited=False
    def poll(self,path):
        try:
            p=Path(path);info=p.stat()
            if p.is_symlink() or not p.is_file():return []
            ident=(info.st_dev,info.st_ino)
            if self.identity!=ident or info.st_size<self.offset:self.offset=0;self.buffer=b'';self.skipping=False;self.identity=ident
            with p.open('rb') as f:f.seek(self.offset);chunk=f.read(262144);self.offset+=len(chunk)
            self.limited=self.offset<info.st_size;parts=(self.buffer+chunk).split(b'\n');self.buffer=parts.pop();result=[]
            for part in parts:
                if self.skipping:self.skipping=False;continue
                if len(part)>262144:continue
                try:
                    value=json.loads(part)
                    if isinstance(value,dict):result.append(value)
                except ValueError:pass
            if len(self.buffer)>262144:self.buffer=b'';self.skipping=True
            return result
        except (OSError,TypeError):return []

class Workspace:
    """One bounded initial inventory, vnode updates, 10s bounded fallback."""
    def __init__(self,path):
        self.path=Path(path);self.files={};self.dirs=[];self.fds={};self.last_scan=0;self.limited=False
        try:self.kq=select.kqueue()
        except (AttributeError,OSError):self.kq=None
        self.refresh(initial=True)
    def refresh(self,initial=False):
        found={};dirs=[];todo=[self.path];limited=False
        while todo and len(dirs)<64 and len(found)<2000:
            parent=todo.pop(0)
            if parent.is_symlink():continue
            try:
                dirs.append(parent)
                with os.scandir(parent) as entries:
                    for entry in entries:
                        if len(found)>=2000:limited=True;break
                        if entry.name.startswith('.') or entry.name in ['node_modules','__pycache__','cache','vendor']:continue
                        if entry.is_symlink():continue
                        rel=str(Path(entry.path).relative_to(self.path))
                        if PRIVATE.search(rel):continue
                        if entry.is_dir(follow_symlinks=False):todo.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            s=entry.stat(follow_symlinks=False);found[rel]={'path':rel,'bytes':s.st_size,'mtime_ns':s.st_mtime_ns,'inode':s.st_ino}
            except OSError:continue
        self.limited=limited or bool(todo);events=[]
        if not initial:
            removed={k:v for k,v in self.files.items() if k not in found};added={k:v for k,v in found.items() if k not in self.files}
            for old,a in list(removed.items()):
                new=next((k for k,b in added.items() if b['inode']==a['inode']),None)
                if new is not None:events.append(('renamed',new,old));removed.pop(old);added.pop(new)
            events += [('created',k,None) for k in added]+[('deleted',k,None) for k in removed]
            events += [('modified',k,None) for k,v in found.items() if k in self.files and (v['mtime_ns'],v['bytes'])!=(self.files[k]['mtime_ns'],self.files[k]['bytes'])]
        self.files=found;self.dirs=dirs;self.last_scan=time.monotonic();self.watch();return events
    def watch(self):
        if not self.kq:return
        # <=192 descriptors; do not watch generated dependencies/cache contents.
        targets={str(p) for p in self.dirs}|{str(self.path/k) for k in list(self.files)[:128]}
        for path in list(self.fds):
            if path not in targets:os.close(self.fds.pop(path))
        for path in targets-self.fds.keys():
            fd=None
            try:
                fd=os.open(path,getattr(os,'O_EVTONLY',os.O_RDONLY)|os.O_NOFOLLOW)
                self.kq.control([select.kevent(fd,filter=select.KQ_FILTER_VNODE,flags=select.KQ_EV_ADD|select.KQ_EV_CLEAR,fflags=select.KQ_NOTE_WRITE|select.KQ_NOTE_RENAME|select.KQ_NOTE_DELETE|select.KQ_NOTE_ATTRIB)],0,0);self.fds[path]=fd
            except OSError:
                try:
                    if fd is not None:os.close(fd)
                except (OSError,UnboundLocalError):pass
    def poll(self,force=False):
        changed=False
        if self.kq:
            try:changed=bool(self.kq.control(None,192,0))
            except OSError:pass
        if force or changed or time.monotonic()-self.last_scan>=10:return self.refresh()
        return []
    def close(self):
        for fd in self.fds.values():os.close(fd)
        self.fds={}
        if self.kq:self.kq.close()

class Monitor:
    def __init__(self,row_path,group=None,clock=time.time):
        self.path=Path(row_path);self.folder=self.path.parent;self.clock=clock;self.group=group
        self.feed=deque(maxlen=100);self.ids=set();self.last=None;self.tails={};self.tool={};self.tool_meta={};self.first=None;self.regression=None;self.nudge=None;self.recovery=0;self.holds=0;self.counts={};self.created=set();self.modified=set();self.browser={};self.preview=None;self.errors=[];self.overhead=0;self.phase_seen=None;self.recovery_active=False;self.journal_seen=False
        row=bounded_json(self.path);self.workspace=Workspace(row['workspace']);self.row=row
    def add(self,key,description,timestamp=None):
        if key in self.ids:return
        if len(self.ids)>=6000:self.ids.clear()
        self.ids.add(key);stamp=timestamp if isinstance(timestamp,(int,float)) else self.clock()
        self.feed.append({'id':str(key),'timestamp':stamp,'description':description[:240]});self.feed=deque(sorted(self.feed,key=lambda e:e['timestamp'])[-100:],maxlen=100);self.last=max(self.last or stamp,stamp)
    def telemetry(self,e):
        self.journal_seen=True
        event=e.get('event');t=e.get('timestamp');t=self.timestamp(t) if isinstance(t,str) else t;key=str((event,e.get('request_id'),e.get('tool_call_id'),t));simple={'request_start':'Model request started','first_generated_item':'First generated stream item','request_end':'Model request completed','tool_start':'Tool started','tool_end':'Tool completed','recovery_started':'Recovery started','recovery_succeeded':'Recovery completed','recovery_failed':'Recovery failed','infrastructure_hold':'Infrastructure hold','finish_nudge_sent':'Finish nudge sent','task_summary':'Agent turn ended','lifecycle_tool_dispatch_ready':'Tool call dispatched'}
        if event in simple:
            text=simple[event]
            if event in ['tool_start','tool_end']:
                name=e.get('tool_name','Tool');text+=' · '+(name if re.fullmatch('[A-Za-z0-9_/-]{1,40}',name) else 'Tool')
            self.add(key,text,t)
        if event=='request_start':self.counts['model_requests']=self.counts.get('model_requests',0)+1;self.recovery_active=False
        if event=='tool_start':
            self.counts['tool_calls']=self.counts.get('tool_calls',0)+1
            self.tool={'id':e.get('tool_call_id'),'name':e.get('tool_name','Tool'),'start':t}
            if e.get('tool_name')=='bash':self.counts['shell_calls']=self.counts.get('shell_calls',0)+1
        if event=='tool_end' and self.tool.get('id')==e.get('tool_call_id'):self.tool={}
        if event=='first_functional_state':self.first=e;self.add(key,'First functional state reached',t)
        if event=='functional_state_regression':self.regression={'state':'Regression detected','elapsed_s':e.get('elapsed_wall_s',NE),'event':e};self.add(key,'Regression detected',t)
        if event=='recovered_functional_state':self.regression={'state':'Functional restored','elapsed_s':e.get('elapsed_wall_s',NE),'event':e};self.add(key,'Functional restored',t)
        if event=='finish_nudge_sent':self.nudge=t
        if event=='recovery_started':self.recovery+=1;self.recovery_active=True
        if event in ['recovery_succeeded','recovery_failed']:self.recovery_active=False
        if event=='infrastructure_hold':self.holds+=1
        if event=='task_summary':
            s=e.get('summary',{})
            if s.get('first_functional_state'):self.first=s['first_functional_state']
    def acp(self,e):
        m=e.get('message',{});u=m.get('params',{}).get('update',{});kind=u.get('sessionUpdate');t=self.timestamp(e.get('at'))
        # Never copy reasoning text, command text, tool output or source content.
        if kind in ['agent_thought_chunk','agent_message_chunk']:
            phase='REASONING' if kind=='agent_thought_chunk' else 'MODEL OUTPUT'
            if self.phase_seen!=phase:self.phase_seen=phase;self.add(str((phase,t)),phase.title()+' stream observed',t)
        if kind=='tool_call':
            self.phase_seen='TOOL CALL';args=u.get('rawInput',{});loc=(u.get('locations') or [{}])[0].get('path') or args.get('path')
            rel=safe_relative(self.row['workspace'],loc) if isinstance(loc,str) else None
            title=u.get('title','');name=title.split(':',1)[0]
            name=name if name in ['read','write','edit','bash'] else 'Tool'
            command=args.get('command','');action='Arguments hidden'
            if rel:action=rel
            elif isinstance(command,str):
                if re.search(r'\bdwarf-browser\b|\bplaywright\b|\bpuppeteer\b',command):action='Browser operation';self.counts['browser_calls']=self.counts.get('browser_calls',0)+1
                elif re.fullmatch(r'(?:npm|pnpm|bun) (?:run )?(?:test|build|check)',command):action=command
                elif 'http.server' in command:action='Local server command (execution not yet confirmed)'
            self.tool_meta[u.get('toolCallId')]={'name':name,'action':action}
            if len(self.tool_meta)>512:self.tool_meta.pop(next(iter(self.tool_meta)))
            self.add('call:'+str(u.get('toolCallId')),'Tool call · '+name+' · '+action,t)
        if kind=='tool_call_update':
            ident=u.get('toolCallId');state=u.get('status');meta=self.tool_meta.get(ident,{})
            if state=='in_progress':self.add('start:'+str(ident),'Tool running · '+meta.get('name','Tool')+' · '+meta.get('action','Arguments hidden'),t)
            if state in ['completed','failed']:self.add('end:'+str(ident),'Tool '+state+' · '+meta.get('name','Tool'),t)
    @staticmethod
    def timestamp(value):
        try:
            from datetime import datetime
            return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
        except (TypeError,ValueError,AttributeError):return None
    def read_browser(self,force=False):
        # Reuse saved images/receipts only: refresh never starts a browser/server.
        candidates=[];browser_candidates=[]
        for rel,info in self.workspace.files.items():
            suffix=Path(rel).suffix.lower()
            if suffix in ['.png','.jpg','.jpeg','.webp'] and info['bytes']<=16*1024*1024:candidates.append((info['mtime_ns'],str(self.workspace.path/rel),'Workspace image; not proof of browser validation'))
            if suffix=='.json' and re.search('browser|receipt|validation',rel,re.I) and info['bytes']<262144:
                receipt=bounded_json(self.workspace.path/rel,262144)
                if receipt.get('engine')=='playwright':
                    events=receipt.get('events',{});validation=receipt.get('validation',{})
                    observed={'source':rel,'as_of':receipt.get('ended_at',NE),'page_loaded':receipt.get('ok',NE),'console_errors':len(events.get('pageErrors',[]))+sum(x.get('type') in ['error','assert'] for x in events.get('console',[])),'console_warnings':sum(x.get('type')=='warning' for x in events.get('console',[])),'failed_requests':len(events.get('failedRequests',[])),'primary_render':validation.get('primary_ui_visible',NE)}
                    browser_candidates.append((info['mtime_ns'],observed))
                    stamp=self.timestamp(receipt.get('ended_at'))
                    self.add('browser:'+rel+':'+str(info['mtime_ns']),'Browser validation saved · console errors '+str(observed['console_errors'])+' · failed requests '+str(observed['failed_requests']),stamp)
                    for step in receipt.get('results',[]):
                        if step.get('op') in ['reload','screenshot']:self.add('browser-step:'+rel+':'+str(info['mtime_ns'])+':'+step['op'],'Browser '+step['op']+' observed',stamp)
        if browser_candidates:self.browser=max(browser_candidates,key=lambda x:x[0])[1]
        final=bounded_json(self.folder/'evaluation/validation.json')
        if final:
            self.browser={'source':'Post-run validation','as_of':final.get('evaluation_ended_at',NE),'page_loaded':final.get('page_load_success',NE),'console_errors':final.get('console_errors',NE),'console_warnings':final.get('console_warnings',NE),'failed_requests':final.get('failed_network_requests',NE),'primary_render':final.get('primary_page_rendered',NE)}
            image=Path(final.get('screenshot_path',str(self.folder/'evaluation/final.png')))
            if image.is_file() and not image.is_symlink() and image.resolve().is_relative_to(self.folder.resolve()):
                candidates.append((image.stat().st_mtime_ns,str(image),'Final external browser evidence'));self.add('final-image:'+str(image.stat().st_mtime_ns),'Final browser screenshot saved',self.timestamp(final.get('evaluation_ended_at')))
        observed_time=self.timestamp(self.browser.get('as_of'))
        newest_source=max((v['mtime_ns']/1e9 for k,v in self.workspace.files.items() if Path(k).suffix.lower() in ['.html','.css','.js','.mjs','.ts','.tsx','.jsx']),default=0)
        if self.browser:self.browser['source_changed_since_validation']=newest_source>observed_time if observed_time else NE
        chosen=max(candidates,default=(0,None,'Pending'));self.preview=chosen[1:];self.preview_mtime=chosen[0]
    def update(self,force=False):
        start=time.perf_counter();self.errors=[]
        try:
            row=bounded_json(self.path)
            if not row:raise ValueError('Run record unavailable')
            self.row=row
            paths={'telemetry':row.get('telemetry_references',{}).get('journal',str(self.folder/'telemetry.jsonl')),'acp':str(self.folder/'acp-events.jsonl')}
            if row.get('status') in TERMINAL and (self.folder/'telemetry.jsonl').is_file():paths['telemetry']=str(self.folder/'telemetry.jsonl')
            for name,path in paths.items():
                for item in self.tails.setdefault(name,Tail()).poll(path):
                    try:(self.telemetry if name=='telemetry' else self.acp)(item)
                    except Exception:self.errors.append('Some '+name+' observation unavailable')
            for action,rel,old in self.workspace.poll(force):
                if action=='created':self.created.add(rel)
                if action=='modified':self.modified.add(rel)
                self.add(str((action,rel,self.workspace.files.get(rel,{}).get('mtime_ns',self.clock()))),'File '+action+' · '+(old+' → ' if old else '')+rel)
            fingerprint=tuple((k,v['mtime_ns']) for k,v in self.workspace.files.items() if Path(k).suffix.lower() in ['.png','.jpg','.jpeg','.webp','.json','.html','.css','.js','.mjs','.ts','.tsx','.jsx'])
            final_stamp=(self.folder/'evaluation/validation.json').stat().st_mtime_ns if (self.folder/'evaluation/validation.json').exists() else None
            if force or getattr(self,'preview_fingerprint',None)!=(fingerprint,final_stamp):self.read_browser(force);self.preview_fingerprint=(fingerprint,final_stamp)
        except Exception:self.errors.append('Monitor collection unavailable; benchmark is unaffected')
        self.overhead+=time.perf_counter()-start
        return self.project()
    def project(self):
        row=self.row;clock=self.clock();m=row.get('metrics',{});terminal=row.get('status') in TERMINAL
        start=row.get('submitted_epoch') or self.timestamp(row.get('orchestration_started_at'))
        end=self.timestamp(row.get('agent_terminal_at')) if terminal else clock
        elapsed=max(0,(end or clock)-start) if start else NE
        scalar=bounded_json(row.get('telemetry_references',{}).get('scalar_request',str(self.folder/'last-request.json')))
        if terminal:scalar=bounded_json(self.folder/'last-request.json') or scalar
        identity=row.get('acp_process') or {};matches=scalar.get('client_pid')==row.get('acp_pid') and (not identity.get('birth') or scalar.get('client_birth')==identity['birth'])
        stale=clock-scalar.get('observed_at',0)>8
        if not matches or (not terminal and stale):scalar={}
        timing=scalar.get('session_timing',{});phase=scalar.get('phase',NE)
        if phase=='PREFILL / WAITING_FIRST_TOKEN':phase='WAITING FIRST GENERATED ITEM (provider)'
        if phase=='OUTPUT':phase='MODEL OUTPUT'
        if scalar.get('tool_execution_active'):phase='TOOL RUNNING'
        if not terminal and phase=='COMPLETE':phase='IDLE (harness)' if timing.get('phase')=='IDLE' else 'Request complete; benchmark active'
        if not terminal and phase=='ERROR':phase='Last request error; benchmark active'
        if self.recovery_active and not terminal:phase='RECOVERING'
        if (self.folder/'pending-permission.json').exists() and not terminal:phase='WAITING FOR PERMISSION'
        if terminal:phase=row['status']
        if row.get('status')=='QUEUED':phase='QUEUED'
        if row.get('status')=='STARTING':phase='STARTING'
        if row.get('status')=='COMPLETE' and not row.get('finished_at'):phase='POST-RUN EVALUATION / COLLECTION'
        first=m.get('time_to_functional_s')
        if not isinstance(first,(float,int)) and self.first:first=self.first.get('elapsed_wall_s')
        first=first if isinstance(first,(float,int)) else NE
        regression=self.regression or {'state':NE}
        nudge='Disabled' if row['profile_id']!='scope' else 'Sent' if self.nudge or m.get('finish_nudge') is True else 'Not sent' if timing or self.counts else NE
        self.tool={**self.tool,**self.tool_meta.get(self.tool.get('id'),{})} if self.tool else {}
        if not self.tool and scalar.get('tool_execution_active'):self.tool={'name':scalar.get('active_tool_name','Tool'),'action':'Arguments hidden'}
        if terminal:self.tool={}
        group=bounded_json(self.folder.parent.parent/'groups'/row.get('group_id','')/'group.json') if not self.group else self.group
        run_ids=group.get('run_ids',[]);index=run_ids.index(row['run_id'])+1 if row['run_id'] in run_ids else NE
        model={k:timing.get(v,NE) for k,v in {'request_number':'request_count','request_elapsed_s':'request_elapsed_s','ttft_s':'ttft_s','prefill_s':'current_prefill_s','reasoning_s':'reasoning_elapsed_s','last_request_status':'last_classification'}.items()}
        model.update(active=scalar.get('active',NE),decode_elapsed_s=NE,decode_rate=scalar.get('decode_tps',NE))
        counters={k:m.get(v,timing.get(t,self.counts.get(k,NE))) for k,v,t in [('model_requests','model_requests','request_count'),('tool_calls','tool_calls','tool_count'),('browser_calls','browser_tool_calls','browser_count'),('shell_calls','shell_tool_calls','shell_count'),('recoveries','recoveries','recoveries'),('holds','infrastructure_holds','hold_count'),('reasoning_s','reasoning_s','reasoning_s'),('prefill_s','prefill_s','total_prefill_s'),('tool_s','tool_s','tool_s'),('harness_idle_s','harness_idle_s','harness_idle_s')]}
        if self.journal_seen:
            if counters['holds']==NE:counters['holds']=self.holds
            if counters['recoveries']==NE:counters['recoveries']=self.recovery
        counters.update(files_created_observed=len(self.created),files_modified_observed=len(self.modified))
        return {'schema':1,'run_id':row['run_id'],'benchmark':row.get('benchmark',{}).get('title',row.get('benchmark_id',NE)),'profile':row['profile_name'],'policy':row.get('policy',NE),'status':row['status'],'phase':phase,'phase_source':'Existing provider/harness receipts only; not native kernel phase','run_number':index,'run_count':len(run_ids) or NE,'elapsed_s':elapsed,'workspace':row['workspace'],'acp_session_id':row.get('acp_session_id',NE),'acp_state':'Terminal' if terminal else 'Initialized' if row.get('acp_session_id') else 'Pending','model':model,'tool':{**self.tool,'elapsed_s':timing.get('tool_elapsed_s',NE)} if self.tool or scalar.get('tool_execution_active') else {},'last_activity_ago_s':max(0,clock-self.last) if self.last else NE,'activity':list(self.feed)[-60:],'counters':counters,'functional_s':first,'post_functional_tail_s':max(0,elapsed-first) if isinstance(elapsed,(float,int)) and isinstance(first,(float,int)) else NE,'regression':regression,'finish_nudge':nudge,'finish_nudge_at':self.nudge or NE,'finish_nudge_elapsed_s':max(0,self.nudge-start) if isinstance(self.nudge,(int,float)) and start else NE,'files':list(sorted(self.workspace.files.values(),key=lambda x:x['path'])),'filesystem_limited':self.workspace.limited,'filesystem_source':'vnode events + 10s bounded fallback' if self.workspace.kq else '10s bounded fallback','browser':self.browser,'preview_path':self.preview[0] if self.preview else None,'preview_label':self.preview[1] if self.preview else 'Pending','preview_mtime_ns':getattr(self,'preview_mtime',0),'reader_catching_up':any(t.limited for t in self.tails.values()),'errors':list(dict.fromkeys(self.errors)),'monitor_processing_wall_s':self.overhead,'monitor_activity_not_agent_activity':True,'quiet_time_is_not_failure':True,'zed_enabled':Path(row['workspace']).is_dir() and (row['status']!='QUEUED' or bool(row.get('acp_session_id'))),'artifacts':{'Runner log':str(self.folder.parent.parent/'groups'/row.get('group_id','')/'worker.log'),'ACP activity':str(self.folder/'acp-events.jsonl'),'Telemetry':str(self.folder/'telemetry.jsonl') if terminal else row.get('telemetry_references',{}).get('journal',''),'Config snapshot':str(self.folder/'config-snapshot.json'),'Run summary':row.get('report_path','')}}
    def close(self):self.workspace.close()

def find_path(run_id,store=STORE):
    from records import identifier
    identifier(run_id);paths=list(Path(store).glob('*/'+run_id+'/run.json'))
    if len(paths)!=1:raise ValueError('Unknown benchmark run')
    return paths[0]
def passive_catalog(store=STORE):
    """Unlike orchestration status, this never calls recover_dead or writes state."""
    locator=bounded_json(Path(store)/'active.json');group=bounded_json(Path(store)/'groups'/locator.get('group_id','')/'group.json')
    runs=[]
    for rid in group.get('run_ids',[]):
        try:runs.append(bounded_json(find_path(rid,store)))
        except (OSError,ValueError):pass
    index=bounded_json(Path(store)/'index.json')
    recent=index.get('runs',[])[-12:][::-1];inspectable={r.get('run_id'):r for r in runs}
    for item in recent:
        try:
            rid=item['run_id']
            if rid not in inspectable:inspectable[rid]=bounded_json(find_path(rid,store))
        except (ValueError,KeyError):pass
    return {'group':group,'runs':runs,'recent':recent,'inspectable_runs':list(inspectable.values())}
def follow_group(group_id,store=STORE,quiet=False,sink=print,stop=None):
    if quiet:return
    monitors={};seen=set();finished=set()
    try:
        while not (stop and stop()):
            group=bounded_json(Path(store)/'groups'/group_id/'group.json')
            for rid in group.get('run_ids',[]):
                try:
                    if rid in finished:continue
                    path=find_path(rid,store);row=bounded_json(path)
                    if row.get('status')=='QUEUED':
                        key=(rid,'QUEUED')
                        if key not in seen:seen.add(key);sink('[--:--] '+row.get('profile_name','Profile')+' · QUEUED')
                        continue
                    mon=monitors.setdefault(rid,Monitor(path,group=group)) if rid not in monitors else monitors[rid]
                    state=mon.update();elapsed=state['elapsed_s'];prefix=f'[{int(elapsed)//60:02}:{int(elapsed)%60:02}]' if isinstance(elapsed,(int,float)) else '[--:--]'
                    for e in state['activity']:
                        key=(rid,e['id'])
                        if key not in seen:
                            seen.add(key);at=max(0,e['timestamp']-row['submitted_epoch']) if isinstance(row.get('submitted_epoch'),(int,float)) else elapsed
                            event_prefix=f'[{int(at)//60:02}:{int(at)%60:02}]' if isinstance(at,(int,float)) else prefix
                            sink(event_prefix+' '+state['profile']+' · '+e['description'])
                    status_key=(rid,state['status'])
                    if status_key not in seen:seen.add(status_key);sink(prefix+' '+state['profile']+' · '+state['status'])
                    if state['status'] in TERMINAL and row.get('finished_at'):
                        mon.close();monitors.pop(rid,None);finished.add(rid)
                except (OSError,ValueError,KeyError):continue
            if group.get('status') in TERMINAL:return
            time.sleep(2)
    except (Exception,KeyboardInterrupt):return # Output/reader failure never owns the worker.
    finally:
        for mon in monitors.values():mon.close()
def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['watch','snapshot','file','open-zed','open-browser','open-workspace','open-file-zed']);p.add_argument('run_id');p.add_argument('--path');p.add_argument('--offset',type=int,default=0);a=p.parse_args();path=find_path(a.run_id);row=bounded_json(path)
    if a.action=='file':print(json.dumps(inspect_file(row['workspace'],a.path,a.offset)));return
    if a.action=='open-zed':
        from zed_navigation import open_zed
        print(json.dumps(open_zed(row)));return
    if a.action in ('open-browser','open-workspace','open-file-zed'):
        from app_navigation import open_target
        action={'open-browser':'browser','open-workspace':'workspace','open-file-zed':'file-zed'}[a.action]
        target=None
        if a.action=='open-file-zed':
            rel=safe_relative(row['workspace'],a.path)
            if rel is None:raise ValueError('Select a regular workspace source file')
            target=str(Path(row['workspace'])/rel)
        print(json.dumps(open_target(action,target=target,workspace=row['workspace'],task_id=row['run_id'])));return
    mon=Monitor(path);refresh=threading.Event();stop=threading.Event()
    if a.action=='watch':
        def control():
            for line in sys.stdin:
                if line.strip()=='refresh':refresh.set()
            stop.set()
        threading.Thread(target=control,daemon=True).start()
    try:
        while True:
            try:print(json.dumps(mon.update(refresh.is_set())),flush=True);refresh.clear()
            except (BrokenPipeError,OSError):break
            if a.action=='snapshot' or stop.wait(2):break
    finally:mon.close()
if __name__=='__main__':
    try:main()
    except Exception as e:print(json.dumps({'monitor_error':str(e),'benchmark_unaffected':True}));sys.exit(0)
