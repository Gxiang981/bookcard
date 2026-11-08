"""bookcard 探针的测试。

夹具全部用 pymupdf 在 ``tmp_path`` 里现生成，仓库里不放二进制文件。
"""

import json

import pymupdf
import pytest

from bookcard.cli import main
from bookcard.probe import probe, rules

PAGE_W = 595
PAGE_H = 842
CJK_FONT = "china-s"
LATIN_FONT = "helv"

GARBLED = "J( cd* #íB9Â):#=ÃÄ*"


# --------------------------------------------------------------------- 夹具


def _save(doc, path):
    """存盘并关掉文档。"""
    doc.save(path)
    doc.close()


def make_digital_book(path, page_count=40, front_matter=8):
    """数字版：每页有正文，页脚印页码，偏移 -8，前面 8 页不印页码。

    页脚页码 = 物理页号 - 8（``front_matter`` 页之后才开始印）。
    """
    doc = pymupdf.open()
    for index in range(page_count):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_text(
            (72, 100),
            f"Unit {index // 8 + 1} This is a sample English sentence about school textbooks.",
            fontsize=11,
            fontname=LATIN_FONT,
        )
        if index >= front_matter:
            page.insert_text(
                (280, 805),
                str(index + 1 - front_matter),
                fontsize=10,
                fontname=LATIN_FONT,
            )
    doc.set_toc([[1, "Contents", 2], [1, "Unit 1", 5], [1, "Unit 2", 12]])
    _save(doc, path)


def make_watermark_book(path, page_count=10, watermark="微信公众号：电子课本大全"):
    """假文本层：每页只有同一行水印。"""
    doc = pymupdf.open()
    for _ in range(page_count):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_text((72, 100), watermark, fontsize=10, fontname=CJK_FONT)
    _save(doc, path)


def make_blank_book(path, page_count=10):
    """扫描件：纯图片页，没有文本层。"""
    doc = pymupdf.open()
    for _ in range(page_count):
        doc.new_page(width=PAGE_W, height=PAGE_H)
    _save(doc, path)


def make_garbled_book(path, page_count=10):
    """乱码文本层：字体缺 ToUnicode 抽出来的符号。

    每页的乱码内容**必须不一样**：整本书逐字相同的一行会被水印规则剥掉，
    那是水印路径，不是乱码路径（真书里的乱码本来就逐页不同）。
    """
    doc = pymupdf.open()
    for index in range(page_count):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_text((72, 100), f"{GARBLED} {index}", fontsize=11, fontname=LATIN_FONT)
    _save(doc, path)


def make_printed_toc_book(path, page_count=20):
    """数字版中文：第 3 页是印刷目录页，没有书签。"""
    doc = pymupdf.open()
    for index in range(page_count):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        if index == 2:
            page.insert_text((72, 100), "目录", fontsize=16, fontname=CJK_FONT)
            for line in range(6):
                page.insert_text(
                    (72, 150 + line * 30),
                    f"第{line + 1}章  集合与方程  {line * 12 + 3}",
                    fontsize=11,
                    fontname=CJK_FONT,
                )
        else:
            page.insert_text(
                (72, 100),
                f"第{index + 1}页的正文内容，讲的是集合、方程与函数的基本概念。",
                fontsize=11,
                fontname=CJK_FONT,
            )
    _save(doc, path)


def make_numbered_notes_book(path, page_count=10):
    """正文里有 ① 标记，页面底部有以 ① 开头的注释行。

    同样注意：正文逐页不同，否则会被水印规则剥掉。
    """
    doc = pymupdf.open()
    for index in range(page_count):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_text(
            (72, 100),
            f"① 函数是集合之间的一种对应关系，这里是第 {index + 1} 页的正文。",
            fontsize=11,
            fontname=CJK_FONT,
        )
        page.insert_text(
            (72, 700),
            f"① 注释：函数是集合到集合的一种对应关系（第 {index + 1} 条）。",
            fontsize=10,
            fontname=CJK_FONT,
        )
    _save(doc, path)


def make_circled_page_number_book(path, page_count=10):
    """页脚印的是圈号（``①``）而不是数字 —— ``str.isdigit()`` 会把它当成数字。"""
    doc = pymupdf.open()
    for index in range(page_count):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_text(
            (72, 100),
            f"第 {index + 1} 页的正文内容，讲的是集合、方程与函数的基本概念，以及它们之间的联系。",
            fontsize=11,
            fontname=CJK_FONT,
        )
        page.insert_text((280, 805), "①", fontsize=10, fontname=CJK_FONT)
    _save(doc, path)


