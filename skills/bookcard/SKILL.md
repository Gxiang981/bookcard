---
name: bookcard
description: 从书里提取学习卡片（抽词 + 解释、打卡包、在浏览页里看）。当用户要「把这本书做成学习卡片 / 抽关键词和解释 / 生成卡包 / 导入 Flashcard / 从某本书出题」时用这个 skill。走 bookcard MCP 的 11 个工具，从 book_add 到 browse_open，目录一道闸门。
---

# BookCard：从书里提取学习卡片

引擎只做确定性的事（取字、目录、切章节、出题、校验落盘、挑卡、定位、质检、打包、浏览页）；
**抽词由你自己的模型作答**，引擎不调任何模型、不读任何 key。

工具名前缀各家不一样：DSH 是 `mcp__bookcard__*`，Claude Code / Codex / Devin 也都用
`mcp__bookcard__<工具名>`。下面一律写裸工具名（`book_add`、`extract_next`……）。

## 0. 装和接

```bash
# 装（推荐 uv tool install；pipx / pip -e 也行）
uv tool install "bookcard[ocr] @ git+<仓库地址>"
uv tool install --from /path/to/46_BookCard bookcard      # 本地路径
pip install -e '.[ocr]'                                    # 本机开发
```

注册 MCP（**命令要写绝对路径**，别指望 PATH）：

| harness | 怎么接 | 出处 |
|---|---|---|
| DSH | profile 的 `cordis.patch.yml` 加一条 `@deepseek-ai/dsh-mcp-client`（`transport: stdio`） | `skills/bookcard/harness/dsh/` |
| Claude Code | `claude mcp add bookcard -- <绝对路径>/bookcard mcp` | `skills/bookcard/harness/claude-code/` |
| Codex | `~/.codex/config.toml` 的 `[mcp_servers.bookcard]` | `skills/bookcard/harness/codex/` |
| Devin | `devin mcp add bookcard -- <绝对路径>/bookcard mcp` | `skills/bookcard/harness/devin/` |

skill 本身放各家扫得到的目录（`~/.agents/skills/bookcard/SKILL.md` 一份就够 DSH 和 Codex 共用；
Claude Code 用 `~/.claude/skills/bookcard/SKILL.md`；Devin 走 Customize → Skills）。

书的工作目录根是 `$BOOKCARD_HOME`（默认 `~/.bookcard`），一本书一个目录 `$BOOKCARD_HOME/books/<fp12>/`。
早期版本把书直接放在 `$BOOKCARD_HOME/<fp12>/`（没有 `books/` 这一层）——那些书**不搬**，
但照样认：`book_list` 两处都列，每本带 `location`（`books` = 新位置，`legacy` = 旧位置）。

## 0.5 最终产出在哪

- **卡包 zip**：系统「下载」文件夹（mac `~/Downloads` / win `%USERPROFILE%\Downloads`），
  文件名 `<书名>-<fp12>.ttfc.zip`。用户从那儿拿走、拖进 App。
- **分享页**（`bookcard-share`）：同名的**目录**（`<书名>-<fp12>/index.html` + `pages/`）——
  页面图和 html 同级，发走要整个目录发。
- 引擎的工作目录（`$BOOKCARD_HOME/books/<fp12>/`）是**状态**，不是交付物。
- 换位置：环境变量 `BOOKCARD_OUTPUT_DIR`。

## 1. 流程（每步之后调一次 `book_status` 看 `next`）

开工前可以先 `book_list` 看一眼 `$BOOKCARD_HOME` 里已经有哪些书、每本走到哪一步了
（`book_status` 是单本书的详细状态，`book_list` 是清单）。同一本书重复 `book_add` 不会重复建目录。

1. **`book_add`** — 参数 `pdf_path`（可选 `books_jsonl` 给元数据）。返回 `fp12`、书名、页数、
   探针结论、是否走 OCR、**`scene_suggested`（默认 `image`）和 `text_eligible` 判定理由**。
2. **问用户一个事**：要**内部分享版**（`private`）还是**公开版**（`public`）？
   **场景不用问**——默认走**书页图**（`image`）；只有用户主动要文本块时，
   第 11 步才传 `scene="text"`、`scene_chosen_by="user"`。
3. **`toc_review`** — 拿到目录条目、目录页原文、闸门 1 的检查结果。对照目录页原文逐条核对
   标题和页码（判据见 §4）。**有疑问就讲给用户，别自己改目录**（v1 没有改目录的工具）。
   这一步只把目录摊给你看（`gate.status` 是 `pending`），**不许自己批**。
4. **`toc_gate`** — `approve`（true/false）、`approved_by`（`agent:<harness>:<模型名>`）、
   `notes`（逐条写理由）、**`level`（抽词切章节用目录的第几层，用户在第二步确认的；
   不传就是第 1 层）**。批准前程序会重跑确定性判据，不过就不许批准。
   **闸门 1 的 `approved` 只能由这一步写**——不调 `toc_gate` 就调 `sections_build` 会被拒。
   **`level` 写进 `toc.json` 的 `extract_level`，`sections_build` 就按它切**——想换层级
   要重新过闸门（改了层级要重新确认目录）。
