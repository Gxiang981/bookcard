// 给静态页拍一组图，放进 docs/shots/（README 用得上）。
//
//   node tests/shoot.mjs <分享包目录>/index.html [输出目录]
import { createRequire } from 'module';
import fs from 'fs';
import path from 'path';

const require = createRequire(import.meta.url);
let chromium;
try {
  ({ chromium } = require('playwright'));
} catch {
  const alt = createRequire('/.tmp-e2e/x.cjs');
  ({ chromium } = alt('playwright'));
}

const PAGE = path.resolve(process.argv[2] || '');
const OUT = path.resolve(process.argv[3] || path.join(path.dirname(PAGE), '..', '..', 'docs', 'shots'));
fs.mkdirSync(OUT, { recursive: true });

let browser;
try { browser = await chromium.launch({ headless: true }); }
catch (e) { browser = await chromium.connectOverCDP('http://127.0.0.1:9222'); }
const ctx = browser.contexts()[0] || await browser.newContext();
const page = await ctx.newPage({ viewport: { width: 1280, height: 800 } });

await page.goto('file://' + PAGE);
await page.waitForTimeout(1200);

await page.screenshot({ path: path.join(OUT, '1-front.png') });

await page.click('#face-front');
await page.waitForTimeout(900);
await page.screenshot({ path: path.join(OUT, '2-back-image-scene.png') });

await page.evaluate(() => { if (typeof openEdit === 'function') openEdit(); });
await page.waitForTimeout(500);
await page.screenshot({ path: path.join(OUT, '3-edit.png') });
await page.keyboard.press('Escape');
await page.waitForTimeout(300);

await page.setViewportSize({ width: 414, height: 860 });
await page.waitForTimeout(500);
await page.click('#drawer-btn').catch(() => {});
await page.waitForTimeout(500);
await page.screenshot({ path: path.join(OUT, '4-phone-drawer.png') });

console.log('shots →', OUT);
await browser.close().catch(() => {});
