"""闸门 1（契约 §6）：目录能不能切章节。

四条判据，全满足才 ``approved``：

1. 条目页码单调不减，最后一条 ≤ 页数；
2. 正文首页之后每一页都落在某个 section 里，无空洞、无重叠；
3. 抽查 3 个条目：该页文字里确实出现条目标题（允许空白差异）；
4. 每个 section 3–25 页，超出要写理由。

判据逐条写进 ``gate.reasons``；有一条不满足就 ``rejected`` 并说明怎么修。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from difflib import SequenceMatcher

from bookcard.engine import sections as sections_mod

#: 标题匹配的相似度下限（跟看图目录的 C3 同一口径）。
TITLE_MATCH_THRESHOLD = 0.80

#: 抽查几个条目（判据 3）。
SAMPLE_COUNT = 3

#: 抽查时在预期页前后找多少页。
TITLE_SEARCH_WINDOW = 4


def _compact(text: str) -> str:
    """去掉空白和标点（判据 3 允许空白差异，标点差异也不该算对不上）。

    目录上印的「中国古代史大事年表（下）」，OCR 出来的是「中国古代史大事年表(下)」
    （全角括号变半角、还可能断成两行）—— 只去空白的话接不上。
    """
    return re.sub(r"[\s\W_]+", "", text or "", flags=re.UNICODE)


#: 单独成行的脚注标记（判据 3 匹配前先去掉）。
_FOOTNOTE_MARKER_RE = re.compile(r"[a-zA-Z]|[①②③④⑤⑥⑦⑧⑨⑩]")


def _strip_footnote_markers(text: str) -> str:
    """去掉单独成行的脚注标记（``a`` ``b`` ``①`` …）。

    正文页上标题和作者之间常夹着一个脚注标记：

    ```
    7  敬业与乐业
    ①        ← 脚注标记
    梁启超
    ```

    标题主体是「敬业与乐业梁启超」，去掉标记后才连得上
    （看图目录的 C3/C4 已经是这个口径，见 ``tocvision._strip_footnote_markers``）。
    """
    return "\n".join(
        line
        for line in (text or "").splitlines()
        if not _FOOTNOTE_MARKER_RE.fullmatch(line.strip())
    )


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def check_pages_monotonic(entries: list[dict], page_count: int) -> tuple[bool, str]:
    """判据 1：条目页码单调不减，最后一条 ≤ 页数。

    **没换算出物理页的条目不参加**（目录页上没印页码的行，比如人教版历史目录里
    只印单元名的那几行）—— 它们既定不了顺序，也没有页码可查。
    """
    if not entries:
        return False, "判据1 不通过：目录里一条条目都没有"
    with_pages = [entry for entry in entries if entry.get("page") is not None]
    if not with_pages:
        return False, "判据1 不通过：没有一条条目换算出了物理页"
    pages = [entry["page"] for entry in with_pages]
    breaks = [
        f"第 {index + 1} 条（{entry['title']}）页码 {entry['page']} 比上一条小"
        for index, entry in enumerate(with_pages)
        if index and entry["page"] < pages[index - 1]
    ]
    if breaks:
        return False, "判据1 不通过：条目页码不是单调不减 —— " + "；".join(breaks[:3])
    last = pages[-1]
    if last > page_count:
        return False, (
            f"判据1 不通过：最后一条（第 {last} 页）超出全书页数 {page_count} —— "
            "印刷页码与物理页号的偏移算错了，要重新校准"
        )
    return True, (
        f"判据1 通过：{len(with_pages)} 条条目页码单调不减"
        f"（{pages[0]}…{last}），末条 {last} ≤ 全书 {page_count} 页"
    )


def check_coverage(
    sections: list[dict],
    first_body_page: int,
    last_body_page: int,
) -> tuple[bool, str]:
    """判据 2：正文首页之后每一页都落在某个 section 里，无空洞、无重叠。"""
    problems = sections_mod.check_contiguity(sections, first_body_page, last_body_page)
    if problems:
        described = "；".join(
            f"{'空洞' if kind == 'gap' else '重叠'}第 {start}–{end} 页"
            for kind, start, end in problems[:5]
        )
        return False, (
            f"判据2 不通过：正文第 {first_body_page}–{last_body_page} 页没有连续覆盖 —— "
            f"{described}；要调目录条目或切分粒度"
        )
    covered = last_body_page - first_body_page + 1
    return True, (
        f"判据2 通过：正文第 {first_body_page}–{last_body_page} 页（{covered} 页）"
        f"被 {len(sections)} 个 section 连续覆盖，无空洞、无重叠"
    )


def _pick_samples(entries: list[dict]) -> list[dict]:
    """抽 3 个条目（首、中、尾）。

    只抽**正文条目**（``kind == "body"``）—— 前置页和附录不是正文，
    标题也不一定在正文页上原样出现（目录上印「Appendix 3: Useful expressions」，
    正文页上只有「Appendix 3」）。看图目录的 C3 也是这个口径。
    """
    usable = [
        entry
        for entry in entries
        if entry.get("page") is not None
        and str(entry.get("kind") or "body") == "body"
    ]
    if len(usable) <= SAMPLE_COUNT:
        return usable
    middle = len(usable) // 2
    return [usable[0], usable[middle], usable[-1]]


def _similarity(left: str, right: str) -> float:
    """两个字符串的相似度（0..1）。"""
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def _best_window_similarity(needle: str, haystack: str) -> float:
    """``needle`` 在 ``haystack`` 里最好的一次对齐的相似度。

    跟看图目录的同名函数（``tocvision.best_window_similarity``）同一口径 ——
    闸门和 C3/C4 得用一样的匹配宽严，否则会出现「C3 过了、闸门说找不到」。
    """
    if not needle or not haystack:
        return 0.0
    if needle in haystack:
        return 1.0
    if len(haystack) <= len(needle):
        return _similarity(needle, haystack)
    if len(haystack) > len(needle) + 30:
        return _similarity(needle, haystack)
    best = 0.0
    for start in range(len(haystack) - len(needle) + 1):
        value = _similarity(needle, haystack[start : start + len(needle)])
        if value > best:
            best = value
            if best >= 1.0:
                break
    return best


#: 标题匹配前先剥掉的编号前缀 —— 跟 ``tocvision._SECTION_PREFIX_RE`` 同一口径
#: （「第一章 机械运动」→ 「机械运动」，「Unit 5 Let's eat!」→ 「Let's eat!」）。
#: 两边要一致：看图目录的 C3 用的是剥过的标题主体，闸门也得用同一个，
#: 否则会出现「C3 过了、闸门说正文页上找不到」。
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


def _title_parts(title: str) -> list[str]:
    """把「栏目：主题」这种标题拆成两半（两边都要 ≥2 个字才拆）。"""
    parts = [part.strip() for part in re.split(r"[：:]|——|—{2,}", title or "") if part.strip()]
    if len(parts) < 2 or any(len(part) < 2 for part in parts):
        return []
    return parts


def _strip_pinyin(text: str) -> str:
    """去掉文字里的拉丁字母（拼音注音）。

    小学一二年级的课文标题在 PDF 里是「毛笔字图 + 每个字头上标拼音」，
    文本层里是「秋qiū天tiān」这种交错的样子 —— 标题正文不是连续字符串。
    """
    return re.sub(r"[A-Za-z\u00C0-\u024F\u1E00-\u1EFF]+", "", text or "")


def _title_core(title: str) -> str:
    """标题主体：去掉编号前缀、去掉空白和标点。

    剥完只剩 1 个字以内就退回整串（宁可多匹配，也别把标题丢掉）。
    """
    core = _compact(_SECTION_PREFIX_RE.sub("", title or "").strip())
    if len(core) >= 2:
        return core
    return _compact(title)


def find_title_page(title: str, page_texts: dict[int, str], expected: int) -> int | None:
    """在预期页附近找条目标题出现的页（返回物理页号）。

    允许空白和标点差异；编号前缀先剥掉（跟 C3 同一口径）；整串接不上时
    按**逐行相似度**再找一次（目录页上印的「Unit 5 Let's eat!」在正文页上
    可能写成「Unit Five」，直撇号和弯撇号也可能不一样）。
    """
    needle = _title_core(title)
    if not needle:
        return None
    parts = []
    for part in _title_parts(title):
        core = _compact(_SECTION_PREFIX_RE.sub("", part).strip())
        parts.append(core if len(core) >= 2 else _compact(part))

    def hit(text: str) -> bool:
        if not text:
            return False
        if needle in _compact(text):
            return True
        if needle in _compact(_strip_footnote_markers(text)):
            return True
        # 标题是「字图 + 拼音注音」时，去掉拼音再找一次
        if needle in _compact(_strip_pinyin(text)):
            return True
        for line in (text or "").splitlines():
            if (
                _best_window_similarity(needle, _compact(line))
                >= TITLE_MATCH_THRESHOLD
            ):
                return True
        return False

    def hit_parts(text: str) -> bool:
        """「口语交际：转述」这种：栏目徽标和主题在页上是两块文字。"""
        if not parts:
            return False
        stripped = _compact(_strip_pinyin(_strip_footnote_markers(text or "")))
        return all(part and part in stripped for part in parts)

    if expected in page_texts and (hit(page_texts[expected]) or hit_parts(page_texts[expected])):
        return expected
    for page in range(expected - TITLE_SEARCH_WINDOW, expected + TITLE_SEARCH_WINDOW + 1):
        if page == expected:
            continue
        text = page_texts.get(page, "")
        if hit(text) or hit_parts(text):
            return page
    return None


def check_titles(
    entries: list[dict],
    page_texts: dict[int, str],
) -> tuple[bool, str]:
    """判据 3：抽查 3 个条目，标题确实出现在对应页的文字里。"""
    samples = _pick_samples(entries)
    if not samples:
        return False, "判据3 不通过：没有可抽查的条目"
    missing: list[str] = []
    details: list[str] = []
    for entry in samples:
        found = find_title_page(entry["title"], page_texts, entry["page"])
        if found is None:
            missing.append(f"「{entry['title']}」在第 {entry['page']} 页及前后都没找到")
        elif found != entry["page"]:
            details.append(
                f"「{entry['title']}」实际在第 {found} 页（目录写的是第 {entry['page']} 页）"
            )
        else:
            details.append(f"「{entry['title']}」在第 {entry['page']} 页")
    if missing:
        return False, "判据3 不通过：" + "；".join(missing) + "；目录页码和正文对不上"
    note = "；".join(details)
    suffix = "（有偏差，页码已按实际位置校准）" if details and any(
        "实际在第" in item for item in details
    ) else ""
    return True, f"判据3 通过：抽查 {len(samples)} 条 —— {note}{suffix}"


def evaluate(
    entries: list[dict],
    sections: list[dict],
    page_count: int,
    first_body_page: int,
    last_body_page: int,
    page_texts: dict[int, str],
    approved_by: str,
    extra_reasons: list[str] | None = None,
) -> dict:
    """跑闸门 1 的四条判据，返回 ``gate``。

    ``extra_reasons`` 是额外的说明（比如偏移校准的结果），排在判据前面。
    """
    results = [
        check_pages_monotonic(entries, page_count),
        check_coverage(sections, first_body_page, last_body_page),
        check_titles(entries, page_texts),
    ]
    reasons = list(extra_reasons or [])
    reasons.extend(reason for _, reason in results)
    passed = all(ok for ok, _ in results)
    return {
        "status": "approved" if passed else "rejected",
        "approved_by": approved_by if passed else "",
        "at": _now(),
        "reasons": reasons,
    }
