// Advisory, once per user task; consumes actual tool receipts, never model scores.
import {browserCommand} from './result-hygiene.ts';
export const FINISH_NUDGE='The integrated product appears functional and the major requested capabilities have been implemented and validated. Unless a blocking requirement or clearly user-visible defect remains, perform one final targeted validation and finish the original task.';
export const isResume=(text:string)=>/^\s*(?:please\s+)?(?:continue|go on|carry on|keep going|resume|try again|retry)\b/i.test(text);
export class FinishNudge {
 prompt='';product=false;excluded=false;revision=0;sourceBytes=0;validations:any[]=[];active=new Set<string>();failedCommands=new Set<string>();missing=false;eligible=false;sent=false;attempted=false;sentAt:number|null=null;
 begin(prompt:string){this.prompt=prompt;this.product=/\b(build|create|implement|develop)\b/i.test(prompt)&&/\b(app|application|website|web|browser|game|simulator|dashboard|product|frontend|ui)\b/i.test(prompt);this.excluded=/\b(exhaustive|refactor(?:ing)?|every edge case|full test coverage)\b|comprehensive\s+test/i.test(prompt);}
 toolStart(e:any){this.active.add(e.toolCallId);}
 toolEnd(e:any){this.active.delete(e.toolCallId);}
 assistant(message:any){const text=(message?.content??[]).filter((x:any)=>x.type==='text').map((x:any)=>x.text).join('\n');if(/\b(?:not yet implemented|not implemented|unimplemented|major .{0,35} missing|still need to (?:build|implement)|primary .{0,25}(?:broken|missing|does not work))\b/i.test(text))this.missing=true;}
 result(e:any,requestNumber:number,now:number){
  if(e.details?.executed===false||e.details?.__synthetic)return;
  if(['write','edit'].includes(e.toolName)&&!e.isError){const path=String(e.input?.path??'');if(/\.(?:html?|[cm]?js|jsx|tsx?|css|vue|svelte)$/i.test(path)){this.revision++;this.sourceBytes+=Buffer.byteLength(e.input?.content??e.input?.newText??e.input?.new_text??'');this.validations=[];}}
  if(e.toolName!=='bash'||e.content?.length!==1||e.content[0].type!=='text')return;
  const cmd=String(e.input?.command??'').trim();let outer:any;try{outer=JSON.parse(e.content[0].text);}catch{return;}
  if(e.isError||outer.exit_code!==0){this.failedCommands.add(cmd);return;}
  this.failedCommands.delete(cmd);
  if(!browserCommand(cmd))return;
  let r:any;try{r=JSON.parse(outer.output);}catch{return;}
  const v=r.validation;
  // These fields are emitted by the read-only installed browser helper after real
  // launch, rendering/DOM observations and post-interaction assertions. A command
  // printing its own "done"/score JSON is not an eligible browser command.
  if(!v||v.source!=='dwarfstar-browser-validation-v1'||r.ok!==true||v.local!==true||!v.entrypoint_exists||!v.primary_ui_visible||!v.primary_interaction_exercised||v.functional_assertions<1||v.negative_dom_observations>0||v.blocking_runtime_error||!v.evidence_complete){this.validations=[];return;}
  const ended=Date.parse(r.ended_at)/1000;
  if(!Number.isFinite(ended)||ended>now+5||ended<now-120)return;
  this.validations.push({revision:this.revision,request:requestNumber,ended,entrypoint:v.entrypoint});
 }
 evaluate(core:any,turn:any){
  this.eligible=!!(this.product&&!this.excluded&&!this.sent&&!this.attempted&&!this.missing&&!this.failedCommands.size&&!this.active.size&&this.revision>0&&this.sourceBytes>=8192&&core?.alive&&!core?.cancelled&&!core?.request?.signal&&core?.request?.status==='NORMAL_COMPLETE'&&turn?.message?.stopReason==='toolUse'&&turn.toolResults?.length&&this.validations.length>=2&&new Set(this.validations.map(x=>x.request)).size>=2&&new Set(this.validations.map(x=>x.entrypoint)).size===1);
  return this.eligible;
 }
}
