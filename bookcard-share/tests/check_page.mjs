// bookcard-share 静态页的浏览器自检。
//
//   node tests/check_page.mjs <分享包目录>/index.html [截图目录]
//
// 需要 playwright：
//   npm i playwright            （或者复用 46_BookCard/.tmp-e2e/node_modules）
//
// 走 file:// 打开 —— 这是这个子项目的核心主张：**双击就能用**，
// 所以自检也必须在 file:// 下跑，不能拿 http 蒙混过关。
import { createRequire } from 'module';
import fs from 'fs';
import path from 'path';

const require = createRequire(import.meta.url);
let chromium;
try {
  ({ chromium } = require('playwright'));
} catch {
  // 退一步：复用 46_BookCard 里已经装好的 playwright
  try {
    const alt = createRequire('/.tmp-e2e/x.cjs');
    ({ chromium } = alt('playwright'));
  } catch {
    console.error('需要 playwright：npm i playwright');
    process.exit(2);
  }
}

const PAGE = path.resolve(process.argv[2] || '');
const SHOTS = process.argv[3] || '/tmp/bookcard-share-shots';
if (!process.argv[2]) { console.error('用法：node tests/check_page.mjs <index.html>'); process.exit(2); }
fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
const step = (name, ok, detail) => {
  results.push({ name, ok });
  console.log((ok ? 'PASS' : 'FAIL') + ' | ' + name + (detail ? ' | ' + JSON.stringify(detail) : ''));
};

let browser;
try {
  browser = await chromium.launch({ headless: true });
} catch (e) {
  console.log('起不了 headless，改连已经开着的 Chrome（CDP）：' + e.message);
  browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
}
const ctx = browser.contexts()[0] || await browser.newContext();
const page = await ctx.newPage();

const errs = [];
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
page.on('pageerror', e => errs.push('pageerror: ' + e.message));
page.on('requestfailed', r => errs.push('reqfail: ' + r.url()));

await page.goto('file://' + PAGE);
await page.waitForTimeout(1200);

// 1. 页面起来了
const front = (await page.textContent('#front-word').catch(() => '')).trim();
step('页面渲染出正面词条', front.length > 0, { front });

// 2. 左面板有词表
const rows = await page.$$eval('#panel-list .row', ns => ns.length).catch(() => -1);
step('左面板列出了词条', rows > 0, { rows });

// 3. tier 计数
const tiers = await page.$$eval('#tiers .tier-chip', ns => ns.map(n => n.textContent.trim())).catch(() => []);
step('顶部 tier 计数有值', tiers.length > 0, { tiers });

// 4. 翻面 → 书页图 + 高亮框
await page.click('#face-front');
await page.waitForTimeout(700);
const imgOk = await page.$eval('#back-body img', n => n.complete && n.naturalWidth > 0).catch(() => false);
step('背面书页图加载成功（webp）', imgOk);
const boxes = await page.$$eval('#back-body div[style*="left"]', ns => ns.length).catch(() => 0);
step('图上有高亮框', boxes > 0, { boxes });
await page.screenshot({ path: SHOTS + '/1-back.png' });

// 5. 四键评分
const keys = await page.$$eval('#actions-row button', ns => ns.map(n => n.textContent.trim()));
step('翻面后有四颗评分键', keys.length >= 4, { keys });

// 6. 编辑面板
await page.evaluate(() => { if (typeof openEdit === 'function') openEdit(); });
await page.waitForTimeout(400);
const editVisible = await page.$eval('#edit', n => getComputedStyle(n).display !== 'none').catch(() => false);
step('编辑面板能打开', editVisible);
await page.screenshot({ path: SHOTS + '/2-edit.png' });

// 7. 改 front → 保存 → 生效 → 刷新还在
const orig = (await page.textContent('#front-word')).trim();
await page.fill('#e-front', orig + '·改');
await page.click('#e-save');
await page.waitForTimeout(500);
step('改完 front 立刻生效', (await page.textContent('#front-word')).includes('·改'));
await page.reload();
await page.waitForTimeout(1000);
step('刷新后改动还在', (await page.textContent('#front-word')).includes('·改'));

const store = await page.evaluate(() => {
  const k = Object.keys(localStorage).find(k => k.startsWith('bookcard-share:'));
  const v = k ? JSON.parse(localStorage.getItem(k)) : null;
  return v ? { key: k, cards: Object.keys(v.cards || {}).length } : null;
});
step('改动写进了 localStorage', !!store && store.cards > 0, store);

// 8. 评分 → 状态 + review_log
await page.click('#face-front');
await page.waitForTimeout(400);
await page.click('#actions-row button:nth-child(3)').catch(() => {});
await page.waitForTimeout(400);
const store2 = await page.evaluate(() => {
  const k = Object.keys(localStorage).find(k => k.startsWith('bookcard-share:'));
  const v = k ? JSON.parse(localStorage.getItem(k)) : null;
  return v ? { states: Object.keys(v.state || {}).length, log: (v.review_log || []).length } : null;
});
step('评分写进学习状态和 review_log', !!store2 && store2.log === 1, store2);

// 9. 导出 edits.json
const [download] = await Promise.all([
  page.waitForEvent('download', { timeout: 8000 }).catch(() => null),
  page.click('#btn-export'),
]);
let exportOk = false, exportInfo = null;
if (download) {
  const j = JSON.parse(fs.readFileSync(await download.path(), 'utf-8'));
  exportOk = j.format === 'bookcard-share/edits@1' && j.cards.length > 0 && j.review_log.length === 1;
  exportInfo = { format: j.format, cards: j.cards.length, log: j.review_log.length };
}
step('导出 edits.json', exportOk, exportInfo);

// 10. 窄屏抽屉
await page.setViewportSize({ width: 420, height: 800 });
await page.waitForTimeout(400);
await page.click('#drawer-btn').catch(() => {});
await page.waitForTimeout(400);
await page.screenshot({ path: SHOTS + '/3-phone.png' });
const drawerOk = await page.$eval('#drawer', n => getComputedStyle(n).transform !== 'none').catch(() => false);
step('窄屏抽屉能拉开', drawerOk);

step('无 JS 报错', errs.length === 0, { errs: errs.slice(0, 6) });

console.log('\n' + (results.every(r => r.ok) ? '✅ 全部通过' : '❌ 有失败项'));
console.log('截图在 ' + SHOTS);
await browser.close().catch(() => {});
process.exit(results.every(r => r.ok) ? 0 : 1);
