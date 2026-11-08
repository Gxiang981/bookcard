# BookCard 契约 v1：抽取输出 · 私有卡包 · 审核标准 · benchmark

维护者定，2026-11-06。依据 README §7 第 11–15 条。本文件是执行单的唯一口径；改字段先改这里。

## 0. 流水线与目录

一本书一个工作目录（数据不入库）。工作目录根是 `$BOOKCARD_HOME`（没设就是 `~/.bookcard`），
书落在根下的 `books/` 里：

```
<home>/books/<fp12>/                # <home> = $BOOKCARD_HOME；fp12 = 书指纹前 12 位（PDF 全字节 SHA-1，同 train/SCHEMA.md）
  book.json          §1 书的身份
  pages.jsonl        §2 逐页文字（取字结果）
  toc.json           §3 目录 + 闸门 1
  sections.json      §3 切章节（= App 里的词库）
  candidates.jsonl   §4 逐页全量候选（抽词 + 解释）——同时是训练数据
  cards.jsonl        §5 挑选去重后的卡
  review/            §6 审核记录（agent 审 + 人工抽检导出）
  quality_report.json §6
  pack/<fp12>.ttfc.zip §7 私有包
```

- 本仓库内部跑用 `BOOKCARD_HOME=/train/data/bookcard`，
  书就落在 `train/data/bookcard/books/<fp12>/`。
- **旧位置兼容**：早期版本把书直接放在 `<home>/<fp12>/`（没有 `books/` 这一层）。
  **不搬数据**，但引擎照常认：
  - `book_list` 两处都扫（只认 fp12 命名、目录里有 `book.json` 的），每本带
    `location`（`books` = 新位置，`legacy` = 旧位置）；同一个 fp12 两处都有时只列新位置那个；
  - `book_status` 等按 fp12 找书的工具，旧位置的书照样找得到；
  - `book_add` **只往新位置写**。
- 训练侧的 bench（`src/kwtrain/`）的 `--data-root` 指的是「装着书目录的那一层」，
  新布局下就是 `$BOOKCARD_HOME/books`；它和 `BOOKCARD_HOME` 不是同一个东西。

流程：`probe → text → toc（闸门 1）→ sections → extract 全量 → select → locate → audit → pack`。
**闸门只有一道**：没有 `toc.json.gate.status == "approved"` 不许切章节、不许打包（`approved_by` 也得有人认领，没人确认过的不算）。确认人可以是 agent（`approved_by: "agent:<名字>"`），但必须按 §3 判据逐条写理由。

**抽词没有第二道闸门**：引擎唯一的把关是「词必须真的出现在这一页原文里」（§4）。

## 1. book.json

```json
{"fp": "<40 位 sha1>", "fp12": "…", "file_name": "…", "nas_path": "…", "page_count": 206,
 "title": "中外历史纲要（上）", "subject": "历史", "stage": "senior", "grade": "", "volume": "上",
 "publisher": "人民教育出版社", "edition": "2019", "language": "zh",
 "fingerprint": {"pages": 206, "page_text_sha1": ["<每页文字 sha1>", "…"]}}
```

`title/subject/stage/…` 从 `train/data/book/books.jsonl` 和 NAS 路径带过来，缺的留空字符串，不编。

## 2. pages.jsonl（一页一行）

```json
{"page": 12, "printed_page": "8", "text": "…", "text_sha1": "…", "text_origin": "pdf_text",
 "width": 595.0, "height": 842.0, "kind": "body"}
```

- `page` 1 起物理页号；`printed_page` 版心印的页码，认不出为 `null`。
- `text` 用 PyMuPDF 取字，去掉页眉页脚、水印（复用 `bookcard.probe` 的规则），保留换行。
- `kind`：`cover` | `front`（版权页、前言）| `toc` | `body` | `exercise` | `appendix` | `blank`。只有 `body`、`exercise`、`appendix` 进抽取。

### 2.1 段落界 `breaks`（2026-11-06，the maintainer：PDF 的强行折行要按正常行文合并）

`text` 里的 `\n` 绝大多数是排版折行，不是段落结束。`text` **本身不改**（`text_sha1`、候选和金标准的偏移都基于它，benchmark 已冻结），另加一个字段：

```json
{"page": 12, "text": "…", "breaks": [37, 120, 311], …}
```

