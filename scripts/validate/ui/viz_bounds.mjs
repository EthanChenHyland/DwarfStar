// Headless UI check against the running Workstation service (http://127.0.0.1:8038). Run through scripts/validate/ui/run.sh.
// Local UI test: draw the Visualizer with synthetic ticks (test page only) and check every text and MTP pill stays in its panel.
const { chromium } = await import(process.env.PW);
const browser = await chromium.launch({ headless: true });
const results = [];
for (const [label, width, height, model] of [["workstation", 1440, 900, "cyber"], ["screenshot", 1600, 1000, "scope"], ["small", 1100, 700, "swift"], ["small-cyber", 1100, 700, "cyber"]]) {
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 2 });
  await page.goto("http://127.0.0.1:8038/#visualizer");
  await page.waitForTimeout(1500);
  const result = await page.evaluate(async ({ label, model }) => {
    DwarfStar.S.model = model;
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    const P = CanvasRenderingContext2D.prototype, rec = { boxes: [], texts: [], pills: [], cards: [], tokens: [] };
    const oRR = P.roundRect, oFT = P.fillText;
    P.roundRect = function (x, y, w, h, r) { if (r === 9) rec.boxes.push({ x, y, w, h }); if (Math.abs(w - 18) < 0.01 && Math.abs(h - 7) < 0.01) rec.pills.push({ x, y, w, h }); if (this.strokeStyle === "#7fd6a3" && h > 100) rec.cards.push({ x, y, w, h }); if (w === 6 && h === 9) rec.tokens.push({ left: x, right: x + w, y }); return oRR.apply(this, arguments); };
    P.fillText = function (text, x, y) { const w = this.measureText(text).width, a = this.textAlign; const left = a === "right" ? x - w : a === "center" ? x - w / 2 : x; rec.texts.push({ text: String(text), left, right: left + w, y }); return oFT.apply(this, arguments); };
    const t0 = Date.now(); let pos = 1000;
    while (Date.now() - t0 < 2500) {
      const rows = Array.from({ length: 48 }, () => Array.from({ length: 20 }, (_, i) => i % 2 ? 200 : Math.floor(Math.random() * 512)));
      for (const fn of DwarfStar.S.listeners) fn({ kind: "tick", model, n: 2, dp: 3, da: 2, ple: [40, 3600], ms: 40, pos: pos += 2, r: rows, k: 10, t: Date.now() / 1000 });
      await wait(60);
    }
    await wait(300); P.roundRect = oRR; P.fillText = oFT;
    const inside = (t) => rec.boxes.some(b => t.left >= b.x - 0.5 && t.right <= b.x + b.w + 0.5 && t.y >= b.y - 1 && t.y <= b.y + b.h);
    const c = rec.cards[rec.cards.length - 1], inset = 2;
    return { label, viewport: innerWidth + "x" + innerHeight, model, textsDrawn: rec.texts.length, textsOutsidePanels: [...new Set(rec.texts.filter(t => !inside(t)).map(t => t.text))],
             pills: rec.pills.length, pillsOutsideMtpCard: c ? rec.pills.filter(p => p.x < c.x + inset || p.y < c.y + inset || p.x + p.w > c.x + c.w - inset || p.y + p.h > c.y + c.h - inset).length : "no card",
             tokens: rec.tokens.length, tokensOutsidePanels: rec.tokens.filter(t => !inside(t)).length, fonts: getComputedStyle(document.body).fontFamily.slice(0, 30),
             metalValue: (rec.texts.find((t, i, a) => a[i - 1] && a[i - 1].text === "Metal limit") || {}).text, metalLabel: rec.texts.find(t => t.text === "Metal limit") ? "Metal limit " + (rec.texts.find(t => /GiB · plan/.test(t.text)) || {}).text : null,
             horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth || document.querySelector("#main").scrollWidth > document.querySelector("#main").clientWidth };
  }, { label, model });
  await page.screenshot({ path: (process.env.OUT || process.env.TMPDIR || "/tmp") + `/viz-${label}.png` });
  results.push(result);
  await page.close();
}
await browser.close();
console.log(JSON.stringify(results, null, 1));
