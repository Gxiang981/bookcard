# 目录看图 v1（`bookcard tocv`）

契约 v1（）。方向是 用户拍板的：**目录由模型看图产出，规则只做
检查和对照，不再由规则产出目录**。

引擎只做确定性的事：渲染图、换算页码、跑判据、落盘。**看图的活在调用方的 agent
或模型那边做**——引擎不内置任何模型 key，也不调模型（和 `bookcard flow` 同一口径，
见 `docs/mcp-v1.md` §2）。

工作目录是轻量的：**只要一本 PDF 和一个输出目录**，不依赖 `book_add`，不做整本 OCR
（扫描件只对**用到的页**现做 OCR，结果缓存到 `<dir>/ocr/`）。

## 0. 边界

- 这一层**不接 MCP 工具**，也不改 skill 文案（实测通过后另开单）。
- 不改现有的 `build_toc` / 规则解析器——它们只作对照组被调用（C6）。
- 不跑批量。gold 由维护者出，引擎只读。
- 不新增运行时依赖：拼图、印字都用 `pymupdf`。
- **闸门 1 照旧由人或 `toc_gate` 拍板**：这里写出的 `gate.status` **永远不是
  `approved`**，最多到 `pending`（判据不过写 `rejected`）。

## 1. 命令

```
bookcard tocv init    <pdf> --out <dir>
bookcard tocv sheet   <dir> [--first 1] [--last 24] [--cols 4] [--rows 3] [--json]
bookcard tocv locate  <dir> --pages 4,5,6 --by <agent-id>
bookcard tocv pages   <dir> --pages 4,5,6 [--dpi 150] [--json]
bookcard tocv submit  <dir> --draft <file> --by <agent-id>
bookcard tocv score   <dir> --gold <gold.json>
```

`--by` 的格式是 `agent:<harness>:<模型名>`，记进 `tocv.json` / `tocv_report.json`，
用来记「这是谁看出来的」。

### 1.1 `init <pdf> --out <dir>`

建 `<dir>/tocv.json`：

| 字段 | 含义 |
|---|---|
| `pdf_path` | PDF 的绝对路径 |
| `sha256` | 文件指纹 |
| `page_count` | 全书页数 |
| `kind` | `pdf_text`（数字版）/ `scanned`（扫描件） |
| `text_layer_pages` | 每页有没有文本层（页号列表） |
| `page_classes` | 探针的页类型判定 |
| `toc_pages` | 模型认定的目录页（`locate` 写） |

**幂等**：状态文件已经在就不重建（跑第二次返回 `created: false`）。

### 1.2 `sheet <dir>`

把 `[first, last]` 页渲染成缩略图，按网格拼成拼接图
`<dir>/sheets/sheet-<first>-<last>.png`。

- 每张拼接图 12 页（默认 4 列 × 3 行）。
- **每个格子的角上印物理页号**（红底白字的大号字）——模型要靠它报页码。
- 拼接图长边不超过 2000px，格子之间留细分隔线。
- 区间超出全书页数就截断（`truncated: true`）。
- 默认只看前 24 页；目录可能在书后，所以允许指定任意区间。
- 打印输出的文件路径列表；`--json` 出机读格式。

### 1.3 `locate <dir> --pages 4,5,6 --by <agent-id>`

记下模型判断的目录页（**可以不连续**），写进 `tocv.json` 的 `toc_pages`。

### 1.4 `pages <dir> --pages 4,5,6 [--dpi 150]`

把指定页渲染成大图 `<dir>/pages/p<4 位页号>.png`（长边不超过 2000px）。

数字版同时写 `<dir>/pages/p<4 位页号>.txt`（文本层，**只当参考，条目以图为准**）。

**目录页和用来定偏移的正文页都用这条命令看。**

### 1.5 `submit <dir> --draft <file> --by <agent-id>`

见 §3、§4。退出码：`ok` 为 0，其它为 1。

### 1.6 `score <dir> --gold <gold.json>`

见 §5。输出写到 `<dir>/score.json`，并打印一行摘要。

## 2. 草稿格式（`toc_draft.json`）

**这是契约**：模型写这个文件，引擎读它、换算页码、跑判据。

```json
{
  "toc_pages": [4, 5, 6],
  "entries": [
    {"level": 1, "title": "第一章 机械运动", "printed_page": 1},
    {"level": 2, "title": "第1节 长度和时间的测量", "printed_page": 2},
    {"level": 1, "title": "附录", "printed_page": 150, "kind": "appendix"}
  ],
  "anchors": [
    {"printed_page": 1, "physical_page": 9},
    {"printed_page": 120, "physical_page": 128}
  ]
}
```

| 字段 | 含义 |
|---|---|
| `entries[].level` | 层级，1 是最大的一层 |
| `entries[].title` | 标题，照目录页上印的抄 |
| `entries[].printed_page` | 目录上印的页码，整数；**目录上没印页码填 `null`** |
| `entries[].kind` | 可选：`body`（默认）/ `front`（前言、致同学等）/ `appendix`（附录、后记、索引） |
| `anchors` | 至少两条，见下 |
| `toc_pages` | 目录页（和 `locate` 记的一致；草稿里写了就以草稿为准） |

### 2.1 anchors（偏移锚点）

- **至少两条**：第一条一级条目和最后一条一级条目。
- 必须是模型**看过正文页图片**确认的（不能从目录页推）。
- `printed_page` 是那一章目录上印的页码，`physical_page` 是它在书里的**物理页号**
  （PDF 第几页）。

**页码换算**：按 `printed_page` 从小到大分段，每个 anchor 的偏移
（`physical_page − printed_page`）**从它自己的 `printed_page` 开始生效**——
这样能应对中间插了彩页的书。第一个 anchor 之前用第一个 anchor 的偏移。

