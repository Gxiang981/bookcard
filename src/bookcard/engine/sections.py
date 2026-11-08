"""``bookcard sections``：切章节（契约 §3）。

粒度：取能让每个 section 落在 **3–25 页**的那一级（一般是「课/节/Unit」）。
section 之间页码连续、无空洞、无重叠；section = App 里的词库。

粗层级条目的处理：「第一单元」这种是**容器**（里面装着若干「课」），被细层级
吸收；「活动课」「附录」「索引」这种自己不含细条目，**自成一节**。
"""

from __future__ import annotations

#: 每个 section 最少这么多页。
MIN_SECTION_PAGES = 3

#: 每个 section 最多这么多页。
MAX_SECTION_PAGES = 25


def _section_span(sections: list[dict]) -> tuple[int, int]:
    """所有 section 覆盖的页码范围。"""
    if not sections:
        return 0, 0
    return sections[0]["page_start"], sections[-1]["page_end"]


def entry_paths(entries: list[dict]) -> list[str]:
    """按 level 算每条的**目录路径**：``父 | 子``（三级就是 ``一 | 二 | 三``）。

    左栏和词库都用这个当名字 —— 目录改了什么，一看名字就知道。
    """
    paths: list[str] = []
    stack: list[tuple[int, str]] = []
    for entry in entries:
        level = int(entry.get("level") or 1)
        while stack and stack[-1][0] >= level:
            stack.pop()
        title = str(entry.get("title") or "")
        paths.append(" | ".join([t for _, t in stack] + [title]))
        stack.append((level, title))
    return paths



def _section_title(paths: list[str]) -> str:
    """合并后 section 的名字：**公共前缀 | 叶1 / 叶2**。

    路径是全的（``父 | 子``），直接拼会重复成
    ``智能体的特征 | 智能体的特征 | Level 0`` —— 公共前缀提出来，
    剩下的用各条自己的叶子名列开。
    """
    if not paths:
        return ""
    if len(paths) == 1:
        return paths[0]
    parts = [p.split(" | ") for p in paths]
    # 公共前缀最多取到「最短那条的倒数第二段」——每条都要留一个叶子名
    limit = min(len(segs) for segs in parts) - 1
    prefix: list[str] = []
    for index in range(max(0, limit)):
        segs = [p[index] for p in parts]
        if len(set(segs)) == 1:
            prefix.append(segs[0])
        else:
            break
    leaves = [segs[-1] for segs in parts]   # 叶子 = 条目自己的名字
    head = " | ".join(prefix)
    tail = " / ".join(leaves)
    return f"{head} | {tail}" if head else tail


