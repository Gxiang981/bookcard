# 许可与出处（license-v1）

> 起草：2026-11-08（/ 讨论延伸）｜状态：**已拍板**（同日两次：先「分级闸门」，后改为**「和书一样处理」**）
> 配套实现：`src/bookcard/pipeline/license_gate.py`（许可与出处判定）、`src/bookcard/pipeline/arxiv.py`（抓取）、`src/bookcard/pipeline/pack.py`（公开版裁剪与照抄检查）。

## 0. 一句话

arXiv 论文可以作为 BookCard 的新来源（论文源），**处理方式和书完全一样**：
公开包只放**词汇 + LLM 解释 + 出处**（标题 / 章节 / 页码），**不带原文逐字内容、不带图**；
内部分享版（私有包）带截图。论文许可**只记录、不裁剪**——出处四要素里写清许可名与地址。

## 1. 适用范围

- 论文源（`book.type == "paper"`）与书本源**同规则**（契约 §7.1）。书本来源行为一字不变（§7 已冻结）。
- **公开包**（`--edition public`）：卡正面=词汇，背面=解释的第一句；`source` 只留
  `page / printed_page / bbox / verbatim / method`（**没有** evidence / context）；没有页面图；
  词库名=论文标题，library=论文章节结构。
- **私有包 / 内部分享版**（`--edition private`）：词 + 解释全文 + 页面图 + 高亮框 + evidence + context。
- manifest 顶层记 `source_license` / `attribution` / `policy`（论文源才写）——**记录用**，不参与裁剪。


## 2. arXiv 的许可格局

| kind | URI | 原文作者给的权利 |
|---|---|---|
| `cc-by-4.0` | http://creativecommons.org/licenses/by/4.0/ | 任何用途，可改编、可商用，须署名 |
| `cc-by-sa-4.0` | http://creativecommons.org/licenses/by-sa/4.0/ | 同上，衍生物须同协议 |
| `cc-by-nc-sa-4.0` | http://creativecommons.org/licenses/by-nc-sa/4.0/ | 仅非商业，衍生物须同协议 |
| `cc-by-nc-nd-4.0` | http://creativecommons.org/licenses/by-nc-nd/4.0/ | 仅非商业，禁止改编 |
| `cc0-1.0` | http://creativecommons.org/publicdomain/zero/1.0/ | 放弃版权，无条件 |
| `arxiv-nonexclusive-1.0` | http://arxiv.org/licenses/nonexclusive-distrib/1.0/ | **只给 arXiv 分发权**，第三方再分发/改编要找作者 |
| `assumed-1991-2003` | http://arxiv.org/licenses/assumed-1991-2003/ | 同上（2004 年前的文章） |
| `unknown` | —— | 按最严处理 |

官方依据：

