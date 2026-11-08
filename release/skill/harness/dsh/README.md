# DSH（DeepSeek Harness）接入：一页

出处（本文档按 2026-11-06 的当前文档写，抄之前先核一遍）：

- MCP 客户端插件：`@deepseek-ai/dsh-mcp-client` 的 README（在 app 包里）
  `npx @electron/asar@3.2.17 extract-file "/Applications/DeepSeek Harness.app/Contents/Resources/app.asar" dsh/node_modules/@deepseek-ai/dsh-mcp-client/README.md`
- skill 提供方：`@deepseek-ai/dsh-skill-filesystem` 的 README（同上，路径换成 `dsh-skill-filesystem`）
- headless 运行器：`@deepseek-ai/dsh-headless` 的 README（同上，路径换成 `dsh-headless`）

## 1. 装 bookcard

```bash
uv tool install --from /path/to/46_BookCard bookcard
command -v bookcard          # 记下这个绝对路径，overlay 里要用
```

## 2. 注册 MCP

在 profile 的 `cordis.patch.yml`（或 `--patch` 的 overlay）里加一条：

```yaml
- insert:
    - id: mcp-bookcard
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: bookcard
        transport: stdio
        command: ~/.local/bin/bookcard   # 绝对路径
        args: [mcp]
        failOnStartupError: false
        toolCallTimeoutMs: 600000
```

- `serverName` 决定工具名前缀：模型看到的是 `mcp__bookcard__<工具名>`（`mcp__bookcard__book_add`）。
- stdio 传输会把环境变量里带 `KEY` / `PASSWORD` / `SECRET` / `TOKEN` 的**全部抹掉**，
  `DSH_*` 也抹掉；`BOOKCARD_HOME` 不受影响，bookcard 不需要任何 key。
- `failOnStartupError: false`：服务起不来时 harness 照常起，只是没有 bookcard 的工具。

本目录的 `bookcard.patch.yml` 是**完整可跑的 overlay**：模型（`MiniMax-M3.1-Flash`）+
提供方（`minimax-cn`）+ MCP 三样，不含 notify 插件和 UI 条目。

```bash
MINIMAX_CN_API_KEY="$MINIMAX_API_KEY" \
  "/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh" \
  --profile headless --patch skills/bookcard/harness/dsh/bookcard.patch.yml --json \
  "列出你能用的 mcp__bookcard__ 工具名，然后调用 book_list"
```

key 只走环境变量（`apiKeyEnv: MINIMAX_CN_API_KEY`），**不写进任何文件**。

## 3. 装 skill

DSH 扫的 skill 根（`@deepseek-ai/dsh-skill-filesystem` 的 README 里的 Roots 表）：

| 优先级 | 来源 | 路径 |
|---|---|---|
| 100 | project-dsh | `<项目>/.dsh/skills/` |
| 200 | project-agents | `<项目>/.agents/skills/` |
| 400 | user-dsh | `~/.dsh/skills/` |
| 500 | user-agents | `~/.agents/skills/` |

只认一层：`~/.dsh/skills/<name>/SKILL.md` 或 `~/.dsh/skills/<name>.md`；
嵌套的 `**/SKILL.md` **不会**被发现。frontmatter 要有 `name` 和 `description`。

```bash
mkdir -p ~/.dsh/skills
ln -sfn "$PWD/skills/bookcard" ~/.dsh/skills/bookcard
```

（`~/.agents/skills/` 也行——Codex 扫同一个目录，一份 skill 两家用。）

## 4. 确认接上了

```bash
# 工具在不在
dsh --profile headless --patch skills/bookcard/harness/dsh/bookcard.patch.yml --json \
  "列出你能用的 mcp__bookcard__ 工具名，然后调用 book_list"
# 事件流里的 tool_call / tool_result 那几行就是证据

# skill 被识别没有
dsh --profile headless --patch skills/bookcard/harness/dsh/bookcard.patch.yml --json \
  "你有没有 bookcard 这个 skill？有的话说出它的 description 和第一步该做什么"
```

## 5. DSH 上跑 bookcard 的三条纪律（实跑踩出来的）

1. **只用 MCP 工具，不许 import 引擎代码。**
   抽卡、审卡、打包一律走 `mcp__bookcard__*`。**不许**在 agent 侧 `import bookcard.…`
   去调引擎内部函数做预校验，也**不许**直接读写 `$BOOKCARD_HOME` 下书的工作目录自己算状态——
   闸门、判据、计数都在引擎里，自己算的数跟引擎对不上。要看进度调 `book_status`，
   要列书调 `book_list`。

2. **浏览服务跟着 MCP 进程走，截图必须在同一个 run 里做完。**
   `browse_open` 起的服务是 MCP 进程里的一个线程：DSH 这一轮结束、MCP 一退，服务就没了，
   URL 也不再能访问。要截图、要留证据，就在**同一个 run** 里
   `browse_open` → 打开页面 → 截图 → 收工。

3. **DSH 沙箱里起有头 Chromium 会失败（已知限制，只记录不修）。**
   沙箱的临时目录建不出来（`/var/folders/…/T` mkdir EPERM），有头浏览器起不来。
   要截图走 **CDP**（连已经开着的 Chromium 调试端口）或**无头模式**。
   这是环境限制不是引擎的 bug：绕过去，别在流程里死磕。

