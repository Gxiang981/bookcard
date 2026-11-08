# BookCard 出题 / 交卷层（`bookcard.agentflow`）

> **2026-11-07 流程简化**：抽词不再有试抽/审卡/闸门 2。
> 答案只有 `front` / `type` / `back` / `learn_value`（没有 `evidence`），
> 引擎唯一的把关是「词必须真的出现在这一页原文里」。
> 闸门只剩一道：目录闸门（`toc_gate`）。

维护者定的规格在 `docs/mcp-v1.md` §2（the maintainer 2026-11-06 拍板：抽词和审卡统一用
**用户自己 harness 里 agent 的模型**，引擎不调任何 LLM、不读任何 key）。
本文件是这一层的**使用说明**：函数清单、返回样例、命令行用法。

MCP server 和 skill 是这一层的薄包装（下一张单）；`bookcard flow` 子命令是
给**没有 MCP 的 harness** 直接调的入口。

## 0. 边界

- 引擎只做确定性的事：取字 / OCR、目录、切章节、出题（把页面文字套进提示词）、
  校验并落盘 agent 的回答、挑卡、定位、质检、打包。
- agent 做需要模型的事：抽词加解释、按契约 §6 判卡、看目录对不对、把要拍板的
  事问用户。
- 引擎**不内置云端 LLM key**，也不调模型。内部的 MiniMax 路径
  （`bookcard extract` 直接调 `kwtrain.llm`、benchmark）保留不动，但 MCP 和
  skill 里一律不暴露。
- 所有函数都以书的工作目录为第一个参数，返回**可 JSON 序列化的 dict**；
  出问题的返回 `{"ok": False, "error": "…"}`（不抛异常）。

## 1. 目录

- 书的工作目录：契约 §0 的结构（`book.json` / `pages.jsonl` / `toc.json` /
  `sections.json` / `candidates.jsonl` / `cards.jsonl` /
  `review/` / `quality_report.json` / `pack/`）。
- `book_add` 默认把书放在 `$BOOKCARD_HOME/books/<fp12>/`（`BOOKCARD_HOME`
  没设就是 `~/.bookcard`），也可以用 `--home` 指到别处（例如本仓库内部跑用
  `BOOKCARD_HOME=/train/data/bookcard`，
  书就落在 `train/data/bookcard/books/<fp12>/`）。
- 旧位置 `<home>/<fp12>/`（早期版本没有 `books/` 这一层）的书**不搬**，
  但照样认：MCP 的 `book_dir` / `book_list` 会回退到旧位置（见契约 §0）。
- 引擎侧的作业状态（出题租约、这本书的 PDF 路径）在工作目录的
  `agentflow.json` 里。

## 2. 提示词跟着包走

| 提示词 | 位置 | 版本 |
|---|---|---|
| 抽词 | `src/bookcard/prompts/extract_v2.md`（包内，`importlib.resources` 读） | `ex-v2` |

- 按 package data 打包（`pyproject.toml` 的 `"bookcard.prompts" = ["*.md"]`），
  装成 wheel / `uv tool install` 之后一样能读到。
- `bookcard.extract.default_prompt_path()` 指向包内那份；
  `kwtrain.bench` 的默认被测提示词同样改了，**版本号跟着提示词走（现在是 `ex-v2`）**。
- `bookcard.judge` 模块还留着，但只给**基准工具**（kwtrain）用，不在流程里
  （`tests/test_prompts.py` 拿原文逐字比对）。

## 3. 函数清单

