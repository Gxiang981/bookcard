# 第一步和第二步（规范）

> 2026-11-07 定的版。依据：人教版物理、《智能体设计模式》（中文）、《Agentic Design Patterns Complete》（英文）三本书实跑。

## 硬约束（先记住，不然会跑偏）

1. **抽词只看文字，不看图。** 卡片定位靠「词的字符偏移」→ 引擎按偏移在页面文字里搜 →
   拿到 bbox。agent 交上来的词必须在页面文字里逐字命中。
   → **看图只允许出现在「读版式」这一个环节（提目录）**。
2. **页面图是输出侧的**：给用户翻书确认 + 卡上高亮那个词。它是抽词的**产物**，不是输入。
3. **一套页面图**：确认时翻的那套 = 最终打包的那套 = 引擎出的那套（全书每页）。
4. **目录页不参与抽词**（第三步），前置页和附录同理。
5. **只做内部分享版**：`edition=private`，包里永远带页面图，场景固定 `image`。

---

## 第一步：探测 + 取字

**输入**：一个 PDF 的路径。
**产物**：`<BOOKCARD_HOME>/books/<fp12>/{book.json, pages.jsonl}`。

```
用户 → PDF 路径
agent →
  ① 缺依赖自己装（uv tool install "bookcard[ocr]"）——不问用户
  ② book_add(pdf_path)
       探测：页数 / 有没有文本层 / 印刷页标 / 目录页范围 / 正文起止
       取字：扫描件 → 整本 OCR（自己跑，不问）；否则用 PDF 文本层
       每页打 kind：cover/front/toc/body/exercise/appendix/blank
  ③ 幂等：同一本 PDF 再 add 一次直接复用
```

- **不问用户任何问题**。缺依赖自己装，OCR 自己跑（很慢就放后台，跑完再继续）。
- 唯一的例外：加密/损坏 PDF —— 把引擎的报错原文给用户。
- 附件形态：先落到稳定路径（`$BOOKCARD_HOME/inbox/`）再喂给引擎。
- 工作目录默认 `~/.bookcard`（`BOOKCARD_HOME` 可改）。

**这一步不产页面图**（出图是第二步）。

### 已知坑

| 坑 | 说明 |
|---|---|
| 兼容表意文字 | 有些 PDF 的文本层用兼容码位（理=U+F9E4，行、不、更、力、量、器…），占正文汉字的 **3.7%**。模型若写成标准码位 → `place_items` 匹配不上 → **整张卡被丢**；就算匹配上，`search_for` 也定位不到 → **bbox 丢失**。修法在引擎侧：probe 时做 NFKC 归一 + 保留到原文的偏移映射。 |
| 引擎不读 PDF 元数据 | `book.json` 的 title/subject/publisher **只来自可选的 `books_jsonl`**，不读 PDF 自己的元数据 → 第二步 agent 要从封面/正文读出来回填。 |
| 文本层脏页 | 整页是图、文本层是乱码的页（如人教版的「致同学们」）。现在 OCR 是**整本级**决策（`_should_use_ocr`），没有按页 OCR → 这类页要么按页补 OCR，要么标记为不可抽页排除。 |

---

## 第二步：出图 → 提目录 → 给用户确认

**产物**：`<book>/pages/pNNNN.webp`（150dpi q95）+ `<book>/thumbs/pNNNN.webp`（280px）
+ `<book>/toc.json` + `<book>/index.html`（确认页）。

### 2.1 出图（引擎）

全书**每一页**都渲染：大图 + 缩略图。规格沿用分享包现有那套（150dpi webp q95 / 280px 缩略图）。
这就是最终打包用的那套 —— 确认页和最终包引用同一份，不重渲染。

### 2.2 提目录（**分层**，按成本从低到高）

| 层 | 手段 | 什么时候用 | 代价 |
|---|---|---|---|
| 1 | **PDF 书签**（outline） | 书有书签 | 零模型调用，页码已是物理页 |
| 2 | **印刷目录 / 版式解析** | 没有书签，但书里有印出来的目录 | 零模型调用 |
| 3 | **看图抄目录**（`bookcard tocv`） | 前两层判据不过 | 12 张目录页大图 + 逐条转写 |
| 3' | **从正文抽**（`source: "body"`） | **书里根本没有目录** | 找各章标题页 |

第 1、2 层都由 `bookcard toc` 自动做（内部优先级：bookmarks → printed → layout），
**判据不过才升级到第 3 层**。第 3 层的流程：

