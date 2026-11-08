"""页面类型（契约 §2 的 ``kind``）。

取值：``cover`` | ``front``（版权页、前言）| ``toc`` | ``body`` | ``exercise``
| ``appendix`` | ``blank``。只有 ``body``、``exercise``、``appendix`` 进抽取。

判定顺序：先看是不是空白页，再看封面/目录，最后看正文里归到附录还是练习。
"""

from __future__ import annotations

import re

from bookcard.probe import rules

#: 附录类标题（后附材料、词表、索引）。
APPENDIX_TITLE_RE = re.compile(
    r"附录|附錄|索引|词汇表|词表|后记|後記"
    r"|Vocabulary\s+Index|Irregular\s+Verbs|Notes\s+on\s+the\s+Text"
    r"|Tapescripts|Words\s+and\s+Expressions|(^|\s)Grammar(\s|$)",
    re.IGNORECASE,
)

#: 练习类标题。
EXERCISE_TITLE_RE = re.compile(
    r"练习|习题|复习|单元测试|自我检测|综合测试|检测"
    r"|Self\s*Check|Review|Exercises|Practice|Tests?",
    re.IGNORECASE,
)

#: 页面类型取值。
KINDS = ("cover", "front", "toc", "body", "exercise", "appendix", "blank")

#: 进抽取的页面类型。
EXTRACTABLE_KINDS = ("body", "exercise", "appendix")


def _in_ranges(page: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= page <= end for start, end in ranges)


def classify_kinds(
    pages: list[dict],
    toc_pages: set[int],
    first_body_page: int,
    appendix_ranges: list[tuple[int, int]],
    exercise_ranges: list[tuple[int, int]],
) -> list[str]:
    """给每页定 ``kind``。

    参数
    ----
    pages:
        每页要有 ``number`` 和 ``class``（探针口径的页面类型）。
    toc_pages:
        印刷目录页的物理页号集合。
    first_body_page:
        正文首页（物理页号）。
    appendix_ranges / exercise_ranges:
        附录段、练习段的页码区间。
    """
    kinds: list[str] = []
    for page in pages:
        number = page["number"]
        has_text = bool(rules.non_space_chars(page.get("text", "")))
        if number == 1 and has_text:
            # 封面：文字不多（书名、出版社），探针会判成「剥完只剩水印」，
            # 但它就是封面，不该算空白页
            kinds.append("cover")
        elif page["class"] != "text":
            kinds.append("blank")
        elif number in toc_pages:
            kinds.append("toc")
        elif number < first_body_page:
            kinds.append("front")
        elif _in_ranges(number, appendix_ranges):
            kinds.append("appendix")
        elif _in_ranges(number, exercise_ranges):
            kinds.append("exercise")
        else:
            kinds.append("body")
    return kinds


def entry_ranges(
    entries: list[dict],
    first_body_page: int,
    last_body_page: int,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """按目录条目切出「附录段」和「练习段」的页码区间。

    一个条目管的区间：从它自己的页，到下一个条目（任意层级）的前一页。
    """
    appendix: list[tuple[int, int]] = []
    exercise: list[tuple[int, int]] = []
    for index, entry in enumerate(entries):
        start = entry["page"]
        if index + 1 < len(entries):
            end = entries[index + 1]["page"] - 1
        else:
            end = last_body_page
        if start > end:
            continue
        title = entry.get("title", "")
        if APPENDIX_TITLE_RE.search(title):
            appendix.append((start, end))
        elif EXERCISE_TITLE_RE.search(title):
            exercise.append((start, end))
    return appendix, exercise


def count_kinds(kinds: list[str]) -> dict[str, int]:
    """按类型统计页数（返回全部取值，缺的补 0）。"""
    counts = {kind: 0 for kind in KINDS}
    for kind in kinds:
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def extractable_pages(kinds: list[str]) -> list[int]:
    """能进抽取的页号（1 起算）。"""
    return [index + 1 for index, kind in enumerate(kinds) if kind in EXTRACTABLE_KINDS]


def is_blank(page: dict) -> bool:
    """这一页是不是没有可用正文。"""
    return page["class"] != "text" or not rules.non_space_chars(page.get("text", ""))
