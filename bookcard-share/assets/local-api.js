'use strict';
/* ======================================================================
   bookcard-share — 本地数据层（没有服务端、没有 SQLite）

   页面里的数据是**构建时内嵌**的（<script type="application/json"
   id="bookcard-data">），运行时只有两个后端：localStorage（存改动与进度）
   和「导出 / 导入 edits.json」（跨浏览器、跨机器搬数据）。

   对外仍然提供 browse-lite 契约 §4 的六个方法（tree / cards / card /
   rate / ignore / put），语义逐条对齐引擎里的实现：
     · tier 判定      → src/bookcard/browse/db.py:98-113
     · SM-2 排程      → src/bookcard/browse/sm2.py:114-172
     · 评分副作用     → src/bookcard/browse/db.py:715-807
     · 改卡校验/副作用 → src/bookcard/browse/db.py:833-897
   ====================================================================== */

const DATA = JSON.parse(document.getElementById('bookcard-data').textContent);

/* 引擎里 db.py 的常量，原样搬过来。 */
const MAX_FRONT_LEN = 500;
const BACK_PREVIEW_LEN = 200;
const UI_RATINGS = ['unclear', 'forgot', 'good', 'easy'];

/* ============================ 存储 ============================== */

/* localStorage 的 key：按书分。file:// 下 Safari 会禁 localStorage，
   那边读写都抛异常 —— 捕获掉，不让页面挂掉（导出/导入仍然可用）。 */
const STORE_KEY = 'bookcard-share:' + (DATA.book && DATA.book.id || 'book');
let STORE = { v: 1, cards: {}, state: {}, review_log: [], book: null, ui: {} };

function loadStore() {
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    if (!raw) return;
    const p = JSON.parse(raw);
    if (p && p.v === 1) {
      STORE = {
        v: 1,
        cards: p.cards || {},
        state: p.state || {},
        review_log: p.review_log || [],
        book: p.book || null,
        ui: p.ui || {},
      };
    }
  } catch (e) { /* file:// + Safari：没 localStorage，就当没有历史 */ }
}

function saveStore() {
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify(STORE));
  } catch (e) { /* 同上：存不了就算了，不影响浏览 */ }
}

/* ============================ 内存模型 =========================== */

const CARDS = new Map();   /* card_id → 卡 */
const STATES = new Map();  /* card_id → 学习状态 */
const LIBS = new Map();    /* library_id → 词库 */

function defaultState() {
  return {
    repetition: 0, ease: 1.8, interval_days: 0,
    next_review_at: null, last_reviewed_at: null,
    reviews: 0, lapses: 0, ignored: 0, mastered: 0,
  };
}

/* 把内嵌数据 + 本地改动铺进内存。 */
function bootstrap() {
  const book = DATA.book || {};
  (DATA.libraries || []).forEach(l => LIBS.set(l.id, l));
  (DATA.cards || []).forEach(c => {
    CARDS.set(c.id, {
      id: c.id,
      library_id: c.library_id,
      /* 卡所在的页 —— 目录条目靠它映射到卡片 */
      page: c.page == null ? null : parseInt(c.page, 10),
      front: c.front,
      back: c.back || '',
      tags: Array.isArray(c.tags) ? c.tags.slice() : [],
      sort_order: c.sort_order || 0,
      scene: c.scene || null,
      edited: false,
    });
    STATES.set(c.id, defaultState());
  });

  loadStore();

  /* 改动过的卡：以本地为准（edited=1 的卡不覆盖 front/back/tags —— 同 db.py 的
     「重导不覆盖用户改过的卡」口径，这里反过来：包是底稿，本地是改稿）。 */
  Object.keys(STORE.cards || {}).forEach(id => {
    const c = CARDS.get(id);
    if (!c) return;
    const ov = STORE.cards[id] || {};
    if (typeof ov.front === 'string') c.front = ov.front;
    if (typeof ov.back === 'string') c.back = ov.back;
    if (Array.isArray(ov.tags)) c.tags = ov.tags.slice();
    if (typeof ov.library_id === 'string' && LIBS.has(ov.library_id)) c.library_id = ov.library_id;
    c.edited = !!ov.edited;
  });

  /* 学习状态：本地有就用本地的。 */
  Object.keys(STORE.state || {}).forEach(id => {
    if (!STATES.has(id)) return;
    STATES.set(id, Object.assign(defaultState(), STORE.state[id]));
  });
}

