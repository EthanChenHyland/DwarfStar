import {appendFileSync} from 'node:fs';
import {observe,infrastructureSettings} from './lifecycle.ts';
import {shapeResult,contentBytes,browserCommand} from './result-hygiene.ts';
import {FinishNudge,FINISH_NUDGE,isResume} from './finish-nudge.ts';
import {taskSummary} from './task-summary.ts';
import {BenchmarkObserver} from './benchmark-observability.ts';
export function installEfficiency(pi:any,l:any,options:any={}){
 const flags=infrastructureSettings(options.settings),clock=options.clock??l.clock,wall=options.wall??l.wall,core=l.control;
 // Only newly loaded ACP extensions set this marker. Already-running ACP children
 // keep legacy browser output even though the shared helper source was updated.
 if(!options.noProcessMarker)process.env.DWARFSTAR_HARNESS_EFFICIENCY=flags.tool_result_hygiene_enabled===true||flags.finish_nudge_enabled===true?'1':'0';
 let task:any=null,nudge=new FinishNudge(),pending:string|null=null,sequence=0,benchmark:any=null;
 const event=(type:string,data:any={})=>observe(()=>l.event(type,{task_id:task?.id??null,...data}));
 // Each observer call is isolated from shaping, nudge decisions and persistence.
 const passive=(method:string,...args:any[])=>{if(!benchmark)return;try{return observePromise(benchmark[method](...args));}catch{event('benchmark_collection_error',{component:method,control_action:'none'});}};
 const observePromise=(value:any)=>{if(value?.catch)value.catch(()=>event('benchmark_collection_error',{component:'persistence',control_action:'none'}));return value;};
 const persist=options.persist??((summary:any)=>{const path=(options.env??process.env).DWARFSTAR_LATENCY_FILE;if(path)appendFileSync(path,JSON.stringify({schema:2,event:'task_summary',timestamp:wall(),session_id:summary.session_id,task_id:summary.task_id,summary})+'\n',{mode:0o600});});
 const finish=(outcome:string)=>{
  if(!task||task.ended)return;task.ended=true;
  if(task.reasoningStart!==null){task.longestReasoning=Math.max(task.longestReasoning,clock()-task.reasoningStart);task.reasoningStart=null;}
  const extra=passive('summary',nudge,wall(),outcome,l.requests.slice(task.requestIndex));
  const snapshot=passive('snapshot',{terminal_state:outcome},wall());
  if(snapshot)passive('persist',snapshot);
  if(extra)extra.benchmark_snapshot_persisted=passive('persistenceStatus')??false;
  if(flags.timing_summary_enabled===true)try{const summary={...taskSummary(task,l,nudge,clock(),wall(),outcome),...extra};const saved=persist(summary);if(saved?.then)saved.then(()=>event('task_summary_persisted',{terminal_state:outcome}),()=>event('efficiency_collection_error',{component:'summary',control_action:'none'}));else event('task_summary_persisted',{terminal_state:outcome});}catch{event('efficiency_collection_error',{component:'summary',control_action:'none'});}
 };
 const begin=(prompt:string,c:any,restore=false)=>{
  if(task&&!task.ended)finish('superseded_by_distinct_user_task');
  nudge=new FinishNudge();nudge.begin(prompt);task={id:`${c.sessionManager.getSessionId()}:task-${++sequence}`,session:c.sessionManager.getSessionId(),prompt,clock:clock(),wall:wall(),requestIndex:l.requests.length,toolIndex:l.tools.length,loopIndex:l.loops.length,originalBytes:0,visibleBytes:0,browserCalls:0,recoveries:0,holds:0,longestReasoning:0,reasoningStart:null,summaryRevision:0,ended:false};
  if(restore)try{
   if(!c.sessionManager.getBranch)throw Error('History unavailable for resumed task');
   for(const entry of [...c.sessionManager.getBranch()].reverse()){
    if(entry.type==='custom_message'&&entry.customType==='dwarfstar-late-finish'){
     nudge.sent=true;nudge.attempted=true;const stamp=Date.parse(entry.timestamp)/1000;nudge.sentAt=Number.isFinite(stamp)?stamp:null;task.restored=true;
     if(entry.details?.task_id)task.id=entry.details.task_id;break;
    }
    if(entry.type==='message'&&entry.message?.role==='user'){
     const text=typeof entry.message.content==='string'?entry.message.content:entry.message.content?.filter((x:any)=>x.type==='text').map((x:any)=>x.text).join('\n')??'';
     if(!isResume(text))break;
    }
   }
  }catch{nudge.attempted=true;task.restoreUnavailable=true;event('efficiency_collection_error',{component:'resume_nudge_history_unavailable',control_action:'skip_nudge_only'});}
  benchmark=null;
  if(flags.benchmark_observability_enabled===true)observe(()=>{const create=options.benchmarkFactory??((t:any,f:any,o:any)=>new BenchmarkObserver(t,f,o));benchmark=create(task,flags,{path:(options.env??process.env).DWARFSTAR_LATENCY_FILE,event,...options.benchmark});});
 };
 const on=(type:string,fn:any)=>pi.on(type,(e:any,c:any)=>{try{return fn(e,c);}catch{event('efficiency_collection_error',{component:type,control_action:'none'});}});
 const listener=(name:string)=>{if(!task||task.ended)return;if(name==='recovery_started')task.recoveries++;if(name==='infrastructure_hold')task.holds++;};
 l.subscribe?.(listener);
 on('input',(e:any)=>{if(e.source!=='extension')pending=String(e.text??'');});
 on('before_agent_start',(e:any,c:any)=>{
  if(pending!==null){const prompt=pending||String(e.prompt??'');pending=null;if(!task||!isResume(prompt))begin(prompt,c,isResume(prompt));else task.ended=false;}
  else if(!task)begin(String(e.prompt??''),c,true);
 });
 on('before_provider_request',(e:any)=>{if(task&&!task.ended)passive('request',e.payload);});
 on('tool_execution_start',(e:any)=>{if(task&&!task.ended)nudge.toolStart(e);});
 on('tool_execution_end',(e:any)=>{if(task)nudge.toolEnd(e);});
 on('tool_result',(e:any)=>{
  if(!task||task.ended)return;
  let shaped:any;
  try{
   // Evidence is examined before optional shaping; never inferred from a summary.
   nudge.result(e,core?.sequence??l.requests.length,wall());
   if(e.toolName==='bash'&&browserCommand(String(e.input?.command??''))&&!e.details?.__synthetic)task.browserCalls++;
   shaped=(options.shape??shapeResult)(e,{taskPrompt:task.prompt,enabled:flags.tool_result_hygiene_enabled===true});
  }catch{event('efficiency_collection_error',{component:'result_shaping',control_action:'none'});}
  const original=contentBytes(e.content??[]),visible=shaped?.result?contentBytes(shaped.result.content??e.content):original;
  task.originalBytes+=original;task.visibleBytes+=visible;
  event('tool_result_hygiene',{tool_name:e.toolName,tool_call_id:e.toolCallId,original_bytes:original,visible_bytes:visible,rule:shaped?.meta?.rule??'fail_open_unchanged',successful:!e.isError,raw_expansion_available:!!shaped?.meta?.raw_expansion_available});
  passive('result',e,shaped,core?.request?.id,core?.sequence??l.requests.length,wall());
  return shaped?.result;
 });
 on('message_update',(e:any)=>{
  if(!task||task.ended)return;const x=e.assistantMessageEvent;
  if(x?.type==='thinking_delta'&&x.delta?.length)task.reasoningStart??=clock();
  else if(task.reasoningStart!==null&&(x?.type==='thinking_end'||['text_delta','toolcall_delta','done','error'].includes(x?.type))){task.longestReasoning=Math.max(task.longestReasoning,clock()-task.reasoningStart);task.reasoningStart=null;}
 });
 on('message_end',(e:any)=>{if(task&&!task.ended&&e.message?.role==='assistant'){nudge.assistant(e.message);passive('action',e.message,core?.request?.id,wall());}});
 on('turn_end',(e:any,c:any)=>{
  const pendingMessages=task&&!task.ended&&flags.finish_nudge_enabled===true?c.hasPendingMessages?.():false;
  if(task&&!task.ended)passive('eligibility',nudge,core,e,wall(),!!pendingMessages);
  if(!task||task.ended||flags.finish_nudge_enabled!==true||pendingMessages)return;
  const eligible=options.evaluate?options.evaluate(nudge,core,e):nudge.evaluate(core,e);
  passive('evaluated',!!eligible,core,wall());
  if(!eligible)return;
  // Reserve first: even an uncertain delivery exception must never cause a retry.
  nudge.attempted=true;
  try{pi.sendMessage({customType:'dwarfstar-late-finish',display:false,content:FINISH_NUDGE,details:{task_id:task.id}},{deliverAs:'aside',triggerTurn:false});nudge.sent=true;nudge.sentAt=wall();event('finish_nudge_sent',{finish_nudge_eligible:true,revision:nudge.revision,validation_receipts:nudge.validations.length});passive('sent',nudge,wall());}
  catch{passive('deliveryFailed');event('efficiency_collection_error',{component:'finish_nudge_delivery',control_action:'none'});}
 });
 on('agent_end',()=>{if(core?.cancelled)finish('user_cancelled');else if(!l.recovering)finish(core?.request?.status==='NORMAL_COMPLETE'?'completed_turn':'interrupted_or_error');});
 on('session_shutdown',()=>finish(core?.cancelled?'user_cancelled':'session_shutdown'));
 return {get task(){return task;},get benchmark(){return benchmark;},get nudge(){return nudge;},finish,flags};
}
