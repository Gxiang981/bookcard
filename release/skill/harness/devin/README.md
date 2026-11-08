# Devin 接入：一页

出处（2026-11-06 的当前文档，写之前核一遍）：

- MCP 配置：<https://docs.devin.ai/cli/extensibility/mcp/configuration>
- MCP 服务器与市场（网页版 Customize → MCPs）：<https://docs.devinenterprise.com/zh/work-with-devin/mcp>

Devin 有两套：**Devin CLI**（本机命令行）和 **Devin 网页版**（Customize → MCPs / Skills）。
下面先写 CLI，再写网页版。

## 1. 装 bookcard

```bash
uv tool install --from /path/to/46_BookCard bookcard
command -v bookcard          # 记下绝对路径
```

## 2. 注册 MCP（Devin CLI）

```bash
devin mcp add bookcard -- ~/.local/bin/bookcard mcp
devin mcp list               # 看有没有起来
devin mcp get bookcard       # 看细节
```

- `--` 之后是**要跑的进程和参数**（`--` 前面的 `bookcard` 是 MCP server 的名字）。
- 传输方式自动推断：给了 URL 就是 HTTP，尾部有参数就是 stdio。
- 作用域（`-s/--scope`）：

  | scope | 落哪 |
  |---|---|
  | `local`（默认） | `.devin/mcp_config.local.json`（进 .gitignore） |
  | `project` | `.devin/mcp_config.json`（团队共享） |
  | `user` | `~/.config/devin/mcp_config.json`（Windows：`%APPDATA%\devin\mcp_config.json`） |

- 也可以直接写配置文件，`mcpServers` 一节：

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

- 工具名前缀是 `mcp__bookcard__<工具名>`。
- **MCP 工具默认每次都要批准**——想免批准就把工具名加进 Devin 的 `permissions.allow`。

## 3. 注册 MCP（Devin 网页版）

Customize → MCPs 标签页 → Add custom MCP → 选 STDIO 传输，填：

- 命令：`~/.local/bin/bookcard`
- 参数：`mcp`

需要 Manage MCP Servers 权限（个人 / 组织 / 企业三种作用域）。

## 4. 装 skill

- **Devin CLI / 网页版**：skill 走 `SKILL.md`（`name` + `description` 的 frontmatter），
  放到 Devin 的 skill 目录，或在 Customize → Skills 里建。
- 想让 DSH / Codex / Claude Code 也用同一份，把 `skills/bookcard/` 链到各家扫得到的目录
  （`~/.agents/skills/bookcard`、`~/.dsh/skills/bookcard`、`~/.claude/skills/bookcard`）。

## 5. 跑 bookcard 的两条纪律（实跑踩出来的）

1. **只用 MCP 工具，不许 import 引擎代码。**
   抽卡、审卡、打包一律走 `mcp__bookcard__*`；不许 `import bookcard.…` 去调引擎内部函数
   做预校验，也不许直接读写 `$BOOKCARD_HOME` 下书的工作目录自己算状态——
   闸门、判据、计数都在引擎里。要看进度调 `book_status`，要列书调 `book_list`。

2. **浏览服务跟着 MCP 进程走，截图必须在同一个 run 里做完。**
   `browse_open` 起的服务是 MCP 进程里的一个线程：这一轮结束、MCP 一退，服务就没了，
   URL 也不再能访问。要截图、要留证据，就在**同一个 run** 里
   `browse_open` → 打开页面 → 截图 → 收工。

