// Thin ACP transport adapter to the one host policy resolver. No tool schema,
// prompt, provider, retry or model setting is involved.
import {spawnSync} from 'node:child_process';
import {PY,PYENV} from './paths.ts';
export function answerPermission(params:any){
 const q=spawnSync(PY,['-B','-m','dwarfstar.approval_policy'],{
  input:JSON.stringify({approval_mode:process.env.DWARFSTAR_APPROVAL_MODE,message:{jsonrpc:'2.0',id:'native',method:'session/request_permission',params}}),
  encoding:'utf8',timeout:5000,maxBuffer:65536,env:{...process.env,...PYENV}});
 const value=JSON.parse(q.stdout||'{}');
 if(q.status!==0||!value.ok||!value.value)throw Error(value.error??'DwarfStar permission policy unavailable');
 return value.value.result;
}