```
bookcard tocv init <pdf> --out <book_dir>
bookcard tocv sheet <book_dir> --first 1 --last 24     # 拼图，角上印物理页号
bookcard tocv locate <book_dir> --pages 4,5,6 --by agent:<harness>:<model>
bookcard tocv pages <book_dir> --pages 4,5,6            # 目录页大图
# agent 看大图抄条目 → toc_draft.json
bookcard tocv submit <book_dir> --draft toc_draft.json --by agent:<harness>:<model>
```

草稿格式（`docs/tocv-v1.md` 是契约）：

```json
{
  "toc_pages": [4, 5, 6],
  "source": "body",
  "entries": [
    {"level": 1, "title": "第一章 机械运动", "printed_page": 1, "kind": "body"}
  ],
  "anchors": [
    {"printed_page": 1, "physical_page": 9},
    {"printed_page": 120, "physical_page": 128}
  ]
}
```

- `source: "body"` = **这本书没有目录页，目录是从正文抽的** → 判据 C4 判「不适用」，
  不算失败（C3 仍然在正文页上对照标题）。不声明来源又没目录页 → C4 照旧判不通过
  （防止「agent 忘了 locate」被误放行）。
- `anchors` 至少两条（第一条一级条目 + 最后一条一级条目）。
- **抄到哪一层**：抄到「再往下一层全是重复栏目名（一图速览/关键要点/总结/参考文献）」的那层就停。
  抄太深会让条目数爆炸（12 页目录能有 540 条），也影响切章节的粒度。
- 提交后闸门状态只会是 `pending` / `rejected`，**永远不会 approved** —— 批准是人的事。

### 2.3 给用户确认

生成 `index.html`（**书态**：全书页面 + 目录面板，目录可编辑），写死在 `<book>/index.html`
（和 `pages/`、`thumbs/` 同级 —— 和最终包一个布局）。

页面的约定（已定，别再改）：

- **左栏 = 目录树**（不是词库列表）。叶子标成**全路径**：`简介 | 前言`、
  `智能体的特征 | Level 0：核心推理引擎`；三级就是 `一 | 二 | 三`。
- 左栏第一行是**「目录」**，右边显示**目录所在的页码区间**（`p.2–13`），
  **点它跳到目录的第一页**（用户拿书上的印刷目录跟右边那份逐条对）。
- **右栏 = 目录树**，多级时**页号只给最下一级**（父级那行只是分组）。
- 右栏头里有个**层级下拉**：选 N 级 = 显示 1..N 级（选 2 级 = 1 级 + 2 级），
  左右栏同步；默认是**本书实际用到的最大层级**；可以「＋ 增加一级」，
  加完之后循环点数字徽章就能调到新的一级。
- 顶栏：**书名可点开改**；右边**领域**（自由文本）和**水平**（下拉：小学/初中/高中/本科/硕博）
  可编辑；还能加自由标签。agent 在识别书名时就把这几项写进来。
- **offset = 前置页数（正数）**，页面上的公式是 **`PDF 页 = 印刷页 + offset`**。
- 目录改动**实时反映到左栏**（改标题/页码/层级/删除，左栏跟着变）。
- 改完 → 顶栏 **⤒ 导出** → `edits.json`（**目录和书元数据永远带**，改没改都带）→
  给 agent → agent 写回工作目录 → 引擎重跑闸门。
- **storage key 必须带包指纹**：按 `book_id` 存会让旧目录盖住新目录（已经踩了三次）。

### 2.4 目录页不参与抽词

第三步抽词的范围**不含目录页**（也不含前置页和附录）。引擎那边靠「正文起止页」天然排掉，
但流程里要写明这条 —— 别让抽词范围越过目录页。

---

## 两步跑完后的状态

```
<BOOKCARD_HOME>/books/<fp12>/
  book.json        书名 / 领域 / 水平 / 标签 / 指纹 / 场景建议
  pages.jsonl      458 页，每行一页：文字 / text_origin / kind / printed_page / 断行
  pages/           p0001.webp …（全书，每页）
  thumbs/          p0001.webp …（缩略图）
  pages-index.json 页面清单
  toc.json         目录（entries + printed_page + offset + pages + origin）
  tocv.json        看图 v1 的状态（目录页在哪儿、谁看的）
  index.html       确认页（书态）
```

闸门 1 还没批 —— **用户的确认就是批准**。批了才进第三步（抽词）。
