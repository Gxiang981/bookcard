'use strict';
/* ======================================================================
   Browse-lite frontend — single file, spec: docs/browse-lite-v1.md §5.
   ====================================================================== */

/* Tier metadata — order + labels from lib/core/front_tier.dart
   (kToggleTiers = T1,T2,T4,T6,T7; label = Due/New/Learned/Ignored/Mastered;
    colors = playfulDark.tiers[index of Tn-1]). */
const TIERS = {
  T1: {label:'Due',     fg:'#FF7A7A', tint:'rgba(255,122,122,.16)'},
  T2: {label:'New',     fg:'#FFAA55', tint:'rgba(255,170,85,.16)'},
  T4: {label:'Learned', fg:'#7DDC3A', tint:'rgba(125,220,58,.16)'},
  T6: {label:'Ignored', fg:'#6FB5FF', tint:'rgba(111,181,255,.16)'},
  T7: {label:'Mastered',fg:'#C99BFF', tint:'rgba(201,155,255,.16)'},
};
const TIER_ORDER = ['T1','T2','T4','T6','T7'];

/* ---- SM-2, ported from lib/core/sm2.dart (computeSm2) + rating.dart ---- */
/* rating.dart sm2RatingOf: unclear|forgot → 'again'; good|easy → 'good'. */
function sm2RatingOf(ui) {
  if (ui === 'unclear' || ui === 'forgot' || ui === 'again') return 'again';
  return 'good';
}
const round1 = x => Math.round(x * 10) / 10;
function computeSm2(rating, rep = 0, ease = 1.8, interval = 0, nowMs = Date.now()) {
  rep = rep || 0; ease = ease || 1.8; interval = interval || 0;
  if (rating === 'again') {
    rep += 1;
    ease = round1(Math.max(1.3, ease - 0.1));
    interval = interval > 0 ? Math.max(1, Math.floor(interval / 2)) : 1;
  } else { // 'good' — learningRate = 1 → one loop
    rep += 1;
    ease = round1(Math.min(2.5, ease + 0.1));
    if (rep === 1) interval = 1;
    else if (rep === 2) interval = 3;
    else interval = Math.min(365, Math.max(1, Math.round(interval * ease)));
  }
  return { repetition: rep, easeFactor: ease, intervalDays: interval,
           nextReviewAtMs: nowMs + interval * 86400000 };
}
/* Tier — §4: ignored→T6 · mastered→T7 · reviews==0→T2 · due→T1 · else T4 */
function tierOf(st) {
  if (st.ignored) return 'T6';
  if (st.mastered) return 'T7';
  if (st.reviews === 0) return 'T2';
  /* 🔴 与 db.py:98-113 对齐：next_review_at 为空（还没排期）也算到期 → T1。
     原先的写法在 null 时落到 T4，与引擎（及 tests/test_browse_tier.py:33-35）不一致。 */
  if (st.next_review_at == null || st.next_review_at <= Date.now()) return 'T1';
  return 'T4';
}

/* ============================ state ================================== */
const S = {
  tree: [],                    // /api/tree
  scope: null,                 // {book} or {lib}
  off: new Set(),              // tiers turned off
  cards: [],                   // filtered list (current scope)
  tierCounts: {},
  idx: 0,
  card: null,                  // detail of current card
  flipped: false,
  busy: false,
  panel: {level: 'books', book: null, lib: null}, // left-panel drill state
  tocLevel: null,          // 目录看第几级（null = 本书最大层级）
  tocMax: 0,               // 用户手动加出来的层级上限
  editing: false,
  menuOpen: false,
  /* ---- 书视图（读原书）---- */
  book: null,                  // 当前 scope 的书 id
  page: 1,                     // 当前选中的 PDF 页号
  pages: [],                   // DATA.pages（按页号排好）
  pageByNo: new Map(),         // 页号 → 页条目
  toc: {offset: null, entries: []},   // 目录（DATA.toc 打底 + 本地改动）
  tocVer: 0,                   // 目录版本号（有改动就 +1）
  tocShown: -1,                // 已经画出来的目录版本号
  thumbsBook: null,            // 缩略图条当前画的是哪本书
};
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));

