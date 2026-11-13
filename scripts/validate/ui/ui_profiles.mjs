// Headless UI check against the running Workstation service (http://127.0.0.1:8038). Run through scripts/validate/ui/run.sh.
const { chromium } = await import(process.env.PW);
const browser = await chromium.launch({ headless: true });
const out = {};
for (const [w, h] of [[1440, 900], [1100, 700]]) {
  const page = await browser.newPage({ viewport: { width: w, height: h } });
  await page.goto("http://127.0.0.1:8038/#run"); await page.waitForTimeout(1500);
  const r = await page.evaluate(async () => {
    const wait = (ms) => new Promise(r => setTimeout(r, ms)); const seen = [];
    for (const card of [...document.querySelectorAll("[data-model]")].map(b => b.dataset.model)) {
      document.querySelector(`[data-model="${card}"]`).click(); await wait(250);
      for (const effort of [...document.querySelectorAll("[data-effort]")].map(b => b.dataset.effort)) {
        document.querySelector(`[data-effort="${effort}"]`).click(); await wait(250);
        const profile = DwarfStar.S.catalog.profiles.find(p => p.model === DwarfStar.S.model && p.reasoning_effort === DwarfStar.S.effort);
        seen.push(profile && profile.id);
      }
    }
    const chats = document.querySelector('#nav [data-view="chats"]'); chats.click(); await wait(800);
    return { profiles: seen, chatItems: document.querySelectorAll(".chat-item").length,
             hscroll: document.documentElement.scrollWidth > document.documentElement.clientWidth || document.querySelector("#main").scrollWidth > document.querySelector("#main").clientWidth };
  });
  out[`${w}x${h}`] = r; await page.close();
}
await browser.close(); console.log(JSON.stringify(out));
