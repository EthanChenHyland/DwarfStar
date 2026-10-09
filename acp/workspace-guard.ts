// ACP client file access occurs outside Seatbelt; keep the frozen workspace scope.
import {existsSync,realpathSync,statSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
export default function(pi:any){
 const root=realpathSync(process.env.V7_WORKSPACE!);
 const inside=(p:string)=>p===root||p.startsWith(root+'/');
 pi.on('tool_call',(e:any,ctx:any)=>{
  if(realpathSync(ctx.cwd)!==root)return {block:true,reason:'GUI workspace changed; open a new scoped agent in that project.'};
  if(!['read','write','edit','bash','aci'].includes(e.toolName))return {block:true,reason:'Tool is outside the existing selected profile.'};
  if(['read','write','edit'].includes(e.toolName)&&e.input?.path){
   let path=resolve(root,e.input.path),parent=path;
   if(!inside(path))return {block:true,reason:'Path outside designated workspace'};
   while(!existsSync(parent)&&parent!==dirname(parent))parent=dirname(parent);
   if(!inside(realpathSync(parent)))return {block:true,reason:'Ancestor symlink outside workspace'};
   if(e.toolName!=='read'&&existsSync(path)&&statSync(path).nlink>1)return {block:true,reason:'Shared inode write blocked'};
  }
 });
}
