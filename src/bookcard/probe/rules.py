"""bookcard 探针的判定规则。

这里全是纯函数：输入是「从 PDF 里抽出来的东西」，输出是判定结果，
不碰文件系统、不碰 pymupdf，方便单独测试（见 ``tests/test_probe.py``）。

判定口径来自两处：

- 探针单 「判定规则」小节（本文件的权威口径）。
- 书源盘点文档 ``docs/inventory/books-candidates.md`` §3.2 / §3.3（书签三分类）。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence

# --------------------------------------------------------------------- 字符口径

#: 「可读字符」= 汉字 + 拉丁字母 + 数字 + 常见中英标点。
#: 注意刻意**不**收 ``#`` ``*`` ``(`` ``)`` ``$`` ``%`` 这类符号：乱码文本层
#: （字体缺 ToUnicode）抽出来就是 ``J( cd* #íB9Â):#=ÃÄ*`` 这种，收了它们
#: 可读占比会被抬到 0.6 以上，乱码就判不出来了。
READABLE_RE = re.compile(
    "[A-Za-z0-9"
    "\u3400-\u4dbf"  # 汉字扩展 A
    "\u4e00-\u9fff"  # 汉字基本区
    "\u3005"  # 々
    "\uf900-\ufaff"  # 兼容汉字
    "\u3000-\u303f"  # 中文标点
    "\uff01-\uff65"  # 全角标点、全角字母数字
    ",\\.:;!?'\"\\-\u2013\u2014\u2026\u00b7=+\u00d7\u00f7\u00b1"  # 西文标点与常用数学符号
    "]"
)

#: 替换字符，PDF 文本层坏掉时会出现，出现即判乱码。
REPLACEMENT_CHAR = "\ufffd"

#: 一页正文少于这么多字符就当没有正文（用于区分 empty / watermark_only）。
MIN_RAW_CHARS = 10
#: 剥掉水印后少于这么多字符就当没有正文。
MIN_STRIPPED_CHARS = 30


def non_space_chars(text: str) -> list[str]:
    """返回文本里所有非空白字符。"""
    return [ch for ch in text if not ch.isspace()]


def readable_ratio(text: str) -> float:
    """可读字符占非空白字符的比例。

    空文本返回 0.0。判乱码用：低于 0.6 就不是给人看的文本。
    """
    chars = non_space_chars(text)
    if not chars:
        return 0.0
    readable = sum(1 for ch in chars if READABLE_RE.match(ch))
    return readable / len(chars)


def has_replacement_char(text: str) -> bool:
    """文本里有没有 U+FFFD 替换字符（有就一定是坏文本层）。"""
    return REPLACEMENT_CHAR in text


# --------------------------------------------------------------------- 水印

#: 水印行至少这么长才算水印（短的当栏目标签/脚注标记处理，见 find_watermark_lines）。
MIN_WATERMARK_LENGTH = 6


def normalize_line(line: str) -> str:
    """行归一化：内部连续空白压成一个空格，首尾空白去掉。"""
    return re.sub(r"\s+", " ", line).strip()


def find_watermark_lines(
    pages_lines: Sequence[Sequence[str]],
    threshold: float = 0.30,
    min_length: int = MIN_WATERMARK_LENGTH,
) -> list[str]:
    """找出水印行。

    参数
    ----
    pages_lines:
        每页的行文本列表。
    threshold:
        同一行至少出现在多少比例的页上才算水印（定 0.30）。
    min_length:
        水印行至少这么长才算（``MIN_WATERMARK_LENGTH``）。

    返回按出现页数降序排列的水印行（归一化后的文本）。

    **短的行不算水印**：正文里的栏目标签（「阅 读」「思考」「a」「b」脚注标记）
    在很多书上都会出现在三成以上的页上，按比例判就会把它们当成水印 ——
    而水印是**整行抹掉、还会在正文里按子串替换**的，抹掉「阅读」两个字，
    目录里的「新闻阅读」这类标题就再也找不到了（判据 C4 不过）。
    """
    total = len(pages_lines)
    if not total:
        return []

    counter: Counter[str] = Counter()
    for lines in pages_lines:
        seen = {normalize_line(ln) for ln in lines}
        seen.discard("")
        counter.update(seen)

    hits = [
        (count, key)
        for key, count in counter.items()
        if count / total >= threshold and len(key) >= min_length
    ]
    hits.sort(key=lambda item: (-item[0], item[1]))
    return [key for _, key in hits]


def strip_watermarks(text: str, watermarks: Iterable[str]) -> str:
    """从整页文本里剥掉水印行。

    两种水印都处理：独占一行的（整行丢掉），以及混在正文里的（按行内替换）。
    """
    watermark_list = [w for w in watermarks if w]
    if not watermark_list:
        return text

    watermark_set = {normalize_line(w) for w in watermark_list}
    kept = [
        line
        for line in text.splitlines()
        if normalize_line(line) not in watermark_set
    ]
    out = "\n".join(kept)

    for watermark in watermark_list:
        pattern = re.compile(r"\s*".join(re.escape(part) for part in watermark.split(" ")))
        out = pattern.sub("", out)
    return out


# --------------------------------------------------------------------- 页类型

#: 页面分类，取值固定。
PAGE_CLASSES = ("text", "watermark_only", "garbled", "empty")


def classify_page(raw_text: str, stripped_text: str, *, had_watermark: bool = False) -> str:
    """判定单页类型。

    - ``empty``：本来就没有正文（原始文本几乎为空）。
    - ``watermark_only``：原始有字，但剥掉水印后没剩下什么。
    - ``garbled``：剩下的字里可读字符占比过低，或者出现 U+FFFD。
    - ``text``：其余情况，是真正可用的文本层。

    两个细节：

    1. 乱码判定排在「剥后字符数少」之前：字体坏掉的文本层哪怕只剩十几个字符，
       它也是乱码而不是水印（见 乱码用例）。
    2. ``had_watermark`` 为假时，剥后字符数少只说明这一页内容短，
       不代表这一页是水印（例如只有一行标题的扉页）。所以只有真剥掉了水印
       的页面才归到 ``watermark_only``。
    """
    raw_chars = len(non_space_chars(raw_text))
    stripped_chars = len(non_space_chars(stripped_text))

    if raw_chars == 0:
        return "empty"
    if stripped_chars == 0:
        # 原来有字、剥完没字 —— 整页就是水印。
        return "watermark_only"
    if raw_chars < MIN_RAW_CHARS:
        return "empty"
    if has_replacement_char(stripped_text) or readable_ratio(stripped_text) < 0.6:
        return "garbled"
    if stripped_chars < MIN_STRIPPED_CHARS and had_watermark:
        return "watermark_only"
    return "text"


def verdict_from_counts(counts: dict[str, int], total: int) -> str:
    """按各页类型的计数给出全书判定。

    - ``text`` 占比 ≥ 0.8 → ``digital``
    - ``text`` 占比 ≤ 0.1 → 乱码页占多数算 ``garbled``，否则 ``scanned``
    - 介于两者之间 → ``mixed``
    """
    if total <= 0:
        return "broken"
    text_ratio = counts.get("text", 0) / total
    if text_ratio >= 0.8:
        return "digital"
    if text_ratio <= 0.1:
        if counts.get("garbled", 0) * 2 > total:
            return "garbled"
        return "scanned"
    return "mixed"


# --------------------------------------------------------------------- 书签

#: 制作残留：标题是 InDesign 之类的源文件名。
RESIDUE_RE = re.compile(
    r"\.(pdf|indd|ai|psd|cdr|qxd|docx?|xlsx?|pptx?|jpg|jpeg|png|tif|zip)$",
    re.IGNORECASE,
)

#: 自动页锚点：形如 ``..._pdf_p0001``、``数学七上内文_页面_001``、纯数字、``Page 3``。
#: ``_DJD`` 是「读秀/超星」一类的导出残留（盘点文档 §3.3 里的
#: ``33949-0-..._DJD`` 就是这种），也算自动页锚点，不是目录。
AUTO_ANCHOR_RES = (
    re.compile(r"_pdf_p\d+$", re.IGNORECASE),
    re.compile(r"_p\d+$", re.IGNORECASE),
    re.compile(r"_\s*页面\s*_?\s*\d+$"),
    re.compile(r"-\d+_DJD$", re.IGNORECASE),
    re.compile(r"_DJD$", re.IGNORECASE),
    re.compile(r"^\d+$"),
    re.compile(r"^Page\s+\d+$", re.IGNORECASE),
    re.compile(r"^第\s*\d+\s*[页頁]$"),
)


def classify_bookmark_title(title: str, page: int | None = None) -> str:
    """书签标题归类：``manual`` / ``auto_anchor`` / ``residue``。

    ``page`` 是这条书签的目标页。纯数字标题要靠它区分：

    - ``"1"`` 指向第 1 页 → 自动页锚点（逐页跳转）；
    - ``"1"`` 指向第 8 页 → 章节点（中文教材里「第 1 章」的目录条目就是这种），
      盘点文档里 Z7 的「文前、1、2、3…」就属于这一类。
    """
    text = title.strip()
    if not text:
        return "residue"
    if RESIDUE_RE.search(text):
        return "residue"
    for pattern in AUTO_ANCHOR_RES:
        if pattern.search(text):
            if pattern.pattern == r"^\d+$" and page is not None and int(text) != page:
                # 纯数字但目标页不是它自己 —— 是章节号，不是页锚点
                continue
            return "auto_anchor"
    return "manual"


def _titles_differ_only_by_numbers(titles: Sequence[str]) -> bool:
    """标题是否彼此只差数字（``001``/``002`` 这类逐页锚点）。"""
    if len(titles) < 2:
        return False
    stripped = {re.sub(r"\d+", "", title) for title in titles}
    return len(stripped) == 1


def classify_bookmarks(
    toc: Sequence[Sequence],
    page_count: int,
) -> dict:
    """把 ``doc.get_toc()`` 分成三类计数。

    口径见盘点文档 §3.3。返回::

        {"total": 9, "manual": 9, "auto_anchor": 0, "residue": 0, "sample": [...]}
    """
    titles = [str(entry[1]) if len(entry) > 1 else "" for entry in toc]
    pages = [entry[2] if len(entry) > 2 else None for entry in toc]
    counts = {"manual": 0, "auto_anchor": 0, "residue": 0}
    for title, page in zip(titles, pages, strict=True):
        counts[classify_bookmark_title(title, page)] += 1

    total = len(titles)
    # 整体判定：几乎全是自动锚点，且数量接近页数 —— 逐页跳转而不是目录。
    if total and counts["auto_anchor"] / total >= 0.8 and page_count:
        if total >= 0.8 * page_count and _titles_differ_only_by_numbers(titles):
            counts["auto_anchor"] = total
            counts["manual"] = 0
            counts["residue"] = 0

    return {
        "total": total,
        "manual": counts["manual"],
        "auto_anchor": counts["auto_anchor"],
        "residue": counts["residue"],
        "sample": titles[:5],
    }


# --------------------------------------------------------------------- 印刷目录

#: 目录页的标题词。
TOC_HEAD_RE = re.compile(r"目\s*录|Contents", re.IGNORECASE)
#: 「以页码结尾」的行，例如 ``第一章  数列  12`` 或 ``Chapter 1 .... 5``。
TOC_LINE_RE = re.compile(r"\d{1,4}\s*$")


def find_printed_toc_pages(
    pages: Sequence[dict],
    limit: int = 15,
    min_page_lines: int = 5,
) -> list[int]:
    """在前 ``limit`` 页里找印刷目录页，返回物理页号（1 起算）。

    判据：页内出现「目录」或「Contents」，且至少 ``min_page_lines`` 行以页码结尾。
    """
    found: list[int] = []
    for page in pages:
        if page["number"] > limit:
            break
        text = page["text"]
        if not TOC_HEAD_RE.search(text):
            continue
        page_number_lines = sum(1 for ln in text.splitlines() if TOC_LINE_RE.search(ln.strip()))
        if page_number_lines >= min_page_lines:
            found.append(page["number"])
    return found


# --------------------------------------------------------------------- 页码

#: 上下边缘各取这么多比例的页高作为页码候选区。
PAGE_NUMBER_BAND = 0.08
#: 至少要有这么多页给出同一个偏移才算找到了印刷页码。
#: 这里用的是 口径（「至少 3 页给出相同的 printed - page」），
#: 而不是 「全局众数占比 ≥ 0.6」—— 教材常有前正文、后附答两套
#: 编号，全局众数会掉到 0.5 以下，判成「没找到」反而丢信息。
PAGE_NUMBER_MIN_PAGES = 3


#: 页码候选：1 到 4 位**半角**数字。注意不能用 ``str.isdigit()``，
#: 它把 ``①`` 这种圈号也算数字，转 int 会直接炸。
PAGE_NUMBER_RE = re.compile(r"^[0-9]{1,4}$")


def _position_of(bbox: tuple[float, float, float, float], page_height: float,
                  page_width: float) -> tuple[str, str]:
    """返回 (上下位置, 左右位置)。

    上下位置：``header`` / ``footer``；左右位置：``center`` / ``left`` / ``right``。
    """
    x0, y0, x1, y1 = bbox
    if y0 >= (1.0 - PAGE_NUMBER_BAND) * page_height:
        vertical = "footer"
    elif y1 <= PAGE_NUMBER_BAND * page_height:
        vertical = "header"
    else:
        vertical = "middle"

    center_x = (x0 + x1) / 2
    if page_width <= 0:
        horizontal = "center"
    elif abs(center_x - page_width / 2) <= page_width * 0.15:
        horizontal = "center"
    else:
        horizontal = "left" if center_x < page_width / 2 else "right"
    return vertical, horizontal


def _position_name(vertical: str, horizontal: str) -> str | None:
    """把 (上下, 左右) 折成报告里的 position 取值。"""
    if vertical == "middle":
        return None
    if horizontal == "center":
        return f"{vertical}_center"
    return f"{vertical}_outer"


#: 一个位置槽上的编号段：(偏移, 页号列表)。
_SlotRun = tuple[int, list[int]]
#: 选中的位置槽：(打分, 偏移, 连续段的页号, 槽, 该槽所有段)。
_BestSlot = tuple[tuple[int, float, int], int, list[int], tuple[str, str], list[_SlotRun]]


def _slot_runs(per_page: dict[int, set[int]]) -> list[_SlotRun]:
    """把某个位置槽上的候选偏移切成连续段。

    「连续」是按这个槽自己的页序算的，不要求物理页号相邻 —— 奇偶页分左右
    排版时，同一个槽里的页号是 10、12、14 这样跳着的。

    返回 ``[(offset, [页号...]), ...]``。
    """
    runs: list[tuple[int, list[int]]] = []
    current_offset: int | None = None
    current_pages: list[int] = []

    for page_no in sorted(per_page):
        offsets = per_page[page_no]
        if current_offset is not None and current_offset in offsets:
            current_pages.append(page_no)
            continue
        if current_offset is not None:
            runs.append((current_offset, current_pages))
        current_offset = min(offsets) if offsets else None
        current_pages = [page_no]

    if current_offset is not None:
        runs.append((current_offset, current_pages))

    # 相邻两段偏移一样就并起来（中间可能只是某页没有候选）
    merged: list[tuple[int, list[int]]] = []
    for offset, pages in runs:
        if merged and merged[-1][0] == offset:
            merged[-1][1].extend(pages)
        else:
            merged.append((offset, list(pages)))
    return merged


def detect_page_numbers(pages: Sequence[dict]) -> dict:
    """找印刷页码。

    输入的每页需要有 ``number``（物理页号，1 起算）、``height``、``width``、
    ``class``（页面分类）、``spans``（含 ``text`` / ``bbox`` / ``flags``）。
    只在 ``text`` 页里找。

    候选按「位置槽」（页眉/页脚 × 左/中/右）分开算：一本书常常在页眉印一个
    单元号、在页脚印真正的页码，混在一起算众数会互相稀释。页眉那种「每页都
    一样」的号，偏移随物理页号变化，自然不会形成稳定的连续段。

    判定用「连续段」而不是全局众数占比：教材常有前正文、后附答两套编号
    （全局众数会掉到 0.5 以下，判成「没找到」，其实两套都存在）。
    一段至少 3 页给出同一个偏移就取它（的口径），
    ``segments`` 里把同一槽里的其他编号段也一并列出来。

    返回::

        {"status": ..., "position": ..., "offset": ..., "confidence": ...,
         "evidence": [...], "side": ..., "segments": [...]}
    """
    # 候选元组：(物理页, 印刷页码文本, 偏移, 上下位置, 左右位置)
    candidates: list[tuple[int, str, int, str, str]] = []
    for page in pages:
        if page["class"] != "text":
            continue
        height = page["height"]
        for span in page["spans"]:
            text = span["text"].strip()
            if not PAGE_NUMBER_RE.match(text):
                continue
            vertical, horizontal = _position_of(span["bbox"], height, page["width"])
            if vertical == "middle":
                continue
            printed = int(text)
            offset = printed - page["number"]
            candidates.append((page["number"], text, offset, vertical, horizontal))

    result = {
        "status": "not_found",
        "position": None,
        "offset": None,
        "confidence": 0.0,
        "evidence": [],
        "side": None,
        "segments": [],
    }
    if not candidates:
        return result

    slots: dict[tuple[str, str], list[tuple[int, str, int, str, str]]] = {}
    for item in candidates:
        slots.setdefault((item[3], item[4]), []).append(item)

    # (打分, 偏移, 连续段的页号, 位置槽, 该槽的所有段)
    best: _BestSlot | None = None
    for slot, items in slots.items():
        per_page: dict[int, set[int]] = {}
        for page_no, _, offset, _, _ in items:
            per_page.setdefault(page_no, set()).add(offset)
        runs = _slot_runs(per_page)
        if not runs:
            continue
        offset, run_pages = max(runs, key=lambda item: len(item[1]))
        if len(run_pages) < PAGE_NUMBER_MIN_PAGES:
            continue
        coverage = len(run_pages) / len(per_page)
        # 打分：先看连续段长度，再看覆盖率，最后页脚优先
        score = (len(run_pages), coverage, 1 if slot[0] == "footer" else 0)
        if best is None or score > best[0]:
            best = (score, offset, run_pages, slot, runs)

    if best is None:
        result["evidence"] = [[page_no, text] for page_no, text, _, _, _ in candidates[:12]]
        return result

    _, best_offset, run_pages, slot, runs = best
    vertical, horizontal = slot
    per_page_count = len({item[0] for item in slots[slot]})
    coverage = len(run_pages) / per_page_count if per_page_count else 0.0

    matched = [
        item
        for item in slots[slot]
        if item[2] == best_offset and item[0] in set(run_pages)
    ]

    result.update(
        {
            "status": "found",
            "position": _position_name(vertical, horizontal),
            "offset": best_offset,
            "confidence": round(coverage, 3),
            "evidence": [[page_no, text] for page_no, text, _, _, _ in matched[:12]],
            "side": horizontal if horizontal in ("left", "right") else None,
            "segments": [
                {
                    "offset": offset,
                    "from_page": pages[0],
                    "to_page": pages[-1],
                    "pages": len(pages),
                }
                for offset, pages in runs
                if len(pages) >= PAGE_NUMBER_MIN_PAGES
            ],
        }
    )
    return result


# --------------------------------------------------------------------- 编号注释

CIRCLED_RE = re.compile(r"[\u2460-\u2473]")
BRACKET_RE = re.compile(r"\[\d{1,3}\]")


def count_markers(text: str, superscript_spans: int) -> dict[str, int]:
    """统计三类编号标记的个数。"""
    return {
        "circled": len(CIRCLED_RE.findall(text)),
        "bracket": len(BRACKET_RE.findall(text)),
        "superscript": superscript_spans,
    }


def has_numbered_note(page: dict) -> bool:
    """同一页里：有编号标记，且页面底部 20% 高度内有以该标记开头的行。

    ``page["bottom_lines"]`` 由调用方给好：只含落在页面底部 20% 的行。
    """
    markers = set(CIRCLED_RE.findall(page["text"])) | set(BRACKET_RE.findall(page["text"]))
    if not markers:
        return False

    for line in page.get("bottom_lines", []):
        text = line["text"].strip()
        for marker in markers:
            if text.startswith(marker):
                return True
    return False


# --------------------------------------------------------------------- 语言

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3005\uf900-\ufaff]")
LATIN_RE = re.compile(r"[A-Za-z]")


def detect_lang(text: str) -> str:
    """粗判语言：``zh`` / ``en`` / ``mixed`` / ``unknown``。"""
    cjk = len(CJK_RE.findall(text))
    latin = len(LATIN_RE.findall(text))
    if cjk + latin == 0:
        return "unknown"
    ratio = cjk / (cjk + latin)
    if ratio >= 0.8:
        return "zh"
    if ratio <= 0.2:
        return "en"
    return "mixed"