let toastTimer = null;
function toast(msg) {
  const t = $('toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
}
const apiErr = (e) => toast(e.message || String(e));

/* ============================ URL hash ===============================
   #/lib/<id>/card/<id> — persists place across refresh.
   书那一级：#/book/<id>（带 /page/<n> 就连页码也记住）。 */
function writeHash() {
  if (isBookScope()) {
    location.hash = `#/book/${S.scope.book}/page/${S.page}`;
    return;
  }
  if (!S.scope || !S.cards[S.idx]) { location.hash = ''; return; }
  if (S.scope.pages) {
    location.hash = `#/pages/${S.scope.pages[0]}-${S.scope.pages[1]}/card/${S.cards[S.idx].id}`;
    return;
  }
  const seg = S.scope.lib ? 'lib' : 'book';
  const id = S.scope.lib || S.scope.book;
  location.hash = `#/${seg}/${id}/card/${S.cards[S.idx].id}`;
}
function readHash() {
  const m = location.hash.match(/^#\/(lib|book)\/([^/]+)(?:\/card\/([^/]+))?(?:\/page\/(\d+))?/);
  if (m) {
    return {
      kind: m[1],
      id: decodeURIComponent(m[2]),
      card: m[3] ? decodeURIComponent(m[3]) : null,
      page: m[4] ? parseInt(m[4], 10) : null,
    };
  }
  /* 页范围（目录条目 → 那一段页 → 那几个词） */
  const r = location.hash.match(/^#\/pages\/(\d+)-(\d+)(?:\/card\/([^/]+))?/);
  if (r) {
    return {
      kind: 'pages',
      id: `${r[1]}-${r[2]}`,
      pages: [parseInt(r[1], 10), parseInt(r[2], 10)],
      card: r[3] ? decodeURIComponent(r[3]) : null,
      page: null,
    };
  }
  return null;
}

/* ============================ history line ===========================
   learn_card_stage.dart CardHistoryData.lineText (table). */
function historyLine(st) {
  if (!st || st.reviews === 0) return 'New card · never reviewed';
  const ms = st.next_review_at;
  if (ms == null) return `Rep ${st.repetition}`;
  const MIN = 60000, H = 60 * MIN, D = 24 * H;
  const diff = ms - Date.now();
  const span = (a) => {
    if (a >= D) return `${Math.floor(a / D)}d`;
    if (a >= H) return `${Math.floor(a / H)}h`;
    if (a >= MIN) return `${Math.floor(a / MIN)}m`;
    return 'now';
  };
  if (diff <= 0) {
    const days = Math.floor(-diff / D);
    return days < 1 ? `Rep ${st.repetition} · Due now`
                    : `Rep ${st.repetition} · Overdue ${days}d`;
  }
  const s = span(diff);
  return s === 'now' ? `Rep ${st.repetition} · Due now`
                     : `Rep ${st.repetition} · Next in ${s}`;
}

/* ============================ data flow ============================== */
async function boot() {
  initBookData();                     // 书视图的页表 + 目录（要早于第一次 render）
  /* 目录看第几级 —— 用户选了就记住（存本地，刷新后还在） */
  try {
    const ui = api.ui() || {};
    if (ui.tocLevel) S.tocLevel = parseInt(ui.tocLevel, 10) || null;
    if (ui.tocMax) S.tocMax = parseInt(ui.tocMax, 10) || 0;
  } catch (e) { /* 存不了就算了 */ }
  const qp = new URLSearchParams(location.search);
  (qp.get('off') || '').split(',').filter(t => TIERS[t]).forEach(t => S.off.add(t));
  try { S.tree = await api.tree(); } catch (e) { apiErr(e); S.tree = []; }
  /* 一本书一个 index 页：只有一本书时，左栏直接落在那本书的内容级，
     不必先点一下书名，也没有「返回上一级」可点。 */
  if (S.tree.length === 1) { S.panel.book = S.tree[0]; S.panel.level = 'libs'; }
  const h = readHash();
  let scope = null, wantCard = null, wantPage = null;
  if (h) {
    const book = S.tree.find(b => b.id === h.id);
    const lib = book && book.libraries.find(l => l.id === h.id)
      || S.tree.flatMap(b => b.libraries).find(l => l.id === h.id);
    if (h.kind === 'lib' && lib) scope = {lib: lib.id};
    else if (h.kind === 'book' && book) scope = {book: book.id};
    else if (h.kind === 'pages' && h.pages) scope = {pages: h.pages};
    if (scope) { wantCard = h.card; wantPage = h.page; }
  }
  if (!scope) {
    const b = S.tree[0];
    if (DATA.book_only) {
      /* 只有「读原书」这一种界面：直接进书视图。 */
      scope = b ? {book: b.id} : null;
    } else {
      scope = b && b.libraries[0] ? {lib: b.libraries[0].id}
          : b ? {book: b.id} : null;
    }
  }
  if (DATA.book_only) document.body.classList.add('book-only');
  await selectScope(scope, wantCard, wantPage);
  // screenshot hooks (dev only): ?shot=back|menu|edit|drawer|done
  const shot = new URLSearchParams(location.search).get('shot');
  if (shot === 'back') flip(true);
  else if (shot === 'menu') { flip(true); toggleMenu(true); }
  else if (shot === 'edit') openEdit();
  else if (shot === 'drawer') openDrawer(true);
  else if (shot === 'done') { S.idx = S.cards.length; renderStage(); }
}

async function selectScope(scope, wantCard, wantPage) {
  S.scope = scope;
  S.flipped = false;
  try {
    const r = await api.cards(scope, S.off);
    S.cards = r.cards; S.tierCounts = r.tier_counts;
  } catch (e) { apiErr(e); S.cards = []; S.tierCounts = {}; }
  S.idx = wantCard ? Math.max(0, S.cards.findIndex(c => c.id === wantCard)) : 0;
  if (S.idx < 0) S.idx = 0;
  if (isBookScope()) {
    /* 书视图：换书就回到第一页，否则用 hash 里带来的页码。 */
    if (S.book !== S.scope.book) {
      S.book = S.scope.book;
      S.page = minPage();
      S.thumbsBook = null;
    }
    if (wantPage) S.page = wantPage;
    S.page = Math.min(maxPage(), Math.max(minPage(), S.page));
  }
  await loadCard();
  renderAll();
  writeHash();
}

async function loadCard() {
  const lite = S.cards[S.idx];
  if (!lite) { S.card = null; return; }
  try { S.card = await api.card(lite.id); }
  catch (e) { apiErr(e); S.card = lite; }
}

function currentScopeLabel() {
  if (!S.scope) return {book: '', lib: ''};
  for (const b of S.tree) {
    for (const l of b.libraries) {
      if (S.scope.lib === l.id) return {book: b.title, lib: l.name};
    }
    if (S.scope.book === b.id) return {book: b.title, lib: ''};
  }
  return {book: '', lib: ''};
}

/* ---- 书元数据：书名、领域、水平、自由标签 -------------------
   agent 在识别书名时就把领域和水平写进来；用户也能在顶栏直接改。
   改动存 STORE.book，导出 edits.json 时带走，回流时写回 manifest。 */
const LEVELS = ['小学', '初中', '高中', '本科', '硕博'];

function bookMeta() {
  const base = (DATA.book || {});
  const over = STORE.book || {};
  return {
    title: over.title != null ? over.title : (base.title || ''),
    domain: over.domain != null ? over.domain : (base.domain || ''),
    level: over.level != null ? over.level : (base.level || ''),
    tags: (over.tags || base.tags || []).slice(),
  };
}

function setBookMeta(patch) {
  const m = Object.assign(bookMeta(), patch || {});
  STORE.book = { title: m.title, domain: m.domain, level: m.level, tags: m.tags };
  saveStore();
  renderHeader();
  renderPanel();
}

function renderBookTags() {
  const box = $('book-tags');
  if (!box) return;
  box.innerHTML = '';
  const m = bookMeta();
  if (!m.title && !m.domain && !m.level && !m.tags.length) return;

  /* 领域：自由文本 */
  const domChip = tagChip('领域', m.domain);
  domChip.onclick = () => editChip(domChip, m.domain, v => setBookMeta({domain: v}));
  box.appendChild(domChip);
  /* 水平：固定几个档 —— 点开是下拉选，不打字 */
  const lvlChip = tagChip('水平', m.level);
  lvlChip.onclick = () => {
    /* 只开一次：下拉就出现在 chip 里，鼠标抬起时点击事件还会冒泡回 chip，
       再跑一遍就把下拉重建又拆掉（表现为「闪一下就没了」）。 */
    lvlChip.onclick = null;
    const sel = document.createElement('select');
    sel.innerHTML = '<option value="">（未标）</option>'
      + LEVELS.map(l => `<option${l === m.level ? ' selected' : ''}>${l}</option>`).join('');
    const v = lvlChip.querySelector('.bk-v');
    if (v) v.replaceWith(sel); else lvlChip.appendChild(sel);
    sel.onclick = (e) => e.stopPropagation();
    sel.onmousedown = (e) => e.stopPropagation();
    sel.focus();
    sel.onchange = () => setBookMeta({level: sel.value});
    sel.onblur = () => renderBookTags();
  };
  box.appendChild(lvlChip);
  /* 自由标签 */
  for (const tag of m.tags) {
    const c = tagChip('', tag);
    c.onclick = () => editChip(c, tag, v => {
      setBookMeta({tags: m.tags.map(x => x === tag ? v : x)});
    });
    const x = document.createElement('span');
    x.className = 'bk-x';
    x.textContent = '×';
    x.title = '删掉这个标签';
    x.onclick = (e) => { e.stopPropagation(); setBookMeta({tags: m.tags.filter(y => y !== tag)}); };
    c.appendChild(x);
    box.appendChild(c);
  }
  const add = document.createElement('button');
  add.className = 'bk-tag-add';
  add.textContent = '＋';
  add.title = '加一个标签';
  add.onclick = () => {
    const v = prompt('标签内容');
    if (v && v.trim()) setBookMeta({tags: m.tags.concat([v.trim()])});
  };
  box.appendChild(add);
}

function tagChip(key, value) {
  const c = document.createElement('span');
  c.className = 'bk-tag' + (value ? ' editable' : '');
  if (key) {
    const k = document.createElement('span');
    k.className = 'bk-k';
    k.textContent = key + '：';
    c.appendChild(k);
  }
  const v = document.createElement('span');
  v.className = 'bk-v';
  /* 没值就只留个键名（暗一点），不写「（空）」——噪声。 */
  v.textContent = value || '';
  if (!value) v.classList.add('bk-empty');
  c.appendChild(v);
  return c;
}

/* 就地编辑某个 chip 的值。chip 由调用方指认 —— 不去猜是哪一个
   （领域为空时猜会错位到「水平」上）。 */
function editChip(chip, value, onSave) {
  const inp = document.createElement('input');
  inp.value = value || '';
  inp.placeholder = '（空）';
  const holder = document.createElement('span');
  holder.className = 'bk-tag';
  holder.appendChild(inp);
  chip.replaceWith(holder);
  inp.focus();
  if (inp.select) inp.select();
  let done = false;
  const commit = () => {
    if (done) return;
    done = true;
    onSave(inp.value.trim());
  };
  inp.onblur = commit;
  inp.onkeydown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { e.preventDefault(); done = true; renderBookTags(); }
  };
}

/* ============================ render ================================= */
function renderAll() { renderHeader(); renderPanel(); renderStage(); }

function renderHeader() {
  const {book, lib} = currentScopeLabel();
  /* 顶栏：书名（可点开编辑）+ 当前词库。没有下拉箭头 —— 一本书一个 index 页。 */
  const bm = bookMeta();
  $('scope-label').innerHTML = lib
    ? `<span class="book" id="scope-book">${esc(bm.title || book || 'BookCard')}</span><span class="sep">›</span><span class="lib">${esc(lib)}</span>`
    : `<span class="book" id="scope-book">${esc(bm.title || book || 'BookCard')}</span>`;
  renderBookTags();
  const t = $('tiers'); t.innerHTML = '';
  for (const k of TIER_ORDER) {
    const n = S.tierCounts[k] || 0;
    if (n === 0) continue;                       // R109: count 0 → chip hidden
    const on = !S.off.has(k);
    const b = document.createElement('button');
    b.className = 'tier-chip' + (on ? ' on' : '');
    b.dataset.t = k;
    b.title = `${k} ${TIERS[k].label} · ${n}`;
    b.innerHTML = `<span class="dot"></span><span>${k}</span><span class="cnt num">${n}</span>`;
    b.onclick = () => toggleTier(k);
    t.appendChild(b);
  }
  $('tier-reset').style.display = S.off.size ? 'inline-flex' : 'none';
  if (isBookScope()) {
    /* 书视图：页码在下面的工具行里有（和 offset、跳页在一排），
       右上角不再重复一遍。 */
    $('position').textContent = '';
    return;
  }
  const total = S.cards.length;
  const pos = total === 0 ? '0 / 0' : `${Math.min(S.idx + 1, total)} / ${total}`;
  $('position').textContent = pos;
}

function toggleTier(k) {
  S.off.has(k) ? S.off.delete(k) : S.off.add(k);
  const cur = S.cards[S.idx];
  selectScope(S.scope, cur && cur.id);
}
$('tier-reset').onclick = () => { S.off.clear(); const cur = S.cards[S.idx]; selectScope(S.scope, cur && cur.id); };

/* ------------------------------ left panel --------------------------
   Drill-down: books → libraries → word list (tap = jump), tier dot/row. */
function panelRows() {
  const p = S.panel;
  if (p.level === 'books') {
    return {crumb: 'Books', rows: S.tree.map(b => ({
      label: b.title, sub: b.subtitle, arrow: '›',
      cnt: b.libraries.reduce((a, l) => a + l.count, 0),
      active: isBookScope() && S.scope.book === b.id,
      /* 点最上面那级（书）→ 右边切到「读原书」界面，同时左栏下钻到词库。 */
      on: () => { p.book = b; p.level = 'libs'; selectScope({book: b.id}); },
    }))};
  }
  if (p.level === 'libs') {
    const b = p.book;
    /* 一本书一个 index 页 → 没有「Books 列表」那一级可回；只有多本书时才给返回。 */
    const libs = b.libraries || [];
    /* 目录所在的页（agent 提取目录时记下的那几页）。
       点「目录」跳到那儿 —— 用户要拿书上的印刷目录跟右边这份对。 */
    const tp = (S.toc && S.toc.pages) || [];
    const tocRange = tp.length
      ? (tp.length === 1 ? 'p.' + tp[0] : 'p.' + tp[0] + '–' + tp[tp.length - 1])
      : '';
    const rows = [
      {
        label: '目录', sub: tocRange, klass: 'book-entry', arrow: '›',
        active: isBookScope(),
        on: () => {
          /* 跳页要等 scope 切完 —— 切换本身会把页码重置回 1。 */
          const go = () => { if (tp.length) bookSetPage(tp[0], {scroll: true}); };
          const r = selectScope({book: b.id});
          if (r && typeof r.then === 'function') r.then(go); else go();
        },
      },
    ];
    /* 左栏永远是**目录树**（父 | 子 的全路径）——
       确认页和最终页长一样，目录一改这里跟着变。
       叶子如果对上一个词库（按标题配对），点进去是那几张卡；
       没对上就跳到那一页看原文。 */
    for (const n of tocViewNodes()) {
      if (!n.isLeaf || !n.e.page) continue;      // 只列这一层里的叶子（和右栏带页号的是同一批）
      if ((n.e.kind || 'body') !== 'body') continue;
      /* 目录条目 → 页范围 → 卡片。
         打包的目录结构是唯一的（用户选的那一层），页面上的浏览只是**映射**：
         这一条目录占的那几页里有哪几个词，就显示哪几个 —— 不另建一套结构。 */
      const block = tocBlock(n.e);
      const cnt = block ? countCardsInPages(block) : 0;
      rows.push({
        label: n.path, wrap: true, arrow: '›',
        cnt: cnt || null,
        active: !!(S.scope && S.scope.pages && block &&
                   S.scope.pages[0] === block[0] && S.scope.pages[1] === block[1]),
        on: () => {
          if (cnt) {
            p.level = 'words';
            p.lib = null;
            p.block = {range: block, path: n.path, title: n.e.title};
            selectScope({pages: block});
          } else {
            selectScope({book: b.id});
            bookSetPage(n.e.page, {scroll: true});
          }
        },
      });
    }
    /* 词库不再单独列了 —— 左栏就是目录树，点条目按页范围映射到卡片。
       （打包的目录结构是唯一的：用户选的那一层。） */
    return {crumb: b.title,
      back: S.tree.length > 1 ? () => { p.level = 'books'; renderPanel(); } : null,
      rows};
  }
  // words
  const l = p.lib;
  const blk = p.block;
  const crumb = blk
    ? `${p.book.title} / ${blk.path}`
    : `${p.book.title} / ${l.name}`;
  return {crumb, back: () => { p.level = 'libs'; p.block = null; renderPanel(); },
    rows: S.cards.map((c, i) => ({
      label: c.front, tier: c.tier, active: i === S.idx,
      on: () => { goTo(i); closeDrawer(); },
    }))};
}

function renderPanel() {
  for (const [listEl, crumbEl, crumbTxt] of
      [[$('panel-list'), $('crumb'), $('crumb-txt')],
       [$('drawer-list'), $('drawer-crumb'), $('drawer-crumb-txt')]]) {
    const {crumb, back, rows} = panelRows();
    crumbTxt.textContent = crumb;
    crumbEl.style.display = back ? 'flex' : 'none';
    if (back) crumbEl.onclick = back;
    listEl.innerHTML = '';
    for (const r of rows) {
      const d = document.createElement('div');
      d.className = 'row' + (r.active ? ' active' : '') + (r.klass ? ' ' + r.klass : '')
                 + (r.wrap ? ' wrap' : '');
      let inner = '';
      if (r.tier && TIERS[r.tier]) {
        inner += `<span class="dot" style="background:${TIERS[r.tier].fg}"></span>`;
      }
      inner += `<span class="lbl">${esc(r.label)}${r.sub ? `<span class="sub">${esc(r.sub)}</span>` : ''}</span>`;
      if (r.tier && TIERS[r.tier]) {
        inner += `<span class="tier-badge" style="background:${TIERS[r.tier].fg};color:var(--on-solid)">${r.tier}</span>`;
      }
      if (r.cnt != null) inner += `<span class="cnt num">${r.cnt}</span>`;
      if (r.arrow) inner += `<span class="arrow">${r.arrow}</span>`;
      d.innerHTML = inner;
      d.onclick = r.on;
      listEl.appendChild(d);
    }
  }
}

/* ------------------------------ stage ------------------------------- */
function cardHeight() {
  const h = $('stage').clientHeight;
  const w = $('stage').clientWidth;
  // portrait: <600 wide & taller than wide → card eats remaining
  if (w < 600 && h > w) {
    const a = 48 + 10 + 20; // actions = 48 + gap 10 + history 20
    return Math.max(140, h - 12 - 28 - a - 12 - 16);
  }
  /* 横屏：卡片跟着可用高度长 —— 原来死卡在 260–420，横屏时下面空一大块。
     上限 560 是为了别在超大屏上拉成一条。 */
  const reserve = 48 + 10 + 20;      // actions + gap + history
  return Math.max(200, Math.min(h - 12 - 28 - reserve - 12 - 16, 560));
}

function renderStage() {
  const editing = S.editing;
  $('edit').classList.toggle('open', editing);
  $('stage-inner').style.display = editing ? 'none' : 'flex';
  $('prev-btn').style.display = editing ? 'none' : '';
  $('next-btn').style.display = editing ? 'none' : '';
  if (editing) return;

  /* scope 是整本书 → 走「读原书」界面，不是卡片。 */
  if (isBookScope()) { renderBookView(); renderHeader(); return; }
  $('bookview').classList.remove('on');

  const has = S.cards.length > 0 && S.idx < S.cards.length;
  $('card-wrap').style.display = has ? '' : 'none';
  $('actions').style.display = has ? '' : 'none';
  $('prev-btn').style.display = has ? '' : 'none';
  $('next-btn').style.display = has ? '' : 'none';
  $('empty').style.display = has ? 'none' : 'flex';
  if (!has) {
    $('empty-title').textContent = S.cards.length === 0 && S.off.size
      ? 'Nothing in these tiers' : 'All done in this section';
    return;
  }

  const c = S.card || S.cards[S.idx];
  // geometry
  $('card-wrap').style.height = cardHeight() + 'px';
  // front
  $('front-word').textContent = c.front;
  fitFront(c.front);
  const {book: fb, lib: fl} = currentScopeLabel();
  $('front-footer').textContent = [fb, fl].filter(Boolean).join(' · ');
  const tierEl = $('front-tier');
  const tk = c.tier || (S.cards[S.idx] && S.cards[S.idx].tier);
  if (tk && TIERS[tk]) {
    tierEl.style.display = 'block';
    tierEl.textContent = tk;
    tierEl.style.background = TIERS[tk].fg;
    tierEl.style.color = 'var(--on-solid)';
  } else tierEl.style.display = 'none';
  // back
  renderBack(c);
  // flip state
  $('card3d').classList.toggle('flipped', S.flipped);
  $('card-wrap').classList.toggle('flipped', S.flipped);
  // actions
  const row = $('actions-row');
  row.innerHTML = '';
  if (!S.flipped) {
    const b = document.createElement('button');
    b.className = 'btn-primary'; b.textContent = 'Show answer';
    b.disabled = S.busy; b.onclick = () => flip(true);
    row.appendChild(b);
  } else {
    const cardW = Math.min($('stage').clientWidth, 640);
    const w = Math.min(160, (cardW - 30) / 4);
    const keys = [['Unclear','unclear','r-unclear','1'],['Forgot','forgot','r-forgot','2'],
                  ['Good','good','r-good','3'],['Easy','easy','r-easy','4']];
    for (const [label, rating, cls] of keys) {
      const b = document.createElement('button');
      b.className = 'btn-rate ' + cls; b.textContent = label;
      b.style.width = w + 'px'; b.disabled = S.busy;
      b.onclick = () => rate(rating);
      row.appendChild(b);
    }
  }
  // history
  const st = c.state;
  $('history').innerHTML = st
    ? `<span>${esc(historyLine(st))}</span><span class="info">ⓘ</span>`
    : '';
  renderHeader();
}

function fitFront(text) {
  // LearnFlipCard: cap = (cardW * .16).clamp(32,120); fitFrontText then
  // shrinks (wrap first, ≤3 lines) until it fits the face.
  const el = $('front-word');
  const face = $('face-front');
  const availW = face.clientWidth - 48;              // face padding 24×2
  const availH = face.clientHeight - 48 - 30;        // padding + footer line
  let size = Math.min(Math.max(availW * 0.16, 32), 120);
  el.style.webkitLineClamp = 'none';
  el.style.fontSize = size + 'px';
  while (size > 20 && (el.scrollWidth > availW || el.scrollHeight > availH)) {
    size -= 4; el.style.fontSize = size + 'px';
  }
  el.style.webkitLineClamp = '3';
}

function renderBack(c) {
  const el = $('back-body');
  const tk = c.tier || (S.cards[S.idx] && S.cards[S.idx].tier);
  // card_back_scaffold.dart _head: word (title 16 w700) + tier badge
  let html = `<div class="back-head"><span class="word">${esc(c.front)}</span>` +
    (tk && TIERS[tk]
      ? `<span class="front-tier" style="background:${TIERS[tk].fg};color:var(--on-solid)">${tk}</span>`
      : '') + `</div>`;
  html += `<div class="back-text">${esc(c.back || '')}</div>`;
  if (c.tags && c.tags.length) {
    html += `<div class="back-tags">` + c.tags.map(t => `<span class="tag-chip">${esc(t)}</span>`).join('') + `</div>`;
  }
  const sc = c.scene;
  if (sc) html += `<div class="scene-label">WHERE YOU MET IT</div>`;
  if (sc && sc.kind === 'text') {
    html += `<div class="scene">
      <div class="scene-head"><span class="book">${esc(sc.book)}</span>
        <span class="page num">p.${esc(sc.printed_page || sc.page)}</span></div>
      <div class="scene-body" id="scene-body"></div></div>`;
  } else if (sc && sc.kind === 'image') {
    html += `<div class="scene">
      <div class="scene-head"><span class="book">${esc(sc.book)}</span>
        <span class="page num">p.${esc(sc.printed_page || sc.page)}</span></div>
      <div class="scene-img-wrap" id="scene-img">
        <img src="${esc(sc.src)}" alt="">
        ${(sc.boxes || []).map(b => `<div class="bbox" style="left:${b.x / sc.width * 100}%;top:${b.y / sc.height * 100}%;width:${b.w / sc.width * 100}%;height:${b.h / sc.height * 100}%"></div>`).join('')}
      </div></div>`;
  }
  el.innerHTML = html;
  // text scene body with highlights + 6-line collapse (text_scene.dart)
  if (sc && sc.kind === 'text') {
    const body = $('scene-body');
    const spans = [];
    let last = 0;
    const hls = (sc.highlights || []).slice().sort((a, b) => a.start - b.start);
    const len = sc.text.length;
    for (const h of hls) {
      const s = Math.min(len, Math.max(last, h.start | 0));
      const e = Math.min(len, h.end | 0);
      if (e <= s) continue;                     // out-of-order / empty range
      spans.push(esc(sc.text.slice(last, s)), `<mark>${esc(sc.text.slice(s, e))}</mark>`);
      last = e;
    }
    spans.push(esc(sc.text.slice(last)));
    body.innerHTML = `<div class="clamp" id="scene-clamp">${spans.join('')}</div>`;
    const clamp = $('scene-clamp');
    requestAnimationFrame(() => {
      if (clamp.scrollHeight > clamp.clientHeight + 1) {
        const more = document.createElement('span');
        more.className = 'scene-more'; more.textContent = 'Show more';
        more.onclick = (e) => {
          e.stopPropagation();
          const open = clamp.classList.toggle('clamp');
          more.textContent = open ? 'Show less' : 'Show more';
        };
        body.appendChild(more);
      }
    });
  }
  if (sc && sc.kind === 'image') {
    $('scene-img').onclick = (e) => { e.stopPropagation(); openFullscreen(sc); };
  }
}

function openFullscreen(sc) {
  const fs = $('imgfs');
  $('imgfs-img').src = sc.src;
  const boxes = $('imgfs-boxes');
  boxes.innerHTML = '';
  const img = $('imgfs-img');
  const place = () => {
    const wrap = img.getBoundingClientRect();
    boxes.innerHTML = (sc.boxes || []).map(b =>
      `<div class="bbox" style="left:${b.x / sc.width * 100}%;top:${b.y / sc.height * 100}%;width:${b.w / sc.width * 100}%;height:${b.h / sc.height * 100}%"></div>`).join('');
  };
  img.onload = place;
  if (img.complete) place();
  fs.classList.add('open');
}
$('imgfs').onclick = () => $('imgfs').classList.remove('open');

/* ============================ 书视图（读原书） =======================
   scope 是整本书（{book: id}）时，#stage 里换掉卡片区，显示：
     左上 = 选中页的大图（点一下进 #imgfs 全屏层放大）
     右上 = 目录（标题 / PDF 页号 / offset 都能直接改，改动落 STORE.toc）
     底部 = 全书缩略图条（列数随宽度自适应、‹ › 一次翻一屏、能跳页）
   底稿 = DATA.pages + DATA.toc；本地改动 = STORE.toc（localStorage 按书分键）。 */

/* localStorage 的 key：优先用 local-api.js 的 STORE_KEY，
   拿不到就按同样的规则自己算（'bookcard-share:' + book_id）。 */
function storeKey() {
  try { if (typeof STORE_KEY === 'string' && STORE_KEY) return STORE_KEY; } catch (e) {}
  return 'bookcard-share:' + ((DATA.book && DATA.book.id) || 'book');
}

/* 读本地存的目录。
   local-api.js 的 loadStore() 只保留 v/cards/state/review_log，toc 会被丢掉，
   所以这里直接读 localStorage 原文把它捞回来（不改那个文件）。 */
function readStoredToc() {
  try {
    const raw = window.localStorage.getItem(storeKey());
    if (!raw) return null;
    const p = JSON.parse(raw);
    return (p && p.toc && Array.isArray(p.toc.entries)) ? p.toc : null;
  } catch (e) { return null; }        // file:// + Safari：没 localStorage 就算了
}

/* 目录形状归一：{offset: number|null, entries:[{level,title,page}]} */
function normalizeToc(t) {
  const rawOff = t ? t.offset : null;
  const off = (rawOff == null || rawOff === '') ? null : parseInt(rawOff, 10);
  return {
    /* 目录所在的页（agent 提取目录时记下的）—— 左栏「目录」那一行显示并跳过去。 */
    pages: ((t && t.pages) || []).map(n => parseInt(n, 10)).filter(n => Number.isFinite(n)),
    /* 抽词切章节用的层级（用户在第二步确认的）—— 页面默认按它显示目录。 */
    extract_level: parseInt((t && t.extract_level) || 0, 10) || 0,
    /* offset = 前置页数（正数）：PDF 页 = 印刷页 + offset。
       引擎里两种约定（页眉页脚探测是「印刷 − PDF」，看图锚点是「PDF − 印刷」）
       差的只是符号，取绝对值两边都对。 */
    offset: Number.isFinite(off) ? Math.abs(off) : null,
    entries: ((t && t.entries) || []).map(e => ({
      level: Math.min(6, Math.max(1, parseInt(e && e.level, 10) || 1)),
      title: String((e && e.title) != null ? e.title : ''),
      page: Math.max(0, parseInt(e && e.page, 10) || 0),
      /* 目录看图 v1 的条目自带印刷页（printed_page）；没有就用 offset 换算。 */
      printed_page: (e && e.printed_page != null && e.printed_page !== '')
        ? parseInt(e.printed_page, 10) : null,
      /* kind：body / front / appendix —— 前置页和附录不算正文（左栏不列）。 */
      kind: (e && e.kind) ? String(e.kind) : 'body',
    })),
  };
}

/* 初始化书视图的数据：页表 + 目录（本地有改动就盖过底稿）。 */
function initBookData() {
  S.pages = (Array.isArray(DATA.pages) ? DATA.pages : [])
    .filter(p => p && p.page != null)
    .slice()
    .sort((a, b) => a.page - b.page);
  S.pageByNo = new Map();
  for (const p of S.pages) S.pageByNo.set(p.page, p);
  const local = readStoredToc();
  S.toc = normalizeToc(local || DATA.toc || {});
  if (local) {
    /* 本地有改动：内存里的 STORE 也跟上一份（数据层导出 edits.json 时会看它）。 */
    STORE.toc = {offset: S.toc.offset,
                 entries: S.toc.entries.map(e => ({level: e.level, title: e.title, page: e.page}))};
  }
  S.tocVer++; S.tocShown = -1;
  S.page = S.pages.length ? S.pages[0].page : 1;
}

/* 目录改动落地：写 STORE.toc + saveStore()。
   数据层负责把它带进导出的 edits.json（这里不碰 local-api.js）。 */
function saveToc() {
  STORE.toc = {
    offset: S.toc.offset,
    entries: S.toc.entries.map(e => ({level: e.level, title: e.title, page: e.page})),
  };
  saveStore();
}

function isBookScope() { return !!(S.scope && S.scope.book && !S.scope.lib); }
function pageEntry(n) { return S.pageByNo.get(n) || null; }
function minPage() { return S.pages.length ? S.pages[0].page : 1; }
function maxPage() { return S.pages.length ? S.pages[S.pages.length - 1].page : 1; }

/* ------------------------------ 书视图 ----------------------------- */
function renderBookView() {
  $('stage-inner').style.display = 'none';
  $('edit').classList.remove('open');
  $('prev-btn').style.display = 'none';
  $('next-btn').style.display = 'none';
  $('empty').style.display = 'none';
  $('bookview').classList.add('on');
  renderBookPage();
  renderThumbs();
  renderToc();
}

/* 左上大图：选中页的原图（按框自适应，不拉伸变形）。 */
function renderBookPage() {
  const img = $('bv-img');
  const none = $('bv-nopages');
  const e = pageEntry(S.page);
  if (!e) {
    img.removeAttribute('src');
    img.style.display = 'none';
    none.classList.add('on');
    $('bv-nopages-title').textContent = S.pages.length ? '这一页没有图' : '这本书没有页图';
    $('bv-nopages-body').textContent = S.pages.length
      ? '包内找不到 p.' + S.page + ' 的图片。'
      : '包里没有页图（DATA.pages 是空的），下面只有目录可以看。';
    $('bv-pos').textContent = S.pages.length ? 'p.' + S.page + ' / ' + S.pages.length : '—';
    $('bv-print').textContent = '';
    return;
  }
  none.classList.remove('on');
  img.style.display = '';
  if (img.getAttribute('src') !== e.image) img.src = e.image;
  $('bv-pos').textContent = 'p.' + e.page + ' / ' + S.pages.length;
  const off = S.toc.offset;
  $('bv-print').textContent = off == null ? '印刷页号未探测' : '印刷 p.' + (e.page - off);
}
/* 底部缩略图条：列数随宽度自适应（CSS grid），当前页有选中态。 */
function renderThumbs() {
  const strip = $('bv-thumbs');
  const bookId = S.scope && S.scope.book;
  if (strip.childElementCount !== S.pages.length || S.thumbsBook !== bookId) {
    strip.innerHTML = '';
    for (const p of S.pages) {
      const cell = document.createElement('button');
      cell.className = 'bv-thumb';
      cell.dataset.page = p.page;
      cell.title = 'p.' + p.page;
      const img = document.createElement('img');
      img.loading = 'lazy';
      img.alt = 'p.' + p.page;
      if (p.thumb) img.src = p.thumb;
      const pn = document.createElement('span');
      pn.className = 'bv-pn num';
      pn.textContent = p.page;
      cell.appendChild(img);
      cell.appendChild(pn);
      cell.onclick = () => bookSetPage(p.page, {scroll: true});
      strip.appendChild(cell);
    }
    S.thumbsBook = bookId;
  }
  updateThumbSel();
  $('bv-bottom').style.display = S.pages.length ? '' : 'none';
}

function updateThumbSel() {
  const strip = $('bv-thumbs');
  for (const cell of strip.children) cell.classList.toggle('on', +cell.dataset.page === S.page);
}

/* 把某一页的缩略图滚进可视区。 */
function scrollThumbTo(p) {
  const strip = $('bv-thumbs');
  const cell = strip.querySelector('.bv-thumb[data-page="' + p + '"]');
  if (!cell) return;
  const left = cell.offsetLeft, right = left + cell.offsetWidth;
  const sl = strip.scrollLeft, sw = strip.clientWidth;
  if (left < sl) strip.scrollTo({left: Math.max(0, left - 12), behavior: 'smooth'});
  else if (right > sl + sw) strip.scrollTo({left: right - sw + 12, behavior: 'smooth'});
}

/* ‹ › ：一次滚一屏；到头了循环回另一头。 */
function scrollThumbs(dir) {
  const strip = $('bv-thumbs');
  const w = strip.clientWidth;
  const max = Math.max(0, strip.scrollWidth - strip.clientWidth);
  if (max <= 1) return;
  if (max <= w) { strip.scrollTo({left: dir > 0 ? max : 0, behavior: 'smooth'}); return; }
  let x = strip.scrollLeft + dir * w;
  if (dir > 0) x = x > max ? 0 : x;
  else x = x < 0 ? max : x;
  strip.scrollTo({left: x, behavior: 'smooth'});
}

/* 切页：大图、页码、缩略图选中态一起动。 */
function bookSetPage(n, opts) {
  opts = opts || {};
  if (!S.pages.length) return;
  let p = Math.round(Number(n));
  if (!Number.isFinite(p)) p = S.page;
  p = Math.min(maxPage(), Math.max(minPage(), p));
  S.page = p;
  renderBookPage();
  updateThumbSel();
  if (opts.scroll !== false) scrollThumbTo(p);
  writeHash();
}

/* 目录：标题 / PDF 页号 / 换算出的印刷页号（PDF 页 = 印刷页 + offset，offset 是前置页数）。 */
/* ---- 层级：下拉选一级，左右栏一起切到那一层 --------------------
   默认是本书的最大层级；用户可以「增加层级」，加完之后循环点数字徽章
   就能调到新的一级。 */
/* 本书实际用到的最大层级（目录里最深的那一级）。 */
function tocBookMax() {
  const rows = (S.toc && S.toc.entries) || [];
  return rows.reduce((m, e) => Math.max(m, e.level || 1), 1);
}
/* 能选的上限：默认给到 3 级，目录里更深就跟着更深，用户还能手动再加。 */
function tocMaxLevel() {
  return Math.max(3, tocBookMax(), S.tocMax || 0);
}
/* 一个目录条目占的页范围：从它的页开始，到下一个条目开始前。
   （同一页的兄弟条目算同一块 —— 那一页的内容属于它们共同的起点。） */
function tocBlock(entry) {
  if (!entry || !entry.page) return null;
  const all = (S.toc.entries || [])
    .filter(e => e.page && e.page > entry.page)
    .map(e => e.page);
  const next = all.length ? Math.min(...all) : null;
  const last = maxPage ? maxPage() : entry.page;
  return [entry.page, (next ? next - 1 : last)];
}

/* 这个页范围里有几张卡。 */
function countCardsInPages(block) {
  if (!block) return 0;
  const lo = block[0], hi = block[1];
  return (DATA.cards || []).filter(c => c.page != null && c.page >= lo && c.page <= hi).length;
}

/* 记住用户选的目录层级（刷新后还在）。 */
function saveTocLevel() {
  try {
    api.setUi({tocLevel: S.tocLevel, tocMax: S.tocMax});
  } catch (e) { /* 存不了就算了，不影响浏览 */ }
}

/* 当前看第几级：默认跟**抽词切章节的层级**一致（用户在第二步确认的那个），
   目录里没有这一层才退回本书的最大层级。 */
function tocLevel() {
  const max = tocMaxLevel();
  const extractLevel = parseInt((S.toc && S.toc.extract_level) || 0, 10) || 0;
  const fallback = extractLevel ? Math.min(extractLevel, max) : Math.min(tocBookMax(), max);
  const lv = S.tocLevel == null ? fallback : S.tocLevel;
  return Math.min(Math.max(1, lv), max);
}
/* 当前要显示的条目：1..L 层（L = 选中的层级）。
   视图里的「叶子」= 到 L 层为止没有更深的子节点 —— 只有叶子带页号。 */
function tocViewNodes() {
  const all = tocTree();
  const L = tocLevel();
  /* 谁有孩子（按全树的层级关系算） */
  const hasChild = new Set();
  const stack = [];
  for (const n of all) {
    while (stack.length && stack[stack.length - 1].level >= n.level) stack.pop();
    if (stack.length) hasChild.add(stack[stack.length - 1]);
    stack.push(n);
  }
  return all
    .filter(n => n.level <= L)
    .map(n => Object.assign({}, n, {isLeaf: n.level >= L || !hasChild.has(n)}));
}
function renderLevelSelect() {
  const sel = $('bv-level');
  if (!sel) return;
  const max = tocMaxLevel();
  const cur = tocLevel();
  const want = [];
  for (let i = 1; i <= max; i++) want.push(String(i));
  const have = Array.from(sel.options).map(o => o.value);
  if (have.join(',') !== want.join(',')) {
    sel.innerHTML = '';
    for (const v of want) {
      const o = document.createElement('option');
      o.value = v; o.textContent = v + ' 级';
      sel.appendChild(o);
    }
    const o = document.createElement('option');
    o.value = 'add'; o.textContent = '＋ 增加一级';
    sel.appendChild(o);
  }
  sel.value = String(cur);
}

/* 目录树：按 level 组织成树，算出每条的「全路径」和是不是叶子。

   路径用「 | 」连：三级目录就是「第一级 | 第二级 | 第三级」；
   左栏按这个路径列，右栏只在叶子那级给页码。 */
function tocTree() {
  const nodes = S.toc.entries.map(e => ({
    e, level: e.level || 1, path: '', isLeaf: true,
  }));
  const stack = [];
  for (const n of nodes) {
    while (stack.length && stack[stack.length - 1].level >= n.level) stack.pop();
    n.path = stack.map(s => s.title).concat([n.e.title || '（未命名）']).join(' | ');
    if (stack.length) stack[stack.length - 1].node.isLeaf = false;
    stack.push({level: n.level, title: n.e.title || '（未命名）', node: n});
  }
  return nodes;
}

function renderToc() {
  if (S.tocShown === S.tocVer) return;   // 没改就不重画，别把正在编辑的输入框冲掉
  S.tocShown = S.tocVer;
  const off = S.toc.offset;
  const offEl = $('bv-offset');
  if (document.activeElement !== offEl) offEl.value = off == null ? '' : off;
  const list = $('bv-toc-list');
  list.innerHTML = '';
  if (!S.toc.entries.length) {
    const d = document.createElement('div');
    d.className = 'bv-toc-empty';
    d.textContent = '目录是空的 —— 点上面的「＋」加一条。';
    list.appendChild(d);
    return;
  }
  renderLevelSelect();
  const tree = tocViewNodes();
  /* 默认提供到 3 级；目录里真出现了 4 级、5 级，就一路支持到最深那级。 */
  const maxLevel = tocMaxLevel();
  for (const node of tree) {
    const e = node.e;
    const row = document.createElement('div');
    row.className = 'bv-toc-row' + (node.isLeaf ? '' : ' bv-toc-parent');
    row.style.paddingLeft = ((e.level - 1) * 12) + 'px';   // 层级靠缩进表示

    const lvl = document.createElement('button');
    lvl.className = 'bv-toc-lvl num';
    lvl.textContent = e.level;
    lvl.title = '章节层级 L' + e.level + '（点一下改：1→' + maxLevel + '）';
    /* 改层级：视图跟着条目走 —— 不然它在当前层级里「消失」，
       用户看着像没反应（连点几下也看不到变化）。 */
    lvl.onclick = () => {
      e.level = e.level >= maxLevel ? 1 : e.level + 1;
      S.tocLevel = e.level;
      saveTocLevel();
      tocChanged();
      toast('「' + (e.title || '（未命名）') + '」移到 ' + e.level + ' 级');
    };

    const title = document.createElement('span');
    title.className = 'bv-toc-title';
    title.textContent = e.title || '（未命名）';
    title.title = '点一下改标题';
    title.onclick = () => editTocField(row, e, 'title');

    /* 页号：单击跳到那一页，双击改页号。
       跳转是主行为（目录的意义就是带你去那一页），编辑挪到双击。 */
    /* 页号只给最下一级（叶子）：父级的那条只是分组，页码在它孩子上。 */
    const pg = document.createElement('span');
    pg.className = 'bv-toc-page num';
    if (node.isLeaf) {
      pg.textContent = 'p.' + e.page;
      pg.title = '点一下跳到第 ' + e.page + ' 页 · 双击改页号';
      pg.onclick = () => { if (e.page) bookSetPage(e.page, {scroll: true}); };
      pg.ondblclick = () => editTocField(row, e, 'page');
    } else {
      pg.textContent = '';
      pg.title = '这一级只是分组，页码在下面最下一级';
    }

    /* 印刷页：书页上印的那个页号 = PDF 页 − offset（= 印刷页 + offset = PDF 页）。
       offset 探不到时是「—」。
       点它可以改（改的是印刷页号，PDF 页号自动跟着换算）。 */
    const pr = document.createElement('span');
    pr.className = 'bv-toc-print num';
    /* 印刷页：条目自带 printed_page 就用它（目录看图 v1 的产出），
       否则按 offset 换算；两者都没有就显示「—」（offset 还没探测到）。 */
    /* 印刷页号只有一个来源说了算：offset（前置页数）。
       条目自带的 printed_page 只在 offset 还没探到时顶一下 ——
       否则用户改了 offset、这一栏的数字却不动（两个来源打架）。 */
    const printedNo = !node.isLeaf ? null
                  : (off != null ? e.page - off
                     : (e.printed_page != null ? e.printed_page : null));
    pr.textContent = printedNo == null ? (node.isLeaf ? '印刷 —' : '') : '印刷 ' + printedNo;
    pr.title = printedNo == null
      ? '印刷页号（PDF 页 = 印刷页 + offset，offset 是前置页数）。offset 还没探测到，先在下面填。'
      : '印刷页号（点一下改）';
    if (printedNo != null) {
      pr.onclick = () => editTocPrint(row, e);
      pr.classList.add('editable');
    }

    const del = document.createElement('button');
    del.className = 'bv-toc-del';
    del.textContent = '×';
    del.title = '删掉这一条';
    del.onclick = () => {
      S.toc.entries = S.toc.entries.filter(x => x !== e);
      tocChanged();
    };

    row.appendChild(lvl);
    row.appendChild(title);
    row.appendChild(pg);
    row.appendChild(pr);
    row.appendChild(del);
    list.appendChild(row);
  }
}

/* 点标题/页号 → 变成输入框；失焦或回车保存，Esc 放弃。 */
function editTocField(row, e, field) {
  const span = row.querySelector(field === 'title' ? '.bv-toc-title' : '.bv-toc-page');
  if (!span) return;
  const inp = document.createElement('input');
  inp.className = 'bv-toc-input' + (field === 'page' ? ' num' : '');
  inp.value = field === 'title' ? (e.title || '') : String(e.page);
  if (field === 'page') { inp.type = 'text'; inp.inputMode = 'numeric'; }
  span.replaceWith(inp);
  inp.focus();
  if (inp.select) inp.select();
  let done = false;
  const commit = () => {
    if (done) return;
    done = true;
    const v = inp.value.trim();
    if (field === 'title') {
      e.title = v;
    } else {
      const n = parseInt(v, 10);
      if (Number.isFinite(n) && n >= 0) e.page = n;
      else toast('页号只能是数字');
    }
    tocChanged();
  };
  inp.addEventListener('blur', commit);
  inp.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') { ev.preventDefault(); inp.blur(); }
    else if (ev.key === 'Escape') { ev.preventDefault(); done = true; tocChanged(); }
  });
}