- `breaks` = `text` 里**真正的段落结束**那些 `\n` 的偏移（升序）。其余 `\n` 都是软折行。
- 判定用版面几何，在取字时算（PyMuPDF 的 line bbox；OCR 页用 OCR 行框），一行满足任一条，它后面的 `\n` 就是段落界：
  1. 这一行右端离版心右边界超过 1.5 个字宽（行没写满）；
  2. 下一行首缩进超过 1.5 个字宽；
  3. 下一行和这一行的行距超过本页中位行距的 1.6 倍，或字号相差超过 15%（标题、图注换块）；
  4. 下一行属于另一个 PyMuPDF text block，且两块左边界相差超过 1.5 个字宽（分栏、旁栏）。
  - "字宽"取这一行的字号；"版心右边界"取本页正文行右端的 90 分位。
- **几何判定是一个纯函数**：`bookcard.engine.breaks.compute_breaks(lines) -> list[int]`，入参是"一页的行列表"（每行 `text` / `bbox` / `size` / `block`），出参是段落界偏移。**不依赖 PyMuPDF 对象** —— 下一张 OCR 单把 OCR 行框喂同一个函数。
  - 三处口径的补充（2026-11-06，实测三本真书后定的）：
    - **版心按列算**：一页里常有一栏正文 + 一栏旁栏（物理书的竖排旁注、历史书的史料栏），两栏右端差几十个点。按整页算 90 分位，旁栏会把正文的右边界拉偏 ⇒ 整页正文每一行都被判成"没写满"（每个换行都成段落界，合并就白做了）。所以按**列**分别算版心右边界（左边界 = 该列各行 x0 的最小值）。
    - **列的切分**：相邻两行算同一列，当且仅当下一行在上一行**下方**、且行距不超过本页中位行距的 3 倍（图注、表格、旁栏之间有明显空隙，那些要另起一列）。
    - **判据 1 还要"比邻近行也短"**：光看"离版心右边界多远"会把**窄栏**（教材里常见：一段文字排得比别处窄，英语故事的阅读材料就是）每一行都判成"没写满"⇒ 整段被切成一堆单行。所以判据 1 = 离版心右边界超过 1.5 个字宽 **且** 比同一列的邻近行（上一行；没有就看下一行）短超过 1.5 个字宽。
- 没有 `breaks` 字段的老数据：所有 `\n` 都当段落界（即不合并），不报错。
- 重跑 `bookcard text` 只会加 `breaks`，`text` 和 `text_sha1` 必须逐字不变；不一致即失败。

**合并口径**（`bookcard.engine.reflow.reflow(text, breaks) -> (out, offset_map)`，全仓只有这一份实现）：
- 段落界 `\n` 保留成 `\n`；软折行 `\n` 两侧都是 CJK 字符或 CJK 标点 ⇒ 删掉；两侧有一侧是拉丁字母或数字 ⇒ 换成一个空格；行尾是 `-` 且两侧都是小写拉丁字母 ⇒ 删掉 `\n`，保留 `-`（不猜断词）。
- 行首行尾的空白一并压掉；连续空格压成一个。
- `offset_map[i]` = `out[i]` 在原 `text` 里的偏移，高亮、依据句等原文偏移都靠它换算。
- 用在给人看的文字上（§7.1 文本场景的 `context`）；抽取输入和候选偏移仍用原 `text`。

### 2.2 扫描件：OCR 取字（2026-11-06，the maintainer：扫描件现在就做）

- 探针 verdict 为 `scanned` ⇒ 整本书走本地 OCR，每页 `text_origin: "ocr"`；数字版书一页都不 OCR（不然重跑 `bookcard text` 会改动现有三本书的 `text`）。`bookcard text --ocr auto|always|never` 可覆盖，默认 `auto`。OCR 引擎用 `rapidocr`（ONNX，本地跑，不联网），作为可选依赖 `bookcard[ocr]`；没装时遇到扫描页直接报错，提示安装命令，不静默跳过。
- 渲染 200 dpi 灰度图喂 OCR；行按阅读顺序排（先按栏、再从上到下），置信度 < 0.5 的行丢掉，水印规则照常套用。
- OCR 行框另存 `ocr/p<4 位页号>.json`：`[{"text": "…", "bbox": [x0, y0, x1, y1], "conf": 0.97, "start": 120}]`，坐标是 PDF 点（72 dpi），`start` 是这一行在 `text` 里的偏移。§5 定位（bbox）和 §2.1 段落界都读它。
- 一行内某个词的 bbox：按字符数在行框内等比切（中文等宽足够准；英文按字符数近似）。

## 3. toc.json 与 sections.json

```json
{"source": "bookmarks|printed|layout", "entries": [{"level": 1, "title": "第一单元 从中华文明起源到秦汉大一统封建国家的建立与巩固", "page": 7}],
 "checks": {"pages_monotonic": true, "first_body_page": 7, "covered_pages": [7, 206], "gaps": []},
 "gate": {"status": "pending|approved|rejected", "approved_by": "agent:agent", "at": "…", "reasons": ["…"], "checks_passed": true}}
```

