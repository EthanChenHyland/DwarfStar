import {activeProfile} from './lifecycle.ts';
// Bounded local reads only. No subprocesses, network, model/tokenizer or cache writes.
import {readFileSync,statSync,openSync,readSync,closeSync} from 'node:fs';
import {createHash} from 'node:crypto';
export const ROOT='$LOCAL_AI_SETUP/challengers/dwarfstar-swift15';
export const NE='Not exposed';
const canonical=(x:any):any=>Array.isArray(x)?x.map(canonical):x&&typeof x==='object'?Object.fromEntries(Object.keys(x).sort().filter(k=>x[k]!==undefined).map(k=>[k,canonical(x[k])])):x;
export const hash=(x:any)=>createHash('sha256').update(typeof x==='string'||Buffer.isBuffer(x)?x:JSON.stringify(canonical(x))).digest('hex');
export const smallRead=(path:string)=>{if(statSync(path).size>2*1024*1024)throw Error('Observation size bound');return readFileSync(path,'utf8');};
const attempt=(fn:()=>any)=>{try{return fn()??NE;}catch{return NE;}};
export const CODE_FILES=['approval_policy.py','acp/approval.json','acp/permission-policy.ts','acp/permission-runtime.json','acp/profiles.json','profiles.py','agent-tools/permissions.py','acp/benchmark-observability.ts','acp/benchmark-snapshot.ts','acp/efficiency.ts','acp/task-summary.ts','acp/finish-nudge.ts','acp/result-hygiene.ts','acp/telemetry.ts','acp/latency.ts','acp/transport.ts','acp/infrastructure.ts','acp/lifecycle.ts','acp/infrastructure.json','acp/models.yml','scope_agent.py','agent-tools/extension.ts','agent-tools/shell.py','agent-tools/boundary.py','agent-tools/bin/browser.mjs','agent-tools/bin/dwarf-browser'];
export function configSnapshot(flags:any,root=ROOT,read=smallRead){
 const json=(p:string)=>JSON.parse(read(root+'/'+p)),sha=(p:string)=>attempt(()=>hash(read(root+'/'+p)));
 const r=json('runtime.json'),cap=attempt(()=>json('agent-tools/state/capabilities.json')),pkg=attempt(()=>json('agent-tools/package.json'));
 const installed=(name:string)=>attempt(()=>json('agent-tools/node_modules/'+name+'/package.json').version);
 return {profile:activeProfile().name,model_id:r.model_id,model:r.model,quant:r.quant,model_path:r.argv?.[r.argv.indexOf('-m')+1]??NE,
  runtime:{name:r.runtime,version:NE,build_commit:r.source?.commit??NE,branch:r.source?.branch??NE,configured_binary_sha256:r.server_sha256??NE},
  inference:{context:r.context,max_output:r.max_output,thinking:r.thinking,reasoning_effort:activeProfile().reasoning_effort??r.reasoning_effort,mtp:r.mtp,mtp_depth:NE,sampling:r.sampling,endpoint:r.endpoint},
  chat_template:{identity:'Embedded Qwen chat renderer in ds4_server.c',configured_binary_sha256:r.server_sha256??NE,source_file_sha256:sha('ds4_server.c'),note:'Whole renderer source / pinned binary identity; no standalone runtime template hash exposed'},
  scope_prompt_sha256:sha('prompts/scope-first-system-prompt.md'),scope_prompt_applied:activeProfile().prompt==='prompts/scope-first-system-prompt.md',system_prompt:{identity:activeProfile().prompt,mode:activeProfile().prompt_mode,sha256:sha(activeProfile().prompt)},policy:activeProfile().policy,provider_config_sha256:sha(activeProfile().provider_config??'acp/models.yml'),capability_inventory_sha256:sha('agent-tools/state/capabilities.json'),
  approval_mode:process.env.DWARFSTAR_APPROVAL_MODE??attempt(()=>json('acp/approval.json').approval_mode),permission_runtime:attempt(()=>json('acp/permission-runtime.json')),feature_flags:{...flags},versions:{source:'Installed package metadata when readable; otherwise cached inventory, not a fresh executable probe',inventory_detected_at:cap.detected_at??NE,playwright:installed('playwright')===NE?(cap.playwright??NE):installed('playwright'),puppeteer:installed('puppeteer')===NE?(cap.puppeteer??NE):installed('puppeteer'),chrome:cap.browser_version??NE,chrome_path:cap.browser_executable??NE,node:cap.node??NE,acp_bun:process.versions.bun??NE},
  agent_tools:{name:pkg.name??NE,version:pkg.version??NE,package_sha256:sha('agent-tools/package.json'),lock_sha256:sha('agent-tools/package-lock.json')},
  harness_sha256:Object.fromEntries(CODE_FILES.map(p=>[p,sha(p)])),config_read_at:'Task segment start; effective request fingerprints captured separately'};
}
export function cacheMetrics(r:any){
 const exact=(v:any)=>({value:Number.isFinite(v)&&v>=0?v:NE,kind:Number.isFinite(v)&&v>=0?'exact runtime metric':'not exposed'});
 const prompt=exact(r?.prompt_tokens),prefix=exact(r?.reused_tokens),fresh=exact(r?.new_prefill_tokens);
 return {rendered_prompt_tokens:prompt,reusable_prefix_tokens:prefix,newly_prefilled_tokens:fresh,
  prefix_reuse_percent:{value:typeof prompt.value==='number'&&prompt.value>0&&typeof prefix.value==='number'?100*prefix.value/prompt.value:NE,kind:typeof prompt.value==='number'&&prompt.value>0&&typeof prefix.value==='number'?'derived':'not exposed'},
  prefix_hit:{value:typeof prefix.value==='number'?prefix.value>0:NE,kind:typeof prefix.value==='number'?'derived':'not exposed'},
  prefill_seconds:exact(r?.prefill_seconds),checkpoint_reuse:{value:r?.checkpoint_source??NE,kind:r?.checkpoint_source?'exact runtime metric':'not exposed'},checkpoint_restore_seconds:exact(r?.checkpoint_restore_s),
  checkpoint_note:'A reusable live prefix does not prove a disk checkpoint restore; absent checkpoint evidence is unknown, not a miss'};
}
// Existing OMP request receipts carry authoritative total prompt counts. A positive
// cache count is meaningful; a normalized zero can also mean the field was absent.
export function apiCacheMetrics(requests:any[]=[]){
 const rows=requests.map(r=>({request_id:r.id??NE,
  rendered_prompt_tokens:{value:r.wire?.usage_observed===true&&Number.isFinite(r.input_tokens)?r.input_tokens:NE,kind:r.wire?.usage_observed===true&&Number.isFinite(r.input_tokens)?'exact runtime metric':'not exposed'},
  reusable_prefix_tokens:{value:r.wire?.usage_observed===true&&r.cache_read_tokens>0?r.cache_read_tokens:NE,kind:r.wire?.usage_observed===true&&r.cache_read_tokens>0?'exact runtime metric':'not exposed'},
  newly_prefilled_tokens:NE,prefill_seconds:NE,checkpoint_reuse:NE}));
 const promptKnown=rows.length>0&&rows.every(r=>typeof r.rendered_prompt_tokens.value==='number'),prefixKnown=rows.length>0&&rows.every(r=>typeof r.reusable_prefix_tokens.value==='number');
 const prompt=promptKnown?rows.reduce((s,r)=>s+Number(r.rendered_prompt_tokens.value),0):NE,prefix=prefixKnown?rows.reduce((s,r)=>s+Number(r.reusable_prefix_tokens.value),0):NE;
 return {rendered_prompt_tokens:{value:prompt,kind:promptKnown?'exact runtime metric':'not exposed'},reusable_prefix_tokens:prefix,
  reusable_prefix_tokens_kind:prefixKnown?'exact runtime metric':'not exposed',newly_prefilled_tokens:NE,checkpoint_reuse:NE,prefill_seconds:NE,
  prefix_reuse_percent:{value:typeof prompt==='number'&&prompt>0&&typeof prefix==='number'?100*prefix/prompt:NE,kind:typeof prompt==='number'&&prompt>0&&typeof prefix==='number'?'derived':'not exposed'},
  per_request:rows,reason:'Existing API usage receipts only. Normalized zero does not prove an exposed cache zero. Native journal has no client IDs; no correlation is invented.'};
}
export function latestNative(root=ROOT){
 try{
  const server=JSON.parse(smallRead(root+'/state/server.json')),path=root+'/state/latency/native-'+server.pid+'.jsonl';
  const fd=openSync(path,'r');let text='';try{const size=statSync(path).size,n=Math.min(size,65536),b=Buffer.alloc(n);const used=readSync(fd,b,0,n,size-n);text=b.subarray(0,used).toString('utf8');}finally{closeSync(fd);}
  const rows=text.split('\n').flatMap(s=>{try{return [JSON.parse(s)];}catch{return [];}});
  const row=rows.reverse().find(r=>r.server_pid===server.pid&&r.server_birth===server.birth&&r.event==='native_request_end');
  return {scope:'Server request, NOT attributed to this task/client',source:path,event_id:row?.event_id??NE,observed_at:row?.observed_at??NE,native_timestamp:row?.native_timestamp??NE,server_pid:server.pid,server_birth:server.birth,metrics:cacheMetrics(row)};
 }catch{return {scope:'Server request, NOT attributed to this task/client',source:NE,availability:'Native journal absent or inaccessible to this ACP; access policy unchanged',metrics:cacheMetrics(null)};}
}