/* 改「印刷页号」：输入的是书页上印的页号，PDF 页号按 offset 换算回去。
   （人对着书翻页时看到的是印刷页号，不是 PDF 页号。） */
function editTocPrint(row, e) {
  const span = row.querySelector('.bv-toc-print');
  if (!span) return;
  const off = S.toc.offset;
  if (off == null) { toast('先在下面填 offset'); return; }
  const inp = document.createElement('input');
  inp.className = 'bv-toc-input num';
  inp.type = 'text';
  inp.inputMode = 'numeric';
  inp.value = String((e.page || 0) - off);
  span.replaceWith(inp);
  inp.focus();
  if (inp.select) inp.select();
  let done = false;
  const commit = () => {
    if (done) return;
    done = true;
    const n = parseInt(inp.value.trim(), 10);
    if (Number.isFinite(n)) {
      const pdf = n + off;      /* 印刷页 → PDF 页：PDF = 印刷 + 前置页数 */
      if (pdf >= 0) e.page = pdf;
      else toast('换算出来的 PDF 页号是负的，检查一下 offset');
    } else {
      toast('印刷页号只能是数字');
    }
    tocChanged();
  };
  inp.addEventListener('blur', commit);
  inp.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') { ev.preventDefault(); inp.blur(); }
    else if (ev.key === 'Escape') { ev.preventDefault(); done = true; tocChanged(); }
  });
}