`build_toc` **只出 `pending`**（`approved_by` 留空）：判据全过也只是「目录本身没问题，就等人拍板」，
**`approved` 只能由 `toc_gate(approve=true)` 写**；判据不过一律 `rejected`。
`gate.checks_passed` 记四条判据是否全过（只加不改的字段）。
`status == "approved"` 但 `approved_by` 为空（或还留着老版本的 `agent:unreviewed`）——
没人确认过，不许切章节。

`sections.json`：`[{"sid": "s01", "title": "第1课 中华文明的起源与早期国家", "level": 2, "page_start": 7, "page_end": 12, "sort_order": 1}]`。
粒度：取能让每个 section 落在 **3–25 页**的那一级（一般是"课/节/Unit"）；section 之间页码连续、无空洞、无重叠。section = App 词库。

**论文源**（`book.type == "paper"`）没有目录页，目录和章节由 `bookcard paper sections <dir>` 从正文标题认出来
（`src/bookcard/pipeline/paper_sections.py`）：`toc.json` 的 `source` 记 `layout`，**闸门口径不变**
（只出 `pending` / `rejected`，`approved` 只能由 `toc_gate(approve=true)` 写）。
`sections.json` 的条目多一个 **`subheadings`** 字段（论文专用）：同一页上被跳过的子标题记在这一节里
（一页只起一节，取文档顺序靠前的那个；见 `docs/license-v1.md` §4）。书源的 `sections.json` 没有这个字段。

## 4. candidates.jsonl（逐页全量候选，抽词 + 解释同一步）

一页一行（页超过 1500 字按段落切块，`chunk` 从 1 起，规则同 SCHEMA.md）：

```json
{"seg_id": "book:<fp12>:p0012", "page": 12, "chunk": 0, "sid": "s01", "text_sha1": "…",
 "extractor": "llm:minimax:MiniMax-M3.1-Flash:ex-v2", "scope": "full",
 "items": [
   {"front": "良渚文化", "type": "name", "start": 41, "end": 45,
    "explanation": "长江下游的新石器时代晚期文化，以大型城址、水利系统和玉器著称，被视为早期国家形态的代表。",
    "back": "解释……", "learn_value": 3}
 ],
 "dropped": {"not_in_text": 0},
 "created_at": "…"}
```

- LLM 只给 `front / type / back / learn_value`；**偏移由程序落**：`front` 在 `text` 里的每一处都落一个 item 的 `start/end`（同 SCHEMA.md 的落位规则，重叠保留最长），解释只挂在第一处，其余出现只有 `start/end`（`"back": null`）。**找不到原文的 front 丢弃计数** —— 这是唯一的把关。
- **依据句不再由 LLM 给**：引擎自己取页面原文里包住该词的那一句（见下面的「依据句校正」）。
- `explanation`：中文，**15–60 字**（英文书同样用中文解释；英文词条加词性和中文释义，如 `n. 发明；创造`），必须能从依据句或常识得出，不得照抄依据句整句。
- `learn_value`：1 = 可有可无，2 = 值得学，3 = 核心概念。挑卡用，不进训练。
- `type` 同 GUIDE.md 三类；关键词口径同 `train/GUIDE.md`。
- **反推训练标注**：每行直接映射成 `labels.jsonl` 一行：`labeler = extractor`，`spans = items` 的 `start/end/front/type`，`scope = "full"`。由 `kwtrain` 提供转换函数，不另跑标注。

提示词：`src/bookcard/prompts/extract_v1.md`（维护者写，改一个字就升版本；提示词跟着包走，契约 §1）。

## 5. cards.jsonl（挑选去重）

```json
{"card_id": "<fp12>:<sid>:<sha1(front)[:8]>", "sid": "s01", "front": "良渚文化", "back": "…解释…", "type": "name",
 "tags": ["历史", "第1课"], "sort_order": 3,
 "source": {"page": 12, "printed_page": "8", "seg_id": "book:…:p0012", "start": 41, "end": 45,
            "evidence": "……原文依据句……", "verbatim": true, "method": "llm",
            "bbox": {"x": 120, "y": 300, "w": 64, "h": 18}, "image": "images/p0012.jpg",
            "evidence_origin": "sentence"},
 "content_fp": "<sha256(norm(front)+'\n'+norm(back))>"}
```

