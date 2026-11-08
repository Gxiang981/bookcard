"""§5 挑卡去重：``candidates.jsonl`` → ``cards.jsonl``。

规则（契约 §5，确定性，程序做）：

1. 一本书内按 ``norm(front)`` 去重，**只留第一次出现**（最早页）那张，
   ``learn_value`` 取最大值；
2. 丢 ``learn_value == 1``（可有可无）；
3. 卡归属第一次出现的 section；section 内按页码、再按 ``start`` 排 ``sort_order``；
4. ``back`` = ``explanation``；没解释的词不做卡（但仍在 candidates 里进训练）。

偏移口径：``text[start:end] == surface``（Python 码点下标）。

依据句校正（）：LLM 给的依据句有时**根本不含这个词**（挑了同页
另一句）。程序检查一次：不含就回退到**页面原文里包住这个词的那一句**，
并用 ``source.evidence_origin`` 记下这句到底是哪来的（``llm`` / ``sentence``），

"""

from __future__ import annotations

import re
from collections.abc import Iterable

from bookcard.engine.softwrap import find_all_flexible, span_matches
from bookcard.pipeline.workdir import (
    PipelineError,
    WorkDir,
    content_fp,
    norm,
    text_sha1,
    write_jsonl,
)

#: 契约 §7：App 限制 front 长度。
MAX_FRONT_LEN = 500

#: learn_value 低于这个值就不值得学（契约 §4：1 = 可有可无）。
MIN_LEARN_VALUE = 2

#: 依据句的来源（写进 ``source.evidence_origin``）。
EVIDENCE_ORIGIN_SENTENCE = "sentence"

#: 句子边界标点：断句只认这些 + 空行（单个换行不算断句）。
SENTENCE_BOUNDARY_CHARS = "。！？；.!?;"

#: 空行（段分隔）：``\n`` + 可选空白 + ``\n``。
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")

#: 比对时要忽略的连字符（含全角、软连字符、en/em dash）。
_HYPHEN_CHARS = "-‐‑­－–—"

#: 比对时要还原的连字（PDF 抽字常见：``ﬁ`` / ``ﬂ``）。
_LIGATURES = {"\ufb01": "fi", "\ufb02": "fl"}


def _page_text_by_number(pages: Iterable[dict]) -> dict[int, dict]:
    """``pages.jsonl`` → ``{物理页号: 该页记录}``。"""
    table: dict[int, dict] = {}
    for row in pages:
        try:
            number = int(row.get("page"))
        except (TypeError, ValueError):
            continue
        table[number] = row
    return table


def _sections_by_sid(sections: Iterable[dict]) -> dict[str, dict]:
    """``sections.json`` → ``{sid: section}``。"""
    return {str(section.get("sid")): section for section in sections if section.get("sid")}


def _sections_of_page(sections: Iterable[dict], page: int) -> list[str]:
    """这一页落在哪几个 section 里。

    单层时只有一个；**跨层选层级时会有好几个**（章盖住节）——
    那一页的词就同时进这几个词库。
    """
    sids: list[str] = []
    for section in sections:
        start = section.get("page_start")
        end = section.get("page_end")
        if start is None or end is None:
            continue
        if int(start) <= page <= int(end):
            sids.append(str(section.get("sid")))
    return sids


def _section_of_page(sections: Iterable[dict], page: int) -> str | None:
    """按页码区间找 section（第一个命中的）。"""
    sids = _sections_of_page(sections, page)
    return sids[0] if sids else None


def _paragraphs(text: str) -> list[tuple[int, str]]:
    """页文字的段落：``[(页内起始偏移, 段落内容)]``。

    段落按换行切，每段去掉首尾空白（上游切块也是这个口径）。
    """
    paras: list[tuple[int, str]] = []
    offset = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            start = offset + (len(line) - len(line.lstrip()))
            paras.append((start, stripped))
        offset += len(line) + 1
    return paras


