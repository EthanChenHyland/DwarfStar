// Execution facts are independent of the optional timing collector. No watchdogs.
import {readFileSync,lstatSync,realpathSync} from 'node:fs';
import {resolve,relative,dirname,isAbsolute} from 'node:path';
import {ROOT,HOME} from './paths.ts';
export function activeProfile(){
 const registry=JSON.parse(readFileSync(ROOT+'/acp/profiles.json','utf8')),id=process.env.DWARFSTAR_PROFILE_ID??registry.default;
 const profile=registry.profiles[id];if(!profile)throw Error('Unknown DwarfStar profile');return {id,...profile};
}
export function infrastructureSettings(overrides:any={}){
 let value:any={recovery_enabled:true,telemetry_enabled:true};
 try{value={...value,...JSON.parse(readFileSync(HOME+'/acp/infrastructure.json','utf8'))};}
 catch{value={recovery_enabled:false,telemetry_enabled:true};}
 return {...value,...activeProfile().features,...overrides};
}
export const observe=(fn:()=>any,fallback:any=undefined)=>{try{const value=fn();if(value?.catch)value.catch(()=>{});return value;}catch{return fallback;}};
export const TERMINAL_SIGNALS=new Set(['unexpected_eof','transport_exception','provider_protocol_error','acp_disconnect','runtime_process_failure','tool_process_failure','tool_channel_closed','deadline_expired']);
export class Lifecycle {
 session='Pending';sequence=0;request:any=null;requests:any[]=[];tools=new Map<string,any>();workspace:string|null=null;alive=true;cancelled=false;
 constructor(public emit:(type:string,data?:any)=>void=()=>{}){}
 event(type:string,data:any={}){observe(()=>this.emit(type,{...this.facts(),...data}));}
 begin(){const r:any={id:`${this.session}:${++this.sequence}`,state:'MODEL_STREAMING',status:'Pending',connection:'open',parser:'awaiting_items',signal:null,wire:{}};this.request=r;this.requests.push(r);this.event('lifecycle_request_start');return r;}
 facts(r:any=this.request){return {request_id:r?.id??null,lifecycle_state:r?.state??'IDLE',connection_state:this.alive?(r?.connection??'idle'):'closed',parser_state:r?.parser??'not_active',tool_state:[...this.tools.values()].filter(t=>!t.delivered).map(t=>({tool_call_id:t.id,tool_name:t.name,state:t.state})),timeout_watchdog:r?.wire?.timeout_watchdog??null,terminal_signal:r?.signal??null};}
 streamItem(wire:any){const r=this.request;if(!r||r.signal)return;r.wire=wire;r.parser=wire.parser_state;r.state=wire.tool_calls?.length?'TOOL_CALL_STREAMING':'MODEL_STREAMING';}
 terminal(status:string,wire:any={},r:any=this.request){
  if(!r)return;r.status=status;r.wire=wire;r.parser=wire.parser_state??r.parser;
  if(status==='USER_CANCELLED'){this.cancelled=true;r.state='CANCELLED';r.signal=null;}
  else if(TERMINAL_SIGNALS.has(wire.terminal_signal)){r.signal=wire.terminal_signal;r.state='INTERRUPTED';r.connection=wire.connection_state??'failed';}
  else {r.signal=null;r.state=status==='NORMAL_COMPLETE'?(wire.tool_calls?.length?'TOOL_CALL_COMPLETE':'COMPLETE'):'TERMINAL_ERROR';r.connection='complete';}
  this.event('lifecycle_terminal',{classification:status,...this.facts(r)});
 }
 prepare(id:string,name:string,args:any){
  if(this.tools.get(id)?.started)return;
  const t:any={id,name,args,request:this.request?.id,state:'TOOL_CALL_COMPLETE',started:false,ended:false,delivered:false,executed:null};
  if(name==='write'&&typeof args?.path==='string'&&typeof args?.content==='string'){
   const p=this.localPath(args.path);
   if(p){t.path=p;t.expected=Buffer.from(args.content);try{const s=lstatSync(p);t.before={exists:true,ino:s.ino,dev:s.dev,size:s.size};}catch(e:any){t.before={exists:e.code==='ENOENT'?false:null};}}
  }
  this.tools.set(id,t);
 }
 localPath(path:string){
  if(!this.workspace)return null;const root=realpathSync(this.workspace),p=resolve(root,path),rel=relative(root,p);
  if(!rel||rel.startsWith('..')||isAbsolute(rel)||rel.split('/').some(s=>['.git','.ssh','.aws','.azure','.kube'].includes(s)))return null;
  // Reject *any* symlink component; no inspection of redirected/private targets.
  for(let part=p;part!==root;part=dirname(part)){try{const s=lstatSync(part);if(s.isSymbolicLink())return null;}catch(e:any){if(e.code!=='ENOENT')return null;}}
  return p;
 }
 startTool(e:any){this.prepare(e.toolCallId,e.toolName,e.args);const t=this.tools.get(e.toolCallId);t.started=true;t.state='TOOL_RUNNING';this.event('lifecycle_tool_running',{tool_call_id:t.id,tool_name:t.name});}
 dispatched(e:any){this.prepare(e.toolCallId,e.toolName,e.input);const t=this.tools.get(e.toolCallId);if(t){t.state='TOOL_DISPATCHED';this.event('lifecycle_tool_dispatch_ready',{tool_call_id:t.id,receipt:'harness beforeToolCall; execution and approval still owned by OMP'});}}
 endTool(e:any){const t=this.tools.get(e.toolCallId);if(!t)return;t.ended=true;t.executed=e.result?.details?.executed!==false;t.state='TOOL_RESULT_AVAILABLE';t.resultError=!!e.isError;this.event('lifecycle_tool_result_available',{tool_call_id:t.id,tool_name:t.name,executed:t.executed});}
 delivered(id:string){const t=this.tools.get(id);if(t){t.delivered=true;t.state='TOOL_RESULT_DELIVERED';this.event('lifecycle_tool_result_delivered',{tool_call_id:id,receipt:'harness message_end; not a network acknowledgement'});delete t.args;delete t.expected;}}
 inspect(t:any){
  if(t.name==='read')return {tool_call_id:t.id,status:'read_only',automatic:true};
  if(t.executed===false)return {tool_call_id:t.id,status:'not_executed',automatic:true};
  if(t.ended&&!t.resultError)return {tool_call_id:t.id,status:'result_available',automatic:true};
  if(!t.path||!t.expected||this.localPath(t.args.path)!==t.path)return {tool_call_id:t.id,status:'ambiguous_or_sensitive',automatic:false};
  try{
   const s=lstatSync(t.path);if(!s.isFile()||s.isSymbolicLink()||s.nlink!==1||s.size>Math.max(8388608,t.expected.length))return {tool_call_id:t.id,status:'ambiguous',automatic:false};
   const current=readFileSync(t.path);
   if(current.equals(t.expected))return {tool_call_id:t.id,status:'committed',automatic:true};
   if(t.before?.exists===false&&t.started&&current.length<t.expected.length&&t.expected.subarray(0,current.length).equals(current))return {tool_call_id:t.id,status:'partial',automatic:true};
   return {tool_call_id:t.id,status:'ambiguous',automatic:false};
  }catch(e:any){return {tool_call_id:t.id,status:e.code==='ENOENT'?'not_committed':'uninspectable',automatic:e.code==='ENOENT'};}
 }
 decision(){
  const r=this.request;if(this.cancelled)return {action:'none',reason:'user_cancelled'};
  if(!r?.signal||!TERMINAL_SIGNALS.has(r.signal))return {action:'none',reason:'no_terminal_failure'};
  if(!this.alive)return {action:'hold',reason:'acp_disconnected',inspection:[...this.tools.values()].filter(t=>!t.delivered).map(t=>this.inspect(t))};
  const tools=[...this.tools.values()].filter(t=>!t.delivered);
  // A failed provider does not prove a concurrently dispatched tool has died.
  if(tools.some(t=>t.started&&!t.ended)&&!['tool_process_failure','tool_channel_closed'].includes(r.signal))return {action:'defer',reason:'tool_still_running'};
  const inspection=tools.map(t=>this.inspect(t));
  return inspection.some(x=>!x.automatic)?{action:'hold',reason:'genuinely_ambiguous_side_effects',inspection}:{action:'resume',reason:'terminal_failure_with_determinable_state',inspection};
 }
 disconnect(){
  this.alive=false;
  const active=this.request&&['MODEL_STREAMING','TOOL_CALL_STREAMING','INTERRUPTED'].includes(this.request.state);
  if(active||[...this.tools.values()].some(t=>!t.delivered))this.terminal(this.cancelled?'USER_CANCELLED':'ACP_TRANSPORT_INTERRUPTED',{...this.request?.wire,terminal_signal:this.cancelled?null:'acp_disconnect',connection_state:'closed'});
 }
}
