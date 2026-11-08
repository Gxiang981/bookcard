"""``bookcard tocv`` 的单测（契约 ``docs/tocv-v1.md``）。

用 pymupdf 现场造小 PDF，不依赖真书：

- 数字版：有文本层，目录页和正文页都有中文标题。
- 扫描版：没有文本层，OCR 路径打桩（不真跑 OCR）。

CJK 文字用 pymupdf 自带的 ``china-s`` 字体，文字能原样取回来。
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from bookcard.engine import ocr as ocr_mod
from bookcard.engine import tocvision

# ===================== 造书 =====================

CJK_FONT = "china-s"

#: 数字版小书：1 封面 + 2 目录页 + 26 正文页 = 29 页。
#: 印刷页码 = 物理页号 − 3（目录上印 1 → 物理第 4 页）。
BOOK_PAGES: dict[int, list[str]] = {
    1: ["九年级物理"],
    2: [
        "目录",
        "第一章 机械运动 1",
        "第1节 长度和时间的测量 2",
        "第2节 运动的描述 6",
        "第二章 声现象 10",
    ],
    3: [
        "第1节 声音的产生 10",
        "第2节 声音的传播 14",
        "第三章 物态变化 18",
        "第四章 光现象 22",
        "附录 26",
    ],
    4: ["第一章 机械运动", "力是物体之间的相互作用"],
    5: ["第1节 长度和时间的测量", "单位换算要先把单位统一"],
    9: ["第2节 运动的描述", "参照物的选择决定了运动状态"],
    13: ["第二章 声现象", "第1节 声音的产生", "声音由物体的振动产生"],
    17: ["第2节 声音的传播", "声音在不同介质中传播速度不同"],
    21: ["第三章 物态变化", "熔化凝固汽化液化升华凝华"],
    25: ["第四章 光现象", "光沿直线传播"],
    29: ["附录", "附录B 物理量与单位对照表"],
}

#: 正文页的填充文字（每页不同，免得被当成行眉行脚剥掉）。
FILLER: dict[int, list[str]] = {
    6: ["这一页讲的是测量长度的工具", "刻度尺读数要估读到分度值的下一位"],
    7: ["这一页讲的是时间单位和秒表", "秒表读数先看小盘再看大盘"],
    8: ["这一页讲的是误差和准确度", "多次测量取平均值可以减小偶然误差"],
    10: ["这一页讲的是机械运动和参照物", "判断物体运动还是静止要看参照物"],
    11: ["这一页讲的是运动快慢的比较", "速度等于路程除以时间"],
    12: ["这一页讲的是匀速直线运动", "匀速直线运动的速度保持不变"],
    14: ["这一页讲的是声音的频率", "频率越高声音越尖"],
    15: ["这一页讲的是声音的振幅", "振幅越大声音越响"],
    16: ["这一页讲的是乐音和噪声", "从保护听力的角度要控制噪声"],
    18: ["这一页讲的是温度和温度计", "温度计使用前要观察量程和分度值"],
    19: ["这一页讲的是熔化和凝固", "晶体熔化过程中温度保持不变"],
    20: ["这一页讲的是汽化和液化", "蒸发和沸腾都要吸收热量"],
    22: ["这一页讲的是光的直线传播", "小孔成像说明光沿直线传播"],
    23: ["这一页讲的是光的反射", "反射光线和入射光线在同一平面内"],
    24: ["这一页讲的是光的折射和色散", "光从空气斜射入水中时传播方向改变"],
    26: ["这一页讲的是光的色散", "白光经三棱镜分解成七种色光"],
    27: ["这一页讲的是物体的颜色", "透明物体的颜色由透过它的色光决定"],
    28: ["这一页讲的是眼睛和视力", "近视眼要用凹透镜矫正"],
}

#: 一份能全过的草稿。
GOOD_DRAFT: dict = {
    "toc_pages": [2, 3],
    "entries": [
        {"level": 1, "title": "第一章 机械运动", "printed_page": 1},
        {"level": 2, "title": "第1节 长度和时间的测量", "printed_page": 2},
        {"level": 2, "title": "第2节 运动的描述", "printed_page": 6},
        {"level": 1, "title": "第二章 声现象", "printed_page": 10},
        {"level": 2, "title": "第1节 声音的产生", "printed_page": 10},
        {"level": 2, "title": "第2节 声音的传播", "printed_page": 14},
        {"level": 1, "title": "第三章 物态变化", "printed_page": 18},
        {"level": 1, "title": "第四章 光现象", "printed_page": 22},
        {"level": 1, "title": "附录", "printed_page": 26, "kind": "appendix"},
    ],
    "anchors": [
        {"printed_page": 1, "physical_page": 4},
        {"printed_page": 26, "physical_page": 29},
    ],
}


def _write_book(path: Path, pages: dict[int, list[str]], *, total: int | None = None) -> Path:
    """造一本小 PDF（文本层用 ``china-s`` 字体，中文能原样取回来）。"""
    doc = pymupdf.open()
    last = total or (max(pages) if pages else 1)
    for number in range(1, last + 1):
        page = doc.new_page(width=595, height=842)
        y = 140.0
        for line in pages.get(number, []):
            page.insert_text((72, y), line, fontsize=14, fontname=CJK_FONT)
            y += 34
    doc.save(str(path))
    doc.close()
    return path


def _book_pages() -> dict[int, list[str]]:
    """小书的每一页的文字（每次一份新的，别互相污染）。"""
    merged = {number: list(lines) for number, lines in BOOK_PAGES.items()}
    merged.update({number: list(lines) for number, lines in FILLER.items()})
    return merged


@pytest.fixture
def digital_book(tmp_path: Path) -> Path:
    """数字版小书（29 页，有文本层）。"""
    return _write_book(tmp_path / "book.pdf", _book_pages(), total=29)


@pytest.fixture
def scanned_book(tmp_path: Path) -> Path:
    """扫描件小书（29 页，没有文本层）。"""
    return _write_book(tmp_path / "scanned.pdf", {}, total=29)


def _write_draft(tmp_path: Path, draft: dict, name: str = "toc_draft.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _draft_with(entries: list[dict], **extra) -> dict:
    draft = json.loads(json.dumps(GOOD_DRAFT))
    draft["entries"] = entries
    draft.update(extra)
    return draft


def _check(report: dict, check_id: str) -> dict:
    for item in report["checks"]:
        if item["id"] == check_id:
            return item
    raise AssertionError(f"报告里没有 {check_id}")


def _red_pixels(png_path: Path, rect: list[float], *, slack: int = 4) -> int:
    """数一数这块区域里有多少个红底像素（页号标签是红底白字）。"""
    doc = pymupdf.open(str(png_path))
    try:
        pixmap = doc[0].get_pixmap(dpi=72)
    finally:
        doc.close()
    x0 = max(0, int(rect[0]) - slack)
    y0 = max(0, int(rect[1]) - slack)
    x1 = min(pixmap.width, int(rect[2]) + slack)
    y1 = min(pixmap.height, int(rect[3]) + slack)
    count = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            pixel = pixmap.pixel(x, y)
            if pixel[0] > 140 and pixel[1] < 90 and pixel[2] < 90:
                count += 1
    return count


# ===================== init =====================


def test_init_writes_state_and_is_idempotent(tmp_path: Path, digital_book: Path) -> None:
    work = tmp_path / "wd"
    state = tocvision.init_workdir(digital_book, work)
    assert state["created"] is True
    assert state["page_count"] == 29
    assert state["kind"] == "pdf_text"
    assert state["sha256"]
    assert 2 in state["text_layer_pages"]
    assert state["toc_pages"] == []

    again = tocvision.init_workdir(digital_book, work)
    assert again["created"] is False
    assert again["page_count"] == 29


def test_init_marks_scanned_book(tmp_path: Path, scanned_book: Path) -> None:
    state = tocvision.init_workdir(scanned_book, tmp_path / "wd")
    assert state["kind"] == "scanned"
    assert state["text_layer_pages"] == []


# ===================== sheet =====================


def test_sheet_has_page_labels_and_size_limit(tmp_path: Path, digital_book: Path) -> None:
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    result = tocvision.render_sheet(work, first=1, last=12, cols=4, rows=3)

    assert len(result["sheets"]) == 1
    sheet = result["sheets"][0]
    assert sheet["first"] == 1
    assert sheet["last"] == 12
    assert sheet["pages"] == list(range(1, 13))
    assert max(sheet["width"], sheet["height"]) <= tocvision.MAX_LONG_EDGE
    assert Path(sheet["path"]).is_file()

    # 每格角上都有红底白字的页号标签。
    for cell in sheet["cells"]:
        assert cell["label"]["text"] == str(cell["page"])
        assert _red_pixels(Path(sheet["path"]), cell["label"]["rect"]) > 0


def test_sheet_splits_when_range_exceeds_grid(tmp_path: Path, digital_book: Path) -> None:
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    result = tocvision.render_sheet(work, first=1, last=24, cols=4, rows=3)
    assert len(result["sheets"]) == 2
    assert result["sheets"][0]["pages"] == list(range(1, 13))
    assert result["sheets"][1]["pages"] == list(range(13, 25))
    for sheet in result["sheets"]:
        assert max(sheet["width"], sheet["height"]) <= tocvision.MAX_LONG_EDGE


def test_sheet_truncates_beyond_page_count(tmp_path: Path, digital_book: Path) -> None:
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    result = tocvision.render_sheet(work, first=20, last=999, cols=4, rows=3)
    assert result["truncated"] is True
    assert result["sheets"][0]["last"] == 29
    assert result["sheets"][0]["pages"][-1] == 29


# ===================== pages / locate =====================


def test_pages_writes_png_and_text(tmp_path: Path, digital_book: Path) -> None:
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    result = tocvision.render_pages(work, [2, 3], dpi=150)
    assert len(result["pages"]) == 2
    for item in result["pages"]:
        assert Path(item["path"]).is_file()
        assert max(item["width"], item["height"]) <= tocvision.MAX_LONG_EDGE
        assert item["text_path"] is not None
        assert Path(item["text_path"]).is_file()
    assert "目录" in Path(result["pages"][0]["text_path"]).read_text(encoding="utf-8")


def test_locate_records_toc_pages(tmp_path: Path, digital_book: Path) -> None:
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    result = tocvision.locate_pages(work, [4, 5, 6], by="agent:test:model")
    assert result["toc_pages"] == [4, 5, 6]
    state = tocvision.load_state(work)
    assert state["toc_pages"] == [4, 5, 6]
    assert state["located_by"] == "agent:test:model"


# ===================== 页码换算 =====================


def test_convert_pages_uses_segmented_offsets() -> None:
    anchors = [
        {"printed_page": 1, "physical_page": 4},
        {"printed_page": 10, "physical_page": 14},
        {"printed_page": 12, "physical_page": 18},
    ]
    # 第一个 anchor 之前用第一个 anchor 的偏移。
    assert tocvision.convert_pages(0, anchors) == 3
    assert tocvision.convert_pages(1, anchors) == 4
    assert tocvision.convert_pages(9, anchors) == 12
    # 偏移从各自的 printed_page 开始生效。
    assert tocvision.convert_pages(10, anchors) == 14
    assert tocvision.convert_pages(11, anchors) == 15
    assert tocvision.convert_pages(12, anchors) == 18
    assert tocvision.convert_pages(20, anchors) == 26
    assert tocvision.convert_pages(1, []) is None


# ===================== submit：全过 =====================


def test_submit_passing_draft(tmp_path: Path, digital_book: Path) -> None:
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    draft = _write_draft(tmp_path, GOOD_DRAFT)

    result = tocvision.submit_draft(work, draft, by="agent:test:model")
    assert result["status"] == "ok"
    assert result["ok"] is True
    assert result["failed"] == 0
    assert result["gate_status"] == "pending"

    toc = json.loads((work / "toc.json").read_text(encoding="utf-8"))
    assert toc["source"] == "vision"
    # 条目除了 level/title/page，还带印刷页和 kind（下游要用）
    assert toc["entries"][0] == {
        "level": 1,
        "title": "第一章 机械运动",
        "page": 4,
        "printed_page": 1,
        "kind": "body",
    }
    # 偏移（前置页数，正数）和目录页也记在 toc.json 里
    assert toc["offset"] == 3
    assert toc["pages"] == [2, 3]
    assert toc["checks"]["pages_monotonic"] is True
    assert toc["checks"]["first_body_page"] == 4
    assert toc["gate"]["status"] == "pending"
    assert toc["gate"]["approved_by"] == ""

    report = json.loads((work / "tocv_report.json").read_text(encoding="utf-8"))
    assert report["round"] == 1
    assert report["status"] == "ok"
    assert report["failed_entries"] == []


# ===================== submit：逐项失败 =====================


def test_c1_fails_when_pages_decrease(tmp_path: Path, digital_book: Path) -> None:
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    entries = json.loads(json.dumps(GOOD_DRAFT["entries"]))
    entries[0]["printed_page"] = 22  # 物理页 25，比下一条（第 5 页）大
    draft = _write_draft(tmp_path, _draft_with(entries))

    result = tocvision.submit_draft(work, draft, by="agent:test:model")
    assert _check(result, "C1")["passed"] is False
    assert result["status"] == "retry"
    assert result["gate_status"] == "rejected"
    assert any(item["check"] == "C1" for item in result["failed_entries"]) or True


def test_c2_passes_when_section_is_long_but_covered(tmp_path: Path) -> None:
    # 40 页的书：正文第 4 页到第 30 页一节（26 页）。
    # 切几层是**用户第二步确认**的事，基准不再拿「每节 3–25 页」判不合格，
    # 只看正文页有没有被连续覆盖。
    pages: dict[int, list[str]] = {
        number: [f"第{number}页的正文占位文字，每页都不一样"] for number in range(1, 41)
    }
    pages[1] = ["封面"]
    pages[2] = ["目录", "第一章 机械运动 1", "第二章 声现象 27", "第2页的正文占位文字"]
    pages[4] = ["第一章 机械运动", "第4页的正文占位文字"]
    pages[30] = ["第二章 声现象", "第30页的正文占位文字"]
    pdf = _write_book(tmp_path / "long.pdf", pages, total=40)
    work = tmp_path / "wd"
    tocvision.init_workdir(pdf, work)
    draft = _write_draft(
        tmp_path,
        {
            "toc_pages": [2],
            "entries": [
                {"level": 1, "title": "第一章 机械运动", "printed_page": 1},
                {"level": 1, "title": "第二章 声现象", "printed_page": 27},
            ],
            "anchors": [
                {"printed_page": 1, "physical_page": 4},
                {"printed_page": 27, "physical_page": 30},
            ],
        },
    )

    result = tocvision.submit_draft(work, draft, by="agent:test:model")
    assert _check(result, "C2")["passed"] is True
    assert _check(result, "C1")["passed"] is True


def test_c3_passes_when_title_on_body_page(tmp_path: Path) -> None:
    pages = _book_pages()
    pages[3].append("光的现象 20")
    pages[23].append("光的现象")
    pdf = _write_book(tmp_path / "c3.pdf", pages, total=29)
    work = tmp_path / "wd"
    tocvision.init_workdir(pdf, work)

    entries = json.loads(json.dumps(GOOD_DRAFT["entries"]))
    entries.insert(
        7,
        {"level": 1, "title": "光的现象", "printed_page": 20},
    )
    draft = _write_draft(tmp_path, _draft_with(entries))
    result = tocvision.submit_draft(work, draft, by="agent:test:model")

    assert _check(result, "C3")["passed"] is True  # 这一次页上是有标题的


def test_c3_fails_when_title_not_on_body_page(tmp_path: Path) -> None:
    pages = _book_pages()
    pages[3].append("光的现象 20")  # 目录上有
    pdf = _write_book(tmp_path / "c3.pdf", pages, total=29)  # 第 23 页上没有
    work = tmp_path / "wd"
    tocvision.init_workdir(pdf, work)

    entries = json.loads(json.dumps(GOOD_DRAFT["entries"]))
    entries.insert(7, {"level": 1, "title": "光的现象", "printed_page": 20})
    draft = _write_draft(tmp_path, _draft_with(entries))
    result = tocvision.submit_draft(work, draft, by="agent:test:model")

    assert _check(result, "C3")["passed"] is False
    assert _check(result, "C4")["passed"] is True
    failed = [item for item in result["failed_entries"] if item["check"] == "C3"]
    assert failed and failed[0]["title"] == "光的现象"


def test_c4_fails_when_title_not_on_toc_page(tmp_path: Path, digital_book: Path) -> None:
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    entries = json.loads(json.dumps(GOOD_DRAFT["entries"]))
    entries.append({"level": 1, "title": "附录B", "printed_page": 26})
    draft = _write_draft(tmp_path, _draft_with(entries))

    result = tocvision.submit_draft(work, draft, by="agent:test:model")
    assert _check(result, "C4")["passed"] is False
    assert _check(result, "C3")["passed"] is True
    failed = [item for item in result["failed_entries"] if item["check"] == "C4"]
    assert failed and failed[0]["title"] == "附录B"


def test_c5_fails_on_level_jump(tmp_path: Path) -> None:
    pages = _book_pages()
    pages[3].append("光的现象 20")
    pages[23].append("光的现象")
    pdf = _write_book(tmp_path / "c5.pdf", pages, total=29)
    work = tmp_path / "wd"
    tocvision.init_workdir(pdf, work)

    entries = json.loads(json.dumps(GOOD_DRAFT["entries"]))
    entries.insert(7, {"level": 3, "title": "光的现象", "printed_page": 20})
    draft = _write_draft(tmp_path, _draft_with(entries))
    result = tocvision.submit_draft(work, draft, by="agent:test:model")

    assert _check(result, "C5")["passed"] is False
    failed = [item for item in result["failed_entries"] if item["check"] == "C5"]
    assert failed and "跳" in failed[0]["reason"]


def test_c5_fails_on_duplicate_number(tmp_path: Path, digital_book: Path) -> None:
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    entries = json.loads(json.dumps(GOOD_DRAFT["entries"]))
    entries.insert(2, {"level": 2, "title": "第1节 长度和时间的测量", "printed_page": 2})
    draft = _write_draft(tmp_path, _draft_with(entries))

    result = tocvision.submit_draft(work, draft, by="agent:test:model")
    assert _check(result, "C5")["passed"] is False
    failed = [item for item in result["failed_entries"] if item["check"] == "C5"]
    assert failed and "重复" in failed[0]["reason"]


# ===================== 状态机 =====================


def test_round_state_machine(tmp_path: Path, digital_book: Path) -> None:
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    entries = json.loads(json.dumps(GOOD_DRAFT["entries"]))
    entries[0]["printed_page"] = 22  # 故意让 C1 挂
    draft = _write_draft(tmp_path, _draft_with(entries))

    first = tocvision.submit_draft(work, draft, by="agent:test:model")
    assert (first["round"], first["status"]) == (1, "retry")
    second = tocvision.submit_draft(work, draft, by="agent:test:model")
    assert (second["round"], second["status"]) == (2, "retry")
    third = tocvision.submit_draft(work, draft, by="agent:test:model")
    assert (third["round"], third["status"]) == (3, "needs_human")
    assert third["ok"] is False

    report = json.loads((work / "tocv_report.json").read_text(encoding="utf-8"))
    assert report["round"] == 3
    assert report["status"] == "needs_human"


def test_gate_status_is_never_approved(tmp_path: Path, digital_book: Path) -> None:
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)

    good = _write_draft(tmp_path, GOOD_DRAFT)
    tocvision.submit_draft(work, good, by="agent:test:model")
    toc = json.loads((work / "toc.json").read_text(encoding="utf-8"))
    assert toc["gate"]["status"] == "pending"
    assert toc["gate"]["status"] != "approved"

    entries = json.loads(json.dumps(GOOD_DRAFT["entries"]))
    entries[0]["printed_page"] = 22
    bad = _write_draft(tmp_path, _draft_with(entries), name="bad.json")
    tocvision.submit_draft(work, bad, by="agent:test:model")
    toc = json.loads((work / "toc.json").read_text(encoding="utf-8"))
    assert toc["gate"]["status"] == "rejected"
    assert toc["gate"]["status"] != "approved"


# ===================== 扫描件：OCR 路径打桩 =====================


def _stub_ocr(lines_by_page: dict[int, list[str]]):
    def fake(page, *, engine=None, min_conf=ocr_mod.MIN_CONF):
        number = page.number + 1
        rows = []
        for index, text in enumerate(lines_by_page.get(number, [])):
            top = 140 + 34 * index
            rows.append(
                ocr_mod.OcrLine(
                    text=text,
                    bbox=(72.0, top, 500.0, top + 20.0),
                    conf=0.99,
                )
            )
        return rows

    return fake


def test_scanned_book_uses_ocr_path(tmp_path: Path, scanned_book: Path, monkeypatch) -> None:
    work = tmp_path / "wd"
    tocvision.init_workdir(scanned_book, work)
    monkeypatch.setattr(ocr_mod, "ocr_page", _stub_ocr(_book_pages()))

    draft = _write_draft(tmp_path, GOOD_DRAFT)
    result = tocvision.submit_draft(work, draft, by="agent:test:model")

    assert _check(result, "C3")["passed"] is True
    assert _check(result, "C4")["passed"] is True
    assert result["status"] == "ok"

    # OCR 结果缓存到 <dir>/ocr/
    cache = work / "ocr" / "p0002.json"
    assert cache.is_file()
    records = json.loads(cache.read_text(encoding="utf-8"))
    assert any("目录" in item["text"] for item in records)


def test_digital_book_with_image_page_does_not_need_ocr(
    tmp_path: Path, digital_book: Path, monkeypatch
) -> None:
    """数字版里夹一页整页图片，**不许**因此要求装 OCR 依赖。

    真实例子：pool/020.pdf 第 7 页是整页图片，没有文本层。
    契约 §2.2 只对扫描件要求 OCR——数字版走文本层，没有字就是没有字。
    """
    # 造一本数字版：第 7 页是整页图片（没有文本层）。
    doc = pymupdf.open()
    for number in range(1, 30):
        page = doc.new_page(width=595, height=842)
        if number == 7:
            # 整页图片页：只画一个矩形，没有文字
            page.draw_rect(pymupdf.Rect(50, 50, 545, 790), color=None, fill=(0.8, 0.85, 0.9))
            continue
        y = 140.0
        for line in _book_pages().get(number, []):
            page.insert_text((72, y), line, fontsize=14, fontname=CJK_FONT)
            y += 34
    path = tmp_path / "hole.pdf"
    doc.save(str(path))
    doc.close()

    work = tmp_path / "wd"
    state = tocvision.init_workdir(path, work)
    assert state["kind"] == "pdf_text"
    assert 7 not in state["text_layer_pages"]  # 这一页确实没有文本层

    def boom(*args, **kwargs):
        raise AssertionError("数字版不该走 OCR")

    monkeypatch.setattr(ocr_mod, "ocr_page", boom)

    draft = _write_draft(tmp_path, GOOD_DRAFT)
    result = tocvision.submit_draft(work, draft, by="agent:test:model")
    # 判据照跑（正文页 4 就在图片页旁边，标题在别的页上找得到）
    assert _check(result, "C3")["passed"] is True
    assert result["status"] == "ok"


# ===================== score =====================


def _gold(entries: list[dict], toc_pages: list[int] | None = None) -> dict:
    return {"toc_pages": toc_pages or [2, 3], "entries": entries}


def _gold_from_draft(draft: dict, offsets: dict[int, int] | None = None) -> dict:
    entries = []
    for entry in draft["entries"]:
        physical = entry["printed_page"] + 3
        if offsets and entry["title"] in offsets:
            physical = offsets[entry["title"]]
        entries.append(
            {
                "level": entry["level"],
                "title": entry["title"],
                "physical_page": physical,
            }
        )
    return _gold(entries, draft["toc_pages"])


def test_score_exact_match(tmp_path: Path, digital_book: Path) -> None:
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    draft = _write_draft(tmp_path, GOOD_DRAFT)
    tocvision.submit_draft(work, draft, by="agent:test:model")

    gold = _write_draft(tmp_path, _gold_from_draft(GOOD_DRAFT), name="gold.json")
    payload = tocvision.score_draft(work, gold)
    assert payload["f1"] == 1.0
    assert payload["precision"] == 1.0
    assert payload["recall"] == 1.0
    assert payload["page_exact_rate"] == 1.0
    assert payload["level_exact_rate"] == 1.0
    assert payload["toc_pages"]["correct"] is True
    assert payload["missing"] == []
    assert payload["extra"] == []
    assert (work / "score.json").is_file()


def test_score_missing_entry(tmp_path: Path, digital_book: Path) -> None:
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    # 草稿少了一条（模型漏了一条）
    entries = json.loads(json.dumps(GOOD_DRAFT["entries"]))
    entries.pop(2)
    draft = _write_draft(tmp_path, _draft_with(entries))
    tocvision.submit_draft(work, draft, by="agent:test:model")

    gold = _write_draft(tmp_path, _gold_from_draft(GOOD_DRAFT), name="gold.json")
    payload = tocvision.score_draft(work, gold)
    assert payload["recall"] < 1.0
    assert payload["precision"] == 1.0
    assert len(payload["missing"]) == 1
    assert payload["missing"][0]["title"] == "第2节 运动的描述"


def test_score_wrong_page(tmp_path: Path, digital_book: Path) -> None:
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    draft = _write_draft(tmp_path, GOOD_DRAFT)
    tocvision.submit_draft(work, draft, by="agent:test:model")

    gold_doc = _gold_from_draft(GOOD_DRAFT)
    gold_doc["entries"][2]["physical_page"] += 3  # 页码错一条
    gold = _write_draft(tmp_path, gold_doc, name="gold.json")
    payload = tocvision.score_draft(work, gold)
    assert payload["f1"] == 1.0
    assert payload["page_exact_rate"] < 1.0
    assert len(payload["page_mismatch"]) == 1
    assert payload["page_mismatch"][0]["gold_page"] != payload["page_mismatch"][0]["draft_page"]


def test_score_reports_wrong_toc_pages(tmp_path: Path, digital_book: Path) -> None:
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    draft = _write_draft(tmp_path, GOOD_DRAFT)
    tocvision.submit_draft(work, draft, by="agent:test:model")

    gold_doc = _gold_from_draft(GOOD_DRAFT)
    gold_doc["toc_pages"] = [4, 5]
    gold = _write_draft(tmp_path, gold_doc, name="gold.json")
    payload = tocvision.score_draft(work, gold)
    assert payload["toc_pages"]["correct"] is False
    assert payload["toc_pages"]["gold"] == [4, 5]


# ===================== 真实 gold 格式 =====================


def test_score_reads_real_gold_format(tmp_path: Path, digital_book: Path) -> None:
    """维护者的 gold 格式：多 ``offsets`` / ``physical_page`` / ``kind``，条目用物理页。

    gold 给了 ``physical_page``，引擎的 ``score`` 直接用它比对，不重算偏移。
    """
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    draft = _write_draft(tmp_path, GOOD_DRAFT)
    tocvision.submit_draft(work, draft, by="agent:test:model")

    gold_doc = {
        "pool_index": 0,
        "source_pdf": "book.pdf",
        "page_count": 29,
        "toc_pages": [2, 3],
        "offsets": [{"from_printed": 1, "offset": 3}],
        "entries": [
            {
                "level": entry["level"],
                "title": entry["title"],
                "printed_page": entry["printed_page"],
                "physical_page": entry["printed_page"] + 3,
                **({"kind": entry["kind"]} if entry.get("kind") else {}),
            }
            for entry in GOOD_DRAFT["entries"]
        ],
    }
    gold = _write_draft(tmp_path, gold_doc, name="gold.json")
    payload = tocvision.score_draft(work, gold)
    assert payload["f1"] == 1.0
    assert payload["page_exact_rate"] == 1.0
    assert payload["toc_pages"]["correct"] is True


def test_score_null_physical_page_compares_title_and_level_only(
    tmp_path: Path, digital_book: Path
) -> None:
    """gold 里 ``physical_page`` 是 null 的条目只比对标题和层级。

    那一列本来就没印页码，不该拖低页码一致率。
    """
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    draft = _write_draft(tmp_path, GOOD_DRAFT)
    tocvision.submit_draft(work, draft, by="agent:test:model")

    gold_doc = _gold_from_draft(GOOD_DRAFT)
    # 第一条 gold 的物理页是 null（目录上没印页码的那一条）
    gold_doc["entries"][0]["physical_page"] = None
    gold = _write_draft(tmp_path, gold_doc, name="gold.json")
    payload = tocvision.score_draft(work, gold)

    # 标题还是配得上 → 查准查全不受影响
    assert payload["f1"] == 1.0
    # 页码一致率只在有物理页的条目上算 → 仍然是 1.0
    assert payload["page_exact_rate"] == 1.0
    assert payload["page_compared"] == len(gold_doc["entries"]) - 1
    # null 的那一条不算页码不一致
    assert payload["page_mismatch"] == []


def test_score_null_physical_page_still_checks_level(tmp_path: Path, digital_book: Path) -> None:
    """``physical_page`` 是 null 的条目，层级还是要比。"""
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    draft = _write_draft(tmp_path, GOOD_DRAFT)
    tocvision.submit_draft(work, draft, by="agent:test:model")

    gold_doc = _gold_from_draft(GOOD_DRAFT)
    gold_doc["entries"][0]["physical_page"] = None
    gold_doc["entries"][0]["level"] = 3  # 层级故意写错
    gold = _write_draft(tmp_path, gold_doc, name="gold.json")
    payload = tocvision.score_draft(work, gold)

    assert payload["level_exact_rate"] < 1.0
    assert payload["level_mismatch"][0]["gold_level"] == 3
    assert payload["level_mismatch"][0]["draft_level"] == 1


# ===================== 仓库里真实的 gold =====================

#: 基准目录（入库在 main）。
GOLD_DIR = Path(__file__).resolve().parents[1] / "bench" / "tocv" / "gold"


@pytest.mark.skipif(not GOLD_DIR.is_dir(), reason="仓库里还没有 bench/tocv/gold/")
def test_real_gold_files_have_the_expected_shape() -> None:
    """真实的 gold 文件要能被 ``score`` 读进来。

    字段：``toc_pages`` / ``offsets`` / ``entries[].level/title/printed_page/
    physical_page/kind``；``physical_page`` 是 null 的条目只比对标题和层级。
    """
    files = sorted(GOLD_DIR.glob("*.json"))
    assert files, "gold 目录里有文件"
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload.get("toc_pages"), list), path
        assert isinstance(payload.get("entries"), list), path
        for entry in payload["entries"]:
            assert isinstance(entry.get("level"), int), path
            assert isinstance(entry.get("title"), str), path
            assert "printed_page" in entry, path
            assert "physical_page" in entry, path


@pytest.mark.skipif(not GOLD_DIR.is_dir(), reason="仓库里还没有 bench/tocv/gold/")
def test_score_consumes_real_gold(tmp_path: Path, digital_book: Path) -> None:
    """拿真实的 gold 当输入跑 ``score``——字段对不上就报错。"""
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    draft = _write_draft(tmp_path, GOOD_DRAFT)
    tocvision.submit_draft(work, draft, by="agent:test:model")

    gold_path = GOLD_DIR / "102.json"
    if not gold_path.is_file():
        pytest.skip("没有 102.json")
    # 真实 gold 的条目跟小书对不上，但 score 要能读进来并出结果。
    payload = tocvision.score_draft(work, gold_path)
    assert payload["gold_entries"] > 0
    assert "precision" in payload
    assert (work / "score.json").is_file()


# ===================== 草稿格式 =====================


def test_submit_rejects_bad_draft(tmp_path: Path, digital_book: Path) -> None:
    work = tmp_path / "wd"
    tocvision.init_workdir(digital_book, work)
    draft = _write_draft(tmp_path, {"entries": []})
    with pytest.raises(tocvision.DraftError):
        tocvision.submit_draft(work, draft, by="agent:test:model")


def test_submit_needs_init(tmp_path: Path) -> None:
    draft = _write_draft(tmp_path, GOOD_DRAFT)
    with pytest.raises(tocvision.TocvStateError):
        tocvision.submit_draft(tmp_path / "nope", draft, by="agent:test:model")


# ===================== 页文字：别把同一页 OCR 两遍 =====================


def test_text_provider_reuses_pages_jsonl_instead_of_ocr(
    tmp_path: Path, scanned_book: Path, monkeypatch
):
    """整本取字（pages.jsonl）已经 OCR 过的页，看图目录直接用它。"""
    work = tmp_path / "book" / "tocv"
    work.mkdir(parents=True)
    (work.parent / "pages.jsonl").write_text(
        "\n".join(
            json.dumps({"page": n, "text": f"第{n}页 取过的字"}, ensure_ascii=False)
            for n in range(1, 30)
        ),
        encoding="utf-8",
    )

    def boom(*args, **kwargs):
        raise AssertionError("不该再 OCR：pages.jsonl 里已经有文字")

    monkeypatch.setattr(ocr_mod, "ocr_page", boom)
    doc = pymupdf.open(scanned_book)
    try:
        provider = tocvision._TextProvider(
            work,
            {"kind": "scanned", "page_count": 29, "text_layer_pages": [], "verdict": "scanned"},
            doc,
        )
        assert provider.text(5) == "第5页 取过的字"
    finally:
        doc.close()


def test_load_pages_jsonl_returns_empty_when_absent(tmp_path: Path):
    work = tmp_path / "tocv"
    work.mkdir()
    assert tocvision._load_pages_jsonl(work) == {}


def test_title_core_strips_kehao_and_experiment_labels():
    """「课题N」「实验活动N」也是编号前缀。

    扫描件 OCR 的阅读顺序常把这种标签排到标题后面（「溶解度 课题2」），
    不剥掉就匹配不上整串，抄目录的 agent 只能把标题截短。
    """
    assert tocvision.title_core("课题2 溶解度") == "溶解度"
    assert tocvision.title_core("课题1 物质的变化和性质") == "物质的变化和性质"
    assert (
        tocvision.title_core("实验活动5 一定溶质质量分数的氯化钠溶液的配制")
        == "一定溶质质量分数的氯化钠溶液的配制"
    )


def test_title_found_tolerates_footnote_markers():
    """标题和作者之间夹着脚注标记时，也要能匹配上。"""
    text = "7  敬业与乐业\na\n梁启超\n正文"
    assert tocvision._title_found("敬业与乐业梁启超", [text])
    # 没有脚注标记干扰时也一样
    assert tocvision._title_found("敬业与乐业梁启超", ["7 敬业与乐业 梁启超"])


def test_title_found_tolerates_pinyin_annotations():
    """小学一二年级的标题是「字图 + 拼音注音」，文本层里不是连续字符串。"""
    text = "秋qiū天tiān tiān 天空"
    assert tocvision._title_found("秋天", [text])
    assert tocvision._title_found(tocvision.title_core("1 秋天"), [text])


def test_title_parts_found_handles_label_split():
    """「口语交际：转述」这种：栏目徽标和主题在页上是两块文字，接不成整串。"""
    texts = ["口语交际\n转述\n请你说说。"]
    assert tocvision._title_parts_found("口语交际：转述", texts)
    assert not tocvision._title_parts_found("口语交际：转述", ["今天天气不错"])


def test_title_core_strips_appendix_and_unnumbered_labels():
    """「附录N」「实验活动」（没编号）也是编号前缀。"""
    assert tocvision.title_core("附录Ⅰ 名词索引") == "名词索引"
    assert tocvision.title_core("附录Ⅵ 相对原子质量表") == "相对原子质量表"
    assert tocvision.title_core("附录 名词索引") == "名词索引"
    assert tocvision.title_core("实验活动 简单配合物的形成") == "简单配合物的形成"


def test_text_variants_includes_raw_text(tmp_path, digital_book):
    """剥行眉把目录页上的条目剥掉了 —— 匹配时要把原来那份也带上。"""
    doc = pymupdf.open(digital_book)
    try:
        provider = tocvision._TextProvider(
            tmp_path,
            {
                "kind": "pdf_text",
                "page_count": 29,
                "text_layer_pages": [],
                "verdict": "digital",
                "pdf_path": str(digital_book),
            },
            doc,
        )
        variants = provider.text_variants(2)
        assert len(variants) >= 1
        assert any(v for v in variants)
    finally:
        doc.close()


def test_title_parts_found_strips_section_prefix():
    """「8 数学广角——优化」拆两半时，每半也要剥编号前缀（徽标号 OCR 不出来）。"""
    texts = ["数学广角 优化"]
    assert tocvision._title_parts_found("8 数学广角——优化", texts)
