"""``bookcard tocv``：目录看图 v1（契约 ``docs/tocv-v1.md``）。

方向（用户拍板）：**目录由模型看图产出，规则只做检查和对照**。
引擎不内置任何模型 key，也不调模型——看图的活交给调用方的 agent。

工作目录很轻：只要一本 PDF 和一个输出目录。不依赖 ``book_add``，不做整本 OCR。

产物：

- ``tocv.json``：工作状态（书指纹、每页有没有文本层、模型认定的目录页、最后一次草稿）。
- ``sheets/sheet-<first>-<last>.png``：多页缩略图拼接图，角上印物理页号。
- ``pages/p<4 位页号>.png``（数字版另写 ``.txt``）：目录页 / 正文页的大图。
- ``ocr/p<4 位页号>.json``：扫描件**按需** OCR 的缓存（只用到的页）。
- ``toc.json``：和现有 ``toc.json`` 同一格式（``source: "vision"``）。
- ``tocv_report.json``：每项检查的结果、失败条目、轮次、状态。
- ``score.json``：和 gold 对照的准确率。

闸门 1 照旧由人或 ``toc_gate`` 拍板：这里写出的 ``gate.status`` **永远不是
``approved``**，最多到 ``pending``。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pymupdf

from bookcard.engine import analysis, common, gate
from bookcard.engine import ocr as ocr_mod
from bookcard.engine import sections as sections_mod
from bookcard.engine import toc as toc_mod
from bookcard.probe import core as probe_core
from bookcard.probe import rules as probe_rules

# ===================== 常量 =====================

#: 状态文件（工作目录里）。
STATE_FILE = "tocv.json"

#: 拼接图子目录。
SHEETS_DIRNAME = "sheets"

#: 大图子目录。
PAGES_DIRNAME = "pages"

#: 拼接图 / 大图的长边上限（像素）。
MAX_LONG_EDGE = 2000

#: 拼接图默认网格（4×3 = 12 页一张）。
DEFAULT_COLS = 4
DEFAULT_ROWS = 3

#: 拼接图默认看的页区间：只看前 24 页（目录通常在书前）。
DEFAULT_FIRST = 1
DEFAULT_LAST = 24

#: 大图默认 dpi。
DEFAULT_DPI = 150

#: 标题匹配的编辑距离相似度阈值（判据 C3 / C4）。
TITLE_MATCH_THRESHOLD = 0.80

#: 标题在正文页上的容差（±1 页）。
TITLE_PAGE_WINDOW = 1

#: 最多提交几轮（第三轮还失败就是 ``needs_human``）。
MAX_ROUNDS = 3

#: 条目类型。
KINDS = ("body", "front", "appendix")
DEFAULT_KIND = "body"

#: 目录来源（写进 ``toc.json`` 的 ``source``）。
SOURCE = "vision"

#: C1–C5 全过才算 ``ok``；C6 只出报告，不判失败。
FAILING_CHECKS = ("C1", "C2", "C3", "C4", "C5")


class TocvStateError(RuntimeError):
    """工作目录状态不对（没跑 init、PDF 找不到了等）。"""


class DraftError(ValueError):
    """草稿格式不对。"""


# ===================== 文本工具 =====================

#: 去掉空白和标点（匹配前统一归一）。
_PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)

#: 条目编号前缀（匹配标题前先把它去掉）。
#: 「课题N」「实验活动N」也算 —— 人教版化学/生物的目录上有这种标签，
#: 而扫描件 OCR 的阅读顺序常把标签排到标题**后面**（「溶解度 课题2」），
#: 不把标签去掉就永远匹配不上整串，抄目录的 agent 只能把标题截短。
_SECTION_PREFIX_RE = re.compile(
    r"^(?:"
    r"第\s*[0-9一二三四五六七八九十百千零〇两]+\s*[章节课单元部篇卷部分]"
    r"|(?:Unit|Chapter|Lesson|Part)\s*[0-9IVXLC]+"
    r"|课题\s*[0-9一二三四五六七八九十]+"
    r"|实验活动\s*[0-9一二三四五六七八九十]*"
    r"|附录\s*[0-9ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVXivx]*"
    r"|[0-9]+(?:\.[0-9]+)*\s*[.、．:：]?"
    r")\s*[:：、.\-—－]*\s*",
    re.IGNORECASE,
)

#: 文字里的拉丁字母（拼音注音；小学一二年级的标题是「字图 + 拼音」）。
#: 拉丁字母（含带声调的拼音字母，如 qiū、tiān）。
_LATIN_RUN_RE = re.compile(r"[A-Za-z\u00C0-\u024F\u1E00-\u1EFF]+")

#: 单独成行的脚注标记（匹配标题时先去掉）。
_FOOTNOTE_MARKER_RE = re.compile(r"[a-zA-Z]|[①②③④⑤⑥⑦⑧⑨⑩]|\u2460|\u2461")

#: 条目编号（用来查 C5 的编号连续性）。
_CN_NUMBER_RE = re.compile(
    r"^第\s*([0-9一二三四五六七八九十百千零〇两]+)\s*(章|节|课|单元|部分|篇|部|卷)"
)
_EN_NUMBER_RE = re.compile(
    r"^(Unit|Chapter|Lesson|Part)\s*([0-9]+)", re.IGNORECASE
)

#: 中文数字。
_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

#: 编号的“序列”归类：章 / 单元 / Unit 都是一级；节 / 课 都是二级。
_UNIT_KINDS = {"章", "单元", "部分", "篇", "部", "卷", "Unit", "Chapter", "Part"}
_LESSON_KINDS = {"节", "课", "Lesson"}


def normalize(text: str) -> str:
    """去掉空白和标点（匹配前的统一口径）。"""
    return _PUNCT_RE.sub("", text or "")


def edit_distance(left: str, right: str) -> int:
    """编辑距离（Levenshtein）。"""
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for index, left_char in enumerate(left, start=1):
        current = [index]
        for offset, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[offset] + 1,
                    current[offset - 1] + 1,
                    previous[offset - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def similarity(left: str, right: str) -> float:
    """编辑距离相似度：``1 - 距离 / 较长的一边``。"""
    longest = max(len(left), len(right))
    if longest == 0:
        return 1.0
    return 1.0 - edit_distance(left, right) / longest


def best_window_similarity(needle: str, haystack: str) -> float:
    """``needle`` 在 ``haystack`` 里最好的一次对齐的相似度。

    整段包含就是 1.0；否则把 ``needle`` 当滑窗在 ``haystack`` 上滑一遍取最好成绩。
    两边长度差太多就不滑了（那种情况本来也不可能是同一条标题）。
    """
    if not needle or not haystack:
        return 0.0
    if needle in haystack:
        return 1.0
    if len(haystack) <= len(needle):
        return similarity(needle, haystack)
    if len(haystack) > len(needle) + 30:
        return similarity(needle, haystack)
    best = 0.0
    for start in range(len(haystack) - len(needle) + 1):
        value = similarity(needle, haystack[start : start + len(needle)])
        if value > best:
            best = value
            if best >= 1.0:
                break
    return best


def strip_section_number(title: str) -> str:
    """去掉标题开头的章节号（``第一章 机械运动`` → ``机械运动``）。"""
    return _SECTION_PREFIX_RE.sub("", title or "").strip()


def title_core(title: str) -> str:
    """标题主体：去掉章节号、去掉空白和标点。

    去掉章节号后太短（1 个字以内）就用整个标题——宁可多匹配，也别把标题丢掉。
    """
    core = normalize(strip_section_number(title))
    if len(core) >= 2:
        return core
    return normalize(title)


def cn_to_int(text: str) -> int | None:
    """中文数字 → 整数（``十二`` → 12，``二十三`` → 23，``三`` → 3）。"""
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total = 0
    if "百" in text:
        head, _, tail = text.partition("百")
        hundreds = cn_to_int(head) if head else 1
        if hundreds is None:
            return None
        total += hundreds * 100
        text = tail
    if "十" in text:
        head, _, tail = text.partition("十")
        tens = cn_to_int(head) if head else 1
        if tens is None:
            return None
        total += tens * 10
        text = tail
    if text:
        digit = _CN_DIGITS.get(text[0])
        if digit is None:
            return None
        total += digit
    return total


def entry_number(title: str) -> tuple[int, str] | None:
    """条目编号 → ``(编号, 序列)``；认不出返回 ``None``。

    序列是 ``"unit"``（章 / 单元 / Unit）或 ``"lesson"``（节 / 课）。
    """
    text = (title or "").strip()
    match = _CN_NUMBER_RE.match(text)
    if match:
        number = cn_to_int(match.group(1))
        if number is None:
            return None
        kind = match.group(2)
        sequence = "unit" if kind in _UNIT_KINDS else "lesson"
        return number, sequence
    match = _EN_NUMBER_RE.match(text)
    if match:
        kind = match.group(1).capitalize()
        sequence = "unit" if kind in _UNIT_KINDS else "lesson"
        return int(match.group(2)), sequence
    return None


# ===================== 状态 =====================


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def state_path(work_dir: str | Path) -> Path:
    return Path(work_dir) / STATE_FILE


def load_state(work_dir: str | Path) -> dict:
    """读 ``tocv.json``。"""
    path = state_path(work_dir)
    if not path.is_file():
        raise TocvStateError(
            f"工作目录 {work_dir} 里没有 {STATE_FILE}："
            f"先跑 bookcard tocv init <pdf> --out {work_dir}"
        )
    state = common.read_json(path)
    if not isinstance(state, dict):
        raise TocvStateError(f"{path} 不是合法的状态文件")
    return state


def save_state(work_dir: str | Path, state: dict) -> Path:
    """写 ``tocv.json``。"""
    return common.write_json(state_path(work_dir), state)


def init_workdir(pdf_path: str | Path, out_dir: str | Path) -> dict:
    """建工作目录的状态文件（幂等：已经有了就不重建）。

    状态里有：PDF 路径、sha256、页数、每页有没有文本层（探针的判定）。
    """
    work_dir = Path(out_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    path = state_path(work_dir)
    if path.is_file():
        state = common.read_json(path)
        if isinstance(state, dict):
            return {**state, "created": False}
        raise TocvStateError(f"{path} 不是合法的状态文件")

    pdf = Path(pdf_path)
    if not pdf.is_file():
        raise TocvStateError(f"PDF 找不到：{pdf}")

    report = probe_core.probe(pdf)
    file_info = report.get("file") or {}
    if report.get("verdict") == "broken":
        raise TocvStateError(f"PDF 打不开：{file_info.get('error') or '未知原因'}")

    page_count = int(file_info.get("page_count") or 0)
    classes = list(report.get("text_layer", {}).get("per_page") or [])
    verdict = str(report.get("verdict") or "")
    # 每页有没有文本层：这一页能不能取到字（不是探针的页类型——
    # 标题页只有一行字，也是有文本层的）。
    text_layer_pages: list[int] = []
    doc = pymupdf.open(pdf)
    try:
        for index in range(doc.page_count):
            if doc[index].get_text("text").strip():
                text_layer_pages.append(index + 1)
    finally:
        doc.close()
    # 整本书算不算数字版：探针的判定说了算（乱码书不算有文本层）。
    kind = "pdf_text" if verdict in ("digital", "mixed") else "scanned"

    state = {
        "version": 1,
        "pdf_path": str(pdf.resolve()),
        "sha256": str(file_info.get("sha256") or ""),
        "page_count": page_count,
        "kind": kind,
        "verdict": verdict,
        "text_layer_pages": text_layer_pages,
        "page_classes": classes,
        "toc_pages": [],
        "located_by": "",
        "located_at": "",
        "created_at": _now(),
    }
    save_state(work_dir, state)
    return {**state, "created": True}


# ===================== 渲染 =====================


def _open_pdf(state: dict) -> pymupdf.Document:
    path = Path(state["pdf_path"])
    if not path.is_file():
        raise TocvStateError(f"PDF 找不到：{path}")
    return pymupdf.open(path)


def _fit_dpi(long_edge_points: float, dpi: int) -> int:
    """让长边不超过 :data:`MAX_LONG_EDGE` 像素的 dpi（至少 1）。"""
    if long_edge_points <= 0:
        return max(1, dpi)
    limit = int(72 * MAX_LONG_EDGE / long_edge_points)
    return max(1, min(dpi, limit))


def _label_font_size(cell_width: float, cell_height: float) -> float:
    """页号标签的字号（醒目的大号字）。"""
    return max(10.0, min(60.0, min(cell_width, cell_height) * 0.10))


def _draw_label(page: pymupdf.Page, rect: pymupdf.Rect, number: int, font_size: float) -> dict:
    """在格子角上画红底白字的物理页号。"""
    text = str(number)
    padding = font_size * 0.18
    width = font_size * 0.62 * len(text) + padding * 2
    height = font_size * 1.15 + padding
    box = pymupdf.Rect(rect.x0 + 2, rect.y0 + 2, rect.x0 + 2 + width, rect.y0 + 2 + height)
    page.draw_rect(box, color=None, fill=(0.85, 0.05, 0.05))
    page.insert_text(
        pymupdf.Point(box.x0 + padding, box.y0 + font_size * 0.92),
        text,
        fontsize=font_size,
        fontname="hebo",
        color=(1, 1, 1),
    )
    return {"page": number, "text": text, "rect": [box.x0, box.y0, box.x1, box.y1]}


def render_sheet(
    work_dir: str | Path,
    *,
    first: int = DEFAULT_FIRST,
    last: int = DEFAULT_LAST,
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
) -> dict:
    """把 ``[first, last]`` 页拼成多页缩略图拼接图。

    每张图最多 ``cols × rows`` 页（默认 12 页一张）。每格角上印物理页号
    （红底白字），模型靠它报页码。区间超出全书页数就截断。

    返回 ``{"sheets": [每张图的清单], ...}``。
    """
    state = load_state(work_dir)
    page_count = int(state.get("page_count") or 0)
    if page_count <= 0:
        raise TocvStateError("状态文件里没有页数，重跑 bookcard tocv init")
    if cols < 1 or rows < 1:
        raise ValueError("--cols 和 --rows 至少是 1")

    first = max(1, int(first))
    requested_last = int(last)
    last = min(int(last), page_count)
    if last < first:
        raise ValueError(f"页区间 [{first}, {last}] 没法渲染（全书 {page_count} 页）")

    out_dir = Path(work_dir) / SHEETS_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = _open_pdf(state)
    sheet_doc = pymupdf.open()
    try:
        page_rect = doc[0].rect
        aspect = (page_rect.width / page_rect.height) if page_rect.height else 0.7
        # 长边不超过 MAX_LONG_EDGE：格子宽度同时受列数和行数约束。
        cell_width = min(MAX_LONG_EDGE / cols, MAX_LONG_EDGE * aspect / rows)
        cell_height = cell_width / aspect if aspect else cell_width
        sheet_width = cols * cell_width
        sheet_height = rows * cell_height
        font_size = _label_font_size(cell_width, cell_height)
        thumb_dpi = max(1, int(72 * cell_width / page_rect.width)) if page_rect.width else 72

        chunks: list[dict] = []
        capacity = cols * rows
        numbers = list(range(first, last + 1))
        for offset in range(0, len(numbers), capacity):
            chunk = numbers[offset : offset + capacity]
            out = sheet_doc.new_page(width=sheet_width, height=sheet_height)
            out.draw_rect(out.rect, color=None, fill=(1, 1, 1))
            cells: list[dict] = []
            for index, number in enumerate(chunk):
                column = index % cols
                row = index // cols
                rect = pymupdf.Rect(
                    column * cell_width,
                    row * cell_height,
                    (column + 1) * cell_width,
                    (row + 1) * cell_height,
                )
                pixmap = doc[number - 1].get_pixmap(dpi=thumb_dpi)
                out.insert_image(rect, pixmap=pixmap)
                label = _draw_label(out, rect, number, font_size)
                cells.append(
                    {
                        "page": number,
                        "rect": [rect.x0, rect.y0, rect.x1, rect.y1],
                        "label": label,
                    }
                )
            # 格子之间的细分隔线
            for column in range(1, cols):
                x = column * cell_width
                out.draw_line(
                    pymupdf.Point(x, 0),
                    pymupdf.Point(x, sheet_height),
                    color=(0.72, 0.72, 0.72),
                    width=0.6,
                )
            for row in range(1, rows):
                y = row * cell_height
                out.draw_line(
                    pymupdf.Point(0, y),
                    pymupdf.Point(sheet_width, y),
                    color=(0.72, 0.72, 0.72),
                    width=0.6,
                )
            out.draw_rect(
                pymupdf.Rect(0, 0, sheet_width, sheet_height),
                color=(0.4, 0.4, 0.4),
                width=0.8,
            )

            target = out_dir / f"sheet-{chunk[0]}-{chunk[-1]}.png"
            pixmap = out.get_pixmap(dpi=72)
            pixmap.save(str(target))
            chunks.append(
                {
                    "path": str(target),
                    "first": chunk[0],
                    "last": chunk[-1],
                    "pages": chunk,
                    "cols": cols,
                    "rows": rows,
                    "width": pixmap.width,
                    "height": pixmap.height,
                    "cells": cells,
                }
            )
    finally:
        sheet_doc.close()
        doc.close()

    return {
        "requested": {
            "first": first,
            "last": requested_last,
            "cols": cols,
            "rows": rows,
        },
        "truncated": last != requested_last,
        "sheets": chunks,
        "paths": [chunk["path"] for chunk in chunks],
    }


def render_pages(
    work_dir: str | Path,
    pages: list[int],
    *,
    dpi: int = DEFAULT_DPI,
) -> dict:
    """把指定页渲染成大图（长边不超过 2000px）。

    数字版另写同名 ``.txt``（文本层，只当参考——条目以图为准）。
    """
    state = load_state(work_dir)
    page_count = int(state.get("page_count") or 0)
    out_dir = Path(work_dir) / PAGES_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)
    text_layer_pages = set(state.get("text_layer_pages") or [])

    doc = _open_pdf(state)
    written: list[dict] = []
    try:
        for number in pages:
            number = int(number)
            if number < 1 or number > page_count:
                continue
            page = doc[number - 1]
            long_edge = max(page.rect.width, page.rect.height)
            use_dpi = _fit_dpi(long_edge, int(dpi))
            target = out_dir / f"p{number:04d}.png"
            pixmap = page.get_pixmap(dpi=use_dpi)
            pixmap.save(str(target))
            record = {
                "page": number,
                "path": str(target),
                "text_path": None,
                "dpi": use_dpi,
                "width": pixmap.width,
                "height": pixmap.height,
            }
            if number in text_layer_pages:
                text_path = out_dir / f"p{number:04d}.txt"
                text_path.write_text(page.get_text("text"), encoding="utf-8")
                record["text_path"] = str(text_path)
            written.append(record)
    finally:
        doc.close()

    return {"pages": written, "paths": [item["path"] for item in written]}


def locate_pages(
    work_dir: str | Path,
    pages: list[int],
    *,
    by: str,
) -> dict:
    """记下模型判断的目录页（可以不连续），写进 ``tocv.json`` 的 ``toc_pages``。"""
    state = load_state(work_dir)
    page_count = int(state.get("page_count") or 0)
    cleaned: list[int] = []
    for number in pages:
        number = int(number)
        if number < 1 or (page_count and number > page_count):
            continue
        if number not in cleaned:
            cleaned.append(number)
    cleaned.sort()
    state["toc_pages"] = cleaned
    state["located_by"] = by
    state["located_at"] = _now()
    save_state(work_dir, state)
    return {"toc_pages": cleaned, "located_by": by, "located_at": state["located_at"]}


# ===================== 页文字 =====================


def _load_pages_jsonl(work_dir: Path) -> dict[int, str]:
    """书的工作目录里已经取过字（``pages.jsonl``）就把文字读进来。

    看图目录的判据（尤其扫描件的 C3/C4）要页文字；整本取字那一步
    已经 OCR 过的页，别再 OCR 一遍 —— 批量跑几十本书的时候这是大头。
    看几个位置：看图目录自己、它的上级（书的工作目录）、上上级。
    """
    for base in (work_dir, work_dir.parent, work_dir.parent.parent):
        path = Path(base) / "pages.jsonl"
        if not path.is_file():
            continue
        texts: dict[int, str] = {}
        try:
            for row in common.read_jsonl(path):
                number = row.get("page")
                text = str(row.get("text") or "")
                if number and text:
                    texts[int(number)] = text
        except (OSError, ValueError):
            continue
        if texts:
            return texts
    return {}


class _TextProvider:
    """按需取页文字：数字版用文本层，扫描件现做 OCR（缓存到 ``<dir>/ocr/``）。"""

    def __init__(self, work_dir: Path, state: dict, doc: pymupdf.Document) -> None:
        self.work_dir = work_dir
        self.state = state
        self.doc = doc
        self._texts: dict[int, str] = {}
        self._raw_texts: dict[int, str] = {}
        self._analysis: dict | None = None
        self._analyzed = False
        self._text_layer_pages = set(state.get("text_layer_pages") or [])
        self._pages_jsonl = _load_pages_jsonl(work_dir)

    @property
    def digital(self) -> bool:
        return self.state.get("kind") == "pdf_text"

    def analysis(self) -> dict:
        """规则解析的结果（只有数字版才有）。"""
        if not self._analyzed:
            self._analyzed = True
            if self.digital:
                self._analysis = analysis.analyze(self.state["pdf_path"])
                for page in self._analysis.get("pages") or []:
                    number = int(page["number"])
                    self._texts[number] = str(page.get("text") or "")
                    raw = str(page.get("raw_text") or "")
                    if raw:
                        self._raw_texts[number] = raw
        return self._analysis or {}

    def _has_text_layer(self, number: int) -> bool:
        return number in self._text_layer_pages

    def _needs_ocr(self, number: int) -> bool:
        """这一页要不要走 OCR。

        - **扫描件**：整本都走 OCR。
        - **混合本**（探针判定 ``mixed``）：只有没文本层的页走 OCR。
        - **纯数字版**：**不用 OCR**——整页是图的页面本来就没有字，
          不能因为它就把 OCR 依赖拖进来（契约 §2.2 只对扫描件要求 OCR）。
        """
        if self.state.get("kind") == "scanned":
            return True
        if str(self.state.get("verdict") or "") == "mixed":
            return not self._has_text_layer(number)
        return False

    def _ocr_text(self, number: int) -> str:
        """扫描件的一页文字：先看缓存，没有就现做 OCR。"""
        cache_dir = self.work_dir / ocr_mod.OCR_DIRNAME
        path = cache_dir / ocr_mod.ocr_file_name(number)
        if path.is_file():
            payload = common.read_json(path)
            if isinstance(payload, list):
                return ocr_mod.page_text_of(payload)
        # **整本取字已经做过 OCR 的书**（工作目录里有 pages.jsonl）：
        # 直接用那次的文字，别把同一页再 OCR 一遍 —— 批量跑的时候这是大头。
        if self._pages_jsonl:
            cached = self._pages_jsonl.get(int(number))
            if cached:
                return cached
        page = self.doc[number - 1]
        lines = ocr_mod.ocr_page(page)
        rows = ocr_mod.page_to_dict(number, page, lines)["lines"]
        common.write_json(path, ocr_mod.line_records(rows))
        return ocr_mod.page_text_of(ocr_mod.line_records(rows))

    def text_variants(self, number: int) -> list[str]:
        """一页的文字的几个版本：剥过的 + 原来的。

        剥行眉那一步是启发式：目录页上印着的「课题研究」如果同时是正文页的
        页眉，也会被当成重复行眉剥掉 —— 目录条目就再也核不上了
        （人教版高中物理选择性必修第三册就有一条是这样丢的）。
        所以匹配标题时，剥过的和原来的一起找。
        """
        texts = [self.text(number)]
        raw = self._raw_texts.get(int(number))
        if raw and raw not in texts:
            texts.append(raw)
        return texts

    def text(self, number: int) -> str:
        """一页的文字（数字版 = 文本层；扫描件 = OCR）。"""
        number = int(number)
        if number in self._texts:
            return self._texts[number]
        page_count = int(self.state.get("page_count") or 0)
        if number < 1 or (page_count and number > page_count):
            return ""
        if self._needs_ocr(number):
            text = self._ocr_text(number)
        else:
            self.analysis()
            if number in self._texts:
                return self._texts[number]
            text = self.doc[number - 1].get_text("text")
        self._texts[number] = text
        return text


# ===================== 草稿 → 物理页 =====================


def convert_pages(
    printed_page: int,
    anchors: list[dict],
) -> int | None:
    """印刷页码 → 物理页号（按 anchor 分段换算）。

    按 ``printed_page`` 从小到大分段：每个 anchor 的偏移从它自己的
    ``printed_page`` 开始生效（应对中间插了彩页的书）；第一个 anchor 之前
    用第一个 anchor 的偏移。
    """
    if printed_page is None:
        return None
    ordered = sorted(
        (anchor for anchor in anchors if anchor.get("printed_page") is not None),
        key=lambda anchor: int(anchor["printed_page"]),
    )
    if not ordered:
        return None
    offset = int(ordered[0]["physical_page"]) - int(ordered[0]["printed_page"])
    for anchor in ordered:
        if int(anchor["printed_page"]) <= printed_page:
            offset = int(anchor["physical_page"]) - int(anchor["printed_page"])
        else:
            break
    return printed_page + offset


def _parse_draft(payload: Any) -> tuple[list[dict], list[dict], list[int]]:
    """草稿 → ``(条目, anchors, 目录页)``，顺带做格式校验。"""
    if not isinstance(payload, dict):
        raise DraftError("草稿必须是一个 JSON 对象")

    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise DraftError("草稿里没有 entries（要是非空数组）")

    entries: list[dict] = []
    for index, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            raise DraftError(f"第 {index + 1} 条条目不是一个对象")
        title = str(item.get("title") or "").strip()
        if not title:
            raise DraftError(f"第 {index + 1} 条条目没有标题")
        level = item.get("level", 1)
        try:
            level = int(level)
        except (TypeError, ValueError) as exc:
            raise DraftError(f"第 {index + 1} 条条目的 level 不是整数") from exc
        if level < 1:
            raise DraftError(f"第 {index + 1} 条条目的 level 小于 1")
        printed = item.get("printed_page")
        if printed is not None:
            try:
                printed = int(printed)
            except (TypeError, ValueError) as exc:
                raise DraftError(
                    f"第 {index + 1} 条条目的 printed_page 不是整数：{item.get('printed_page')!r}"
                ) from exc
        kind = str(item.get("kind") or DEFAULT_KIND)
        if kind not in KINDS:
            kind = DEFAULT_KIND
        entries.append(
            {
                "index": index,
                "level": level,
                "title": title,
                "printed_page": printed,
                "kind": kind,
            }
        )

    raw_anchors = payload.get("anchors")
    if not isinstance(raw_anchors, list):
        raw_anchors = []
    anchors: list[dict] = []
    for index, item in enumerate(raw_anchors):
        if not isinstance(item, dict):
            raise DraftError(f"第 {index + 1} 个 anchor 不是一个对象")
        printed = item.get("printed_page")
        physical = item.get("physical_page")
        if printed is None or physical is None:
            raise DraftError(
                f"第 {index + 1} 个 anchor 缺 printed_page 或 physical_page"
            )
        try:
            anchors.append(
                {
                    "printed_page": int(printed),
                    "physical_page": int(physical),
                }
            )
        except (TypeError, ValueError) as exc:
            raise DraftError(f"第 {index + 1} 个 anchor 的页码不是整数") from exc

    raw_toc_pages = payload.get("toc_pages")
    toc_pages: list[int] = []
    if isinstance(raw_toc_pages, list):
        for item in raw_toc_pages:
            try:
                number = int(item)
            except (TypeError, ValueError) as exc:
                raise DraftError(f"目录页 {item!r} 不是整数") from exc
            if number not in toc_pages:
                toc_pages.append(number)
        toc_pages.sort()

    return entries, anchors, toc_pages


# ===================== 检查 =====================


def _check_c1(
    entries: list[dict],
    anchors: list[dict],
    page_count: int,
) -> tuple[bool, str, list[dict]]:
    """C1 页码：物理页单调不减、落在书里；anchor 也要站得住。"""
    failed: list[dict] = []
    problems: list[str] = []

    if len(anchors) < 2:
        problems.append(
            f"anchors 只有 {len(anchors)} 条，至少要 2 条（第一条和最后一条一级条目）"
        )
    first_level = [entry for entry in entries if entry["level"] == 1]
    if first_level and anchors:
        # anchor 的顺序不该影响判据：按印刷页码排了再比首尾。
        ordered = sorted(anchors, key=lambda anchor: int(anchor["printed_page"]))
        first_printed = first_level[0]["printed_page"]
        last_printed = first_level[-1]["printed_page"]
        if first_printed is not None and int(ordered[0]["printed_page"]) != first_printed:
            problems.append(
                f"第一个 anchor 的 printed_page 是 {ordered[0]['printed_page']}，"
                f"但第一条一级条目印的是 {first_printed}"
            )
        if last_printed is not None and int(ordered[-1]["printed_page"]) != last_printed:
            problems.append(
                f"最后一个 anchor 的 printed_page 是 {ordered[-1]['printed_page']}，"
                f"但最后一条一级条目印的是 {last_printed}"
            )
    for anchor in anchors:
        if not 1 <= int(anchor["physical_page"]) <= page_count:
            problems.append(
                f"anchor（印刷页 {anchor['printed_page']}）指向物理页 "
                f"{anchor['physical_page']}，不在 1–{page_count} 页里"
            )

    previous: int | None = None
    for entry in entries:
        page = entry.get("page")
        if page is None:
            continue
        if not 1 <= page <= page_count:
            problems.append(
                f"第 {entry['index'] + 1} 条「{entry['title']}」换算出的物理页 {page} "
                f"不在 1–{page_count} 页里"
            )
            failed.append(
                {
                    "index": entry["index"],
                    "title": entry["title"],
                    "check": "C1",
                    "reason": f"物理页 {page} 不在 1–{page_count} 页里",
                }
            )
            previous = page
            continue
        if previous is not None and page < previous:
            problems.append(
                f"第 {entry['index'] + 1} 条「{entry['title']}」的物理页 {page} "
                f"比上一条的 {previous} 小"
            )
            failed.append(
                {
                    "index": entry["index"],
                    "title": entry["title"],
                    "check": "C1",
                    "reason": f"物理页 {page} 比上一条的 {previous} 小",
                }
            )
        previous = page

    if problems:
        return False, "C1 不通过：" + "；".join(problems[:5]), failed
    counted = sum(1 for entry in entries if entry.get("page") is not None)
    return (
        True,
        f"C1 通过：{counted} 条条目物理页单调不减，且都在 1–{page_count} 页里",
        failed,
    )


def _check_c2(
    entries: list[dict],
) -> tuple[bool, str, list[dict], dict]:
    """C2 覆盖：按默认第 1 层切，看正文页有没有被连续覆盖。

    切几层是**用户第二步确认**的事，基准不替用户挑层级，也不再判 3–25 页。
    """
    with_pages = [entry for entry in entries if entry.get("page") is not None]
    if not with_pages:
        return False, "C2 不通过：没有一条条目换算出了物理页", [], {}

    body_pages = [
        entry["page"] for entry in with_pages if entry["kind"] == DEFAULT_KIND
    ]
    all_pages = [entry["page"] for entry in with_pages]
    first_body_page = min(body_pages or all_pages)
    last_body_page = max(all_pages)

    level = toc_mod.DEFAULT_EXTRACT_LEVEL
    sections = sections_mod.sections_for_level(
        [
            {"level": entry["level"], "title": entry["title"], "page": entry["page"]}
            for entry in with_pages
        ],
        level,
        first_body_page,
        last_body_page,
    )
    coverage_ok, coverage_reason = gate.check_coverage(
        sections, first_body_page, last_body_page
    )

    failed: list[dict] = []
    if not coverage_ok:
        for entry in with_pages:
            if entry["kind"] != DEFAULT_KIND:
                continue
            failed.append(
                {
                    "index": entry["index"],
                    "title": entry["title"],
                    "check": "C2",
                    "reason": "正文页没被连续覆盖（空洞 / 重叠）",
                }
            )
    problems = sections_mod.check_contiguity(sections, first_body_page, last_body_page)
    gaps = [[start, end] for kind, start, end in problems if kind == "gap"]
    meta = {
        "level": level,
        "first_body_page": first_body_page,
        "last_body_page": last_body_page,
        "covered_pages": [first_body_page, last_body_page],
        "gaps": gaps,
        "sections": len(sections),
    }
    ok = coverage_ok
    reason = coverage_reason
    return ok, reason, failed, meta


def _strip_footnote_markers(text: str) -> str:
    """去掉单独成行的脚注标记（``a`` ``b`` ``①`` …）。

    人教版语文这类教材，正文页上标题和作者之间夹着一个脚注字母：

    ```
    7  敬业与乐业
    a        ← 脚注标记
    梁启超
    ```

    标题主体是「敬业与乐业梁启超」，去掉脚注标记后才连得上。
    """
    return "\n".join(
        line
        for line in (text or "").splitlines()
        if not _FOOTNOTE_MARKER_RE.fullmatch(line.strip())
    )


def _strip_pinyin(text: str) -> str:
    """去掉文字里的拉丁字母（拼音注音）。

    小学一二年级的课文标题在 PDF 里是「毛笔字图 + 每个字头上标拼音」，
    文本层里是「秋qiū天tiān」这种交错的样子 —— 标题正文压根不是连续字符串，
    C3 对这类条目必然不过。去掉拼音再找一次就能接上。
    """
    return _LATIN_RUN_RE.sub("", text or "")


def _title_found(core: str, texts: list[str]) -> bool:
    """标题在若干页文字里找得到吗（判据同 C3 / C4）。"""
    if not core:
        return False
    for text in texts:
        if core in normalize(text):
            return True
    # 脚注标记把标题和作者隔开时，去掉标记再找一次
    for text in texts:
        if core in normalize(_strip_footnote_markers(text)):
            return True
    # 标题是「字图 + 拼音注音」时，去掉拼音再找一次
    for text in texts:
        if core in normalize(_strip_pinyin(text)):
            return True
    for text in texts:
        for line in (text or "").splitlines():
            if best_window_similarity(core, normalize(line)) >= TITLE_MATCH_THRESHOLD:
                return True
            if best_window_similarity(
                core, normalize(_strip_pinyin(line))
            ) >= TITLE_MATCH_THRESHOLD:
                return True
    return False


def _title_parts(title: str) -> list[str]:
    """把「栏目：主题」这种标题拆成两半（两边都要 ≥2 个字才拆）。"""
    parts = [part.strip() for part in re.split(r"[：:]|——|—{2,}", title or "") if part.strip()]
    if len(parts) < 2:
        return []
    if any(len(part) < 2 for part in parts):
        return []
    return parts


def _title_parts_found(title: str, texts: list[str]) -> bool:
    """「口语交际：转述」这种标题：栏目徽标和主题在页上是两块文字，接不成整串。

    只要**每一半都在同一批页文字里**就算找到 —— 目录上印的就是这么一行，
    拆成两半只是正文页版式造成的，不该判成「模型编出来的条目」。
    """
    parts = _title_parts(title)
    if not parts:
        return False
    # 每半也要剥编号前缀（口径跟 title_core 一样）：目录上的序号印在徽标里，
    # OCR 出来的文字里没有 —— 「8 数学广角——优化」的「8 」不该成为匹配条件。
    cores = [normalize(strip_section_number(part)) for part in parts]
    cores = [
        core if len(core) >= 2 else normalize(part)
        for core, part in zip(cores, parts, strict=False)
    ]
    for text in texts:
        stripped = normalize(_strip_pinyin(_strip_footnote_markers(text or "")))
        if all(core and core in stripped for core in cores):
            return True
    return False


def _check_c3(
    entries: list[dict],
    provider: _TextProvider,
    page_count: int,
) -> tuple[bool, str, list[dict]]:
    """C3 标题在正文页上：每条 body 条目的标题要在它的物理页（±1 页）上。"""
    failed: list[dict] = []
    checked = 0
    for entry in entries:
        if entry["kind"] != DEFAULT_KIND:
            continue
        page = entry.get("page")
        if page is None:
            continue
        checked += 1
        candidates = [
            number
            for number in range(page - TITLE_PAGE_WINDOW, page + TITLE_PAGE_WINDOW + 1)
            if 1 <= number <= page_count
        ]
        texts = [
            variant
            for number in candidates
            for variant in provider.text_variants(number)
        ]
        core = title_core(entry["title"])
        if not _title_found(core, texts) and not _title_parts_found(
            entry["title"], texts
        ):
            failed.append(
                {
                    "index": entry["index"],
                    "title": entry["title"],
                    "check": "C3",
                    "reason": f"标题在第 {page} 页（±{TITLE_PAGE_WINDOW} 页）上找不到",
                }
            )
    if failed:
        return (
            False,
            f"C3 不通过：{len(failed)}/{checked} 条 body 条目的标题在正文页上找不到 —— "
            + "；".join(f"「{item['title']}」" for item in failed[:5]),
            failed,
        )
    return True, f"C3 通过：{checked} 条 body 条目的标题都在正文页上找到了", failed


def _check_c4(
    entries: list[dict],
    toc_pages: list[int],
    provider: _TextProvider,
    page_count: int,
    *,
    source: str = "",
) -> tuple[bool, str, list[dict]]:
    """C4 标题在目录页上：每条标题都要出现在目录页文字里。

    **书里根本没有目录时，这条判据不适用**（没有可对照的东西）：
    草稿里声明 ``source: "body"``（目录是从正文抽出来的）时判「不适用」，
    不算失败 —— 否则这类书永远过不了闸门。
    没声明来源又没目录页，仍然按「忘了 locate」判不通过。
    """
    if not toc_pages:
        if source == "body":
            return True, (
                "C4 不适用：这本书没有目录页，目录是从正文抽出来的"
                "（已用 C3 对照正文页）"
            ), []
        return False, "C4 不通过：没有目录页（先跑 bookcard tocv locate 记下目录页）", []

    texts = [
        variant
        for number in toc_pages
        if 1 <= number <= page_count
        for variant in provider.text_variants(number)
    ]
    failed: list[dict] = []
    for entry in entries:
        core = title_core(entry["title"])
        if not _title_found(core, texts) and not _title_parts_found(
            entry["title"], texts
        ):
            failed.append(
                {
                    "index": entry["index"],
                    "title": entry["title"],
                    "check": "C4",
                    "reason": f"标题在目录页（{'、'.join(str(n) for n in toc_pages)}）上找不到",
                }
            )
    if failed:
        return (
            False,
            f"C4 不通过：{len(failed)}/{len(entries)} 条标题在目录页上找不到 —— "
            + "；".join(f"「{item['title']}」" for item in failed[:5])
            + "；多半是模型编出来的条目",
            failed,
        )
    return (
        True,
        f"C4 通过：{len(entries)} 条标题都在目录页（第 "
        f"{'、'.join(str(n) for n in toc_pages)} 页）上找到了",
        failed,
    )


def _check_c5(entries: list[dict]) -> tuple[bool, str, list[dict]]:
    """C5 层级：第一条 body 是 1 级、层级不跳级、同级编号连续。"""
    failed: list[dict] = []
    problems: list[str] = []

    body = [entry for entry in entries if entry["kind"] == DEFAULT_KIND]
    if not body:
        return False, "C5 不通过：一条 body 条目都没有", []
    if body[0]["level"] != 1:
        message = f"第一条 body 条目「{body[0]['title']}」的层级是 {body[0]['level']}，应该是 1"
        problems.append(message)
        failed.append(
            {
                "index": body[0]["index"],
                "title": body[0]["title"],
                "check": "C5",
                "reason": message,
            }
        )

    previous_level: int | None = None
    for entry in entries:
        if previous_level is not None and entry["level"] - previous_level > 1:
            message = (
                f"第 {entry['index'] + 1} 条「{entry['title']}」的层级从 {previous_level} "
                f"直接跳到 {entry['level']}（最多往下跳一级）"
            )
            problems.append(message)
            failed.append(
                {
                    "index": entry["index"],
                    "title": entry["title"],
                    "check": "C5",
                    "reason": message,
                }
            )
        previous_level = entry["level"]

    # 同级编号连续：按 (层级, 序列) 分组，编号回退算“重新起编”，不判失败。
    sequences: dict[tuple[int, str], int] = {}
    for entry in entries:
        numbered = entry_number(entry["title"])
        if numbered is None:
            continue
        number, sequence = numbered
        key = (entry["level"], sequence)
        last = sequences.get(key)
        if last is None:
            sequences[key] = number
            continue
        if number == last:
            message = f"第 {entry['index'] + 1} 条「{entry['title']}」的编号和上一条重复了"
            problems.append(message)
            failed.append(
                {
                    "index": entry["index"],
                    "title": entry["title"],
                    "check": "C5",
                    "reason": message,
                }
            )
        elif number > last + 1:
            missing = list(range(last + 1, number))
            message = (
                f"第 {entry['index'] + 1} 条「{entry['title']}」的编号跳了："
                f"缺 {'、'.join(str(item) for item in missing)}"
            )
            problems.append(message)
            failed.append(
                {
                    "index": entry["index"],
                    "title": entry["title"],
                    "check": "C5",
                    "reason": message,
                }
            )
        # number < last：重新起编（新的一章），不算失败。
        sequences[key] = number

    if problems:
        return False, "C5 不通过：" + "；".join(problems[:5]), failed
    return True, f"C5 通过：{len(entries)} 条层级连贯，同级编号连续", failed


def _align_entries(
    left: list[dict],
    right: list[dict],
    *,
    threshold: float = TITLE_MATCH_THRESHOLD,
) -> list[tuple[int, int, float]]:
    """按顺序对齐两组条目（标题规整后做编辑距离匹配）。

    返回 ``[(左下标, 右下标, 相似度), ...]``。
    """
    scores = [
        [
            best_window_similarity(title_core(left_item["title"]), title_core(right_item["title"]))
            for right_item in right
        ]
        for left_item in left
    ]
    rows = len(left)
    columns = len(right)
    table = [[0.0] * (columns + 1) for _ in range(rows + 1)]
    for index in range(1, rows + 1):
        for offset in range(1, columns + 1):
            best = max(table[index - 1][offset], table[index][offset - 1])
            score = scores[index - 1][offset - 1]
            if score >= threshold:
                best = max(best, table[index - 1][offset - 1] + score)
            table[index][offset] = best

    pairs: list[tuple[int, int, float]] = []
    index = rows
    offset = columns
    while index > 0 and offset > 0:
        score = scores[index - 1][offset - 1]
        if (
            score >= threshold
            and abs(table[index][offset] - (table[index - 1][offset - 1] + score)) < 1e-9
        ):
            pairs.append((index - 1, offset - 1, score))
            index -= 1
            offset -= 1
        elif table[index - 1][offset] >= table[index][offset - 1]:
            index -= 1
        else:
            offset -= 1
    pairs.reverse()
    return pairs


def _check_c6(
    entries: list[dict],
    state: dict,
    provider: _TextProvider,
) -> tuple[bool, str, dict]:
    """C6 对照：和规则解析结果、书签比一比（只出报告，不判失败）。"""
    page_count = int(state.get("page_count") or 0)
    report: dict[str, Any] = {"rule": None, "bookmarks": None, "skipped": []}

    rule_entries: list[dict] = []
    source = None
    if provider.digital:
        info = provider.analysis()
        source, rule_entries = toc_mod._pick_source(info, Path(state["pdf_path"]))
        report["rule"] = {
            "source": source,
            "entries": len(rule_entries),
            "missing": [],
            "extra": [],
            "page_mismatch": [],
        }
    else:
        report["skipped"].append(
            "扫描件没有现成的 OCR 产物，规则对照跳过（可以用 bookcard text 建工作目录后再比）"
        )

    if rule_entries:
        pairs = _align_entries(rule_entries, entries)
        matched_rule = {left for left, _, _ in pairs}
        matched_vision = {right for _, right, _ in pairs}
        for index, left in enumerate(rule_entries):
            if index not in matched_rule:
                report["rule"]["missing"].append(
                    {"title": left["title"], "page": left["page"]}
                )
        for index, right in enumerate(entries):
            if index not in matched_vision:
                report["rule"]["extra"].append(
                    {"title": right["title"], "page": right.get("page")}
                )
        for left_index, right_index, _ in pairs:
            left = rule_entries[left_index]
            right = entries[right_index]
            if left.get("page") != right.get("page"):
                report["rule"]["page_mismatch"].append(
                    {
                        "title": right["title"],
                        "rule_page": left.get("page"),
                        "vision_page": right.get("page"),
                    }
                )

    # 书签：每页一个自动书签的（数量接近页数）不当目录用。
    try:
        doc = _open_pdf(state)
        try:
            raw = doc.get_toc()
        finally:
            doc.close()
    except Exception:  # 书签读不出来就跳过，不判失败
        raw = []
    classified = probe_rules.classify_bookmarks(raw, page_count)
    if classified["manual"] >= 3:
        # 只比**人工书签**——自动页锚点（每页一个）不是目录，比了全是噪声。
        bookmarks = [
            {
                "level": int(entry[0]),
                "title": str(entry[1]).strip(),
                "page": int(entry[2]),
            }
            for entry in raw
            if len(entry) >= 3
            and entry[2]
            and probe_rules.classify_bookmark_title(str(entry[1]), entry[2]) == "manual"
        ]
        pairs = _align_entries(bookmarks, entries)
        matched_left = {left for left, _, _ in pairs}
        matched_right = {right for _, right, _ in pairs}
        missing = [
            {"title": bookmarks[index]["title"], "page": bookmarks[index]["page"]}
            for index in range(len(bookmarks))
            if index not in matched_left
        ]
        extra = [
            {"title": entries[index]["title"], "page": entries[index].get("page")}
            for index in range(len(entries))
            if index not in matched_right
        ]
        mismatch = []
        for left_index, right_index, _ in pairs:
            if bookmarks[left_index]["page"] != entries[right_index].get("page"):
                mismatch.append(
                    {
                        "title": entries[right_index]["title"],
                        "bookmark_page": bookmarks[left_index]["page"],
                        "vision_page": entries[right_index].get("page"),
                    }
                )
        report["bookmarks"] = {
            "total": classified["total"],
            "manual": classified["manual"],
            "missing": missing,
            "extra": extra,
            "page_mismatch": mismatch,
        }
    elif classified["total"]:
        report["skipped"].append(
            f"书签有 {classified['total']} 条但只有 {classified['manual']} 条人工书签"
            "（其余是自动页锚点），当无效书签，不比"
        )
    else:
        report["skipped"].append("这本书没有书签")

    return True, "C6 对照只出报告，不判失败", report


# ===================== 提交 =====================


def submit_draft(
    work_dir: str | Path,
    draft_path: str | Path,
    *,
    by: str,
) -> dict:
    """交草稿：换算物理页、跑检查、写 ``toc.json`` 和 ``tocv_report.json``。

    ``gate.status`` **永远不会是 ``approved``**：最多 ``pending``，判据不过就
    ``rejected``——闸门 1 照旧由人或 ``toc_gate`` 拍板。
    """
    state = load_state(work_dir)
    page_count = int(state.get("page_count") or 0)
    payload = common.read_json(draft_path)
    entries, anchors, draft_toc_pages = _parse_draft(payload)
    # 草稿可以声明目录的来源：「body」= 这本书没有目录页，目录是从正文抽出来的
    draft_source = str(payload.get("source") or "") if isinstance(payload, dict) else ""

    toc_pages = draft_toc_pages or list(state.get("toc_pages") or [])

    for entry in entries:
        entry["page"] = (
            convert_pages(entry["printed_page"], anchors)
            if entry["printed_page"] is not None
            else None
        )

    doc = _open_pdf(state)
    try:
        provider = _TextProvider(Path(work_dir), state, doc)
        checks: list[dict] = []
        failed_entries: list[dict] = []

        c1_ok, c1_reason, c1_failed = _check_c1(entries, anchors, page_count)
        checks.append({"id": "C1", "name": "页码", "passed": c1_ok, "reason": c1_reason})
        failed_entries.extend(c1_failed)

        c2_ok, c2_reason, c2_failed, c2_meta = _check_c2(entries)
        checks.append({"id": "C2", "name": "覆盖与粒度", "passed": c2_ok, "reason": c2_reason})
        failed_entries.extend(c2_failed)

        c3_ok, c3_reason, c3_failed = _check_c3(entries, provider, page_count)
        checks.append({"id": "C3", "name": "标题在正文页上", "passed": c3_ok, "reason": c3_reason})
        failed_entries.extend(c3_failed)

        c4_ok, c4_reason, c4_failed = _check_c4(
            entries, toc_pages, provider, page_count, source=draft_source
        )
        checks.append({"id": "C4", "name": "标题在目录页上", "passed": c4_ok, "reason": c4_reason})
        failed_entries.extend(c4_failed)

        c5_ok, c5_reason, c5_failed = _check_c5(entries)
        checks.append({"id": "C5", "name": "层级", "passed": c5_ok, "reason": c5_reason})
        failed_entries.extend(c5_failed)

        c6_ok, c6_reason, c6_report = _check_c6(entries, state, provider)
        checks.append(
            {
                "id": "C6",
                "name": "对照（只出报告）",
                "passed": c6_ok,
                "reason": c6_reason,
                "report_only": True,
            }
        )
    finally:
        doc.close()

    failed_entries.sort(key=lambda item: (item["index"] is None, item["index"] or 0))

    # ---- 轮次与状态 ----
    report_path = Path(work_dir) / "tocv_report.json"
    round_number = 1
    if report_path.is_file():
        previous = common.read_json(report_path)
        if isinstance(previous, dict):
            round_number = int(previous.get("round") or 0) + 1

    passed_all = all(check["passed"] for check in checks if check["id"] in FAILING_CHECKS)
    if passed_all:
        status = "ok"
    elif round_number < MAX_ROUNDS:
        status = "retry"
    else:
        status = "needs_human"

    gate_status = "pending" if passed_all else "rejected"
    reasons = [check["reason"] for check in checks]

    # ---- toc.json（和现有格式一致，source 换成 vision）----
    # 偏移 = 前置页数（正数：PDF 页 = 印刷页 + offset），从锚点取众数
    _offsets = [a["physical_page"] - a["printed_page"] for a in anchors]
    offset = max(set(_offsets), key=_offsets.count) if _offsets else None
    toc_payload = {
        "source": SOURCE,
        # 这本书的目录是从哪来的：「body」= 没有目录页，从正文抽的
        "origin": draft_source,
        # 前置页数（正数）
        "offset": offset,
        # 目录所在的页（没有目录页就是空）
        "pages": list(toc_pages),
        "entries": [
            {
                "level": entry["level"],
                "title": entry["title"],
                "page": entry.get("page"),
                # 条目自己印的页码（= page − offset）
                "printed_page": entry.get("printed_page"),
                "kind": entry.get("kind", DEFAULT_KIND),
            }
            for entry in entries
        ],
        "checks": {
            "pages_monotonic": c1_ok,
            "first_body_page": c2_meta.get("first_body_page", 1),
            "covered_pages": c2_meta.get(
                "covered_pages", [c2_meta.get("first_body_page", 1)] * 2
            ),
            "gaps": c2_meta.get("gaps", []),
            "level": c2_meta.get("level"),
        },
        "gate": {
            "status": gate_status,
            "approved_by": "",
            "at": _now(),
            "reasons": reasons,
            "checks_passed": passed_all,
        },
    }
    common.write_json(Path(work_dir) / "toc.json", toc_payload)

    report_payload = {
        "round": round_number,
        "status": status,
        "submitted_by": by,
        "submitted_at": _now(),
        "toc_pages": toc_pages,
        "checks": checks,
        "failed_entries": failed_entries,
        "comparison": c6_report,
        "entries": [
            {
                "index": entry["index"],
                "level": entry["level"],
                "title": entry["title"],
                "printed_page": entry["printed_page"],
                "page": entry.get("page"),
                "kind": entry["kind"],
            }
            for entry in entries
        ],
    }
    common.write_json(report_path, report_payload)

    # 存最后一次草稿，给 score 用。
    state["last_draft"] = {
        "toc_pages": toc_pages,
        "entries": [
            {
                "level": entry["level"],
                "title": entry["title"],
                "printed_page": entry["printed_page"],
                "page": entry.get("page"),
                "kind": entry["kind"],
            }
            for entry in entries
        ],
        "submitted_by": by,
        "round": round_number,
    }
    state["toc_pages"] = toc_pages or state.get("toc_pages") or []
    save_state(work_dir, state)

    return {
        "ok": status == "ok",
        "status": status,
        "round": round_number,
        "entries": len(entries),
        "failed": len(failed_entries),
        "failed_entries": failed_entries,
        "checks": checks,
        "toc_path": str(Path(work_dir) / "toc.json"),
        "report_path": str(report_path),
        "gate_status": gate_status,
    }


# ===================== 打分 =====================


def score_draft(work_dir: str | Path, gold_path: str | Path) -> dict:
    """和 gold 对照：查准 / 查全 / F1、页码一致率、层级一致率、目录页定位。"""
    state = load_state(work_dir)
    gold = common.read_json(gold_path)
    if not isinstance(gold, dict):
        raise DraftError("gold 必须是一个 JSON 对象")
    gold_entries = gold.get("entries")
    if not isinstance(gold_entries, list):
        raise DraftError("gold 里没有 entries 数组")

    last = state.get("last_draft")
    if not last:
        raise TocvStateError(
            f"工作目录 {work_dir} 里还没有提交过草稿（没有 last_draft）：先跑 bookcard tocv submit"
        )
    draft_entries = [
        {
            "level": int(item.get("level") or 1),
            "title": str(item.get("title") or ""),
            "page": item.get("page"),
        }
        for item in last.get("entries") or []
    ]

    # gold 的条目用 ``physical_page``（不是 ``printed_page``）——
    # ``physical_page`` 是 null 的条目只比对标题和层级，不参加页码一致率。
    normalized_gold = [
        {
            "level": int(item.get("level") or 1),
            "title": str(item.get("title") or ""),
            "page": item.get("physical_page"),
        }
        for item in gold_entries
    ]

    pairs = _align_entries(normalized_gold, draft_entries)
    matched_gold = {left for left, _, _ in pairs}
    matched_draft = {right for _, right, _ in pairs}

    total_gold = len(normalized_gold)
    total_draft = len(draft_entries)
    hits = len(pairs)

    precision = hits / total_draft if total_draft else 0.0
    recall = hits / total_gold if total_gold else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    page_same = 0
    page_compared = 0
    level_same = 0
    page_detail: list[dict] = []
    level_detail: list[dict] = []
    for left_index, right_index, _ in pairs:
        gold_item = normalized_gold[left_index]
        draft_item = draft_entries[right_index]
        # gold 里 ``physical_page`` 是 null 的条目只比对标题和层级，
        # 不参加页码一致率（那一列本来就没印页码）。
        if gold_item["page"] is not None:
            page_compared += 1
            if gold_item["page"] == draft_item["page"]:
                page_same += 1
            else:
                page_detail.append(
                    {
                        "title": draft_item["title"],
                        "gold_page": gold_item["page"],
                        "draft_page": draft_item["page"],
                    }
                )
        if gold_item["level"] == draft_item["level"]:
            level_same += 1
        else:
            level_detail.append(
                {
                    "title": draft_item["title"],
                    "gold_level": gold_item["level"],
                    "draft_level": draft_item["level"],
                }
            )

    gold_toc_pages = sorted({int(item) for item in (gold.get("toc_pages") or [])})
    draft_toc_pages = sorted({int(item) for item in (last.get("toc_pages") or [])})
    toc_pages_ok = gold_toc_pages == draft_toc_pages

    missing = [
        {"title": normalized_gold[index]["title"], "page": normalized_gold[index]["page"]}
        for index in range(total_gold)
        if index not in matched_gold
    ]
    extra = [
        {"title": draft_entries[index]["title"], "page": draft_entries[index]["page"]}
        for index in range(total_draft)
        if index not in matched_draft
    ]

    payload = {
        "gold": str(Path(gold_path)),
        "gold_entries": total_gold,
        "draft_entries": total_draft,
        "matched": hits,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "page_exact_rate": round(page_same / page_compared, 4) if page_compared else 0.0,
        "page_compared": page_compared,
        "level_exact_rate": round(level_same / hits, 4) if hits else 0.0,
        "toc_pages": {
            "gold": gold_toc_pages,
            "draft": draft_toc_pages,
            "correct": toc_pages_ok,
        },
        "missing": missing,
        "extra": extra,
        "page_mismatch": page_detail,
        "level_mismatch": level_detail,
    }
    common.write_json(Path(work_dir) / "score.json", payload)
    return payload


def summarize_score(payload: dict) -> str:
    """一行摘要。"""
    toc_pages = payload.get("toc_pages") or {}
    return (
        f"查准 {payload['precision']:.2f} / 查全 {payload['recall']:.2f} / "
        f"F1 {payload['f1']:.2f}；页码一致 {payload['page_exact_rate']:.2f}，"
        f"层级一致 {payload['level_exact_rate']:.2f}；"
        f"目录页{'对' if toc_pages.get('correct') else '不对'}"
        f"（漏 {len(payload.get('missing') or [])} 条，多 {len(payload.get('extra') or [])} 条）"
    )
