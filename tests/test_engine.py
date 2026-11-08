"""bookcard 抽取引擎的测试。

夹具全部现造，仓库里不放二进制文件。
"""

import json

import pymupdf
import pytest

from bookcard.cli import EXIT_GATE, main
from bookcard.engine import common, extract, gate, tocparse
from bookcard.engine import kinds as kinds_mod
from bookcard.engine import sections as sections_mod
from bookcard.engine import toc as toc_mod
from bookcard.engine.toc import GateNotApprovedError, build_sections_file, build_toc

# --------------------------------------------------------------------- 夹具


def line(text, x=100.0, y=100.0, size=12.0, spans=None):
    """造一行（几何 + span）。"""
    if spans is None:
        spans = [{"text": text, "bbox": (x, y, x + 60, y + size), "size": size}]
    return {
        "text": text,
        "bbox": (x, y, x + 60, y + size),
        "size": size,
        "spans": spans,
    }


def page(number, lines, width=595.0, height=842.0):
    """造一页。"""
    return {"number": number, "width": width, "height": height, "lines": lines}


def entries(*specs):
    """``entries((level, title, page), ...)``。"""
    return [{"level": level, "title": title, "page": number} for level, title, number in specs]


# --------------------------------------------------------------------- 判据 1


def test_pages_monotonic_ok():
    ok, reason = gate.check_pages_monotonic(entries((1, "甲", 7), (2, "乙", 12)), 100)
    assert ok
    assert "单调不减" in reason


def test_pages_monotonic_rejects_decreasing():
    ok, reason = gate.check_pages_monotonic(entries((1, "甲", 12), (2, "乙", 7)), 100)
    assert not ok
    assert "单调不减" in reason


def test_pages_monotonic_rejects_beyond_page_count():
    ok, reason = gate.check_pages_monotonic(entries((1, "甲", 7), (2, "乙", 120)), 100)
    assert not ok
    assert "超出全书页数" in reason


# --------------------------------------------------------------------- 判据 2


def test_coverage_ok():
    secs = [
        {"title": "甲", "level": 1, "page_start": 7, "page_end": 12, "pages": 6},
        {"title": "乙", "level": 1, "page_start": 13, "page_end": 20, "pages": 8},
    ]
    ok, reason = gate.check_coverage(secs, 7, 20)
    assert ok
    assert "连续覆盖" in reason


def test_coverage_detects_gap():
    secs = [
        {"title": "甲", "level": 1, "page_start": 7, "page_end": 12, "pages": 6},
        {"title": "乙", "level": 1, "page_start": 15, "page_end": 20, "pages": 6},
    ]
    ok, reason = gate.check_coverage(secs, 7, 20)
    assert not ok
    assert "空洞" in reason


def test_coverage_detects_overlap():
    secs = [
        {"title": "甲", "level": 1, "page_start": 7, "page_end": 12, "pages": 6},
        {"title": "乙", "level": 1, "page_start": 11, "page_end": 20, "pages": 10},
    ]
    ok, reason = gate.check_coverage(secs, 7, 20)
    assert not ok
    assert "重叠" in reason


def test_coverage_detects_tail_gap():
    secs = [{"title": "甲", "level": 1, "page_start": 7, "page_end": 12, "pages": 6}]
    ok, _ = gate.check_coverage(secs, 7, 20)
    assert not ok


def test_check_contiguity_reports_both_kinds():
    secs = [
        {"title": "甲", "level": 1, "page_start": 7, "page_end": 12, "pages": 6},
        {"title": "乙", "level": 1, "page_start": 15, "page_end": 20, "pages": 6},
    ]
    problems = sections_mod.check_contiguity(secs, 7, 20)
    assert ("gap", 13, 14) in problems


# --------------------------------------------------------------------- 判据 3


def test_find_title_page_allows_whitespace_difference():
    texts = {7: "第一单元  从中华文明起源到秦汉统一\n多民族封建国家"}
    assert gate.find_title_page("第一单元 从中华文明起源到秦汉统一多民族封建国家", texts, 7) == 7