def make_header_and_footer_number_book(path, page_count=20):
    """页眉印不变的单元号，页脚印真正的页码 —— 两者必须分开算。"""
    doc = pymupdf.open()
    for index in range(page_count):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_text(
            (72, 100),
            f"第 {index + 1} 页的正文内容，讲的是集合、方程与函数的基本概念和它们之间的联系。",
            fontsize=11,
            fontname=CJK_FONT,
        )
        page.insert_text((40, 40), "3", fontsize=10, fontname=LATIN_FONT)
        page.insert_text((280, 805), str(index + 1 + 4), fontsize=10, fontname=LATIN_FONT)
    _save(doc, path)


def make_two_numbering_schemes_book(path, page_count=20, back_start=12):
    """前半段和后半段用两套编号（教材的正文 + 附答就是这个样子）。"""
    doc = pymupdf.open()
    for index in range(page_count):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_text(
            (72, 100),
            f"第 {index + 1} 页的正文内容，讲的是集合、方程与函数的基本概念和它们之间的联系。",
            fontsize=11,
            fontname=CJK_FONT,
        )
        if index < back_start:
            printed = index + 1 - 4
        else:
            printed = index + 1 - 11
        page.insert_text((280, 805), str(printed), fontsize=10, fontname=LATIN_FONT)
    _save(doc, path)


def make_anchor_book(path, page_count=12):
    """自动页锚点书签：每页一条 ``book_pdf_p0001`` 形态的书签。"""
    doc = pymupdf.open()
    for _ in range(page_count):
        doc.new_page(width=PAGE_W, height=PAGE_H)
    doc.set_toc([[1, f"book_pdf_p{index + 1:04d}", index + 1] for index in range(page_count)])
    _save(doc, path)


# --------------------------------------------------------------------- 纯规则


def test_readable_ratio_flags_garbled_text():
    """乱码串的可读占比要低于 0.6。"""
    assert rules.readable_ratio(GARBLED) < 0.6
    assert rules.readable_ratio("第一章 集合与方程 12") > 0.9
    assert rules.readable_ratio("") == 0.0


def test_classify_page_uses_waterfall():
    """页类型判定：空页 / 仅水印 / 乱码 / 正文。"""
    assert rules.classify_page("", "") == "empty"
    assert rules.classify_page("微信公众号：电子课本大全", "") == "watermark_only"
    assert rules.classify_page(GARBLED, GARBLED) == "garbled"
    assert rules.classify_page("第一章 集合与方程 12", "第一章 集合与方程 12") == "text"


def test_bookmark_titles_are_classified():
    """书签标题三分类：人工 / 自动页锚点 / 制作残留。"""
    assert rules.classify_bookmark_title("目录") == "manual"
    assert rules.classify_bookmark_title("义务教育 01-11") == "manual"
    assert rules.classify_bookmark_title("book_pdf_p0001") == "auto_anchor"
    assert rules.classify_bookmark_title("数学七上内文_页面_001") == "auto_anchor"
    assert rules.classify_bookmark_title("12") == "auto_anchor"
    assert (
        rules.classify_bookmark_title("33949-0-普通高中教科书 历史 必修 中外历史纲要 上_DJD")
        == "auto_anchor"
    )
    assert rules.classify_bookmark_title("9上.pdf") == "residue"
    assert rules.classify_bookmark_title("2-版权(要替换).pdf") == "residue"


def test_pure_digit_bookmark_uses_target_page():
    """纯数字书签要靠目标页区分：页锚点还是章节号。"""
    assert rules.classify_bookmark_title("12", page=12) == "auto_anchor"
    assert rules.classify_bookmark_title("12", page=34) == "manual"
    assert rules.classify_bookmark_title("1", page=8) == "manual"


def test_probe_chapter_number_bookmarks_are_manual(tmp_path):
    """中文教材的章节号书签（「1」→第 8 页）是人工书签，不是页锚点。"""
    book = tmp_path / "chapters.pdf"
    doc = pymupdf.open()
    for _ in range(20):
        doc.new_page(width=PAGE_W, height=PAGE_H)
    doc.set_toc([[1, "文前", 1], [1, "1", 8], [1, "2", 12], [1, "3", 16]])
    _save(doc, book)

    report = probe(book)

    bookmarks = report["toc"]["bookmarks"]
    assert bookmarks["total"] == 4
    assert bookmarks["manual"] == 4
    assert bookmarks["auto_anchor"] == 0
    assert report["toc"]["chosen"] == "bookmarks"


def test_probe_header_and_footer_numbers_are_separated(tmp_path):
    """页眉的单元号不能稀释页脚的真页码。"""
    book = tmp_path / "hf.pdf"
    make_header_and_footer_number_book(book)

    report = probe(book)

    assert report["page_numbers"]["status"] == "found"
    assert report["page_numbers"]["offset"] == 4
    assert report["page_numbers"]["position"] == "footer_center"


