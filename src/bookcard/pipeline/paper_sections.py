"""论文源（``book.type == "paper"``）的章节结构识别（）。

书有目录页，论文没有——章节标题就写在正文里（``1 Introduction``、
``2.1 Related Work``、``Abstract``、``References``……）。本模块从
``pages.jsonl`` 的正文里认这些标题行，产出契约 §3 的两样东西：

- ``sections.json``：页码连续的章节切分（**一页只属于一节**）；
- ``toc.json``：论文的目录 + 闸门 1。

**闸门不许跳**：``gate.status`` 只有 ``pending`` / ``rejected``，
``approved`` 只能由既有的 ``toc_gate(approve=True)`` 写——本模块不写 approved。

## 怎么认标题行

真实论文（LaTeX 双栏）的取字结果有两种样子：

- 编号和标题**分开两行**（最常见）：``1`` / ``Introduction``；
- 编号和标题**同一行**：``2.1 Related Work``。

两种都认，另加固定的一级标题词（``Abstract`` / ``References`` /
``Acknowledgments`` / ``Appendix A`` / ``Bibliography``）。

**主判据是「标题自己占一段」**——用 ``pages.jsonl`` 的 ``breaks``（段落界，
契约 §2.1）看标题那一行是不是段落的最后一行：

- 有 ``breaks``：标题行必须结束一个段落（认得准，也不怕它在页面的哪儿）；
- 没有 ``breaks``（合成夹具）：退回「页首 20 行内」的粗判据。

其它约束：

1. 编号只认一次：**正文页优先**，否则认第一次（首页目录式重复的编号行不算）；
2. 认到 ``References`` / ``Bibliography`` 之后不再认标题（参考文献里的编号行不是章节）；
3. 编号的第一段最多两位（挡掉「2023. 某某」这种年份开头的行），标题须以字母
   开头（挡掉「1. 2」这种编号列表项）。

同一页上有两个标题（比如「2 Related Work」和「2.1 Attention」都在第 3 页）时，
只认**第一个**（文档顺序上靠前的那个，通常是上一级）——因为 section 的页码
不许重叠，两个标题没法各占一节。被跳过的那个记进那一节的 ``subheadings``，
层级信息不丢（这个字段只在 ``sections.json`` 里，**不进 manifest**）。
"""

from __future__ import annotations

import re

from bookcard.engine import gate
from bookcard.engine import sections as sections_mod

#: 没有段落界信息时，页首多少行内才认标题行。
HEADING_SCAN_LINES = 20

#: 标题正文最长多少字符（超了多半是正文句子，不是标题）。
MAX_HEADING_CHARS = 60

#: 编号 + 标题在同一行：``1 Introduction`` / ``2.1 Related Work``。
_NUMBERED_HEADING_RE = re.compile(r"^(?P<number>\d{1,2}(?:\.\d+)*)[.\s]+(?P<title>\S.*)$")

#: 编号独占一行（LaTeX 里编号和标题是两个 span，取字后分成两行）。
_NUMBER_ONLY_RE = re.compile(r"^(?P<number>\d{1,2}(?:\.\d+)*)$")

#: 章节号的每一段最大是多少（表格里的数字——「23.75」「88.3」——不是章节号）。
MAX_NUMBER_PART = 20

#: 标题正文至少这么长（一个字母不是标题，多半是公式里的碎片）。
MIN_HEADING_CHARS = 2

#: 表格里的数字单元格（只有数字、没有别的）。
_BARE_NUMBER_RE = re.compile(r"^[\d.]+$")

#: 固定的一级标题词（论文没有目录页，这些词就是章节起点）。
_FIXED_HEADINGS: dict[str, str] = {
    "abstract": "Abstract",
    "acknowledgments": "Acknowledgments",
    "acknowledgements": "Acknowledgements",
    "references": "References",
    "bibliography": "Bibliography",
}

#: 附录标题：``Appendix`` / ``Appendix A`` / ``Appendix B1``。
_APPENDIX_RE = re.compile(r"appendix(\s+[a-z]\d*)?$")

