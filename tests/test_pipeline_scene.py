"""§7.1 的测试：``scene_mode`` 判定 + 文本场景的 ``source.context``。

判定口径（契约 §7.1）：

- 探针 verdict 不是 ``scanned/garbled/broken``；
- body 页里 ``text_origin == "pdf_text"`` 的占比 ≥ 0.9；
- body 页图片覆盖率的中位数 < 0.35。

三条全中 ⇒ ``text_eligible`` 为 ``True``（只作参考，the maintainer 2026-11-06 拍板：
处理书一律**默认走图片场景**，不用问用户）。
"""

import json

import pymupdf
import pytest

from bookcard import extract
from bookcard.engine.reflow import reflow_span
from bookcard.pipeline.scene import (
    DEFAULT_SCENE,
    MAX_CONTEXT_LEN,
    SCENE_IMAGE,
    SCENE_TEXT,
    _normalize_with_map,
    build_context,
    build_contexts,
    detect_scene_mode,
)
from bookcard.pipeline.select import _normalize_for_match
from bookcard.pipeline.workdir import WorkDir
from pipeline_fixtures import page_rows, write_workdir

# ===================== 合成 PDF =====================

PAGE_W = 595
PAGE_H = 842
CJK_FONT = "china-s"

#: 够长的正文（探针把少于 10 个字的页判成 empty，判定就变成 scanned 了）。
TEXT_PAGES = (
    "良渚文化位于长江下游，是新石器时代晚期的文化遗存，城址规模很大。",
    "二里头遗址被视为早期国家的都城遗址候选之一，发现了宫殿建筑群。",
    "良渚文化的玉器制作水平很高，反映了当时的手工业发展水平。",
    "红山文化分布在辽河流域，以女神庙和玉器闻名于世。",
)


def make_pdf(path, mode: str) -> str:
    """造三种书：纯文字 / 纯图 / 文字 + 大图。

    **要 4 页且每页文字不同**：探针把「在 ≥30% 的页上出现的同一行」当水印，
    一页一行的书会被整页剥空（判成 watermark_only）。
    """
    doc = pymupdf.open()
    for index in range(4):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        if mode in ("text", "text_image"):
            page.insert_text((72, 100), TEXT_PAGES[index], fontsize=12, fontname=CJK_FONT)
        if mode in ("image", "text_image"):
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 120))
            pixmap.set_rect(pixmap.irect, (200, 30, 30))
            # 铺掉大半页 ⇒ 覆盖率远超 0.35
            page.insert_image(pymupdf.Rect(40, 160, 555, 760), pixmap=pixmap)
    doc.save(str(path))
    doc.close()
    return str(path)


def _workdir(tmp_path, pdf_path: str, *, text_origin: str = "pdf_text"):
    """造一个能判场景的工作目录（页文字随便给，重点是 kind / text_origin）。"""
    rows = page_rows()
    for row in rows:
        row["text_origin"] = text_origin
    work = write_workdir(tmp_path / "book", candidates=[], pages=rows)
    return work


# ===================== scene_mode 判定 =====================


def test_default_scene_is_image():
    """默认场景是书页图（the maintainer 2026-11-06 拍板）。"""
    assert DEFAULT_SCENE == SCENE_IMAGE


def test_text_pdf_defaults_to_image_but_is_text_eligible(tmp_path):
    """纯文字书：默认仍是图片场景，但三条判据全中 ⇒ ``text_eligible``。"""
    pdf = make_pdf(tmp_path / "text.pdf", "text")
    work = _workdir(tmp_path, pdf)

    result = detect_scene_mode(work, pdf)

    assert result["mode"] == SCENE_IMAGE
    assert result["text_eligible"] is True
    assert result["verdict"] == "digital"
    assert result["text_origin_ratio"] == 1.0
    assert result["median_image_coverage"] == 0.0
    assert result["reasons"]
    # 第一条永远是「为什么默认走图片场景」。
    assert "默认走图片场景" in result["reasons"][0]
    assert "三条判据全中" in result["reasons"][1]


def test_scan_pdf_is_not_text_eligible(tmp_path):
    """纯图（扫描件）⇒ 图片场景，且 ``text_eligible`` 为 False。"""
    pdf = make_pdf(tmp_path / "scan.pdf", "image")
    work = _workdir(tmp_path, pdf)

    result = detect_scene_mode(work, pdf)

    assert result["mode"] == SCENE_IMAGE
    assert result["text_eligible"] is False


def test_text_with_big_image_is_not_text_eligible(tmp_path):
    """文字 + 大图 ⇒ 图片场景（图多的教科书）。"""
    pdf = make_pdf(tmp_path / "mixed.pdf", "text_image")
    work = _workdir(tmp_path, pdf)

    result = detect_scene_mode(work, pdf)

    assert result["mode"] == SCENE_IMAGE
    assert result["text_eligible"] is False
    assert result["median_image_coverage"] >= 0.35


