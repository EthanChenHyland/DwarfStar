// Passive task projection. Callers isolate every invocation; no lifecycle writes.
import {appendFileSync} from 'node:fs';
import {browserCommand,contentBytes} from './result-hygiene.ts';
import {configSnapshot,hash,latestNative,apiCacheMetrics,NE,ROOT} from './benchmark-snapshot.ts';
import {HOME} from './paths.ts';
const iso=(n:number)=>new Date(n*1000).toISOString();
const parse=(s:any)=>{try{return JSON.parse(s);}catch{return null;}};
const texts=(c:any)=>typeof c==='string'?[c]:(c??[]).filter((b:any)=>b.type==='text').map((b:any)=>b.text);
export const CATEGORIES=['shell_command','test_output','build_output','package_install','browser_console','browser_network','DOM','file_read','structured_data','other'];
const empty=()=>({result_count:0,original_raw_bytes:0,model_visible_bytes:0,bytes_reduced:0,reduction_percent:0,shaped_results:0,unshaped_results:0,failures_preserved:0,raw_expansion_requests:0});
function accumulate(s:any,raw:number,visible:number,shaped:boolean,failed:boolean,expanded:boolean){
 s.result_count++;s.original_raw_bytes+=raw;s.model_visible_bytes+=visible;s.bytes_reduced=s.original_raw_bytes-s.model_visible_bytes;s.reduction_percent=s.original_raw_bytes?100*s.bytes_reduced/s.original_raw_bytes:0;
 if(shaped)s.shaped_results++;else s.unshaped_results++;if(failed&&!shaped)s.failures_preserved++;if(expanded)s.raw_expansion_requests++;
}
function browserComponents(content:any){
 const r=parse(parse(content?.length===1&&content[0].type==='text'?content[0].text:null)?.output);if(r?.engine!=='playwright')return null;
 const ev=r.events??{};
 return {browser_console:{console:ev.console??[],pageErrors:ev.pageErrors??[],...(ev.console_summary?{summary:ev.console_summary}:{})},browser_network:{responses:ev.responses??[],failedRequests:ev.failedRequests??[],...(ev.response_summary?{summary:ev.response_summary}:{})},DOM:(r.results??[]).filter((x:any)=>x.op==='dom')};
}
function browserReceipt(e:any){
 if(e.toolName!=='bash'||!browserCommand(String(e.input?.command??''))||e.details?.__synthetic||e.details?.executed===false)return null;
 const out=parse(e.content?.length===1&&e.content[0].type==='text'?e.content[0].text:null),r=parse(out?.output);
 const v=r?.validation??e.details?.dwarfstar_browser_validation;
 return r?.engine==='playwright'&&v?.source==='dwarfstar-browser-validation-v1'?{out,r,v}:null;
}
export function resultCategory(e:any,rule=''){
 const cmd=String(e.input?.command??''),r=browserReceipt(e);
 if(e.toolName==='read')return 'file_read';
 if(r){
  if(r.r.results?.some((s:any)=>s.op==='dom'))return 'DOM';
  const ev=r.r.events??{},a=Buffer.byteLength(JSON.stringify(ev.console??[])),b=Buffer.byteLength(JSON.stringify([ev.responses??[],ev.failedRequests??[]]));
  return b>a?'browser_network':'browser_console';
 }
 if(rule==='package_install'||/^\s*(?:npm\s+(?:install|ci|add)|pnpm\s+(?:install|add)|bun\s+(?:install|add))\b/.test(cmd))return 'package_install';
 if(rule==='test_success'||/^(?:npm|pnpm|bun)\s+(?:run\s+)?test\b|^(?:node\s+--test|pytest|(?:npx\s+)?(?:jest|vitest))\b|^python[\d.]*\s+-m\s+(?:pytest|unittest)\b/.test(cmd.trim()))return 'test_output';
 if(rule==='build_success'||/^(?:(?:npm|pnpm|bun)\s+(?:run\s+)?(?:build|check|typecheck)|(?:npx\s+)?(?:tsc|vite\s+build)|make\s+(?:all|build))\b/.test(cmd.trim()))return 'build_output';
 if(e.toolName==='bash')return 'shell_command';
 if(e.content?.some((b:any)=>b.type==='text'&&parse(b.text)!==null))return 'structured_data';
 return 'other';
}
// Diagnostic mirror only. The existing evaluator remains the ONLY nudge decision.
export function nudgeConditions(n:any,core:any,turn:any,enabled:boolean,pending:boolean){
 return {feature_enabled:enabled,no_pending_human:!pending,browser_product:n.product,not_exhaustive_or_refactor:!n.excluded,not_sent:!n.sent,not_attempted:!n.attempted,no_explicit_missing_requirement:!n.missing,no_failed_commands:n.failedCommands.size===0,no_active_tools:n.active.size===0,source_revision:n.revision>0,source_bytes_at_least_8192:n.sourceBytes>=8192,connection_alive:!!core?.alive,not_cancelled:!core?.cancelled,no_terminal_signal:!core?.request?.signal,normal_complete:core?.request?.status==='NORMAL_COMPLETE',tool_use_turn:turn?.message?.stopReason==='toolUse',tool_results_present:!!turn?.toolResults?.length,two_validation_receipts:n.validations.length>=2,two_provider_passes:new Set(n.validations.map((x:any)=>x.request)).size>=2,one_entrypoint:new Set(n.validations.map((x:any)=>x.entrypoint)).size===1};
}
export class BenchmarkObserver {
 first:any=null;regressions:any[]=[];categories:any=Object.fromEntries(CATEGORIES.map(k=>[k,empty()]));
 browserComponents:any=Object.fromEntries(['browser_console','browser_network','DOM'].map(k=>[k,empty()]));
 lastTool:any=null;nudge:any={eligible:false,first_eligible_timestamp:null,fired:false,suppressed:false,suppression_reasons:[],unsatisfied_before_eligibility:[],first_model_action_after:null};
 config:any=NE;configError=false;requestVariants=new Map<string,any>();capabilityHashes=new Set<string>();toolHashes=new Set<string>();systemPromptHashes=new Set<string>();saved=false;reserved=false;lastConditions='';requestVariantOverflow=false;
 constructor(public task:any,public flags:any,public options:any={}){try{this.config=(options.snapshot??configSnapshot)(flags,options.root??ROOT);}catch{this.configError=true;}}
 request(payload:any){
  if(!payload)return;
  // Never retain task messages, tool arguments/results, prompts, or credentials.
  const effective=Object.fromEntries(['model','max_tokens','max_completion_tokens','reasoning_effort','enable_thinking','chat_template_kwargs','temperature','top_p','top_k','min_p','presence_penalty','frequency_penalty'].filter(k=>payload[k]!==undefined).map(k=>[k,payload[k]]));
  const fingerprint=hash(effective);if(this.requestVariants.has(fingerprint))this.requestVariants.get(fingerprint).count++;else if(this.requestVariants.size<32)this.requestVariants.set(fingerprint,{sha256:fingerprint,count:1,values:JSON.parse(JSON.stringify(effective))});else this.requestVariantOverflow=true;
  if(payload.tools)this.toolHashes.add(hash(payload.tools));
  this.systemPromptHashes.add(hash((payload.messages??[]).filter((m:any)=>m.role==='system'||m.role==='developer').map((m:any)=>m.content)));
  for(const m of payload.messages??[])for(const s of texts(m.content)){
   if(typeof s!=='string'||!s.includes('"environment"')||!s.includes('"agent_tools"'))continue;
   const p=parse(s);if(p?.environment?.agent_tools===HOME+'/agent-tools'&&typeof p.usage==='string')this.capabilityHashes.add(hash(s));
  }
 }
 result(e:any,shaped:any,requestId:any,requestNumber:any,now:number){
  if(e.details?.__synthetic||e.details?.executed===false)return;
  this.lastTool={tool_call_id:e.toolCallId??NE,tool_name:e.toolName,request_id:requestId??NE,observed_at:iso(now)};
  const category=resultCategory(e,shaped?.meta?.rule),s=this.categories[category],raw=contentBytes(e.content??[]),visible=shaped?.result?contentBytes(shaped.result.content??e.content):raw;
  const outer=parse(e.content?.[0]?.text);
  const failed=!!(e.isError||outer?.exit_code!==undefined&&outer.exit_code!==0||/\b(?:FAIL\s|AssertionError|Traceback|error:|failed\s*[:=]\s*[1-9]|[1-9]\d*\s+(?:failed|failures))/i.test(outer?.output??''));
  const expanded=!!(browserCommand(String(e.input?.command??''))?.full||e.toolName==='read'&&String(e.input?.path??'').split('/').includes('.dwarfstar-tooling-logs'));
  accumulate(s,raw,visible,!!shaped?.result,failed,expanded);
  const receipt=browserReceipt(e);if(!receipt)return;
  const before=browserComponents(e.content),after=browserComponents(shaped?.result?.content??e.content);
  if(before&&after)for(const key of Object.keys(this.browserComponents)){
   const a=JSON.stringify(before[key]),b=JSON.stringify(after[key]);
   accumulate(this.browserComponents[key],Buffer.byteLength(a),Buffer.byteLength(b),a!==b,failed,expanded);
  }
  const {out,r,v}=receipt,ended=Date.parse(r.ended_at)/1000;
  if(!Number.isFinite(ended)||ended<Math.max(this.task.wall,now-120)||ended>now+5||v.local!==true)return;
  const good=!e.isError&&out.exit_code===0&&r.ok===true&&v.entrypoint_exists===true&&v.primary_ui_visible===true&&v.primary_interaction_exercised===true&&v.functional_assertions>=1&&v.negative_dom_observations===0&&v.blocking_runtime_error===false&&v.evidence_complete===true;
  const evidence={timestamp:iso(ended),observed_at:iso(now),elapsed_wall_s:Math.max(0,ended-this.task.wall),tool_call_id:e.toolCallId??NE,request_id:requestId??NE,provider_pass:requestNumber,entrypoint:v.entrypoint,blocking_errors_absent:v.blocking_runtime_error===false,signals:{entrypoint_exists:v.entrypoint_exists,primary_ui_visible:v.primary_ui_visible,primary_interaction_exercised:v.primary_interaction_exercised,functional_assertions:v.functional_assertions,negative_dom_observations:v.negative_dom_observations,evidence_complete:v.evidence_complete,blocking_runtime_error:v.blocking_runtime_error}};
  if(good){if(!this.first){this.first=evidence;this.options.event?.('first_functional_state',evidence);}else if(this.first.entrypoint===v.entrypoint){const last=this.regressions.at(-1);if(last&&!last.recovered_functional){last.recovered_functional=evidence;this.options.event?.('recovered_functional_state',evidence);}}}
  // Absence/incomplete telemetry is not a demonstrated regression. Require a
  // concrete failed observation on the SAME entrypoint after it first worked.
  else if(this.first&&this.first.entrypoint===v.entrypoint&&ended>=Date.parse(this.first.timestamp)/1000&&(v.blocking_runtime_error===true||v.entrypoint_exists===false||v.primary_ui_visible===false||v.negative_dom_observations>0||v.evidence_complete===true&&r.ok===false&&v.primary_interaction_exercised===false)){
   if(!this.regressions.at(-1)||this.regressions.at(-1).recovered_functional){this.regressions.push({detected:evidence,recovered_functional:null});this.options.event?.('functional_state_regression',evidence);}
  }
 }
 eligibility(n:any,core:any,turn:any,now:number,pending:boolean){
  const conditions=nudgeConditions(n,core,turn,this.flags.finish_nudge_enabled===true,pending),unsatisfied=Object.keys(conditions).filter(k=>!conditions[k]);
  this.nudge.last_conditions=conditions;
  this.nudge.eligible=false;this.nudge.suppressed=unsatisfied.length>0;this.nudge.suppression_reasons=unsatisfied;
  if(!this.nudge.first_eligible_timestamp)for(const key of unsatisfied)if(!this.nudge.unsatisfied_before_eligibility.includes(key))this.nudge.unsatisfied_before_eligibility.push(key);
  const key=JSON.stringify(unsatisfied);if(key!==this.lastConditions){this.lastConditions=key;this.options.event?.('finish_nudge_conditions',{conditions,unsatisfied,observed_at:iso(now)});}
 }
 evaluated(eligible:boolean,core:any,now:number){
  this.nudge.eligible=eligible;
  if(eligible&&!this.nudge.first_eligible_timestamp){this.nudge.first_eligible_timestamp=iso(now);this.nudge.trigger_conditions={...this.nudge.last_conditions};this.nudge.immediately_before={request_id:core?.request?.id??NE,tool:this.lastTool};}
 }
 deliveryFailed(){this.nudge.suppressed=true;this.nudge.suppression_reasons=['delivery_exception_reserved_no_retry'];}
 sent(n:any,now:number){if(n.sent){this.nudge.fired=true;this.nudge.actual_timestamp=iso(n.sentAt??now);this.nudge.suppressed=false;this.nudge.suppression_reasons=[];}}
 action(message:any,requestId:any,now:number){
  if(!this.nudge.fired||this.nudge.first_model_action_after||message?.role!=='assistant')return;
  const part=message.content?.find((b:any)=>b.type==='toolCall'||b.type==='text'&&b.text?.trim());
  if(part)this.nudge.first_model_action_after={timestamp:iso(now),timestamp_basis:'Completed assistant message receipt, not action start',request_id:requestId??NE,type:part.type,tool_name:part.type==='toolCall'?part.name:undefined,tool_call_id:part.type==='toolCall'?part.id:undefined};
 }
 summary(n:any,wall:number,outcome:string,requests:any[]=[]){
  const first=this.first?Date.parse(this.first.timestamp)/1000:null,sent=n.sentAt,final=outcome==='completed_turn'?wall:null;
  const ranked=Object.entries(this.categories).filter(([,s]:any)=>s.result_count>0).sort((a:any,b:any)=>b[1].bytes_reduced-a[1].bytes_reduced);
  return {benchmark_observability_schema:1,observation_scope:this.task.restored?'Current ACP segment; earlier functional evidence not reconstructed':'Current task in this ACP connection',
   first_functional_state:this.first?{...this.first,later_invalidated:this.regressions.length>0}:null,first_functional_timestamp:this.first?.timestamp??null,first_functional_elapsed_s:this.first?.elapsed_wall_s??null,regressions_after_first_functional:this.regressions,
   final_completion_elapsed_s:final===null?null:Math.max(0,final-this.task.wall),functional_to_final_tail_s:first===null||final===null?null:Math.max(0,final-first),
   finish_nudge_elapsed_s:sent===null?null:Math.max(0,sent-this.task.wall),first_functional_to_nudge_s:first===null||sent===null?null:Math.max(0,sent-first),nudge_to_completion_s:sent===null?null:Math.max(0,wall-sent),
   total_tool_result_bytes_reduced:(this.task.originalBytes??Object.values(this.categories).reduce((n:number,s:any)=>n+s.original_raw_bytes,0))-(this.task.visibleBytes??Object.values(this.categories).reduce((n:number,s:any)=>n+s.model_visible_bytes,0)),
   finish_nudge_trigger:{...this.nudge,fired:n.sent,actual_timestamp:n.sentAt===null?null:iso(n.sentAt),history_restored:!!this.task.restored},tool_result_categories:this.categories,browser_result_components:this.browserComponents,top_tool_category_by_bytes_reduced:ranked.length?(ranked[0] as any)[0]:null,
   category_accounting:'Exclusive whole-result categories. Browser precedence: DOM, otherwise larger console/network JSON component; ties console. No double counting. Raw bytes are post-redaction/restoration payload, not unobservable pre-wrapper output. Explicit --full/browser or raw-log reads counted as expansion requests. browser_result_components is a separate non-additive breakdown of UTF-8 serialized console/network/DOM subtrees (not shell-envelope bytes); never add it to category totals.',
   cache_prefix:{task_metrics:apiCacheMetrics(requests),latest_server_request:(this.options.native??latestNative)(this.options.root??ROOT)},
   benchmark_snapshot_path:this.options.path??NE,benchmark_snapshot_locator:{event:'benchmark_snapshot',task_id:this.task.id},benchmark_snapshot_generation_failed:this.configError};
 }
 snapshot(summary:any,wall:number){return {schema:1,event:'benchmark_snapshot',timestamp:iso(wall),session_id:this.task.session,task_id:this.task.id,config:this.config,effective_request_variants:[...this.requestVariants.values()],request_variant_limit_reached:this.requestVariantOverflow,effective_system_prompt_sha256:[...this.systemPromptHashes],tool_schema_sha256:this.toolHashes.size?[...this.toolHashes]:NE,capability_context_sha256:this.capabilityHashes.size?[...this.capabilityHashes]:NE,missing_context_note:'Unobserved payload fields remain Not exposed; inventory hash is separate from actual emitted context hash',snapshot_generation_failed:this.configError,summary_locator:{event:'task_summary',task_id:this.task.id},terminal_state:summary.terminal_state??NE};}
 persistenceStatus(){return this.saved;}
 persist(snapshot:any){if(this.reserved)return;this.reserved=true;const path=this.options.path;if(!path)throw Error('No journal configured');const write=this.options.persist??((record:any)=>appendFileSync(path,JSON.stringify(record)+'\n',{mode:0o600}));const result=write(snapshot);if(result?.then)return result.then(()=>{this.saved=true;});this.saved=true;return result;}
}
