"""§2.1 合并口径：把 PDF 的强行折行按正常行文合并。

契约 §2.1（``docs/contract-v1.md``）：

- 段落界 ``\\n`` 保留成 ``\\n``；软折行 ``\\n``
  - 两侧都是 CJK 字符或 CJK 标点 ⇒ **删掉**；
  - 两侧有一侧是拉丁字母或数字 ⇒ 换成一个**空格**；
  - 行尾是 ``-`` 且两侧都是小写拉丁字母 ⇒ 删掉 ``\\n``，**保留** ``-``
    （不猜断词）。
- 行首行尾的空白一并压掉；连续空格压成一个。
- ``offset_map[i]`` = ``out[i]`` 在原 ``text`` 里的偏移。

**全仓只有这一份实现**：OCR 单、文本场景、测试都调它。

原 ``text`` **不改**（``text_sha1``、候选和金标准的偏移都基于它）；这里只
产出给人看的合并文字，以及换算偏移用的 ``offset_map``。
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence

__all__ = [
    "is_cjk_char",
    "is_latin_or_digit",
    "map_offset",
    "map_offset_range",
    "map_span",
    "reflow",
    "reflow_span",
]


# ===================== 字符分类 =====================


def is_cjk_char(char: str) -> bool:
    """是不是 CJK 字符或 CJK 标点（决定软折行是"直接接上"还是"空一格"）。"""
    if not char:
        return False
    code = ord(char)
    if 0x4E00 <= code <= 0x9FFF:  # 基本汉字
        return True
    if 0x3400 <= code <= 0x4DBF:  # 扩展 A
        return True
    if 0xF900 <= code <= 0xFAFF:  # 兼容汉字
        return True
    if 0x20000 <= code <= 0x2A6DF:  # 扩展 B
        return True
    if 0x3000 <= code <= 0x303F:  # CJK 标点（、。《》「」……）
        return True
    if 0xFF01 <= code <= 0xFF60:  # 全角标点 + 全角字母数字
        return True
    if 0xFFE0 <= code <= 0xFFE6:  # 全角货币符号
        return True
    return code in {
        0x00B7,  # 间隔号
        0x2013,  # en dash
        0x2014,  # em dash（中文破折号）
        0x2018,  # 左单引号
        0x2019,  # 右单引号
        0x201C,  # 左双引号
        0x201D,  # 右双引号
        0x2026,  # 省略号
    }


def is_latin_or_digit(char: str) -> bool:
    """是不是拉丁字母或数字（决定软折行要不要空一格）。"""
    if not char:
        return False
    if char.isascii():
        return char.isalpha() or char.isdigit()
    code = ord(char)
    if 0x00C0 <= code <= 0x024F:  # 拉丁字母补充区 / 扩展 A/B
        return True
    return char.isdigit()


def _is_lowercase_latin(char: str) -> bool:
    """是不是小写拉丁字母（行尾连字符的判据）。"""
    return bool(char) and char.isascii() and char.isalpha() and char.islower()


# ===================== 内部工具 =====================


def _break_set(text: str, breaks: Iterable[int] | None) -> set[int]:
    """段落界偏移集合。

    ``breaks is None`` ⇒ **所有** ``\\n`` 都当段落界（老数据没有 ``breaks``
    字段：不合并，行为不变，契约 §2.1）。
    """
    if breaks is None:
        return {index for index, char in enumerate(text) if char == "\n"}
    return {int(offset) for offset in breaks}


def _lines_of(text: str) -> list[tuple[int, int, str]]:
    """把原文切成行：``[(行起始偏移, 行尾偏移（不含换行）, 行内容（已去首尾空白）)]``。"""
    lines: list[tuple[int, int, str]] = []
    position = 0
    length = len(text)
    while True:
        newline = text.find("\n", position)
        end = length if newline == -1 else newline
        lines.append((position, end, text[position:end].strip()))
        if newline == -1:
            break
        position = newline + 1
    return lines


def _emit_line(
    content: str,
    start: int,
    out_chars: list[str],
    out_offsets: list[int],
) -> None:
    """把一行写进结果：首尾空白压掉，连续空白压成一个空格。"""
    index = 0
    length = len(content)
    while index < length:
        char = content[index]
        if char.isspace():
            run_start = index
            while index < length and content[index].isspace():
                index += 1
            # 连续空白压成一个空格（偏移记在空白的第一格上）。
            out_chars.append(" ")
            out_offsets.append(start + run_start)
            continue
        out_chars.append(char)
        out_offsets.append(start + index)
        index += 1


def _join_soft_wrap(
    left: str,
    right: str,
    newline_offset: int,
    out_chars: list[str],
    out_offsets: list[int],
) -> None:
    """软折行的接法（契约 §2.1 的合并口径）。

    ``left`` = 上一行最后一个字符，``right`` = 下一行第一个字符。
    """
    if not left or not right:
        return

    # 行尾是 ``-`` 且两侧都是小写拉丁字母 ⇒ 删掉换行，保留 ``-``（不猜断词）。
    if left == "-" and _is_lowercase_latin(right):
        return

    if is_cjk_char(left) and is_cjk_char(right):
        return  # 中文软折行：直接接上

    if is_latin_or_digit(left) or is_latin_or_digit(right):
        # 中英混排 ⇒ 空一格。
        out_chars.append(" ")
        out_offsets.append(newline_offset)


# ===================== 合并 =====================


def reflow(text: str, breaks: Iterable[int] | None = None) -> tuple[str, list[int]]:
    """按契约 §2.1 的合并口径合并软折行。

    返回 ``(out, offset_map)``：

    - ``out`` = 合并后的文字（段落界仍是 ``\\n``）；
    - ``offset_map[i]`` = ``out[i]`` 在原 ``text`` 里的偏移。

    ``breaks`` = ``text`` 里真正的段落结束那些 ``\\n`` 的偏移（升序）。
    ``breaks is None`` ⇒ 所有 ``\\n`` 都当段落界（老数据，不合并）。
    """
    if not text:
        return "", []

    break_set = _break_set(text, breaks)
    lines = _lines_of(text)

    out_chars: list[str] = []
    out_offsets: list[int] = []

    for index, (start, end, content) in enumerate(lines):
        _emit_line(content, start, out_chars, out_offsets)
        if index >= len(lines) - 1:
            break
        newline_offset = end  # ``end`` 就是这个换行符在原文里的偏移
        if newline_offset in break_set:
            out_chars.append("\n")
            out_offsets.append(newline_offset)
            continue
        _join_soft_wrap(
            content[-1] if content else "",
            lines[index + 1][2][:1],
            newline_offset,
            out_chars,
            out_offsets,
        )

    return "".join(out_chars), out_offsets


def reflow_span(
    text: str,
    breaks: Iterable[int] | None,
    begin: int,
    end: int,
) -> str:
    """把原文的 ``[begin, end)`` 这一段按合并口径合并。

    段落界偏移换算成相对 ``begin`` 的偏移；段内外的段落界都不影响结果
    （段落界本身在合并结果里保留成 ``\\n``）。
    """
    if not text or begin >= end:
        return ""
    begin = max(0, begin)
    end = min(len(text), end)
    if begin >= end:
        return ""
    if breaks is None:
        local: list[int] | None = None
    else:
        local = [int(offset) - begin for offset in breaks if begin <= int(offset) < end]
    merged, _ = reflow(text[begin:end], local)
    return merged


def map_offset(offset_map: Sequence[int], original_offset: int) -> int:
    """原文偏移 → 合并后文字的偏移。

    ``offset_map`` 是 :func:`reflow` 的第二个返回值（单调不减）。
    原文里被删掉的字符（软折行的换行）落在它后面那个字符的位置。
    """
    if not offset_map:
        return 0
    position = bisect_right(offset_map, original_offset) - 1
    if position < 0:
        return 0
    return position


def map_offset_range(
    offset_map: Sequence[int], begin: int, end: int
) -> tuple[int, int]:
    """原文区间 ``[begin, end)`` → 合并后文字的区间。"""
    if not offset_map:
        return 0, 0
    start = bisect_left(offset_map, begin)
    if start >= len(offset_map):
        start = len(offset_map) - 1
    stop = bisect_left(offset_map, max(begin, end - 1))
    return start, min(stop + 1, len(offset_map))


def map_span(offset_map: Sequence[int], begin: int, end: int) -> tuple[int, int]:
    """原文区间 ``[begin, end)`` → 合并后文字的区间（切片用）。

    ``merged[map_span(...)[0] : map_span(...)[1]]`` 就是原文那一段合并后的
    文字——和 :func:`reflow_span` 的结果逐字一致。
    """
    if not offset_map:
        return 0, 0
    start = bisect_left(offset_map, begin)
    stop = bisect_left(offset_map, max(begin, end))
    return min(start, len(offset_map)), min(stop, len(offset_map))
