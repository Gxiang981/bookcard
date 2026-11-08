"""§7.1 两档卡包：``scene_mode`` 判定 + 文本场景的 ``source.context`` 构造。

契约 §7.1（the maintainer 2026-11-06 拍板）：

- ``scene_mode`` **整本书一个值**（``text`` / ``image``），写进 manifest 顶层和
  ``book.json``；
- **默认走图片场景**（the maintainer 2026-11-06 补充拍板：处理书一律默认用书页图）——
  :data:`DEFAULT_SCENE` 就是 ``image``，不用问用户；
- 下面的三条判据**照算照存**，但只作**参考**：三条全中时 ``text_eligible`` 为
  ``True``（用户主动要文本块时可以选 ``text``），其余为 ``False``：

  1. 探针 verdict 不是 ``scanned`` / ``garbled`` / ``broken``；
  2. body 页里 ``text_origin == "pdf_text"`` 的占比 ≥ 0.9；
  3. body 页图片覆盖率（``page.get_image_info()`` 的图片框面积 ∪ / 页面积）的
     中位数 < 0.35。

- 文本场景：每张卡加 ``source.context = {"text": …, "highlights": [{start, end}]}``。
  ``text`` 是页面原文里含依据句的段落（≤ 400 字，句界处截，截掉的一侧加 ``…``），
  ``highlights`` 是卡 ``front`` 在这段文字里的**每一处**偏移。

段落以**契约 §2.1 的 ``breaks``** 为界（没有 ``breaks`` 的老数据退回空行口径）；
段落里的软折行按 :mod:`bookcard.engine.reflow` 的合并口径合并掉——
``context.text`` 就是原文对应区间经 ``reflow`` 后的结果。

口径与 §5 一致的地方（归一化、断句）**直接复用** :mod:`bookcard.pipeline.select`
里的那一份，不复制第二份。
"""

from __future__ import annotations

import statistics
from pathlib import Path

import pymupdf

from bookcard.engine.reflow import map_offset, map_span, reflow, reflow_span
from bookcard.pipeline.locate import resolve_pdf
from bookcard.pipeline.select import (
    _HYPHEN_CHARS,
    _LIGATURES,
    _sentence_spans,
)
from bookcard.pipeline.workdir import PipelineError, WorkDir
from bookcard.probe import probe

#: 场景：文本块 / 书页图（契约 §7.1）。
SCENE_TEXT = "text"
SCENE_IMAGE = "image"

#: **默认场景**（契约 §7.1，the maintainer 2026-11-06 拍板）：处理书一律默认用书页图。
#: 不用问用户；三条判据只作 ``text_eligible`` 参考。
DEFAULT_SCENE = SCENE_IMAGE

#: 探针判定为这些时，这本书不能直接取字 ⇒ 一律图片场景。
BLOCKED_VERDICTS = ("scanned", "garbled", "broken")

#: body 页里 ``text_origin == "pdf_text"`` 的占比下限。
MIN_TEXT_ORIGIN_RATIO = 0.9

#: body 页图片覆盖率的中位数上限（超过就是"图多的教科书"）。
MAX_MEDIAN_IMAGE_COVERAGE = 0.35

#: ``source.context.text`` 的字数上限（契约 §7.1）。
MAX_CONTEXT_LEN = 400

#: 截断标记（契约 §7.1：截掉的一侧加 ``…``）。
ELLIPSIS = "…"


# ===================== scene_mode 判定 =====================


def _body_pages(work: WorkDir) -> list[dict]:
    """body 页（没有 body 页就拿全部页——宁可按全书口径判）。"""
    try:
        pages = work.pages()
    except PipelineError:
        return []
    body = [row for row in pages if str(row.get("kind") or "") == "body"]
    return body or pages


def _text_origin_ratio(body: list[dict]) -> float:
    """body 页里 ``text_origin == "pdf_text"`` 的占比。"""
    if not body:
        return 0.0
    hits = sum(1 for row in body if str(row.get("text_origin") or "") == "pdf_text")
    return hits / len(body)


