// bookcard-share「读原书」书视图的浏览器自检（跑真实分享包，file:// 下）。
//
//   node tests/check_bookview.mjs <分享包目录>/index.html [截图目录]
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
const SHOTS = path.resolve(process.argv[3] || '/tmp/bookcard-share-bookview');
if (!process.argv[2]) { console.error('用法：node tests/check_bookview.mjs <index.html>'); process.exit(2); }
fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
const step = (name, ok, detail) => {
  results.push({ name, ok });
  console.log((ok ? 'PASS' : 'FAIL') + ' | ' + name + (detail ? ' | ' + JSON.stringify(detail) : ''));
};

let browser;
try { browser = await chromium.launch({ headless: true }); }
catch (e) { browser = await chromium.connectOverCDP('http://127.0.0.1:9222'); }
const ctx = browser.contexts()[0] || await browser.newContext();
const page = await ctx.newPage({ viewport: { width: 1440, height: 900 } });

const errs = [];
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
page.on('pageerror', e => errs.push('pageerror: ' + e.message));
page.on('requestfailed', r => errs.push('reqfail: ' + r.url()));

await page.goto('file://' + PAGE);
await page.waitForTimeout(1500);

// 1. 进书视图（左栏点最上面那一级）
/* 进书视图：左栏「读原书」入口（词库那一级就有）。 */
await page.evaluate(() => { const l = DATA.libraries[0]; location.hash = '#/lib/' + l.id; });
await page.waitForTimeout(1200);
const bookEntry = await page.$('#panel-list .row.book-entry');
if (bookEntry) { await bookEntry.click(); }
else { await page.evaluate(() => location.hash = '#/book/' + DATA.book.id); }
await page.waitForTimeout(1800);

const bvOn = await page.$eval('#bookview', n => n.classList.contains('on')).catch(() => false);
step('点书那一级 → 进书视图', bvOn);
step('hash 变成 #/book/<id>', /#\/book\//.test(await page.evaluate(() => location.hash)),
     { hash: await page.evaluate(() => location.hash) });

// 2. 大图
const imgOk = await page.$eval('#bv-img', n => n.complete && n.naturalWidth > 0).catch(() => false);
step('大图加载成功', imgOk);
const pos = await page.textContent('#bv-pos').catch(() => '');
step('大图有页码标签', /p\.\d+ \/ \d+/.test(pos || ''), { pos });

// 3. 目录
const tocRows = await page.$$eval('#bv-toc-list .bv-toc-row', ns => ns.length).catch(() => 0);
step('目录有条目', tocRows > 0, { rows: tocRows });

// 4. 缩略图网格：列数自适应 + 数量 = 全书页数
const thumbCount = await page.$$eval('#bv-thumbs .bv-thumb', ns => ns.length).catch(() => 0);
const cols = await page.$eval('#bv-thumbs', n => getComputedStyle(n).gridTemplateColumns.split(' ').length).catch(() => 0);
const thumbW = await page.$eval('#bv-thumbs .bv-thumb', n => Math.round(n.getBoundingClientRect().width)).catch(() => 0);
step('缩略图铺满全书', thumbCount > 100, { thumbCount });
step('缩略图按宽度铺排（横向滚动）', cols > 1 && thumbW > 40, { cols, thumbW });

// 5. 点缩略图 → 切页
await page.click('#bv-thumbs .bv-thumb:nth-child(30)');
await page.waitForTimeout(900);
const pos2 = await page.textContent('#bv-pos').catch(() => '');
step('点缩略图切到那一页', pos2 !== pos, { before: pos, after: pos2 });

// 6. 翻页（大图区的按钮已去掉，统一用键盘 ←→）
await page.keyboard.press('ArrowRight');
await page.waitForTimeout(700);
const pos3 = await page.textContent('#bv-pos').catch(() => '');
step('键盘 → 翻到下一页', pos3 !== pos2, { before: pos2, after: pos3 });
const bigPagerGone = await page.$('#bv-next');
step('大图区不再有翻页按钮（跟缩略图条不重复）', bigPagerGone === null);

// 7. 跳页码
await page.fill('#bv-jump-input', '200');
await page.press('#bv-jump-input', 'Enter');
await page.waitForTimeout(900);
const pos4 = await page.textContent('#bv-pos').catch(() => '');
step('跳页码到 p.200', /p\.200\b/.test(pos4 || ''), { pos4 });

// 8. 缩略图条左右按钮 = 滚一屏
/* 缩略图条是横向滚的（grid-auto-flow: column），所以看 scrollLeft。 */
const before = await page.$eval('#bv-thumbs', n => n.scrollLeft);
await page.click('#bv-scroll-next').catch(() => {});
await page.waitForTimeout(700);
const after = await page.$eval('#bv-thumbs', n => n.scrollLeft);
step('缩略图条 › 翻一屏', after > before, { before, after });

