# bookcard-share —— 分享包（能看能改，也能导入 App）

一本书 → 一个 zip。里面就几样东西：

```
<fp12>-share.zip
├── manifest.json      App 的导入格式（BulkImportBody 形状）+ 目录
├── index.html         自包含的查看/编辑页，双击就开，零依赖
├── images/*.webp      全书每一页的大图
└── thumbs/*.webp      全书每一页的缩略图
```

**同一个 zip**：解压 → 双击 `index.html` → 看、改、翻原书；原封不动拖进 Flashcard App → 导入。
图片只有一份，HTML 和 App 用的是同一批字节。

这是 `46_BookCard` 里的一个**独立子项目**：不动引擎现有代码，也不动 `00_flashcard`。

---

## 1. 两种界面

点左栏最上面那一级（书）→ **读原书**：

```
┌───────────────────────────────────────────────────────────────────┐
│ ┌───────────────────────────────┐ ┌─────────────────────────────┐ │
│ │  大图：当前页（点击可全屏放大）   │ │ 目录（可直接编辑）             │ │
│ │  p.12 / 239   印刷页 p.8        │ │  offset [-4]  ＋             │ │
│ │                  ‹  12  ›      │ │  1 第一章…        p.9   ×   │ │
│ │                                │ │  2 1.1 小节…      p.14  ×   │ │
│ └───────────────────────────────┘ └─────────────────────────────┘ │
│ ┌───────────────────────────────────────────────────────────────┐ │
│ │ ‹  [缩][缩][缩][缩][缩][缩][缩][缩]…  ›      跳到 p. [ 12 ] Go │ │
│ └───────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────┘
```

- **底部**：全书每一页的缩略图（239 页），列数随宽度自适应，‹ › 翻一屏，右下角跳页码
- **左上**：选中页的大图（150dpi），点一下全屏放大；‹ › 切页
- **右上**：TOC 抽取出来的目录，**标题 / 页码 / offset 都能直接改**，改动落 localStorage、跟着 `edits.json` 走

点左栏的词库 → **卡片**（原来的界面）：翻面、四键评分、编辑 front/back/tags/换词库。

两个入口都一步可达：左栏词库那级的第一行「📖 读原书 · 全书页面」，或点顶栏的书名。

## 2. 为什么长这样

- **真源只有一份**：`manifest.json`。HTML 里内嵌的就是它，App 导入的也是它。
  所以「在预览里看到的」＝「导入后看到的」。
- **图片存 webp**：App 的 `to_webp` 是**幂等**的（已是 webp 就原样返回，
  `00_flashcard/server-python/app/services/image_transcode.py:157-159`）。
  引擎侧直接出 webp ⇒ App 导入时**零转码**，落盘字节 == 包内字节，
  sha256 一致，没有代际损失。
- **质量对齐 App**：`IMPORT_QUALITY = 95`（同一个文件 `:79`）。
  顺带一提，webp q95 比引擎原来那套 jpeg q85 **更小**（150dpi 扫描页
  94KB vs 113KB），换过去包不会变大。
- **HTML 零依赖**：CSS/JS/数据全部内联，没有 CDN、没有外链字体、没有 fetch。
  书页图走包内相对路径，`file://` 下双击就开。
- **全书每一页都带**：包里原本只有「有卡的页」（这本 199 / 全书 239）。分享包
  从 PDF 补渲了缺的那些页，底部页条才有全书可翻。
  实测重渲染出来的字节与包里已有的图**逐字节一致** ⇒ App 侧算出的 sha 不变。

## 3. 怎么用

```bash
cd bookcard-share
pip install -r requirements.txt          # 只需要 Pillow（编 webp 用）

# 建分享包
python3 build.py build <pack.ttfc.zip> [-o OUT_DIR]

# 把静态页导出的改动合回卡包，重建分享包
python3 build.py apply --edits edits.json --pack <pack.ttfc.zip> [-o OUT_DIR]
```

| 参数 | 作用 |
|---|---|
| `--pdf PATH` | 书的 PDF。给了就把**全书每一页**都渲染出来（大图+缩略图） |
| `--work-dir DIR` | 引擎的书工作目录。给了才带目录（`toc.json`）和印刷页 offset |
| `--zip PATH` / `--no-zip` | zip 输出路径 / 不打 zip |
| `--quality N` | webp 质量（默认 95，对齐 App 导入档） |