def test_find_title_page_searches_around():
    texts = {9: "第1课 中华文明的起源与早期国家"}
    assert gate.find_title_page("第1课 中华文明的起源与早期国家", texts, 7) == 9


def test_find_title_page_returns_none_when_absent():
    assert gate.find_title_page("查无此条", {7: "正文"}, 7) is None


def test_build_sections_is_contiguous_without_gap_or_overlap():
    toc = entries(
        (1, "第一单元", 7),
        (2, "第1课", 8),
        (2, "第2课", 20),
        (2, "第3课", 30),
    )
    secs = sections_mod.build_sections(toc, 2, 7, 40)
    assert sections_mod.check_contiguity(secs, 7, 40) == []
    for earlier, later in zip(secs, secs[1:], strict=False):
        assert later["page_start"] == earlier["page_end"] + 1


def test_build_sections_keeps_standalone_blocks():
    """「活动课」「附录」自己不含细条目 → 自成一节。"""
    toc = entries(
        (1, "第一单元", 7),
        (2, "第1课", 8),
        (2, "第2课", 20),
        (1, "活动课", 30),
        (1, "附录", 35),
    )
    secs = sections_mod.build_sections(toc, 2, 7, 40)
    titles = [s["title"] for s in secs]
    assert "活动课" in titles
    assert "附录" in titles
    assert sections_mod.check_contiguity(secs, 7, 40) == []


def test_build_sections_skips_entries_without_physical_page():
    """目录页上没印页码的条目（人教版历史的单元行）换算不出物理页。

    它们定不了边界，要跳过 —— 不能让排序炸掉，也不能切出空洞。
    """
    toc = [
        {"level": 1, "title": "第一单元", "page": None},
        {"level": 2, "title": "第1课", "page": 7},
        {"level": 2, "title": "第2课", "page": 20},
        {"level": 1, "title": "第二单元", "page": None},
        {"level": 2, "title": "第3课", "page": 30},
    ]
    secs = sections_mod.build_sections(toc, 2, 7, 40)
    assert [s["page_start"] for s in secs] == [7, 20, 30]
    assert sections_mod.check_contiguity(secs, 7, 40) == []


def test_sections_for_level_falls_back_to_level_that_has_pages():
    """请求的层级只有「没页码的条目」时，退到真正有页码的那一层。"""
    toc = [
        {"level": 1, "title": "第一单元", "page": None},
        {"level": 2, "title": "第1课", "page": 7},
        {"level": 2, "title": "第2课", "page": 20},
    ]
    secs = sections_mod.sections_for_level(toc, 1, 7, 30)
    assert [s["page_start"] for s in secs] == [7, 20]
    assert sections_mod.check_contiguity(secs, 7, 30) == []


# --------------------------------------------------------------------- 闸门


def test_gate_approved_only_when_every_criterion_passes():
    toc = entries((1, "甲", 7), (1, "乙", 20))
    secs = sections_mod.build_sections(toc, 1, 7, 30)
    texts = {7: "甲", 20: "乙"}
    result = gate.evaluate(toc, secs, 30, 7, 30, texts, "agent:test")
    assert result["status"] == "approved"
    assert result["approved_by"] == "agent:test"
    assert len(result["reasons"]) == 3


def test_gate_rejected_when_title_missing():
    toc = entries((1, "甲", 7), (1, "乙", 20))
    secs = sections_mod.build_sections(toc, 1, 7, 30)
    result = gate.evaluate(toc, secs, 30, 7, 30, {7: "别的", 20: "别的"}, "agent:test")
    assert result["status"] == "rejected"
    assert result["approved_by"] == ""
    assert any("判据3" in reason for reason in result["reasons"])


# --------------------------------------------------------------------- 闸门不许跳


