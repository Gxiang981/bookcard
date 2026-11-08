"""印刷目录跨页 + 分栏版式的测试。

夹具全部用 pymupdf 在 ``tmp_path`` 里现生成，仓库里不放二进制文件。

要盖住的两件事（/ D2）：

1. **目录跨页**：目录常常有好几页，只有第一页印着「目录」二字。续页要靠
   「像条目的行够多 + 页码行够多」认出来。
2. **分栏版式**：标题一整块在左边、页码一整块在右边，两块**逐行对齐**
   （页码是单独一行的独立数字，不在标题那一行上）。
"""

import pymupdf

from bookcard.engine import analysis, text, toc, tocparse
from bookcard.engine import kinds as kinds_mod

PAGE_W = 595
PAGE_H = 842
CJK_FONT = "china-s"
LATIN_FONT = "helv"

#: 标题栏的左边距 / 页码栏的左边距（右栏）。
TITLE_X = 72.0
NUMBER_X = 520.0

#: 目录第一页的条目：(标题, 印刷页码)。
TOC_PAGE_1 = [
    ("第一章 打开物理世界的大门", 1),
    ("第一节 走进神奇", 1),
    ("第二节 探索之路", 2),
    ("第三节 站在巨人的肩膀上", 3),
    ("第二章 运动的世界", 4),
    ("第一节 动与静", 5),
]

#: 目录第二页（**没有**「目录」二字）的条目。
TOC_PAGE_2 = [
    ("第二节 长度与时间的测量", 6),
    ("第三节 快与慢", 7),
    ("第三章 声的世界", 8),
    ("第一节 科学探究：声音的产生与传播", 9),
    ("第二节 声音的特性", 10),
]

#: 正文首页的物理页号 / 印刷页码的偏移。
FIRST_BODY_PAGE = 4
PRINTED_OFFSET = -3


def _save(doc, path):
    doc.save(path)
    doc.close()


def _insert_toc_page(page, entries, *, head=False):
    """把一页目录画成「标题块 + 页码块」两栏。"""
    if head:
        page.insert_text((420, 90), "目录", fontsize=22, fontname=CJK_FONT)
    y = 150.0
    for title, printed in entries:
        # 标题在左栏，页码在右栏，同一行。
        page.insert_text((TITLE_X, y), title, fontsize=12, fontname=CJK_FONT)
        page.insert_text((NUMBER_X, y), str(printed), fontsize=12, fontname=LATIN_FONT)
        y += 26


def make_columnar_toc_book(path, body_pages=10):
    """造一本目录跨两页、而且是分栏版式的书。

    版式照抄沪科版物理八年级：目录在物理第 2–3 页（只有第一页印「目录」），
    正文从物理第 4 页开始，**页码印在页眉**（页脚一个字都没有）——
    跟真书一样，页脚那一套找不到印刷页码的偏移。
    """
    doc = pymupdf.open()
    # 物理第 1 页：封面
    cover = doc.new_page(width=PAGE_W, height=PAGE_H)
    cover.insert_text((150, 300), "义务教育教科书 物理", fontsize=16, fontname=CJK_FONT)

    # 物理第 2 页：目录第一页（有「目录」二字）
    toc_page_1 = doc.new_page(width=PAGE_W, height=PAGE_H)
    _insert_toc_page(toc_page_1, TOC_PAGE_1, head=True)

    # 物理第 3 页：目录续页（没有「目录」二字）
    toc_page_2 = doc.new_page(width=PAGE_W, height=PAGE_H)
    _insert_toc_page(toc_page_2, TOC_PAGE_2)

    # 正文页：页眉左侧印印刷页码
    for index in range(body_pages):
        number = FIRST_BODY_PAGE + index
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_text((54, 50), str(number + PRINTED_OFFSET), fontsize=12, fontname=LATIN_FONT)
        page.insert_text(
            (72, 120),
            f"这是第 {number} 页的正文，讲的是物理世界里那些说不清的现象。",
            fontsize=12,
            fontname=CJK_FONT,
        )
    _save(doc, path)
    return path