/* 目录有改动：版本号 +1（让 renderToc 重画）+ 存进 STORE.toc。 */
function tocChanged() {
  S.tocVer++;
  saveToc();
  renderToc();
  renderBookPage();       // 印刷页号会跟着 offset 变
  renderPanel();          // 左栏那份「父 | 子」清单要跟着变
}

/* 「＋」加一条：默认空标题、页码 = 当前选中页。 */
function addTocEntry() {
  const e = {level: 1, title: '', page: S.page};
  S.toc.entries.push(e);
  tocChanged();
  const list = $('bv-toc-list');
  list.scrollTop = list.scrollHeight;
  const rows = list.querySelectorAll('.bv-toc-row');
  const row = rows[rows.length - 1];
  if (row) editTocField(row, e, 'title');
}

/* 跳到页码：先当 PDF 页号；不是的话，offset 在就按印刷页号换算。 */
function doJump() {
  const inp = $('bv-jump-input');
  const v = parseInt(inp.value, 10);
  if (!Number.isFinite(v)) { toast('页码得是数字'); return; }
  let target = v;
  if (!pageEntry(v) && S.toc.offset != null && pageEntry(v + S.toc.offset)) target = v + S.toc.offset;
  bookSetPage(target, {scroll: true});
  inp.blur();
}