| 函数 | 对应 | 干什么 |
|---|---|---|
| `book_add(pdf_path, *, home=None, books_jsonl=None)` | §2.4 | probe + text（扫描件自动 OCR），返回 `fp12` / 标题 / 页数 / verdict / 是否 OCR / `scene_suggested`（默认 `image`）+ 三条判据的数值与 `text_eligible`。同一本书再加一次是幂等的。 |
| `status(book_dir)` | §2.4 | 当前走到哪一步、两道闸门的状态、各计数，外加一句 `next`。 |
| `toc_review(book_dir)` | §2.3 | 目录条目 + 目录页原文 + 闸门 1 的状态，给 agent 逐条核对。目录只到 `pending`（不许自动批）。 |
| `toc_gate(book_dir, approve, approved_by, notes)` | §2.3 | 闸门 1 拍板。批准前重跑契约 §6 的四条**确定性**判据，有一条不过就不许 approved。`approved` 只能由这里写。 |
| `sections_build(book_dir)` | §2.4 | 切章节（闸门 1 没过、或没人确认过，就拒绝）。 |
| `extract_next(book_dir, max_segments=4)` | §2.1 | 出抽词题。 |
| `extract_submit(book_dir, seg_id, output, extractor)` | §2.1 | 交抽词的卷：解析 → 落位 → 写 `candidates.jsonl`。 |
| `cards_build(book_dir)` | §2.4 | 挑卡 → 定位 → 质检，返回质检摘要。 |
| `pack(book_dir, edition, scene=None, scene_chosen_by=None)` | §2.4 | 打包。内部分享版的 `scene` **可选**，不传就用默认场景（`image`，the maintainer 2026-11-06 拍板）；`scene_chosen_by` 只认 `user` / `default`。 |
| `pack_from_cli(book_dir, edition, scene=None)` | §2.4 | 命令行打包入口（`flow_cli` 用）：显式传 `scene` 记 `cli`，不传记 `default`（契约 §7.1）。 |

### 3.1 标识格式

- `extractor`：`agent:<harness>:<模型名>:<提示词版本>`，例如
  `agent:claude-code:claude-sonnet-4-5:ex-v2`。**不合格式就拒收。**
- `approved_by` / `reviewer`：`agent:<harness>:<模型名>`，例如
  `agent:claude-code:claude-sonnet-4-5`。**不合格式就拒收。**

### 3.2 出题租约

「已出题未交卷」的段不重复出，除非超过 **30 分钟**没交。租约状态存在工作目录的
`agentflow.json` 里（`extract.issued` / `judge.issued` 各存一份「key → 签发时间」）。
交卷之后租约就还回去。

### 3.3 覆盖规则

同一个 seg 重复交卷：**后交的覆盖先交的**（`candidates.jsonl` 里同一个
`seg_id` 只留一行）。

## 4. 返回样例

### `book_add`

```json
{"ok": true, "fp12": "7576b2db6bbb", "book_dir": "/…/books/7576b2db6bbb",
 "title": "", "subject": "", "page_count": 10, "verdict": "digital",
 "ocr": false, "ocr_pages": 0, "scene_suggested": "image", "text_eligible": true,
 "scene_reasons": ["默认走图片场景（the maintainer 2026-11-06）",
                   "三条判据全中，可选文本场景：探针判定 digital，body 页能直接取字 100%，图片覆盖率中位数 0%"],
 "scene_detection": {"verdict": "digital", "text_origin_ratio": 1.0,
                     "median_image_coverage": 0.0, "text_eligible": true,
                     "reasons": ["…"]},
 "warnings": []}
```

### `extract_next`

```json
{"ok": true, "prompt_version": "ex-v2", "system": "<提示词 system 段>",
 "segments": [{"seg_id": "book:7576b2db6bbb:p0002", "page": 2,
               "section_title": "第1课 中华文明的起源", "user": "<渲染好的 user 段>"}],
 "remaining": 6, "done": 0}
```

### `extract_submit`

```json
{"ok": true, "seg_id": "book:7576b2db6bbb:p0002", "items": 1,
 "dropped": {"not_in_text": 0, "bad_evidence": 0}}
```

解析失败（agent 的回包不像样）：

```json
{"ok": false, "error": "回包里找不到 keywords 列表", "retry": true}
```

### `status`

```json
{"ok": true, "fp12": "7576b2db6bbb", "title": "", "step": "done",
 "gates": {"toc": "approved"},
 "counts": {"pages": 10, "sections": 3, "segments": 9, "candidates": 9,
            "cards": 9, "packed": true},
 "remaining": 0, "next": "做完了。可以调 browse_open 把包导进浏览库。"}
```