def _recover_chunk(
    text: str,
    declared_sha1: str | None,
) -> tuple[str, list[tuple[int, str]]] | None:
    """用 sha1 校验，从页文字里找回「chunk 原文」和它的段落游程。

    上游对超过 1500 字的页会按段落切块，``candidates.jsonl`` 里那一行的
    ``text_sha1`` 是 **chunk 文字**的 sha1，item 偏移也是 **chunk 内**偏移。
    chunk 文字 = 页文字里连续若干段落各去空白后用 ``\\n`` 拼接，所以枚举
    连续段落的游程、比对 sha1 就能精确找回它。

    找不到就返回 ``None``（调用方退回按页内偏移处理）。
    """
    if not declared_sha1:
        return None
    paras = _paragraphs(text)
    for begin in range(len(paras)):
        parts: list[str] = []
        for end in range(begin, len(paras)):
            parts.append(paras[end][1])
            chunk_text = "\n".join(parts)
            if text_sha1(chunk_text) == declared_sha1:
                return chunk_text, paras[begin : end + 1]
    return None


def _chunk_offset_map(paras: list[tuple[int, str]]) -> tuple[list[int], list[int]]:
    """chunk 内偏移 → 页内偏移 的映射表。

    返回 ``(chunk_starts, page_starts)``：第 i 段在 chunk 里的起点和在页里的起点。
    """
    chunk_starts: list[int] = []
    page_starts: list[int] = []
    position = 0
    for page_start, content in paras:
        chunk_starts.append(position)
        page_starts.append(page_start)
        position += len(content) + 1  # 段之间用 "\n" 拼
    return chunk_starts, page_starts


def _to_page_offset(offset: int, chunk_starts: list[int], page_starts: list[int]) -> int | None:
    """chunk 内偏移 → 页内偏移（找不到所在段落就返回 ``None``）。"""
    if not chunk_starts:
        return None
    for index in range(len(chunk_starts) - 1, -1, -1):
        if offset >= chunk_starts[index]:
            return page_starts[index] + (offset - chunk_starts[index])
    return None


def _iter_occurrences(
    candidates: Iterable[dict],
    page_texts: dict[int, dict],
) -> list[dict]:
    """把 ``candidates.jsonl`` 摊平成一条条「出现」。

    每个 ``candidates.jsonl`` 行是一页（或一页的一个 chunk），``items`` 里每个
    item 是一次出现。返回的每条记录带 ``page``/``chunk``/``index``，排序后
    就是「第一次出现」的顺序。

    **偏移基准**：上游对超长页按段落切块，这时行里的 ``text_sha1`` 是 chunk
    文字的 sha1、item 偏移是 chunk 内偏移。本函数用 sha1 把 chunk 找回，
    再把偏移换算成**页内**偏移（``source.start``/``end`` 因此可以直接在
    ``pages.jsonl`` 的页文字上切片）。依据句也从 chunk 文字里切。
    """
    lines = sorted(
        enumerate(candidates),
        key=lambda pair: (
            int(pair[1].get("page") or 0),
            int(pair[1].get("chunk") or 0),
            pair[0],
        ),
    )

    occurrences: list[dict] = []
    for _, line in lines:
        page = int(line.get("page") or 0)
        page_row = page_texts.get(page, {})
        text = page_row.get("text") or ""
        declared = line.get("text_sha1")
        page_matches = bool(declared) and declared == page_row.get("text_sha1")

        # ---- 这一行对应的「段文字」和 chunk→页 的偏移映射 ----
        if page_matches:
            seg_text = text
            seg_map: tuple[list[int], list[int]] | None = None
        else:
            recovered = _recover_chunk(text, declared)
            if recovered is None:
                seg_text, seg_map = text, None
            else:
                seg_text, seg_map = recovered[0], _chunk_offset_map(recovered[1])

        used_offsets: set[int] = set()
        for index, item in enumerate(line.get("items") or []):
            surface = (item.get("front") or item.get("surface") or "").strip()
            if not surface:
                continue
            start = item.get("start")
            end = item.get("end")
            verbatim = False
            if isinstance(start, int) and isinstance(end, int) and span_matches(
                seg_text, start, end, surface
            ):
                verbatim = True
                if seg_map is not None:
                    # chunk 内偏移 → 页内偏移
                    mapped_start = _to_page_offset(start, seg_map[0], seg_map[1])
                    mapped_end = _to_page_offset(end, seg_map[0], seg_map[1])
                    if mapped_start is not None and mapped_end is not None:
                        start, end = mapped_start, mapped_end
                        verbatim = span_matches(text, start, end, surface)
                    else:
                        verbatim = False
            elif text:
                # 偏移对不上：在页文字里找这个 surface 的下一个位置（跨行也算）。
                offset = -1
                for hit_start, hit_end in find_all_flexible(text, surface):
                    if hit_start not in used_offsets:
                        offset = hit_start
                        start, end = hit_start, hit_end
                        break
                if offset >= 0:
                    verbatim = True
                else:
                    start, end = (start if isinstance(start, int) else 0), (
                        end if isinstance(end, int) else 0
                    )
            else:
                start = start if isinstance(start, int) else 0
                end = end if isinstance(end, int) else 0

            if isinstance(start, int):
                used_offsets.add(start)

            occurrences.append(
                {
                    "page": page,
                    "chunk": int(line.get("chunk") or 0),
                    "index": index,
                    "seg_id": line.get("seg_id"),
                    "extractor": line.get("extractor"),
                    "sid": line.get("sid"),
                    "item": item,
                    "front": surface,
                    "start": int(start),
                    "end": int(end),
                    "verbatim": verbatim,
                    "page_text": text,
                    "seg_text": seg_text,
                    "page_matches": page_matches,
                }
            )
    return occurrences


