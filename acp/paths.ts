// Where product code and per-model state live. The agent launcher sets these;
// nothing here is machine-specific.
import {resolve} from 'node:path';
import {readFileSync} from 'node:fs';
/** Repository root (product code). */
export const HOME=process.env.DWARFSTAR_HOME??resolve(import.meta.dir,'..');
/** Local data root (models, state, caches). */
export const DATA=process.env.DWARFSTAR_DATA??'';
/** Runtime root of the current model: generated config plus mutable state. */
export const ROOT=process.env.DWARFSTAR_INSTANCE??'';
export const PY=process.env.DWARFSTAR_PYTHON??'python3';
/** Environment that lets a child Python process import the dwarfstar package for the current model. */
export const PYENV={PYTHONPATH:HOME+'/src',DWARFSTAR_HOME:HOME,DWARFSTAR_DATA:DATA,DWARFSTAR_MODEL:process.env.DWARFSTAR_MODEL??'',DWARFSTAR_INSTANCE:ROOT,PYTHONDONTWRITEBYTECODE:'1'};
/** Files that belong to the model's runtime root; everything else named by a relative path is product code. */
const INSTANCE_PREFIXES=['runtime.json','acp/profiles.json','acp/providers/','acp/models.yml','state/','assets.json'];
const SHARED_CONFIG=new Map([['acp/approval.json','config/approval.json'],['acp/permission-runtime.json','config/permission-runtime.json']]);
export function locate(relative:string,root=ROOT){
 if(SHARED_CONFIG.has(relative))return DATA+'/'+SHARED_CONFIG.get(relative);
 return (INSTANCE_PREFIXES.some(p=>relative===p||relative.startsWith(p))?root:HOME)+'/'+relative;
}
export function endpoint(){return JSON.parse(readFileSync(ROOT+'/runtime.json','utf8')).endpoint as string;}