def test_low_text_origin_ratio_is_not_text_eligible(tmp_path):
    """body 页不能直接取字的占比过高 ⇒ 图片场景。"""
    pdf = make_pdf(tmp_path / "text.pdf", "text")
    work = _workdir(tmp_path, pdf, text_origin="ocr")

    result = detect_scene_mode(work, pdf)

    assert result["mode"] == SCENE_IMAGE
    assert result["text_eligible"] is False
    assert result["text_origin_ratio"] == 0.0


def test_missing_pdf_is_not_text_eligible(tmp_path):
    """找不到 PDF ⇒ 核不了覆盖率 ⇒ 保守，``text_eligible`` 为 False。"""
    work = write_workdir(tmp_path / "book", candidates=[], pages=page_rows())

    result = detect_scene_mode(work, str(tmp_path / "根本没有这本.pdf"))

    assert result["mode"] == SCENE_IMAGE
    assert result["text_eligible"] is False
    assert result["pdf"] is None


# ===================== source.context =====================

PAGE_WITH_PARAGRAPHS = (
    "第一段说些别的。\n"
    "\n"
    "第二段里出现了良渚文化这个词，它是新石器时代晚期的文化遗存，"
    "城址和水利系统展现了早期国家的形态。\n"
    "\n"
    "第三段还是别的。"
)


def test_context_takes_the_paragraph_of_the_evidence():
    """context = 含依据句的那个段落。"""
    context = build_context(
        PAGE_WITH_PARAGRAPHS,
        "良渚文化",
        "良渚文化这个词，它是新石器时代晚期的文化遗存",
    )

    assert context is not None
    assert context["text"] == (
        "第二段里出现了良渚文化这个词，它是新石器时代晚期的文化遗存，"
        "城址和水利系统展现了早期国家的形态。"
    )
    assert context["highlights"] == [{"start": 7, "end": 11}]


def test_context_text_is_verbatim_substring_of_the_page():
    """context 必须是 pages.jsonl 原文的逐字子串。"""
    context = build_context(
        PAGE_WITH_PARAGRAPHS,
        "良渚文化",
        "良渚文化这个词，它是新石器时代晚期的文化遗存",
    )

    assert context is not None
    assert context["text"] in PAGE_WITH_PARAGRAPHS


def test_context_highlights_every_occurrence():
    """highlights = front 在 context 里**每一处**出现的偏移。"""
    page = "良渚文化的城址和水利系统展现了早期国家形态，而良渚文化的玉器也很有名。"
    context = build_context(page, "良渚文化", "良渚文化的城址和水利系统展现了早期国家形态")

    assert context is not None
    assert len(context["highlights"]) == 2
    for hit in context["highlights"]:
        assert page[hit["start"] : hit["end"]] == "良渚文化"


def test_context_truncates_long_paragraph_at_sentence_boundary():
    """超 400 字 ⇒ 以依据句为中心截，在句界处截，截掉的一侧加 …。"""
    filler = "".join(f"这是第{index}句填充文字。" for index in range(40))
    # 空行分段 ⇒ 依据句那一段很长（超过 400 字），要截。
    page = "\n\n".join(["开头一句。", filler + "良渚文化是核心概念。", "还有一句收尾。"])
    evidence = "良渚文化是核心概念"

    context = build_context(page, "良渚文化", evidence)

    assert context is not None
    assert len(context["text"]) <= MAX_CONTEXT_LEN
    assert "…" in context["text"]
    assert evidence in context["text"]
    # 去掉 … 之后必须还是页面原文的逐字子串
    body = context["text"].replace("…", "")
    assert body in page
    # 右边截在句界上
    assert body.endswith(("。", "！", "？", ".", "!", "?"))


def test_context_without_blank_lines_uses_neighbouring_sentences():
    """整页没有空行 ⇒ 用依据句前后各一句。"""
    page = "第一句无关。第二句开始。良渚文化是核心概念。第二句结束。最后一句无关。"
    evidence = "良渚文化是核心概念"

    context = build_context(page, "良渚文化", evidence)

    assert context is not None
    assert evidence in context["text"]
    assert "第二句开始" in context["text"]
    assert "第二句结束" in context["text"]
    assert "第一句无关" not in context["text"]
    assert context["text"] in page


def test_context_single_newlines_are_line_wraps_not_paragraphs():
    """教材正文一行一行折行：单个换行不是段界，整页没空行 ⇒ 依据句前后各一句。"""
    page = (
        "这是火山在爆发。\n地球内部有高温、高压的岩浆。在极大的压\n"
        "力下，岩浆会从地壳薄弱的地方喷涌而出。\n火山爆发时很危险。"
    )

    context = build_context(page, "岩浆", "地球内部有高温、高压的岩浆。")

    assert context["text"] == page[: page.index("出。") + 2]
    assert len(context["highlights"]) == 2