```
anchor: (1 → 9)  (10 → 14)  (12 → 18)
printed 1  → 物理 9      偏移 +8
printed 10 → 物理 14     偏移 +4
printed 12 → 物理 18     偏移 +6
```

## 3. 提交（`submit`）

换算出物理页 → 跑检查 → 写两个文件：

- `<dir>/toc.json`：**和现有 `toc.json` 同一格式**，`source` 写 `"vision"`，
  `entries` 带 `level` / `title` / `page`，另有 `checks` 和 `gate`。
  `gate.status` **永远只到 `pending`**（判据不过写 `rejected`），不许写 `approved`。
- `<dir>/tocv_report.json`：每项检查的结果、`failed_entries`、`round`、`status`。

### 3.1 检查项

全部是确定性的。失败的条目**逐条列出来**（序号、标题、哪项没过、原因）。

| 编号 | 名称 | 判据 |
|---|---|---|
| **C1** | 页码 | 物理页单调不减，落在 `[1, page_count]` 内；`printed_page: null` 的条目不参加。anchors 至少两条，且都指在书里。 |
| **C2** | 覆盖与粒度 | 复用 `engine/sections.choose_granularity` 和 `engine/gate.evaluate` 的判据（覆盖无空洞、每节 3 到 25 页）。 |
| **C3** | 标题在正文页上 | 每条 body 条目的标题，都要在它的物理页（允许 ±1 页）上找到。数字版用文本层；扫描件**只对用到的页**调 `engine/ocr.ocr_page` 现做 OCR，结果缓存到 `<dir>/ocr/`。 |
| **C4** | 标题在目录页上 | 每条标题都要出现在目录页文字里（数字版用文本层，扫描件 OCR 目录页）——用来抓模型编出来的条目。 |
| **C5** | 层级 | 第一条 body 条目的层级是 1；层级每次最多往下跳一级；同一层级的「第X章 / 第X节 / Unit N / 第X单元」编号要连续，缺号和重号都列出来。 |
| **C6** | 对照 | **只出报告，不判失败**。见 §3.2。 |

**C3 / C4 的匹配口径**（两条一样）：

- 匹配前统一去掉空白和标点；
- 判据是**去掉章节号后的标题主体被页面文字包含**，或者**编辑距离相似度不低于 0.8**
  （阈值是模块常量 `tocvision.TITLE_MATCH_THRESHOLD`）。

### 3.2 C6 对照（只出报告）

和两样东西比一比，列出**缺的、多的、页码不一致的**：

1. **规则解析结果**：`analysis.analyze` / `analyze_ocr_workdir` 加 `toc._pick_source`。
   扫描件没有现成 OCR 产物就跳过。
2. **书签**：书签条数接近页数（每页一个自动书签）的**当作无效书签，不比**；
   只比人工书签。

### 3.3 状态机

`tocv_report.json` 的 `status`：

| 值 | 含义 |
|---|---|
| `ok` | C1 到 C5 全过 |
| `retry` | 有失败，但 `round < 3`——**允许模型看图重做两轮** |
| `needs_human` | 第 3 次提交还有失败，要人拍板 |

`round` 是第几次提交（每次 `submit` 加一）。

命令退出码：`ok` 为 0，其它为 1。

## 4. gold 格式

和草稿基本相同，但**每条用 `physical_page`，不用 `printed_page`**（gold 直接给物理页，
不用引擎换算）：

```json
{
  "pool_index": 102,
  "source_pdf": "102.pdf",
  "page_count": 82,
  "toc_pages": [5],
  "offsets": [{"from_printed": 1, "offset": 5}],
  "entries": [
    {"level": 1, "title": "Unit 1 Hello!", "printed_page": 2, "physical_page": 7},
    {"level": 1, "title": "Appendix 1: Words in each unit",
     "printed_page": 70, "physical_page": 75, "kind": "appendix"}
  ]
}
```

- `offsets` 是维护者记的偏移分段（给人看的），引擎的 `score` **不用它**——
  gold 已经给了 `physical_page`。
- **`physical_page` 是 `null` 的条目只比对标题和层级**，不参加页码一致率
  （那一列本来就没印页码）。

## 5. 打分（`score`）

给小测量准确率用。**按顺序对齐条目**（标题规整后做编辑距离匹配），输出：

- **条目查准率 / 查全率 / F1**；
- 匹配上的条目里，**物理页完全一致的比例**、**层级一致的比例**；
- **目录页定位对不对**；
- **漏了哪些、多了哪些**。

写到 `<dir>/score.json`，并打印一行摘要。

```
查准 0.98 / 查全 0.96 / F1 0.97；页码一致 0.92，层级一致 1.00；目录页对（漏 1 条，多 0 条）
```

## 6. 文件一览

| 路径 | 谁写 | 内容 |
|---|---|---|
| `<dir>/tocv.json` | `init` / `locate` / `submit` | 工作状态（书指纹、文本层、目录页、最后一次草稿） |
| `<dir>/sheets/sheet-<first>-<last>.png` | `sheet` | 拼接图（角上印物理页号） |
| `<dir>/pages/p<4 位页号>.png` | `pages` | 大图 |
| `<dir>/pages/p<4 位页号>.txt` | `pages` | 文本层（只当参考） |
| `<dir>/ocr/p<4 位页号>.json` | `submit` | 扫描件按需 OCR 的缓存（只用到的页） |
| `<dir>/toc.json` | `submit` | 目录（`source: "vision"`）+ checks + gate |
| `<dir>/tocv_report.json` | `submit` | 检查结果、失败条目、轮次、状态 |
| `<dir>/score.json` | `score` | 和 gold 对照的准确率 |
