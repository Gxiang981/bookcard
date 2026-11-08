"""卡片审核 HTML 的静态资源（CSS / JS / 页面骨架）。

拆成单独模块是为了让 ``review.py`` 只管数据和契约；JS 里不碰 DOM 的纯逻辑放在
``JS_CORE``，可以单独拿到 Node 里跑单测（见 ``tests/test_review_html.py``）。

约定：偏移一律按 Python 码点下标；JS 侧用 ``Array.from`` 按码点切分，
避免 emoji / 扩展区汉字的 UTF-16 代理对把偏移算错。

界面全中文，所有资源内联，不联网、不依赖 CDN。
"""

from __future__ import annotations

CSS = """
* { box-sizing: border-box; }

/* 作者样式里的 display 会盖掉 UA 的 [hidden]{display:none}，这里兜住。 */
[hidden] { display: none !important; }

body {
  margin: 0;
  background: #f4f6f8;
  color: #1f2328;
  font: 15px/1.8 "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif;
}

code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }

/* ---------- 顶栏 ---------- */

.top {
  position: sticky;
  top: 0;
  z-index: 30;
  background: #fff;
  border-bottom: 1px solid #e3e6ea;
  padding: 10px 20px 8px;
}

.top-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.top h1 { margin: 0; font-size: 18px; }
.sub { color: #6b7280; font-size: 13px; margin-top: 2px; }
.sub code { background: #f3f4f6; border-radius: 4px; padding: 0 4px; }

.actions { display: flex; gap: 8px; flex-shrink: 0; }
button {
  font: inherit;
  padding: 5px 12px;
  border: 1px solid #d0d5db;
  background: #fff;
  border-radius: 6px;
  cursor: pointer;
}
button:hover { background: #f3f4f6; }
button.primary { background: #2563eb; border-color: #2563eb; color: #fff; }
button.primary:hover { background: #1d4ed8; }

.progress { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.prog {
  display: flex;
  align-items: baseline;
  gap: 4px;
  background: #f3f4f6;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 12px;
  color: #6b7280;
}
.prog b { font-size: 14px; color: #1f2328; }
.prog.pass b { color: #16a34a; }
.prog.fail b { color: #dc2626; }
.prog.agree b { color: #2563eb; }

.legend {
  display: flex; flex-wrap: wrap; gap: 10px; margin-top: 8px;
  font-size: 12px; color: #6b7280;
}
.lg { display: inline-flex; align-items: center; gap: 4px; }
.lg .sw { width: 14px; height: 12px; border-radius: 3px; display: inline-block; }
.lg .sw.kw { background: #fde68a; }
.lg .sw.ev { background: #dbeafe; }
.lg .hint { margin-left: auto; }

/* ---------- 告警 ---------- */

.warnings {
  margin: 12px 20px 0;
  padding: 8px 12px;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 8px;
  color: #92400e;
  font-size: 13px;
}
.warnings ul { margin: 4px 0 0; padding-left: 18px; }

/* ---------- 卡片 ---------- */

.cards { padding: 16px 20px 80px; }

.card {
  background: #fff;
  border: 1px solid #e3e6ea;
  border-radius: 10px;
  margin-bottom: 14px;
  padding: 12px 16px;
}
.card.current { border-color: #2563eb; box-shadow: 0 0 0 2px rgba(37, 99, 235, .12); }
.card.human { border-left: 4px solid #2563eb; }

.card-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; font-size: 12px; }
.card-id {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-weight: 600; color: #111827;
}
.tag { background: #eef2f7; color: #4b5563; border-radius: 4px; padding: 0 6px; }
.tag.type-term { background: #dbeafe; color: #1e40af; }
.tag.type-name { background: #fce7f3; color: #9d174d; }
.tag.type-concept { background: #dcfce7; color: #166534; }
.src { color: #9ca3af; }
.state { margin-left: auto; color: #9ca3af; }
.state.done { color: #16a34a; }

.front { font-size: 20px; font-weight: 600; margin: 8px 0 2px; word-break: break-word; }
.back { color: #374151; margin-bottom: 8px; word-break: break-word; }

.row { display: flex; gap: 8px; align-items: baseline; margin: 6px 0; }
.label { color: #6b7280; font-size: 12px; flex: 0 0 64px; }
.val { word-break: break-word; }
.evidence {
  background: #f0f9ff;
  border-left: 3px solid #38bdf8;
  padding: 4px 8px;
  border-radius: 0 6px 6px 0;
}

.page-text {
  margin: 8px 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 15px;
  line-height: 2.05;
  padding: 8px 10px;
  background: #fbfcfd;
  border: 1px solid #eef1f4;
  border-radius: 6px;
  max-height: 260px;
  overflow: auto;
}
.page-text .ev { background: #dbeafe; }
.page-text .kw { background: #fde68a; font-weight: 600; }

/* ---------- agent 判定 ---------- */

.agent-box {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 8px 10px;
  margin: 8px 0;
  font-size: 13px;
}
.agent-box .label { flex: 0 0 64px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.chip {
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid #d0d5db;
  background: #fff;
  color: #374151;
}
.chip.kw-accept, .chip.ev-ok { border-color: #16a34a; color: #15803d; background: #f0fdf4; }
.chip.kw-boundary { border-color: #d97706; color: #b45309; background: #fffbeb; }
.chip.kw-reject, .chip.ev-wrong { border-color: #dc2626; color: #b91c1c; background: #fef2f2; }
.chip.ev-missing { border-color: #6b7280; color: #4b5563; background: #f3f4f6; }
.chip.expl-2 { border-color: #16a34a; color: #15803d; background: #f0fdf4; }
.chip.expl-1 { border-color: #d97706; color: #b45309; background: #fffbeb; }
.chip.expl-0 { border-color: #dc2626; color: #b91c1c; background: #fef2f2; }
.chip.fatal-true { border-color: #dc2626; color: #b91c1c; background: #fef2f2; font-weight: 600; }
.chip.fatal-false { border-color: #d1d5db; color: #6b7280; }
.agent-note { color: #4b5563; margin-top: 6px; white-space: pre-wrap; }
.agent-pass { color: #16a34a; font-weight: 600; }
.agent-fail { color: #dc2626; font-weight: 600; }
.agent-none { color: #9ca3af; }

/* ---------- 人工判定 ---------- */

.human-box {
  border: 1px dashed #c7d2fe;
  background: #f5f7ff;
  border-radius: 8px;
  padding: 8px 10px;
}
.human-box .field { display: flex; gap: 8px; align-items: center; margin: 5px 0; flex-wrap: wrap; }
.human-box .label { flex: 0 0 64px; }
.opts { display: flex; flex-wrap: wrap; gap: 4px; }
.opt { padding: 2px 10px; font-size: 12px; border-radius: 999px; }
.opt.on { background: #2563eb; border-color: #2563eb; color: #fff; font-weight: 600; }

textarea {
  font: inherit;
  width: 100%;
  min-height: 46px;
  padding: 5px 8px;
  border: 1px solid #d0d5db;
  border-radius: 6px;
  resize: vertical;
}
input[type="text"], input[type="number"] {
  font: inherit;
  padding: 3px 8px;
  border: 1px solid #d0d5db;
  border-radius: 6px;
}
.missed-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 6px; }
.missed-list { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.missed-item {
  font-size: 12px;
  background: #fef3c7;
  border: 1px dashed #d97706;
  border-radius: 999px;
  padding: 1px 8px;
}
.missed-item .del {
  border: none;
  background: none;
  padding: 0 0 0 4px;
  cursor: pointer;
  color: #b91c1c;
}

.card-foot { margin-top: 8px; font-size: 12px; color: #9ca3af; }

/* ---------- 浮层 ---------- */

.toast {
  position: fixed;
  left: 50%;
  bottom: 24px;
  transform: translateX(-50%);
  z-index: 80;
  background: #111827;
  color: #fff;
  padding: 8px 16px;
  border-radius: 999px;
  font-size: 13px;
}
"""