// 9. 点大图 → 放大
await page.click('#bv-img');
await page.waitForTimeout(700);
const fsOn = await page.$eval('#imgfs', n => getComputedStyle(n).display !== 'none').catch(() => false);
step('点大图能放大', fsOn);
await page.screenshot({ path: SHOTS + '/05-fullscreen.png' });
await page.keyboard.press('Escape');
await page.waitForTimeout(400);

// 9.5 点目录页号 → 跳到对应页
const tocPageBefore = await page.textContent('#bv-pos').catch(() => '');
await page.click('#bv-toc-list .bv-toc-row:nth-child(3) .bv-toc-page');
await page.waitForTimeout(800);
const tocPageAfter = await page.textContent('#bv-pos').catch(() => '');
step('点目录页号跳到对应页', tocPageAfter !== tocPageBefore, { before: tocPageBefore, after: tocPageAfter });

// 10. 目录可编辑
const firstTitle = await page.textContent('#bv-toc-list .bv-toc-title').catch(() => '');
await page.click('#bv-toc-list .bv-toc-title');
await page.waitForTimeout(300);
const editing = await page.$('#bv-toc-list input');
if (editing) {
  await editing.fill('改过的目录标题');
  await editing.press('Enter');
  await page.waitForTimeout(400);
}
const afterTitle = await page.textContent('#bv-toc-list .bv-toc-title').catch(() => '');
step('目录标题能改', afterTitle.includes('改过的'), { before: firstTitle, after: afterTitle });

// 11. offset 能改（口径：offset = 前置页数，正数；印刷页 = PDF 页 − offset）
await page.fill('#bv-offset', '4');
await page.press('#bv-offset', 'Enter');
await page.waitForTimeout(400);
const printed = await page.textContent('#bv-toc-list .bv-toc-row').catch(() => '');
// 第一条是 p.8 → 印刷页 = 8 − 4 = 4
step('offset 能改并影响印刷页显示', printed.includes('印刷 4'), { row: printed.trim().slice(0, 60) });
// 负数输入归一成正数（老口径的数据也不会错）
await page.fill('#bv-offset', '-4');
await page.press('#bv-offset', 'Enter');
await page.waitForTimeout(400);
const offVal = await page.$eval('#bv-offset', n => n.value).catch(() => '');
const printed2 = await page.textContent('#bv-toc-list .bv-toc-row').catch(() => '');
step('offset 负数归一成正数', offVal.trim() === '4' && printed2.includes('印刷 4'),
     { offset: offVal, row: printed2.trim().slice(0, 40) });

// 12. 刷新后目录改动还在
await page.reload();
await page.waitForTimeout(1500);
const afterReload = await page.evaluate(() => {
  const k = Object.keys(localStorage).find(k => k.startsWith('bookcard-share:'));
  return k ? JSON.parse(localStorage.getItem(k)) : null;
});
step('目录改动进了 localStorage', !!afterReload && !!afterReload.toc,
     { toc: afterReload && afterReload.toc ? afterReload.toc.entries.length : 0 });

// 13. 导出 edits.json 带上目录
await page.evaluate(() => location.hash = '#/book/' + DATA.book.id);
await page.waitForTimeout(1200);
const [download] = await Promise.all([
  page.waitForEvent('download', { timeout: 8000 }).catch(() => null),
  page.click('#btn-export'),
]);
let exportOk = false, exportInfo = null;
if (download) {
  const j = JSON.parse(fs.readFileSync(await download.path(), 'utf-8'));
  exportOk = !!(j.toc && j.toc.entries.length);
  exportInfo = { tocEntries: j.toc ? j.toc.entries.length : 0, offset: j.toc ? j.toc.offset : null };
}
step('导出 edits.json 带上目录', exportOk, exportInfo);

await page.screenshot({ path: SHOTS + '/01-bookview.png' });

// 14. 选词库 → 卡片流程照常
await page.evaluate(() => {
  const lib = DATA.libraries[0];
  location.hash = '#/lib/' + lib.id;
});
await page.waitForTimeout(1500);
const backToCards = await page.$eval('#stage-inner', n => getComputedStyle(n).display !== 'none').catch(() => false);
step('选词库后回到卡片流程', backToCards);

step('无 JS 报错', errs.length === 0, { errs: errs.slice(0, 6) });

console.log('\n' + (results.every(r => r.ok) ? '✅ 全部通过' : '❌ 有失败项'));
console.log('截图在 ' + SHOTS);
await browser.close().catch(() => {});
process.exit(results.every(r => r.ok) ? 0 : 1);