挑选规则（确定性，程序做）：
1. 一本书内按 `norm(front)`（NFKC、去首尾空白、英文小写）去重，**只留第一次出现**（最早页）那张；`learn_value` 取最大值。
2. 丢 `learn_value == 1`。
3. 卡归属第一次出现的 section；section 内按页码、再按 `start` 排 `sort_order`。
4. `back` = `explanation`；没解释的词不做卡（但仍在 candidates 里进训练）。

依据句：`source.evidence` = **页面原文里包住该词的那一句**（必须是原文逐字的子串；句子边界只认 `。！？；.!?;` 和空行，单个换行不算边界），`source.evidence_origin = "sentence"`。依据句由引擎取，不由 LLM 给。

`evidence_origin` 是**只加不改**的新字段：旧版 App 忽略它也能照常导入。

定位：在该页 PDF 上 `page.search_for(front)` 取第一处框（多处时取与 `start` 序号对应那处），换算到渲染图像素坐标（150 dpi）；找不到 `bbox: null`，计入 `locate_rate`。

## 6. 审核标准（写进 skill，agent 审和 benchmark 共用）

### 闸门 1：目录（toc.json）—— 全部满足才 approved

1. 条目页码单调不减，最后一条 ≤ 页数；
2. 正文首页之后每一页都落在某个 section 里，无空洞、无重叠；
3. 抽查 3 个条目：该页文字里确实出现条目标题（允许空白差异）；
4. 每个 section 3–25 页，超出要写理由。
理由逐条写进 `gate.reasons`；有一条不满足就 `rejected` 并说明怎么修。
判据全过也只是 `pending`——`approved` 只能由 `toc_gate` 的确认动作写入（`build_toc` 不自动批）。

### quality_report.json

`{"cards", "locate_rate", "verbatim_rate", "by_section": {…}, "gates": {"toc": "approved"}}`

`verbatim_rate` = 卡正面确实是原文逐字的比例；`locate_rate` = 定位到书页 bbox 的比例。

## 7. 私有卡包 `.ttfc.zip`

zip 内：`manifest.json` + `images/p<4 位页号>.jpg`（150 dpi，JPEG 质量 85）。manifest = App `BulkImportBody` 形状（`00_flashcard/server-python/app/routes/imports.py`），图片用 `file` 指 zip 内路径；v2 字段 App 会忽略。

```jsonc
{
  "format": "ttfc/2", "visibility": "private",
  "book": { /* book.json 全部字段 */ },
  "provenance": {"tool": "bookcard <版本>", "method": "llm", "extractor": "llm:minimax:…:ex-v2", "created_at": 1793600000000},
  "quality": { /* quality_report.json */ },
  "license": {"cards": "private", "page_images": "private"},
  "category": {"name": "<book.title>", "description": "<出版社 版次>", "sort_order": 0,
    "libraries": [{"name": "<section.title>", "slug": "<fp12>-<sid>", "sort_order": 1,
      "cards": [{"front": "…", "back": "…", "tags": ["…"], "difficulty": 1, "sort_order": 3,
        "images": [{"file": "images/p0012.jpg", "width": 1240, "height": 1754,
                    "occurrences": [{"x": 120, "y": 300, "w": 64, "h": 18, "page": 12}]}],
        "source": { /* cards.jsonl 的 source */ }, "content_fp": "…"}]}]}
}
```

- `front` ≤ 500 字符（App 限制）；`bbox` 为 null 时 `occurrences: []`，但仍带页面图。
- 公开包的指纹对齐、社区发布仍属 P3；包本身的两档形状见 §7.1。
- 验收：用 App 的 `POST /api/v2/imports/bulk-zip` 导入成功，App 里能看到词库、卡、页面图和高亮框。

### 7.1 两档：公开版 / 内部分享版（the maintainer 2026-11-06 拍板）

`bookcard pack --edition public|private`（默认 `private`），产物：内部分享版沿用 `pack/<fp12>.ttfc.zip`，公开版 `pack/<fp12>.public.ttfc.zip`。

| | 公开版 `visibility: "public"` | 内部分享版 `visibility: "private"` |
|---|---|---|
| 背面 `back` | **只有一句解释**：`explanation` 取到第一个句末标点（`。！？.!?`）为止 | `explanation` 全文 |
| 场景 | 无 | 按书的 `scene_mode` 二选一（下表） |
| 书页图 `images/` | **禁止**（打包时强校验，zip 里出现任何图片即失败） | 只有 `scene_mode == "image"` 时带 |
| `source` | 只留 `page / printed_page / bbox / verbatim / method`（`evidence`、`context` 去掉） | 全部 |
| `book.fingerprint` | **必填**（缺失即失败）；`nas_path` / `file_name` 去掉（本机路径不外发） | 全部 |
| `license` | `{"cards": "CC-BY-SA-4.0", "page_images": "none"}` | `{"cards": "private", "page_images": "private"}` |