/* 书视图里那些固定控件的绑定（模板里是静态的，绑一次就够）。 */
/* 翻页统一由底部缩略图条负责：大图区不再单独放翻页按钮（跟缩略图条重复）。 */
$('bv-scroll-prev').onclick = () => scrollThumbs(-1);
$('bv-scroll-next').onclick = () => scrollThumbs(1);
$('bv-jump-go').onclick = doJump;
$('bv-jump-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); doJump(); }
});
$('bv-toc-add').onclick = addTocEntry;
$('bv-offset').addEventListener('change', () => {
  const v = $('bv-offset').value.trim();
  const n = v === '' ? null : parseInt(v, 10);
  /* offset 是前置页数，一律正数 */
  S.toc.offset = Number.isFinite(n) ? Math.abs(n) : null;
  tocChanged();
});
$('bv-offset').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); $('bv-offset').blur(); }
});
/* 点大图 → 全屏放大（复用现成的 #imgfs 层，不另写一套全屏）。 */
$('bv-img').onclick = () => {
  const e = pageEntry(S.page);
  if (!e || !e.image) return;
  openFullscreen({src: e.image, width: e.width, height: e.height, boxes: []});
};

/* ------------------------------ flip / paging ----------------------- */
function flip(toBack) {
  S.flipped = toBack == null ? !S.flipped : !!toBack;
  renderStage();
}
$('face-front').onclick = () => flip(true);
/* 背面点一下翻回正面（图片和「⋯」自己处理，见各自的事件）。 */
$('face-back').onclick = () => flip(false);
async function goTo(i) {
  if (!S.cards.length) return;
  S.idx = (i + S.cards.length) % S.cards.length;
  S.flipped = false; S.menuOpen = false; $('menu').classList.remove('open');
  await loadCard();
  writeHash();
  renderStage(); renderPanel();
}
const next = () => goTo(S.idx + 1);
const prev = () => goTo(S.idx - 1);
$('next-btn').onclick = next;
$('prev-btn').onclick = prev;

