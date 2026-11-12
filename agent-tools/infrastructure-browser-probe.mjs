// Cached preflight only; a local blank page, one tiny screenshot, one HEAD.
// Existing browser library owns profile cleanup. No inference or model context.
import {launchBrowser} from './lib/browser.mjs';
const b=await launchBrowser();
try {
  await b.page.setContent('<!doctype html><title>Infrastructure preflight</title>');
  const bytes=await b.page.screenshot({type:'png'});
  let internet=false;
  try {const r=await b.page.goto('https://registry.npmjs.org/',{waitUntil:'commit',timeout:3000});internet=!!r?.ok();}catch{}
  console.log(JSON.stringify({'Chrome launch':true,Screenshot:bytes.length>0,'Browser internet':internet}));
} finally {await b.close();}