#: 认到这些词之后，后面的编号行都不再认（参考文献里的编号行不是章节）。
_STOP_WORDS = frozenset({"references", "bibliography"})

#: 不算正文的页面类型（首页目录式重复的编号行就落在这些页上）。
_FRONT_KINDS = frozenset({"cover", "front", "toc", "blank"})


def _is_body_page(row: dict) -> bool:
    """这一行是不是正文页（``kind`` 缺失时按正文算）。"""
    return str(row.get("kind") or "body") not in _FRONT_KINDS


def _page_paragraph_ends(text: str, breaks: list | None) -> set[int]:
    """哪些行是**段落的最后一行**（按 ``breaks`` 的偏移算）。

    ``breaks`` 是页文字里那些「段落界」换行的偏移（契约 §2.1）。
    没有 ``breaks`` 就返回空集合（调用方改用页首行数的粗判据）。
    """
    if not breaks:
        return set()
    break_offsets = set()
    for value in breaks:
        try:
            break_offsets.add(int(value))
        except (TypeError, ValueError):
            continue
    ends: set[int] = set()
    offset = 0
    lines = text.split("\n")
    for index, line in enumerate(lines[:-1]):
        offset += len(line)
        if offset in break_offsets:
            ends.add(index)
        offset += 1
    return ends


def _is_title_like(line: str) -> bool:
    """像不像一个标题。

    四个条件：不太短、**首字母大写**、不带方括号引文、括号配平。

    首字母大写这条挡的是公式碎片——LaTeX 的公式是独立的块，取字后常变成
    「2 ln k」「1 πM」这种一行，看着跟「2. Introduction」一模一样，还会把真
    章节的编号抢走（同一个编号只认一次）。真章节标题基本都首字母大写
    （``introduction`` 这种句首小写的写法少见）；中文标题没有大小写，不受这条管。
    """
    text = (line or "").strip()
    if len(text) < MIN_HEADING_CHARS or len(text) > MAX_HEADING_CHARS:
        return False
    if "[" in text or "]" in text:
        # 「Deep-Att + PosUnk [39]」这种是表格里的模型名，不是章节标题。
        return False
    if text.count("(") != text.count(")"):
        # 「ln(r」这种是公式被切碎的碎片，不是标题。
        return False
    first = text[:1]
    if first.islower():
        return False
    return first.isalpha()


def _is_bare_number(line: str) -> bool:
    """这一行是不是只有一个数字（表格里的数字单元格）。"""
    return bool(_BARE_NUMBER_RE.fullmatch((line or "").strip()))


def _looks_like_table_row(lines: list[str], number_index: int) -> bool:
    """这个「编号 + 标题」是不是表格的一行。

    表格里的行长得跟标题一模一样（一个数字 + 一段文字），但**上一行也是数字**——
    真章节标题的上一行是上一段的正文。
    """
    return number_index > 0 and _is_bare_number(lines[number_index - 1])


def _is_section_number(number: str) -> bool:
    """像不像章节号（每一段都不大）。"""
    parts = number.split(".")
    if len(parts) > 4:
        return False
    return all(part.isdigit() and int(part) <= MAX_NUMBER_PART for part in parts)


