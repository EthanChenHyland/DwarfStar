/** Direct DwarfStar-owned MCP connection using the installed OMP MCP transport.
 * No Pi subprocess/config/import. Live discovery supplies every tool schema.
 */
import {readFileSync,writeFileSync,renameSync,mkdirSync,appendFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {join} from 'node:path';
import {connectToServer,listTools,disconnectServer} from '$HOME/.bun/install/global/node_modules/@oh-my-pi/pi-coding-agent/src/mcp/client.ts';
import {MCPTool} from '$HOME/.bun/install/global/node_modules/@oh-my-pi/pi-coding-agent/src/mcp/tool-bridge.ts';
import {imageFacts} from './image-content.ts';
const ROOT='$LOCAL_AI_SETUP/challengers/dwarfstar-paperniuk';
const PY='$LOCAL_AI_SETUP/v6/.venv/bin/python';
const STATENAME='vision-kimicu.json';
function scalar(event:string,facts:any={}){try{const path=process.env.DWARFSTAR_LATENCY_FILE;if(path){appendFileSync(path,JSON.stringify({schema:1,event,timestamp:Date.now()/1000,...facts})+'\n',{mode:0o600});}}catch{}}
function registerOwnedChildren(){
 const key=process.env.DWARFSTAR_TASK_ID;if(!key)return;
 const script=`import sys,subprocess,json
sys.path.insert(0,sys.argv[1]);import task_infrastructure as i
pid=int(sys.argv[2]);key=sys.argv[3];parent=i.fingerprint(pid);rows=[]
for line in subprocess.check_output(['/bin/ps','-axo','pid=,ppid=,command='],text=True).splitlines():
 v=line.strip().split(None,2)
 if len(v)==3 and int(v[1])==pid and v[2].startswith('/Applications/KimiCU.app/Contents/MacOS/kimi-cu mcp'):
  rows.append(i.Store().register(key,int(v[0]),'mcp-kimicu',parent=parent))
print(json.dumps(rows))`;
 const result=spawnSync(PY,['-B','-c',script,ROOT,String(process.pid),key],{encoding:'utf8',timeout:5000});
 if(result.status!==0)throw Error('Cannot register owned KimiCU child');
 return JSON.parse(result.stdout);
}
export default async function installKimiCU(pi:any, dependencies:any={}){
 const connect=dependencies.connect??connectToServer,discover=dependencies.discover??listTools,disconnect=dependencies.disconnect??disconnectServer,bridge=dependencies.bridge??((connection:any,definitions:any)=>MCPTool.fromTools(connection,definitions)),own=dependencies.own??registerOwnedChildren;
 const config=JSON.parse(readFileSync(ROOT+'/acp/kimicu.json','utf8'));if(!config.enabled)return;
 let connection:any;let closed=false;let status:any={connected:false,tool_count:0,image_request_count:0,image_session:false};
 const publish=()=>{try{const dir=process.env.PI_CODING_AGENT_DIR;if(!dir)return;mkdirSync(dir,{recursive:true});const path=join(dir,STATENAME);writeFileSync(path+'.tmp',JSON.stringify(status)+'\n',{mode:0o600});renameSync(path+'.tmp',path);}catch{}};
 const close=async()=>{if(closed)return;closed=true;try{if(connection)await disconnect(connection);}finally{status.connected=false;publish();scalar('kimicu_disconnected');}};
 pi.on('session_shutdown',close);
 try{
  const started=performance.now();
  connection=await connect(config.name,{type:'stdio',command:config.command,args:config.args,timeout:30000});
  const owned=own();const definitions=await discover(connection);
  status={...status,connected:true,tool_count:definitions.length,discovery_ms:performance.now()-started,owned_processes:owned,tools:definitions,transport:'stdio directly from DwarfStar; no Pi',image_content:'typed image blocks; no textual base64',image_session_save:'Native image-conditioned disk KV save remains unsupported; ACP transcript can retain typed images; replay re-encodes images.'};publish();scalar('kimicu_connected',{tool_count:definitions.length,discovery_ms:status.discovery_ms});
  for(const native of bridge(connection,definitions)){
   pi.registerTool({name:native.name,label:native.label,description:native.description,parameters:native.parameters,strict:false,loadMode:'essential',approval:'exec',mcpServerName:config.name,mcpToolName:native.mcpToolName,
    async execute(id:string,params:any,signal:any,onUpdate:any,ctx:any){
     const started=performance.now();
     // No automatic action retry: an uncertain click must be observed, not duplicated.
     const result=await native.execute(id,params,onUpdate,ctx,signal);
     // The native bridge also keeps rawContent in UI details. Retain pixels once,
     // in typed content; details hold only a scalar description of each image.
     if(result.details?.rawContent)result.details={...result.details,rawContent:result.details.rawContent.map((b:any)=>b?.type==='image'?{type:b.type,mimeType:b.mimeType,bytes:Buffer.byteLength(b.data??'','base64')}:b)};
     const images=(result.content??[]).filter((b:any)=>b.type==='image');
     if(images.length){status.image_session=true;status.kv_save_supported=false;}
     publish();scalar('kimicu_action',{tool_name:native.mcpToolName,duration_s:(performance.now()-started)/1000,is_error:!!result.isError,output_types:(result.content??[]).map((b:any)=>b.type),image_count:images.length,image_bytes:images.reduce((n:number,b:any)=>n+Buffer.byteLength(b.data??'','base64'),0)});
     return result;
    }});
  }
 }catch(error){await close();scalar('kimicu_connection_error',{error:error instanceof Error?error.message:'Connection failed'});/* Optional GUI capability failure must not disable existing text/coding tools. */}
 pi.on('before_provider_request',(event:any)=>{
  const facts=imageFacts(event.payload?.messages);if(facts.image_count){status.image_request_count++;status.image_session=true;status.kv_save_supported=false;publish();scalar('vision_request',facts);}return undefined;
 });
 pi.on('tool_result',(event:any)=>{if((event.content??[]).some((b:any)=>b.type==='image')){status.image_session=true;status.kv_save_supported=false;publish();}});
}