/* horizontal swipe on the card (phone) — ~50px threshold, ignore vertical */
(() => {
  let x0 = null, y0 = null;
  const wrap = $('card-wrap');
  wrap.addEventListener('pointerdown', (e) => {
    if (e.pointerType === 'mouse') return;      // touch/pen only — desktop uses ‹ ›
    x0 = e.clientX; y0 = e.clientY;
  });
  wrap.addEventListener('pointerup', (e) => {
    if (x0 == null) return;
    const dx = e.clientX - x0, dy = e.clientY - y0;
    x0 = y0 = null;
    if (Math.abs(dx) >= 50 && Math.abs(dx) > Math.abs(dy)) {
      dx < 0 ? next() : prev();
    }
  });
  wrap.addEventListener('pointercancel', () => { x0 = y0 = null; });
})();

/* ------------------------------ rating ------------------------------ */
async function rate(rating) {
  if (S.busy || !S.card) return;
  S.busy = true; renderStage();
  try {
    const id = S.card.id;
    await api.rate(id, rating);
    // refresh list + counts, then auto-advance in the filtered list (§5)
    const r = await api.cards(S.scope, S.off);
    S.cards = r.cards; S.tierCounts = r.tier_counts;
    // still in the filtered set ⇒ the card after it; left the set (its tier
    // is off) ⇒ the card now at the same index. Past the end ⇒ All done.
    const k = S.cards.findIndex(c => c.id === id);
    S.idx = Math.min(k >= 0 ? k + 1 : S.idx, S.cards.length);
    S.flipped = false;
    await loadCard();
    writeHash();
  } catch (e) { apiErr(e); }
  S.busy = false;
  renderStage(); renderPanel();
}

