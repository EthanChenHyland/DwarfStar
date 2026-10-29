// Backward-compatible scalar status plus DwarfStar-only infrastructure hooks.
import {installInfrastructure} from './infrastructure.ts';
import {infrastructureSettings,activeProfile} from './lifecycle.ts';
import {installEfficiency} from './efficiency.ts';
import {readFileSync,writeFileSync,renameSync} from 'node:fs';
import {join} from 'node:path';
const ROOT='$LOCAL_AI_SETUP/challengers/dwarfstar-paperniuk';
export default function installTelemetry(pi:any,options:any={}){
 const timing=installInfrastructure(pi,options.infrastructure??{});
 installEfficiency(pi,timing,{settings:options.infrastructure?.settings,...options.efficiency});
 const settings=infrastructureSettings(options.infrastructure?.settings);
 if(!settings.telemetry_enabled)return;
 const env=options.env??process.env;
 const clock=options.clock??(()=>performance.now()/1000);
 const wall=options.wall??(()=>Date.now()/1000);
 const ledger=options.ledger??(()=>JSON.parse(readFileSync(ROOT+'/state/server.json','utf8')));
 const publish=options.publish??((value:any)=>{const file=env.DWARFSTAR_REQUEST_FILE;if(!file)return;const temp=file+'.tmp';writeFileSync(temp,JSON.stringify(value)+'\n',{mode:0o600});renameSync(temp,file);});
 const profile=activeProfile().name;
 let current:any=null,last:any=null,timer:any,ctxTimer:any,sequence=0;
 const toolNames=new Map<string,string>();
 const snapshot=()=>{try{
   const value=current?{...current,elapsed_s:clock()-current.clock}:last??{active:false,phase:'IDLE',profile};
   const {clock:unused,first,...safe}=value;
   let vision:any;try{const state=JSON.parse(readFileSync(join(env.PI_CODING_AGENT_DIR??'', 'vision-kimicu.json'),'utf8'));vision={kimicu_connected:state.connected,tool_count:state.tool_count,image_session:state.image_session,image_request_count:state.image_request_count,kv_save_supported:state.kv_save_supported,save_note:state.image_session_save};}catch{}
   const published=publish({...safe,...(vision?{vision}:{}),session_timing:timing.summary(),client_pid:process.pid,client_birth:env.DWARFSTAR_CLIENT_BIRTH,observed_at:wall()});
   if(published?.catch)published.catch(()=>{});
 }catch{/* A telemetry sink must never fail a provider/tool operation. */}};
 const ours=(ctx:any)=>ctx?.model?.provider==='dwarfstar-local';
 const finish=(outcome:string,usage?:any)=>{
   if(!current)return;
   // OMP normalizes prompt usage into input + cacheRead + cacheWrite.
   if(Number.isFinite(usage?.input)&&usage.input+Number(usage.cacheRead??0)+Number(usage.cacheWrite??0)>0){
     current.input_tokens=usage.input+Number(usage.cacheRead??0)+Number(usage.cacheWrite??0);
     current.reused_tokens=Number(usage.cacheRead??0);current.new_prefill_tokens=current.input_tokens-current.reused_tokens;
     if(Number.isFinite(usage.output)&&usage.output>=0)current.output_tokens=usage.output;
     current.token_source='native API usage via OMP';
   }
   const elapsed=clock()-current.clock,decode=current.first===null?null:clock()-current.first;
   last={...current,active:false,phase:['error','aborted','shutdown'].includes(outcome)?'ERROR':'COMPLETE',outcome,ended_at:wall(),elapsed_s:elapsed,
     provider_decode_tps:decode!==null&&decode>0&&current.output_tokens!=null?current.output_tokens/decode:null,
     provider_rate_source:'derived: API completion tokens / first-event-to-finish duration',clock:undefined,first:undefined};
   current=null;snapshot();
 };
 const on=(name:string,handler:any)=>pi.on(name,(event:any,ctx:any)=>{const failed=()=>{if(current)current.telemetry_error='Collection error';snapshot();};try{const value=handler(event,ctx);return value?.catch?value.catch(failed):value;}catch{failed();}});
 const toolName=(name:any)=>typeof name==='string'&&/^[a-zA-Z0-9_.:/-]{1,64}$/.test(name)?name:undefined;
 on('session_start',(_e:any,ctx:any)=>{ctxTimer=ctx;timer=ctx.setInterval(snapshot,1000);});
 on('before_provider_request',(e:any,ctx:any)=>{if(!ours(ctx))return;
   const row=ledger(),limit=e.payload?.max_completion_tokens??e.payload?.max_tokens;
   last=null;toolNames.clear();current={active:true,phase:'PREFILL / WAITING_FIRST_TOKEN',phase_source:'provider submission / stream events',profile,request_sequence:++sequence,server_pid:row.pid,server_birth:row.birth,started_at:wall(),clock:clock(),first:null,ttft_s:null,output_tokens:null,requested_output:Number.isInteger(limit)?limit:null};snapshot();
 });
 on('message_update',(e:any,ctx:any)=>{if(!ours(ctx)||!current)return;const event=e.assistantMessageEvent;if(!event)return;const phaseBefore=current.phase,tokensBefore=current.output_tokens,toolBefore=current.active_tool_name;
   const phases:any={thinking_delta:'REASONING',text_delta:'OUTPUT',toolcall_start:'TOOL_CALL',toolcall_delta:'TOOL_CALL',toolcall_end:'TOOL_CALL'};
   if(phases[event.type]&&(event.delta?.length||event.type==='toolcall_start'||event.type==='toolcall_end')){
     if(current.first===null){current.first=clock();current.ttft_s=current.first-current.clock;}
     current.phase=phases[event.type];
     if(current.phase==='TOOL_CALL')current.active_tool_name=toolName(event.partial?.content?.[event.contentIndex]?.name);else current.active_tool_name=undefined;
   }
   if(Number.isFinite(event.partial?.usage?.output)&&event.partial.usage.output>0)current.output_tokens=event.partial.usage.output;
   if(event.type==='error'||event.type==='done')finish(event.type==='error'?'error':event.reason??'stop',event.message?.usage);else if(current.phase!==phaseBefore||current.output_tokens!==tokensBefore||current.active_tool_name!==toolBefore)snapshot();
 });
 on('message_end',(e:any,ctx:any)=>{if(ours(ctx)&&e.message?.role==='assistant')finish(e.message.stopReason??'stop',e.message.usage);});
 on('tool_execution_start',(e:any,ctx:any)=>{if(!ours(ctx))return;toolNames.set(e.toolCallId,toolName(e.toolName)??'Tool');if(last){last={...last,phase:'TOOL_CALL',tool_execution_active:true,active_tool_name:[...toolNames.values()].join(', ')};snapshot();}});
 on('tool_execution_end',(e:any,ctx:any)=>{if(!ours(ctx))return;toolNames.delete(e.toolCallId);if(last){last={...last,phase:e.isError?'ERROR':toolNames.size?'TOOL_CALL':'COMPLETE',tool_execution_active:toolNames.size>0,active_tool_name:toolNames.size?[...toolNames.values()].join(', '):undefined};snapshot();}});
 on('agent_end',()=>{if(current)finish('aborted');});
 on('session_shutdown',()=>{finish('shutdown');toolNames.clear();if(last){last.tool_execution_active=false;last.active_tool_name=undefined;snapshot();}if(timer)ctxTimer.clearTimer(timer);});
 if(!options.noExitHook)process.once('exit',()=>{try{finish('shutdown');}catch{}});
}
