# Claude Code 接入：一页

出处（2026-11-06 的当前文档，写之前核一遍）：

- MCP：<https://code.claude.com/docs/en/mcp>（完整参考）、<https://code.claude.com/docs/en/mcp-quickstart>（上手）
- skill：<https://code.claude.com/docs/en/skills>

## 1. 装 bookcard

```bash
uv tool install --from /path/to/46_BookCard bookcard
command -v bookcard          # 记下绝对路径
```

## 2. 注册 MCP

```bash
claude mcp add bookcard -- ~/.local/bin/bookcard mcp
```

- `--` 之后是**要跑的进程和参数**（`--` 前面的 `bookcard` 是 MCP server 的名字）。
- 验证：`claude mcp list`、`claude mcp get bookcard`；会话里用 `/mcp` 看工具列表。
- 工具名前缀是 `mcp__bookcard__<工具名>`。
- 改完 `.mcp.json` 要**重开会话**才生效。
- 想让某个项目的人都能用，加 `--scope project`（写进项目里的 `.mcp.json`），
  自己所有项目都能用就 `--scope user`。

也可以直接写 `.mcp.json`：

```json
{
  "mcpServers": {
    "bookcard": {
      "command": "~/.local/bin/bookcard",
      "args": ["mcp"]
    }
  }
}
```

## 3. 装 skill

| 位置 | 路径 | 作用范围 |
|---|---|---|
| 个人 | `~/.claude/skills/bookcard/SKILL.md` | 这台机器上的所有项目 |
| 项目 | `.claude/skills/bookcard/SKILL.md` | 提交进仓库，团队共用 |

```bash
mkdir -p ~/.claude/skills
ln -sfn "$PWD/skills/bookcard" ~/.claude/skills/bookcard
```

- 只认一层：`<根>/<skill 名>/SKILL.md`；frontmatter 要有 `description`，
  `name` 给了就当命令名（不给就用目录名）。
- 目录名和 `name` 用小写字母 + 连字符（`bookcard`）。
- `/bookcard` 直接调用；描述对得上时 Claude 会自己加载。

## 4. 跑 bookcard 的两条纪律（实跑踩出来的）

1. **只用 MCP 工具，不许 import 引擎代码。**
   抽卡、审卡、打包一律走 `mcp__bookcard__*`；不许 `import bookcard.…` 去调引擎内部函数
   做预校验，也不许直接读写 `$BOOKCARD_HOME` 下书的工作目录自己算状态——
   闸门、判据、计数都在引擎里。要看进度调 `book_status`，要列书调 `book_list`。

2. **浏览服务跟着 MCP 进程走，截图必须在同一个 run 里做完。**
   `browse_open` 起的服务是 MCP 进程里的一个线程：会话一关、MCP 一退，服务就没了，
   URL 也不再能访问。要截图、要留证据，就在**同一个 run** 里
   `browse_open` → 打开页面 → 截图 → 收工。

