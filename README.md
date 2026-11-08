# BookCard — 从书里出学习卡片

把一本书变成一套**可学、可溯源、可分享**的学习卡片 ✓。

```
一本 PDF  →  抽关键词和解释  →  一个 .ttfc.zip 卡包  →  导入 Flashcard App
```

## 0. 它是什么

| 层 | 是什么 |
|---|---|
| **抽取引擎**（`src/bookcard/`） | 确定性的活：探针取字、目录识别、切章节、抽词出题、定位高亮、打包、浏览页 ✓ |
| **卡包格式**（`.ttfc.zip`） | 一个 zip：根目录 `manifest.json` + `images/` ✓ —— App 认的格式 ✓ |
| **skill**（`release/skill/`） | 一份 `SKILL.md` ✓ —— 告诉 agent 这本书该怎么变成卡片 ✓ |

**引擎不调任何模型 ✓** —— 抽词和解释由**你自己的 agent**作答 ✓，
引擎只做确定性的事 ✓（取字、目录、切章节、定位、校验、打包）✓。

## 1. 装

```bash
# 引擎（Python 包，装完就有 bookcard 命令和 MCP 服务端）
uv tool install "bookcard[ocr] @ git+https://github.com/Gxiang981/bookcard.git"

# skill（拷进 harness 的 skills 目录）
cp -R release/skill/. ~/.dsh/skills/bookcard/
```

四家 harness（DSH / Claude Code / Codex / Devin）的完整接法见
[`release/skill/INSTALL.md`](release/skill/INSTALL.md) ✓。

## 2. 怎么用

对着 agent 说「把这本书做成学习卡片」✓，然后它走这条流程 ✓：

```
book_add          探针 + 取字（扫描件自动 OCR）
toc_review        把目录摊开给你核对（标题和页码逐条对）
toc_gate          目录闸门拍板（带 level = 用目录的第几层）
sections_build    按那一层切章节（一个 section = App 的一个词库）
extract_next      出抽词题 → agent 作答 → extract_submit（逐段，直到抽完）
cards_build       挑卡 + 定位（词在书页上的位置）+ 质检
pack              打包成 .ttfc.zip
browse_open       导进本地浏览库并起浏览服务
```

- **只有一道闸门** ✓（目录确认）✓ —— 抽词本身没有第二道闸门 ✓，
  引擎把关「词必须真的出现在原文里」✓
- **最终产出落在系统「下载」文件夹** ✓（mac `~/Downloads` ✓，
  win `%USERPROFILE%\Downloads` ✓），文件名 `<书名>-<fp12>.ttfc.zip` ✓
- 想换位置：`BOOKCARD_OUTPUT_DIR` ✓

## 3. 卡包里有什么

- **卡** ✓：正面是词 ✓，背面是解释 ✓（15–60 字 ✓）+ 依据句 ✓（原文一字不差 ✓）
- **卡上的标签** ✓：书的元信息标签 ✓（领域 ✓ + 水平 ✓ + 书上自带的标签 ✓）
- **难度** ✓：由「学习价值」映射 ✓ —— 不是手填的 ✓
- **书页图 + 高亮框** ✓：私有包带整页图 ✓ 和每个词在页上的位置 ✓（`bbox` ✓）
  —— 高亮框按字号和基线收紧 ✓（直接用 PDF 的 em 框会盖住上下两行 ✓）
- **公开版**（`edition=public`）**不许带书页图** ✓，必须带书指纹 ✓

## 4. 目录

| 目录 | 是什么 |
|---|---|
| `src/bookcard/` | 引擎 ✓ |
| `skills/bookcard/` | skill 的真源 ✓（`release/skill/` 是生成物 ✓） |
| `release/skill/` | **发布用**的 skill ✓（SKILL.md + INSTALL.md + 各 harness 接法 ✓） |
| `docs/` | 规格与契约 ✓（卡包格式 ✓ / MCP ✓ / 流程 ✓ / 许可 ✓ / 基准 ✓） |
| `tests/` | 测试 ✓ |
| `bench/` | 目录识别的基准与实测 ✓ |
| `bookcard-share/` | 静态分享页（一个 HTML ✓，自带浏览和编辑 ✓） |

## 5. 版权提醒

**书页图是受版权保护的内容** ✓ —— 把整页扫描图**公开分发风险最高** ✓。
私有包（`edition=private` ✓）自用没问题 ✓；要公开分发就用 `edition=public` ✓
（引擎会强校验 ✓：包里有任何图片都直接失败 ✓）。

抽出来的关键词和解释是**你自己书的衍生物** ✓ —— 分发前自己判断 ✓。

## 6. 开发

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/ruff check .
```

运行时依赖只有 `pymupdf` ✓；`[ocr]` 额外带 OCR 依赖 ✓。
引擎**不内置任何模型 key** ✓ —— 不写进任何文件、不打进日志 ✓。

## 7. 许可与出处

- 引擎与 skill：本仓库 ✓
- 卡包里的词和解释：由使用者自己生成 ✓
- 书页图：**版权归原出版方** ✓ —— 见 §5 ✓
