"""§2.2 扫描件 OCR 取字的测试。

覆盖：

- **阅读顺序**（先按栏、再从上到下）：XY 切分的纯函数，喂固定的行框；
- **置信度过滤**：低于 0.5 的行丢掉；
- **``start`` 偏移**：``ocr/pXXXX.json`` 里的偏移要落在页文字上；
- **等比切 bbox**：词在 OCR 行框里的框（契约 §2.2）；
- **数字版书走 ``auto`` 不触发 OCR**；
- **真跑 OCR 的冒烟测试**（没装 ``bookcard[ocr]`` 就 skip）。
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bookcard.engine import ocr
from bookcard.engine.text import build_text
from pipeline_fixtures import make_pdf


def _line(text: str, x0: float, y0: float, x1: float, y1: float, conf: float = 0.99):
    """造一行（坐标是 PDF 点）。"""
    return ocr.OcrLine(text=text, bbox=(x0, y0, x1, y1), conf=conf)


def _engine(rows):
    """打桩的 OCR 引擎：``rows`` 是 ``[(文字, 置信度, 四个角点), ...]``。

    ``rows`` 也可以给一个函数（按第几次调用返回当页的行）——每页内容不一样，
    才不会被「水印规则」（同一行出现在 ≥30% 的页上）当成水印剥掉。
    """

    class Stub:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, image, **kwargs):
            self.calls += 1
            page_rows = rows(self.calls) if callable(rows) else rows
            return SimpleNamespace(
                boxes=[row[2] for row in page_rows],
                txts=[row[0] for row in page_rows],
                scores=[row[1] for row in page_rows],
            )

    return Stub()


def _box(x0, y0, x1, y1):
    """rapidocr 给的四个角点（左上、右上、右下、左下）。"""
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


# ===================== 阅读顺序 =====================


def test_reading_order_puts_the_left_column_first():
    """两栏：先读完左栏，再读右栏（契约 §2.2「先按栏、再从上到下」）。"""
    lines = [
        _line("右一", 300, 100, 500, 120),
        _line("左二", 50, 140, 250, 160),
        _line("右二", 300, 140, 500, 160),
        _line("左一", 50, 100, 250, 120),
    ]

    ordered = [line.text for line in ocr.reading_order(lines)]

    assert ordered == ["左一", "左二", "右一", "右二"]


def test_reading_order_sorts_top_to_bottom_in_one_column():
    """一栏：从上到下。"""
    lines = [
        _line("第三", 50, 200, 250, 220),
        _line("第一", 50, 100, 250, 120),
        _line("第二", 50, 150, 250, 170),
    ]

    ordered = [line.text for line in ocr.reading_order(lines)]

    assert ordered == ["第一", "第二", "第三"]


def test_reading_order_does_not_split_a_column_when_a_line_spans_it():
    """有一行横跨两栏时，不能在那里切栏（那不是真的分栏）。"""
    lines = [
        _line("左", 50, 100, 250, 120),
        _line("横跨", 50, 140, 500, 160),
        _line("右", 300, 180, 500, 200),
    ]

    ordered = [line.text for line in ocr.reading_order(lines)]

    assert ordered == ["左", "横跨", "右"]


def test_reading_order_keeps_a_side_box_after_the_body_text():
    """左边窄旁栏（教材的「试一试」框）不当栏排——不能把标题压到后面。

    版面：上方是正文标题和段落，下方左边是旁栏框、右边是正文。
    旁栏不是「从页面顶部就并排开始」的栏，排出去会把整栏提到正文前面，
    页标题都被压到第 13 行去了（实测人教生物七上第 101 页）。
    """
    lines = [
        _line("标题", 200, 100, 400, 125),
        _line("正文一", 200, 140, 500, 160),
        _line("旁栏一", 50, 400, 160, 420),
        _line("旁栏二", 50, 425, 160, 445),
        _line("正文二", 200, 400, 500, 420),
    ]

    ordered = [line.text for line in ocr.reading_order(lines)]

    # 关键：旁栏不能抢到页面上方正文的前面（标题必须第一行）。
    assert ordered[0] == "标题"
    assert ordered.index("正文一") < ordered.index("旁栏一")
    # 旁栏和它旁边的正文并排时，按「先按栏」左边在前。
    assert ordered.index("旁栏一") < ordered.index("正文二")


# ===================== 置信度过滤 =====================


def test_ocr_page_drops_the_low_confidence_lines(tmp_path):
    """置信度低于 0.5 的行丢掉（契约 §2.2）。"""
    pdf = make_pdf(tmp_path / "book.pdf")
    engine = _engine(
        [
            ("认得出的", 0.97, _box(100, 100, 300, 130)),
            ("认不出的", 0.42, _box(100, 150, 300, 180)),
        ]
    )

    import pymupdf

    with pymupdf.open(pdf) as doc:
        lines = ocr.ocr_page(doc[0], engine=engine)

    assert [line.text for line in lines] == ["认得出的"]


# ===================== start 偏移 =====================


def test_line_records_offsets_land_on_the_page_text():
    """``start`` 要落在页文字上（和 ``join_lines`` 同一份拼法）。"""
    lines = [
        {"text": "第一行", "bbox": (10, 10, 90, 30), "conf": 0.9},
        {"text": "第二行", "bbox": (10, 40, 90, 60), "conf": 0.9},
    ]

    records = ocr.line_records(lines)
    text = ocr.page_text_of(lines)

    assert records[0]["start"] == 0
    assert records[1]["start"] == len("第一行") + 1
    assert text[records[1]["start"] :][: len("第二行")] == "第二行"


def test_line_records_keep_bbox_and_conf():
    """行框和置信度原样写进 ``ocr/pXXXX.json``。"""
    lines = [{"text": "甲", "bbox": (1.0, 2.0, 3.0, 4.0), "conf": 0.87654}]

    record = ocr.line_records(lines)[0]

    assert record["bbox"] == [1.0, 2.0, 3.0, 4.0]
    assert record["conf"] == 0.8765
    assert record["text"] == "甲"


# ===================== 等比切 bbox =====================


def test_ocr_word_rect_splits_the_line_by_character_count():
    """词在行框内按字符数等比切（契约 §2.2）。"""
    records = [{"text": "中华人民", "bbox": (100, 200, 300, 220), "conf": 0.9, "start": 0}]

    rect = ocr.word_rect(records, 2, 2)

    assert rect == (200.0, 200, 300.0, 220)


def test_ocr_word_rect_returns_none_when_the_word_is_not_on_the_page():
    """词不在这一页 ⇒ 没有框。"""
    records = [{"text": "中华人民", "bbox": (100, 200, 300, 220), "conf": 0.9, "start": 0}]

    assert ocr.word_rect(records, 99, 2) is None


def test_ocr_word_rect_returns_none_for_a_word_spanning_two_lines():
    """词跨行 ⇒ 没有框（不硬凑）。"""
    records = [
        {"text": "中华", "bbox": (100, 200, 300, 220), "conf": 0.9, "start": 0},
        {"text": "人民", "bbox": (100, 240, 300, 260), "conf": 0.9, "start": 3},
    ]

    assert ocr.word_rect(records, 2, 2) is None


def test_locate_exposes_the_same_ocr_rect():
    """定位模块用的是引擎里那一份口径（只此一份实现）。"""
    from bookcard.pipeline.locate import ocr_word_rect

    records = [{"text": "中华人民", "bbox": (100, 200, 300, 220), "conf": 0.9, "start": 0}]

    assert ocr_word_rect(records, 2, 2) == ocr.word_rect(records, 2, 2)


# ===================== 行内分段（页码、目录要用） =====================


def test_line_spans_split_the_page_number_off_the_running_head():
    """「第一章认识生物11」要能拆出末尾的页码（印刷页码认得出）。"""
    spans = ocr.line_spans("第一章认识生物11", (338.8, 654.4, 421.0, 667.4), 13.0)

    assert spans[-1]["text"] == "11"
    # 页码落在右边（版心外侧）——页码槽才认得出来。
    assert spans[-1]["bbox"][0] > 404.0


def test_line_spans_split_the_leading_page_number():
    """「12 第一单元 生物和生物圈」的行首页码要单独成段。"""
    spans = ocr.line_spans("12 第一单元 生物和生物圈", (46.5, 653.7, 154.1, 664.8), 11.1)

    assert spans[0]["text"] == "12"
    assert spans[0]["bbox"][0] == pytest.approx(46.5)


# ===================== 数字版书：一页都不 OCR =====================


def test_auto_mode_does_not_ocr_a_digital_book(tmp_path, monkeypatch):
    """数字版书走 ``auto`` 不触发 OCR（契约 §2.2）。"""
    pdf = make_pdf(tmp_path / "book.pdf")

    def _boom():
        raise AssertionError("数字版书不许走 OCR")

    monkeypatch.setattr(ocr, "get_engine", _boom)

    summary = build_text(pdf, tmp_path / "out", ocr_mode="auto")

    assert summary["ocr"] is False
    rows = [
        json.loads(line)
        for line in (tmp_path / "out" / "pages.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert rows
    assert all(row["text_origin"] == "pdf_text" for row in rows)
    assert not (tmp_path / "out" / "ocr").exists()


def test_never_mode_never_writes_ocr_files(tmp_path, monkeypatch):
    """``--ocr never`` 一页都不 OCR，也不写 ``ocr/``。"""
    pdf = make_pdf(tmp_path / "book.pdf")

    def _boom():
        raise AssertionError("never 模式不许走 OCR")

    monkeypatch.setattr(ocr, "get_engine", _boom)

    summary = build_text(pdf, tmp_path / "out", ocr_mode="never")

    assert summary["ocr"] is False
    assert not (tmp_path / "out" / "ocr").exists()


# ===================== 端到端（打桩引擎） =====================


def test_build_text_with_a_stubbed_engine_writes_ocr_files(tmp_path):
    """打桩引擎跑一遍 ``text``：``text_origin``、``ocr/``、``breaks`` 都要有。"""
    pdf = make_pdf(tmp_path / "book.pdf", texts={1: "甲", 2: "乙", 3: "丙", 4: "丁"})

    def _rows(call):
        return [
            (f"第{call}页第一行内容", 0.98, _box(50, 100, 400, 125)),
            (f"第{call}页第二行内容", 0.97, _box(50, 140, 400, 165)),
            ("低置信度", 0.10, _box(50, 180, 400, 205)),
        ]

    engine = _engine(_rows)
    summary = build_text(pdf, tmp_path / "out", ocr_mode="always", ocr_engine=engine)
    work = tmp_path / "out"

    assert summary["ocr"] is True
    assert engine.calls == 4  # 四页各 OCR 一次

    rows = [
        json.loads(line)
        for line in (work / "pages.jsonl").read_text(encoding="utf-8").splitlines() if line
    ]
    assert all(row["text_origin"] == "ocr" for row in rows)
    assert "低置信度" not in rows[0]["text"]
    assert rows[0]["text"].startswith("第1页第一行内容")

    records = json.loads((work / "ocr" / "p0001.json").read_text(encoding="utf-8"))
    assert [record["text"] for record in records] == ["第1页第一行内容", "第1页第二行内容"]
    assert records[1]["start"] == len("第1页第一行内容") + 1


def test_watermark_rule_applies_to_ocr_pages(tmp_path):
    """水印规则照常套用（契约 §2.2）：每页都有的那一行被剥掉。"""
    pdf = make_pdf(tmp_path / "book.pdf")

    def _rows(call):
        return [
            (f"第{call}页正文", 0.98, _box(50, 100, 400, 125)),
            ("微信公众号：电子课本大全", 0.99, _box(50, 700, 400, 725)),
        ]

    build_text(pdf, tmp_path / "out", ocr_mode="always", ocr_engine=_engine(_rows))
    rows = [
        json.loads(line)
        for line in (tmp_path / "out" / "pages.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]

    assert all("公众号" not in row["text"] for row in rows)
    assert all(row["text"].startswith("第") for row in rows)


# ===================== 真跑 OCR 的冒烟测试 =====================


@pytest.mark.skipif(not ocr.ocr_available(), reason="没装 bookcard[ocr]")
def test_real_ocr_smoke(tmp_path):
    """真跑一次 OCR：认得出字、坐标在页内、``start`` 对得上页文字。"""
    import pymupdf

    sentence = "良渚文化位于长江下游，是新石器时代晚期的文化遗存。"
    pdf = make_pdf(tmp_path / "book.pdf", texts={1: sentence, 2: sentence})
    engine = ocr.get_engine()

    with pymupdf.open(pdf) as doc:
        page = doc[0]
        lines = ocr.ocr_page(page, engine=engine)

        assert lines, "这一页应该有 OCR 结果"
        for line in lines:
            assert 0 <= line.x0 <= page.rect.width
            assert 0 <= line.y0 <= page.rect.height
            assert line.conf >= ocr.MIN_CONF

    text = "\n".join(line.text for line in lines)
    assert "良渚" in text


def test_require_ocr_mentions_the_install_command(monkeypatch):
    """没装 OCR 依赖时的提示要带安装命令（契约 §2.2：不静默跳过）。"""
    monkeypatch.setattr(ocr, "ocr_available", lambda: False)

    with pytest.raises(ocr.OCRUnavailableError) as excinfo:
        ocr.require_ocr()

    assert "bookcard[ocr]" in str(excinfo.value)
    assert ocr.INSTALL_COMMAND in str(excinfo.value)


def test_ocr_module_does_not_import_rapidocr_at_module_level():
    """引擎是可选依赖：模块顶层不能 import rapidocr（否则装不上 extras 的人 import 就炸）。"""
    source = Path(ocr.__file__).read_text(encoding="utf-8")
    module_level = source.split("def ocr_available")[0]

    assert "import rapidocr" not in module_level


def test_ocr_thread_setting_reads_config_file(tmp_path, monkeypatch):
    """子进程拿不到环境变量时，配置件 ``$BOOKCARD_HOME/ocr.conf`` 也能配线程数。"""
    monkeypatch.delenv(ocr.OCR_THREADS_ENV, raising=False)
    monkeypatch.setenv("BOOKCARD_HOME", str(tmp_path))
    assert ocr._ocr_thread_setting() is None
    (tmp_path / "ocr.conf").write_text("# 批量跑时封顶\nthreads=2\n", encoding="utf-8")
    assert ocr._ocr_thread_setting() == 2


def test_ocr_thread_setting_prefers_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOKCARD_HOME", str(tmp_path))
    monkeypatch.setenv(ocr.OCR_THREADS_ENV, "3")
    (tmp_path / "ocr.conf").write_text("threads=2\n", encoding="utf-8")
    assert ocr._ocr_thread_setting() == 3