BODY = """
<header class="top">
  <div class="top-row">
    <div>
      <h1>卡片审核</h1>
      <div class="sub" id="sub">—</div>
    </div>
    <div class="actions">
      <button id="btn-toggle">显示全部</button>
      <button id="btn-export" class="primary">导出 review.jsonl</button>
      <button id="btn-reset">清空进度</button>
    </div>
  </div>
  <div class="progress">
    <div class="prog"><b id="p-total">0</b><span>总卡数</span></div>
    <div class="prog"><b id="p-sampled">0</b><span>抽检张数</span></div>
    <div class="prog pass"><b id="p-agent-pass">—</b><span>agent 合格率</span></div>
    <div class="prog"><b id="p-reviewed">0</b><span>人工已审</span></div>
    <div class="prog agree"><b id="p-agree">—</b><span>人机一致率</span></div>
    <div class="prog fail"><b id="p-missed">0</b><span>漏标</span></div>
  </div>
  <div class="legend">
    <span class="lg"><i class="sw kw"></i>关键词</span>
    <span class="lg"><i class="sw ev"></i>依据句</span>
    <span class="lg hint">合格 = kw accept + evidence ok + expl ≥ 1 + fatal false</span>
  </div>
</header>

<div id="warnings" class="warnings" hidden></div>
<main id="cards" class="cards"></main>

<div id="toast" class="toast" hidden></div>
"""

