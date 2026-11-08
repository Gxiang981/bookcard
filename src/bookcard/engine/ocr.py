"""§2.2 扫描件：本地 OCR 取字。

契约 §2.2（``docs/contract-v1.md``）：

- 探针 verdict 为 ``scanned`` ⇒ 整本书走本地 OCR，每页 ``text_origin: "ocr"``；
  数字版书**一页都不 OCR**（不然重跑 ``bookcard text`` 会改动现有三本书的文字）。
- 渲染 200 dpi 灰度图喂 OCR；行按阅读顺序排（先按栏、再从上到下），
  置信度 < 0.5 的行丢掉，水印规则照常套用。
- OCR 行框另存 ``ocr/p<4 位页号>.json``：``[{"text", "bbox", "conf", "start"}]``，
  坐标是 PDF 点（72 dpi），``start`` 是这一行在 ``text`` 里的偏移。
  §5 定位（bbox）和 §2.1 段落界都读它。
- 一行内某个词的 bbox：按字符数在行框内等比切。

**阅读顺序的实现**（§2.2 的「先按栏、再从上到下」）：递归 XY 切分。每一步看
当前这组行在竖直方向和水平方向的投影各分成几**段**，段数少的那一维切一刀：

- 竖直切 ⇒ 分栏：左边一栏读完再读右边一栏（教材里常见的左侧提要栏、
  右侧正文，就是这样分开的）；
- 水平切 ⇒ 分块：上面一块读完再读下面一块。

切线必须是**没有行跨过**的空白带（跨过了就不是真的分栏）。两维都切不动时，
按 ``(y, x)`` 排。

**字号**：OCR 拿不到字号，用行框高度当字号近似（汉字的字面高度接近 em），
供契约 §2.1 的「字宽」用。
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

from bookcard.engine.breaks import join_lines

#: 契约 §2.2：渲染 200 dpi 灰度图喂 OCR。
OCR_DPI = 200

#: 契约 §2.2：置信度低于 0.5 的行丢掉。
MIN_CONF = 0.5

#: ``--ocr`` 的三个取值。
MODE_AUTO = "auto"
MODE_ALWAYS = "always"
MODE_NEVER = "never"
OCR_MODES = (MODE_AUTO, MODE_ALWAYS, MODE_NEVER)

#: 没装 OCR 依赖时的安装提示（可选依赖组是 ``ocr``）。
INSTALL_COMMAND = "pip install 'bookcard[ocr]'"

#: OCR 行框文件在工作目录里的子目录名。
OCR_DIRNAME = "ocr"

#: OCR 引擎的推理线程数（环境变量可调）。见 :func:`get_engine`。
OCR_THREADS_ENV = "BOOKCARD_OCR_THREADS"

#: rapidocr 的日志级别（默认只报 warning，别刷屏）。
OCR_LOG_LEVEL_ENV = "BOOKCARD_OCR_LOG_LEVEL"


class OCRUnavailableError(RuntimeError):
    """没装 OCR 依赖（``bookcard[ocr]``）。"""


# ===================== 行 =====================


@dataclass(frozen=True)
class OcrLine:
    """一行 OCR 结果。

    ``bbox`` 是 PDF 点（72 dpi）的 ``(x0, y0, x1, y1)``——和 PyMuPDF 的
    rect 口径一致，定位时直接换算成像素。
    ``index`` 是这一行在原始行列表里的下标（重排时用来找回原行）。
    """

    text: str
    bbox: tuple[float, float, float, float]
    conf: float
    index: int = -1

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def height(self) -> float:
        """行框高度（OCR 没有字号，拿它当字号）。"""
        return max(0.0, self.bbox[3] - self.bbox[1])


# ===================== 依赖 =====================

_ENGINE: Any = None


def ocr_available() -> bool:
    """OCR 依赖装没装。"""
    try:
        import rapidocr  # noqa: F401
    except ImportError:
        return False
    return True


def require_ocr() -> None:
    """没装 OCR 依赖就直接报错，并给出安装命令（契约 §2.2：不静默跳过）。"""
    if not ocr_available():
        raise OCRUnavailableError(
            "这本书是扫描件，要走本地 OCR 取字，但没装 OCR 依赖。"
            f"装上：{INSTALL_COMMAND}"
        )


def _ocr_thread_setting() -> int | None:
    """每个 OCR 引擎的推理线程数（``None`` = 用 onnxruntime 的默认）。

    两个地方都能配：

    1. 环境变量 ``BOOKCARD_OCR_THREADS``；
    2. 配置文件 ``$BOOKCARD_HOME/ocr.conf`` 里的 ``threads=N``
       （``BOOKCARD_HOME`` 没设就是 ``~/.bookcard``）——
       **给子进程用**：agent 起的 ``bookcard`` 子进程拿不到环境变量时靠它。

    设了之后，一台机器上同时跑好几个 OCR 进程时不会互相抢核
    （实测十几套 onnxruntime 线程池能把 CPU 抢到每页 60 秒）。
    """
    raw = os.environ.get(OCR_THREADS_ENV, "").strip()
    if not raw:
        home = Path(os.environ.get("BOOKCARD_HOME", "~/.bookcard")).expanduser()
        conf = home / "ocr.conf"
        if conf.is_file():
            try:
                for line in conf.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("threads="):
                        raw = line.split("=", 1)[1].strip()
                        break
            except OSError:
                raw = ""
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def get_engine() -> Any:
    """拿 OCR 引擎（进程内只建一次）。

    **一台机器上同时跑好几个 OCR 进程时**（批量取字 + 看图目录的按需 OCR），
    onnxruntime 默认按核数给每个引擎开线程池，十几个线程池一起抢核，
    整机会卡到每页几十秒（实测 5069 个线程、CPU 几乎 0% 空闲）。
    用 ``BOOKCARD_OCR_THREADS=1``（或 ``$BOOKCARD_HOME/ocr.conf`` 里写
    ``threads=1``）把每个引擎的线程数封顶，批量跑的吞吐会高一个数量级。
    **不设就用 onnxruntime 的默认值**（单本跑时更快）。
    """
    global _ENGINE
    if _ENGINE is None:
        require_ocr()
        from rapidocr import RapidOCR

        params: dict[str, Any] = {}
        threads = _ocr_thread_setting()
        if threads:
            params["EngineConfig.onnxruntime.intra_op_num_threads"] = threads
            params["EngineConfig.onnxruntime.inter_op_num_threads"] = 1
        params["Global.log_level"] = os.environ.get(OCR_LOG_LEVEL_ENV, "warning")
        _ENGINE = RapidOCR(params=params)
    return _ENGINE


# ===================== 阅读顺序 =====================


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """区间合并成若干段（按起点排序）。"""
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _straddles(lines: Sequence[OcrLine], gap: tuple[float, float], axis: str) -> bool:
    """有没有行跨过这条切线（跨过了就不是真的分栏／分块）。"""
    low, high = gap
    for line in lines:
        if axis == "x":
            start, end = line.x0, line.x1
        else:
            start, end = line.y0, line.y1
        if start < high and end > low:
            return True
    return False


def _find_cut(
    lines: Sequence[OcrLine], axis: str
) -> tuple[tuple[float, float], int] | None:
    """在这一维上找一条切线。

    返回 ``((空白带起点, 空白带终点), 该维的段数)``；没有合法切线就是 ``None``。

    合法切线 = 投影的一段空白带，且**没有行跨过**它。
    """
    if axis == "x":
        intervals = [(line.x0, line.x1) for line in lines]
    else:
        intervals = [(line.y0, line.y1) for line in lines]
    runs = _merge_intervals(intervals)
    if len(runs) < 2:
        return None

    best: tuple[tuple[float, float], int] | None = None
    for current, following in zip(runs, runs[1:], strict=False):
        gap = (current[1], following[0])
        if _straddles(lines, gap, axis):
            continue
        if best is None or (gap[1] - gap[0]) > (best[0][1] - best[0][0]):
            best = (gap, len(runs))
    return best


#: 旁栏守卫：分栏时，栏也得**从页面顶部起**（离内容顶不超过这个比例）。
#:
#: 教材左边常有「小资料」「试一试」「提要」这种**旁栏**——它窄、只占页面
#: 一部分、顶边远低于页面内容顶。它不是一栏正文：把它当栏排，整栏会被提到
#: 正文前面，页标题都被压到后面去了（实测人教生物七上第 101 页：标题
#: 「果实和种子的形成」被压到第 13 行）。真栏是**从页面顶部就并排开始**的。
SIDE_BOX_TOP_RATIO = 0.25


def _column_pair_ok(left: list[OcrLine], right: list[OcrLine], lines: list[OcrLine]) -> bool:
    """这两组真的是「并排的两栏」吗（而不是旁栏 + 正文）。

    判据：两组的顶边都离页面内容顶不远（:data:`SIDE_BOX_TOP_RATIO`）。
    """
    content_top = min(line.y0 for line in lines)
    content_bottom = max(line.y1 for line in lines)
    span = max(1.0, content_bottom - content_top)
    limit = SIDE_BOX_TOP_RATIO * span
    for group in (left, right):
        if min(line.y0 for line in group) - content_top > limit:
            return False
    return True


def _xy_blocks(lines: list[OcrLine]) -> list[list[OcrLine]]:
    """递归 XY 切分，返回若干「块」（每块内部按从上到下排）。"""
    if not lines:
        return []
    if len(lines) == 1:
        return [list(lines)]

    vertical = _find_cut(lines, "x")
    horizontal = _find_cut(lines, "y")

    # 竖直方向段数更少（或水平方向只有一段）⇒ 先分栏。
    if vertical is not None:
        v_gap, v_runs = vertical
        h_runs = horizontal[1] if horizontal is not None else 1
        if h_runs == 1 or v_runs <= h_runs:
            left = [line for line in lines if line.x1 <= v_gap[0]]
            right = [line for line in lines if line.x0 >= v_gap[1]]
            if left and right:
                if _column_pair_ok(left, right, lines):
                    return _xy_blocks(left) + _xy_blocks(right)
                if horizontal is None:
                    # 没有横向切口可用 ⇒ 只能分栏（宁可排错也不能乱序）。
                    return _xy_blocks(left) + _xy_blocks(right)

    if horizontal is not None:
        h_gap = horizontal[0]
        top = [line for line in lines if line.y1 <= h_gap[0]]
        bottom = [line for line in lines if line.y0 >= h_gap[1]]
        if top and bottom:
            return _xy_blocks(top) + _xy_blocks(bottom)

    return [sorted(lines, key=lambda line: (line.y0, line.x0))]


def reading_order(lines: Sequence[OcrLine]) -> list[OcrLine]:
    """按阅读顺序排行：先按栏，再从上到下（契约 §2.2）。

    「栏」指**从页面顶部就并排开始**的正文栏；页面左侧的旁栏（教材的
    「小资料」「试一试」那类窄框）不算栏——它插在自己的位置读，
    不能把整栏提到正文前面（见 :data:`SIDE_BOX_TOP_RATIO`）。
    """
    return [line for block in _xy_blocks(list(lines)) for line in block]


def reorder_page_lines(lines: list[dict]) -> list[dict]:
    """按阅读顺序重排一页的行（并更新 ``block`` 序号）。

    入参是引擎统一的行结构（``text`` / ``bbox`` / ``size`` / ``block`` /
    ``spans``），出参是同样结构的行，只是顺序和 ``block`` 变了。
    """
    items = [
        OcrLine(
            text=str(line.get("text") or ""),
            bbox=tuple(float(value) for value in (line.get("bbox") or (0, 0, 0, 0))[:4]),
            conf=float(line.get("conf") or 0.0),
            index=index,
        )
        for index, line in enumerate(lines)
    ]
    reordered: list[dict] = []
    for block_index, block in enumerate(_xy_blocks(items)):
        for item in block:
            if 0 <= item.index < len(lines):
                reordered.append({**lines[item.index], "block": block_index})
    return reordered


# ===================== 行的 span（页码、目录要用） =====================


#: 汉字与中文标点（算 em 宽用）。
_CJK_RE = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")

#: 拉丁字母与数字。
_LATIN_RE = re.compile(r"[0-9A-Za-z]")


def _char_em(char: str) -> float:
    """一个字符占多少 em（汉字 1.0，字母数字 0.5，空格 0.5）。"""
    if _CJK_RE.match(char):
        return 1.0
    if char.isspace():
        return 0.5
    if _LATIN_RE.match(char):
        return 0.5
    return 0.4


def _char_class(char: str) -> str:
    """字符类别：``cjk`` / ``latin`` / ``other``（用来切行内分段）。"""
    if _CJK_RE.match(char):
        return "cjk"
    if _LATIN_RE.match(char):
        return "latin"
    return "other"


def _segments(text: str) -> list[tuple[str, float]]:
    """把一行切成「同类字符连段」：``[(文字, em 宽), ...]``（空格是分隔）。"""
    segments: list[tuple[str, float]] = []
    buffer = ""
    buffer_em = 0.0
    buffer_class = ""
    for char in text:
        if char.isspace():
            if buffer:
                segments.append((buffer, buffer_em))
                buffer = ""
                buffer_em = 0.0
            continue
        kind = _char_class(char)
        if buffer and kind != buffer_class:
            segments.append((buffer, buffer_em))
            buffer = ""
            buffer_em = 0.0
        buffer += char
        buffer_em += _char_em(char)
        buffer_class = kind
    if buffer:
        segments.append((buffer, buffer_em))
    return segments


def line_spans(text: str, bbox: tuple[float, float, float, float], size: float) -> list[dict]:
    """给一行造 span（按字符宽度等比切出行框）。

    OCR 只给整行的框；页码（「…11」「12 …」）和目录条目（「第一章 … 3」）
    要看行内各段的框，所以把一行按 em 宽度切成若干段，每段一个 span。
    """
    x0, y0, x1, y1 = bbox
    width = x1 - x0
    segments = _segments(text)
    total = sum(em for _, em in segments)
    if not segments or total <= 0 or width <= 0:
        return [{"text": text, "bbox": tuple(bbox), "size": size}]

    spans: list[dict] = []
    position = 0.0
    for segment, em in segments:
        span_x0 = x0 + width * (position / total)
        span_x1 = x0 + width * ((position + em) / total)
        spans.append(
            {
                "text": segment,
                "bbox": (span_x0, y0, span_x1, y1),
                "size": size,
            }
        )
        position += em
    return spans


# ===================== 跑 OCR =====================


def render_gray(page: pymupdf.Page, dpi: int = OCR_DPI):
    """把一页渲染成灰度图（200 dpi，契约 §2.2）。"""
    import numpy as np

    pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n
    )


def _unpack(result: Any) -> tuple[list[Any], list[str], list[float]]:
    """兼容 rapidocr 3.x 的输出对象和 2.x 的元组。"""
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if boxes is not None and texts is not None:
        return list(boxes), list(texts), list(scores or [])
    if isinstance(result, tuple) and result:
        payload = result[0] or []
        boxes = []
        texts = []
        scores = []
        for item in payload:
            if len(item) >= 3:
                boxes.append(item[0])
                texts.append(item[1])
                scores.append(item[2])
        return boxes, texts, scores
    return [], [], []


def ocr_page(
    page: pymupdf.Page,
    *,
    engine: Any = None,
    min_conf: float = MIN_CONF,
) -> list[OcrLine]:
    """OCR 一页，返回按阅读顺序排好的行（置信度低的已经丢掉）。"""
    engine = engine if engine is not None else get_engine()
    image = render_gray(page)
    result = engine(image)
    boxes, texts, scores = _unpack(result)

    scale = 72.0 / OCR_DPI
    lines: list[OcrLine] = []
    for box, text, score in zip(boxes, texts, scores, strict=False):
        content = str(text or "").strip()
        if not content:
            continue
        try:
            confidence = float(score)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < min_conf:
            continue
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        bbox = (
            min(xs) * scale,
            min(ys) * scale,
            max(xs) * scale,
            max(ys) * scale,
        )
        lines.append(OcrLine(text=content, bbox=bbox, conf=confidence))
    return reading_order(lines)


def page_to_dict(number: int, page: pymupdf.Page, lines: Sequence[OcrLine]) -> dict:
    """OCR 行 → 和 :func:`bookcard.engine.extract.extract_pages` 一样的页结构。

    ``size`` 用行框高度（OCR 没有字号）；``block`` 用 XY 切分出来的块序号
    （契约 §2.1 判据 4「换块」要用）。
    """
    blocks = _xy_blocks(list(lines))
    rows: list[dict] = []
    for block_index, block in enumerate(blocks):
        for line in block:
            size = line.height
            rows.append(
                {
                    "text": line.text,
                    "bbox": line.bbox,
                    "size": size,
                    "block": block_index,
                    "conf": line.conf,
                    "spans": line_spans(line.text, line.bbox, size),
                }
            )
    return {
        "number": number,
        "width": float(page.rect.width),
        "height": float(page.rect.height),
        "lines": rows,
    }


def ocr_pages(
    pdf_path: str | Path,
    *,
    engine: Any = None,
    pages: set[int] | None = None,
    on_page: Callable[[int, float, int], None] | None = None,
) -> tuple[list[dict], dict]:
    """OCR 整本书。

    返回 ``(页列表, 统计)``。页列表和 :func:`bookcard.engine.extract.extract_pages`
    同一个形状，统计里有每页耗时（毫秒）和总耗时。
    """
    engine = engine if engine is not None else get_engine()
    doc = pymupdf.open(pdf_path)
    try:
        result: list[dict] = []
        timings: list[dict] = []
        started = time.perf_counter()
        for index in range(doc.page_count):
            number = index + 1
            if pages is not None and number not in pages:
                continue
            page = doc[index]
            begin = time.perf_counter()
            lines = ocr_page(page, engine=engine)
            elapsed = time.perf_counter() - begin
            result.append(page_to_dict(number, page, lines))
            timings.append(
                {"page": number, "seconds": round(elapsed, 3), "lines": len(lines)}
            )
            if on_page is not None:
                on_page(number, elapsed, len(lines))
        total = time.perf_counter() - started
    finally:
        doc.close()

    stats = {
        "pages": len(result),
        "total_seconds": round(total, 3),
        "per_page": timings,
        "lines": sum(item["lines"] for item in timings),
    }
    return result, stats


# ===================== 产物：ocr/p<4 位页号>.json =====================


def ocr_file_name(page: int) -> str:
    """OCR 行框文件名：``p0012.json``（4 位页号）。"""
    return f"p{page:04d}.json"


def line_records(lines: Sequence[dict]) -> list[dict]:
    """页里的行 → ``ocr/pXXXX.json`` 的记录（带 ``start`` 偏移）。

    ``start`` 是这一行在页文字 ``text`` 里的偏移——``text`` 用
    :func:`bookcard.engine.breaks.join_lines` 拼，所以偏移算法和它一致。
    """
    records: list[dict] = []
    offset = 0
    total = len(lines)
    for index, line in enumerate(lines):
        text = str(line.get("text") or "").strip()
        bbox = [round(float(value), 2) for value in (line.get("bbox") or (0, 0, 0, 0))[:4]]
        records.append(
            {
                "text": text,
                "bbox": bbox,
                "conf": round(float(line.get("conf") or 0.0), 4),
                "start": offset,
            }
        )
        offset += len(text) + (1 if index < total - 1 else 0)
    return records


def page_text_of(lines: Sequence[dict]) -> str:
    """页文字（和引擎其余部分同一份实现）。"""
    return join_lines(list(lines))


def word_rect(
    records: Sequence[dict],
    start: int,
    length: int,
) -> tuple[float, float, float, float] | None:
    """词在 OCR 行框里的位置（PDF 点）。

    先按 ``start`` 找到词落在哪一行，再**按字符数在行框内等比切**
    （契约 §2.2：中文等宽足够准）。词跨行或找不到就返回 ``None``。
    """
    if length <= 0:
        return None
    for record in records:
        text = str(record.get("text") or "")
        line_start = int(record.get("start") or 0)
        line_end = line_start + len(text)
        if not (line_start <= start and start + length <= line_end):
            continue
        bbox = record.get("bbox") or []
        if len(bbox) != 4:
            return None
        x0, y0, x1, y1 = (float(value) for value in bbox)
        count = len(text)
        if count <= 0:
            return None
        char_width = (x1 - x0) / count
        offset = start - line_start
        return (
            x0 + offset * char_width,
            y0,
            x0 + (offset + length) * char_width,
            y1,
        )
    return None