def _match_heading(
    lines: list[str],
    index: int,
    paragraph_ends: set[int],
) -> tuple[str, int, str, int] | None:
    """这一行（或这一行 + 下一行）像不像章节标题。

    返回 ``(标题, 层级, 去重键, 标题行下标)``；不像就返回 ``None``。

    去重键：编号标题用编号本身（``"2.1"``），固定词用小写词（``"abstract"``）——
    同一个键只认一次。
    """
    line = (lines[index] or "").strip()
    if not line:
        return None

    # ---- 编号独占一行，下一行是标题（LaTeX 双栏最常见） ----
    number = _NUMBER_ONLY_RE.fullmatch(line)
    if number:
        title_index = index + 1
        if title_index >= len(lines):
            return None
        title_line = (lines[title_index] or "").strip()
        if not _is_title_like(title_line):
            return None
        value = number.group("number")
        if not _is_section_number(value):
            return None
        if _looks_like_table_row(lines, index):
            return None
        if paragraph_ends and title_index not in paragraph_ends:
            return None
        return f"{value} {title_line}", value.count(".") + 1, value, title_index

    # ---- 编号和标题在同一行 ----
    match = _NUMBERED_HEADING_RE.match(line)
    if match:
        title = match.group("title").strip()
        if not _is_title_like(title):
            return None
        value = match.group("number")
        if not _is_section_number(value):
            return None
        if _looks_like_table_row(lines, index):
            return None
        if paragraph_ends and index not in paragraph_ends:
            return None
        return line, value.count(".") + 1, value, index

    # ---- 固定词标题 ----
    lowered = line.rstrip(".:").strip().lower()
    if lowered in _FIXED_HEADINGS:
        if paragraph_ends and index not in paragraph_ends:
            return None
        return _FIXED_HEADINGS[lowered], 1, lowered, index
    if _APPENDIX_RE.fullmatch(lowered):
        if paragraph_ends and index not in paragraph_ends:
            return None
        return line, 1, lowered, index
    return None


def _heading_candidates(pages: list[dict]) -> list[dict]:
    """扫出所有像标题的行（还没去重）。"""
    candidates: list[dict] = []
    stopped = False
    for row in pages or []:
        if stopped:
            break
        try:
            page = int(row.get("page") or 0)
        except (TypeError, ValueError):
            page = 0
        text = str(row.get("text") or "")
        lines = text.split("\n")
        paragraph_ends = _page_paragraph_ends(text, row.get("breaks"))
        for index in range(len(lines)):
            found = _match_heading(lines, index, paragraph_ends)
            if found is None:
                continue
            title, level, key, title_index = found
            if not paragraph_ends and title_index >= HEADING_SCAN_LINES:
                # 没有段落界信息 ⇒ 只认页首若干行（正文里的编号行不算）。
                continue
            candidates.append(
                {
                    "page": page,
                    "line": title_index,
                    "title": title,
                    "level": level,
                    "key": key,
                    "body": _is_body_page(row),
                }
            )
            if key in _STOP_WORDS:
                # 参考文献之后全是文献条目，不再认标题。
                stopped = True
                break
    return candidates


def _pick_headings(candidates: list[dict]) -> list[dict]:
    """同一个编号/词只认一次：**正文页优先**，否则认第一次。"""
    best: dict[str, dict] = {}
    for candidate in candidates:
        key = candidate["key"]
        current = best.get(key)
        if current is None:
            best[key] = candidate
            continue
        if candidate["body"] and not current["body"]:
            best[key] = candidate
    return sorted(best.values(), key=lambda item: (item["page"], item["line"]))


def _last_text_page(pages: list[dict]) -> int:
    """最后一页有字的页号（末节到这一页）。"""
    last = 0
    for row in pages or []:
        try:
            page = int(row.get("page") or 0)
        except (TypeError, ValueError):
            continue
        if not str(row.get("text") or "").strip():
            continue
        last = max(last, page)
    if last:
        return last
    numbers = []
    for row in pages or []:
        try:
            numbers.append(int(row.get("page") or 0))
        except (TypeError, ValueError):
            continue
    return max(numbers) if numbers else 0