def _write_work_dir(tmp_path, gate_status):
    work = tmp_path / "book"
    work.mkdir()
    common.write_json(
        work / "toc.json",
        {
            "source": "printed",
            "entries": [
                {"level": 2, "title": "第1课 甲", "page": 8},
                {"level": 2, "title": "第2课 乙", "page": 20},
            ],
            "checks": {
                "pages_monotonic": True,
                "first_body_page": 7,
                "covered_pages": [7, 30],
                "gaps": [],
            },
            "gate": {
                "status": gate_status,
                "approved_by": "agent:test" if gate_status == "approved" else "",
                "at": "2026-01-01T00:00:00+08:00",
                "reasons": ["判据4 不通过：超范围"],
            },
        },
    )
    return work


def test_sections_refuses_when_gate_not_approved(tmp_path):
    work = _write_work_dir(tmp_path, "rejected")
    with pytest.raises(GateNotApprovedError):
        build_sections_file(work)
    assert not (work / "sections.json").exists()


def test_sections_written_when_gate_approved(tmp_path):
    work = _write_work_dir(tmp_path, "approved")
    summary = build_sections_file(work)
    rows = json.loads((work / "sections.json").read_text(encoding="utf-8"))
    assert summary["sections"] == len(rows) == 2
    assert rows[0]["sid"] == "s01"
    assert rows[0]["sort_order"] == 1
    assert rows[0]["page_start"] == 7
    assert rows[0]["page_end"] == 19
    assert rows[-1]["page_end"] == 30


def test_sections_refuses_when_gate_approved_but_unreviewed(tmp_path):
    """老版本自动批留下的 ``agent:unreviewed``：照样不许切章节。"""
    work = _write_work_dir(tmp_path, "approved")
    payload = json.loads((work / "toc.json").read_text(encoding="utf-8"))
    payload["gate"]["approved_by"] = "agent:unreviewed"
    common.write_json(work / "toc.json", payload)

    with pytest.raises(GateNotApprovedError):
        build_sections_file(work)
    assert not (work / "sections.json").exists()


def test_sections_refuses_when_gate_approved_without_approver(tmp_path):
    """``approved_by`` 为空（没人核对过）：照样不许切章节。"""
    work = _write_work_dir(tmp_path, "approved")
    payload = json.loads((work / "toc.json").read_text(encoding="utf-8"))
    payload["gate"]["approved_by"] = ""
    common.write_json(work / "toc.json", payload)

    with pytest.raises(GateNotApprovedError):
        build_sections_file(work)
    assert not (work / "sections.json").exists()


# --------------------------------------------------------------------- 目录只出 pending


#: 夹具书的九页正文（每章三页，标题在章首页）。
BOOK_PAGE_TEXTS: dict[int, str] = {
    1: "第1课 甲\n正文一。",
    2: "正文二。",
    3: "正文三。",
    4: "第2课 乙\n正文四。",
    5: "正文五。",
    6: "正文六。",
    7: "第3课 丙\n正文七。",
    8: "正文八。",
    9: "正文九。",
}


def _make_book_pdf(path, *, bookmarks: bool = False):
    """造一本九页的小 PDF（每章三页，标题在章首页）。"""
    doc = pymupdf.open()
    for number in sorted(BOOK_PAGE_TEXTS):
        page = doc.new_page(width=595, height=842)
        for line_no, text in enumerate(BOOK_PAGE_TEXTS[number].split("\n")):
            page.insert_text((72, 100 + line_no * 18), text, fontsize=12, fontname="china-s")
    if bookmarks:
        doc.set_toc([[1, "第1课 甲", 1], [1, "第2课 乙", 4], [1, "第3课 丙", 7]])
    doc.save(str(path))
    doc.close()
    return path


