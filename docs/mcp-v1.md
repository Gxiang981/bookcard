# BookCard MCP / skill 契约 v1（P2）

维护者定，2026-11-06。依据 README §7 第 2、3、12 条和 the maintainer 2026-11-06 的三条拍板：
1. **抽词、审卡统一用用户自己 harness 里 agent 的模型**。引擎不调任何 LLM、不读任何 key；agent 拿到页面文字和提示词，自己写出结果，再交回引擎校验落盘。
2. **形态**：MCP server 和 skill 一起做；skill 说明先写 DSH（DeepSeek Harness）、Claude Code、Codex、Devin 四家；**验收用 DSH headless + `MiniMax-M3.1-Flash` 从零跑通一本新书**（the maintainer 2026-11-06 改定：本机 DSH 已配好 M3.1 Flash，走 MiniMax token plan）。
3. **扫描件现在就做**（OCR 见 `contract-v1.md` §2.2）；**场景由用户选**（书页图还是文本块，见 `contract-v1.md` §7.1）。

工作目录、文件格式、闸门判据一律沿用 `contract-v1.md`，本文件只定"agent 怎么驱动流水线"。

## 0. 边界

- 引擎只做确定性的事：取字 / OCR、目录、切章节、出题（把页面文字套进提示词）、校验并落盘 agent 的回答、挑卡、定位、质检、打包、浏览页。
- agent 做需要模型的事：抽词加解释、按 §6 判卡、看目录对不对、把要拍板的事问用户。
- 内部用的 MiniMax 路径（`bookcard extract` 直接调 `kwtrain.llm`、benchmark）保留不动，但 **MCP 和 skill 里一律不暴露**。
- 运行时依赖不加：MCP 用标准库自己实现 stdio JSON-RPC，不引入 `mcp` SDK。OCR 是可选依赖 `bookcard[ocr]`。

## 1. 安装与目录

- 安装：`uv tool install "bookcard[ocr] @ git+<仓库地址>"` 或 `pipx install`；本机开发用 `pip install -e '.[ocr]'`。
- 入口：`bookcard mcp`（stdio）。
- 书的工作目录：`$BOOKCARD_HOME/books/<fp12>/`，`BOOKCARD_HOME` 默认 `~/.bookcard`。目录结构同 `contract-v1.md` §0。浏览库沿用 `~/.bookcard/browse/browse.db`。
- 提示词跟着包走：`prompts/extract_v1.md` 挪到 `src/bookcard/prompts/extract_v1.md`（内容不改，版本号 ex-v1 不变），按 package data 打包，用 `importlib.resources` 读；仓库里所有引用（`bookcard.extract.default_prompt_path`、`kwtrain.bench` 的默认被测提示词、文档）一起改。§6 判卡的提示词同样放进 `src/bookcard/prompts/judge_v1.md`，从 `bookcard/judge.py` 的常量 `SYSTEM` 抽出来，`judge.py` 改成读这个文件，文字逐字不变。

## 2. 引擎侧：agent 驱动的出题 / 交卷（`src/bookcard/agentflow.py`）

MCP 工具和 CLI 都是这一层的薄包装。全部函数以书的工作目录为第一个参数，返回可 JSON 序列化的 dict。

### 2.1 抽词

```python
extract_next(book_dir, mode: Literal["trial", "full"], max_segments: int = 4) -> dict
# {"prompt_version": "ex-v1", "system": "<提示词 system 段>",
#  "segments": [{"seg_id": "...", "page": 12, "section_title": "...", "user": "<渲染好的 user 段>"}],
#  "remaining": 37, "done": 120}
extract_submit(book_dir, seg_id: str, output: str, extractor: str) -> dict
# {"ok": true, "items": 23, "dropped": {"not_in_text": 1, "bad_evidence": 0}}
# 解析失败: {"ok": false, "error": "回包里找不到 keywords 列表", "retry": true}
```

