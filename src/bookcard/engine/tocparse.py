"""目录解析：印刷目录（两种版式）、书签、版式标题。

印刷目录有两种版式，都要认：

- **经典式**（历史）：左边标题、右边页码，标题可以折行。
- **分栏式**（物理）：左边一整块标题、右边一整块页码，两块**逐行对齐**
  ——页码是单独一行的独立数字，跟同一行的标题是一对。

两种排法用同一套「按版面位置配对」的逻辑（见 :func:`parse_classic_toc`），
不用先判「这是哪种版式」。另外还有表格式目录（英语教材那种 ``Page N`` 标记）。

再就是书签（人工书签优先）和版式（大字号标题）两个兜底来源。
目录条目里的页码是**印刷页码**，换算成物理页号由调用方做（要靠偏移量）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from bookcard.probe import rules

#: 版式兜底时跳过的页面类型（封面、前置页不是目录条目）。
#:
#: 页类型在 ``pages.jsonl`` 里（``kind``）——数字版书走 PDF 文本层分析时没有
#: 这个字段，就不跳（行为不变）。
SKIP_KINDS = ("cover", "front")

#: 找印刷目录页时只看前多少页。
TOC_SEARCH_PAGES = 15

#: 版式来源用的大字号阈值（相对全书正文字号）。
LARGE_FONT_RATIO = 1.30

#: OCR 页的阈值（相对全书正文字号）。
#:
#: OCR 拿不到字号，只能用**行框高度**当字号——噪声比真实字号大得多：
#: 「讨论」「练习」这种栏目标签和正文结尾的短行都能到 1.35 倍。真正的
#: 章/单元大标题在 1.85 倍以上，所以 OCR 页把阈值放高，只认大标题。
OCR_LARGE_FONT_RATIO = 1.80

#: 条目起点（标题行开头的样子）→ 层级。
ENTRY_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"^第\s*[一二三四五六七八九十百千零〇两\d]+\s*单元"), 1),
    (re.compile(r"^第\s*[一二三四五六七八九十百千零〇两\d]+\s*章"), 1),
    (re.compile(r"^活动课"), 1),
    (re.compile(r"^附\s*录"), 1),
    (re.compile(r"^索\s*引"), 1),
    (re.compile(r"^后\s*记"), 1),
    (re.compile(r"^第\s*[一二三四五六七八九十百千零〇两\d]+\s*课"), 2),
    # 「第X节」：中文数字和阿拉伯数字都认（沪科版物理用「第一节」，
    # 人教版用「第1节」——两种写法是同一层级）。
    (re.compile(r"^第\s*[一二三四五六七八九十百千零〇两\d]+\s*节"), 2),
    (re.compile(r"^Unit\s+\d+", re.IGNORECASE), 1),
    (re.compile(r"^Chapter\s+\d+", re.IGNORECASE), 1),
)

#: 版面上的**标记**（脚注星号、难度星号…）——不是标题的一部分。
#:
#: 教材目录里标题左边常有「＊」，标着这一节另有例题。它跟标题同一行，
#: 但字号小得多（6–7 磅 vs 标题的 12 磅），是版面上的标记，
#: 不是标题的折行——当成折行会把页码配到错的条目上。
MARKER_CHARS = "*＊※†‡"

#: 整行都是标记的行（目录里的脚注星号）——直接跳过。
MARKER_LINE_RE = re.compile(rf"^[{MARKER_CHARS}\s]+$")

#: 圈号/装饰性标记（英语目录里的 ➊ ➋ …），不算标题的一部分。
MARKER_ONLY_RE = re.compile(r"^[\u2460-\u2473\u2780-\u2793\uf000-\uf0ff\s]+$")

#: 英语目录表里的页码标记。
PAGE_MARKER_RE = re.compile(r"^Page\s+(\d+)$", re.IGNORECASE)

#: 右栏页码占页面宽度的比例（超过这个位置才算「右栏页码」）。
PAGE_COLUMN_RATIO = 0.72

#: 目录里，标题块和独立页码行**纵向配对**的容差（点）。
#:
#: 两种排法配的都是「块的最后一行」：经典式里页码跟在折行标题的末尾，
#: 分栏式里标题就一行、页码跟它同一行。容差给到 8 点，够覆盖字号差
#: （章的页码字号大，行框比标题行高一截，但仍然重叠）。
COLUMN_Y_TOLERANCE = 8.0

#: 目录续页的判据下限：像条目的行数 / 页码行数。
#:
#: 目录常常跨好几页，只有第一页印着「目录」二字。续页靠「像条目的行够多
#: + 页码行够多」认出来，不要求有「目录」二字。
TOC_CONTINUATION_MIN_ENTRIES = 3
TOC_CONTINUATION_MIN_PAGE_NUMBERS = 5

#: 标题里至少要有一个「像文字」的字符（汉字或字母）。
#:
#: 版面上纯符号的大字（教材「请提问」栏那个 40 磅的 ``?``）不是标题，
#: 版式兜底不能把它当成目录条目——不然词库名就是一串问号。
TITLE_TEXT_RE = re.compile(r"[A-Za-z\u3400-\u4dbf\u4e00-\u9fff]")

#: 表格式目录里，页码标记所在栏的宽度比例（左栏）。
TABLE_LEFT_COLUMN_RATIO = 0.25

#: 表格式目录里，往上找标题时允许的最大行距。
TABLE_TITLE_GAP = 30.0

#: 表格式目录里，找右栏标题时允许的纵向偏差。
TABLE_TITLE_Y_TOLERANCE = 4.0

#: 折行标题拼接时，中文之间不插空格。
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3005\uf900-\ufaff]")


def _is_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text))


def _join_titles(parts: list[str]) -> str:
    """把折行的标题拼回去。

    中文之间直接接上（``……国家`` + ``的建立`` → ``……国家的建立``），
    其它语言用空格接上（``How can we`` + ``become good`` → ``How can we become good``）。
    """
    title = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if not title:
            title = part
        elif _is_cjk(title[-1]) and _is_cjk(part[0]):
            title += part
        else:
            title += " " + part
    return title


def _entry_start(text: str) -> tuple[int, str] | None:
    """标题行开头像不像条目；像的话返回 ``(层级, 标签后面的余下文字)``。

    「余下文字」用来分辨两种折行：

    - ``第一单元 从中华文明起源……`` —— 标签后面有字，下一行是**续行**
      （``的建立与巩固``），拼的时候不插空格；
    - ``活动课`` —— 标签后面没字，下一行才是**标题**
      （``家国情怀与统一多民族国家的演进``），拼的时候插空格。
    """
    for pattern, level in ENTRY_PATTERNS:
        match = pattern.match(text)
        if match:
            return level, text[match.end() :]
    return None


def _indent_levels(items: list[tuple[str, float]]) -> dict[str, int]:
    """按缩进给没有固定写法的标题分层（左边距越靠左层级越高）。"""
    if not items:
        return {}
    xs = sorted({round(x, 1) for _, x in items})
    clusters: list[list[float]] = [[xs[0]]]
    for value in xs[1:]:
        if value - clusters[-1][-1] <= 12.0:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    rank: dict[float, int] = {}
    for index, cluster in enumerate(clusters, start=1):
        for value in cluster:
            rank[value] = index
    return {text: rank.get(round(x, 1), 1) for text, x in items}


def _standalone_page_number(line: dict, width: float) -> int | None:
    """这一行是不是「右栏页码」。

    两种形态：整行就是一个数字；或者行尾挂着一个独立的数字 span（版式把
    标题和页码连成了一行）。
    """
    text = rules.normalize_line(line["text"])
    if rules.PAGE_NUMBER_RE.match(text):
        return int(text)

    spans = line.get("spans") or []
    if not spans:
        return None
    last = spans[-1]
    tail = rules.normalize_line(last["text"])
    if not rules.PAGE_NUMBER_RE.match(tail):
        return None
    center_x = (last["bbox"][0] + last["bbox"][2]) / 2
    if center_x < PAGE_COLUMN_RATIO * width:
        return None
    return int(tail)


def _split_trailing_page_number(line: dict, width: float) -> tuple[str, int | None]:
    """把行尾的页码从标题里摘出来。"""
    text = rules.normalize_line(line["text"])
    number = _standalone_page_number(line, width)
    if number is None:
        return text, None
    spans = line.get("spans") or []
    tail = rules.normalize_line(spans[-1]["text"]) if spans else ""
    if tail == text:
        return "", number
    if text.endswith(tail):
        return text[: -len(tail)].strip(), number
    return text, number


def _vertical_distance(first: tuple[float, ...], second: tuple[float, ...]) -> float:
    """两个行框在纵向上的距离（重叠算 0）。"""
    return max(0.0, max(first[1], second[1]) - min(first[3], second[3]))


def _strip_markers(text: str) -> str:
    """去掉标题两头的标记（脚注星号、难度星号…）。

    只动两头——标题中间的字（``功·功率``这种）不动。
    """
    head = re.sub(rf"^[{MARKER_CHARS}\s]+", "", text)
    return re.sub(rf"[{MARKER_CHARS}\s]+$", "", head).strip()


def _toc_blocks(page: dict) -> tuple[list[dict], list[dict]]:
    """把一页目录拆成「条目块」和「独立页码行」。

    条目块 = 一个条目起点行（``第X章``/``第X节``…）**加上它后面的折行**。
    独立页码行 = 整行就是一个数字的页码（分栏式里页码单独占一行）。

    块上记着 ``last_bbox``（块的**最后一行**）——页码就是配给它的。
    """
    width = page["width"]
    lines = sorted(page["lines"], key=lambda line: line["bbox"][1])
    blocks: list[dict] = []
    numbers: list[dict] = []
    current: dict | None = None

    for line in lines:
        text = rules.normalize_line(line["text"])
        if not text:
            continue
        if MARKER_LINE_RE.match(text):
            # 版面上的标记（脚注星号、难度星号）——不是标题，也不是折行
            continue
        title, trailing_page = _split_trailing_page_number(line, width)

        # 整行就是一个页码 —— 独立页码行（分栏式的页码块）
        if not title:
            if trailing_page is not None:
                numbers.append({"bbox": line["bbox"], "page": trailing_page})
            continue

        title = _strip_markers(title)
        if not title:
            continue

        start = _entry_start(title)
        if start is not None:
            level, remainder = start
            if current is not None:
                blocks.append(current)
            current = {
                "level": level,
                "title": title,
                "page": None,
                "x": line["bbox"][0],
                "has_title": bool(remainder.strip()),
                "last_bbox": line["bbox"],
            }
            if trailing_page is not None:
                # 页码跟标题在同一行（经典式：「第4课 西汉与东汉…… 20」）
                current["page"] = trailing_page
                blocks.append(current)
                current = None
            continue

        if current is None:
            # 没有块可续（目录头那几行：目录标题、前言标题…）
            continue

        # 折行：接在当前条目后面
        separator = "" if current["has_title"] else " "
        current["title"] = f"{current['title']}{separator}{title}".strip()
        current["has_title"] = True
        current["last_bbox"] = line["bbox"]
        if trailing_page is not None:
            current["page"] = trailing_page
            blocks.append(current)
            current = None

    if current is not None:
        blocks.append(current)
    return blocks, numbers


def _assign_pages(blocks: list[dict], numbers: list[dict]) -> None:
    """把独立页码行配给条目块——**按版面位置配对**。

    配的是块的**最后一行**：

    - 经典式里，标题可以折行，页码跟在标题块的末尾 ⇒ 配最后一行；
    - 分栏式里，标题就一行，页码在右栏同一行 ⇒ 配最后一行（也是唯一一行）。

    两种排法共用这一套，所以不用先判「这是哪种版式」——按 y 坐标配就对了。
    配不上的页码直接丢（页码串不是标题）。
    """
    pairs: list[tuple[float, int, int]] = []
    for number_index, number in enumerate(numbers):
        for block_index, block in enumerate(blocks):
            distance = _vertical_distance(block["last_bbox"], number["bbox"])
            if distance <= COLUMN_Y_TOLERANCE:
                pairs.append((distance, number_index, block_index))
    pairs.sort()

    used_numbers: set[int] = set()
    used_blocks: set[int] = set()
    for _distance, number_index, block_index in pairs:
        if number_index in used_numbers or block_index in used_blocks:
            continue
        used_numbers.add(number_index)
        used_blocks.add(block_index)
        blocks[block_index]["page"] = numbers[number_index]["page"]


def parse_classic_toc(toc_pages: list[dict]) -> list[dict]:
    """解析印刷目录（左标题、右页码）。

    同时认两种排法：

    - **经典式**（历史）：标题可以折行，页码跟在标题块末尾，或者和标题同一行。
    - **分栏式**（物理）：左边一整块标题、右边一整块页码，两块**逐行对齐**
      ——页码是单独一行的独立数字，跟同一行的标题是一对。

    两种排法共用「按版面位置配对」：先把标题行拼成条目块，再把独立页码行
    按纵向位置配给块（配块的**最后一行**）。所以不用先判「这是哪种版式」。

    返回 ``[{"level": 1, "title": "...", "page": 7}, ...]``，页码是印刷页码。
    """
    raw: list[dict] = []
    for page in toc_pages:
        blocks, numbers = _toc_blocks(page)
        _assign_pages(blocks, numbers)
        raw.extend(blocks)

    return _finalize_levels(raw)


def _finalize_levels(raw: list[dict]) -> list[dict]:
    """定层级（认不出写法的按缩进分），并去掉没有页码的条目。"""
    fallback = _indent_levels([(item["title"], item["x"]) for item in raw])

    entries: list[dict] = []
    for item in raw:
        if item["page"] is None:
            continue
        level = item["level"]
        if level is None:
            level = fallback.get(item["title"], 1)
        entries.append({"level": level, "title": item["title"], "page": int(item["page"])})
    return entries


def _is_table_toc_page(page: dict) -> bool:
    """这一页是不是表格式目录页（左栏里有多个 ``Page N`` 标记）。"""
    count = 0
    for line in page["lines"]:
        text = rules.normalize_line(line["text"])
        match = PAGE_MARKER_RE.match(text)
        if not match:
            continue
        if line["bbox"][0] > TABLE_LEFT_COLUMN_RATIO * page["width"]:
            continue
        count += 1
    return count >= 2


def parse_table_toc(toc_pages: list[dict]) -> list[dict]:
    """解析表格式印刷目录（英语教材那种）。

    左栏是单元标题（可折行）+ ``Page N`` 标记；没有标题的 ``Page N``
    是后附材料，标题在右栏同一行。
    """
    entries: list[dict] = []
    for page in toc_pages:
        width = page["width"]
        lines = sorted(page["lines"], key=lambda line: line["bbox"][1])
        for line in lines:
            text = rules.normalize_line(line["text"])
            match = PAGE_MARKER_RE.match(text)
            if not match:
                continue
            if line["bbox"][0] > TABLE_LEFT_COLUMN_RATIO * width:
                continue

            title = _table_unit_title(lines, line)
            if not title:
                title = _table_side_title(lines, line, width)
            if not title:
                continue
            entries.append({"level": 1, "title": title, "page": int(match.group(1))})
    return entries


def _table_unit_title(lines: list[dict], marker: dict) -> str:
    """``Page N`` 上方同一栏里、比它大一号字号的那些行（单元标题）。

    只认同一栏（左边界相差 12 以内）；别的栏的行跳过但继续往上找 ——
    表格目录是「一栏一栏排下去」的，中间隔着别的栏的行。
    """
    parts: list[str] = []
    marker_size = marker["size"]
    previous_y = marker["bbox"][1]
    for line in reversed(lines):
        if line["bbox"][1] >= previous_y:
            continue
        if previous_y - line["bbox"][1] > TABLE_TITLE_GAP:
            break
        if abs(line["bbox"][0] - marker["bbox"][0]) > 12.0:
            continue
        text = rules.normalize_line(line["text"])
        if not text or MARKER_ONLY_RE.match(text):
            previous_y = line["bbox"][1]
            continue
        if line["size"] <= marker_size:
            break
        parts.append(text)
        previous_y = line["bbox"][1]
    parts.reverse()
    return _join_titles(parts)


def _table_side_title(lines: list[dict], marker: dict, width: float) -> str:
    """后附材料：标题在右边同一行。"""
    for line in lines:
        if abs(line["bbox"][1] - marker["bbox"][1]) > TABLE_TITLE_Y_TOLERANCE:
            continue
        if line["bbox"][0] <= marker["bbox"][0] + 20.0:
            continue
        if line["bbox"][0] > 0.6 * width:
            continue
        text = rules.normalize_line(line["text"])
        if text and not MARKER_ONLY_RE.match(text):
            return text
    return ""


def parse_printed_toc(toc_pages: list[dict]) -> list[dict]:
    """解析印刷目录（先试表格式，再试左标题右页码）。

    表格式（英语教材那种 ``Page N`` 标记）最明确，先试它；
    剩下的是经典式和分栏式——:func:`parse_classic_toc` 按版面位置配对，
    两种排法都能认，不用先判版式。
    """
    table = parse_table_toc(toc_pages)
    if table:
        return table
    return parse_classic_toc(toc_pages)


def looks_like_toc_page(page: dict) -> bool:
    """这一页像不像目录的**续页**。

    判据：像条目的行（``第X章``/``第X节``/单元标题…）够多，
    而且有足够多的页码行（整行是数字，或行尾挂着独立数字）。

    续页**不要求**有「目录」二字——只有目录头那一页才有。
    """
    width = page["width"]
    entries = 0
    numbers = 0
    for line in page["lines"]:
        text = rules.normalize_line(line["text"])
        if not text:
            continue
        if _entry_start(text) is not None:
            entries += 1
        if _standalone_page_number(line, width) is not None:
            numbers += 1
    return (
        entries >= TOC_CONTINUATION_MIN_ENTRIES
        and numbers >= TOC_CONTINUATION_MIN_PAGE_NUMBERS
    )


def find_toc_pages(pages: list[dict]) -> list[int]:
    """找印刷目录页（物理页号）。

    探针的判定（「目录」/「Contents」标题 + 至少 5 行以页码结尾）只认得
    经典式；英语教材那种表格式目录另外按「``Page N`` 标记」认。

    目录常常跨好几页，只有第一页印着「目录」二字，所以命中之后**往后连页**：
    遇到第一张不像目录的页就停（判据见 :func:`looks_like_toc_page`）。
    """
    found: set[int] = set()
    probe_pages = rules.find_printed_toc_pages(pages, limit=TOC_SEARCH_PAGES)
    found.update(probe_pages)

    for page in pages:
        if page["number"] > TOC_SEARCH_PAGES:
            break
        if _is_table_toc_page(page):
            found.add(page["number"])

    # ---- 目录续页 ----
    if found:
        last = max(found)
        for page in pages:
            if page["number"] <= last:
                continue
            if page["number"] > TOC_SEARCH_PAGES:
                break
            if not looks_like_toc_page(page):
                break
            found.add(page["number"])
            last = page["number"]

    return sorted(found)


def parse_bookmarks(pdf_path: str | Path) -> list[dict]:
    """读书签（人工书签才是目录，自动页锚点不算）。

    返回 ``[{"level": 1, "title": "...", "page": 7}, ...]``，页码是物理页号。
    """
    doc = pymupdf.open(pdf_path)
    try:
        raw = doc.get_toc()
    finally:
        doc.close()

    entries: list[dict] = []
    for entry in raw:
        if len(entry) < 3:
            continue
        level, title, page = entry[0], entry[1], entry[2]
        kind = rules.classify_bookmark_title(str(title), page)
        if kind != "manual":
            continue
        if not page:
            continue
        entries.append({"level": int(level), "title": str(title).strip(), "page": int(page)})
    return entries


def parse_layout_toc(
    pages: list[dict],
    first_body_page: int,
    body_font: float,
    ratio: float = LARGE_FONT_RATIO,
) -> list[dict]:
    """版式兜底：按大字号标题切目录。

    返回 ``[{"level": 1, "title": "...", "page": 12}, ...]``，页码是物理页号。

    ``ratio`` 是「多大算标题」的阈值（相对全书正文字号）。OCR 页的行框高度
    噪声大 ⇒ 用 :data:`OCR_LARGE_FONT_RATIO`（见那里的说明）。
    """
    threshold = ratio * body_font
    entries: list[dict] = []
    for page in pages:
        if page["number"] < first_body_page:
            continue
        if str(page.get("kind") or "") in SKIP_KINDS:
            # 封面／前置页的大字不是目录条目（扫描件的封面字很大，容易被当成标题）。
            continue
        headings = [
            line
            for line in page["lines"]
            if line["size"] >= threshold and TITLE_TEXT_RE.search(line["text"])
        ]
        if not headings:
            continue
        headings.sort(key=lambda line: line["bbox"][1])
        title = _join_titles([rules.normalize_line(line["text"]) for line in headings])
        if not title or not TITLE_TEXT_RE.search(title):
            continue
        entries.append({"level": 1, "title": title, "page": page["number"]})
    return entries