5. **`sections_build`** — 切章节。闸门 1 没过（或还没人确认过）会被拒。
6. **抽词循环** — `extract_next` → 按提示词作答 → `extract_submit`。
   直到 `extract_next` 返回的 `segments` 为空。**没有审卡这一步**：
   引擎只把关一件事——词必须真的出现在这一页原文里。
7. **`cards_build`** — 挑卡 → 定位 → 质检，写 `cards.jsonl` 和 `quality_report.json`。
   还有段没抽完会被拒。
8. **`pack`**    `edition` 必填（`private` / `public`）。**产出落在系统「下载」文件夹**
   （mac 是 `~/Downloads`，win 是 `%USERPROFILE%\Downloads`；Windows 被重定向到
   OneDrive 时会自动找 `OneDrive/Downloads`），文件名 `<书名>-<fp12>.ttfc.zip` ——
   工作目录里那份只是状态，`browse_open` 从那儿读。场景**默认 `image`，不用问用户**；
    `scene` 可选（`text` / `image`），只有用户主动要文本块时才传 `scene="text"`、
    `scene_chosen_by="user"`（不传就是 `scene_chosen_by="default"`）。公开版**不许**带书页图。
9. **`browse_open`** — 把卡包导进本地浏览库并起浏览服务，返回 URL（只听 127.0.0.1，
    端口被占就顺延，重复调用复用已起的服务）。

**用户中途改主意**（比如要文本块而不是书页图）：重调 `pack` 即可，`scene_mode` 是整本书一个值。

## 2. 抽词时怎么做（`extract_next` → `extract_submit`）

- `extract_next` 给 `system`（抽词提示词，版本 `ex-v2`）和 `segments`，每段一个
  `seg_id` / `page` / `section_title` / `user`（渲染好的这一页文字）。
- **严格按 `system` 作答**：只看这一页的 `user` 文字找词；解释用中文 15–60 字。
- **只输出 JSON**，不要 Markdown 代码块、不要多余说明：

  ```json
  {"keywords": [{"front": "原文片段", "type": "term",
                 "back": "简短解释", "learn_value": 2}]}
  ```

- **没有 `evidence` 字段**——依据句由引擎自己从页面原文里取（包住这个词的那一句）。
  模型不用抄、不用判、也不容易抄错。
- `front` 必须是这一页正文里**一字不差**的连续片段，**从正文复制**，不要凭记忆重打
  （引擎会核对；对不上就丢掉这个词）。
- `extract_submit` 的参数：`book`（fp12）、`seg_id`、`output`（上面那个 JSON 文本）、
  `extractor`（`agent:<harness>:<模型名>:ex-v2`，例如 `agent:dsh:MiniMax-M3.1-Flash:ex-v2`）。
- **交卷失败（返回里带 `retry: true`）就重答这一段再交**，别跳过、别编。
- 同一个 `seg_id` 重复交卷：**后交的覆盖先交的**。
- 已出题未交卷的段 30 分钟内不会重复出题（租约），过期才会重新出。
- **能起子 agent 的 harness 可以并行抽多段**（每段独立作答、独立交卷）。

### 卡包里带什么（下游会怎么用）

- **卡的 `tags`** = **书的元信息标签**：领域（`domain`）+ 水平（`level`）+ 书上自带的 `tags`
  （例：人工智能 / 本科 / 智能体 / 大语言模型 / 软件架构）。
  App 的卡背 chip 读的是 `card_tags` 表，导入端会拿这组标签去建行 —— 只填 JSON 列是不显示的。
- **卡的 `difficulty`** 由 `learn_value` 映射（1 可有可无→2、2 值得学→3、3 核心→4）。
  App 服务端按 difficulty 换 tier（1–2→T1、3→T2、4→T3、5→T4），写死 1 会让所有卡都落 T1。
- **书页图 + bbox**：私有包带整页图和词的高亮框；`bbox` 的高度按字号和基线收紧
  （PDF 的 em 框会高到字号的 2.8 倍，直接用会盖住上下两行）。

## 3. 把关规则（没有审卡这一步）

抽词**不需要审卡**。引擎只做一条确定性检查：

- **词必须真的出现在这一页正文里**（一字不差；跨 PDF 硬折行、兼容码位都算命中）。
  对不上就丢掉这个词，并在交卷结果里报 `dropped.not_in_text`。

其余的（哪些词值得记、解释写得准不准）由你作答时自己把握；引擎不管，也没人复核。

## 4. 闸门判据（原文抄自 `docs/contract-v1.md` §3 / §6，照着逐条对照）

### 目录与切章节（§3）