/* ------------------------------ ⋯ menu ------------------------------ */
function toggleMenu(open) {
  S.menuOpen = open == null ? !S.menuOpen : open;
  const m = $('menu');
  if (!S.menuOpen) { m.classList.remove('open'); return; }
  const ignored = S.card && S.card.state && S.card.state.ignored;
  const items = [
    {label: ignored ? 'Unignore' : 'Ignore', fn: doIgnore},
    {label: 'Skip', fn: () => next()},
    {label: 'Edit card', fn: openEdit},
  ];
  m.innerHTML = items.map((it, i) => `<button class="mi" data-i="${i}">${it.label}</button>`).join('');
  [...m.children].forEach((b, i) => b.onclick = () => { toggleMenu(false); items[i].fn(); });
  const wrap = $('card-wrap').getBoundingClientRect();
  m.style.top = (8 + 32 + 4) + 'px';
  m.style.right = '8px';
  m.classList.add('open');
}
$('more-btn').onclick = (e) => { e.stopPropagation(); toggleMenu(); };
document.addEventListener('click', (e) => {
  if (S.menuOpen && !$('menu').contains(e.target)) toggleMenu(false);
});

async function doIgnore() {
  if (!S.card || !S.card.state) return;
  const id = S.card.id;
  const nowIgnored = !S.card.state.ignored;
  try {
    await api.ignore(id, nowIgnored);
    const r = await api.cards(S.scope, S.off);
    S.cards = r.cards; S.tierCounts = r.tier_counts;
    // keep place on the same card if still visible, else stay at index
    const ni = S.cards.findIndex(c => c.id === id);
    S.idx = ni >= 0 ? ni : Math.min(S.idx, Math.max(0, S.cards.length - 1));
    await loadCard(); writeHash();
  } catch (e) { apiErr(e); }
  renderStage(); renderPanel();
}