- 分段沿用 `bookcard.extract.build_segments`；`trial` 只出 `pick_trial_sections` 选中的 2 个 section；`full` 要求闸门 2 已 approved，否则返回 `{"ok": false, "error": "闸门 2 未通过"}`。
- "已出题未交卷"的段不重复出，除非超过 30 分钟没交（租约，状态存 `agentflow.json`）。
- `extract_submit` 复用 `parse_keywords` + `place_items`，行格式同 §4，写进 `candidates.jsonl`（断点续跑同现状）。同一个 seg 重复交卷：后交的覆盖先交的。
- `extractor` 由 agent 填：`agent:<harness>:<模型名>:ex-v1`，例如 `agent:claude-code:claude-sonnet-4-5:ex-v1`。不合这个格式就拒收。

### 2.2 试抽审卡（闸门 2）

```python
judge_next(book_dir, batch_size: int = 8) -> dict
# {"system": "<judge_v1 提示词>", "batches": [{"batch_id": "...", "user": "<render_batch 的结果>"}], "remaining": 3}
judge_submit(book_dir, batch_id: str, output: str, reviewer: str) -> dict
trial_gate(book_dir, approve: bool, approved_by: str, notes: str) -> dict
# 返回 §6 的汇总（合格率、fatal 数、各项分布）和闸门状态
```

- 先由 `write_trial_cards` 从试抽候选出卡，再分批出题。`judge_submit` 复用 `parse_verdicts` + `apply_deterministic`，记录写 `review/agent.jsonl`。
- `trial_gate(approve=True)` 只在 §6 阈值全部满足时才落 approved；不满足返回失败原因和"改提示词或参数重试"的提示，不落盘。`approved_by` 格式 `agent:<harness>:<模型名>`。

### 2.3 目录（闸门 1）

```python
toc_review(book_dir) -> dict
# {"entries": [...toc.json 的条目...], "toc_pages_text": [{"page": 4, "text": "..."}], "gate": {...}}
toc_gate(book_dir, approve: bool, approved_by: str, notes: str) -> dict
```

- agent 对照目录页原文逐条核对标题和页码，按 §3 判据决定。不通过就把问题讲给用户，流程停在这里；v1 不提供改目录的工具。
- `toc_review` 只把目录摊给 agent 核对：`gate.status` 是 `pending`（判据全过也只是 pending），**`approved` 只能由 `toc_gate(approve=true)` 写**。跳过 `toc_gate` 直接调 `sections_build` 会被拒（错误文字里说清下一步）。

### 2.4 其余步骤

- `book_add(pdf_path) -> dict`：probe + text（扫描件自动 OCR）。返回 `fp12`、标题、页数、verdict、是否 OCR、`scene_suggested`（默认 `image`）和判定理由（三条判据各自的数值 + `text_eligible`）。同一本书再加一次是幂等的。
- `sections_build(book_dir)`、`cards_build(book_dir)`（select → locate → audit，返回质检摘要）、`pack(book_dir, edition, scene=None, scene_chosen_by=None)`：包装现有 pipeline 函数。
- `status(book_dir) -> dict`：当前走到哪一步、两道闸门状态、各计数，外加一句 `next`，写明 agent 下一步该调哪个工具。skill 让 agent 每步之后都看 `next`。

## 3. MCP server（`src/bookcard/mcp/`）

- 协议：MCP stdio，JSON-RPC 2.0，一行一条消息。实现 `initialize`、`notifications/initialized`、`ping`、`tools/list`、`tools/call`；`protocolVersion` 回客户端请求的版本（支持 `2025-06-18` 和 `2025-03-26`）。日志只写 stderr。
- 工具出错时返回 `isError: true`，错误文字用中文，说清下一步怎么做；不抛到协议层。
- 工具（名字固定，参数用 JSON Schema 写全；`book` 参数一律是 `fp12`）：