def _evidence_text(occurrence: dict) -> str | None:
    """取出依据句原文。

    ``candidates`` 的 ``evidence`` 是**段内**偏移（契约 §4）；找不到就是 ``None``。
    兼容上游直接给字符串的情况。
    """
    evidence = occurrence["item"].get("evidence")
    if isinstance(evidence, str):
        return evidence or None
    if not isinstance(evidence, dict):
        return None
    start = evidence.get("start")
    end = evidence.get("end")
    text = occurrence.get("seg_text") or occurrence["page_text"]
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    if start < 0 or end > len(text) or start >= end:
        return None
    return text[start:end]


def _normalize_for_match(text: str) -> str:
    """比对用的归一化：去空白和连字符 → 转小写 → fi/fl 连字还原 → ``’`` 换 ``'``。"""
    kept = [
        char
        for char in (text or "")
        if not char.isspace() and char not in _HYPHEN_CHARS
    ]
    joined = "".join(kept).lower()
    for ligature, plain in _LIGATURES.items():
        joined = joined.replace(ligature, plain)
    return joined.replace("’", "'")


def _contains_word(evidence: str | None, word: str) -> bool:
    """依据句里是不是**含这个词**。

    比对前两边都归一化（去空白/连字符、小写、连字还原、撇号统一）；
    书名号《》两边可以不算——含《》的一边和不含的一边都算含。
    """
    if not evidence:
        return False
    norm_evidence = _normalize_for_match(evidence)
    norm_word = _normalize_for_match(word)
    if not norm_word:
        return False
    if norm_word in norm_evidence:
        return True
    bare_evidence = norm_evidence.replace("《", "").replace("》", "")
    bare_word = norm_word.replace("《", "").replace("》", "")
    return bool(bare_word) and bare_word in bare_evidence


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """把页面原文切成句子区间 ``[(start, end)]``（不含首尾空白）。

    句子边界只认 ``。！？；.!?;`` 和**空行**；单个换行不算边界（教材正文
    一行一行折行，跨行仍是一句）。
    """
    spans: list[tuple[int, int]] = []
    seg_start = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in SENTENCE_BOUNDARY_CHARS:
            spans.append((seg_start, index + 1))
            seg_start = index + 1
            index += 1
            continue
        if char == "\n":
            # 空行（段分隔）：往后跳过空白看是不是又一行空行。
            probe = index + 1
            while probe < length and text[probe] in " \t":
                probe += 1
            if probe < length and text[probe] == "\n":
                spans.append((seg_start, index))
                seg_start = probe + 1
                index = seg_start
                continue
        index += 1
    if seg_start < length:
        spans.append((seg_start, length))

    trimmed: list[tuple[int, int]] = []
    for begin, finish in spans:
        while begin < finish and text[begin].isspace():
            begin += 1
        while finish > begin and text[finish - 1].isspace():
            finish -= 1
        if finish > begin:
            trimmed.append((begin, finish))
    return trimmed