### `pack`

```json
{"ok": true, "pack": "/…/pack/7576b2db6bbb.ttfc.zip", "edition": "private",
 "bytes": 3280, "cards": 9, "libraries": 3, "images": 0,
 "scene_mode": "text", "scene_detected": "image", "scene_override": "text",
 "scene_chosen_by": "user", "scene_reasons": ["…"],
 "contexts": {"cards": 9, "with_context": 9, "without_context": 0},
 "gates": {"toc": "approved"},
 "book": {"fp12": "7576b2db6bbb", "scene_mode": "text",
          "scene_suggested": "image", "scene_chosen_by": "user"}}
```

内部分享版不传 `scene`（the maintainer 2026-11-06 拍板：**默认走图片场景**，不用问用户）：

```json
{"ok": true, "pack": "/…/pack/7576b2db6bbb.ttfc.zip", "edition": "private",
 "scene_mode": "image", "scene_detected": "image", "scene_override": null,
 "scene_chosen_by": "default", "scene_reasons": ["默认走图片场景（the maintainer 2026-11-06）", "…"],
 "book": {"fp12": "7576b2db6bbb", "scene_mode": "image",
          "scene_suggested": "image", "scene_chosen_by": "default"}}
```

`scene_chosen_by` 只认 `"user"` / `"default"`，别的值直接报错：

```json
{"ok": false, "error": "scene_chosen_by 只能是 user 或 default（现在是 'cli'）"}
```

命令行走 `pack_from_cli(book_dir, edition, scene=None)`：显式传 `scene` 记 `"cli"`，不传记 `"default"`（契约 §7.1），返回结构与 `pack` 相同。

## 5. 命令行

每个函数一个同名子命令（函数名里的下划线换成连字符），结果 **JSON 打到
stdout**，日志走 stderr。退出码：0 正常，1 业务失败（闸门没过、解析失败……），
2 参数用错。

```bash
bookcard flow book-add BOOK.pdf [--home DIR] [--books-jsonl PATH]
bookcard flow status DIR
bookcard flow toc-review DIR
bookcard flow toc-gate DIR (--approve | --reject) --approved-by WHO [--notes TEXT]
bookcard flow sections-build DIR
bookcard flow extract-next DIR [--mode trial|full] [--max-segments N]
bookcard flow extract-submit DIR --seg-id ID (--output-file PATH | --output TEXT) --extractor WHO
bookcard flow judge-next DIR [--batch-size N]
bookcard flow judge-submit DIR --batch-id ID (--output-file PATH | --output TEXT) --reviewer WHO
bookcard flow trial-gate DIR (--approve | --reject) --approved-by WHO [--notes TEXT]
bookcard flow cards-build DIR
bookcard flow pack DIR [--edition private|public] [--scene text|image]
```

### 5.1 走一遍（夹具书）