# --------------------------------------------------------------------- 目录跨页


def test_find_toc_pages_continues_past_the_head_page(tmp_path):
    """目录跨两页时，续页（没有「目录」二字）也要认出来。"""
    path = make_columnar_toc_book(tmp_path / "columnar.pdf")
    pages = analysis.extract.extract_pages(path)
    info = analysis.analyze_pages(pages)

    assert info["toc_range"] == [2, 3]


def test_toc_pages_are_classified_as_toc(tmp_path):
    """两页目录的 ``kind`` 都得是 ``toc``（不是 ``body``）。"""
    path = make_columnar_toc_book(tmp_path / "columnar.pdf")
    info = analysis.analyze(path)

    kinds = kinds_mod.classify_kinds(
        info["pages"],
        set(info["toc_range"]),
        info["first_body_page"],
        [],
        [],
    )
    assert kinds[0] == "cover"
    assert kinds[1] == "toc"
    assert kinds[2] == "toc"


# --------------------------------------------------------------------- 分栏解析


def test_parse_columnar_toc_pairs_titles_with_page_numbers(tmp_path):
    """标题块和页码块按版面位置配对，层级和页码都要对。"""
    path = make_columnar_toc_book(tmp_path / "columnar.pdf")
    info = analysis.analyze(path)

    printed = info["printed_entries"]
    assert [(item["title"], item["page"]) for item in printed] == [
        (title, printed_page) for title, printed_page in TOC_PAGE_1 + TOC_PAGE_2
    ]
    levels = {item["title"]: item["level"] for item in printed}
    assert levels["第一章 打开物理世界的大门"] == 1
    assert levels["第二章 运动的世界"] == 1
    assert levels["第三章 声的世界"] == 1
    assert levels["第一节 走进神奇"] == 2
    assert levels["第二节 长度与时间的测量"] == 2


def test_columnar_toc_has_no_noise_entries(tmp_path):
    """不许出现 ``?`` 或者纯数字的标题——那些是页码串，不是条目。"""
    path = make_columnar_toc_book(tmp_path / "columnar.pdf")
    info = analysis.analyze(path)

    for entry in info["toc_entries"]:
        title = entry["title"]
        assert title.strip(), "空标题"
        assert title != "?", "问号标题（版式兜底把装饰符号当标题了）"
        assert not title.replace(" ", "").isdigit(), f"纯数字标题：{title!r}"


def test_columnar_toc_uses_printed_source(tmp_path):
    """走印刷目录，不再退到版式启发式。"""
    path = make_columnar_toc_book(tmp_path / "columnar.pdf")
    work = tmp_path / "work"
    text.build_text(path, work)
    result = toc.build_toc(work, path)

    assert result["source"] == "printed"
    assert result["entries"] == len(TOC_PAGE_1) + len(TOC_PAGE_2)


def test_columnar_toc_physical_pages_match_printed_pages(tmp_path):
    """印刷页码换成物理页号（靠页眉页码算出来的偏移）。"""
    path = make_columnar_toc_book(tmp_path / "columnar.pdf")
    info = analysis.analyze(path)

    assert info["offset"] == PRINTED_OFFSET
    for entry, (_title, printed_page) in zip(
        info["toc_entries"], TOC_PAGE_1 + TOC_PAGE_2, strict=True
    ):
        assert entry["page"] == printed_page - PRINTED_OFFSET


# --------------------------------------------------------------------- 单元级


def _toc_page(number, lines, width=PAGE_W):
    return {"number": number, "width": width, "height": PAGE_H, "lines": lines}


def _line(text, x, y, size=12.0):
    return {
        "text": text,
        "bbox": (x, y, x + 60, y + size),
        "size": size,
        "spans": [{"text": text, "bbox": (x, y, x + 60, y + size), "size": size}],
    }


