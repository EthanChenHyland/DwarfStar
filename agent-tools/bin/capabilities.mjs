#!/usr/bin/env node
import {existsSync,readFileSync,writeFileSync,renameSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import os from 'node:os';
import {join} from 'node:path';
import {tools,browserExecutable} from '../lib/browser.mjs';
const command=(name,args=['--version'])=>{try{return execFileSync(name,args,{encoding:'utf8',timeout:5000,stdio:['ignore','pipe','ignore']}).trim().split('\n')[0];}catch{return null;}};
const version=name=>{try{return JSON.parse(readFileSync(join(tools,'node_modules',name,'package.json'),'utf8')).version;}catch{return null;}};
const path=join(tools,'state/capabilities.json');let previous={};try{previous=JSON.parse(readFileSync(path,'utf8'));}catch{}
let network=previous.network??{status:'untested'};
if(process.argv.includes('--network')){
  const urls={github:'https://github.com/',raw_github:'https://raw.githubusercontent.com/microsoft/TypeScript/main/README.md',npm:'https://registry.npmjs.org/is-number',cdnjs:'https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js',mdn:'https://developer.mozilla.org/en-US/docs/Web/JavaScript',three:'https://threejs.org/docs/',node:'https://nodejs.org/api/'};
  const rows=await Promise.all(Object.entries(urls).map(async([name,url])=>{
    let shell,node;
    try{const code=execFileSync('/usr/bin/curl',['--silent','--show-error','--location','--max-time','18','--output','/dev/null','--write-out','%{http_code}',url],{encoding:'utf8',timeout:20000}).trim();shell={ok:Number(code)>=200&&Number(code)<400,http_status:Number(code)};}catch(e){shell={ok:false,error:String(e).slice(0,180)};}
    try{const response=await fetch(url,{signal:AbortSignal.timeout(18000)});node={ok:response.ok,http_status:response.status};await response.body?.cancel();}catch(e){node={ok:false,error:String(e)};}
    return [name,{url,shell,node}];
  }));
  network={checked_at:new Date().toISOString(),source:'actual DwarfStar OS sandbox',resources:Object.fromEntries(rows),browser:previous.network?.browser??{status:'untested'}};
  network.status=rows.some(([,r])=>r.shell.ok&&r.node.ok)?'available':'unavailable';
}
const validation=(()=>{try{return JSON.parse(readFileSync(join(tools,'state/validation.json'),'utf8'));}catch{return null;}})();
const result={schema:1,detected_at:new Date().toISOString(),os:os.type()+' '+os.release(),architecture:os.arch(),node:process.version,npm:command('npm'),bun:command('bun'),pnpm:command('pnpm'),git:command('git'),playwright:version('playwright'),puppeteer:version('puppeteer'),browser_executable:browserExecutable(),browser_version:browserExecutable()?command(browserExecutable()):null,agent_tools:tools,browser_cache:join(tools,'cache/browsers'),network,network_receipt_stale:network.checked_at?Date.now()-Date.parse(network.checked_at)>86400000:true,browser_automation:validation?.ok===true?'validated; see tested_at':'installed; validation pending',tested_at:validation?.at??null,features:validation?.features??{},policy:{workspace_writes:true,public_https:true,server_binding:'Use 127.0.0.1 explicitly; obvious public bind commands require approval. macOS Seatbelt localhost also matches machine-local interfaces; arbitrary-code bind addresses are not strictly enforced',workspace_package_installs:'automatic + logged',agent_tools_installs:'automatic + logged',global_installs:'explicit operator approval; blocked in sandbox',sudo:'explicit operator approval; blocked in sandbox',credentials:'not exposed',browser_profile:'ephemeral; --use-mock-keychain on macOS'},scope:'Enhanced tooling enabled for DwarfStar Scope Coding 128K'};
if(process.argv.includes('--network')||!process.argv.includes('--read-only')){writeFileSync(path+'.tmp',JSON.stringify(result,null,2)+'\n');renameSync(path+'.tmp',path);}
console.log(JSON.stringify(result));
