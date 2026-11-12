// Infrastructure context and shell only. Behavioral prompt and provider policy stay unchanged.
import {spawn,spawnSync} from 'node:child_process';
import {readFileSync,appendFileSync,realpathSync,statSync} from 'node:fs';
import {restoreShellOutput,browserCommand} from '../acp/result-hygiene.ts';
import {resolve} from 'node:path';
import {forTextRedaction,restoreImageContent} from '../acp/image-content.ts';
import {infrastructureSettings,activeProfile} from '../acp/lifecycle.ts';
const ROOT='$LOCAL_AI_SETUP/challengers/dwarfstar-cyber/agent-tools';
const PY='$LOCAL_AI_SETUP/challengers/dwarfstar-cyber/.venv/bin/python';
// Reuse the shared local policy for approvals and shell enforcement. Missing
// policy facts require confirmation; never fall back to unconditional approval.
export function permission(command:string){
 try{const q=spawnSync(PY,['-B',ROOT+'/permissions.py'],{input:JSON.stringify({command,workspace:process.env.DWARFSTAR_WORKSPACE??process.cwd(),approval_mode:process.env.DWARFSTAR_APPROVAL_MODE}),encoding:'utf8',timeout:2000,maxBuffer:8192,env:{PATH:'/usr/bin:/bin',PYTHONDONTWRITEBYTECODE:'1'}});if(q.status!==0)throw Error('Policy unavailable');return JSON.parse(q.stdout);}catch{return {tier:4,category:'policy_unavailable',policy:'prompt',sandbox_blocked:true};}
}
export function protectedFile(input:any){
 const paths=[input?.path,input?.oldPath,input?.newPath].filter((p:any)=>typeof p==='string');
 if(typeof input?.input==='string')for(const match of input.input.matchAll(/(?:^|\n)(?:\[([^\]\n]+)#[A-Fa-f0-9]{4}\]|\*\*\* (?:Add|Update|Delete) File:\s*(.+))/g))paths.push(match[1]??match[2]);
 return paths.some((path:string)=>/(?:^|[\/])(?:\.ssh|\.aws|\.azure|\.kube|Keychains)(?:[\/]|$)|(?:^|[\/])(?:id_rsa|id_ed25519|Cookies|Login Data|\.netrc|\.git-credentials)$/i.test(path));
}
function audit(tool:string,p:any,decision:string,outcome?:string){
 try{const file=process.env.DWARFSTAR_LATENCY_FILE;if(file)appendFileSync(file,JSON.stringify({schema:1,event:'permission_decision',timestamp:Date.now()/1000,tool,policy_tier:p.tier,category:p.category,decision,outcome})+'\n',{mode:0o600});}catch{}
}
export default function(pi:any){
  pi.on('tool_call',(e:any)=>{if(process.env.DWARFSTAR_APPROVAL_MODE!=='full_auto'&&e.toolName!=='bash'&&protectedFile(e.input)){audit(e.toolName,{tier:4,category:'protected_credential_path'},'blocked_operator_required');return {block:true,reason:'Protected credential/keychain path requires explicit operator action; workspace auto approval does not grant access.'};}});
  pi.on('tool_execution_end',(e:any)=>{if(e.toolName!=='bash'&&e.result?.details?.executed!==false)audit(e.toolName,{tier:1,category:'workspace_file_or_inspection'},e.isError?'failed_or_blocked':'auto_approved');});
  const flags=infrastructureSettings(),hygieneEnabled=flags.tool_result_hygiene_enabled===true;
  if(flags.benchmark_observability_enabled===true&&!hygieneEnabled&&flags.finish_nudge_enabled===false)process.env.DWARFSTAR_PASSIVE_BROWSER_RECEIPTS='1';
  // Preserve the existing fail-closed result redaction for native read/write/edit.
  pi.on('tool_result',(e:any,ctx:any)=>{
    // Optional expansion from the existing shell log stays BEFORE redaction.
    // Any expansion failure returns the original result; security redaction is unchanged.
    e=restoreShellOutput(e,ctx,hygieneEnabled);
    const q=spawnSync(PY,['$LOCAL_AI_SETUP/v7/tools/redact_stdin_v2.py',ctx.cwd],{input:JSON.stringify({content:forTextRedaction(e.content),details:e.details}),encoding:'utf8',timeout:10000,maxBuffer:4000000,env:{PATH:'/usr/bin:/bin',HOME:process.env.HOME!,PYTHONDONTWRITEBYTECODE:'1'}});
    try{const value=JSON.parse(q.stdout);return {...value,content:restoreImageContent(e.content,value.content),isError:e.isError};}catch{return {content:[{type:'text',text:'{"status":"error","error":"Redaction failed closed"}'}],details:{redaction_failed:true},isError:true};}
  });
  // Identical sampling defaults formerly provided by aci_safe.ts.
  pi.on('before_provider_request',(e:any)=>{const p=e.payload;if(p.enable_thinking===false)return p;return {...p,temperature:p.temperature??1,top_p:p.top_p??.95,top_k:p.top_k??20,presence_penalty:p.presence_penalty??0,frequency_penalty:p.frequency_penalty??0};});
  const exposed=new Set<string>();
  pi.on('before_agent_start',(e:any,ctx:any)=>{
    // OMP appends this behavioral boilerplate even with an empty custom template.
    // Clean controls remove ONLY that exact bundled block via the supported hook.
    const critical='<critical>\n- Each response MUST advance the task; completion only stopping condition.\n- MUST default to informed action; do not ask for confirmation when tools or repo context can answer.\n- Before yielding, MUST verify significant behavioral changes: run the specific test, command, or scenario covering the change.\n</critical>';
    const override=activeProfile().prompt_mode==='template'&&Array.isArray(e.systemPrompt)?{systemPrompt:e.systemPrompt.map((p:string)=>p.replace(critical,'').trimEnd())}:{};
    const id=ctx.sessionManager.getSessionId();if(exposed.has(id))return Object.keys(override).length?override:undefined;
    // A compact environmental message; never replace/append to the system prompt.
    let capabilities:any;try{capabilities=JSON.parse(readFileSync(ROOT+'/state/capabilities.json','utf8'));}catch{capabilities={status:'unknown; run dwarf-capabilities'};}
    exposed.add(id);
    const summary={os:capabilities.os,architecture:capabilities.architecture,node:capabilities.node,npm:capabilities.npm,bun:capabilities.bun,pnpm:capabilities.pnpm,git:capabilities.git,playwright:capabilities.playwright,puppeteer:capabilities.puppeteer,browser:capabilities.browser_executable,agent_tools:ROOT,browser_cache:capabilities.browser_cache,network:capabilities.network?.status,network_checked_at:capabilities.network?.checked_at,network_receipt_stale:capabilities.network_receipt_stale,shell_http:Object.values(capabilities.network?.resources??{}).filter((x:any)=>x.shell?.ok).length,node_http:Object.values(capabilities.network?.resources??{}).filter((x:any)=>x.node?.ok).length,browser_http:capabilities.network?.browser,features:capabilities.features,policy:capabilities.policy};
    return {...override,message:{customType:'dwarfstar-environment-v1',display:false,content:JSON.stringify({environment:summary,usage:'bash: dwarf-browser --help; dwarf-node script.mjs (imports playwright/puppeteer); dwarf-capabilities --network refreshes connectivity. Browser uses a new unauthenticated profile. Background servers must be started/tested/stopped in one bounded bash invocation, or use dwarf-browser plan.server. Global/system/credential operations require explicit operator approval; sandbox does not self-escalate.'})}};
  });
  pi.registerTool({name:'bash',label:'Development shell',loadMode:'essential',description:'Run workspace builds/tests, git, public HTTPS, local dependency installs and dwarf-browser/Playwright/Puppeteer. Workspace and agent-tools writes only; fresh unauthenticated browser; global/system/credential operations require explicit operator approval. Each command is logged. Use bounded same-call server + browser workflows; background children are cleaned up.',
    // Keep the existing bash argument schema.
    parameters:{type:'object',properties:{command:{type:'string'},timeout:{type:'number'}},required:['command'],additionalProperties:false},
    approval:(p:any)=>{const value=permission(p.command);return {tier:'exec',policy:value.policy,reason:value.policy==='prompt'?'DwarfStar explicit approval: '+value.category:undefined};},
    async execute(_id:any,p:any,signal:any,_up:any,ctx:any){
      const granted=permission(p.command);audit('bash',granted,granted.policy==='allow'?'auto_approved':'user_confirmed');
      const child=spawn(PY,['-B',ROOT+'/shell.py'],{cwd:ctx.cwd,env:process.env,stdio:['pipe','pipe','pipe']});
      child.stdin.end(JSON.stringify(p));let output='',error='';
      child.stdout.on('data',b=>output+=b);child.stderr.on('data',b=>error+=b);
      const abort=()=>child.kill('SIGTERM');signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)abort();
      try{await new Promise<void>((resolve,reject)=>{child.once('error',reject);child.once('close',()=>resolve());});
        const result=JSON.parse(output||JSON.stringify({exit_code:126,output:error.slice(-1000)}));
        let validation:any;try{
          if(process.env.DWARFSTAR_PASSIVE_BROWSER_RECEIPTS==='1'){
            const call=browserCommand(p.command),root=realpathSync(ctx.cwd);
            const local=(file:string)=>{const path=realpathSync(resolve(root,file));if(!path.startsWith(root+'/')||statSync(path).size>1048576)throw Error('Observation outside bound');return path;};
            if(call){const plan=JSON.parse(readFileSync(local(call.input),'utf8'));if(plan.output)validation=JSON.parse(readFileSync(local(plan.output),'utf8')).validation;}
          }
        }catch{/* Unobservable evidence never changes the normal tool result. */}
        return {content:[{type:'text',text:JSON.stringify(result)}],details:{exitCode:result.exit_code,...(validation?{dwarfstar_browser_validation:validation}:{})},isError:result.exit_code!==0};
      }finally{signal?.removeEventListener('abort',abort);}
    }
  });
}
