"""逐页抽取：页面几何、水印与页眉页脚清理、印刷页码。

判定口径尽量复用 :mod:`bookcard.probe.rules`（水印、页面类型、语言、页码正则、
书签分类）；只有探针口径偏窄的地方在这里放宽：

- 印刷页码带取页高底部 12%（物理教材的页码落在 0.91 处，探针的 0.08 会漏掉）。
- 页眉页脚除了水印，还认「反复出现的行眉/行脚」。
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from bookcard.engine.breaks import join_lines
from bookcard.probe import rules

#: 页眉带：页面顶部这么多比例以内算页眉。
HEADER_BAND = 0.05

#: 页脚带：默认取页面底部这么多比例（该页没印页码、没法锚定时用）。
FOOTER_BAND = 0.10

#: 页脚带锚定在印刷页码那一行时，上下浮动多少比例的页高。
FOOTER_ANCHOR_SLACK = 0.02

#: 同一行在多少页上重复出现才算「页眉/页脚」（行眉行脚每页都变，阈值高了会漏）。
RUNNING_MIN_PAGES = 3

#: 印刷页码候选带：页面底部这么多比例以内。
PRINTED_BAND = 0.12

#: 行首的独立数字 —— 印刷页码（「144 物理 九年级」这种页码跟行脚连在一行的也算）。
LEADING_NUMBER_RE = re.compile(r"^\s*([0-9]{1,4})(?=\s|$)")

#: 行内任意位置出现的独立数字（OCR 页的行脚：页码跟在行眉后面，
#: 比如「第一章 认识生物11」——这种行首没有数字，认不出来）。
CONTAINS_NUMBER_RE = re.compile(r"(?<![0-9])([0-9]{1,4})(?![0-9])")

#: 判「这一行是页码」还要看位置：版心左右的页码槽。
OUTER_MARGIN_RATIO = 0.22


def extract_pages(pdf_path: str | Path) -> list[dict]:
    """抽全书页面几何。

    每页::

        {"number": 12, "width": 595.3, "height": 841.9,
         "lines": [{"text": "...", "bbox": (x0, y0, x1, y1), "size": 12.0,
                    "block": 0,
                    "spans": [{"text": "...", "bbox": (...), "size": 12.0}]}]}

    ``block`` 是文本块序号（PyMuPDF 的 text block，按页内出现顺序从 0 起）——
    契约 §2.1 的段落界判据 4（下一行属于另一个块）要用。
    """
    doc = pymupdf.open(pdf_path)
    try:
        pages: list[dict] = []
        for index in range(doc.page_count):
            page = doc[index]
            lines: list[dict] = []
            block_index = -1
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") != 0:  # 0 = 文本块，1 = 图片
                    continue
                block_index += 1
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    text = "".join(span.get("text", "") for span in spans)
                    if not text.strip():
                        continue
                    lines.append(
                        {
                            "text": text,
                            "bbox": tuple(float(v) for v in line.get("bbox", (0.0, 0.0, 0.0, 0.0))),
                            "size": max(
                                (float(span.get("size", 0.0)) for span in spans),
                                default=0.0,
                            ),
                            "block": block_index,
                            "spans": [
                                {
                                    "text": span.get("text", ""),
                                    "bbox": tuple(
                                        float(v) for v in span.get("bbox", (0.0, 0.0, 0.0, 0.0))
                                    ),
                                    "size": float(span.get("size", 0.0)),
                                }
                                for span in spans
                            ],
                        }
                    )
            pages.append(
                {
                    "number": index + 1,
                    "width": float(page.rect.width),
                    "height": float(page.rect.height),
                    "lines": lines,
                }
            )
        return pages
    finally:
        doc.close()


def find_watermarks(pages: list[dict]) -> list[str]:
    """找出水印行（口径同探针：出现在 ≥30% 页上的行）。"""
    return rules.find_watermark_lines(
        [[line["text"] for line in page["lines"]] for page in pages],
        threshold=0.30,
    )


def strip_watermark_lines(lines: list[dict], watermarks: list[str]) -> list[dict]:
    """剥掉水印：整行是水印就丢，行内含水印就替换掉。

    逐行处理（而不是整页替换）是为了保住行几何 —— 目录解析要用行位置。
    """
    if not watermarks:
        return list(lines)

    kept: list[dict] = []
    for line in lines:
        text = rules.strip_watermarks(line["text"], watermarks)
        if not text.strip():
            continue
        kept.append({**line, "text": text})
    return kept


def _in_header_band(line: dict, height: float) -> bool:
    return line["bbox"][3] <= HEADER_BAND * height


def _in_footer_band(line: dict, height: float, anchor: float | None) -> bool:
    if anchor is None:
        return line["bbox"][1] >= (1.0 - FOOTER_BAND) * height
    return line["bbox"][1] >= anchor - FOOTER_ANCHOR_SLACK * height


def _number_in_line(line: dict, *, head_number: bool = False) -> tuple[int, float] | None:
    """这一行里的印刷页码：``(页码, 页码的 x 位置)``。

    页码可能在行首（``144 物理 九年级``），也可能在行尾（``第十八章 电功率103``）。

    ``head_number``：行首数字后面**直接跟汉字**时也算（``16第一单元 生物和生物圈``）。
    OCR 出来的行常常没空格，:data:`LEADING_NUMBER_RE` 认不出——那一行的印刷页码
    就丢了，行脚剥不掉，页文字里留一条噪声。只在 OCR 路径上开。
    """
    match = LEADING_NUMBER_RE.match(line["text"])
    if match:
        return int(match.group(1)), line["bbox"][0]

    spans = line.get("spans") or []
    if spans:
        if head_number:
            first = spans[0]
            head = rules.normalize_line(first["text"])
            if rules.PAGE_NUMBER_RE.match(head):
                return int(head), first["bbox"][0]
        last = spans[-1]
        tail = rules.normalize_line(last["text"])
        if rules.PAGE_NUMBER_RE.match(tail):
            return int(tail), last["bbox"][0]
    return None


def _page_number_candidates(page: dict, *, head_number: bool = False) -> list[tuple[int, float]]:
    """这一页印的页码候选：``[(印刷页码, 该行顶边 y), ...]``。

    判据：行首或行尾是独立数字，且数字落在版心左右的页码槽（页脚外侧）。
    位置看**页码自己**而不是整行 —— 页码跟行脚连在一行时（``144 物理 九年级``），
    整行的中心在版心中间，会被误判掉。
    """
    height = page["height"]
    width = page["width"]
    found: list[tuple[int, float, float]] = []
    for line in page["lines"]:
        if line["bbox"][1] < (1.0 - PRINTED_BAND) * height:
            continue
        number = _number_in_line(line, head_number=head_number)
        if number is None:
            continue
        printed, x = number
        if OUTER_MARGIN_RATIO * width < x < (1.0 - OUTER_MARGIN_RATIO) * width:
            continue
        found.append((printed, line["bbox"][1], x))
    return found


def detect_printed_pages(
    pages: list[dict],
    *,
    keep_outliers: bool = False,
    head_number: bool = False,
) -> dict:
    """找每页印的页码，并算出印刷页码与物理页号的偏移。

    返回::

        {"per_page": {12: "8"}, "offset": -7, "position": "footer_left",
         "anchors": {12: 786.7}}

    ``offset`` = 印刷页码 − 物理页号（众数口径，至少 3 页支持）。

    ``keep_outliers``：把**和全局偏移对不上**的页也记上它自己的印刷页码。
    扫描件常有漏页（这份电子课本扫描件就漏了 6 页），偏移不是常数——
    那几页的页码认得出来，只是不跟着众数走。不记上的话，这些页的行眉行脚
    （页码在行尾的那种）剥不掉，页文字里会留一条噪声。

    ``head_number``：行首数字后面直接跟汉字时也算页码（见
    :func:`_number_in_line`）。只在 OCR 路径上开——数字版书的行为不能变
    （重跑 ``bookcard text`` 文字不许变）。
    """
    candidates: dict[int, list[tuple[int, float, float]]] = {}
    for page in pages:
        found = _page_number_candidates(page, head_number=head_number)
        if found:
            candidates[page["number"]] = found

    result: dict = {"per_page": {}, "offset": None, "position": None, "anchors": {}}
    if not candidates:
        return result

    votes: dict[int, int] = {}
    for number, found in candidates.items():
        for printed, _, _ in found:
            votes[printed - number] = votes.get(printed - number, 0) + 1
    if not votes:
        return result

    offset = max(votes.items(), key=lambda item: (item[1], -item[0]))[0]
    support = sum(
        1
        for number, found in candidates.items()
        if any(printed - number == offset for printed, _, _ in found)
    )
    if support < rules.PAGE_NUMBER_MIN_PAGES:
        return result

    per_page: dict[int, str] = {}
    anchors: dict[int, float] = {}
    left = right = 0
    for number, found in candidates.items():
        matching = [(printed, y, x) for printed, y, x in found if printed - number == offset]
        if matching:
            printed, y, x = matching[0]
        elif keep_outliers and found:
            # 和全局偏移对不上 ⇒ 取最接近的那个候选（页码就在页脚外侧，
            # 认错了也不会差太远）。
            printed, y, x = min(found, key=lambda item: abs(item[0] - (number + offset)))
        else:
            continue
        per_page[number] = str(printed)
        anchors[number] = y
        if x < pages[number - 1]["width"] / 2:
            left += 1
        else:
            right += 1

    position = "footer_left" if left >= right else "footer_right"
    result.update(
        {
            "per_page": per_page,
            "offset": offset,
            "position": position,
            "anchors": anchors,
        }
    )
    return result


def _running_key(text: str) -> str:
    """行眉/行脚的判别键：去掉行首页码，归一化空白。"""
    match = LEADING_NUMBER_RE.match(text)
    if match:
        text = text[match.end() :]
    return rules.normalize_line(text)


def strip_running_lines(
    pages: list[dict],
    printed: dict,
    *,
    drop_page_number_line: bool = False,
) -> list[list[dict]]:
    """去掉行眉行脚，返回每页剩下的行。

    判据：这一行落在页眉带/页脚带里，而且同样的文字在 ≥3 页上重复出现。
    （正文里的重复行不带位置特征，不会被误删；行眉行脚每页位置都一样。）

    ``drop_page_number_line``：额外把「页脚带里、含有本页印刷页码」的行也去掉。
    OCR 页的行脚常常是「第一章 认识生物11」这种——页码在行尾，认不出行首数字，
    而每一页的行眉文字都不一样（带页码），也过不了「重复 ≥3 次」这条。
    数字版书不传这个开关，行为不变（重跑 ``bookcard text`` 文字不许变）。
    """
    counts: dict[str, int] = {}
    for page in pages:
        anchor = printed["anchors"].get(page["number"])
        for line in page["lines"]:
            if not (
                _in_header_band(line, page["height"])
                or _in_footer_band(line, page["height"], anchor)
            ):
                continue
            key = _running_key(line["text"])
            if key:
                counts[key] = counts.get(key, 0) + 1

    repeated = {key for key, count in counts.items() if count >= RUNNING_MIN_PAGES}

    result: list[list[dict]] = []
    for page in pages:
        anchor = printed["anchors"].get(page["number"])
        printed_page = printed["per_page"].get(page["number"])
        kept: list[dict] = []
        for line in page["lines"]:
            in_band = _in_header_band(line, page["height"]) or _in_footer_band(
                line, page["height"], anchor
            )
            if in_band:
                # 页码行：整行就是页码，或者页码跟行脚连在一行里
                match = LEADING_NUMBER_RE.match(line["text"])
                if match and printed_page == match.group(1):
                    continue
                if drop_page_number_line and printed_page:
                    if _contains_page_number(line["text"], printed_page):
                        continue
                if _running_key(line["text"]) in repeated:
                    continue
            kept.append(line)
        result.append(kept)
    return result


def _contains_page_number(text: str, printed_page: str) -> bool:
    """这一行里有没有出现这个印刷页码（独立的数字，前后不是别的数字）。"""
    for match in CONTAINS_NUMBER_RE.finditer(text):
        if match.group(1) == str(printed_page):
            return True
    return False


def page_text(lines: list[dict]) -> str:
    """一行一个换行拼出整页文字。

    拼法在 :mod:`bookcard.engine.breaks`（``join_lines``）——``breaks`` 的偏移
    必须落在这份文字上，所以只有一份实现。
    """
    return join_lines(lines)


def page_class(raw_text: str, stripped_text: str, had_watermark: bool) -> str:
    """页面文本层分类（口径同探针：``text`` / ``empty`` / ``garbled`` / ``watermark_only``）。"""
    return rules.classify_page(raw_text, stripped_text, had_watermark=had_watermark)