def test_classify_bookmarks_counts_three_kinds():
    """书签分类计数。"""
    toc = [[1, "目录", 2], [1, "第1章", 3], [1, "义务教育 01-11", 12]]
    result = rules.classify_bookmarks(toc, page_count=20)
    assert result["total"] == 3
    assert result["manual"] == 3
    assert result["auto_anchor"] == 0
    assert result["residue"] == 0


def test_find_watermark_lines_needs_repeat():
    """同一行要出现在足够多的页上才算水印。

    水印行还有长度下限（``MIN_WATERMARK_LENGTH``）—— 短的栏目标签不算水印。
    """
    pages = [["水印行在这里"], ["水印行在这里"], ["水印行在这里"], ["正文"]]
    assert rules.find_watermark_lines(pages, threshold=0.5) == ["水印行在这里"]
    assert rules.find_watermark_lines(pages, threshold=0.9) == []


def test_verdict_thresholds():
    """判定阈值：digital / mixed / scanned / garbled。"""
    assert rules.verdict_from_counts({"text": 9, "empty": 1}, 10) == "digital"
    assert rules.verdict_from_counts({"text": 5, "empty": 5}, 10) == "mixed"
    assert rules.verdict_from_counts({"text": 0, "empty": 10}, 10) == "scanned"
    assert rules.verdict_from_counts({"text": 0, "garbled": 10}, 10) == "garbled"


# --------------------------------------------------------------------- 探针


def test_probe_digital_book(tmp_path):
    """数字版：可用文本层高、目录来自书签、页码偏移与位置都对。"""
    book = tmp_path / "digital.pdf"
    make_digital_book(book)

    report = probe(book)

    assert report["ok"] if "ok" in report else True  # 报告结构里没有 ok 字段也算通过
    assert report["probe_version"] == 1
    assert report["verdict"] == "digital"
    assert report["ok"] is True
    assert report["text_layer"]["usable_ratio"] == 1.0
    assert report["text_layer"]["pages"]["text"] == 40
    assert report["toc"]["chosen"] == "bookmarks"
    assert report["toc"]["bookmarks"]["manual"] == 3
    assert report["toc"]["bookmarks"]["total"] == 3
    assert report["page_numbers"]["status"] == "found"
    assert report["page_numbers"]["offset"] == -8
    assert report["page_numbers"]["position"] == "footer_center"
    assert report["file"]["page_count"] == 40
    assert report["file"]["sha256"]
    assert report["file"]["bytes"] > 0


def test_probe_watermark_only_book(tmp_path):
    """水印假文本层：页面全是 watermark_only，判为 scanned。"""
    book = tmp_path / "watermark.pdf"
    make_watermark_book(book)

    report = probe(book)

    assert report["verdict"] == "scanned"
    assert report["text_layer"]["pages"]["watermark_only"] == 10
    assert report["text_layer"]["pages"]["text"] == 0
    assert report["text_layer"]["usable_ratio"] == 0.0
    assert "微信公众号：电子课本大全" in report["text_layer"]["watermarks"]


def test_probe_blank_book(tmp_path):
    """扫描件：没有文本层，页面算 empty。"""
    book = tmp_path / "blank.pdf"
    make_blank_book(book)

    report = probe(book)

    assert report["verdict"] == "scanned"
    assert report["text_layer"]["pages"]["empty"] == 10
    assert report["text_layer"]["usable_ratio"] == 0.0
    assert report["page_numbers"]["status"] == "unknown"


def test_probe_garbled_book(tmp_path):
    """乱码文本层：页面分类 garbled，全书判定 garbled。"""
    book = tmp_path / "garbled.pdf"
    make_garbled_book(book)

    report = probe(book)

    assert report["verdict"] == "garbled"
    assert report["text_layer"]["pages"]["garbled"] == 10
    assert report["text_layer"]["usable_ratio"] == 0.0


def test_probe_auto_anchor_bookmarks(tmp_path):
    """自动页锚点：书签不能当目录用。"""
    book = tmp_path / "anchor.pdf"
    make_anchor_book(book)

    report = probe(book)

    assert report["toc"]["bookmarks"]["auto_anchor"] == 12
    assert report["toc"]["bookmarks"]["manual"] == 0
    assert report["toc"]["chosen"] != "bookmarks"


def test_probe_printed_toc_page(tmp_path):
    """印刷目录页：目录来源是 printed_page，目录页是第 3 页。"""
    book = tmp_path / "toc.pdf"
    make_printed_toc_book(book)

    report = probe(book)

    assert report["toc"]["chosen"] == "printed_page"
    assert report["toc"]["printed_toc_pages"] == [3]
    assert report["toc"]["bookmarks"]["total"] == 0


