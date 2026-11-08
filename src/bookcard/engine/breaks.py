"""§2.1 段落界 ``breaks``：按版面几何算一页里**真正的段落结束**。

契约 §2.1（``docs/contract-v1.md``）——一行满足任一条，它后面的 ``\\n``
就是段落界：

1. 这一行右端离版心右边界超过 1.5 个字宽（行没写满）；
2. 下一行首缩进超过 1.5 个字宽；
3. 下一行和这一行的行距超过本页中位行距的 1.6 倍，或字号相差超过 15%；
4. 下一行属于另一个 PyMuPDF text block，且两块左边界相差超过 1.5 个字宽。

**纯函数**：入参是"一页的行列表"（每行 ``text`` / ``bbox`` / ``size`` /
``block``），出参是 ``text`` 里段落结束那些 ``\\n`` 的偏移（升序）。
**不依赖 PyMuPDF 对象** —— 下一张 OCR 单把 OCR 行框喂给同一个函数。

两处口径上的补充（实测三本真书后定的，理由写在这里）：

- **版心按列算**（多栏页 / 旁栏页）：一页里常有一栏正文 + 一栏旁栏，两栏的
  右端差好几十个点。按整页算 90 分位，旁栏会把正文的右边界拉偏 ⇒ 整页正文
  的每一行都会被判成"没写满"（每个换行都成段落界，合并就白做了）。所以按
  **列**（竖直相邻的一串行）分别算版心右边界。
- **列的切分**：相邻两行算同一列，当且仅当下一行在上一行**下方**、且两者
  的行距不超过本页中位行距的 :data:`COLUMN_PITCH_FACTOR` 倍（图注、表格、
  旁栏之间有明显的空隙）。列左边界 = 该列所有行 x0 的最小值。
"""

from __future__ import annotations

from typing import Any

__all__ = ["compute_breaks", "join_lines"]


#: 判据 1/2/4 的阈值：1.5 个字宽。
INDENT_FACTOR = 1.5

#: 判据 3 的行距阈值：1.6 倍中位行距。
PITCH_FACTOR = 1.6

#: 判据 3 的字号阈值：相差超过 15%。
SIZE_FACTOR = 0.15

#: 相邻两行算同一列的最大行距（几倍中位行距）——图注、表格、旁栏之间有空隙，
#: 那些要另起一列（版心右边界要按列算）。
COLUMN_PITCH_FACTOR = 3.0


# ===================== 行的几何 =====================


def _char_width(line: dict, fallback: float) -> float:
    """这一行的"字宽"（契约 §2.1：字宽取这一行的字号）。"""
    try:
        size = float(line.get("size") or 0.0)
    except (TypeError, ValueError):
        size = 0.0
    return size if size > 0 else fallback


def _bbox(line: dict) -> tuple[float, float, float, float]:
    """这一行的框（``x0, y0, x1, y1``）；缺了就当全零。"""
    bbox = line.get("bbox") or ()
    values: list[float] = []
    for value in list(bbox)[:4]:
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            values.append(0.0)
    while len(values) < 4:
        values.append(0.0)
    return values[0], values[1], values[2], values[3]


