# Codex 接入：一页

出处（2026-11-06 的当前文档，写之前核一遍）：

- MCP：<https://developers.openai.com/codex/mcp>（配置参考：<https://developers.openai.com/codex/config-reference>）
- skill：<https://developers.openai.com/codex/skills>（通用格式：<https://agentskills.io/specification>）

## 1. 装 bookcard

```bash
uv tool install --from /path/to/46_BookCard bookcard
command -v bookcard          # 记下绝对路径
```

## 2. 注册 MCP

写进 `~/.codex/config.toml`（项目级的放 `.codex/config.toml`，项目要**信任**了才加载）：

```toml
[mcp_servers.bookcard]
command = "~/.local/bin/bookcard"
args = ["mcp"]
```

或者用命令行（会写进 `~/.codex/config.toml`）：

```bash
codex mcp add bookcard -- ~/.local/bin/bookcard mcp
codex mcp list               # 看有没有起来
codex mcp get bookcard       # 看细节
```

- stdio 服务器用 `command` + `args`；远程的用 `url`。
- `command` 写**绝对路径**，别指望 PATH（`spawn npx ENOENT` 就是这个坑）。
- 工具名前缀是 `mcp__bookcard__<工具名>`。
- 冷启动慢就把 `startup_timeout_sec` 调大（默认 10 秒）。

## 3. 装 skill

| 位置 | 路径 | 作用范围 |
|---|---|---|
| 个人 | `~/.agents/skills/bookcard/SKILL.md` | 这台机器上的所有项目 |
| 项目 | `.agents/skills/bookcard/SKILL.md` | 提交进仓库，团队共用 |

```bash
mkdir -p ~/.agents/skills
ln -sfn "$PWD/skills/bookcard" ~/.agents/skills/bookcard
```

- `~/.agents/skills/` 是各家共用的目录（DSH 也扫这个），一份 skill 两家用。
- 只认一层：`<根>/<skill 名>/SKILL.md`；目录名要和 frontmatter 的 `name` 一致
  （都是 `bookcard`），`name` 用小写字母 + 连字符。
- frontmatter 要有 `name` 和 `description`；Codex 额外支持 `agents/openai.yaml`
  （调用策略、MCP 依赖声明），bookcard 不需要。
- `$bookcard` 显式调用；描述对得上时 Codex 会自己隐式调用。

## 4. 跑 bookcard 的两条纪律（实跑踩出来的）

1. **只用 MCP 工具，不许 import 引擎代码。**
   抽卡、审卡、打包一律走 `mcp__bookcard__*`；不许 `import bookcard.…` 去调引擎内部函数
   做预校验，也不许直接读写 `$BOOKCARD_HOME` 下书的工作目录自己算状态——
   闸门、判据、计数都在引擎里。要看进度调 `book_status`，要列书调 `book_list`。

2. **浏览服务跟着 MCP 进程走，截图必须在同一个 run 里做完。**
   `browse_open` 起的服务是 MCP 进程里的一个线程：这一轮结束、MCP 一退，服务就没了，
   URL 也不再能访问。要截图、要留证据，就在**同一个 run** 里
   `browse_open` → 打开页面 → 截图 → 收工。