def build_sections(
    entries: list[dict],
    level: int,
    first_body_page: int,
    last_body_page: int,
) -> list[dict]:
    """按指定层级切 section。

    section 覆盖 ``[first_body_page, last_body_page]``，页码连续、无空洞、无重叠。

    **前置页（``kind == "front"``）不参与切章节** —— 目录页、前言、译者序这些
    不是正文内容，抽词时也不该抽（契约：「目录页不参与提取」）。

    **没有物理页的条目不参与切分** —— 目录页上没印页码的条目（比如人教版历史
    目录里只印单元名、不印页码的那几行）换算不出物理页，定不了边界，跳过。
    """
    _all_entries = list(entries)
    entries = [
        e
        for e in entries
        if str(e.get("kind") or "body") != "front" and e.get("page") is not None
    ]
    fine = sorted(
        (entry for entry in entries if entry["level"] == level),
        key=lambda entry: entry["page"],
    )
    coarse = sorted(
        (entry for entry in entries if entry["level"] < level),
        key=lambda entry: entry["page"],
    )

    # 细条目自己的边界
    bounds = sorted({entry["page"] for entry in fine if entry["page"] >= first_body_page})

    # 粗条目里自己不含细条目的 → 自成一节（活动课、附录、索引）
    standalone: list[dict] = []
    for index, entry in enumerate(coarse):
        end = coarse[index + 1]["page"] - 1 if index + 1 < len(coarse) else last_body_page
        span = range(entry["page"], end + 1)
        if not any(fine_entry["page"] in span for fine_entry in fine):
            standalone.append(entry)

    for entry in standalone:
        if entry["page"] >= first_body_page:
            bounds.append(entry["page"])
    bounds = sorted(set(bounds))

    # 名字用目录路径（父 | 子），不是条目自己的标题
    # 路径按**完整**的目录树算 —— 前置条目只是不切章节，不该把父级从路径里抹掉
    _paths = entry_paths(_all_entries)
    _named = [
        {**entry, "_path": path}
        for entry, path in zip(_all_entries, _paths, strict=False)
    ]
    # 同一页可能有多级条目（章和它的第一节同页）—— 优先取**本层**那条，
    # 否则章节名会变成「第 1 章：提示链 | 提示链模式概述」。
    fine_pages = {e["page"] for e in fine}
    standalone_pages = {e["page"] for e in standalone}
    titles: dict[int, dict] = {}
    for entry in _named:
        if entry["page"] in fine_pages and int(entry.get("level") or 1) == level:
            titles[entry["page"]] = entry
    for entry in _named:
        if entry["page"] in standalone_pages and entry["page"] not in titles:
            titles[entry["page"]] = entry

    sections: list[dict] = []
    for index, start in enumerate(bounds):
        # 第一节从正文首页起（目录里第一个条目可能比正文首页晚几页）
        begin = first_body_page if index == 0 else start
        end = bounds[index + 1] - 1 if index + 1 < len(bounds) else last_body_page
        if begin > end:
            continue
        entry = titles.get(start)
        sections.append(
            {
                "title": entry.get("_path") or (entry["title"] if entry else ""),
                "_paths": [entry["_path"]] if entry and entry.get("_path") else [],
                "level": entry["level"] if entry else level,
                "page_start": begin,
                "page_end": end,
                "pages": end - begin + 1,
            }
        )
    return sections


def sections_for_level(
    entries: list[dict],
    level: int,
    first_body_page: int,
    last_body_page: int,
) -> list[dict]:
    """按指定层级切章节。

    **层级由用户选**；没选就是第 1 层。不猜粒度、不并节 —— 选什么就切什么。

    目录里压根没有这一层（层级不齐）时退到**最深的一层**，免得切出 0 章。
    退到哪一层，只看**换算得出物理页**的条目 —— 目录页上没印页码的那些行
    （人教版历史目录里的单元行）不算层级。
    """
    levels = {
        int(entry.get("level") or 1)
        for entry in entries
        if str(entry.get("kind") or "body") != "front" and entry.get("page") is not None
    }
    if levels and level not in levels:
        level = max(levels)
    return build_sections(entries, level, first_body_page, last_body_page)


def check_contiguity(sections: list[dict], first_body_page: int, last_body_page: int) -> list:
    """找页码空洞和重叠。

    返回 ``[(kind, 起点, 终点), ...]``，``kind`` 是 ``"gap"`` 或 ``"overlap"``。
    """
    problems: list[tuple[str, int, int]] = []
    previous_end: int | None = None
    for section in sections:
        start = section["page_start"]
        end = section["page_end"]
        if previous_end is None:
            if start > first_body_page:
                problems.append(("gap", first_body_page, start - 1))
        elif start > previous_end + 1:
            problems.append(("gap", previous_end + 1, start - 1))
        elif start <= previous_end:
            problems.append(("overlap", start, previous_end))
        previous_end = end
    if previous_end is not None and previous_end < last_body_page:
        problems.append(("gap", previous_end + 1, last_body_page))
    return problems


def to_sections_file(sections: list[dict]) -> list[dict]:
    """转成 ``sections.json`` 的形状（补 sid 和 sort_order）。"""
    rows: list[dict] = []
    for index, section in enumerate(sections, start=1):
        rows.append(
            {
                "sid": f"s{index:02d}",
                "title": section["title"],
                "level": section["level"],
                "page_start": section["page_start"],
                "page_end": section["page_end"],
                "sort_order": index,
            }
        )
    return rows