def _union_area(rects: list[tuple[float, float, float, float]]) -> float:
    """一组矩形（PDF 点坐标）的**并集**面积。

    扫线法：按 x 切成竖条，每条里把 y 区间并起来。
    """
    if not rects:
        return 0.0
    xs = sorted({value for rect in rects for value in (rect[0], rect[2])})
    total = 0.0
    for x0, x1 in zip(xs, xs[1:], strict=False):
        if x1 <= x0:
            continue
        middle = (x0 + x1) / 2
        intervals = [
            (rect[1], rect[3]) for rect in rects if rect[0] <= middle <= rect[2]
        ]
        if not intervals:
            continue
        intervals.sort()
        length = 0.0
        current_start, current_end = intervals[0]
        for start, end in intervals[1:]:
            if start > current_end:
                length += current_end - current_start
                current_start, current_end = start, end
            else:
                current_end = max(current_end, end)
        length += current_end - current_start
        total += (x1 - x0) * length
    return total


def _page_image_coverage(page: pymupdf.Page) -> float:
    """一页的图片覆盖率：图片框面积 ∪ / 页面积。"""
    rect = page.rect
    page_area = float(rect.width * rect.height)
    if page_area <= 0:
        return 0.0
    boxes: list[tuple[float, float, float, float]] = []
    for info in page.get_image_info():
        bbox = info.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        x0 = max(float(bbox[0]), float(rect.x0))
        y0 = max(float(bbox[1]), float(rect.y0))
        x1 = min(float(bbox[2]), float(rect.x1))
        y1 = min(float(bbox[3]), float(rect.y1))
        if x1 <= x0 or y1 <= y0:
            continue
        boxes.append((x0, y0, x1, y1))
    if not boxes:
        return 0.0
    return min(1.0, _union_area(boxes) / page_area)


def _median_image_coverage(pdf_path: Path, body: list[dict]) -> float | None:
    """body 页图片覆盖率的中位数（核不到就是 ``None``）。"""
    if not body:
        return None
    numbers: list[float] = []
    doc = pymupdf.open(pdf_path)
    try:
        for row in body:
            number = row.get("page")
            if not isinstance(number, int):
                continue
            index = number - 1
            if index < 0 or index >= doc.page_count:
                continue
            numbers.append(_page_image_coverage(doc[index]))
    finally:
        doc.close()
    if not numbers:
        return None
    return float(statistics.median(numbers))


def _find_pdf(work: WorkDir, pdf: str | Path | None) -> Path | None:
    """找书 PDF（找不到就是 ``None``，不报错——判定退回保守值）。"""
    try:
        return resolve_pdf(work, pdf)
    except PipelineError:
        return None


def detect_scene_mode(work: WorkDir | str, pdf: str | Path | None = None) -> dict:
    """判定这本书的 ``scene_mode``（契约 §7.1）。

    **默认走图片场景**（the maintainer 2026-11-06 拍板）：处理书一律默认用书页图，
    不问用户。三条判据**照算照存**，但只作参考——三条全中时
    ``text_eligible`` 为 ``True``（用户主动要文本块时可以选 ``text``）。

    返回::

        {"mode": "image", "text_eligible": bool, "verdict": str,
         "text_origin_ratio": float, "median_image_coverage": float|None,
         "reasons": [str, ...], "pdf": str|None}

    没有 PDF 时核不了探针判定和图片覆盖率 ⇒ ``text_eligible`` 为 ``False``。
    """
    work = work if isinstance(work, WorkDir) else WorkDir(work)
    body = _body_pages(work)
    ratio = _text_origin_ratio(body)

    # 第一条永远是「为什么默认走图片场景」，判据的结论跟在后面作说明。
    reasons: list[str] = ["默认走图片场景（the maintainer 2026-11-06）"]

    pdf_path = _find_pdf(work, pdf)
    verdict = ""
    coverage: float | None = None
    failed: list[str] = []
    if pdf_path is None:
        failed.append("找不到书 PDF：核不了探针判定和图片覆盖率")
    else:
        verdict = str(probe(pdf_path).get("verdict") or "")
        coverage = _median_image_coverage(pdf_path, body)
        if verdict in BLOCKED_VERDICTS:
            failed.append(f"探针判定 {verdict}：书不能直接取字")
        if ratio < MIN_TEXT_ORIGIN_RATIO:
            failed.append(
                f"body 页能直接取字的只有 {ratio:.0%}"
                f"（< {MIN_TEXT_ORIGIN_RATIO:.0%}）"
            )
        if coverage is None:
            failed.append("核不到 body 页图片覆盖率")
        elif coverage >= MAX_MEDIAN_IMAGE_COVERAGE:
            failed.append(
                f"body 页图片覆盖率中位数 {coverage:.0%}"
                f"（≥ {MAX_MEDIAN_IMAGE_COVERAGE:.0%}）：图太多"
            )

    text_eligible = not failed
    if text_eligible:
        reasons.append(
            "三条判据全中，可选文本场景："
            f"探针判定 {verdict}，body 页能直接取字 {ratio:.0%}，"
            f"图片覆盖率中位数 {(coverage or 0.0):.0%}"
        )
    else:
        reasons.append("三条判据没全中，只能走图片场景：" + "；".join(failed))

    return {
        "mode": DEFAULT_SCENE,
        "text_eligible": text_eligible,
        "verdict": verdict,
        "text_origin_ratio": round(ratio, 4),
        "median_image_coverage": round(coverage, 4) if coverage is not None else None,
        "reasons": reasons,
        "pdf": str(pdf_path) if pdf_path else None,
    }


