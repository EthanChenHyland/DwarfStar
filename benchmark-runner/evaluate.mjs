// External evaluation only, AFTER ACP has terminated. Never writes source files.
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {launchBrowser,capture} from '../agent-tools/lib/browser.mjs';
const plan=JSON.parse(readFileSync(process.argv[2],'utf8'));
const output=resolve(plan.output);mkdirSync(output,{recursive:true});
const result={schema:1,evaluation_started_at:new Date().toISOString(),entrypoint:plan.entrypoint,
  primary_interaction_success:'Not exposed',interaction_attempted:false,source_modified:false,
  note:'Rendered/load evidence is not semantic correctness or subjective visual acceptance.'};
let session;
const close=async()=>{await session?.close();};
for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await close();process.exit(130);});
try{
  const url=new URL(plan.url);
  if(!['127.0.0.1','localhost'].includes(url.hostname)||['8000','8010','8020','8021','8030'].includes(url.port))throw Error('Evaluator URL must be its workspace HTTP server');
  session=await launchBrowser({viewport:{width:1280,height:800}});
  const page=session.page;result.events=capture(page);
  const response=await page.goto(plan.url,{waitUntil:'load',timeout:45000});
  await page.waitForTimeout(Math.min(plan.settle_ms??1500,5000));
  result.page_load_success=response?.ok()??false;result.http_status=response?.status();result.url=page.url();
  result.dom_load_state=await page.evaluate(()=>document.readyState);
  result.primary_page_rendered=await page.evaluate(()=>{
    const b=document.body;if(!b)return false;
    return [...b.querySelectorAll('*')].some(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>10&&r.height>10&&s.display!=='none'&&s.visibility!=='hidden'&&(e.tagName==='CANVAS'||e.tagName==='SVG'||e.tagName==='IMG'||(e.textContent??'').trim().length>0);});
  });
  for(const key of plan.press_keys??[]){await page.keyboard.press(key);result.interaction_attempted=true;}
  if(plan.interaction_assertion){result.primary_interaction_success=(await page.evaluate(plan.interaction_assertion))===true;}
  result.screenshot_path=join(output,'final.png');await page.screenshot({path:result.screenshot_path,fullPage:true});
  writeFileSync(join(output,'dom.html'),await page.content());
  result.console_errors=result.events.console.filter(x=>['error','assert'].includes(x.type)).length+result.events.pageErrors.length;
  result.console_warnings=result.events.console.filter(x=>x.type==='warning').length;
  result.failed_network_requests=result.events.failedRequests.length;
  result.http_error_responses=result.events.responses.filter(x=>x.status>=400).length;
  result.event_capture_limit=500;result.event_counts_are_lower_bounds=Object.values(result.events).some(x=>Array.isArray(x)&&x.length>=500);
  result.primary_validation=result.page_load_success&&result.primary_page_rendered?'LOAD/RENDER OBSERVED':'LOAD/RENDER FAILED';
  if(result.primary_interaction_success===false)result.primary_validation='DECLARED INTERACTION FAILED';
}catch(error){result.error=String(error);result.primary_validation='EVALUATION UNAVAILABLE/FAILED';}
finally{await close();result.evaluation_ended_at=new Date().toISOString();writeFileSync(join(output,'validation.json'),JSON.stringify(result,null,2)+'\n');}
