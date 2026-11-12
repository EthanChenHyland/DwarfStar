// Result handling only. Never participates in tool admission or stream classification.
import {readFileSync,lstatSync,realpathSync} from 'node:fs';
import {resolve,relative,isAbsolute,dirname} from 'node:path';
import {infrastructureSettings} from './lifecycle.ts';
export const contentBytes=(content:any[])=>content.reduce((n,b)=>n+(b.type==='text'?Buffer.byteLength(b.text??''):b.type==='image'?Buffer.byteLength(b.data??''):0),0);
export function browserCommand(command:string){
 const binary=command.trim().split(/\s+/,1)[0];
 if(!['dwarf-browser','$LOCAL_AI_SETUP/challengers/dwarfstar-cyber/agent-tools/bin/dwarf-browser'].includes(binary))return null;
 const m=command.trim().match(/^(?:dwarf-browser|\/[^\s;|&`$<>]*\/dwarf-browser)\s+(?:(--full)\s+)?(?:"([^"\n$`]+)"|'([^'\n]+)'|([^\s;&|`$<>]+))$/);
 return m?{full:!!m[1],input:m[2]??m[3]??m[4]}:null;
}
function localLog(file:string,workspace:string){
 const root=realpathSync(workspace),alias=resolve(workspace),logs=resolve(root,'.dwarfstar-tooling-logs'),p=resolve(file.startsWith(alias+'/')?resolve(root,relative(alias,file)):file),rel=relative(logs,p);
 if(!rel||rel.startsWith('..')||isAbsolute(rel))return null;
 for(let q=p;q!==root;q=dirname(q))if(lstatSync(q).isSymbolicLink())return null;
 const s=lstatSync(p);return s.isFile()&&s.nlink===1?{path:p,bytes:s.size}:null;
}
// Restore from the existing redacted shell log BEFORE the existing result redactor.
// No duplicate archive, raw reads outside the workspace, or unbounded large reads.
export function restoreShellOutput(e:any,ctx:any,enabled=infrastructureSettings().tool_result_hygiene_enabled===true){
 if(!enabled||e.toolName!=='bash'||e.content?.length!==1||e.content[0].type!=='text')return e;
 try{const value=JSON.parse(e.content[0].text);if(typeof value.log!=='string'||typeof value.output!=='string')return e;
  const log=localLog(value.log,ctx.cwd);if(!log)return e;
  const pointer=()=>browserCommand(String(e.input?.command??''))?{...e,content:[{type:'text',text:JSON.stringify({...value,output:JSON.stringify({raw_output:log.path,raw_bytes:log.bytes,note:'Complete browser JSON exceeds bounded inline expansion. Read raw_output (ranges supported) for full DOM/network/console data.',unmodified_shell_tail:value.output})})}]}:e;
  if(log.bytes>2*1024*1024)return pointer();
  const p=log.path,output=readFileSync(p,'utf8');
  if(output===value.output)return e;
  const candidate={...e,content:[{type:'text',text:JSON.stringify({...value,output})}],details:{...e.details,dwarfstar_raw_output:{path:p,restored:true}}};
  // Respect the existing redactor's 4-MB response buffer, including JSON escaping.
  return Buffer.byteLength(JSON.stringify({content:candidate.content,details:candidate.details}))<=1024*1024?candidate:pointer();
 }catch{return e;}
}
const requestedFull=(s:string)=>/\b(?:full|raw|entire|unabridged|untruncated|verbatim)\b.{0,45}\b(?:output|logs?|results?|console|network|dom|file)\b|\b(?:output|logs?|results?|console|network|dom|file)\b.{0,45}\b(?:in full|verbatim|unabridged|untruncated)\b|\b(?:show|list|return|print|report)\b.{0,35}\b(?:all network requests|network resources|console messages|font requests|image requests)\b/i.test(s);
export const wantsFullOutput=requestedFull;
const uniq=(rows:any[])=>{const m=new Map<string,any>();for(const row of rows){const k=JSON.stringify(row),v=m.get(k);if(v)v.occurrences++;else m.set(k,{...typeof row==='string'?{text:row}:row,occurrences:1});}return [...m.values()];};
export function compactBrowser(r:any,raw:string){
 if(!r||r.engine!=='playwright'||r.ok!==true||!r.events||!raw)return null;
 // Lifecycle timing and executable inventory already have authoritative channels.
 // Their full browser receipt remains available via the existing raw log.
 const {validation:internalValidation,browser:executableInventory,started_at:startedAt,ended_at:endedAt,...visible}=r;
 const ev=r.events,consoleRows=ev.console??[],responses=ev.responses??[];
 return {...visible,events:{...ev,
  console:uniq(consoleRows.filter((x:any)=>['error','warning','warn','assert'].includes(x.type))),
  console_summary:{errors:consoleRows.filter((x:any)=>['error','assert'].includes(x.type)).length,warnings:consoleRows.filter((x:any)=>['warn','warning'].includes(x.type)).length,informational:consoleRows.filter((x:any)=>!['error','assert','warn','warning'].includes(x.type)).length,observation_complete:!ev.dropped?.console},
  pageErrors:uniq(ev.pageErrors??[]),
  responses:responses.filter((x:any)=>!Number.isFinite(x.status)||x.status>=300||!x.resource_type||['document','script','stylesheet'].includes(x.resource_type)),
  response_summary:{observed:responses.length,omitted_successful_secondary:responses.filter((x:any)=>x.status<300&&x.resource_type&&!['document','script','stylesheet'].includes(x.resource_type)).length}
 },raw_output:raw,output_note:'Secondary successes omitted; read raw_output for full data.'};
}
function commandKind(command:string){
 if(/[;&|`<>\n]/.test(command))return null;
 if(/^\s*(?:npm\s+(?:install|ci|add)|pnpm\s+(?:install|add)|bun\s+(?:install|add))\b/.test(command))return 'package_install';
 if(/^\s*(?:(?:npm|pnpm)\s+(?:run\s+)?(?:test|test:[\w:-]+)|bun\s+test|node\s+--test|(?:npx\s+)?(?:vitest|jest|pytest)|python[\d.]*\s+-m\s+(?:pytest|unittest))\b/.test(command))return 'test_success';
 if(/^\s*(?:(?:npm|pnpm|bun)\s+(?:run\s+)?(?:build|check|typecheck)|(?:npx\s+)?(?:tsc|vite\s+build)|make\s+(?:all|build))\b/.test(command))return 'build_success';
 return null;
}
function concise(command:string,output:string,kind:string){
 const lines=output.replace(/\x1b\[[0-9;]*m/g,'').split(/\r?\n/),important=new Set<number>();
 // Keep complete warning paragraphs, never a fixed number of continuation lines.
 for(let i=0;i<lines.length;i++)if(/warn|deprecat|vulnerab|security|audit|error|fail/i.test(lines[i])){let end=i+1;while(end<lines.length&&lines[end].trim())end++;for(let j=Math.max(0,i-2);j<=Math.min(end,lines.length-1);j++)important.add(j);i=end;}
 // An exit-0 wrapper can still contain a genuine failure. Never conceal it.
 if(lines.some(x=>/\b(?:error:|failed\s*[:=]\s*[1-9]|[1-9]\d*\s+(?:failed|failures)|FAIL\s|Traceback|AssertionError|ERR!)/i.test(x)))return null;
 const totals=lines.filter(x=>/^\s*(?:Tests?:|Test Suites:|Duration|Time:|Ran \d+|\d+\s+(?:pass|fail|passed|failed|skipped|tests?))|\b(?:added|changed|removed|audited)\s+\d+\s+packages?\b|\d+\s+modules?\s+(?:transformed|compiled)|built in|successfully compiled/i.test(x));
 if(kind==='test_success'&&!totals.length)return null;
 const context=[...important].sort((a,b)=>a-b).map(i=>lines[i]);
 const tail=lines.filter(x=>x.trim()).slice(-16);
 return {command,exit_code:0,summary:'Command completed successfully; recorded output excerpts follow.',kind,totals:[...new Set(totals)],warnings_and_context:context,final_lines:tail,original_lines:lines.length};
}
export function shapeResult(e:any,{taskPrompt='',enabled=true}:any={}){
 const original=contentBytes(e.content??[]);const meta:any={original_bytes:original,visible_bytes:original,rule:'unchanged',successful:!e.isError,raw_expansion_available:false};
 if(!enabled||e.toolName!=='bash'||e.content?.length!==1||e.content[0].type!=='text')return {result:undefined,meta};
 let outer:any;try{outer=JSON.parse(e.content[0].text);}catch{return {result:undefined,meta};}
 if(typeof outer.output!=='string')return {result:undefined,meta};
 const raw=typeof outer.log==='string'?outer.log:null;meta.raw_expansion_available=!!raw;
 const command=String(e.input?.command??'');
 // Explicit inspections (including source files) are not low-value log output.
 if(e.isError||outer.exit_code!==0||wantsFullOutput(taskPrompt)||/--(?:full|verbose)\b/.test(command))return {result:undefined,meta};
 let output:any,rule:string|null=null;
 if(browserCommand(command)){
  try{const receipt=JSON.parse(outer.output);output=compactBrowser(receipt,raw);if(output)rule='browser_validation';}catch{}
 }else{
  const kind=commandKind(command);
  if(kind&&raw&&Buffer.byteLength(outer.output)>4096){const summary=concise(command,outer.output,kind);if(summary){output={...summary,raw_output:raw,expand:'Read the existing raw_output log for complete output.'};rule=kind;}}
 }
 if(!rule)return {result:undefined,meta};
 const content=[{type:'text',text:JSON.stringify({...outer,output:typeof output==='string'?output:JSON.stringify(output)})}];
 // Never grow a result merely to attach summary labels; already-compact results
 // are better left intact, regardless of result type.
 if(contentBytes(content)>=original)return {result:undefined,meta};
 meta.visible_bytes=contentBytes(content);meta.rule=rule;
 return {result:{content},meta};
}
