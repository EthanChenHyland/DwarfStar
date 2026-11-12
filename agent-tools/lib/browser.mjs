import {existsSync,mkdtempSync,rmSync,realpathSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn,spawnSync} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';
export const tools=resolve(dirname(fileURLToPath(import.meta.url)),'..');
export function browserExecutable({headed=false}={}){
  const isolated=headed?join(tools,'cache/browsers/chromium-1248/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'):join(tools,'cache/browsers/chromium_headless_shell-1248/chrome-headless-shell-mac-arm64/chrome-headless-shell');
  if(existsSync(isolated))return isolated;
  return ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','/Applications/Chromium.app/Contents/MacOS/Chromium',join(process.env.HOME,'Applications/Google Chrome.app/Contents/MacOS/Google Chrome')].find(existsSync)??null;
}
export async function launchBrowser({engine='playwright',headed=false,viewport={width:1280,height:800}}={}){
  const executablePath=browserExecutable({headed});if(!executablePath)throw new Error('Chrome/Chromium unavailable; explicitly install Chromium into PLAYWRIGHT_BROWSERS_PATH');
  const profile=mkdtempSync(join(process.env.TMPDIR??tmpdir(),'dwarf-browser-'));
  // macOS rejects Chrome's second sandbox initialization inside our inherited
  // Seatbelt profile (verified SIGSEGV). Keep the enclosing process-tree sandbox.
  const confined=process.env.V7_SEATBELT_ACTIVE==='1';
  const args=process.platform==='darwin'?['--use-mock-keychain',...(confined?['--no-sandbox']:[])]:[];
  let browser,context,page;
  try{
    if(engine==='puppeteer'){
      const {default:puppeteer}=await import('puppeteer');
      browser=await puppeteer.launch({executablePath,headless:!headed,userDataDir:profile,args,timeout:20000});
      page=await browser.newPage();await page.setViewport(viewport);
    }else{
      const {chromium}=await import('playwright');
      context=await chromium.launchPersistentContext(profile,{executablePath,headless:!headed,args,viewport,chromiumSandbox:!confined,timeout:20000});
      page=context.pages()[0]??await context.newPage();
    }
    // Record exact profile/PID ancestry in the existing host task registry.
    // Failure is an observability warning, never a reason to switch user profiles.
    let ownership={ok:false,error:'No registered task owner'};
    if(process.env.DWARFSTAR_TASK_ID){
      try{const r=spawnSync(process.env.DWARFSTAR_PYTHON??'python3',['-B','-m','dwarfstar.browser_ownership'],{input:JSON.stringify({task_id:process.env.DWARFSTAR_TASK_ID,parent:process.pid,profile,engine,headed}),env:process.env,encoding:'utf8',timeout:12000,maxBuffer:262144});ownership=JSON.parse(r.stdout||'{}');}catch(e){ownership={ok:false,error:String(e)};}
    }
    let closed=false;
    const close=async()=>{if(closed)return;closed=true;try{await (context??browser).close();}finally{rmSync(profile,{recursive:true,force:true});}};
    return {page,browser,context,executablePath,profile,ownership,headed,mode:headed?'headed':'headless',close};
  }catch(error){try{await (context??browser)?.close();}finally{rmSync(profile,{recursive:true,force:true});}throw error;}
}
export function capture(page){
  const enhanced=process.env.DWARFSTAR_HARNESS_EFFICIENCY==='1';
  const optional=fn=>{try{return fn();}catch{return undefined;}};
  const events={console:[],pageErrors:[],failedRequests:[],responses:[],...(enhanced?{dropped:{console:0,pageErrors:0,failedRequests:0,responses:0}}:{})};
  // Bounded metadata only: no cookies, authorization headers or response bodies.
  const keep=(key,value)=>{if(events[key].length<500)events[key].push(value);else if(enhanced)events.dropped[key]++;};
  page.on('console',m=>keep('console',{type:m.type(),text:m.text(),...(enhanced?{location:optional(()=>m.location?.())}:{})}));
  page.on('pageerror',e=>keep('pageErrors',enhanced?(optional(()=>e.stack)??String(e)):String(e)));
  page.on('requestfailed',r=>keep('failedRequests',{url:r.url(),method:r.method(),failure:r.failure()}));
  page.on('response',r=>keep('responses',{url:r.url(),status:r.status(),...(enhanced?{resource_type:optional(()=>r.request().resourceType?.())??'unknown'}:{})}));
  return events;
}
export function workspacePath(path){
  const root=realpathSync(process.env.DWARFSTAR_WORKSPACE??process.cwd());const target=resolve(root,path);
  if(target!==root&&!target.startsWith(root+'/'))throw new Error('Artifact/input must be in current workspace');
  let parent=target;while(!existsSync(parent))parent=dirname(parent);
  const real=realpathSync(parent);if(real!==root&&!real.startsWith(root+'/'))throw new Error('External symlink blocked');
  return target;
}
export async function withServer(config,fn){
  if(!config)return fn();
  const url=new URL(config.url);if(!['127.0.0.1','localhost','[::1]'].includes(url.hostname))throw new Error('Server readiness URL must be localhost');
  const child=spawn('/bin/zsh',['-f','-c',config.command],{cwd:process.cwd(),env:process.env,detached:true,stdio:['ignore','pipe','pipe']});
  let logs='';for(const stream of [child.stdout,child.stderr])stream.on('data',b=>{logs=(logs+b).slice(-12000);});
  let launchError;child.on('error',e=>launchError=e);
  const stop=()=>{try{process.kill(-child.pid,'SIGTERM');}catch{}};
  process.on('SIGTERM',stop);process.on('SIGINT',stop);
  try{
    const deadline=Date.now()+Math.min(config.timeout_ms??30000,120000);let ready=false;
    while(Date.now()<deadline){if(launchError)throw launchError;if(child.exitCode!==null)throw new Error('Server exited: '+logs);
      try{const response=await fetch(config.url,{signal:AbortSignal.timeout(1000)});if(response.ok){ready=true;break;}}catch{}
      await delay(150);
    }
    if(!ready)throw new Error('Local server readiness timeout: '+logs);
    return await fn();
  }finally{
    stop();process.off('SIGTERM',stop);process.off('SIGINT',stop);
    await delay(200);try{process.kill(-child.pid,'SIGKILL');}catch{}
    if(child.exitCode===null&&child.signalCode===null)await new Promise(r=>child.once('close',r));
  }
}