```json
{"source": "bookmarks|printed|layout", "entries": [{"level": 1, "title": "…", "page": 7}],
 "checks": {"pages_monotonic": true, "first_body_page": 7, "covered_pages": [7, 206], "gaps": []},
 "gate": {"status": "pending|approved|rejected", "approved_by": "agent:agent", "at": "…", "reasons": ["…"]}}
```

`sections.json`：`[{"sid": "s01", "title": "…", "level": 2, "page_start": 7, "page_end": 12, "sort_order": 1}]`。
粒度：取能让每个 section 落在 **3–25 页**的那一级（一般是"课/节/Unit"）；section 之间页码连续、
无空洞、无重叠。section = App 词库。

### 闸门 1：目录（toc.json）—— 全部满足才 approved

1. 条目页码单调不减，最后一条 ≤ 页数；
2. 正文首页之后每一页都落在某个 section 里，无空洞、无重叠；
3. 抽查 3 个条目：该页文字里确实出现条目标题（允许空白差异）；
4. 每个 section 3–25 页，超出要写理由。

理由逐条写进 `gate.reasons`；有一条不满足就 `rejected` 并说明怎么修。
判据全过也只是 `pending`——**`approved` 只能由 `toc_gate` 写**（`build_toc` 不自动批）。

### 闸门只有一道

- 没有 `toc.json.gate.status == "approved"` **不许**切章节（`sections_build` 会被拒），
  也不许打包。
- **抽词没有第二道闸门**：引擎只把关「词必须真的出现在这一页原文里」这一条。
- 确认人可以是 agent（`approved_by: "agent:<名字>"`），但必须按上面的判据逐条写理由。

## 5. 不做的

- **不改书源文件**（NAS 上的 PDF 只读）；产物只写 `$BOOKCARD_HOME`。
- **公开包不许带书页图**（引擎会强校验，zip 里出现任何图片即失败）；公开版还要有书指纹。
- **不把 key 写进任何文件、不打进日志**；引擎不读 key、不调模型。
- 不自己改目录（v1 没有改目录的工具）；目录有问题讲给用户。
- 不替用户改场景（书页图还是文本块）：默认就是书页图（`image`），用户主动要文本块时才传 `scene="text"`。

## 6. 三条纪律（实跑踩出来的）

1. **只用 MCP 工具，不许 import 引擎代码。**
   抽卡、打包一律走 `mcp__bookcard__*` 工具。**不许** `import bookcard.…` 去调引擎内部
   函数做预校验，也**不许**直接读写书的工作目录（`$BOOKCARD_HOME/…`）自己算状态——
   闸门、判据、计数全在引擎里，自己算出来的数跟引擎对不上，最后还是得走一遍工具，
   还多一份可能算错的记录。要看进度就 `book_status`，要列书就 `book_list`。

2. **浏览服务跟着 MCP 进程走，截图必须在同一个 run 里做完。**
   `browse_open` 起的服务是 MCP 进程里的一个线程：MCP 一退（harness 退出、这一轮结束），
   服务就没了，返回的 URL 也不再能访问。要截图、要留证据，就在**同一个 run** 里
   `browse_open` → 打开页面 → 截图 → 收工；别指望下一轮还能连上。

3. **DSH 沙箱里起有头 Chromium 会失败（已知限制，只记录不修）。**
   沙箱的临时目录建不出来（`/var/folders/…/T` mkdir EPERM），有头浏览器起不来。
   要截图走 **CDP**（连已经开着的 Chromium 调试端口）或**无头模式**。
   这是环境限制不是引擎的 bug：绕过去，别在流程里死磕。

## 7. 抽词时怎么对付 PDF 文本层的坏字

有些书的 PDF 文本层是坏的：正文里同一个字，**看到的和搜到的不是一个码位**。
踩过的三种（都出在《智能体设计模式》中文版）：

| 现象 | 长什么样 | 怎么办 |
| --- | --- | --- |
| 兼容表意文字 | `理` 在文本层是 U+F9E4（兼容字），不是 U+7406 | 引擎已容错（NFKC 归一），照常抄即可 |
| 连字 | `ﬂ`（U+FB02）而不是 `fl`，`Reﬂection` | 引擎已容错（NFKC 会展开成 `fl`） |
| 字形映射坏了 | `（` 被映成 `Ջ`（U+054B），配对的 `）` 还整个丢掉 | **引擎只能把坏字当通配**；抄出来的依据句对不上原文 |

**给 agent 的做法**：

- `front` **从页面正文里复制**，不要凭记忆重打 —— 重打会把兼容字写成标准字、坏字写成正常标点。
- **挑不含坏字的词和句子**。正文里出现 `Ջ`、`�` 这种明显不是汉字的符号时，
  绕开它所在的片段更省事（引擎对坏字已经容错，但绕开更稳）。
- 解释（`explanation`）里不要出现乱码字符。
- 引擎判定 `front` 走容错匹配（跨 PDF 硬折行、兼容码位都算命中），
  所以「抄得一模一样」就够，不用去猜码位。

