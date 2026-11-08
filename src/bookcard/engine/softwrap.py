"""softwrap.py — PDF 硬折行的压缩与跨行查找（契约 §2.1 / §4）。

抽词段的文字带着 PDF 的硬换行：``量子\\n力学`` 里的 ``量子力学``、
``hard\\nline`` 里的 ``hard line`` 都算命中。本模块提供：

- :func:`compress_soft_wraps`：把硬折行按合并口径压掉，记下原偏移；
- :func:`find_all_flexible`：先逐字找，找不到再压折行找，命中映射回原文坐标；
- :func:`span_matches`：判断 ``text[start:end]`` 是不是 ``surface``（跨行也算）。
"""

from __future__ import annotations

import unicodedata

from bookcard.engine.reflow import is_cjk_char, is_latin_or_digit


def _find_all(text: str, needle: str) -> list[int]:
    """``needle`` 在 ``text`` 里的所有出现位置（码点下标，允许互相重叠）。"""
    hits: list[int] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index == -1:
            return hits
        hits.append(index)
        start = index + 1


def _last_nonspace(text: str, end: int) -> str:
    """``text[:end]`` 里最后一个非空白字符（没有就是空串）。"""
    index = end - 1
    while index >= 0 and text[index].isspace():
        index -= 1
    return text[index] if index >= 0 else ""


def _first_nonspace(text: str, start: int) -> str:
    """``text[start:]`` 里第一个非空白字符（没有就是空串）。"""
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    return text[index] if index < len(text) else ""


def compress_soft_wraps(text: str) -> tuple[str, list[int]]:
    """把 PDF 的硬折行按合并口径压掉（契约 §2.1），记下每个字符的原偏移。

    - 折行两侧都是 CJK ⇒ 直接接上（``量子\\n力学`` ⇒ ``量子力学``）；
    - 拉丁字母 / 数字参与的折行 ⇒ 空一格（``hard\\nline`` ⇒ ``hard line``）；
    - 空行（段落之间）⇒ 空一格，不让两个段落粘成一个词。

    返回 ``(压缩后的文字, 每个字符对应的原下标)``。
    """
    out: list[str] = []
    index_map: list[int] = []
    position = 0
    length = len(text)
    while position < length:
        char = text[position]
        if not char.isspace():
            out.append(char)
            index_map.append(position)
            position += 1
            continue

        run_start = position
        while position < length and text[position].isspace():
            position += 1
        run = text[run_start:position]
        if "\n" not in run:
            # 不是折行：连续空白压成一个空格。
            out.append(" ")
            index_map.append(run_start)
            continue

        left = _last_nonspace(text, run_start)
        right = _first_nonspace(text, position)
        if run.count("\n") > 1 or not left or not right:
            # 空行 / 段首段尾 ⇒ 空一格，不跨段落粘词。
            out.append(" ")
            index_map.append(run_start)
            continue
        if is_cjk_char(left) and is_cjk_char(right):
            continue  # 中文软折行：直接接上
        if is_latin_or_digit(left) or is_latin_or_digit(right):
            # 拉丁字母 / 数字参与的折行：空一格（"hard\nline" ⇒ "hard line"）。
            out.append(" ")
            index_map.append(run_start)
    return "".join(out), index_map


def nfkc_index(text: str) -> tuple[str, list[int], list[int]] | None:
    """逐字 NFKC 归一 + 每个归一字符对应的原文区间。

    有些 PDF 的文本层用**兼容表意文字**（理=U+F9E4、行=U+FA08……）或连字
    （ﬂ=U+FB02），而模型写出来的是标准码位（理、fl）—— 逐字找不到，
    整条卡就丢了。归一后能找到，偏移再换算回原文。

    返回 ``(归一文本, 每个归一字符的原文起点, 每个归一字符的原文终点)``。
    归一会改变长度（ﬂ → fl、㍿ → 株式会社）也支持：映射到原文的区间。
    """
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for index, char in enumerate(text):
        normalized = unicodedata.normalize("NFKC", char)
        if not normalized:
            continue
        # 一个原字可能归一成多个字符（ﬂ → fl）—— 每个归一字符都要记下标，
        # 否则映射表会错位（后面所有的偏移都偏一点）。
        for _ in normalized:
            out.append(_)
            starts.append(index)
            ends.append(index + 1)
    return "".join(out), starts, ends