/* ============================ 本地 api =========================== */
/* 与 browse-lite 契约 §4 同形状；页面其余部分一行都不用改。 */

function cardDetail(id) {
  const c = CARDS.get(id);
  if (!c) return null;
  const st = STATES.get(id) || defaultState();
  return {
    id: c.id,
    library_id: c.library_id,
    book_id: DATA.book ? DATA.book.id : '',
    front: c.front,
    back: c.back,
    tags: c.tags.slice(),
    tier: tierOf(st),
    scene: c.scene,
    state: Object.assign({}, st),
    edited: c.edited,
  };
}

function backPreview(back) {
  const text = back || '';
  return text.length <= BACK_PREVIEW_LEN ? text : text.slice(0, BACK_PREVIEW_LEN) + '…';
}

function makeLocalApi() {
  return {
    /* 界面状态（目录看第几级等）—— 存本地，刷新后还在 */
    ui: () => Object.assign({}, STORE.ui),
    setUi: (patch) => {
      STORE.ui = Object.assign({}, STORE.ui, patch || {});
      saveStore();
      return STORE.ui;
    },
    tree: () => [{
      id: DATA.book.id,
      title: DATA.book.title,
      subtitle: DATA.book.subtitle || '',
      visibility: DATA.book.visibility || 'private',
      scene_mode: DATA.book.scene_mode || 'none',
      libraries: (DATA.libraries || []).map(l => ({
        id: l.id, name: l.name, count: l.count,
        /* 章节的页范围 —— 左栏拿它把目录条目对上词库 */
        page_start: l.page_start, page_end: l.page_end,
      })),
    }],

    cards: (scope, off) => {
      /* 排序：词库序 → 卡序 → 卡 id（对齐 db.py:627）。 */
      const libOrder = new Map((DATA.libraries || []).map(l => [l.id, l.sort_order || 0]));
      let all = (DATA.cards || []).map(c => CARDS.get(c.id)).filter(Boolean);
      if (scope && scope.pages) {
        /* 目录条目 → 页范围 → 卡片（页面上的浏览只是映射，不是第二套结构） */
        const lo = parseInt(scope.pages[0], 10);
        const hi = parseInt(scope.pages[1], 10);
        all = all.filter(c => c.page != null && c.page >= lo && c.page <= hi);
      } else if (scope && scope.lib) {
        all = all.filter(c => c.library_id === scope.lib);
      } else if (scope && scope.book) {
        const bookId = scope.book;
        const inBook = new Set((DATA.libraries || [])
          .filter(l => l.book_id === bookId || l.id.indexOf(bookId + '-') === 0)
          .map(l => l.id));
        all = all.filter(c => inBook.has(c.library_id));
      }
      all.sort((a, b) => {
        const la = libOrder.get(a.library_id) || 0, lb = libOrder.get(b.library_id) || 0;
        if (la !== lb) return la - lb;
        if (a.sort_order !== b.sort_order) return a.sort_order - b.sort_order;
        return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
      });

      /* tier_counts 统计的是**筛选前**整个作用域（db.py:598,634-640）。 */
      const tier_counts = {T1: 0, T2: 0, T4: 0, T6: 0, T7: 0};
      all.forEach(c => { tier_counts[tierOf(STATES.get(c.id))]++; });

      const skip = off || new Set();
      const list = all.filter(c => !skip.has(tierOf(STATES.get(c.id))));
      return {
        cards: list.map(c => ({
          id: c.id, front: c.front, back_preview: backPreview(c.back),
          tier: tierOf(STATES.get(c.id)), library_id: c.library_id,
          sort_order: c.sort_order,
        })),
        tier_counts: tier_counts,
      };
    },

    card: (id) => {
      const d = cardDetail(id);
      if (!d) throw new Error('卡不存在：' + id);
      return d;
    },

    rate: (id, rating) => {
      const c = CARDS.get(id);
      if (!c) throw new Error('卡不存在：' + id);
      if (UI_RATINGS.indexOf(rating) < 0) {
        throw new Error('rating 只能是 ' + UI_RATINGS.join(', ') + '：' + rating);
      }
      const st = Object.assign(defaultState(), STATES.get(id));
      const now = Date.now();
      const sm2r = sm2RatingOf(rating);
      const r = computeSm2(sm2r, st.repetition, st.ease, st.interval_days, now);

      /* 掌握标记（db.py:752-760）：easy ⇒ 1；unclear/forgot ⇒ 0；good 不动。 */
      let mastered = st.mastered;
      if (rating === 'easy') mastered = 1;
      else if (rating === 'unclear' || rating === 'forgot') mastered = 0;

      const next = {
        repetition: r.repetition,
        ease: r.easeFactor,
        interval_days: r.intervalDays,
        next_review_at: r.nextReviewAtMs,
        last_reviewed_at: now,
        reviews: st.reviews + 1,
        lapses: st.lapses + (sm2r === 'again' ? 1 : 0),
        ignored: st.ignored,
        mastered: mastered,
      };
      STATES.set(id, next);
      STORE.state[id] = next;
      STORE.review_log.push({
        card_id: id, rating: rating, sm2: sm2r,
        reviewed_at: now, interval_days: r.intervalDays,
      });
      saveStore();
      return cardDetail(id);
    },

    ignore: (id, ignored) => {
      const c = CARDS.get(id);
      if (!c) throw new Error('卡不存在：' + id);
      const st = Object.assign(defaultState(), STATES.get(id));
      st.ignored = ignored ? 1 : 0;
      STATES.set(id, st);
      STORE.state[id] = st;
      saveStore();
      return cardDetail(id);
    },

    put: (id, data) => {
      const c = CARDS.get(id);
      if (!c) throw new Error('卡不存在：' + id);
      const upd = {};
      if (data.front !== undefined && data.front !== null) {
        const text = String(data.front);
        if (text.length < 1 || text.length > MAX_FRONT_LEN) {
          throw new Error('front 长度必须是 1–' + MAX_FRONT_LEN + '（现在是 ' + text.length + '）');
        }
        upd.front = text;
      }
      if (data.back !== undefined && data.back !== null) upd.back = String(data.back);
      if (data.tags !== undefined && data.tags !== null) {
        if (!Array.isArray(data.tags)) throw new Error('tags 必须是数组');
        upd.tags = data.tags.map(t => String(t));
      }
      if (data.library_id !== undefined && data.library_id !== null) {
        if (!LIBS.has(data.library_id)) throw new Error('词库不存在：' + data.library_id);
        upd.library_id = data.library_id;
      }
      Object.assign(c, upd);
      c.edited = true;                       /* db.py:883 —— 改过就永久标记 */
      STORE.cards[id] = {
        front: c.front, back: c.back, tags: c.tags.slice(),
        library_id: c.library_id, edited: true,
      };
      saveStore();
      return cardDetail(id);
    },
  };
}