英文点号 `.` 不一定是句末：点号后面（跳过空白）是中文 ⇒ 是词性标签（`n. 发明`），不算；点号前面是单个拉丁字母（`e.g.`）⇒ 缩写，不算（实测：照字面切，英语教材 87% 的卡背面会变成 `n.`）。

**`scene_mode`（整本书一个值，写进 manifest 顶层和 `book.json`）**：

**默认走图片场景**（the maintainer 2026-11-06 拍板：处理书一律默认用 `image`，不用问用户）。下面的自动判定（三条判据）**照算照存**，但只作**参考**：三条全中时 `text_eligible` 为 `true`（用户主动要文本块时可以选 `text`），其余为 `false`。`book.json` 和 manifest 里的 `scene_suggested` 恒为 `"image"`；用户/agent 选的值记进 `book.json.scene_mode` 和 `provenance.scene_chosen_by`（取 `"user"` = 当场问过用户 / `"default"` = 用的默认场景 / `"cli"` = 命令行显式传了 `--scene`）。CLI 不传 `--scene` 时用默认场景，MCP 的 `pack` 工具 `scene` 可选（默认 `image`）。扫描件选 `text` 时，文本块用 OCR 取到的字。

- **参考判据**（三条全中 ⇒ `text_eligible: true`，否则 `false`）：书能直接取字 —— 探针 verdict 不是 `scanned/garbled/broken`，**且** body 页里 `text_origin == "pdf_text"` 的占比 ≥ 0.9，**且** body 页图片覆盖率（PyMuPDF `page.get_image_info()` 的图片框面积 ∪ / 页面积）的中位数 < 0.35。
- 可用 `--scene text|image` 覆盖默认场景（此时记 `scene_chosen_by = "cli"`），覆盖时在 `provenance.scene_override` 记一笔。

**文本场景**（`scene_mode == "text"`）：每张卡加 `source.context = {"text": "…", "highlights": [{"start": 3, "end": 7}]}`，参照 agent 聊天模式的文字块：
- `text` = 页面原文里包含依据句（`source.evidence`）的那个**段落**（以 §2.1 的 `breaks` 为界；没有 `breaks` 的老数据以空行为界，再没有就用依据句前后各一句），**按 §2.1 合并口径去掉软折行**；超过 400 字时以依据句为中心截取到 400 字以内，在句界处截，截掉的一侧加 `…`。必须等于原文对应区间经 `reflow` 后的结果（`…` 除外），即"合并后逐字"。
- `highlights` = 卡 `front` 在合并后的 `text` 里**每一处**出现的偏移（归一化规则同 §5 依据句校正）；至少一处，否则改用依据句本身作 `text`。
- 不带 `images/`。

**图片场景**（`scene_mode == "image"`）：同 §7 现状 —— `images/p<页号>.jpg` + `occurrences` bbox，不加 `context`。

## 8. benchmark（抽关键词 + 解释）

细则（选页、金标准做法、评分口径、评审模型）见 `docs/bench-v1.md`，以那里为准。

- **题目**：3 本书各固定抽 30 页（分层抽样：正文 ≥ 20、练习 ≥ 4、注释多的页 ≥ 3、目录 1 页作为"应基本为空"的对照），共 90 页；页号清单存 `bench/v1/pages.json` 并入库，之后不改，改了就升版本。
- **金标准**：每页由 agent 按 §6 审定候选，人工抽检覆盖；金标准 = `kw == accept` 的词 + `missed` 补标，每个词带一条被判 `expl == 2` 的参考解释。存 `bench/v1/gold.jsonl`（不含书页全文，只存 `seg_id`、`text_sha1` 和 span，书页文字从书重新取）。
- **被测**：任意模型按 `src/bookcard/prompts/extract_v1.md` 跑同样 90 页，输出 candidates 格式。
- **评分**（脚本 `kwtrain bench`）：
  1. 抽词：精确匹配与重叠匹配的 P / R / F1（同 `span_train.span_prf / overlap_prf`，按 surface 首次出现位置算）；
  2. 依据：`evidence` 落在原文且与词重叠或同句的比例；
  3. 解释：对命中的词，用**固定评审模型**按 §6 的 `expl` 0/1/2 打分，报平均分和 `0` 分占比；评审模型和评审提示词入库、固定版本，人工抽 10% 复核评审一致率；
  4. 成本：每页输入/输出 token、耗时、失败率。
- 报告一张表：模型 × {抽词 F1、依据率、解释均分、0 分率、每页 token、每页秒数}。