- [arXiv License Information](https://info.arxiv.org/help/license/index.html)——可选许可清单、每个版本不可更改；
- [Reuse Requests FAQ](https://info.arxiv.org/help/license/reuse.html)——"arXiv is not the copyright holder on any of the e-prints in our corpus"；默认许可下要公开再分发得联系作者；
- [非独占许可 1.0 原文](https://arxiv.org/licenses/nonexclusive-distrib/1.0/)、[1991–2003 assumed license](https://arxiv.org/licenses/assumed-1991-2003/)。

要点：

1. **所有 e-print 都受版权保护**，arXiv 只是托管方。默认许可（绝大多数文章）只允许 arXiv 自己分发。
2. 每篇文章的 abs 页（`https://arxiv.org/abs/<id>`）Download 下面那行写明它自己的许可；**每个版本的许可不可更改**，
   v1 和 v2 可能不同 → 出处和 `book.json` 必须带版本号。
3. arXiv 的元数据一律 CC0——跟正文权利无关。
4. 论文里的**图是独立作品**，许可归图的作者，且常常还嵌着第三方素材；论文的许可不替图兜底。

## 3. 怎么读许可（机器可读）

许可链接在 abs 页 `div.abs-license` 的 `<a href>` 里：

- `creativecommons.org/licenses/by/4.0/` ⇒ `cc-by-4.0`
- `creativecommons.org/licenses/by-sa/4.0/` ⇒ `cc-by-sa-4.0`
- `creativecommons.org/licenses/by-nc-sa/4.0/` ⇒ `cc-by-nc-sa-4.0`
- `creativecommons.org/licenses/by-nc-nd/4.0/` ⇒ `cc-by-nc-nd-4.0`
- `creativecommons.org/publicdomain/zero/1.0/` ⇒ `cc0-1.0`
- `arxiv.org/licenses/nonexclusive-distrib/1.0/` ⇒ `arxiv-nonexclusive-1.0`
- `arxiv.org/licenses/assumed-1991-2003/` ⇒ `assumed-1991-2003`
- 其它 / 没找到 ⇒ `unknown`（按最严处理）

arXiv 的 Atom API 目前**不返回**许可字段（官方说许可在 OAI-PMH 输出里）→ 工程上以 abs 页为准。

## 4. 公开包能带什么（论文源 = 书源）

公开包与书源**完全同规则**：

| 档位 | 词汇 | LLM 解释 | 出处（标题/章节/页码/bbox） | 原文逐字内容（evidence / context） | 页面图 |
|---|---|---|---|---|---|
| 公开包 | ✅ | ✅（只留第一句） | ✅ | ❌ | ❌ |
| 内部分享版（私有包） | ✅ | ✅（全文） | ✅ | ✅ | ✅ |

**许可不参与这张表**：因为公开包里没有任何原文逐字内容（没有句子、没有段落、没有图），
剩下的只是「词汇 + 自己写的解释 + 出处」——这是引用与合理使用，不受原文许可限制。
许可仍然要**记录**（出处四要素里写清许可名与地址；社区层按 `nc` / 默认许可给提示；作者要知道这篇能不能商用）。

规则：

1. **页面图在公开包里一律禁止**（§7 第 1 条）。
2. **出处四要素必须齐全**：作者、标题、arXiv ID+版本、链接、许可名与地址（缺项不编，缺作者写 "Unknown authors"）。
3. **公开包的解释不得大段照抄原文**——否则等于把原文塞回公开包，绕过了上面的判断。
   判据：整段解释与**该卡所在页原文**的最长公共子串 ≥ 60 字符（纯拉丁文）或 ≥ 30 字符（含中文）
   ⇒ 判为照抄，**该卡不进公开包**，计数并报出来。查不到该页原文时**不误杀**（跳过检查）。
   （论文源先做；书本源要不要一起做，另拍板。）
4. **结论复述卡**（把论文某个结论用自己的话复述成一张卡）**可以进公开包**——著作权保护表达、不保护思想与事实，
   出处写清即可；前提是第 3 条：不能大段照抄。学术层面仍要标明出自哪篇哪节。
5. NC 类包要在 manifest 里标 `noncommercial`，社区层（P3/P4）发布时按非商业约束处理。


## 5. 出处四要素与模板

```
{作者}. "{标题}". arXiv:{arxiv_id} ({更新日期}). https://arxiv.org/abs/{arxiv_id} — License: {kind} ({uri})
```

例：

```
Ashish Vaswani, Noam Shazeer, et al. "Attention Is All You Need". arXiv:1706.03762v7 (2023-08-02).
https://arxiv.org/abs/1706.03762v7 — License: cc-by-4.0 (http://creativecommons.org/licenses/by/4.0/)
```

引擎字段：

- `book.json`：`attribution`（出处文本）、`paper_license`（kind）、`paper_license_uri`；
- manifest：`attribution`（同文本）、`source_license`（kind/uri/nc/nd/sa/permissive/noncommercial）、
  `policy`（记录性：许可种类、nc/nd/sa、非商业标记、照抄剔除数、术语卡/结论卡计数）。

## 6. 法律口径（诚实说明，别把话说满）

- **著作权保护表达，不保护思想、事实、方法。** 自己写的解释、改写的结论，一般不构成侵权。
  真正受保护的是：原文的具体措辞、图表、版面。
- 硬线在三处：**逐字搬原文的篇幅**、**图**、**再分发整篇**。本项目的对策就是：公开包不带这三样，
  只带词汇、自己写的解释和出处。
- 《中华人民共和国著作权法》（2020 修订）第二十四条：合理使用，含"为个人学习、研究或者欣赏，使用他人已经发表的作品"
  和"为介绍、评论某一作品或者在说明某一问题时，在作品中适当引用他人已经发表的作品"；条件是注明作者姓名或名称、
  作品名称，比例适当，不影响作品正常使用，也不得不正当地损害著作权人合法权益。
- **学术诚信比版权更常被追责**：引别人的结论要标明出自哪篇哪节；不标明是学术不端。
  所以出处四要素必须齐全——这既是版权上的"适当引用"条件，也是学术上的基本规矩。
- 本文件不是法律意见。批量生产或要商业化时，找律师看一遍。

## 7. 落到卡包格式（只加字段，不改已有字段）

| 位置 | 新增字段 | 说明 |
|---|---|---|
| `book.json` | `type` | `"paper"`（书缺省即 book） |
| `book.json` | `arxiv_id` / `arxiv_base_id` / `arxiv_version` / `arxiv_published` / `arxiv_updated` | 带版本号；版本不同许可可能不同 |
| `book.json` | `doi` / `journal_ref` / `categories` / `source_url` | 认不出留空，不编 |
| `book.json` | `paper_license` / `paper_license_uri` | 许可 kind 与 URI |
| `book.json` | `attribution` | 出处文本（模板见 §5） |
| `book.json` | `fingerprint` | 论文用 `{"pages": N, "pdf_sha256": "…"}`（书的页文本 hash 指纹不适用） |
| manifest | `source_license` | 许可信息快照（只对论文源写，记录用） |
| manifest | `attribution` | 出处文本（只对论文源写） |
| manifest | `policy` | 记录性：许可种类 / nc / nd / sa / 非商业标记 / 照抄剔除数 / 术语卡·结论卡计数（只对论文源写） |
| `cards.jsonl` | `share` | `"term"` / `"summary"`，**只用于统计**，不参与打包裁剪，不进 manifest 的卡 |

## 8. 引擎落点

| 模块 | 职责 |
|---|---|
| `src/bookcard/pipeline/license_gate.py` | 许可种类与 URI 映射、许可旗标（nc/nd/sa/permissive）、出处四要素判定、照抄判定（最长公共子串）、卡片性质统计 |
| `src/bookcard/pipeline/arxiv.py` | 抓 abs 页元数据与许可、下载 PDF、写 `book.json` 的论文字段 |
| `src/bookcard/pipeline/paper_sections.py` | 从正文认论文章节 → `toc.json` + `sections.json`（闸门仍只出 pending） |
| `src/bookcard/pipeline/pack.py` | 公开版按「与书同规则」裁剪（剥 evidence / context、无图）+ 论文源的照抄检查；写 `source_license` / `attribution` / `policy` |

### 论文源怎么走（和书源一样的四步）

```bash
bookcard arxiv fetch <arxiv_id> --out DIR --download   # 元数据 + 许可 + PDF，写 book.json
bookcard text DIR/book.pdf --out DIR                   # 抽文字：pages.jsonl（论文字段保住）
bookcard paper sections DIR                            # 认章节：toc.json + sections.json（gate=pending，等拍板）
# 之后与书源完全一样：toc_gate 批准 → 抽卡 → select/locate/audit → pack
```

CLI 清单：

```
bookcard arxiv fetch <arxiv_id> [--out DIR] [--pdf PATH] [--download] [--json]
bookcard paper sections <dir>
```

## 9. 未决 / 后续

1. **抽卡环节对论文的适配**：`extract` 的提示词是照书写的（词 + 解释 + 依据句），论文能不能直接用、
   要不要改提示词（改了会动 `prompt_ver` 和训练链路），要先在真论文上试一轮。
2. **抽取端要输出 `share`**（term / summary）：只用于统计与将来可能的社区筛选，不影响公开包内容；
   改抽取提示词会动 `prompt_ver` 和训练链路，单独开单（task-3）。
3. **社区层的 NC 约束**：NC 类包在 P3/P4 发布时要校验"非商业"。
4. **论文的「自带书」对齐**：论文的指纹现在是书源那套（`page_text_sha1`），对齐逻辑与书一致；
   PDF 的 `pdf_sha256` 也记在 `fingerprint` 里，可用于认同一份 PDF。
5. **训练数据另算**：训练不向公众传播原文，风险模型不同；不要和 `license.source` 共用一个字段。
6. **书本要不要也做照抄检查**：本文件先只管论文源；书源保持现状（§7 已冻结），要一起做另拍板。

## 10. 参考

- arXiv 许可页：<https://info.arxiv.org/help/license/index.html>
- arXiv 复用 FAQ：<https://info.arxiv.org/help/license/reuse.html>
- 卡包契约：[docs/contract-v1.md](contract-v1.md) §7 / §7.1
- 版权边界：[README.md](../README.md) §6