/* ============================ 导出 / 导入 ======================== */

function downloadText(name, text, mime) {
  const blob = new Blob([text], { type: (mime || 'application/json') + ';charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
}

/* 导出：只带**动过**的卡 + 非初始的学习状态 + 全部评分记录。 */
function exportEdits() {
  const edited = [];
  Object.keys(STORE.cards || {}).forEach(id => {
    const c = CARDS.get(id);
    if (!c) return;
    edited.push({
      id: id, front: c.front, back: c.back, tags: c.tags.slice(),
      library_id: c.library_id, edited: true,
    });
  });
  const state = {};
  Object.keys(STORE.state || {}).forEach(id => { state[id] = STORE.state[id]; });

  const payload = {
    format: 'bookcard-share/edits@1',
    book: Object.assign({
      id: DATA.book.id, title: DATA.book.title,
      subtitle: DATA.book.subtitle || '', domain: DATA.book.domain || '',
      level: DATA.book.level || '', tags: (DATA.book.tags || []).slice(),
      publisher: DATA.book.publisher || '', edition: DATA.book.edition || '',
    }, STORE.book || {}),
    exported_at: new Date().toISOString(),
    cards: edited,
    state: state,
    review_log: STORE.review_log || [],
  };
  /* 目录（TOC）：书视图里改的标题 / 页码 / offset 都在这儿。
     目录不是卡，是一本书的结构 —— 整段带走，回流时整段盖回去。
     **即使一条没改也要带走** —— 用户确认过的那份目录就是结论，
     agent 拿它写回工作目录；只在「改过」时才带的话，
     用户没动过目录反而导不出目录。 */
  const tocSrc = STORE.toc || (DATA.toc && DATA.toc.entries ? DATA.toc : null);
  if (tocSrc) {
    payload.toc = {
      offset: tocSrc.offset == null ? null : tocSrc.offset,
      source: tocSrc.source || (DATA.toc && DATA.toc.source) || '',
      entries: (tocSrc.entries || []).map(e => ({
        level: e.level, title: e.title, page: e.page,
      })),
    };
  }
  const base = String(DATA.book.id || 'bookcard');
  downloadText(base + '-edits.json', JSON.stringify(payload, null, 2));
  const tocN = payload.toc ? payload.toc.entries.length : 0;
  toast('已导出 ' + edited.length + ' 处改动' + (tocN ? ' + 目录 ' + tocN + ' 条' : ''));
}

/* 导入：把别的机器/别的编辑合进来（同名卡以导入的为准）。 */
function importEdits(text) {
  let payload;
  try { payload = JSON.parse(text); }
  catch (e) { toast('不是合法的 JSON'); return false; }
  if (!payload || typeof payload !== 'object') { toast('文件内容不对'); return false; }
  if (payload.format && String(payload.format).indexOf('bookcard-share/edits') !== 0) {
    toast('不是 bookcard-share 的改动文件'); return false;
  }
  (payload.cards || []).forEach(ov => {
    if (!ov || !ov.id || !CARDS.has(ov.id)) return;
    const c = CARDS.get(ov.id);
    if (typeof ov.front === 'string') c.front = ov.front;
    if (typeof ov.back === 'string') c.back = ov.back;
    if (Array.isArray(ov.tags)) c.tags = ov.tags.slice();
    if (typeof ov.library_id === 'string' && LIBS.has(ov.library_id)) c.library_id = ov.library_id;
    c.edited = true;
    STORE.cards[ov.id] = {
      front: c.front, back: c.back, tags: c.tags.slice(),
      library_id: c.library_id, edited: true,
    };
  });
  Object.keys(payload.state || {}).forEach(id => {
    if (!STATES.has(id)) return;
    const st = Object.assign(defaultState(), payload.state[id]);
    STATES.set(id, st);
    STORE.state[id] = st;
  });
  if (Array.isArray(payload.review_log)) {
    STORE.review_log = payload.review_log;
  }
  /* 目录：整段盖回去（书视图会从 STORE.toc 读）。 */
  if (payload.toc && typeof payload.toc === 'object') {
    STORE.toc = {
      offset: payload.toc.offset == null ? null : payload.toc.offset,
      entries: (payload.toc.entries || []).map(e => ({
        level: e.level || 1, title: String(e.title || ''), page: e.page,
      })),
    };
  }
  saveStore();
  return true;
}

function wireToolbar() {
  const btnExport = document.getElementById('btn-export');
  const btnImport = document.getElementById('btn-import');
  const fileInput = document.getElementById('file-import');
  if (btnExport) btnExport.addEventListener('click', exportEdits);
  if (btnImport && fileInput) {
    btnImport.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
      const f = fileInput.files && fileInput.files[0];
      if (!f) return;
      const reader = new FileReader();
      reader.onload = () => {
        if (importEdits(String(reader.result || ''))) {
          toast('已导入改动');
          if (typeof selectScope === 'function' && S.scope) selectScope(S.scope, S.card && S.card.id);
        }
      };
      reader.readAsText(f, 'utf-8');
      fileInput.value = '';
    });
  }
}

/* ============================ 启动 ============================== */

bootstrap();
const api = makeLocalApi();
wireToolbar();