# 纯逻辑部分：不碰 DOM，可以单独拿到 Node 里做单测。
JS_CORE = """
const BC_KW_VALUES = ["accept", "boundary", "reject"];
const BC_EV_VALUES = ["ok", "missing", "wrong"];
const BC_EXPL_VALUES = [2, 1, 0];
const BC_KW_TEXT = { accept: "接受", boundary: "边界错", reject: "不该标" };
const BC_EV_TEXT = { ok: "依据可用", missing: "缺依据", wrong: "依据不对" };
const BC_EXPL_TEXT = { "2": "2 · 准确", "1": "1 · 勉强", "0": "0 · 错误" };
const BC_FATAL_TEXT = { "true": "true · 严重错误", "false": "false · 无" };

/* 按码点切分：emoji、扩展区汉字在 UTF-16 里是代理对，必须用 Array.from。 */
function bc_cp(text) {
  return Array.from(String(text === null || text === undefined ? "" : text));
}

function bc_slice(text, start, end) {
  return bc_cp(text).slice(start, end).join("");
}

/* §6：合格 = kw accept + evidence ok + expl >= 1 + fatal false。 */
function bc_isPass(verdict) {
  if (!verdict) return false;
  return (
    verdict.kw === "accept" &&
    verdict.evidence === "ok" &&
    Number(verdict.expl) >= 1 &&
    verdict.fatal === false
  );
}

function bc_verdictClass(verdict) {
  if (!verdict) return "none";
  return bc_isPass(verdict) ? "pass" : "fail";
}

/* 四个判定逐项比对。 */
function bc_sameValue(field, a, b) {
  if (field === "expl") return Number(a) === Number(b);
  if (field === "fatal") return (a === true) === (b === true);
  return String(a) === String(b);
}

/* 人机一致率：只算人工动过、且 agent 有判定的卡。 */
function bc_consistency(cards, state) {
  const result = {
    reviewed: 0,
    compared: 0,
    matched: 0,
    exact: 0,
    byField: {
      kw: { compared: 0, matched: 0 },
      evidence: { compared: 0, matched: 0 },
      expl: { compared: 0, matched: 0 },
      fatal: { compared: 0, matched: 0 },
    },
  };
  (cards || []).forEach(function (card) {
    const st = (state || {})[card.card_id];
    if (!st || !st.touched) return;
    result.reviewed += 1;
    const agent = card.agent;
    if (!agent) return;
    let allSame = true;
    ["kw", "evidence", "expl", "fatal"].forEach(function (field) {
      result.compared += 1;
      result.byField[field].compared += 1;
      if (bc_sameValue(field, agent[field], st[field])) {
        result.matched += 1;
        result.byField[field].matched += 1;
      } else {
        allSame = false;
      }
    });
    if (allSame) result.exact += 1;
  });
  return result;
}

/* 只导出人工动过的卡；格式同 §6 审核记录，人工判定覆盖 agent 判定。 */
function bc_buildReviewRows(cards, state, reviewer, at) {
  const rows = [];
  (cards || []).forEach(function (card) {
    const st = (state || {})[card.card_id];
    if (!st || !st.touched) return;
    const row = {
      card_id: card.card_id,
      kw: st.kw || "",
      evidence: st.evidence || "",
      expl: Number(st.expl),
      fatal: st.fatal === true,
      note: st.note || "",
      reviewer: reviewer,
      at: at,
    };
    const missed = (st.missed || []).map(function (item) {
      return { surface: item.surface, page: item.page };
    });
    if (missed.length) row.missed = missed;
    rows.push(row);
  });
  return rows;
}

function bc_reviewJsonl(cards, state, reviewer, at) {
  const rows = bc_buildReviewRows(cards, state, reviewer, at);
  if (!rows.length) return "";
  return rows.map(function (row) { return JSON.stringify(row); }).join("\\n") + "\\n";
}

/* 把一段文字按若干区间切开，给每段打上 class（关键词 / 依据句可以叠加）。 */
function bc_splitByRanges(text, ranges) {
  const cps = bc_cp(text);
  const n = cps.length;
  const clamped = [];
  (ranges || []).forEach(function (range) {
    const start = Math.max(0, Math.min(n, Number(range.start) || 0));
    const end = Math.max(0, Math.min(n, Number(range.end) || 0));
    if (end > start) clamped.push({ start: start, end: end, cls: range.cls });
  });
  const marks = {};
  marks[0] = true;
  marks[n] = true;
  clamped.forEach(function (range) {
    marks[range.start] = true;
    marks[range.end] = true;
  });
  const points = Object.keys(marks)
    .map(Number)
    .sort(function (a, b) { return a - b; });
  const out = [];
  for (let i = 0; i + 1 < points.length; i += 1) {
    const start = points[i];
    const end = points[i + 1];
    const cls = [];
    clamped.forEach(function (range) {
      if (start >= range.start && end <= range.end) cls.push(range.cls);
    });
    out.push({ text: cps.slice(start, end).join(""), cls: cls.join(" ") });
  }
  return out;
}
"""