def _percentile(values: list[float], ratio: float) -> float:
    """分位数（线性插值）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = ratio * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


# ===================== 页文字 =====================


def join_lines(lines: list[dict]) -> str:
    """一页的行拼成页文字（一行一个换行，行首尾空白去掉）。

    和 :func:`bookcard.engine.extract.page_text` 是同一份实现
    （那边直接调这里）——``breaks`` 的偏移必须落在这份文字上。
    """
    return "\n".join(str(line.get("text") or "").strip() for line in lines)


def _newline_offsets(texts: list[str]) -> list[int]:
    """每个换行符在页文字里的偏移（第 i 个换行符分隔第 i 行和第 i+1 行）。"""
    offsets: list[int] = []
    position = 0
    for text in texts[:-1]:
        position += len(text)
        offsets.append(position)
        position += 1
    return offsets


# ===================== 列 =====================


def _assign_columns(geometry: list[dict], median_pitch: float) -> list[int]:
    """每一行属于哪一列（列 = 竖直相邻的一串行）。

    下一行还在上一行**下方**、且两行的行距不超过中位行距的
    :data:`COLUMN_PITCH_FACTOR` 倍 ⇒ 同一列；否则另起一列。
    """
    columns = [0] * len(geometry)
    current = 0
    for index in range(1, len(geometry)):
        previous = geometry[index - 1]
        line = geometry[index]
        pitch = line["y0"] - previous["y0"]
        if line["y0"] <= previous["y0"] or pitch > COLUMN_PITCH_FACTOR * median_pitch:
            current += 1
        columns[index] = current
    return columns


# ===================== 段落界 =====================


def _neighbour_line(
    geometry: list[dict], columns: list[int], index: int
) -> dict | None:
    """判据 1 用的参照行：同一列的上一行，没有就看下一行。"""
    if index > 0 and columns[index - 1] == columns[index]:
        return geometry[index - 1]
    if index + 1 < len(geometry):
        return geometry[index + 1]
    return None


def compute_breaks(lines: list[dict]) -> list[int]:
    """按契约 §2.1 的四条几何判据，算一页的段落界。

    入参：一页的行列表，每行 ``{"text", "bbox", "size", "block"}``。
    出参：页文字里真正的段落结束那些 ``\\n`` 的偏移（升序）。
    """
    if len(lines) < 2:
        return []

    texts = [str(line.get("text") or "").strip() for line in lines]
    newline_offsets = _newline_offsets(texts)

    geometry: list[dict[str, Any]] = []
    for line in lines:
        x0, y0, x1, y1 = _bbox(line)
        geometry.append(
            {
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "size": float(line.get("size") or 0.0),
                "block": line.get("block"),
                "width": 0.0,
            }
        )

    fallback_width = _median([item["size"] for item in geometry if item["size"] > 0])
    for item in geometry:
        item["width"] = _char_width(item, fallback_width)

    # 本页中位行距（相邻两行的行距；跨列/跨块的大间距不算行距）。
    pitches = [
        geometry[index]["y0"] - geometry[index - 1]["y0"]
        for index in range(1, len(geometry))
        if geometry[index]["y0"] > geometry[index - 1]["y0"]
    ]
    median_pitch = _median(pitches)

    columns = _assign_columns(geometry, median_pitch)

    # 每列的版心：左边界（x0 最小值）、右边界（x1 的 90 分位）。
    column_left: dict[int, float] = {}
    column_right: dict[int, list[float]] = {}
    for index, item in enumerate(geometry):
        column = columns[index]
        column_left[column] = min(column_left.get(column, item["x0"]), item["x0"])
        column_right.setdefault(column, []).append(item["x1"])
    column_right_edge = {
        column: _percentile(values, 0.9) for column, values in column_right.items()
    }

    # 本页中位行距（只算同一列内的相邻行——跨列的间距不是"行距"）。
    column_pitches: list[float] = []
    for index in range(1, len(geometry)):
        if columns[index] != columns[index - 1]:
            continue
        column_pitches.append(geometry[index]["y0"] - geometry[index - 1]["y0"])
    if column_pitches:
        median_pitch = _median(column_pitches)

    breaks: list[int] = []
    for index in range(len(geometry) - 1):
        current = geometry[index]
        following = geometry[index + 1]
        width = current["width"]
        if width <= 0:
            continue

        # 判据 1：这一行没写满（右端离版心右边界超过 1.5 个字宽），
        # **而且**比同一列的邻近行（上一行；没有就看下一行）短超过 1.5 个字宽。
        #   只看"离版心多远"会把**窄栏**（教材里常见：一段文字排得比别处窄，
        #   英语故事的阅读材料就是）每一行都判成"没写满"⇒ 每个换行都成段落界，
        #   合并白做。加上"比邻近的行也短"，才是真的"这段到此为止"。
        right_edge = column_right_edge.get(columns[index], current["x1"])
        if right_edge - current["x1"] > INDENT_FACTOR * width:
            reference = _neighbour_line(geometry, columns, index)
            if reference is not None and reference["x1"] - current["x1"] > INDENT_FACTOR * width:
                breaks.append(newline_offsets[index])
                continue

        # 判据 2：下一行首缩进超过 1.5 个字宽。
        if following["x0"] - current["x0"] > INDENT_FACTOR * width:
            breaks.append(newline_offsets[index])
            continue

        # 判据 3：行距超过本页中位行距的 1.6 倍，或字号相差超过 15%。
        pitch = following["y0"] - current["y0"]
        size_gap = abs(following["size"] - current["size"])
        bigger_size = max(following["size"], current["size"])
        if median_pitch > 0 and pitch > PITCH_FACTOR * median_pitch:
            breaks.append(newline_offsets[index])
            continue
        if bigger_size > 0 and size_gap > SIZE_FACTOR * bigger_size:
            breaks.append(newline_offsets[index])
            continue

        # 判据 4：换块（分栏、旁栏）且两块左边界相差超过 1.5 个字宽。
        if current["block"] != following["block"]:
            left_gap = abs(
                column_left.get(columns[index + 1], following["x0"])
                - column_left.get(columns[index], current["x0"])
            )
            if left_gap > INDENT_FACTOR * width:
                breaks.append(newline_offsets[index])
                continue

    return breaks