def test_context_falls_back_when_the_evidence_paragraph_lacks_the_word():
    """依据句所在的段落里没有这个词 ⇒ 退回含这个词的那一句。"""
    page = "良渚文化出现在第一段。\n\n第二段是依据句，但里面没有那个词。"

    context = build_context(page, "良渚文化", "第二段是依据句，但里面没有那个词")

    assert context is not None
    assert "良渚文化" in context["text"]
    assert context["highlights"]
    hit = context["highlights"][0]
    assert context["text"][hit["start"] : hit["end"]] == "良渚文化"


def test_context_keeps_the_word_when_the_sentence_is_over_the_budget():
    """单句就超 400 字 ⇒ 改为以词为中心硬截，**高亮不能丢**。"""
    page = "开头" + "填" * 500 + "良渚文化很重要。" + "尾巴" * 100

    context = build_context(page, "良渚文化", "良渚文化很重要")

    assert context is not None
    assert len(context["text"]) <= MAX_CONTEXT_LEN
    assert context["highlights"]
    hit = context["highlights"][0]
    assert context["text"][hit["start"] : hit["end"]] == "良渚文化"


def test_context_stays_within_budget_when_both_ends_are_cut():
    """两头都截（两个 …）时也不能超 400 字。"""
    page = "开头" + "填" * 300 + "良渚文化" + "填" * 300 + "结尾。"

    context = build_context(page, "良渚文化", "良渚文化")

    assert context is not None
    assert len(context["text"]) <= MAX_CONTEXT_LEN
    assert context["text"].startswith("…")
    assert context["text"].endswith("…")
    hit = context["highlights"][0]
    assert context["text"][hit["start"] : hit["end"]] == "良渚文化"


def test_context_is_none_when_the_word_is_nowhere():
    """页面文字里根本没有这个词 ⇒ 没有 context。"""
    assert build_context("这一页里什么都没有。", "良渚文化", "这一页里什么都没有。") is None