def _sentence_covering(text: str, start: int, end: int) -> str | None:
    """页面原文里**包住** ``[start, end)`` 的那一句（原文逐字的子串）。

    找不到就返回 ``None``（调用方保留原依据句）。
    """
    if not text:
        return None
    if start < 0 or end > len(text) or start >= end:
        return None
    spans = _sentence_spans(text)
    if not spans:
        return None

    chosen: tuple[int, int] | None = None
    for begin, finish in spans:
        if begin <= start < finish:
            chosen = (begin, finish)
            break
    if chosen is None:
        return None

    begin, finish = chosen
    if end > finish:
        # 词本身跨过了句子边界：把后面的句子也并进来。
        for other_begin, other_finish in spans:
            if other_begin < end <= other_finish:
                finish = other_finish
                break
    return text[begin:finish]


def _page_word_span(occurrence: dict) -> tuple[int, int] | None:
    """这个词在**页面原文**里的区间 ``[start, end)``。

    正常情况 ``occurrence["start"/"end"]`` 就是页内偏移；上游切块换算失败时
    偏移可能不是页内的，这时回页文字里把这个词找回来（离给定偏移最近的一处）。
    """
    text = occurrence.get("page_text") or ""
    surface = occurrence.get("front") or occurrence.get("surface") or ""
    start = int(occurrence.get("start") or 0)
    end = int(occurrence.get("end") or 0)
    if not text or not surface:
        return None
    if span_matches(text, start, end, surface):
        return start, end

    hits = find_all_flexible(text, surface)
    if not hits:
        return None
    return min(hits, key=lambda hit: abs(hit[0] - start))


def _resolve_evidence(occurrence: dict) -> tuple[str | None, str]:
    """算出这张卡的依据句。

    返回 ``(evidence, evidence_origin)``：

    依据句**不再由模型给** —— 引擎自己取页面原文里包住这个词的那一句，
    ``origin = "sentence"``。
    """
    span = _page_word_span(occurrence)
    sentence = None
    if span is not None:
        sentence = _sentence_covering(occurrence.get("page_text") or "", span[0], span[1])
    return sentence, EVIDENCE_ORIGIN_SENTENCE


def _method_of(extractor: str | None) -> str:
    """``source.method``：从 extractor 推断（``llm:minimax:…`` → ``llm``）。"""
    if not extractor:
        return "llm"
    head = str(extractor).split(":", 1)[0].strip()
    return head or "llm"