def test_probe_numbered_notes(tmp_path):
    """编号注释：正文有 ①，页底有以 ① 开头的注释行。"""
    book = tmp_path / "notes.pdf"
    make_numbered_notes_book(book)

    report = probe(book)

    assert report["notes"]["numbered_notes"] is True
    assert report["notes"]["markers"]["circled"] >= 10


def test_probe_broken_file(tmp_path):
    """坏文件：不是 PDF，报 broken，不抛异常。"""
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"this is not a pdf at all")

    report = probe(broken)

    assert report["verdict"] == "broken"
    assert report["ok"] is False
    assert report["file"]["is_pdf"] is False
    assert report["file"]["error"]
    assert report["file"]["page_count"] == 0
    assert report["text_layer"]["pages"] == {
        "text": 0,
        "watermark_only": 0,
        "garbled": 0,
        "empty": 0,
    }


def test_probe_missing_file(tmp_path):
    """文件不存在：也是坏文件，不抛异常。"""
    report = probe(tmp_path / "nope.pdf")

    assert report["verdict"] == "broken"
    assert report["file"]["error"]


def test_probe_circled_page_number_is_not_a_page_number(tmp_path):
    """页脚的圈号不能被当成印刷页码（``str.isdigit()`` 对 ``①`` 返回 True）。"""
    book = tmp_path / "circled.pdf"
    make_circled_page_number_book(book)

    report = probe(book)

    assert report["verdict"] != "broken"
    assert report["page_numbers"]["status"] == "not_found"
    assert report["page_numbers"]["offset"] is None


def test_probe_two_numbering_schemes(tmp_path):
    """两套编号的书：取最长的一段，segments 里把另一段也列出来。"""
    book = tmp_path / "two.pdf"
    make_two_numbering_schemes_book(book)

    report = probe(book)

    numbers = report["page_numbers"]
    assert numbers["status"] == "found"
    assert numbers["offset"] == -4
    offsets = {segment["offset"] for segment in numbers["segments"]}
    assert offsets == {-4, -11}


def test_probe_pages_limit(tmp_path):
    """--pages 只看前 N 页。"""
    book = tmp_path / "digital.pdf"
    make_digital_book(book, page_count=40)

    report = probe(book, pages=5)

    assert report["file"]["page_count"] == 40
    assert len(report["text_layer"]["per_page"]) == 5


# --------------------------------------------------------------------- CLI


def test_cli_writes_json_report(tmp_path, capsys):
    """CLI：--out 写报告文件，--json 的 stdout 能被 json.loads 解析。"""
    book = tmp_path / "digital.pdf"
    make_digital_book(book, page_count=40)
    out = tmp_path / "report.json"

    code = main(["probe", str(book), "--out", str(out), "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "digital"
    assert out.exists()
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["probe_version"] == 1


def test_cli_out_directory(tmp_path, capsys):
    """CLI：--out 给目录时，每本书写一个 <文件名>.probe.json。"""
    book = tmp_path / "digital.pdf"
    make_digital_book(book, page_count=40)
    outdir = tmp_path / "reports"

    code = main(["probe", str(book), "--out", str(outdir)])

    assert code == 0
    assert (outdir / "digital.probe.json").exists()
    assert "类型：digital" in capsys.readouterr().out


def test_cli_broken_file_exit_code(tmp_path, capsys):
    """CLI：坏文件退出码 3，且不打印 traceback。"""
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")

    code = main(["probe", str(broken)])

    assert code == 3
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out + captured.err
    assert "broken" in captured.out


def test_cli_requires_path():
    """CLI：缺参数时报错退出（退出码 2）。"""
    with pytest.raises(SystemExit) as excinfo:
        main(["probe"])

    assert excinfo.value.code == 2


def test_find_watermark_lines_ignores_short_column_labels():
    """「阅 读」「a」这类栏目标签/脚注标记不算水印。

    水印会整行抹掉、还在正文里按子串替换 —— 把「阅读」当水印，
    目录里的「新闻阅读」这类标题就找不到了（判据 C4 不过）。
    """
    pages = [
        ["正文一", "阅 读"],
        ["正文二", "阅 读"],
        ["正文三", "阅 读", "a"],
        ["正文四", "阅 读"],
        ["正文五", "阅 读"],
        ["正文六"],
        ["正文七", "a"],
        ["正文八"],
        ["正文九", "a"],
        ["正文十"],
    ]
    assert rules.find_watermark_lines(pages) == []


def test_find_watermark_lines_still_finds_real_watermarks():
    pages = [["正文", "微信公众号：电子课本大全"] for _ in range(10)]
    assert rules.find_watermark_lines(pages) == ["微信公众号：电子课本大全"]