def detect_paper_sections(pages: list[dict]) -> list[dict]:
    """从论文正文里认章节，产出 ``sections.json`` 的行。

    输入是 ``pages.jsonl`` 的行（``{page, text, kind, breaks, …}``）；纯函数、不联网。

    返回 ``[{"sid", "title", "level", "page_start", "page_end", "sort_order",
    "subheadings"}]``：

    - ``level`` 由编号层级定（``2.1`` ⇒ 2），固定词标题都是 1；
    - ``page_end`` = 下一节的 ``page_start - 1``，末节到最后一页；
    - 页码连续、无空洞无重叠（一页只属于一节）；
    - ``subheadings`` = 同一页上被跳过的子标题（层级信息不丢）。

    **一个标题都没认出来就返回空列表** —— 不许瞎编章节。
    """
    headings = _pick_headings(_heading_candidates(pages))
    if not headings:
        return []

    # 同一页上的第二个标题不另起一节（页码不许重叠）——保留第一个（文档顺序靠前的），
    # 被跳过的记进那一节的 subheadings。
    starts: list[dict] = []
    for heading in headings:
        if starts and heading["page"] == starts[-1]["page"]:
            starts[-1].setdefault("subheadings", []).append(heading["title"])
            continue
        starts.append(heading)

    last_page = _last_text_page(pages)
    sections: list[dict] = []
    for index, heading in enumerate(starts):
        start = int(heading["page"])
        end = (
            int(starts[index + 1]["page"]) - 1
            if index + 1 < len(starts)
            else last_page
        )
        if end < start:
            continue
        sections.append(
            {
                "title": heading["title"],
                "level": int(heading["level"]),
                "page_start": start,
                "page_end": end,
                "subheadings": list(heading.get("subheadings") or []),
            }
        )
    rows = sections_mod.to_sections_file(sections)
    for row, section in zip(rows, sections, strict=True):
        # to_sections_file 只搬契约里的字段；subheadings 是论文源自己加的。
        row["subheadings"] = list(section.get("subheadings") or [])
    return rows


def build_paper_toc(sections: list[dict], pages: list[dict]) -> dict:
    """把认出的章节做成 ``toc.json``（契约 §3 形状）。

    ``gate.status`` **只会是 ``pending`` 或 ``rejected``**——判据全过也只是
    「目录本身没问题，就等人拍板」；``approved`` 只能由既有的
    ``toc_gate(approve=True)`` 写（这里不写 approved）。

    判据沿用 :mod:`bookcard.engine.gate`（页码单调、正文连续覆盖、标题确实
    出现在那页）——和 ``engine.toc.build_toc`` 同一套口径：判据全过写
    ``pending``，不过写 ``rejected``，``approved`` 只由确认动作写。
    """
    rows = list(sections or [])
    page_texts: dict[int, str] = {}
    for row in pages or []:
        try:
            page = int(row.get("page") or 0)
        except (TypeError, ValueError):
            continue
        page_texts[page] = str(row.get("text") or "")
    page_count = max(page_texts) if page_texts else 0

    entries = [
        {
            "level": int(row.get("level") or 1),
            "title": str(row.get("title") or ""),
            "page": int(row.get("page_start") or 0),
        }
        for row in rows
    ]

    first_body_page = int(rows[0]["page_start"]) if rows else 1
    last_body_page = int(rows[-1]["page_end"]) if rows else first_body_page

    gate_result = gate.evaluate(
        entries,
        rows,
        page_count,
        first_body_page,
        last_body_page,
        page_texts,
        "",
    )
    checks_passed = gate_result.get("status") == "approved"
    # 没人拍板 ⇒ 只写 pending（判据不过写 rejected）；approved 只能走 toc_gate。
    gate_result["status"] = "pending" if checks_passed else "rejected"
    gate_result["approved_by"] = ""
    gate_result["at"] = ""
    gate_result["checks_passed"] = checks_passed

    problems = sections_mod.check_contiguity(rows, first_body_page, last_body_page)
    gaps = [[start, end] for kind, start, end in problems if kind == "gap"]
    entry_pages = [entry["page"] for entry in entries]
    monotonic = all(
        later >= earlier for earlier, later in zip(entry_pages, entry_pages[1:], strict=False)
    )
    covered = (
        [first_body_page, last_body_page]
        if rows
        else [first_body_page, first_body_page]
    )

    return {
        "source": "layout",
        "entries": entries,
        "checks": {
            "pages_monotonic": monotonic,
            "first_body_page": first_body_page,
            "covered_pages": covered,
            "gaps": gaps,
        },
        "gate": gate_result,
    }
