"""把一本书拆成「页面 + 印刷页码 + 目录」的中间结果。

``text`` / ``toc`` / ``sections`` 三步共用这一份分析，保证口径一致：
页面怎么清、印刷页码怎么找、目录页是哪几页、正文从哪一页开始 —— 只有一处。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from statistics import median
from typing import Any

from bookcard.engine import common, extract, ocr, tocparse
from bookcard.probe import rules

#: 一页里最大字号 ≥ 全页中位字号这么多倍，才算「这一页有标题」。
HEADING_PAGE_RATIO = 1.30

#: 找正文首页时往后看多少页。
HEADING_LOOKAHEAD = 12

#: 页码也可能印在**页眉**（版心上方外侧），不是页脚。
#:
#: 有些教材（沪科版物理八年级）就是这样：页码印在版心左上/右上的外侧，
#: 页脚一个字都没有。页脚那一套找不到偏移时，用这个补上。
HEADER_PAGE_BAND = 0.14


def _median(values: list[float]) -> float:
    return float(median(values)) if values else 0.0


def _header_page_offset(pages: list[dict]) -> int | None:
    """页眉带的印刷页码 → 偏移（页脚没有页码的教材靠这个）。

    口径跟页脚那套一样：独立数字 span、左右分槽算众数，
    至少 :data:`bookcard.probe.rules.PAGE_NUMBER_MIN_PAGES` 页支持才算。
    """
    slots: dict[str, dict[int, set[int]]] = {}
    for page in pages:
        height = page["height"]
        width = page["width"]
        number = page["number"]
        for line in page["lines"]:
            for span in line.get("spans") or []:
                text = rules.normalize_line(span["text"])
                if not rules.PAGE_NUMBER_RE.match(text):
                    continue
                if span["bbox"][3] > HEADER_PAGE_BAND * height:
                    continue
                center_x = (span["bbox"][0] + span["bbox"][2]) / 2
                side = "left" if center_x < width / 2 else "right"
                slots.setdefault(side, {}).setdefault(number, set()).add(int(text) - number)

    best: tuple[int, int] | None = None
    for per_page in slots.values():
        votes: dict[int, int] = {}
        for offsets in per_page.values():
            for offset in offsets:
                votes[offset] = votes.get(offset, 0) + 1
        if not votes:
            continue
        offset, support = max(votes.items(), key=lambda item: (item[1], -item[0]))
        if support < rules.PAGE_NUMBER_MIN_PAGES:
            continue
        if best is None or support > best[0]:
            best = (support, offset)
    return best[1] if best else None


def _is_heading_page(page: dict, ratio: float = HEADING_PAGE_RATIO) -> bool:
    """这一页有没有大标题（相对本页自己的正文字号）。"""
    sizes = [line["size"] for line in page["lines"] if line["size"] > 0]
    if not sizes:
        return False
    return max(sizes) >= ratio * _median(sizes)


def _first_heading_page(pages: list[dict], start: int) -> int | None:
    """从 ``start`` 开始往后找第一个有大标题的页。"""
    for number in range(start, min(len(pages), start + HEADING_LOOKAHEAD) + 1):
        page = pages[number - 1]
        if page["class"] != "text":
            continue
        if _is_heading_page(page):
            return number
    return None


def _trim_trailing_blanks(pages: list[dict], start: int, end: int) -> int:
    """把区间末尾的空白页去掉。"""
    while end >= start and pages[end - 1]["class"] != "text":
        end -= 1
    return end


def analyze_ocr_workdir(work_dir: str | Path) -> dict:
    """扫描件：从工作目录的产物做分析（**不重跑 OCR**）。

    扫描件没有文本层，``analyze`` 读 PDF 只会读到空白页——目录就永远认不出来。
    这里改用已经算好的产物重放一遍：

    - ``pages.jsonl``：页文字、印刷页码、页类型；
    - ``ocr/p<4 位页号>.json``：行框（目录解析、版式兜底都要行框）。

    口径和 :func:`analyze_pages` 一样：目录页怎么找、条目怎么解析、
    正文首页怎么定，都是同一份代码。
    """
    work_dir = Path(work_dir)
    rows = common.read_jsonl(work_dir / "pages.jsonl")

    pages: list[dict] = []
    per_page: dict[int, str] = {}
    for row in rows:
        number = int(row["page"])
        lines = _ocr_lines_from_records(work_dir, number)
        text = str(row.get("text") or "")
        pages.append(
            {
                "number": number,
                "width": float(row.get("width") or 0.0),
                "height": float(row.get("height") or 0.0),
                "lines": lines,
                "text": text,
                "class": "text" if text.strip() else "empty",
                # 页类型（契约 §2）：版式兜底要跳过封面／前置页。
                "kind": str(row.get("kind") or ""),
            }
        )
        printed_page = row.get("printed_page")
        if printed_page is not None and str(printed_page).strip():
            per_page[number] = str(printed_page)

    printed = {
        "per_page": per_page,
        "offset": _printed_offset(per_page),
        "position": None,
        "anchors": {},
    }

    return _analyze_from_pages(pages, printed, [], text_origin="ocr")


def _ocr_lines_from_records(work_dir: Path, number: int) -> list[dict]:
    """``ocr/pXXXX.json`` → 引擎统一的行结构。"""
    path = work_dir / ocr.OCR_DIRNAME / ocr.ocr_file_name(number)
    if not path.is_file():
        return []
    payload = common.read_json(path)
    if not isinstance(payload, list):
        return []
    lines: list[dict] = []
    for index, record in enumerate(payload):
        if not isinstance(record, dict):
            continue
        bbox = tuple(float(value) for value in (record.get("bbox") or (0, 0, 0, 0))[:4])
        text = str(record.get("text") or "")
        size = max(0.0, bbox[3] - bbox[1])
        lines.append(
            {
                "text": text,
                "bbox": bbox,
                "size": size,
                "block": index,
                "conf": float(record.get("conf") or 0.0),
                "spans": ocr.line_spans(text, bbox, size),
            }
        )
    return lines


def _printed_offset(per_page: dict[int, str]) -> int | None:
    """印刷页码 → 物理页号的偏移（众数；至少 3 页支持）。"""
    votes: dict[int, int] = {}
    for number, printed in per_page.items():
        try:
            offset = int(printed) - number
        except (TypeError, ValueError):
            continue
        votes[offset] = votes.get(offset, 0) + 1
    if not votes:
        return None
    best = max(votes.items(), key=lambda item: (item[1], -item[0]))
    if best[1] < rules.PAGE_NUMBER_MIN_PAGES:
        return None
    return best[0]


def analyze(pdf_path: str | Path) -> dict:
    """分析一本书（用 PDF 的文本层）。

    返回::

        {"pages": [...], "printed": {...}, "watermarks": [...],
         "toc_range": [4, 6], "toc_entries": [...], "offset": -7,
         "first_body_page": 8, "last_body_page": 206, "body_font": 12.6}

    ``toc_entries`` 的页码已经是**物理页号**（印刷页码减偏移）。
    """
    return analyze_pages(extract.extract_pages(pdf_path), text_origin="pdf_text")


def analyze_ocr(
    pdf_path: str | Path,
    *,
    engine: Any = None,
    on_page: Callable[[int, float, int], None] | None = None,
) -> dict:
    """分析一本书（扫描件：整本走本地 OCR，契约 §2.2）。

    页的结构和 :func:`analyze` 一样，只是文字来自 OCR。``info["ocr"]``
    里有每页耗时。
    """
    pages, stats = ocr.ocr_pages(pdf_path, engine=engine, on_page=on_page)
    info = analyze_pages(
        pages,
        text_origin="ocr",
        drop_page_number_line=True,
        keep_printed_outliers=True,
        reorder=ocr.reorder_page_lines,
    )
    info["ocr"] = stats
    return info


def analyze_pages(
    pages: list[dict],
    *,
    text_origin: str = "pdf_text",
    drop_page_number_line: bool = False,
    keep_printed_outliers: bool = False,
    reorder: Callable[[list[dict]], list[dict]] | None = None,
) -> dict:
    """对已经抽好的页做分析（``text`` / ``toc`` 两步共用这一份口径）。

    ``text_origin`` 只是记在返回值里（``pdf_text`` 或 ``ocr``）。
    ``drop_page_number_line`` 传给行眉行脚清理——OCR 页的行脚页码在行尾，
    要单独去掉（见 :func:`bookcard.engine.extract.strip_running_lines`）。
    ``keep_printed_outliers`` 让漏页的扫描件也能剥掉行眉行脚（见
    :func:`bookcard.engine.extract.detect_printed_pages`）。
    ``reorder`` 是排阅读顺序的函数（OCR 页用，PDF 页不用——PyMuPDF 的块顺序
    已经是阅读顺序了）。
    """
    # ---- 水印：先找出来，剥掉 ----
    watermarks = extract.find_watermarks(pages)
    for page in pages:
        raw_lines = page["lines"]
        stripped = extract.strip_watermark_lines(raw_lines, watermarks)
        page["had_watermark"] = len(stripped) != len(raw_lines)
        page["lines"] = stripped
        page["raw_text"] = extract.page_text(raw_lines)

    # ---- 印刷页码（要赶在剥行眉行脚之前，页码那行的位置是页脚锚点）----
    printed = extract.detect_printed_pages(
        pages,
        keep_outliers=keep_printed_outliers,
        # OCR 行常常「16第一单元」这样数字后面直接跟汉字，认不出页码
        # ⇒ 那页的行脚剥不掉。只在 OCR 路径上开这个口径。
        head_number=text_origin == "ocr",
    )

    # ---- 行眉行脚 ----
    for page, lines in zip(
        pages,
        extract.strip_running_lines(
            pages, printed, drop_page_number_line=drop_page_number_line
        ),
        strict=True,
    ):
        page["lines"] = lines
        page["class"] = rules.classify_page(
            page["raw_text"],
            extract.page_text(lines),
            had_watermark=page["had_watermark"],
        )

    # ---- 阅读顺序 ----
    # OCR 页要**剥完水印和行眉行脚再排**：页脚那一行在页面最下面，
    # 不剥掉的话它会跟正文抢栏（教材页脚有页码，位置偏左）。
    if reorder is not None:
        for page in pages:
            page["lines"] = reorder(page["lines"])

    for page in pages:
        page["text"] = extract.page_text(page["lines"])

    return _analyze_from_pages(
        pages,
        printed,
        watermarks,
        text_origin=text_origin,
    )


def _analyze_from_pages(
    pages: list[dict],
    printed: dict,
    watermarks: list[str],
    *,
    text_origin: str = "pdf_text",
) -> dict:
    """页已经清好了，算目录、正文首页、末页这些（两边共用这一份口径）。

    数字版书和扫描件都走这里——目录页怎么找、条目怎么解析、正文从哪页开始，
    只有一处实现。
    """
    body_font = _median([line["size"] for page in pages for line in page["lines"]])

    # ---- 目录页区间 ----
    detected = tocparse.find_toc_pages(pages)
    toc_range: list[int] = []
    if detected:
        start = min(detected)
        heading_page = _first_heading_page(pages, max(detected) + 1)
        # 目录往后连了几页（``find_toc_pages`` 已经把续页并进来了），
        # 正文的第一张大字页就是目录的尾巴——它前面那些页都是目录。
        # 找不到大字页就以认出来的目录页为准，别把整本书都算成目录。
        end = (heading_page - 1) if heading_page else max(detected)
        end = _trim_trailing_blanks(pages, start, end)
        toc_range = list(range(start, end + 1))

    # ---- 印刷页码 → 物理页号的偏移 ----
    offset = printed["offset"]
    if offset is None:
        # 页脚没有页码的教材（页码印在页眉）靠这一条补上。
        offset = _header_page_offset(pages)
        if offset is not None:
            printed = {**printed, "offset": offset}

    # ---- 目录条目（印刷页码 → 物理页号）----
    printed_entries: list[dict] = []
    entries: list[dict] = []
    if toc_range:
        printed_entries = tocparse.parse_printed_toc([pages[n - 1] for n in toc_range])
        if offset is not None:
            entries = [
                {
                    "level": entry["level"],
                    "title": entry["title"],
                    "page": entry["page"] - offset,
                    "printed_page": entry["page"],
                }
                for entry in printed_entries
            ]

    # ---- 正文首页 / 末页 ----
    candidates: list[int] = []
    if entries:
        candidates.append(min(entry["page"] for entry in entries))
    if toc_range:
        heading_page = _first_heading_page(pages, max(toc_range) + 1)
        if heading_page:
            candidates.append(heading_page)
    first_body_page = min(candidates) if candidates else 1
    last_body_page = max(
        (page["number"] for page in pages if page["class"] == "text"),
        default=first_body_page,
    )

    return {
        "pages": pages,
        "printed": printed,
        "watermarks": watermarks,
        "toc_range": toc_range,
        "toc_entries": entries,
        "printed_entries": printed_entries,
        "offset": offset,
        "first_body_page": first_body_page,
        "last_body_page": last_body_page,
        "body_font": body_font,
        "text_origin": text_origin,
    }