JS_APP = """
(function () {
  "use strict";

  const DATA = JSON.parse(document.getElementById("bookcard-data").textContent);
  const CARDS = DATA.cards || [];
  const PAGES = DATA.pages || {};
  const SUMMARY = DATA.summary || {};
  const WARNINGS = DATA.warnings || [];
  const LS_KEY = "bookcard-review-" + DATA.data_key;
  const DEFAULT_REVIEWER = DATA.reviewer || "gxiang";

  const state = loadState();
  let showAll = false;
  let currentIdx = 0;
  let toastTimer = null;

  const el = {
    sub: document.getElementById("sub"),
    warnings: document.getElementById("warnings"),
    cards: document.getElementById("cards"),
    toast: document.getElementById("toast"),
    btnToggle: document.getElementById("btn-toggle"),
    btnExport: document.getElementById("btn-export"),
    btnReset: document.getElementById("btn-reset"),
  };

  /* ---------------- 状态 ---------------- */

  function loadState() {
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (!raw) return {};
      const obj = JSON.parse(raw);
      return obj && typeof obj === "object" ? obj : {};
    } catch (err) {
      return {};
    }
  }

  function saveState() {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(state));
    } catch (err) {
      /* 无痕模式等场景存不了，忽略 */
    }
  }

  /* 人工判定的初值取 agent 的判定；人工动过之后 touched 才为 true。 */
  function cardState(card) {
    if (!state[card.card_id]) {
      const agent = card.agent || {};
      state[card.card_id] = {
        kw: agent.kw || "accept",
        evidence: agent.evidence || "ok",
        expl: agent.expl === null || agent.expl === undefined ? 2 : Number(agent.expl),
        fatal: agent.fatal === true,
        note: "",
        missed: [],
        touched: false,
      };
    }
    const st = state[card.card_id];
    if (!st.missed) st.missed = [];
    return st;
  }

  function visibleCards() {
    if (showAll) return CARDS;
    return CARDS.filter(function (card) { return card.sampled; });
  }

  /* ---------------- 渲染 ---------------- */

  function tag(text, cls) {
    const span = document.createElement("span");
    span.className = cls;
    span.textContent = text;
    return span;
  }

  function pageLabel(card) {
    if (card.page === null || card.page === undefined) return "页码未知";
    if (card.printed_page) return "第 " + card.page + " 页（印刷 " + card.printed_page + "）";
    return "第 " + card.page + " 页";
  }

  function agentChips(agent) {
    const wrap = document.createElement("div");
    wrap.className = "chips";
    if (!agent) {
      wrap.appendChild(tag("agent 没有判定", "chip agent-none"));
      return wrap;
    }
    const kw = agent.kw || "—";
    const ev = agent.evidence || "—";
    const expl = agent.expl === null || agent.expl === undefined ? "—" : agent.expl;
    const fatal = agent.fatal === true;
    wrap.appendChild(tag("kw " + kw + " " + (BC_KW_TEXT[kw] || ""), "chip kw-" + kw));
    wrap.appendChild(tag("evidence " + ev + " " + (BC_EV_TEXT[ev] || ""), "chip ev-" + ev));
    wrap.appendChild(
      tag("expl " + expl + " " + (BC_EXPL_TEXT[String(expl)] || ""), "chip expl-" + expl)
    );
    wrap.appendChild(
      tag(
        "fatal " + (fatal ? "true" : "false") + " " + (BC_FATAL_TEXT[String(fatal)] || ""),
        "chip fatal-" + (fatal ? "true" : "false")
      )
    );
    const passed = bc_isPass(agent);
    const verdictCls = passed ? "chip agent-pass" : "chip agent-fail";
    wrap.appendChild(tag(passed ? "合格" : "不合格", verdictCls));
    return wrap;
  }

  function optionGroup(field, values, textMap, current) {
    const opts = document.createElement("div");
    opts.className = "opts";
    values.forEach(function (value) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "opt";
      button.dataset.field = field;
      button.dataset.value = String(value);
      button.textContent = textMap[String(value)] || String(value);
      if (String(current) === String(value)) button.classList.add("on");
      opts.appendChild(button);
    });
    return opts;
  }

  function buildCard(card, index) {
    const st = cardState(card);
    const node = document.createElement("article");
    node.className = "card";
    node.dataset.idx = String(index);
    node.dataset.cardId = card.card_id;

    const head = document.createElement("div");
    head.className = "card-head";
    head.appendChild(tag(card.card_id, "card-id"));
    if (card.sid) {
      const section = card.section_title ? " · " + card.section_title : "";
      head.appendChild(tag(card.sid + section, "tag"));
    }
    if (card.type) head.appendChild(tag(card.type, "tag type-" + card.type));
    head.appendChild(tag(pageLabel(card), "src"));
    const stateTag = tag(st.touched ? "已审" : "未审", "state");
    stateTag.id = "state-" + index;
    head.appendChild(stateTag);
    node.appendChild(head);

    const front = document.createElement("div");
    front.className = "front";
    front.textContent = card.front;
    node.appendChild(front);

    if (card.back) {
      const back = document.createElement("div");
      back.className = "back";
      back.textContent = card.back;
      node.appendChild(back);
    }

    const evRow = document.createElement("div");
    evRow.className = "row";
    evRow.appendChild(tag("依据句", "label"));
    const evVal = document.createElement("div");
    evVal.className = "val evidence";
    evVal.textContent = card.evidence || "（没有依据句）";
    evRow.appendChild(evVal);
    node.appendChild(evRow);

    const pageText = document.createElement("div");
    pageText.className = "page-text";
    const raw = card.page === null || card.page === undefined ? "" : PAGES[String(card.page)];
    if (raw) {
      const ranges = [];
      if (card.kw_start !== null && card.kw_start !== undefined) {
        ranges.push({ start: card.kw_start, end: card.kw_end, cls: "kw" });
      }
      if (card.evidence_start !== null && card.evidence_start !== undefined) {
        ranges.push({ start: card.evidence_start, end: card.evidence_end, cls: "ev" });
      }
      bc_splitByRanges(raw, ranges).forEach(function (piece) {
        if (piece.cls) {
          const span = document.createElement("span");
          span.className = piece.cls;
          span.textContent = piece.text;
          pageText.appendChild(span);
        } else {
          pageText.appendChild(document.createTextNode(piece.text));
        }
      });
    } else {
      pageText.textContent = "（这一页的文字不在 pages.jsonl 里）";
    }
    node.appendChild(pageText);

    const agentBox = document.createElement("div");
    agentBox.className = "agent-box";
    const agentRow = document.createElement("div");
    agentRow.className = "row";
    agentRow.appendChild(tag("agent 判定", "label"));
    agentRow.appendChild(agentChips(card.agent));
    agentBox.appendChild(agentRow);
    const reason = document.createElement("div");
    reason.className = "agent-note";
    const note = card.agent && card.agent.note ? card.agent.note : "";
    const who = card.agent && card.agent.reviewer ? card.agent.reviewer : "无";
    const at = card.agent && card.agent.at ? card.agent.at : "";
    reason.textContent =
      "理由（note）：" + (note || "（没写）") + "　［" + who + (at ? " · " + at : "") + "］";
    agentBox.appendChild(reason);
    if (card.human_prev) {
      const prev = document.createElement("div");
      prev.className = "agent-note";
      const prevExpl =
        card.human_prev.expl === null || card.human_prev.expl === undefined
          ? "—"
          : card.human_prev.expl;
      prev.textContent =
        "上次人工判定：kw " + (card.human_prev.kw || "—") +
        " / evidence " + (card.human_prev.evidence || "—") +
        " / expl " + prevExpl +
        " / fatal " + (card.human_prev.fatal === true ? "true" : "false") +
        (card.human_prev.note ? "　备注：" + card.human_prev.note : "") +
        (card.human_prev.at ? "［" + card.human_prev.at + "］" : "");
      agentBox.appendChild(prev);
    }
    node.appendChild(agentBox);

    const humanBox = document.createElement("div");
    humanBox.className = "human-box";

    const kwField = document.createElement("div");
    kwField.className = "field";
    kwField.appendChild(tag("关键词 kw", "label"));
    kwField.appendChild(optionGroup("kw", BC_KW_VALUES, BC_KW_TEXT, st.kw));
    humanBox.appendChild(kwField);

    const evField = document.createElement("div");
    evField.className = "field";
    evField.appendChild(tag("依据 evidence", "label"));
    evField.appendChild(optionGroup("evidence", BC_EV_VALUES, BC_EV_TEXT, st.evidence));
    humanBox.appendChild(evField);

    const explField = document.createElement("div");
    explField.className = "field";
    explField.appendChild(tag("解释 expl", "label"));
    explField.appendChild(optionGroup("expl", BC_EXPL_VALUES, BC_EXPL_TEXT, st.expl));
    humanBox.appendChild(explField);

    const fatalField = document.createElement("div");
    fatalField.className = "field";
    fatalField.appendChild(tag("严重错误 fatal", "label"));
    fatalField.appendChild(optionGroup("fatal", [true, false], BC_FATAL_TEXT, st.fatal));
    humanBox.appendChild(fatalField);

    const noteArea = document.createElement("textarea");
    noteArea.className = "note";
    noteArea.placeholder = "人工备注（导出时写进 note）";
    noteArea.value = st.note || "";
    noteArea.addEventListener("input", function () {
      const cur = cardState(card);
      cur.note = noteArea.value;
      cur.touched = true;
      saveState();
      updateCardState(card);
      updateSummary();
    });
    humanBox.appendChild(noteArea);

    const missedRow = document.createElement("div");
    missedRow.className = "missed-row";
    const surfaceInput = document.createElement("input");
    surfaceInput.type = "text";
    surfaceInput.className = "missed-surface";
    surfaceInput.placeholder = "漏标的词";
    const pageInput = document.createElement("input");
    pageInput.type = "number";
    pageInput.className = "missed-page";
    pageInput.placeholder = "页码";
    pageInput.value = card.page === null || card.page === undefined ? "" : String(card.page);
    pageInput.style.width = "90px";
    const addButton = document.createElement("button");
    addButton.type = "button";
    addButton.textContent = "记为漏标";
    addButton.addEventListener("click", function () {
      const surface = surfaceInput.value.trim();
      if (!surface) {
        toast("先填要补的词");
        return;
      }
      const cur = cardState(card);
      const page = pageInput.value === "" ? card.page : Number(pageInput.value);
      const missedPage = page === null || page === undefined ? null : page;
      cur.missed.push({ surface: surface, page: missedPage });
      cur.touched = true;
      surfaceInput.value = "";
      saveState();
      renderCard(card, index);
      updateSummary();
      toast("已记漏标：" + surface);
    });
    missedRow.appendChild(surfaceInput);
    missedRow.appendChild(pageInput);
    missedRow.appendChild(addButton);
    humanBox.appendChild(missedRow);

    const missedList = document.createElement("div");
    missedList.className = "missed-list";
    humanBox.appendChild(missedList);
    renderMissed(card, index, st, missedList);

    node.appendChild(humanBox);

    const foot = document.createElement("div");
    foot.className = "card-foot";
    foot.textContent = "j / k 切换卡片 · 改了判定就算「已审」，导出时只导出人工动过的卡";
    node.appendChild(foot);

    node.addEventListener("mousedown", function () { setCurrent(index, false); });
    return node;
  }

  function renderCard(card, index) {
    const node = el.cards.querySelector('[data-card-id="' + cssEscape(card.card_id) + '"]');
    if (!node) return;
    const st = cardState(card);
    const opts = node.querySelectorAll(".opt");
    Array.prototype.forEach.call(opts, function (button) {
      const field = button.dataset.field;
      const value = button.dataset.value;
      let current = st[field];
      if (field === "expl") current = Number(current);
      if (field === "fatal") current = current === true || current === "true";
      if (field === "fatal") {
        button.classList.toggle("on", (current ? "true" : "false") === value);
      } else {
        button.classList.toggle("on", String(current) === value);
      }
    });
    const noteArea = node.querySelector(".note");
    if (noteArea && noteArea.value !== (st.note || "")) noteArea.value = st.note || "";
    const missedList = node.querySelector(".missed-list");
    if (missedList) renderMissed(card, index, st, missedList);
    updateCardState(card);
  }

  /* 漏标清单：刷新后也要显示出来（localStorage 里存着的不能只算数不显示）。 */
  function renderMissed(card, index, st, missedList) {
    missedList.textContent = "";
    (st.missed || []).forEach(function (item, i) {
      const chip = document.createElement("span");
      chip.className = "missed-item";
      const noPage = item.page === null || item.page === undefined;
      const pageLabel = noPage ? "" : "（p" + item.page + "）";
      chip.textContent = item.surface + pageLabel;
      const del = document.createElement("button");
      del.type = "button";
      del.className = "del";
      del.textContent = "×";
      del.title = "删掉这条漏标";
      del.addEventListener("click", function () {
        st.missed.splice(i, 1);
        saveState();
        renderCard(card, index);
        updateSummary();
      });
      chip.appendChild(del);
      missedList.appendChild(chip);
    });
  }

  function cssEscape(value) {
    if (window.CSS && window.CSS.escape) return window.CSS.escape(value);
    return String(value).replace(/["\\\\]/g, "\\\\$&");
  }

  function updateCardState(card) {
    const node = el.cards.querySelector('[data-card-id="' + cssEscape(card.card_id) + '"]');
    if (!node) return;
    const st = cardState(card);
    const stateNode = node.querySelector(".state");
    if (stateNode) {
      stateNode.textContent = st.touched ? "已审" : "未审";
      stateNode.classList.toggle("done", !!st.touched);
    }
    node.classList.toggle("human", !!st.touched);
  }

  function buildCards() {
    el.cards.textContent = "";
    visibleCards().forEach(function (card, index) {
      el.cards.appendChild(buildCard(card, index));
    });
  }

  /* ---------------- 汇总 ---------------- */

  function setText(id, value) {
    const node = document.getElementById(id);
    if (node) node.textContent = String(value);
  }

  function rateText(rate) {
    if (rate === null || rate === undefined) return "—";
    return (rate * 100).toFixed(1) + "%";
  }

  function updateSummary() {
    const cards = CARDS;
    const consistency = bc_consistency(cards, state);
    const reviewed = consistency.reviewed;
    let missed = 0;
    cards.forEach(function (card) {
      const st = state[card.card_id];
      if (st && st.missed) missed += st.missed.length;
    });
    setText("p-total", SUMMARY.cards_total !== undefined ? SUMMARY.cards_total : cards.length);
    const sampled = SUMMARY.cards_sampled !== undefined ? SUMMARY.cards_sampled : cards.length;
    setText("p-sampled", sampled);
    setText("p-agent-pass", rateText(SUMMARY.agent_pass_rate));
    setText("p-reviewed", reviewed);
    setText(
      "p-agree",
      consistency.compared ? rateText(consistency.matched / consistency.compared) : "—"
    );
    setText("p-missed", missed);
  }

  /* ---------------- 导出 ---------------- */

  function nowISO() {
    const d = new Date();
    const pad = function (x) { return String(x).padStart(2, "0"); };
    const off = -d.getTimezoneOffset();
    const sign = off >= 0 ? "+" : "-";
    const abs = Math.abs(off);
    return (
      d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      "T" + pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds()) +
      sign + pad(Math.floor(abs / 60)) + ":" + pad(abs % 60)
    );
  }

  function downloadText(name, text) {
    const blob = new Blob([text], { type: "application/x-ndjson;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
  }

  function exportReview() {
    const reviewer = "human:" + (DATA.reviewer || DEFAULT_REVIEWER);
    const text = bc_reviewJsonl(CARDS, state, reviewer, nowISO());
    if (!text) {
      toast("还没动过任何卡，没有可导出的内容");
      return;
    }
    const base = String(DATA.work_dir || "").split("/").filter(Boolean).pop() || "bookcard";
    downloadText("review-" + base + ".jsonl", text);
    toast("已导出 " + text.trim().split("\\n").length + " 条人工判定");
  }

  function resetAll() {
    Object.keys(state).forEach(function (key) { delete state[key]; });
    saveState();
    buildCards();
    updateSummary();
    toast("进度已清空");
  }

  function toggleScope() {
    showAll = !showAll;
    el.btnToggle.textContent = showAll ? "只看抽检" : "显示全部";
    buildCards();
    updateSummary();
  }

  /* ---------------- 导航 ---------------- */

  function setCurrent(index, scroll) {
    const nodes = el.cards.querySelectorAll(".card");
    if (!nodes.length) return;
    currentIdx = Math.max(0, Math.min(nodes.length - 1, index));
    Array.prototype.forEach.call(nodes, function (node, i) {
      node.classList.toggle("current", i === currentIdx);
    });
    if (scroll) {
      const node = nodes[currentIdx];
      if (node) node.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  function move(delta) {
    setCurrent(currentIdx + delta, true);
  }

  function onKeyDown(ev) {
    const target = ev.target;
    if (
      target &&
      (target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.isContentEditable)
    ) {
      return;
    }
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    const key = (ev.key || "").toLowerCase();
    if (key === "j") {
      move(1);
      ev.preventDefault();
    } else if (key === "k") {
      move(-1);
      ev.preventDefault();
    }
  }

  /* ---------------- 提示 ---------------- */

  function toast(text) {
    el.toast.textContent = text;
    el.toast.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.toast.hidden = true; }, 2200);
  }

  /* ---------------- 初始化 ---------------- */

  function init() {
    const parts = [];
    if (DATA.work_dir) parts.push("目录 " + DATA.work_dir);
    if (DATA.cards_file) parts.push("卡文件 " + DATA.cards_file);
    if (DATA.summary && DATA.summary.cards_total !== undefined) {
      parts.push("共 " + DATA.summary.cards_total + " 张卡");
    }
    if (DATA.sample !== null && DATA.sample !== undefined) {
      const sampledCount = DATA.summary ? DATA.summary.cards_sampled : 0;
      parts.push("抽检 " + sampledCount + " 张（seed " + DATA.seed + "）");
    }
    parts.push("生成于 " + DATA.generated_at);
    el.sub.textContent = parts.join(" ｜ ");

    if (WARNINGS.length) {
      el.warnings.hidden = false;
      const title = document.createElement("div");
      title.textContent = "数据告警（" + WARNINGS.length + " 条）";
      el.warnings.appendChild(title);
      const ul = document.createElement("ul");
      WARNINGS.forEach(function (warning) {
        const li = document.createElement("li");
        li.textContent = warning;
        ul.appendChild(li);
      });
      el.warnings.appendChild(ul);
    }

    buildCards();
    updateSummary();
    setCurrent(0, false);

    document.addEventListener("keydown", onKeyDown);
    el.btnExport.addEventListener("click", exportReview);
    el.btnReset.addEventListener("click", resetAll);
    el.btnToggle.addEventListener("click", toggleScope);
    el.cards.addEventListener("click", function (ev) {
      const opt = ev.target.closest(".opt");
      if (opt) {
        const node = ev.target.closest(".card");
        const card = cardById(node.dataset.cardId);
        if (!card) return;
        const st = cardState(card);
        const field = opt.dataset.field;
        if (field === "expl") {
          st.expl = Number(opt.dataset.value);
        } else if (field === "fatal") {
          st.fatal = opt.dataset.value === "true";
        } else {
          st[field] = opt.dataset.value;
        }
        st.touched = true;
        saveState();
        renderCard(card, Number(node.dataset.idx));
        updateSummary();
        return;
      }
      const node = ev.target.closest(".card");
      if (node) setCurrent(Number(node.dataset.idx), false);
    });
  }

  function cardById(cardId) {
    for (let i = 0; i < CARDS.length; i += 1) {
      if (CARDS[i].card_id === cardId) return CARDS[i];
    }
    return null;
  }

  init();
})();
"""