# ===================== 文本场景的 context =====================


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """归一化（口径同 §5 的依据句校正），**同时**给出每个归一化字符的原文下标。

    §5 的归一化会删空白和连字符，所以下标会变；这里把映射一起带出来，
    才能把命中位置换算回原文偏移。
    """
    chars: list[str] = []
    index_map: list[int] = []
    for index, char in enumerate(text or ""):
        if char.isspace() or char in _HYPHEN_CHARS:
            continue
        if char in _LIGATURES:
            for plain in _LIGATURES[char]:
                chars.append(plain)
                index_map.append(index)
            continue
        lowered = char.lower()
        if lowered == char:
            chars.append(char)
            index_map.append(index)
            continue
        for piece in lowered:  # 极少数字符 lower() 后长度变了（如 İ）
            chars.append(piece)
            index_map.append(index)
    joined = "".join(chars).replace("’", "'")
    return joined, index_map


def _match_occurrences(text: str, needle: str) -> list[dict]:
    """``needle`` 在 ``text`` 里的每一处出现（归一化后比对，偏移换算回原文）。"""
    norm_text, index_map = _normalize_with_map(text)
    norm_needle, _ = _normalize_with_map(needle)
    if not norm_needle or not norm_text:
        return []
    hits: list[dict] = []
    cursor = norm_text.find(norm_needle)
    while cursor >= 0:
        start = index_map[cursor]
        end = index_map[cursor + len(norm_needle) - 1] + 1
        hits.append({"start": start, "end": end})
        cursor = norm_text.find(norm_needle, cursor + 1)
    return hits


def _find_highlights(text: str, front: str) -> list[dict]:
    """卡词在 ``text`` 里的每一处偏移。

    归一化规则同 §5 依据句校正；书名号《》两边可以不算（§5 同一条规则）。
    """
    for candidate in (front, (front or "").replace("《", "").replace("》", "")):
        if not candidate:
            continue
        hits = _match_occurrences(text, candidate)
        if hits:
            return hits
    return []


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    """按**空行**切出来的段落区间（契约 §7.1：段落以空行为界）。"""
    spans: list[tuple[int, int]] = []
    index = 0
    length = len(text)
    while index < length:
        end = text.find("\n", index)
        if end == -1:
            end = length
        # 一行是空的 ⇒ 段分隔（跳过连续空行）
        if not text[index:end].strip():
            index = end + 1
            continue
        # 找这一段的结束（下一个空行）
        probe_index = end + 1
        stop = length
        while probe_index < length:
            line_end = text.find("\n", probe_index)
            if line_end == -1:
                line_end = length
            if not text[probe_index:line_end].strip():
                stop = probe_index
                break
            probe_index = line_end + 1
        spans.append((index, stop))
        index = stop
    return spans


