// Supported OMP hooks: normal tools have no recovery/telemetry admission gate.
import {readFileSync} from 'node:fs';
import {dlopen,FFIType,ptr} from 'bun:ffi';
import {Latency,RECOVERY,INFRA} from './latency.ts';
import {API,guardedStream} from './transport.ts';
import {Lifecycle,infrastructureSettings,observe,activeProfile} from './lifecycle.ts';
const ROOT='$LOCAL_AI_SETUP/challengers/dwarfstar-paperniuk';
export function installInfrastructure(pi:any,options:any={}){
 const settings=infrastructureSettings(options.settings),l=new Latency({...options,enabled:settings.telemetry_enabled});
 const core=new Lifecycle((name,data)=>observe(()=>l.event(name,data)));(l as any).control=core;(l as any).settings=settings;
 const source=options.provider??Bun.YAML.parse(readFileSync(ROOT+'/acp/models.yml','utf8')).providers['dwarfstar-local'];
 pi.registerProvider('dwarfstar-local',{...source,authHeader:false,apiKey:'none',api:API,models:source.models.map((m:any)=>({...m,api:API})),streamSimple:(model:any,context:any,opts:any)=>guardedStream(model,context,opts,l,{...options.transport,lifecycle:core,recoveryEnabled:settings.recovery_enabled})});
 let ctx:any,recoveryUsed=false,queued=false,recovering=false,freshUser=false,recoveryCause:any=null;
 const on=(name:string,fn:any)=>pi.on(name,(e:any,c:any)=>{ctx=c;const failed=()=>observe(()=>l.event('collection_error',{component:name,control_action:'none'}));try{const v=fn(e,c);return v?.catch?v.catch(failed):v;}catch{failed();}});
 on('session_start',async(_e:any,c:any)=>{
  core.session=c.sessionManager.getSessionId();core.workspace=c.cwd??options.workspace??null;l.session=core.session;
  if(c.modelRegistry&&pi.setModel){const bound=c.modelRegistry.find('dwarfstar-local','qwen3.8-flash-next');if(!bound||bound.api!==API||!await pi.setModel(bound))throw new Error('DwarfStar transport guard binding failed');}
  observe(()=>l.event('session_start',{profile:activeProfile().name,tool_names:pi.getActiveTools(),capabilities:'agent-tools/extension.ts environmental bootstrap',recovery_enabled:settings.recovery_enabled,telemetry_enabled:settings.telemetry_enabled}));
 });
 on('input',(e:any)=>{if(e.source!=='extension')freshUser=true;});
 on('before_agent_start',()=>{if(freshUser||(!queued&&!recovering)){freshUser=false;core.cancelled=false;recoveryUsed=false;recovering=false;recoveryCause=null;observe(()=>{l.turn++;l.event('user_turn_start');});}});
 on('message_update',(e:any)=>observe(()=>l.delta(e.assistantMessageEvent)));
 on('message_end',(e:any)=>{
  const m=e.message;if(m?.role==='toolResult'){core.delivered(m.toolCallId);return;}
  if(m?.role!=='assistant')return;if(m.stopReason==='aborted'){core.cancelled=true;if(core.request)core.terminal('USER_CANCELLED',core.request.wire);}
  observe(()=>l.finish(m,core.request?.wire??{},core.cancelled));
 });
 // Read-only bookkeeping, not a dispatch/side-effect gate. Always returns undefined.
 on('tool_call',(e:any)=>{observe(()=>core.dispatched(e));});
 on('tool_execution_start',(e:any)=>{core.startTool(e);observe(()=>l.toolStart(e));});
 on('tool_execution_end',async(e:any,c:any)=>{core.endTool(e);observe(()=>l.toolEnd(e));if(core.request?.signal)await recover({},c,false);});
 const recover=async(e:any,c:any,stopHook:boolean)=>{
  const r=core.request;
  if(!r||queued||e.willContinue||core.cancelled||e.signal?.aborted||c.hasPendingMessages?.())return;
  if(recovering){recovering=false;observe(()=>{l.recovering=false;if(r.status==='NORMAL_COMPLETE'){l.successes++;l.event('recovery_succeeded',{...core.facts(),recovery_cause:recoveryCause});}else{l.failures++;l.event('recovery_failed',{...core.facts(),classification:r.status,recovery_cause:recoveryCause});}});}
  // No observation, incomplete parser or pending tool can manufacture this signal.
  if(!r.signal||!INFRA.has(r.status))return;
  if(!settings.recovery_enabled){core.event('recovery_disabled',{condition:'explicit terminal failure observed; native error handling retained'});return;}
  const decision=core.decision();
  if(decision.action==='none'||decision.action==='defer'){core.event('recovery_deferred',{condition:decision.reason});return;}
  if(decision.action==='hold'){core.event('infrastructure_hold',{condition:decision.reason,inspection:decision.inspection,manual_inspection_required:true});return;}
  if(recoveryUsed){if(!r.exhaustion_logged){r.exhaustion_logged=true;core.event('recovery_exhausted',{condition:'terminal failure repeated after bounded recovery',limit:1});}return;}
  let healthy=false;try{healthy=options.health?await options.health():await sameRuntime();}catch{}
  if(core.cancelled||e.signal?.aborted||!core.alive||c.hasPendingMessages?.())return;
  if(!healthy){core.event('infrastructure_hold',{condition:'runtime health or ownership unconfirmed after terminal failure',classification:'RUNTIME_PROCESS_ERROR',inspection:decision.inspection});return;}
  recoveryUsed=true;queued=true;recovering=true;recoveryCause={...core.facts(),condition:decision.reason,inspection:decision.inspection};observe(()=>{l.recoveries++;l.recovering=true;});
  core.event('recovery_started',{condition:decision.reason,classification:r.status,attempt:1,limit:1,inspection:decision.inspection,side_effect_policy:'No direct replay; resume original task through normal tool approval/boundary path.'});
  const context=RECOVERY+(decision.inspection?.length?'\nObserved local operation state (use original tool path; do not repeat committed work): '+JSON.stringify(decision.inspection):'');
  if(stopHook)return {continue:true,additionalContext:context};
  pi.sendMessage({customType:'dwarfstar-infrastructure-recovery',display:false,content:context},{deliverAs:'nextTurn',triggerTurn:true});
 };
 on('session_stop',(e:any,c:any)=>recover(e,c,true));
 on('before_provider_request',()=>{observe(()=>{l.provider_passes++;l.event('provider_pass_start',{...core.facts(),provider_pass:l.provider_passes});});queued=false;});
 on('agent_end',async(e:any,c:any)=>{await recover(e,c,false);observe(()=>l.event('session_summary',{summary:l.summary()}));});
 on('session_shutdown',()=>{
  core.disconnect();const r=core.request;
  observe(()=>{if(l.current&&(core.cancelled||r?.signal))l.finish({stopReason:core.cancelled?'aborted':'error'},{...r?.wire, failure:core.cancelled?'USER_CANCELLED':'ACP_TRANSPORT_INTERRUPTED'},core.cancelled);});
  if(!core.cancelled&&r?.signal)core.event('recovery_withheld',{condition:'ACP closed; no automatic reconnect or tool replay',inspection:core.decision().inspection});
  core.event('session_shutdown',{classification:core.cancelled?'USER_CANCELLED':r?.status??'NORMAL_COMPLETE',automatic_resume:false});observe(()=>l.event('session_summary',{summary:l.summary()}));
 });
 return l;
}
async function sameRuntime(){
 const before=JSON.parse(readFileSync(ROOT+'/state/server.json','utf8'));
 // Read-only Darwin process birth lookup, including inside Seatbelt. /bin/ps
 // is setuid and cannot execute there; do not weaken the sandbox to run it.
 const lib=dlopen('/usr/lib/libproc.dylib',{proc_pidinfo:{args:[FFIType.i32,FFIType.i32,FFIType.u64,FFIType.ptr,FFIType.i32],returns:FFIType.i32}});
 let birth:number;try{const info=Buffer.alloc(136);if(lib.symbols.proc_pidinfo(before.pid,3,0,ptr(info),136)!==136)return false;birth=Number(info.readBigUInt64LE(120));}finally{lib.close();}
 if(birth!==Math.floor(Date.parse(before.birth)/1000))return false;
 const r=await fetch('http://127.0.0.1:8030/v1/models',{signal:AbortSignal.timeout(2000)});const after=JSON.parse(readFileSync(ROOT+'/state/server.json','utf8'));return r.ok&&before.pid===after.pid&&before.birth===after.birth&&before.argv_sha256===after.argv_sha256;
}