def test_columnar_toc_drops_entries_without_a_page_number():
    """配不上的条目宁可丢，也不要产出 ``?`` 或纯数字标题。"""
    page = _toc_page(
        4,
        [
            _line("第一章 打开物理世界的大门", 184.0, 239.0),
            _line("2", 454.0, 230.0),
            _line("第一节 走进神奇", 184.0, 287.0),
        ],
    )
    parsed = tocparse.parse_classic_toc([page])
    assert parsed == [{"level": 1, "title": "第一章 打开物理世界的大门", "page": 2}]


def test_classic_toc_keeps_wrapped_title_with_its_page_number():
    """经典式：标题折行时，页码跟在标题块的末尾（配最后一行）。"""
    page = _toc_page(
        4,
        [
            _line("第一单元 从中华文明起源到秦汉", 175.0, 192.0),
            _line("的建立与巩固", 236.0, 216.0),
            _line("1", 526.0, 217.0),
        ],
    )
    parsed = tocparse.parse_classic_toc([page])
    assert parsed == [
        {
            "level": 1,
            "title": "第一单元 从中华文明起源到秦汉的建立与巩固",
            "page": 1,
        }
    ]


def test_classic_toc_skips_footnote_marker_lines():
    """标题左边的脚注星号不是折行——当成折行会把页码配到错的条目上。

    真书（沪科版物理九年级 p5）：「＊」印在「第4节 欧姆定律…」左边，
    字号只有 7 磅（标题是 12 磅），是版面上的标记。
    """
    page = _toc_page(
        4,
        [
            _line("第二节 力的合成", 187.0, 306.0),
            _line("131", 452.0, 305.0),
            _line("*", 181.0, 306.0, size=6.0),
            _line("第三节 力的平衡", 187.0, 330.0),
            _line("135", 452.0, 329.0),
        ],
    )
    parsed = tocparse.parse_classic_toc([page])
    assert parsed == [
        {"level": 2, "title": "第二节 力的合成", "page": 131},
        {"level": 2, "title": "第三节 力的平衡", "page": 135},
    ]


def test_classic_toc_strips_marker_from_title():
    """标题自带的星号（同一行）也要去掉。"""
    page = _toc_page(
        4,
        [
            _line("第3节 电阻的测量*", 187.0, 305.0),
            _line("80", 440.0, 306.0),
        ],
    )
    parsed = tocparse.parse_classic_toc([page])
    assert parsed == [{"level": 2, "title": "第3节 电阻的测量", "page": 80}]


def test_entry_start_recognizes_chinese_numeral_section():
    """「第一节」是二级条目（人教版写「第1节」，沪科版写「第一节」）。"""
    assert tocparse._entry_start("第一节 走进神奇") == (2, " 走进神奇")
    assert tocparse._entry_start("第1节 走进神奇") == (2, " 走进神奇")
    assert tocparse._entry_start("第一章 打开物理世界的大门") == (1, " 打开物理世界的大门")


def test_layout_toc_ignores_symbol_only_headings():
    """版式兜底不能把纯符号的大字（教材「请提问」那个 ``?``）当成标题。"""
    pages = [
        {
            "number": 23,
            "width": PAGE_W,
            "height": PAGE_H,
            "kind": "body",
            "lines": [
                _line("?", 95.0, 589.0, size=40.0),
                _line("请提问", 69.0, 609.0, size=14.0),
            ],
        }
    ]
    assert tocparse.parse_layout_toc(pages, 23, 12.0) == []


def test_header_page_offset_reads_header_page_numbers():
    """页码印在页眉（页脚没有）时，偏移也能算出来。"""
    pages = [
        {
            "number": number,
            "width": PAGE_W,
            "height": PAGE_H,
            "lines": [
                {
                    "text": str(number + PRINTED_OFFSET),
                    "bbox": (54.0, 50.0, 68.0, 64.0),
                    "size": 12.0,
                    "spans": [
                        {
                            "text": str(number + PRINTED_OFFSET),
                            "bbox": (54.0, 50.0, 68.0, 64.0),
                            "size": 12.0,
                        }
                    ],
                }
            ],
        }
        for number in (4, 5, 6, 7)
    ]
    assert analysis._header_page_offset(pages) == PRINTED_OFFSET