| 工具 | 对应 | 说明 |
|---|---|---|
| `book_add` | §2.4 | 参数 `pdf_path` |
| `book_list` | — | 列 `$BOOKCARD_HOME/books/`（新位置）和 `$BOOKCARD_HOME/` 根下（旧位置）的书和各自的 `status.next`；每本带 `location`（`books` / `legacy`） |
| `book_status` | `status` | |
| `toc_review` / `toc_gate` | §2.3 | |
| `sections_build` | §2.4 | 闸门 1 没过就拒绝（没人确认过也不许切章节） |
| `extract_next` / `extract_submit` | §2.1 | |
| `judge_next` / `judge_submit` / `trial_gate` | §2.2 | |
| `cards_build` | §2.4 | 全量抽完才能调 |
| `pack` | §2.4 | `edition` 必填；`scene` **可选**（默认 `image`，the maintainer 2026-11-06 拍板：不用问用户），`scene_chosen_by` 可选（`user` / `default`，默认 `default`） |
| `browse_open` | browse-lite | 把这本书的包导进浏览库，在 MCP 进程里起一个线程跑 `bookcard browse serve`（只听 127.0.0.1，端口被占就顺延），返回 URL；重复调用复用已起的服务 |

- 长任务（OCR、定位、打包）同步执行，执行期间每页发一次 `notifications/progress`（客户端给了 `progressToken` 时）。

## 4. skill（`skills/bookcard/`）

- `skills/bookcard/SKILL.md`，开头是标准 frontmatter（`name: bookcard`、`description`），DSH、Claude Code、Codex、Devin 都按这个格式读（DSH 扫 `~/.dsh/skills/<name>/SKILL.md`、`~/.agents/skills/` 和项目内 `.dsh/skills/`）。
- 正文要写：
  1. **装和接**：安装命令；四家各自注册 MCP 的写法（DSH：profile 的 `cordis.patch.yml` 加一条 `@deepseek-ai/dsh-mcp-client`，`transport: stdio`、`command: bookcard`、`args: [mcp]`；Claude Code `claude mcp add bookcard -- bookcard mcp`；Codex `~/.codex/config.toml` 的 `[mcp_servers.bookcard]`；Devin 的 MCP 配置）。写之前先按各家当前文档核对，不凭记忆写。
  2. **流程**：`book_add` → 把 `scene_suggested` 和理由讲给用户，**问用户**要书页图还是文本块、要不要公开版 → `toc_review` / `toc_gate` → `sections_build` → 试抽循环 → `judge` 循环 → `trial_gate` → 全量循环 → `cards_build` → `pack` → `browse_open`。每步后看 `book_status.next`。
  3. **抽词时怎么做**：严格按 `extract_next` 给的 system / user 作答，只输出 JSON；交卷失败带 `retry` 就重答这一段。支持子 agent 的 harness 可以并行抽多段。
  4. **闸门判据**：`contract-v1.md` §3、§6 的判据原文抄进来，审卡时逐条对照；闸门不许跳。
  5. **不做的**：不改书源文件，不把书页图放进公开包，不把 key 写进任何文件。
- `skills/bookcard/harness/` 下放三家的最短接入示例（一页以内）。

## 5. 验收

- 单测：`agentflow` 用一个小 PDF 夹具从 `book_add` 走到 `pack`，模型回答用夹具里写死的 JSON；MCP 用子进程起 `bookcard mcp`，走完 `initialize` → `tools/list` → 几次 `tools/call`。
- 实跑：用 DSH headless（`dsh --profile headless --patch <overlay>`，模型 `MiniMax-M3.1-Flash`）从零跑通一本没跑过的数字版教材（由维护者指定），产出私有包，并在浏览页里打开。overlay 只放模型和 MCP 两类条目，key 只走环境变量 `MINIMAX_CN_API_KEY`，不写进任何文件。
- 扫描件：选一本人教扫描件（维护者指定）走 OCR，给用户看文本块和书页图两种场景的样张，由用户选。