def test_build_contexts_maps_card_id_to_context(tmp_path):
    """build_contexts 按 card_id 给每张卡挂 context。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[],
        pages=[
            {
                "page": 1,
                "printed_page": "1",
                "text": "良渚文化位于长江下游，是新石器时代晚期的文化遗存。",
                "text_sha1": "x",
                "text_origin": "pdf_text",
                "width": 595.0,
                "height": 842.0,
                "kind": "body",
            }
        ],
    )
    cards = [
        {
            "card_id": "abc",
            "front": "良渚文化",
            "source": {
                "page": 1,
                "evidence": "良渚文化位于长江下游，是新石器时代晚期的文化遗存",
                "start": 0,
                "end": 4,
            },
        }
    ]

    contexts, stats = build_contexts(work, cards)

    assert "abc" in contexts
    assert contexts["abc"]["text"].startswith("良渚文化")
    assert stats["with_context"] == 1
    assert stats["without_context"] == 0


# ===================== 段落界 breaks + 合并（契约 §2.1） =====================

#: 一页正文：前一段是三行折行，第二段另起。
WRAPPED_PAGE = (
    "分子之间既有引力又有斥力。当固体被压缩\n"
    "时，分子间的距离变小，作用力表现为斥力；当\n"
    "固体被拉伸时，作用力表现为引力。\n"
    "另一段开始了。"
)

#: 上面那一页的段落界（第三行末尾那个换行——段落界在段落**最后一行**的末尾）。
WRAPPED_BREAKS = [WRAPPED_PAGE.index("另一段开始了") - 1]


def test_context_merges_the_soft_wraps_inside_a_paragraph():
    """段落里的软折行合并掉（契约 §2.1 + §7.1）。"""
    context = build_context(
        WRAPPED_PAGE,
        "斥力",
        "作用力表现为斥力",
        breaks=WRAPPED_BREAKS,
    )

    assert context is not None
    assert context["text"] == (
        "分子之间既有引力又有斥力。当固体被压缩时，分子间的距离变小，"
        "作用力表现为斥力；当固体被拉伸时，作用力表现为引力。"
    )


def test_highlights_land_on_the_word_in_the_merged_text():
    """合并后 ``highlights`` 仍然落在词上（偏移是合并后文字的偏移）。"""
    context = build_context(
        WRAPPED_PAGE,
        "斥力",
        "作用力表现为斥力",
        breaks=WRAPPED_BREAKS,
    )

    assert context is not None
    assert len(context["highlights"]) == 2
    for hit in context["highlights"]:
        assert context["text"][hit["start"] : hit["end"]] == "斥力"


def test_context_text_equals_the_reflow_of_the_original_span():
    """``context.text`` = 原文对应区间经 ``reflow`` 后的结果（契约 §7.1）。"""
    context = build_context(
        WRAPPED_PAGE,
        "斥力",
        "作用力表现为斥力",
        breaks=WRAPPED_BREAKS,
    )

    assert context is not None
    expected = reflow_span(WRAPPED_PAGE, WRAPPED_BREAKS, 0, WRAPPED_BREAKS[0])
    assert context["text"] == expected


def test_cross_line_item_highlight_lands_on_the_merged_word():
    """跨行落位的词，卡片高亮落在合并后的词上（契约 §2.1 + §7.1）。"""
    page = "我们研究量子\n力学的应用。"
    keyword = extract.Keyword(
        front="量子力学",
        type="term",
        back="描述微观物质运动规律的物理学分支。",
        learn_value=3,
    )

    items, not_in_text = extract.place_items(page, [keyword])
    assert not_in_text == 0
    item = items[0]
    # 落位的偏移跨了 PDF 的硬换行。
    assert "\n" in page[item["start"] : item["end"]]

    context = build_context(
        page,
        "量子力学",
        start=item["start"],
        end=item["end"],
        breaks=[],
    )

    assert context is not None
    assert context["text"] == "我们研究量子力学的应用。"
    hit = context["highlights"][0]
    assert context["text"][hit["start"] : hit["end"]] == "量子力学"


def test_context_without_breaks_keeps_the_old_behaviour():
    """没有 ``breaks`` 的老数据 ⇒ 段落仍以空行为界（行为不变）。"""
    page = "第一句无关。第二句开始。良渚文化是核心概念。第二句结束。最后一句无关。"

    context = build_context(page, "良渚文化", "良渚文化是核心概念")

    assert context is not None
    assert context["text"] == "第二句开始。良渚文化是核心概念。第二句结束。"


def test_context_with_breaks_uses_the_paragraph_not_the_sentence():
    """有 ``breaks`` 时取的是**段落**（不是前后各一句）。"""
    context = build_context(
        WRAPPED_PAGE,
        "斥力",
        "作用力表现为斥力",
        breaks=WRAPPED_BREAKS,
    )

    assert context is not None
    assert "另一段开始了。" not in context["text"]
    assert "固体被拉伸时" in context["text"]


def test_build_contexts_reads_breaks_from_pages_jsonl(tmp_path):
    """``build_contexts`` 按页读 ``breaks``，走合并口径。"""
    rows = page_rows({1: WRAPPED_PAGE})
    rows[0]["breaks"] = WRAPPED_BREAKS
    work = write_workdir(tmp_path / "book", candidates=[], pages=rows)
    cards = [
        {
            "card_id": "abc",
            "front": "斥力",
            "source": {
                "page": 1,
                "evidence": "作用力表现为斥力",
                "start": WRAPPED_PAGE.index("作用力表现为斥力"),
                "end": WRAPPED_PAGE.index("作用力表现为斥力") + len("作用力表现为斥力"),
            },
        }
    ]

    contexts, stats = build_contexts(work, cards)

    assert "abc" in contexts
    assert contexts["abc"]["text"] == (
        "分子之间既有引力又有斥力。当固体被压缩时，分子间的距离变小，"
        "作用力表现为斥力；当固体被拉伸时，作用力表现为引力。"
    )
    assert stats["merged_pages"] == 1


# ===================== 归一化口径一致 =====================


@pytest.mark.parametrize(
    "text",
    [
        "良渚 文化的城址",
        "well-known ﬁnal ﬂow",
        "It’s a test",
        "全角ＡＢＣ",
        "混合 mixed 文本 text",
    ],
)
def test_normalize_with_map_matches_the_section5_normalizer(text):
    """本模块的归一化必须和 §5 依据句校正那一份**逐字一致**。"""
    assert _normalize_with_map(text)[0] == _normalize_for_match(text)


def test_normalize_with_map_keeps_original_offsets():
    """归一化删掉空白后，偏移还能换算回原文。"""
    text = "良渚 文化"
    normalized, index_map = _normalize_with_map(text)

    assert normalized == "良渚文化"
    assert text[index_map[0] : index_map[3] + 1] == "良渚 文化"


def test_scene_mode_is_written_to_book_json(tmp_path):
    """scene_mode 写进 book.json（契约 §7.1）。"""
    from bookcard.pipeline.pack import _write_scene_mode

    work = write_workdir(tmp_path / "book", candidates=[])
    _write_scene_mode(WorkDir(work), SCENE_TEXT)

    assert json.loads((work / "book.json").read_text(encoding="utf-8"))["scene_mode"] == SCENE_TEXT
    assert WorkDir(work).book()["scene_mode"] == SCENE_TEXT