def _sentence_index(sentences: list[tuple[int, int]], offset: int) -> int:
    """包含 ``offset`` 的那一句的下标（找不到就落在最接近的一句）。"""
    for index, (begin, finish) in enumerate(sentences):
        if begin <= offset < finish:
            return index
    return 0


def _sentence_span(
    sentences: list[tuple[int, int]], start: int, end: int
) -> tuple[int, int] | None:
    """包住 ``[start, end)`` 的句子区间（跨句就并起来）。"""
    if not sentences:
        return None
    index = _sentence_index(sentences, start)
    begin, finish = sentences[index]
    if end > finish:
        for other_begin, other_finish in sentences:
            if other_begin < end <= other_finish:
                finish = other_finish
                break
    return begin, finish


def _cut_to_budget(
    text: str, anchor_start: int, anchor_end: int, budget: int
) -> str:
    """以依据句为中心截到 ``budget`` 字以内（句界处截，截掉的一侧加 ``…``）。

    ``anchor_start/anchor_end`` 是**依据**在 ``text`` 里的区间——截断时它一定
    被保住（高亮要落在词上，词被截掉这张卡就没法用了）。
    """
    if len(text) <= budget:
        return text
    sentences = _sentence_spans(text)
    if not sentences:
        return text[: budget - 1] + ELLIPSIS

    index = _sentence_index(sentences, anchor_start)
    core_begin, core_finish = sentences[index]
    if anchor_end > core_finish:
        # 依据跨句 ⇒ 并起来（§5 的依据句本来就可能跨句）。
        for other_begin, other_finish in sentences:
            if other_begin < anchor_end <= other_finish:
                core_finish = other_finish
                break
    core_len = core_finish - core_begin
    if core_len > budget:
        # 单句就超预算 ⇒ 没有句界可退，改为**以锚点为中心**硬截（保住高亮）。
        keep = budget - 2  # 预留两个 … 的位置
        span = max(1, anchor_end - anchor_start)
        if span > keep:
            span = keep
            anchor_end = anchor_start + keep
        left = max(0, (keep - span) // 2)
        right = max(0, keep - span - left)
        start = max(core_begin, anchor_start - left)
        end = min(core_finish, anchor_end + right)
        prefix = ELLIPSIS if start > core_begin else ""
        suffix = ELLIPSIS if end < core_finish else ""
        return prefix + text[start:end] + suffix

    spare = budget - 2 - core_len  # 预留两个 … 的位置
    first = index
    last = index
    remaining = spare

    def _left_cost() -> int | None:
        """左边再扩一句要多长（含句间空白）。"""
        if first <= 0:
            return None
        return sentences[first][0] - sentences[first - 1][0]

    def _right_cost() -> int | None:
        """右边再扩一句要多长（含句间空白）。"""
        if last + 1 >= len(sentences):
            return None
        return sentences[last + 1][1] - sentences[last][1]

    # 每次扩**更便宜的那一边**（便宜 ⇒ 更接近居中），直到两边都扩不动。
    while True:
        left_cost = _left_cost()
        right_cost = _right_cost()
        left_fits = left_cost is not None and left_cost <= remaining
        right_fits = right_cost is not None and right_cost <= remaining
        if not left_fits and not right_fits:
            break
        if left_fits and (not right_fits or left_cost <= right_cost):
            first -= 1
            remaining -= left_cost
        else:
            last += 1
            remaining -= right_cost

    def _render(first_index: int, last_index: int) -> str:
        body = text[sentences[first_index][0] : sentences[last_index][1]]
        prefix = ELLIPSIS if sentences[first_index][0] > 0 else ""
        suffix = ELLIPSIS if sentences[last_index][1] < len(text) else ""
        return prefix + body + suffix

    result = _render(first, last)
    # 句子之间的空白也算长度 ⇒ 再收一次，保证不超预算。
    while len(result) > budget and first < last:
        left_gap = sentences[first + 1][0] - sentences[first][0]
        right_gap = sentences[last][1] - sentences[last - 1][1]
        if right_gap >= left_gap:
            last -= 1
        else:
            first += 1
        result = _render(first, last)
    if len(result) > budget:
        result = result[: budget - 1] + ELLIPSIS
    return result

def _strip_keep_verbatim(page_text: str, begin: int, finish: int) -> tuple[str, int]:
    """切一段原文并去掉首尾空白，返回 ``(文字, 相对起点的偏移)``。

    去空白后必须仍是原文的逐字子串——不是就退回未去空白的切片。
    """
    raw = page_text[begin:finish]
    stripped = raw.strip()
    lead = len(raw) - len(raw.lstrip())
    if stripped and stripped in page_text:
        return stripped, lead
    return raw, 0


def _locate_evidence(
    page_text: str,
    evidence: str | None,
    start: int | None,
    end: int | None,
    front: str,
) -> tuple[int, int] | None:
    """依据在页面原文里的位置（先依据句，再卡的偏移，最后词本身）。"""
    if evidence:
        index = page_text.find(evidence)
        if index >= 0:
            return index, index + len(evidence)
    if isinstance(start, int) and isinstance(end, int):
        if 0 <= start < end <= len(page_text):
            return start, end
    index = page_text.find(front)
    if index >= 0:
        return index, index + len(front)
    return None


def _paragraph_spans_by_breaks(text: str, breaks: list[int] | None) -> list[tuple[int, int]]:
    """按**段落界**（契约 §2.1 的 ``breaks``）切出来的段落区间。

    区间是原文坐标，**不含**段落界那个换行本身。
    """
    spans: list[tuple[int, int]] = []
    start = 0
    for offset in sorted({int(value) for value in breaks or []}):
        if offset < start or offset >= len(text):
            continue
        if offset > start:
            spans.append((start, offset))
        start = offset + 1
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _span_containing(spans: list[tuple[int, int]], offset: int) -> tuple[int, int] | None:
    """包含 ``offset`` 的那一段（找不到就落在最接近的一段）。"""
    for begin, finish in spans:
        if begin <= offset < finish:
            return begin, finish
    if not spans:
        return None
    return min(spans, key=lambda item: min(abs(item[0] - offset), abs(item[1] - offset)))


def _build_context_merged(
    page_text: str,
    breaks: list[int],
    merged_page: str,
    offset_map: list[int],
    front: str,
    evidence: str | None,
    start: int | None,
    end: int | None,
) -> dict | None:
    """有 ``breaks`` 时的 context（契约 §2.1 + §7.1）。

    段落以 ``breaks`` 为界，段落里的软折行按合并口径合并掉——``text`` 就是
    原文对应区间经 :func:`bookcard.engine.reflow.reflow` 后的结果。
    """
    if not merged_page.strip():
        return None

    evidence_pos = _locate_evidence(page_text, evidence, start, end, front)
    if evidence_pos is None:
        return None

    # ---- 底稿：依据所在的那一段（原文坐标 → 合并后的文字）----
    paragraphs = _paragraph_spans_by_breaks(page_text, breaks)
    span = _span_containing(paragraphs, evidence_pos[0])
    if span is None:
        return None
    base_text = reflow_span(page_text, breaks, span[0], span[1])
    if not base_text.strip():
        return None

    # 校验：整页合并后切出来的同一段，必须和"原文区间经 reflow"逐字一致。
    merged_begin, merged_end = map_span(offset_map, span[0], span[1])
    if merged_page[merged_begin:merged_end] != base_text:
        return None

    # ---- 锚点：优先取**词**在底稿里的位置（高亮落在词上，截断时必须保住）----
    word_hits = _find_highlights(base_text, front)
    if word_hits:
        anchor_start = word_hits[0]["start"]
        anchor_end = word_hits[0]["end"]
    else:
        anchor_start = map_offset(offset_map, evidence_pos[0]) - merged_begin
        anchor_end = map_offset(offset_map, evidence_pos[1] - 1) + 1 - merged_begin
        anchor_start = max(0, min(anchor_start, max(len(base_text) - 1, 0)))
        anchor_end = max(anchor_start + 1, min(anchor_end, len(base_text)))

    text = _cut_to_budget(base_text, anchor_start, anchor_end, MAX_CONTEXT_LEN)
    highlights = _find_highlights(text, front)
    if not highlights:
        # 这一段里没有这个词 ⇒ 改用依据句（合并后的那一段文字）。
        sentences = _sentence_spans(base_text)
        index = _sentence_index(sentences, anchor_start)
        if sentences:
            begin, finish = sentences[index]
            sentence_text = base_text[begin:finish]
            sentence_highlights = _find_highlights(sentence_text, front)
            if sentence_highlights:
                hit = sentence_highlights[0]
                text = _cut_to_budget(sentence_text, hit["start"], hit["end"], MAX_CONTEXT_LEN)
                highlights = _find_highlights(text, front)
    if not highlights:
        # 再退一步：用整页里包住这个词的那一句（合并后）。
        page_hits = _find_highlights(merged_page, front)
        if page_hits:
            hit = page_hits[0]
            sentences = _sentence_spans(merged_page)
            span_sentence = _sentence_span(sentences, hit["start"], hit["end"])
            if span_sentence is not None:
                sentence_text = merged_page[span_sentence[0] : span_sentence[1]]
                sentence_highlights = _find_highlights(sentence_text, front)
                if sentence_highlights:
                    hit = sentence_highlights[0]
                    text = _cut_to_budget(
                        sentence_text, hit["start"], hit["end"], MAX_CONTEXT_LEN
                    )
                    highlights = _find_highlights(text, front)

    if not highlights:
        return None
    return {"text": text, "highlights": highlights}


def build_context(
    page_text: str,
    front: str,
    evidence: str | None = None,
    start: int | None = None,
    end: int | None = None,
    breaks: list[int] | None = None,
) -> dict | None:
    """给一张卡造 ``source.context``（契约 §7.1 文本场景）。

    有 ``breaks``（契约 §2.1 的段落界）时：段落以 ``breaks`` 为界，段落里的
    软折行按合并口径合并掉；``text`` = 原文对应区间经 ``reflow`` 后的结果，
    超过 :data:`MAX_CONTEXT_LEN` 字时以依据句为中心截取，在句界处截，截掉的
    一侧加 ``…``。``highlights`` = ``front`` 在这段文字里的每一处偏移。

    没有 ``breaks`` 的老数据：段落以空行为界，文字保持原文逐字（行为不变）。

    造不出来（页面文字里根本找不到这个词）返回 ``None``。
    """
    if not page_text or not front:
        return None

    if breaks is not None:
        merged_page, offset_map = reflow(page_text, breaks)
        return _build_context_merged(
            page_text, breaks, merged_page, offset_map, front, evidence, start, end
        )

    sentences = _sentence_spans(page_text)
    if not sentences:
        return None

    # ---- 1. 定位依据句 ----
    # ``evidence_pos`` = 依据**本身**在页面文字里的位置；``evidence_span`` =
    # 包住它的那一句（段落/前后句都要用它找）。
    evidence_pos = _locate_evidence(page_text, evidence, start, end, front)
    if evidence_pos is None:
        return None
    evidence_span = _sentence_span(sentences, evidence_pos[0], evidence_pos[1])

    # ---- 2. 底稿：段落（空行界）；没有空行就用依据句前后各一句 ----
    paragraphs = _paragraph_spans(page_text)
    if len(paragraphs) > 1:
        base_text = ""
        base_begin = 0
        for begin, finish in paragraphs:
            if begin <= evidence_span[0] < finish:
                base_text, lead = _strip_keep_verbatim(page_text, begin, finish)
                base_begin = begin + lead
                break
        if not base_text:
            return None
    else:
        index = _sentence_index(sentences, evidence_span[0])
        begin = sentences[max(0, index - 1)][0]
        finish = sentences[min(len(sentences) - 1, index + 1)][1]
        base_text, lead = _strip_keep_verbatim(page_text, begin, finish)
        base_begin = begin + lead

    if not base_text:
        return None

    # 锚点：优先取**词**在底稿里的位置（高亮落在词上，截断时它必须保住）；
    # 没有词就用依据的位置。
    word_hits = _find_highlights(base_text, front)
    if word_hits:
        anchor_start = word_hits[0]["start"]
        anchor_end = word_hits[0]["end"]
    else:
        anchor_start = min(max(evidence_pos[0] - base_begin, 0), max(len(base_text) - 1, 0))
        anchor_end = min(
            anchor_start + max(1, evidence_pos[1] - evidence_pos[0]), len(base_text)
        )

    # ---- 3. 截到 400 字以内 ----
    text = _cut_to_budget(base_text, anchor_start, anchor_end, MAX_CONTEXT_LEN)

    # ---- 4. 高亮：front 的每一处出现 ----
    highlights = _find_highlights(text, front)
    if not highlights:
        # 段落里没有这个词 ⇒ 改用依据句本身。
        sentence_text, _ = _strip_keep_verbatim(page_text, evidence_span[0], evidence_span[1])
        sentence_highlights = _find_highlights(sentence_text, front)
        if sentence_highlights:
            hit = sentence_highlights[0]
            text = _cut_to_budget(
                sentence_text, hit["start"], hit["end"], MAX_CONTEXT_LEN
            )
            highlights = _find_highlights(text, front)
        else:
            # 再退一步：用包住这个词的那一句。
            word_index = page_text.find(front)
            if word_index >= 0:
                span = _sentence_span(sentences, word_index, word_index + len(front))
                if span is not None:
                    word_text, _ = _strip_keep_verbatim(page_text, span[0], span[1])
                    word_highlights = _find_highlights(word_text, front)
                    if word_highlights:
                        hit = word_highlights[0]
                        text = _cut_to_budget(
                            word_text, hit["start"], hit["end"], MAX_CONTEXT_LEN
                        )
                        highlights = _find_highlights(text, front)

    if not highlights:
        return None
    return {"text": text, "highlights": highlights}


def build_contexts(work: WorkDir | str, cards: list[dict]) -> tuple[dict[str, dict], dict]:
    """给一批卡算 ``source.context``。

    返回 ``({card_id: context}, 统计)``。

    页面有 ``breaks``（契约 §2.1）⇒ 段落以段落界为界、软折行按合并口径合并；
    没有 ``breaks`` 的老数据 ⇒ 走原来的空行口径（行为不变）。
    """
    work = work if isinstance(work, WorkDir) else WorkDir(work)
    try:
        pages = work.pages()
    except PipelineError:
        pages = []
    page_texts = {int(row.get("page") or 0): row.get("text") or "" for row in pages}
    page_breaks = {int(row.get("page") or 0): row.get("breaks") for row in pages}

    # 整页合并后的文字按页缓存（一张卡算一次就够）。
    merged_cache: dict[int, tuple[str, list[int]]] = {}

    contexts: dict[str, dict] = {}
    stats = {
        "cards": len(cards),
        "with_context": 0,
        "without_context": 0,
        "merged_pages": 0,
    }
    for card in cards:
        source = card.get("source") or {}
        page = source.get("page")
        page_number = int(page) if isinstance(page, int) else 0
        text = page_texts.get(page_number, "")
        breaks = page_breaks.get(page_number)
        if breaks is None:
            context = build_context(
                text,
                str(card.get("front") or ""),
                source.get("evidence"),
                source.get("start"),
                source.get("end"),
            )
        else:
            if page_number not in merged_cache:
                merged_cache[page_number] = reflow(text, list(breaks))
                stats["merged_pages"] += 1
            merged_page, offset_map = merged_cache[page_number]
            context = _build_context_merged(
                text,
                list(breaks),
                merged_page,
                offset_map,
                str(card.get("front") or ""),
                source.get("evidence"),
                source.get("start"),
                source.get("end"),
            )
        card_id = str(card.get("card_id") or "")
        if context is None:
            stats["without_context"] += 1
            continue
        contexts[card_id] = context
        stats["with_context"] += 1
    return contexts, stats


__all__ = [
    "BLOCKED_VERDICTS",
    "DEFAULT_SCENE",
    "ELLIPSIS",
    "MAX_CONTEXT_LEN",
    "MAX_MEDIAN_IMAGE_COVERAGE",
    "MIN_TEXT_ORIGIN_RATIO",
    "SCENE_IMAGE",
    "SCENE_TEXT",
    "build_context",
    "build_contexts",
    "detect_scene_mode",
]
