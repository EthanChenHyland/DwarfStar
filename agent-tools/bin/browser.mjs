#!/usr/bin/env node
import {readFileSync,writeFileSync,mkdirSync,existsSync} from 'node:fs';
import {dirname} from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {launchBrowser,capture,workspacePath,withServer} from '../lib/browser.mjs';
if(process.argv.includes('--help')||process.argv.length<3){
  console.log(`dwarf-browser [--full] PLAN.json | URL [screenshot.png]
Plan: {url|file, headed:false, viewport:{width:1280,height:800}, output:"browser-receipt.json", screenshot:"page.png", dom:"page.html", server?:{command,url,timeout_ms}, steps:[...]}
Steps: {op:"click",selector|text}, {op:"type",selector,value}, {op:"press",key}, {op:"wait",selector}, {op:"exists",selector}, {op:"eval",expression}, {op:"title"}, {op:"reload"}, {op:"screenshot",path}, {op:"dom",selector?}, {op:"viewport",width,height}.
Uses Playwright + isolated Chrome. All artifact/file paths workspace-local. Always closes browser/server. For Puppeteer or custom scripts: dwarf-node script.mjs; import {launchBrowser,capture,withServer} from the agent-tools lib/browser.mjs path.`);process.exit(0);
}
const args=process.argv.slice(2).filter(x=>x!=='--full'),input=args[0];
const plan=/^(https?:|file:)/.test(input)?{url:input,screenshot:args[1]}:JSON.parse(readFileSync(workspacePath(input),'utf8'));
if(plan.file)plan.url=pathToFileURL(workspacePath(plan.file)).href;
const receipt={started_at:new Date().toISOString(),engine:'playwright',results:[]};
let session;
const cleanup=async()=>{await session?.close();};
for(const signal of ['SIGTERM','SIGINT'])process.once(signal,async()=>{await cleanup();process.exit(130);});
try{
  await withServer(plan.server,async()=>{
    session=await launchBrowser(plan);const page=session.page;receipt.browser=session.executablePath;receipt.events=capture(page);
    page.setDefaultTimeout(15000);await page.goto(plan.url,{waitUntil:'load',timeout:45000});
    let interactionPending=false,postInteractionAssertions=0,negativeDomObservations=0;
    for(const step of plan.steps??[]){
      const loc=step.selector?page.locator(step.selector):step.text?page.getByText(step.text,{exact:true}):null;
      let value=true;
      switch(step.op){
        case 'click':await loc.click();break;
        case 'type':await loc.fill(step.value);break;
        case 'press':if(loc)await loc.press(step.key);else await page.keyboard.press(step.key);break;
        case 'wait':await loc.waitFor({state:'visible'});break;
        case 'exists':value=await loc.count();if(!value)throw new Error('Missing element: '+step.selector);break;
        case 'eval':value=await page.evaluate(step.expression);break;
        case 'title':value=await page.title();break;
        case 'reload':await page.reload();break;
        case 'viewport':await page.setViewportSize({width:step.width,height:step.height});break;
        case 'screenshot':await page.screenshot({path:workspacePath(step.path),fullPage:true});break;
        case 'dom':value=step.selector?await loc.evaluate(e=>e.outerHTML):await page.content();break;
        default:throw new Error('Unknown step: '+step.op);
      }receipt.results.push({op:step.op,value});
      if(['click','type','press'].includes(step.op))interactionPending=true;
      if(step.op==='eval'&&value===true&&interactionPending&&/\b(?:document|window|globalThis|canvas|scene|app)\b/.test(step.expression)){postInteractionAssertions++;interactionPending=false;}
      if(step.op==='eval'&&value===false&&/\b(?:document|window|globalThis|canvas|scene|app)\b/.test(step.expression))negativeDomObservations++;
    }
    if(plan.screenshot)await page.screenshot({path:workspacePath(plan.screenshot),fullPage:true});
    if(plan.dom)writeFileSync(workspacePath(plan.dom),await page.content());
    receipt.title=await page.title();receipt.url=page.url();receipt.ok=true;
    // Observational metadata only: inability to observe it must never fail a tool.
    // No agent-supplied completion score/feature checklist is accepted.
    if(process.env.DWARFSTAR_HARNESS_EFFICIENCY==='1'||process.env.DWARFSTAR_PASSIVE_BROWSER_RECEIPTS==='1')try{
      const ui=await page.evaluate(()=>{
        const visible=[...document.body.querySelectorAll('*')].some(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>10&&r.height>10&&s.visibility!=='hidden'&&s.display!=='none'&&(e.textContent?.trim().length??0)>0;});
        const canvases=[...document.querySelectorAll('canvas')].filter(c=>{const r=c.getBoundingClientRect();return r.width>10&&r.height>10;});
        let variedCanvas=false;
        for(const canvas of canvases)try{
          const probe=document.createElement('canvas');probe.width=16;probe.height=16;const context=probe.getContext('2d');context.drawImage(canvas,0,0,16,16);const data=context.getImageData(0,0,16,16).data,colors=new Set();
          for(let i=0;i<data.length;i+=4)colors.add([data[i],data[i+1],data[i+2],data[i+3]].join(','));
          if(colors.size>4)variedCanvas=true;
        }catch{/* Tainted/unreadable/cleared canvas means insufficient evidence, not a tool failure. */}
        return {visible,bodyText:(document.body.innerText??'').trim().length,renderObserved:canvases.length===0||variedCanvas};
      });
      const url=new URL(receipt.url),local=url.protocol==='file:'||['127.0.0.1','localhost','[::1]'].includes(url.hostname);
      const entrypoint=plan.file??(url.protocol==='file:'?fileURLToPath(url):'index.html');
      const events=receipt.events;
      receipt.validation={source:'dwarfstar-browser-validation-v1',local,entrypoint,entrypoint_exists:existsSync(workspacePath(entrypoint)),primary_ui_visible:ui.visible&&ui.bodyText>0&&ui.renderObserved,render_surface_observed:ui.renderObserved,primary_interaction_exercised:postInteractionAssertions>0,functional_assertions:postInteractionAssertions,negative_dom_observations:negativeDomObservations,blocking_runtime_error:events.pageErrors.length>0||events.console.some(x=>['error','assert'].includes(x.type))||events.failedRequests.length>0||events.responses.some(x=>x.status>=400),evidence_complete:Object.values(events.dropped??{}).every(x=>x===0)};
    }catch{receipt.validation={source:'dwarfstar-browser-validation-v1',evidence_complete:false};}
  });
}catch(e){receipt.ok=false;receipt.error=String(e);process.exitCode=1;}
finally{
  await cleanup();receipt.ended_at=new Date().toISOString();
  if(plan.output){const output=workspacePath(plan.output);mkdirSync(dirname(output),{recursive:true});writeFileSync(output,JSON.stringify(receipt,null,2)+'\n');}
  // Passive-only controls keep native stdout unchanged; evidence stays in the
  // existing requested receipt file and travels as private tool details.
  const {validation,...native}=receipt;
  console.log(JSON.stringify(process.env.DWARFSTAR_PASSIVE_BROWSER_RECEIPTS==='1'&&process.env.DWARFSTAR_HARNESS_EFFICIENCY!=='1'?native:receipt));
}
