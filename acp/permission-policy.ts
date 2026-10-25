// Thin ACP transport adapter to the one host policy resolver. No tool schema,
// prompt, provider, retry or model setting is involved.
import {spawnSync} from 'node:child_process';
const root='$LOCAL_AI_SETUP/challengers/dwarfstar-paperniuk';
export function answerPermission(params:any){
 const q=spawnSync('$LOCAL_AI_SETUP/v6/.venv/bin/python',['-B',root+'/approval_policy.py'],{
  input:JSON.stringify({approval_mode:process.env.DWARFSTAR_APPROVAL_MODE,message:{jsonrpc:'2.0',id:'native',method:'session/request_permission',params}}),
  encoding:'utf8',timeout:5000,maxBuffer:65536,env:process.env});
 const value=JSON.parse(q.stdout||'{}');
 if(q.status!==0||!value.ok||!value.value)throw Error(value.error??'DwarfStar permission policy unavailable');
 return value.value.result;
}