`--pdf` 和 `--work-dir` **不传也能跑**，只是没有全书页图、没有目录；
按卡包路径能自动猜到工作目录（`<work>/pack/x.ttfc.zip` → `<work>`）和
PDF（`train/data/src/<file_name>.pdf`）。

样例（image 场景那本）：

```bash
python3 build.py build \
    ../train/data/bookcard/books/94a9b8d6b27d/pack/94a9b8d6b27d.ttfc.zip \
    -o out/94a9b8d6b27d-share
```

```
书名    : 义务教育教科书 物理 八年级 全一册
词库    : 68 个
卡片    : 704 张
页面图  : 239 页（大图+缩略图）
目录    : 151 条
图片体积: 40.6 MB
静态页  : 493 KB（自包含，双击即开）
分享包  : out/94a9b8d6b27d-share.zip（40.9 MB）
```

## 4. 静态页能干什么

界面和业务逻辑是从引擎的 browse-lite 页面**整份抄过来**的
（`src/bookcard/browse/static/index.html`），只换了数据层：

| 能力 | 说明 |
|---|---|
| 读原书 | 全书页图 + 缩略图条 + 翻页 + 跳页码 + 全屏放大 |
| 目录编辑 | 标题 / 页号 / offset 都能改；`印刷页 = PDF 页 + offset` |
| 翻卡 / 前后翻 | 点击或空格翻面，← → 翻页，触屏横滑 |
| 场景 | 图片场景：书页图 + 关键词高亮框（点图放大）；文本场景：原文块 + 高亮 |
| 四键评分 | Unclear / Forgot / Good / Easy（键 1–4），走 SM-2 排程 |
| 层级过滤 | 顶部 tier 药丸（Due / New / Learned / Ignored / Mastered） |
| 忽略 | ⋯ 菜单 → Ignore |
| **编辑** | ⋯ 菜单 → Edit：Front / Back / Tags / 换词库 |
| 进度持久化 | localStorage（按书分键），刷新不丢 |
| **导出 / 导入** | 顶栏 ⤒ / ⤓：导出 `edits.json`，导入别人的 `edits.json` |

`edits.json` 的内容：

```json
{
  "format": "bookcard-share/edits@1",
  "book": {"id": "94a9b8d6b27d", "title": "…"},
  "cards":  [{"id": "…", "front": "…", "back": "…", "tags": [], "library_id": "…"}],
  "state":  {"<card_id>": {"repetition": 1, "ease": 1.9, "interval_days": 1, "…": "…"}},
  "review_log": [{"card_id": "…", "rating": "good", "sm2": "good", "reviewed_at": 0, "interval_days": 1}],
  "toc": {"offset": -4, "entries": [{"level": 1, "title": "…", "page": 9}]}
}
```

## 5. 闭环：改完怎么进 App

```
卡包 → build → 分享包.zip
                    ├─ 解压 → index.html → 用户改（卡片 + 目录）→ 导出 edits.json
                    │                                  ↓
                    │                          build.py apply → 新分享包.zip
                    └─ 拖进 App → 导入 ✓                ↓
                                                    拖进 App → 导入 ✓
```

`apply` 合回的口径跟 App 改卡一致：只改 `front` / `back` / `tags` / `library_id`，
**不动** `source`（页码、bbox、依据句）——改词不改它在书上的位置。
目录是**整段替换**（不是合并）。改了 `front` 的卡会被钉一个 `card_id`，
让 id 在下一轮里保持不变（id 的推导规则见 `src/bookcard/browse/db.py:513-523`；
App 不认这个字段，不影响导入）。

## 6. 实测（2026-11-07）

用 `00_flashcard` 自己的导入端点 `POST /api/v2/imports/bulk-zip` 真跑过：

- `HTTP 200`，`libraries: 68, cards: 704, images: 704, occurrences: 704, image_errors: []`
- 卡背自动追加 `![[<sha256>.webp]]`；`mime_type=image/webp`，宽高 1123×1571
- **包内 `images/p0005.webp` 的 sha256 == App 落盘文件的 blob_hash**（字节完全一致）
- 浏览器里改的 `光的反射【已改】` / `【改过的背面】` 原样进了 App

