// Request-local transport facts; optional telemetry never decides execution.
import {streamOpenAICompletions} from '$HOME/.bun/install/global/node_modules/@oh-my-pi/pi-ai/src/providers/register-builtins.ts';
import {AssistantMessageEventStream} from '$HOME/.bun/install/global/node_modules/@oh-my-pi/pi-ai/src/utils/event-stream.ts';
import {classify} from './latency.ts';
import {observe} from './lifecycle.ts';
import {getOpenAIStreamIdleTimeoutMs,getOpenAIStreamFirstEventTimeoutMs} from '$HOME/.bun/install/global/node_modules/@oh-my-pi/pi-ai/src/utils/idle-iterator.ts';
export const API='dwarfstar-infrastructure-completions';
export class Wire {
 buffer='';dataLines:string[]=[];decoder=new TextDecoder();calls=new Map<any,any>();finish:any=null;done=false;failure:any=null;normal=false;bytes=0;status:any=null;usage_observed=false;
 eof_received=false;consumer_cancelled=false;protocol_error=false;terminal_signal:any=null;connection_state='open';transport_exception:any=null;
 timeout_watchdog:any=null;
 line(line:string){if(line===''){if(this.dataLines.length)this.data(this.dataLines.join('\n'));this.dataLines=[];}else if(line.startsWith('data:'))this.dataLines.push(line.slice(5).replace(/^ /,''));}
 data(raw:string){
  raw=raw.trim();if(!raw)return;if(raw==='[DONE]'){this.done=true;this.normal=true;return;}
  let v:any;try{v=JSON.parse(raw);}catch{this.protocol_error=true;return;}
  if(v.usage)this.usage_observed=true;
  for(const c of v.choices??[]){if(c.finish_reason){this.finish=c.finish_reason;this.normal=true;}
   for(const tc of c.delta?.tool_calls??[]){const key=tc.index??tc.id??0;let x=this.calls.get(key);if(!x){x={id:tc.id??null,name:null,args:'',bytes:0};this.calls.set(key,x);}x.id??=tc.id;x.name=tc.function?.name??x.name;const a=tc.function?.arguments;if(typeof a==='string'){x.args+=a;x.bytes+=Buffer.byteLength(a);}else if(a&&typeof a==='object'){x.args=JSON.stringify(a);x.bytes=Buffer.byteLength(x.args);}}
  }
 }
 feed(chunk:Uint8Array){this.bytes+=chunk.byteLength;this.buffer+=this.decoder.decode(chunk,{stream:true});let i;while((i=this.buffer.indexOf('\n'))>=0){this.line(this.buffer.slice(0,i).replace(/\r$/,''));this.buffer=this.buffer.slice(i+1);}}
 close(){
  this.eof_received=true;this.connection_state='closed';this.buffer+=this.decoder.decode();if(this.buffer)this.line(this.buffer);if(this.dataLines.length){this.data(this.dataLines.join('\n'));this.dataLines=[];}this.buffer='';this.finalize();
 }
 fail(kind:string,signal:string,error?:any){this.failure=kind;this.terminal_signal=signal;this.connection_state='failed';this.transport_exception=error?{name:error.name??'Error',code:typeof error.code==='string'&&/^[A-Z0-9_]{1,64}$/.test(error.code)?error.code:null}:null;}
 finalize(){
  if(this.failure)return;
  if(this.finish==='network_error')this.fail('MODEL_STREAM_INTERRUPTED','transport_exception');
  else if(['error','insufficient_system_resource'].includes(this.finish))this.fail('RUNTIME_PROCESS_ERROR','runtime_process_failure');
  else if(!this.normal)this.fail('MODEL_STREAM_INTERRUPTED',this.protocol_error?'provider_protocol_error':'unexpected_eof');
  else if(this.finish!=='length'&&[...this.calls.values()].some(x=>!this.complete(x)))this.failure='TOOL_SCHEMA_ERROR';
 }
 complete(x:any){try{const a=JSON.parse(x.args);return !!x.name&&!!x.id&&a!==null&&typeof a==='object'&&!Array.isArray(a);}catch{return false;}}
 safe(){const partial=[...this.calls.values()].some(x=>!this.complete(x));return {stream_ended_normally:this.normal,finish:this.finish,done_sentinel:this.done,failure:this.failure,http_status:this.status,usage_observed:this.usage_observed,eof_received:this.eof_received,consumer_cancelled:this.consumer_cancelled,terminal_signal:this.terminal_signal,transport_exception:this.transport_exception,timeout_watchdog:this.timeout_watchdog,connection_state:this.connection_state,parser_state:partial?(this.connection_state==='open'?'tool_arguments_streaming':'terminal_incomplete_arguments'):this.normal?'terminal_envelope':'streaming_envelope',tool_calls:[...this.calls.values()].map(x=>({id:x.id,name:typeof x.name==='string'&&/^[\w./:-]{1,64}$/.test(x.name)?x.name:null,structurally_complete:this.complete(x),argument_bytes:x.bytes,execution_began:false}))};}
}
const HOLD='DwarfStar infrastructure hold; details are in the local scalar journal.';
const INTERRUPTED='DwarfStar request interrupted; bounded recovery will inspect current state.';
export function guardedStream(model:any,context:any,options:any={},latency:any,deps:any={}){
 const out=new AssistantMessageEventStream(),wire=new Wire(),originalFetch=options.fetch??deps.fetch??globalThis.fetch;
 const recoveryEnabled=deps.recoveryEnabled!==false,core=deps.lifecycle,request=core?.begin();
 const record:any=observe(()=>latency.start(),{start:performance.now()/1000});observe(()=>latency.nextRequest(record));
 const event=(type:string,data:any={})=>observe(()=>latency.event(type,{...core?.facts(request),...data}));
 const controlledFetch=async(input:any,init:any)=>{
  let response:Response;
  try{response=await originalFetch(input,init);}catch(error){if(options.signal?.aborted)throw error;wire.fail('MODEL_STREAM_INTERRUPTED','transport_exception',error);if(recoveryEnabled)return holdResponse();throw error;}
  record.headers=observe(()=>latency.clock(),performance.now()/1000);wire.status=response.status;event('response_headers',{status:response.status,request_to_headers_s:record.headers-record.start});
  if(response.status>=500){wire.fail('RUNTIME_PROCESS_ERROR','runtime_process_failure');if(recoveryEnabled){observe(()=>response.body?.cancel());return holdResponse();}}
  if(!response.ok||!response.body||!response.headers.get('content-type')?.includes('text/event-stream'))return response;
  const reader=response.body.getReader();let cancelled=false,closed=false;
  const body=new ReadableStream({async pull(controller){
   if(cancelled||closed)return;
   let item:ReadableStreamReadResult<Uint8Array>;
   try{item=await reader.read();}catch(error){
    if(cancelled||closed||wire.normal){event('stream_cleanup_exception_ignored',{...wire.safe(),condition:'consumer cleanup after normal completion'});if(!cancelled&&!closed){closed=true;controller.close();}return;}
    wire.fail('MODEL_STREAM_INTERRUPTED','transport_exception',error);closed=true;controller.error(error);return;
   }
   // reader.cancel() resolves an outstanding read with done:true. This is NOT EOF.
   if(cancelled||closed)return;
   if(item.done){closed=true;wire.close();observe(()=>core?.streamItem(wire.safe()));if(wire.failure&&recoveryEnabled)controller.error(new Error(HOLD));else controller.close();}
   else {
    // Observation is fail-open; it never suppresses, rewrites or delays a chunk.
    observe(()=>wire.feed(item.value));
    // Never repeatedly JSON.parse a growing argument string merely for telemetry.
    observe(()=>core?.streamItem({parser_state:wire.calls.size?'tool_arguments_streaming':'streaming_envelope',tool_calls:wire.calls.size?[{}]:[]}));
    controller.enqueue(item.value);
   }
  },async cancel(reason){
   cancelled=true;wire.consumer_cancelled=true;wire.connection_state=wire.normal?'complete':'consumer_closed';
   event('stream_consumer_closed',{condition:wire.normal?'normal provider terminal cleanup':'provider/user consumer closed',...wire.safe()});
   try{await reader.cancel(reason);}catch(error){
    if(wire.normal||options.signal?.aborted){event('stream_cleanup_exception_ignored',{...wire.safe(),condition:'terminal consumer cancellation cleanup'});return;}
    wire.fail('MODEL_STREAM_INTERRUPTED','transport_exception',error);throw error;
   }
  }});
  return new Response(body,{status:response.status,statusText:response.statusText,headers:response.headers});
 };
 const originalModel={...model,api:'openai-completions',compat:undefined,compatConfig:model.compatConfig??model.compat};
 const originalContext={...context,messages:context.messages.map((m:any)=>m.role==='assistant'&&m.api===API&&m.provider===model.provider&&m.model===model.id?{...m,api:'openai-completions'}:m)};
 const settle=(m:any)=>{
  if(options.signal?.aborted||m.stopReason==='aborted'){record.classification='USER_CANCELLED';}
  else {
   if(m.stopReason!=='error'&&!wire.failure)wire.finalize();
   if(m.stopReason==='error'){
    const text=String(m.errorMessage??'');
    if(!wire.normal&&/timed? ?out|deadline expired|OpenAI completions stream stalled while waiting for the next event/i.test(text)){
     wire.fail('MODEL_STREAM_INTERRUPTED','deadline_expired');
     const compat=model.compatConfig??model.compat??{},idle=options.streamIdleTimeoutMs??getOpenAIStreamIdleTimeoutMs(compat.streamIdleTimeoutMs),first=options.streamFirstEventTimeoutMs??getOpenAIStreamFirstEventTimeoutMs(idle,compat.streamFirstEventTimeoutMs);
     wire.timeout_watchdog={source:'native provider terminal timeout error',name:/first event/i.test(text)?'native_first_event':'native_idle_or_request_deadline',duration_ms:/first event/i.test(text)?first:idle};
    }
    else if(!wire.failure&&/closed before a finish_reason|unexpected eof|stream.*closed before/i.test(text))wire.fail('MODEL_STREAM_INTERRUPTED','unexpected_eof');
    else if(!wire.failure&&/ECONNRESET|ECONNREFUSED|socket|fetch failed|connection (reset|closed)|network error/i.test(text))wire.fail('MODEL_STREAM_INTERRUPTED','transport_exception');
    else if(!wire.failure&&wire.protocol_error)wire.fail('MODEL_STREAM_INTERRUPTED','provider_protocol_error');
   }
   record.classification=classify(m,wire.safe(),false);
  }
  record.status=record.classification;record.wire=wire.safe();
  // Store complete proposed actions for out-of-band inspection only, never replay.
  for(const b of m.content??[])if(b.type==='toolCall'){
   const raw=[...wire.calls.values()].find(x=>x.id===b.id);
   // An unfinished proposal was never dispatchable: it has no uncertain side effect.
   if(!raw||wire.complete(raw))observe(()=>core?.prepare(b.id,b.name,b.arguments));
  }
  observe(()=>core?.terminal(record.classification,record.wire,request));
  event('provider_terminal',{classification:record.classification,...wire.safe()});
  return record.classification;
 };
 void(async()=>{try{
  if(options.signal?.aborted){const m=empty(model,'aborted');settle(m);out.push({type:'error',reason:'aborted',error:m});out.end();return;}
  const tc=options.toolChoice;const toolChoice=tc==='any'?'required':typeof tc==='string'?tc:tc?.type==='tool'?{type:'function',function:{name:tc.name}}:tc?.type==='function'?{type:'function',function:{name:tc.function?.name??tc.name}}:undefined;
  const native=(deps.stream??streamOpenAICompletions)(originalModel,originalContext,{...options,maxTokens:options.maxTokens??model.maxTokens,maxTokensExplicit:options.maxTokens!==undefined,reasoning:model.reasoning&&model.thinking?options.reasoning:undefined,disableReasoning:options.disableReasoning||options.forceReasoningOff,toolChoice,fetch:controlledFetch});let terminal=false;
  for await(const e of native){
   if(e.type!=='done'&&e.type!=='error'){out.push(e);continue;}
   terminal=true;const m=e.type==='done'?e.message:e.error,kind=settle(m);
   if(recoveryEnabled&&['MODEL_STREAM_INTERRUPTED','TOOL_SCHEMA_ERROR','RUNTIME_PROCESS_ERROR'].includes(kind)){
    const raw=[...wire.calls.values()];let index=0;const content=m.content.filter((b:any)=>b.type!=='toolCall'||(raw[index]?wire.complete(raw[index++]):true));
    const message=kind==='RUNTIME_PROCESS_ERROR'?HOLD:kind==='TOOL_SCHEMA_ERROR'?'DwarfStar tool arguments were incomplete at terminal completion.':INTERRUPTED;
    event(kind==='RUNTIME_PROCESS_ERROR'?'infrastructure_hold':kind==='TOOL_SCHEMA_ERROR'?'tool_schema_error':'terminal_interruption',{condition:kind==='RUNTIME_PROCESS_ERROR'?'explicit runtime failure':'provider terminal failure',classification:kind,...wire.safe()});
    out.push({type:'error',reason:'error',error:{...m,api:model.api,content,stopReason:'error',errorId:0,errorStatus:undefined,errorClassificationMessage:message,errorMessage:message}});
   }else out.push(e.type==='done'?{...e,message:{...m,api:model.api}}:{...e,error:{...m,api:model.api}});
   break;
  }
  if(!terminal){if(!options.signal?.aborted)wire.fail('MODEL_STREAM_INTERRUPTED','unexpected_eof');const m=empty(model,options.signal?.aborted?'aborted':'error');settle(m);out.push({type:'error',reason:m.stopReason,error:m});}
  out.end();
 }catch(error){
  if(!options.signal?.aborted&&!wire.normal&&!wire.failure)wire.fail('MODEL_STREAM_INTERRUPTED','transport_exception',error);
  const m=empty(model,options.signal?.aborted?'aborted':'error');settle(m);out.push({type:'error',reason:m.stopReason,error:m});out.end();
 }})();return out;
}
function empty(model:any,stopReason:string){return {role:'assistant',api:model.api,provider:model.provider,model:model.id,content:[],usage:{input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}},stopReason,errorMessage:INTERRUPTED,errorClassificationMessage:INTERRUPTED,errorId:0,timestamp:Date.now()};}
function holdResponse(){return new Response(JSON.stringify({error:{message:HOLD,type:'dwarfstar_infrastructure_hold'}}),{status:400,headers:{'content-type':'application/json'}});}
