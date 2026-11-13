// Regenerates docs/assets screenshots from the real Workstation with Scope LOW running and one real agent turn
// (shell tool). Starts and stops the model; the demo chat is deleted again afterwards (moved aside). Home-directory
// paths in visible text are written as "~" before each capture, the same convention as Copy diagnostics.
const { chromium } = await import(process.env.PW);
const OUT = new URL("../../../docs/assets/", import.meta.url).pathname;
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
const errors = []; page.on("pageerror", (e) => errors.push(String(e)));
const api = (path, body) => page.evaluate(([p, b]) => fetch(p, b ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) } : {}).then(r => r.json()), [path, body]);
const overflow = [];
const shot = async (name) => {
  overflow.push(...(await page.evaluate((n) => {
    const main = document.querySelector("#main").getBoundingClientRect(), out = [];
    for (const e of document.querySelectorAll(".view.active *")) {
      const r = e.getBoundingClientRect();
      if (r.width && r.right > main.right + 1 && getComputedStyle(e).position !== "fixed") out.push(`${n}: ${e.tagName.toLowerCase()}.${e.className} ${Math.round(r.right - main.right)}px`);
    }
    if (document.documentElement.scrollWidth > innerWidth) out.push(`${n}: page scrolls horizontally`);
    return out.slice(0, 5);
  }, name)));
  await page.evaluate(() => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) n.nodeValue = n.nodeValue.replace(/\/Users\/[^/\s]+/g, "~");
    document.querySelectorAll("[title]").forEach((e) => e.title = e.title.replace(/\/Users\/[^/\s]+/g, "~"));
  });
  await page.screenshot({ path: OUT + name });
};
const go = async (view, wait = 1200) => { await page.evaluate((v) => DwarfStar.show(v), view); await page.waitForTimeout(wait); };
await page.goto("http://127.0.0.1:8038/#run"); await page.waitForTimeout(1500);
await api("/api/switch", { profile: "scope-low" });
await page.reload(); await page.waitForTimeout(2000);
await shot("workstation-run.png");
await page.click('[data-model="swift"]'); await page.waitForTimeout(400); await shot("model-selector.png");
await page.click('[data-model="scope"]');
const chat = (await api("/api/chats", { profile: "scope-low" })).chat;
await go("chats"); await page.click(`.chat-item[data-id="${chat.id}"]`); await page.waitForTimeout(600);
await page.fill("#chat-input", "Use the shell to list the files in the current folder with sizes, then write a two-line summary of what is there.");
await page.press("#chat-input", "Enter");
await go("visualizer", 300);
for (let i = 0; i < 120; i++) { await page.waitForTimeout(500); const live = await api("/api/live"); if (live.live?.request?.phase === "DECODE" || live.live?.request?.phase === "decode") break; }
await page.waitForTimeout(2500); await shot("visualizer.png");
await go("live", 3000); await shot("live.png");
for (let i = 0; i < 400; i++) { await page.waitForTimeout(1000); if (!(await api("/api/chats/" + chat.id)).progress) break; }
await api(`/api/chats/${chat.id}/rename`, { title: "Folder overview" });
await api(`/api/chats/${chat.id}/organize`, { starred: true, tags: ["demo"] });
await go("chats"); await page.click(`.chat-item[data-id="${chat.id}"]`); await page.waitForTimeout(1000); await shot("chats.png");
await go("visualizer", 1500);
await page.evaluate(() => document.querySelector("#viz-requests")?.scrollIntoView()); await page.waitForTimeout(400); await shot("visualizer-requests.png");
await go("models"); await page.click('[data-tab="cyber"]'); await page.waitForTimeout(800); await shot("models-stats.png");
await go("diagnostics", 2000); await shot("diagnostics.png");
await go("settings", 1200); await shot("settings.png");
await go("run"); await page.keyboard.press("Meta+k"); await page.fill("#palette-input", "cyber"); await page.waitForTimeout(300); await shot("palette.png");
await page.keyboard.press("Escape");
await api(`/api/chats/${chat.id}/delete`, { confirm: chat.id });
await api("/api/stop", {});
await browser.close(); console.log(JSON.stringify({ errors, overflow }));
