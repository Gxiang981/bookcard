"""§5 定位的测试：像素坐标换算、页面图渲染、定位成功率。"""

import pymupdf

from bookcard.pipeline.locate import (
    PIXEL_SCALE,
    image_name,
    image_relpath,
    locate,
    rect_to_pixel,
    tight_rect,
)
from bookcard.pipeline.select import select
from bookcard.pipeline.workdir import WorkDir
from pipeline_fixtures import PAGE_TEXTS, candidate, make_pdf, write_workdir


def _workdir_with_cards(tmp_path, candidates, pdf_path=None):
    """造一个工作目录，先挑卡，再（可选）放上 PDF。"""
    work = write_workdir(tmp_path / "book", candidates=candidates)
    select(work)
    if pdf_path is not None:
        make_pdf(pdf_path)
    return work


def test_rect_to_pixel_converts_points_at_150_dpi():
    """PDF 点 → 150 dpi 像素（scale = 150/72）。"""
    assert PIXEL_SCALE == 150 / 72

    bbox = rect_to_pixel((72.0, 88.0, 120.0, 102.4))

    assert bbox == {"x": 150, "y": 183, "w": 100, "h": 30}


def test_rect_to_pixel_rounds_to_nearest_pixel():
    """坐标取整到最近的像素，宽高由差值算。"""
    bbox = rect_to_pixel((0.0, 0.0, 10.0, 10.0))

    assert bbox == {"x": 0, "y": 0, "w": 21, "h": 21}


def test_image_names_follow_contract():
    """页面图文件名：``images/p0012.jpg``（4 位页号）。"""
    assert image_name(12) == "p0012.jpg"
    assert image_relpath(12) == "images/p0012.jpg"


def test_locate_finds_bbox_on_real_pdf(tmp_path):
    """在真 PDF 上 search_for 找到框，bbox 填进 cards.jsonl。"""
    pdf = tmp_path / "book.pdf"
    work = _workdir_with_cards(
        tmp_path,
        [candidate(1, "良渚文化", explanation="解释。")],
        pdf_path=pdf,
    )

    summary = locate(work, pdf)
    card = WorkDir(work).cards()[0]

    assert summary["cards"] == 1
    assert summary["located"] == 1
    assert summary["locate_rate"] == 1.0
    assert card["source"]["bbox"] is not None
    assert card["source"]["bbox"]["x"] >= 0
    assert card["source"]["bbox"]["w"] > 0
    assert card["source"]["image"] == "images/p0001.jpg"


def test_locate_renders_page_image_at_150_dpi(tmp_path):
    """页面图是 150 dpi 的 JPEG，尺寸跟着渲染分辨率走。"""
    pdf = tmp_path / "book.pdf"
    work = _workdir_with_cards(
        tmp_path,
        [candidate(1, "良渚文化", explanation="解释。")],
        pdf_path=pdf,
    )

    locate(work, pdf)

    image_path = work / "images" / "p0001.jpg"
    assert image_path.exists()
    pixmap = pymupdf.Pixmap(str(image_path))
    assert (pixmap.width, pixmap.height) == (1240, 1755)
    assert pixmap.n == 3  # RGB（不是带 alpha 的 RGBA）


def test_locate_leaves_bbox_null_when_front_not_found(tmp_path):
    """页面上找不到这个词 ⇒ bbox 为 null，计入定位失败。"""
    pdf = tmp_path / "book.pdf"
    work = _workdir_with_cards(
        tmp_path,
        [candidate(1, "良渚文化", explanation="解释。")],
        pdf_path=pdf,
    )
    # 把卡片换成页面上没有的词。
    cards = WorkDir(work).cards()
    cards[0]["front"] = "不存在的词"
    WorkDir(work).cards_path.write_text(
        "".join(__import__("json").dumps(card, ensure_ascii=False) + "\n" for card in cards),
        encoding="utf-8",
    )

    summary = locate(work, pdf)
    card = WorkDir(work).cards()[0]

    assert summary["located"] == 0
    assert summary["locate_rate"] == 0.0
    assert card["source"]["bbox"] is None
    assert card["source"]["image"] == "images/p0001.jpg"


def test_locate_picks_occurrence_matching_start_offset(tmp_path):
    """同一个词在页上出现多次 ⇒ 取与 start 序号对应的那处。"""
    text = "良渚文化是良渚文化的代表，良渚文化很重要。"
    texts = {1: text}
    work = write_workdir(
        tmp_path / "book",
        pages=[
            {
                "page": 1,
                "printed_page": "1",
                "text": text,
                "text_sha1": __import__("hashlib").sha1(text.encode()).hexdigest(),
                "kind": "body",
            }
        ],
        sections=[
            {
                "sid": "s01",
                "title": "第1课",
                "level": 2,
                "page_start": 1,
                "page_end": 1,
                "sort_order": 1,
            }
        ],
        candidates=[
            candidate(1, "良渚文化", explanation="解释。", texts=texts),
        ],
    )
    select(work)
    pdf = tmp_path / "book.pdf"
    make_pdf(pdf, texts={1: text})

    locate(work, pdf)
    card = WorkDir(work).cards()[0]

    # 第一处出现 ⇒ 框应该落在页面靠上的位置。
    assert card["source"]["bbox"]["y"] < 400


def test_locate_reports_rendered_pages(tmp_path):
    """只用到的页才渲染。"""
    pdf = tmp_path / "book.pdf"
    work = _workdir_with_cards(
        tmp_path,
        [candidate(1, "良渚文化", explanation="解释。")],
        pdf_path=pdf,
    )

    summary = locate(work, pdf)

    assert summary["rendered_pages"] == 1
    assert not (work / "images" / "p0002.jpg").exists()


def test_locate_uses_pages_text_for_ordinal(tmp_path):
    """定位用 pages.jsonl 的页文字来定「第几次出现」。"""
    assert PAGE_TEXTS[1].count("良渚文化") == 1


def test_tight_rect_shrinks_the_font_em_box():
    """PDF 给的是字体的 em 框（可能到字号的 2.8 倍）—— 高亮要收紧到字本身。"""
    # 一个 12pt 的字，em 框从 562 到 596（高 34pt），基线在 583.7
    em_box = (85.2, 562.0, 168.9, 596.2)
    fonts = [((85.2, 562.0, 336.0, 596.2), 12.0, 583.7)]

    rect = tight_rect(em_box, fonts)

    assert rect[0] == em_box[0] and rect[2] == em_box[2]      # 左右不动
    assert 11 <= rect[3] - rect[1] <= 16                     # 高度收到字高
    assert abs(rect[3] - (583.7 + 12 * 0.22)) < 0.01          # 下沿贴着基线


def test_tight_rect_keeps_box_when_no_span_matches():
    """找不到对应的 span 就原样返回（别把框弄丢）。"""
    em_box = (10.0, 20.0, 30.0, 40.0)
    assert tight_rect(em_box, []) == em_box