def _book_work_dir(tmp_path, *, bookmarks: bool = False):
    """一个能跑 ``build_toc`` 的书工作目录，返回 ``(工作目录, PDF 路径)``。"""
    work = tmp_path / "book"
    work.mkdir(parents=True, exist_ok=True)
    pdf = _make_book_pdf(tmp_path / "book.pdf", bookmarks=bookmarks)
    common.write_json(
        work / "book.json",
        {"fp12": "0123456789ab", "title": "示例教科书", "page_count": len(BOOK_PAGE_TEXTS)},
    )
    common.write_jsonl(
        work / "pages.jsonl",
        [
            {"page": number, "text": text, "kind": "body"}
            for number, text in sorted(BOOK_PAGE_TEXTS.items())
        ],
    )
    return work, pdf


def test_build_toc_without_approver_writes_pending(tmp_path):
    """``approved_by=None``：判据全过也只写 ``pending``（不许自动批）。"""
    work, pdf = _book_work_dir(tmp_path, bookmarks=True)
    summary = build_toc(work, pdf, approved_by=None)
    assert summary["gate"] == "pending"

    toc = json.loads((work / "toc.json").read_text(encoding="utf-8"))
    assert toc["gate"]["status"] == "pending"
    assert toc["gate"]["approved_by"] == ""
    assert toc["gate"]["checks_passed"] is True
    assert toc["gate"]["reasons"]


def test_build_toc_without_approver_rejects_when_checks_fail(tmp_path):
    """判据不过：照旧 ``rejected``，并记下 ``checks_passed`` 是 false。"""
    work, pdf = _book_work_dir(tmp_path)  # 没有书签 → 没有目录条目
    summary = build_toc(work, pdf, approved_by=None)
    assert summary["gate"] == "rejected"

    toc = json.loads((work / "toc.json").read_text(encoding="utf-8"))
    assert toc["gate"]["status"] == "rejected"
    assert toc["gate"]["approved_by"] == ""
    assert toc["gate"]["checks_passed"] is False


def test_build_toc_with_approver_still_approves(tmp_path):
    """给了 ``approved_by`` 还是老行为：判据全过就写 ``approved``。"""
    work, pdf = _book_work_dir(tmp_path, bookmarks=True)
    summary = build_toc(work, pdf, approved_by="agent:test")
    assert summary["gate"] == "approved"

    toc = json.loads((work / "toc.json").read_text(encoding="utf-8"))
    assert toc["gate"]["status"] == "approved"
    assert toc["gate"]["approved_by"] == "agent:test"
    assert toc["gate"]["checks_passed"] is True


def test_cli_sections_exit_code_is_gate_code(tmp_path, capsys):
    work = _write_work_dir(tmp_path, "rejected")
    assert main(["sections", str(work)]) == EXIT_GATE
    assert "闸门 1 没通过" in capsys.readouterr().out


# --------------------------------------------------------------------- 页码


def test_number_in_line_reads_leading_number():
    found = extract._number_in_line(line("144 物理 九年级", x=45.0))
    assert found == (144, 45.0)


def test_number_in_line_reads_trailing_number():
    spans = [
        {"text": "第十八章 电功率", "bbox": (379.5, 672.2, 458.0, 684.0), "size": 11.0},
        {"text": "103", "bbox": (458.7, 672.2, 478.0, 684.0), "size": 11.0},
    ]
    found = extract._number_in_line(line("第十八章 电功率103", x=379.5, spans=spans))
    assert found == (103, 458.7)


def test_number_in_line_ignores_middle_number():
    assert extract._number_in_line(line("第4课 西汉与东汉 20", x=235.0)) is None


# --------------------------------------------------------------------- 目录解析


def test_parse_classic_toc_reads_titles_and_pages():
    toc_page = page(
        4,
        [
            line("目 录", x=261.6, y=81.8, size=24.0),
            line("第一单元 从中华文明起源", x=175.3, y=191.9),
            line("的建立与巩固", x=236.3, y=215.7),
            line("1", x=525.6, y=216.9),
            line("第1课 中华文明的起源与早期国家", x=235.3, y=244.6),
            line("2", x=525.3, y=249.1),
        ],
    )
    parsed = tocparse.parse_classic_toc([toc_page])
    assert parsed == [
        {
            "level": 1,
            "title": "第一单元 从中华文明起源的建立与巩固",
            "page": 1,
        },
        {"level": 2, "title": "第1课 中华文明的起源与早期国家", "page": 2},
    ]