def find_all_flexible(text: str, surface: str) -> list[tuple[int, int]]:
    """``surface`` 在 ``text`` 里的所有出现位置（**跨硬折行**、**兼容码位**也算）。

    先精确找（口径与 :func:`find_all` 完全一致）；一处都没有时，把 PDF 的
    硬折行按合并口径压掉再找，命中的偏移换算回**原文**坐标——跨行的词落在
    原文里那个跨 ``\\n`` 的区间上（契约 §4）。

    只接受**跨了换行**的命中：没有跨行的命中逐字找早就找到了。
    """
    exact = _find_all(text, surface)
    if exact:
        return [(start, start + len(surface)) for start in exact]
    if not surface:
        return []

    # 兼容字兜底：文本层用兼容码位（理=U+F9E4）时，模型写的是标准码位。
    hits: list[tuple[int, int]] = []

    normalized_text = nfkc_index(text)
    normalized_surface = nfkc_index(surface)
    if normalized_text and normalized_surface:
        n_text, n_start, n_end = normalized_text
        n_surface, _, _ = normalized_surface
        for start in _find_all(n_text, n_surface):
            hits.append((n_start[start], n_end[start + len(n_surface) - 1]))

    # 跨硬折行：压掉折行再找。**归一后的文本也要走这一遍** ——
    # 「跨行 + 含兼容字」的词，两条路各差一口气，两条都匹配不上。
    for base_text, base_needle, base_map in _search_variants(text, surface):
        compressed, index_map = compress_soft_wraps(base_text)
        needle, _ = compress_soft_wraps(base_needle)
        if not needle:
            continue
        cursor = compressed.find(needle)
        while cursor >= 0:
            start = index_map[cursor]
            end = index_map[cursor + len(needle) - 1] + 1
            if "\n" in base_text[start:end]:
                hits.append((base_map[start], base_map[end - 1] + 1))
            cursor = compressed.find(needle, cursor + 1)
    # 多个版本（原文 / 归一）可能命中同一处 —— 去重
    unique: list[tuple[int, int]] = []
    for hit in hits:
        if hit not in unique:
            unique.append(hit)
    return unique


def _search_variants(
    text: str, surface: str
) -> list[tuple[str, str, list[int]]]:
    """要参与查找的版本：原文 + NFKC 归一后的版本（各自带下标映射）。

    **文本和 needle 要用同一套码位** —— 归一后的文本配原始 needle 会
    在 U+2011（不换行连字符，归一后是 U+2010）这种地方对不上。
    """
    variants: list[tuple[str, str, list[int]]] = [
        (text, surface, list(range(len(text))))
    ]
    normalized = nfkc_index(text)
    normalized_surface = nfkc_index(surface)
    if normalized and normalized_surface:
        n_text, starts, _ends = normalized
        n_surface, _, _ = normalized_surface
        variants.append((n_text, n_surface, starts))
    return variants


def span_matches(text: str, start: int, end: int, surface: str) -> bool:
    """``text[start:end]`` 是不是 ``surface``（**跨硬折行**的也算）。

    逐字相等直接算命中；切片里含 ``\\n`` 时两边都按合并口径压掉折行再比。
    """
    if not (0 <= start < end <= len(text)):
        return False
    span = text[start:end]
    if span == surface:
        return True
    if "\n" not in span:
        return False
    return compress_soft_wraps(span)[0] == compress_soft_wraps(surface)[0]
