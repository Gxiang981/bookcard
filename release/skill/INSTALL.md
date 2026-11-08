# 安装 bookcard skill

从书里出学习卡片：抽词 + 解释 → 打包（`.ttfc.zip`）→ 导入 Flashcard App / 在浏览页看。

## 1. 装引擎（一次就够）

引擎是个 Python 包（`bookcard`），装完就有了 `bookcard` 命令和 MCP 服务端。

```bash
# 方式 A：从 GitHub 装（推荐，拿到新版本就重装）
uv tool install "bookcard[ocr] @ git+https://github.com/Gxiang981/bookcard.git"

# 方式 B：本地克隆后装
git clone https://github.com/Gxiang981/bookcard.git bookcard-repo
uv tool install "./bookcard-repo"          # 要 OCR 就加 [ocr]
```

Windows 同一条命令（PowerShell 里一样）✓ —— 装了 Python 3.11+ 和
[uv](https://docs.astral.sh/uv/) 就行 ✓。

> 引擎不内置任何模型 key ✓ —— 抽词由**你自己的 agent 或你配的模型**做 ✓。
> 引擎只做确定性的活：取字、目录、切章节、定位、打包、浏览页 ✓。

## 2. 装 skill（每个 harness 一次）

skill 是一份 `SKILL.md` ✓ —— 告诉 agent「这本书该怎么变成卡片」✓。
把**本目录**（`skill/`）按下面的路径放好 ✓：

| harness | 放哪 | 备注 |
|---|---|---|
| DSH | `~/.dsh/skills/bookcard/` | 本目录整个拷进去 ✓ |
| Claude Code | `~/.claude/skills/bookcard/` | 同上 ✓ |
| Codex | `~/.agents/skills/bookcard/` | DSH 和 Codex 共用这一份 ✓ |
| Devin | Customize → Skills | 传本目录的内容 ✓ |

```bash
mkdir -p ~/.dsh/skills/bookcard
cp -R skill/. ~/.dsh/skills/bookcard/
```

## 3. 接 MCP（让 agent 能调引擎的工具）

| harness | 怎么接 |
|---|---|
| DSH | profile 的 `cordis.patch.yml` 加一条 `@deepseek-ai/dsh-mcp-client`（`transport: stdio`）—— 见 `harness/dsh/bookcard.patch.yml` ✓ |
| Claude Code | `claude mcp add bookcard -- <绝对路径>/bookcard mcp` |
| Codex | `~/.codex/config.toml` 的 `[mcp_servers.bookcard]` |
| Devin | `devin mcp add bookcard -- <绝对路径>/bookcard mcp` |

**命令要写绝对路径** ✓ —— 别指望 PATH ✓。

## 4. 开跑

```
book_add → toc_review / toc_gate → sections_build
→ extract_next / extract_submit（逐段抽词）
→ cards_build → pack → browse_open
```

- 只有**一道闸门**（目录确认）✓ —— 抽词本身没有第二道闸门 ✓，
  引擎把关「词必须真的出现在原文里」✓
- **最终产出落在系统「下载」文件夹** ✓（mac `~/Downloads` ✓，
  win `%USERPROFILE%\Downloads` ✓），文件名 `<书名>-<fp12>.ttfc.zip` ✓
- 想换个位置：环境变量 `BOOKCARD_OUTPUT_DIR` ✓

详细的流程和判据看 `SKILL.md` ✓ ✓。