def test_parse_classic_toc_keeps_label_only_line_separate():
    """「活动课」独占一行时，下一行是标题，要用空格接上。"""
    toc_page = page(
        6,
        [
            line("活动课", x=175.3, y=514.6),
            line("家国情怀与统一多民族国家的演进", x=235.3, y=542.2),
            line("183", x=513.3, y=542.2),
        ],
    )
    parsed = tocparse.parse_classic_toc([toc_page])
    assert parsed[0]["title"] == "活动课 家国情怀与统一多民族国家的演进"


def test_parse_table_toc_reads_unit_titles():
    toc_page = page(
        5,
        [
            line("➊", x=56.7, y=104.2, size=18.0),
            line("How can we ", x=56.7, y=139.3, size=12.0),
            line("become good ", x=56.7, y=154.3, size=12.0),
            line("learners?", x=56.7, y=169.3, size=12.0),
            line("Page 1", x=56.7, y=192.9, size=10.5),
            line("Learning how to learn", x=150.2, y=112.7, size=10.5),
        ],
    )
    parsed = tocparse.parse_table_toc([toc_page])
    assert parsed == [
        {"level": 1, "title": "How can we become good learners?", "page": 1}
    ]


def test_parse_table_toc_reads_back_matter_title():
    toc_page = page(
        13,
        [
            line("Page 113", x=56.7, y=518.5, size=10.5),
            line("Notes on the Text", x=150.2, y=518.8, size=10.5),
        ],
    )
    parsed = tocparse.parse_table_toc([toc_page])
    assert parsed == [{"level": 1, "title": "Notes on the Text", "page": 113}]


# --------------------------------------------------------------------- 页面类型


def test_classify_kinds():
    pages = [
        {"number": 1, "class": "text", "text": "封面"},
        {"number": 2, "class": "text", "text": "版权页"},
        {"number": 3, "class": "text", "text": "目 录"},
        {"number": 4, "class": "text", "text": "正文第一页"},
        {"number": 5, "class": "text", "text": "附录内容"},
        {"number": 6, "class": "empty", "text": ""},
    ]
    result = kinds_mod.classify_kinds(pages, {3}, 4, [(5, 5)], [])
    assert result == ["cover", "front", "toc", "body", "appendix", "blank"]


def test_classify_kinds_marks_exercise():
    pages = [{"number": 4, "class": "text", "text": "练习"}]
    result = kinds_mod.classify_kinds(pages, set(), 4, [], [(4, 4)])
    assert result == ["exercise"]


# --------------------------------------------------------------------- 元数据