浏览器侧（Playwright 跑 `file://`，不是 http）：

- **卡片流程** 14 项全通过（渲染、翻面、高亮框、四键评分、编辑、localStorage、导出、窄屏抽屉、零 JS 报错）
- **书视图** 18 项全通过（进书视图、大图、151 条目录、239 个缩略图、点缩略图切页、
  ‹› 翻页、跳页码、缩略图条翻屏、全屏放大、目录改名、offset 改、刷新后目录还在、
  导出带目录、切回词库卡片流程照常）

SM-2 对拍：`tests/check_sm2_parity.py` 把 `assets/page.js` 里那段 JS 抠出来用 node 跑，
跟引擎 `src/bookcard/browse/sm2.py` 逐个输入对比 —— 9 组全一致。

## 7. 已知边界（第一版就这样，不是 bug）

- **文本场景的内容进不了 App**：App 的导入模型没有 scene/context 字段
  （`00_flashcard/server-python/app/routes/imports.py:174-186`），它的
  `local_flashcards.source` 被硬编码成 `'import'`。所以 `source.context`
  （文本块 + 高亮）**只在静态页里看得到**，导入后 App 里没有。
  图片场景没这问题（走 `images[].occurrences`，App 自己画高亮框）。
  要对齐得动 App 的导入路径 —— 详见 [NOTES.md](NOTES.md)。
- **目录改了不重排词库**：目录（TOC）和词库（section）现在是两套东西。
  目录改了会带着走，但**不会**顺带重建 `category.libraries`。
- **进度只在浏览器里**：`file://` 下 Safari 禁用 localStorage，Chrome/Edge/Firefox
  可以。跨机器靠导出/导入 `edits.json`。
- **重导不更新 bbox**：App 的快速通道（`imports.py:748-750`）——卡已有图行且卡背
  已有 sha 命名 wikilink ⇒ 整个图片段跳过。想让 bbox 生效得删分类重导。
- **包内不要自己写图片 wikilink**：非 sha 命名的 `![[p0012.webp]]` 会被
  `_strip_vault_image_refs` 剥掉（`imports.py:318-350`）。引擎不写，App 自己加。
- **图片体积**：整本书的页图都进包（这本 239 页 ≈ 40 MB，含缩略图 2.5 MB）。
  公开包按契约不带书页图。

## 8. 目录

```
bookcard-share/
├── build.py              CLI：build / apply
├── requirements.txt      pillow（编 webp）
├── sharepack/
│   ├── pack.py           读卡包（zip / 目录）
│   ├── pages.py          从 PDF 渲染全书页（大图 + 缩略图）
│   ├── webp.py           图片 → webp（幂等，口径抄 App）
│   ├── toc.py            目录 + 印刷页 offset
│   ├── payload.py        manifest → 静态页内嵌数据（口径抄引擎 db.py）
│   ├── html.py           模板 → 自包含 HTML
│   ├── apply.py          edits.json → 合回 manifest
│   └── build.py          打包
├── assets/
│   ├── template.html     页面骨架
│   ├── page.css          从引擎抄的样式（Playful Dark）+ 书视图的样式
│   ├── page.js           从引擎抄的页面逻辑 + 书视图（去掉了 HTTP/mock 后端）
│   └── local-api.js      本子项目新写：内嵌数据 + localStorage + 导出导入
├── docs/shots/           页面截图
└── tests/
    ├── test_sharepack.py 单测（26 条）
    ├── check_sm2_parity.py  JS ↔ Python SM-2 对拍
    ├── check_page.mjs     卡片流程自检（需 playwright）
    ├── check_bookview.mjs 书视图自检（需 playwright）
    └── shoot.mjs          截图
```

## 9. 自检

```bash
cd bookcard-share
../.venv/bin/python -m pytest -q tests/       # 26 passed
../.venv/bin/python tests/check_sm2_parity.py  # SM-2 对拍
node tests/check_page.mjs out/<fp12>-share/index.html          # 卡片流程
node tests/check_bookview.mjs out/<fp12>-share/index.html      # 书视图
```
