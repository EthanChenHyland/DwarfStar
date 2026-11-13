// Headless UI check against the running Workstation service (http://127.0.0.1:8038). Run through scripts/validate/ui/run.sh.
const { chromium } = await import(process.env.PW);
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto("http://127.0.0.1:8038/#run"); await page.waitForTimeout(1500);
const snap = async (label) => page.evaluate(async (label) => {
  const st = await fetch("/api/state").then(r => r.json());
  const pill = [...document.querySelectorAll(".kv dd .pill")][0];
  return { label, ui: { nav: document.querySelector("#nav-model").innerText + " / " + document.querySelector("#nav-phase").innerText,
           launch: document.querySelector("#launch")?.innerText || null, stop: !!document.querySelector("#stop"), status: pill && pill.innerText },
           controller: { running: st.running_models, active: st.active_profile, selected: st.selected_profile, busy: st.lifecycle.busy,
                         cyberPid: st.models.cyber.pid, healthy: st.models.cyber.healthy } };
}, label);
const waitIdle = async () => { for (let i = 0; i < 120; i++) { await page.waitForTimeout(1000); const busy = await page.evaluate(() => DwarfStar.S.busy || DwarfStar.S.state?.lifecycle?.busy); if (!busy) break; } await page.waitForTimeout(1500); };
const out = [];
await page.click('[data-model="cyber"]'); await page.click('[data-effort="medium"]'); await page.waitForTimeout(400);
await page.click("#launch"); await waitIdle(); out.push(await snap("after Launch Cyber MEDIUM"));
await page.click("#stop"); await waitIdle(); out.push(await snap("after Stop"));
await page.click('[data-effort="low"]'); await page.waitForTimeout(600); out.push(await snap("after selecting LOW"));
await page.click("#launch"); await waitIdle(); out.push(await snap("after Launch Cyber LOW"));
await page.click("#stop"); await waitIdle(); out.push(await snap("after final Stop"));
await browser.close(); console.log(JSON.stringify(out, null, 1));