def test_load_book_meta_leaves_missing_fields_empty(tmp_path):
    path = tmp_path / "books.jsonl"
    path.write_text(
        json.dumps({"book_fp": "abc", "title": "只有标题"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    meta = common.load_book_meta("abc", path)
    assert meta["title"] == "只有标题"
    assert meta["publisher"] == ""
    assert meta["stage"] == ""


def test_load_book_meta_unknown_fp_is_all_empty(tmp_path):
    path = tmp_path / "books.jsonl"
    path.write_text(json.dumps({"book_fp": "abc", "title": "x"}) + "\n", encoding="utf-8")
    meta = common.load_book_meta("zzz", path)
    assert all(value == "" for value in meta.values())


def test_sections_for_level_按用户选的层级切():
    """层级是用户第二步确认的：不猜、不并节。"""
    toc = entries(
        (1, "第一单元", 7),
        (2, "第1课", 8),
        (2, "第2课", 20),
    )
    # 第 1 层 → 一节
    secs = sections_mod.sections_for_level(toc, 1, 7, 30)
    assert len(secs) == 1
    assert secs[0]["page_start"] == 7
    # 第 2 层 → 两节（不并节，哪怕有一节只有几页）
    secs2 = sections_mod.sections_for_level(toc, 2, 7, 30)
    assert len(secs2) == 2
    # 第一节从父条目的页开始（父级封面页算在第一节里）
    assert [s["page_start"] for s in secs2] == [7, 20]


def test_sections_for_level_目录里没这一层就退到最深():
    toc = entries((2, "第1课", 8), (2, "第2课", 20))
    secs = sections_mod.sections_for_level(toc, 1, 7, 30)
    assert len(secs) == 2


def test_extract_level_of_默认第1层():
    assert toc_mod.extract_level_of({}) == 1
    assert toc_mod.extract_level_of({"extract_level": 3}) == 3
    assert toc_mod.extract_level_of({"extract_level": "2"}) == 2


def test_sections_for_level_一层就是那层的条目():
    """选一层就只切那层（目录结构要唯一）。"""
    toc = entries(
        (1, "第 1 章", 7),
        (2, "第一节", 8),
        (2, "第二节", 20),
    )
    secs = sections_mod.sections_for_level(toc, 1, 7, 30)
    assert [s["title"] for s in secs] == ["第 1 章"]
    secs2 = sections_mod.sections_for_level(toc, 2, 7, 30)
    assert [s["title"] for s in secs2] == ["第 1 章 | 第一节", "第 1 章 | 第二节"]


def test_gate_ignores_entries_without_physical_page():
    """目录里没印页码的条目（page=None）不参加判据 1 和判据 3。"""
    toc = [
        {"level": 1, "title": "第一单元", "page": None},
        {"level": 2, "title": "第1课", "page": 7},
        {"level": 2, "title": "第2课", "page": 20},
    ]
    ok, reason = gate.check_pages_monotonic(toc, 100)
    assert ok, reason
    assert "2 条" in reason

    texts = {7: "第1课 中华文明的起源", 20: "第2课 早期国家"}
    ok3, reason3 = gate.check_titles(toc, texts)
    assert ok3, reason3


def test_find_title_page_ignores_punctuation_differences():
    """目录上的全角括号、OCR 出来的半角 —— 不该算对不上。"""
    texts = {121: "附录|中国古代史大事年表|(下)|事件|隋朝建立"}
    assert gate.find_title_page("中国古代史大事年表（下）", texts, 121) == 121


def test_find_title_page_ignores_footnote_markers():
    """标题和作者之间夹着脚注标记时也要找得到（跟看图目录的 C3 同一口径）。"""
    texts = {9: "1 春\na\n朱自清\n正文"}
    assert gate.find_title_page("1 春/ 朱自清", texts, 9) == 9


def test_find_title_page_strips_section_prefix():
    """标题主体跟 C3 同一口径：先剥掉编号前缀再找。

    目录上印「Unit 5 Let's eat!」，正文页上「Unit Five」一行、
    「Let's eat!」另起一行 —— 剥掉前缀才找得到。
    """
    texts = {51: "Can I have some milk?\nUnit Five", 52: "Let's eat!\nThank you."}
    assert gate.find_title_page("Unit 5 Let's eat!", texts, 51) == 52


def test_find_title_page_falls_back_to_line_similarity():
    """整串接不上时，逐行相似度也要能找到（跟看图目录的 C3 同一口径）。"""
    texts = {9: "9 化学使世界变得更加绚丽多彩"}
    assert gate.find_title_page("化学使世界变得更加绚丽多彩", texts, 9) == 9


def test_find_title_page_tolerates_pinyin_annotations():
    """标题是「字图 + 拼音」时，去掉拼音再找（跟 C3 同一口径）。"""
    texts = {5: "秋qiū天tiān tiān 天空"}
    assert gate.find_title_page("秋天", texts, 5) == 5


def test_find_title_page_handles_label_split():
    """目录上印「口语交际：转述」，正文页上「口语交际」和「转述」是两块文字。"""
    texts = {5: "口语交际\n转述\n请你说说。"}
    assert gate.find_title_page("口语交际：转述", texts, 5) == 5
