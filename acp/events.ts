// Structured agent and tool events for the telemetry hub.
// Fire-and-forget loopback UDP. Tool names, categories, outcomes and durations
// only: never arguments, file contents, command lines or model text.
import dgram from 'node:dgram';
const port=Number(process.env.DWARFSTAR_TELEMETRY_PORT??0);
const enabled=process.env.DWARFSTAR_TELEMETRY!=='0'&&port>0;
let socket:any;
export function emit(kind:string,fields:any={}){
 if(!enabled)return;
 try{
  if(!socket){socket=dgram.createSocket('udp4');socket.unref?.();socket.on?.('error',()=>{});}
  const data=Buffer.from(JSON.stringify({v:1,t:Date.now()/1000,src:'agent',model:process.env.DWARFSTAR_MODEL,profile:process.env.DWARFSTAR_PROFILE_ID,kind,...fields}));
  if(data.length<=8192)socket.send(data,port,'127.0.0.1',()=>{});
 }catch{/* telemetry never affects the session */}
}
/** Which part of the workstation a tool call touches. Decided from the tool name and, for the shell, the program name only. */
export function category(name:string,args?:any){
 if(name==='bash'){const first=String(args?.command??'').trim().split(/\s+/,1)[0];return /(^|\/)dwarf-browser$/.test(first)?'browser':'shell';}
 if(['read','write','edit','grep','find','ls'].includes(name))return 'workspace';
 if(typeof name==='string'&&name.startsWith('mcp__kimicu_'))return 'gui';
 return 'tool';
}
const short=(id:any)=>String(id??'').slice(-8);
export function installEvents(pi:any){
 if(!enabled)return;
 const started=new Map<string,{t:number,name:string,category:string}>();
 pi.on('tool_execution_start',(e:any)=>{try{const c=category(e.toolName,e.args);started.set(e.toolCallId,{t:performance.now(),name:e.toolName,category:c});emit('tool',{phase:'start',name:e.toolName,category:c,call:short(e.toolCallId)});}catch{}});
 pi.on('tool_execution_end',(e:any)=>{try{const s=started.get(e.toolCallId);started.delete(e.toolCallId);emit('tool',{phase:'end',name:s?.name??e.toolName,category:s?.category??category(e.toolName),call:short(e.toolCallId),ok:!e.isError,ms:s?Math.round(performance.now()-s.t):null});}catch{}});
 pi.on('session_shutdown',()=>{emit('agent',{phase:'session_end'});});
}