```bash
$ bookcard flow book-add book.pdf --home ./home
{"ok": true, "fp12": "7576b2db6bbb", "page_count": 10, "verdict": "digital",
 "ocr": false, "scene_suggested": "text", "scene_reasons": ["探针判定 digital，…"], …}

$ bookcard flow toc-review ./home/books/7576b2db6bbb
{"ok": true, "entries": [{"level": 1, "title": "第1课 中华文明的起源", "page": 2}, …],
 "toc_pages_text": [], "checks": {…}, "gate": {"status": "pending", …}}

$ bookcard flow toc-gate ./home/books/7576b2db6bbb --approve \
      --approved-by agent:claude-code:claude-sonnet-4-5
{"ok": true, "gate": {"status": "approved",
                      "approved_by": "agent:claude-code:claude-sonnet-4-5",
                      "reasons": ["判据1 通过：…", "判据2 通过：…", "判据3 通过：…", "判据4 通过：…"]},
 "entries": 3}

$ bookcard flow sections-build ./home/books/7576b2db6bbb
{"ok": true, "level": 1, "sections": 3, "first_body_page": 2, "last_body_page": 10, …}

$ bookcard flow extract-next ./home/books/7576b2db6bbb --mode trial
{"ok": true, "prompt_version": "ex-v2", "system": "…",
 "segments": [{"seg_id": "book:7576b2db6bbb:p0002", "page": 2,
               "section_title": "第1课 中华文明的起源",
               "user": "书名：；学科：；章节：第1课 中华文明的起源；页码：2\n\n本页文字：\n<<<\n…\n>>>"},
              …],
 "remaining": 6, "done": 0}

$ bookcard flow extract-submit ./home/books/7576b2db6bbb \
      --seg-id book:7576b2db6bbb:p0002 --output-file answer.json \
      --extractor agent:claude-code:claude-sonnet-4-5:ex-v1
{"ok": true, "seg_id": "book:7576b2db6bbb:p0002", "items": 1,
 "dropped": {"not_in_text": 0, "bad_evidence": 0}}

$ bookcard flow judge-next ./home/books/7576b2db6bbb
{"ok": true, "system": "…", "batches": [{"batch_id": "b000-ca7c3cd5", "user": "待判定的卡：…"}],
 "remaining": 6, "batches_total": 1}

$ bookcard flow judge-submit ./home/books/7576b2db6bbb --batch-id b000-ca7c3cd5 \
      --output-file verdicts.json --reviewer agent:claude-code:claude-sonnet-4-5
{"ok": true, "batch_id": "b000-ca7c3cd5", "judged": 6, "qualified": 6}

$ bookcard flow trial-gate ./home/books/7576b2db6bbb --approve \
      --approved-by agent:claude-code:claude-sonnet-4-5
{"ok": true, "summary": {"cards": 6, "pass_rate": 1.0, "fatal": 0, …},
 "gate": {"status": "approved", …}}

$ bookcard flow extract-next ./home/books/7576b2db6bbb --mode full
{"ok": true, "segments": [{"seg_id": "book:7576b2db6bbb:p0005", …}], "remaining": 3, "done": 6}

$ bookcard flow cards-build ./home/books/7576b2db6bbb
{"ok": true, "select": {"cards": 9, "sections": 3, …},
 "locate": {"cards": 9, "located": 9, "locate_rate": 1.0, …},
 "quality": {"cards": 9, "locate_rate": 1.0, "pass_rate": 1.0, "fatal": 0, …}}

$ bookcard flow pack ./home/books/7576b2db6bbb --edition private
{"ok": true, "pack": "…/pack/7576b2db6bbb.ttfc.zip", "cards": 9, "libraries": 3,
 "scene_mode": "image", "scene_chosen_by": "default", …}

$ bookcard flow pack ./home/books/7576b2db6bbb --edition private --scene text
{"ok": true, "pack": "…/pack/7576b2db6bbb.ttfc.zip", "cards": 9, "libraries": 3,
 "scene_mode": "text", "scene_chosen_by": "cli", …}

$ bookcard flow status ./home/books/7576b2db6bbb
{"ok": true, "step": "done", "gates": {"toc": "approved"},
 "counts": {"cards": 9, "packed": true}, "remaining": 0,
 "next": "做完了。可以调 browse_open 把包导进浏览库。"}
```

## 6. 单测

`tests/test_agentflow.py` 用一本 10 页的小 PDF 夹具（`tests/agentflow_fixtures.py`，
**模型回答写死**在那里）从 `book_add` 走到 `pack`，另外覆盖：

- 闸门 1 没过时 `sections_build` 被拒；
- 闸门 2 没过时 `extract_next(full)` 被拒；
- 阈值不够时 `trial_gate(approve=True)` 不落盘；
- 交卷解析失败时返回 `retry`；
- 同一个 seg 重交时覆盖；
- 租约过期后重新出题；
- 内部分享版不传 `scene` 就走默认场景（`image`），`scene_chosen_by` 记 `default`；
- `scene_chosen_by` 传了非法值（如 `cli`）就报错；
- 标识格式不合规就拒收。

`tests/test_prompts.py` 断言提示词搬进包内之后**内容一个字都没变**。