/* ------------------------------ edit -------------------------------- */
function openEdit() {
  if (!S.card) return;
  const c = S.card;
  S.editing = true;
  $('e-front').value = c.front || '';
  $('e-back').value = c.back || '';
  $('e-front-err').textContent = '';
  // tags chip input
  const box = $('e-tags');
  box.querySelectorAll('.chip').forEach(x => x.remove());
  for (const t of (c.tags || [])) addTagChip(t);
  // library dropdown — same book only
  const sel = $('e-lib');
  sel.innerHTML = '';
  const book = S.tree.find(b => b.libraries.some(l => l.id === c.library_id));
  for (const l of (book ? book.libraries : [])) {
    const o = document.createElement('option');
    o.value = l.id; o.textContent = l.name;
    if (l.id === c.library_id) o.selected = true;
    sel.appendChild(o);
  }
  renderStage();
  $('e-front').focus();
}
function eTags() {
  return [...$('e-tags').querySelectorAll('.chip')].map(x => x.dataset.t);
}
function addTagChip(t) {
  t = t.trim(); if (!t) return;
  const chip = document.createElement('span');
  chip.className = 'chip'; chip.dataset.t = t;
  chip.innerHTML = `${esc(t)}<span class="x">×</span>`;
  chip.querySelector('.x').onclick = () => chip.remove();
  $('e-tags').insertBefore(chip, $('e-tag-input'));
}
$('e-tag-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault(); addTagChip(e.target.value); e.target.value = '';
  } else if (e.key === 'Backspace' && !e.target.value) {
    const chips = $('e-tags').querySelectorAll('.chip');
    if (chips.length) chips[chips.length - 1].remove();
  }
});
$('e-save').onclick = async () => {
  const front = $('e-front').value.trim();
  if (front.length < 1) { $('e-front-err').textContent = 'Front cannot be empty'; return; }
  if (front.length > 500) { $('e-front-err').textContent = 'Front must be at most 500 characters'; return; }
  $('e-save').disabled = true; $('e-save').textContent = 'Saving…';
  try {
    const id = S.card.id;
    await api.put(id, {front, back: $('e-back').value, tags: eTags(), library_id: $('e-lib').value});
    const r = await api.cards(S.scope, S.off);
    S.cards = r.cards; S.tierCounts = r.tier_counts;
    const ni = S.cards.findIndex(c => c.id === id);
    if (ni >= 0) S.idx = ni;
    S.editing = false;
    await loadCard(); writeHash();
    renderPanel();
  } catch (e) { $('e-front-err').textContent = e.message; }
  $('e-save').disabled = false; $('e-save').textContent = 'Save';
  renderStage();
};
const closeEdit = () => { S.editing = false; renderStage(); };
$('e-cancel').onclick = closeEdit;
$('edit-back').onclick = closeEdit;

/* ------------------------------ drawer ------------------------------ */
function openDrawer(open) {
  $('drawer').classList.toggle('open', open);
  $('scrim').classList.toggle('open', open);
}
function closeDrawer() { openDrawer(false); }
$('drawer-btn').onclick = () => openDrawer(true);
/* 层级下拉：选一级 → 左右栏一起切；「＋ 增加一级」→ 层级上限 +1，
   之后循环点数字徽章就能调到新的一级。 */
$('bv-level').onchange = (e) => {
  const v = e.target.value;
  if (v === 'add') {
    S.tocMax = tocMaxLevel() + 1;
    S.tocLevel = S.tocMax;
  } else {
    S.tocLevel = parseInt(v, 10) || 1;
  }
  saveTocLevel();
  S.tocVer++;
  renderToc();
  renderPanel();
  renderLevelSelect();
};

$('scope-label').onclick = (e) => {
  if (window.innerWidth < 768) { openDrawer(true); return; }
  /* 点书名 → 改名（一本书一个 index 页，进书视图走左栏「读原书」那一行）。 */
  if (e.target && e.target.closest && e.target.closest('.book')) {
    const inp = document.createElement('input');
    inp.className = 'bk-title-input';
    inp.value = bookMeta().title;
    const span = e.target.closest('.book');
    span.replaceWith(inp);
    inp.focus();
    if (inp.select) inp.select();
    let done = false;
    const commit = () => {
      if (done) return;
      done = true;
      setBookMeta({title: inp.value.trim()});
    };
    inp.onblur = commit;
    inp.onkeydown = (ev) => {
      if (ev.key === 'Enter') { ev.preventDefault(); commit(); }
      if (ev.key === 'Escape') { ev.preventDefault(); done = true; renderHeader(); }
    };
  }
};
$('scrim').onclick = closeDrawer;

/* ---- 两侧栏可拉伸 ------------------------------------------------ */
function initSplitter(handleId, cssVar, side, min, max) {
  const h = $(handleId);
  if (!h) return;
  let startX = 0, startW = 0;
  const onMove = (e) => {
    const dx = e.clientX - startX;
    const w = Math.max(min, Math.min(max, startW + (side === 'right' ? -dx : dx)));
    document.documentElement.style.setProperty(cssVar, w + 'px');
  };
  const onUp = () => {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    document.body.classList.remove('resizing');
    try {
      const w = getComputedStyle(document.documentElement).getPropertyValue(cssVar).trim();
      STORE.ui = Object.assign(STORE.ui || {}, {[cssVar]: w});
      saveStore();
    } catch (e) { /* 没 localStorage 就算了 */ }
  };
  h.addEventListener('mousedown', (e) => {
    e.preventDefault();
    startX = e.clientX;
    const cur = parseInt(getComputedStyle(document.documentElement).getPropertyValue(cssVar), 10);
    startW = isNaN(cur) ? (side === 'right' ? 320 : 260) : cur;
    document.body.classList.add('resizing');
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
  /* 记着的宽度（同一本书的静态页跨次打开保留） */
  try {
    const saved = (STORE.ui || {})[cssVar];
    if (saved) document.documentElement.style.setProperty(cssVar, saved);
  } catch (e) { /* ignore */ }
}
initSplitter('panel-split', '--panel-w', 'left', 160, 560);
initSplitter('toc-split', '--toc-w', 'right', 220, 720);

/* ------------------------------ keyboard ---------------------------- */
document.addEventListener('keydown', (e) => {
  if (S.editing) {
    if (e.key === 'Escape') closeEdit();
    return;
  }
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
  /* 书视图里：← → 切页、Esc 关放大；别的键一律不碰
     （别让 → 把卡片翻了 —— 书视图下卡片根本不显示）。 */
  if (isBookScope()) {
    if (e.key === 'ArrowRight') { e.preventDefault(); bookSetPage(S.page + 1, {scroll: true}); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); bookSetPage(S.page - 1, {scroll: true}); }
    else if (e.key === 'Escape') { $('imgfs').classList.remove('open'); }
    else if (e.key === ' ') e.preventDefault();
    return;
  }
  const has = S.cards.length > 0;
  if (e.key === ' ' && has) { e.preventDefault(); flip(); }
  else if (e.key === 'ArrowRight' && has) next();
  else if (e.key === 'ArrowLeft' && has) prev();
  else if (S.flipped && ['1','2','3','4'].includes(e.key)) {
    rate(['unclear','forgot','good','easy'][+e.key - 1]);
  } else if (e.key === 'Escape') { toggleMenu(false); closeDrawer(); $('imgfs').classList.remove('open'); }
});

/* keep the left word list pointing at the right scope */
const _selOrig = selectScope;
selectScope = async function(scope, wantCard, wantPage) {
  await _selOrig(scope, wantCard, wantPage);
  /* 左栏跟着当前 scope 更新「哪本书 / 哪个词库」，但**层级不跟着跳**：
     左栏默认停在目录树那一级（用户要随时看得见目录）；
     只有用户自己点了某个词库，才进卡片那一级。
     （以前 scope 一变就跳到 words，打开最终包只剩三张卡，看不见目录。） */
  if (scope && scope.lib) {
    for (const b of S.tree) for (const l of b.libraries)
      if (l.id === scope.lib) {
        S.panel.book = b;
        if (S.panel.level === 'words') S.panel.lib = l;
      }
  } else if (scope && scope.book) {
    const b = S.tree.find(x => x.id === scope.book);
    if (b) S.panel.book = b;
  }
  renderPanel();
};

let rsT;
window.addEventListener('resize', () => { clearTimeout(rsT); rsT = setTimeout(renderStage, 120); });
window.addEventListener('hashchange', () => {
  const h = readHash();
  if (!h || !S.scope) return;
  const cur = S.scope.lib || S.scope.book;
  if (h.kind === 'pages') {
    if (!S.scope || !S.scope.pages || S.scope.pages[0] !== h.pages[0] || S.scope.pages[1] !== h.pages[1]) {
      selectScope({pages: h.pages}, h.card, null);
    }
    return;
  }
  if (h.id !== cur) { selectScope(h.kind === 'lib' ? {lib: h.id} : {book: h.id}, h.card, h.page); return; }
  if (isBookScope()) {
    if (h.kind === 'book' && h.page && h.page !== S.page) bookSetPage(h.page, {scroll: true});
    return;
  }
  const i = S.cards.findIndex(c => c.id === h.card);
  if (i >= 0 && i !== S.idx) goTo(i);
});

boot();