def select(workdir: WorkDir | str) -> dict:
    """跑一遍挑卡，写出 ``cards.jsonl``，返回统计摘要。

    参数
    ----
    workdir:
        书的工作目录（契约 §0）。

    需要 ``candidates.jsonl``、``sections.json``、``book.json``；``pages.jsonl``
    用来取印刷页码和页内文字（拿依据句、验原文）。
    """
    work = workdir if isinstance(workdir, WorkDir) else WorkDir(workdir)

    book = work.book()
    sections = work.sections()
    candidates = work.candidates()
    pages = work.pages()

    fp12 = str(book.get("fp12") or "")
    if not fp12:
        raise PipelineError(f"book.json 里没有 fp12：{work.book_path}")
    # 卡的标签 = **书的元信息标签**（领域 / 水平 / 书上自带的 tags）。
    # App 的卡级 chip 读的是 card_tags 表，导入端会拿这里这组 tags 去建行。
    book_tags: list[str] = []
    for value in (book.get("domain"), book.get("level")):
        text = str(value or "").strip()
        if text and text not in book_tags:
            book_tags.append(text)
    for value in book.get("tags") or []:
        text = str(value or "").strip()
        if text and text not in book_tags:
            book_tags.append(text)

    page_texts = _page_text_by_number(pages)
    sections_by_sid = _sections_by_sid(sections)
    occurrences = _iter_occurrences(candidates, page_texts)

    # ---- 1. 去重：只留第一次出现 ----
    first: dict[str, dict] = {}
    learn_value: dict[str, int] = {}
    order: list[str] = []
    for occurrence in occurrences:
        key = norm(occurrence.get("front") or occurrence["surface"])
        if not key:
            continue
        if key not in first:
            first[key] = occurrence
            order.append(key)
        value = occurrence["item"].get("learn_value")
        value = int(value) if isinstance(value, int) else 0
        learn_value[key] = max(learn_value.get(key, 0), value)

    # ---- 2. 丢 learn_value == 1 ----
    dropped_low_value = 0
    kept: list[dict] = []
    for key in order:
        if learn_value.get(key, 0) < MIN_LEARN_VALUE:
            dropped_low_value += 1
            continue
        kept.append(first[key])

    # ---- 3. 归属 section + 4. 没解释的不做卡 ----
    dropped_no_explanation = 0
    cards: list[dict] = []
    for occurrence in kept:
        item = occurrence["item"]
        explanation = (item.get("back") or item.get("explanation") or "").strip()
        if not explanation:
            dropped_no_explanation += 1
            continue

        front = occurrence.get("front") or occurrence["surface"]
        sid = _section_of_page(sections, occurrence["page"])
        if not sid:
            # 页码定位不到（section 覆盖不全 = 闸门 1 没过关）时，退回候选行里的
            # sid 兜底；两者都没有就报错。
            sid = occurrence.get("sid")
        if not sid or sid not in sections_by_sid:
            raise PipelineError(
                f"卡 {front!r} 找不到 section（page={occurrence['page']}）"
            )

        back = explanation
        page_row = page_texts.get(occurrence["page"], {})
        evidence, evidence_origin = _resolve_evidence(occurrence)
        source = {
            "page": occurrence["page"],
            "printed_page": page_row.get("printed_page"),
            "seg_id": occurrence.get("seg_id"),
            "start": occurrence["start"],
            "end": occurrence["end"],
            "evidence": evidence,
            "verbatim": occurrence["verbatim"],
            "method": _method_of(occurrence.get("extractor")),
            "bbox": None,
            "image": None,
            # 依据句是哪来的：现在是 "sentence" = 页面原文里包住这个词的那一句。
            "evidence_origin": evidence_origin,
        }

        tags = list(book_tags)
        card = {
            "card_id": f"{fp12}:{sid}:{text_sha1(front)[:8]}",
            "sid": sid,
            "front": front,
            "back": back,
            "type": item.get("type") or "term",
            "learn_value": item.get("learn_value"),
            "tags": tags,
            "sort_order": 0,  # 下面按 section 统一编
            "source": source,
            "content_fp": content_fp(front, back),
        }
        cards.append(card)

    # ---- 3b. section 内按页码、再按 start 排 sort_order（1 起）----
    by_section: dict[str, list[dict]] = {}
    for card in cards:
        by_section.setdefault(card["sid"], []).append(card)
    for group in by_section.values():
        group.sort(key=lambda card: (card["source"]["page"], card["source"]["start"]))
        for order_index, card in enumerate(group, 1):
            card["sort_order"] = order_index

    write_jsonl(work.cards_path, cards)

    return {
        "cards": len(cards),
        "sections": len(by_section),
        "duplicates_merged": len(occurrences) - len(first),
        "dropped_low_value": dropped_low_value,
        "dropped_no_explanation": dropped_no_explanation,
        "cards_path": str(work.cards_path),
    }
