"""bookcard 探针：把一本书跑一遍，出一份体检报告。

只依赖 pymupdf 和标准库，不调 LLM、不做 OCR。
判定规则全在 :mod:`bookcard.probe.rules` 里，这里只负责「抽」和「拼」。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

import pymupdf

from bookcard.probe import rules

#: 报告格式版本。
PROBE_VERSION = 1

#: 水印判定阈值：一个同样的行至少出现在多少比例的页上算水印。
WATERMARK_PAGE_RATIO = 0.30

#: 找印刷目录页时只看前多少页。
TOC_SEARCH_PAGES = 15


def _empty_report(path: Path) -> dict:
    """空报告骨架：坏文件也保持同样的结构。"""
    return {
        "probe_version": PROBE_VERSION,
        # ok 就是「这本读得懂」：坏文件为 False，其余为 True。
        # 跟 verdict 是同一件事的两种说法（ok 布尔、verdict 细分成五种）。
        "ok": False,
        "file": {
            "path": str(path),
            "name": path.name,
            "bytes": 0,
            "sha256": "",
            "is_pdf": False,
            "page_count": 0,
            "error": None,
        },
        "verdict": "broken",
        "lang": "unknown",
        "text_layer": {
            "pages": {"text": 0, "watermark_only": 0, "garbled": 0, "empty": 0},
            "usable_ratio": 0.0,
            "watermarks": [],
            "per_page": [],
            # 扩展字段：便于和盘点文档 §3.2 的「前 20 页口径」对照
            "first20_ratio": 0.0,
            "sampled_pages": [],
        },
        "page_numbers": {
            "status": "unknown",
            "position": None,
            "offset": None,
            "confidence": 0.0,
            "evidence": [],
            "side": None,
        },
        "toc": {
            "chosen": "none",
            "bookmarks": {
                "total": 0,
                "manual": 0,
                "auto_anchor": 0,
                "residue": 0,
                "sample": [],
            },
            "printed_toc_pages": [],
        },
        "notes": {
            "numbered_notes": False,
            "markers": {"circled": 0, "bracket": 0, "superscript": 0},
            "sample": [],
        },
        "warnings": [],
    }


def _broken(report: dict, message: str) -> dict:
    """标记坏文件。"""
    report["file"]["error"] = message
    report["verdict"] = "broken"
    report["ok"] = False
    report["warnings"].append(message)
    return report


def _extract_page(page: pymupdf.Page) -> dict:
    """抽一页的文本、行、span。"""
    data = page.get_text("dict")
    lines: list[dict] = []
    spans: list[dict] = []

    for block in data.get("blocks", []):
        if block.get("type") != 0:  # 0 = 文本块，1 = 图片
            continue
        for line in block.get("lines", []):
            line_spans = line.get("spans", [])
            line_text = "".join(span.get("text", "") for span in line_spans)
            lines.append({"text": line_text, "bbox": tuple(line.get("bbox", (0.0, 0.0, 0.0, 0.0)))})
            for span in line_spans:
                spans.append(
                    {
                        "text": span.get("text", ""),
                        "bbox": tuple(span.get("bbox", (0.0, 0.0, 0.0, 0.0))),
                        "size": float(span.get("size", 0.0)),
                        "flags": int(span.get("flags", 0)),
                    }
                )

    return {
        "number": page.number + 1,
        "text": "\n".join(line["text"] for line in lines),
        "lines": lines,
        "spans": spans,
        "height": float(page.rect.height),
        "width": float(page.rect.width),
    }


def _strip_page(page: dict, watermarks: Sequence[str]) -> dict:
    """剥掉一页里的水印。

    返回的 dict 里 ``text`` 是剥完的正文，``raw_text`` 是剥之前的原文。
    """
    if not watermarks:
        return {
            **page,
            "raw_text": page["text"],
            "lines": list(page["lines"]),
            "had_watermark": False,
        }

    watermark_set = {rules.normalize_line(w) for w in watermarks}
    stripped_lines: list[dict] = []
    had_watermark = False
    for line in page["lines"]:
        if rules.normalize_line(line["text"]) in watermark_set:
            had_watermark = True
            continue
        text = line["text"]
        for watermark in watermarks:
            if not watermark:
                continue
            pattern = re.compile(r"\s*".join(re.escape(part) for part in watermark.split(" ")))
            if pattern.search(text):
                had_watermark = True
            text = pattern.sub("", text)
        stripped_lines.append({**line, "text": text})

    return {
        **page,
        "raw_text": page["text"],
        "text": "\n".join(line["text"] for line in stripped_lines),
        "lines": stripped_lines,
        "had_watermark": had_watermark,
    }


def _bottom_lines(page: dict, ratio: float = 0.20) -> list[dict]:
    """页面底部 ``ratio`` 高度内的行。"""
    limit = (1.0 - ratio) * page["height"]
    return [line for line in page["lines"] if line["bbox"][1] >= limit]


def _count_superscripts(page: dict) -> int:
    """统计上标数字 span。

    主判据是 pymupdf 的 flags 第 0 位（上标）。字号兜底只在正文区域用，
    免得把页脚的页码当成上标。
    """
    sizes = sorted(span["size"] for span in page["spans"] if span["size"] > 0)
    if not sizes:
        median = 0.0
    else:
        median = sizes[len(sizes) // 2]

    count = 0
    for span in page["spans"]:
        text = span["text"].strip()
        if not text or not any(ch.isdigit() for ch in text):
            continue
        if span["flags"] & 1:
            count += 1
            continue
        if median and span["size"] < 0.75 * median:
            in_edge_band = (
                span["bbox"][1] >= (1.0 - rules.PAGE_NUMBER_BAND) * page["height"]
                or span["bbox"][3] <= rules.PAGE_NUMBER_BAND * page["height"]
            )
            if not in_edge_band:
                count += 1
    return count


def broken_report(path: str | Path, message: str) -> dict:
    """造一份「坏文件」报告（CLI 兜底用）。"""
    return _broken(_empty_report(Path(path)), message)


def probe(path: str | Path, *, pages: int | None = None) -> dict:
    """跑一遍探针，返回报告字典。

    参数
    ----
    path:
        PDF 路径。
    pages:
        只看前 N 页（调试用）；``None`` 表示全书。

    坏文件不抛异常：返回 ``verdict == "broken"`` 的报告，``file.error`` 里写原因。
    """
    file_path = Path(path)
    report = _empty_report(file_path)

    try:
        data = file_path.read_bytes()
    except OSError as exc:
        return _broken(report, f"文件打不开：{exc.strerror or exc}")

    report["file"]["bytes"] = len(data)
    report["file"]["sha256"] = hashlib.sha256(data).hexdigest()

    if b"%PDF" not in data[:1024]:
        return _broken(report, "不是 PDF：文件头没有 %PDF")

    try:
        doc = pymupdf.open(file_path)
    except Exception as exc:  # pymupdf 对坏文件抛的异常类型不固定
        return _broken(report, f"PDF 打不开：{exc}")

    try:
        if doc.needs_pass:
            return _broken(report, "PDF 已加密")
        try:
            _analyze(doc, report, pages)
        except Exception as exc:  # 兜底：单本书出问题不能打断整批
            return _broken(report, f"解析失败：{type(exc).__name__}: {exc}")
    finally:
        doc.close()

    return report


def _analyze(doc: pymupdf.Document, report: dict, pages: int | None) -> None:
    """真正的分析流程，结果就地写进 ``report``。"""
    page_count = doc.page_count
    report["file"]["page_count"] = page_count
    report["file"]["is_pdf"] = True

    limit = page_count if pages is None else min(page_count, max(0, pages))
    extracted = [_extract_page(doc[index]) for index in range(limit)]

    # ---- 水印：先找出来，剥掉，再做后面的判定 ----
    watermarks = rules.find_watermark_lines(
        [[line["text"] for line in page["lines"]] for page in extracted],
        threshold=WATERMARK_PAGE_RATIO,
    )
    report["text_layer"]["watermarks"] = watermarks
    if watermarks:
        report["warnings"].append(
            f"检测到 {len(watermarks)} 条水印行（出现在 ≥{WATERMARK_PAGE_RATIO:.0%} 的页上）"
        )

    stripped_pages = [_strip_page(page, watermarks) for page in extracted]

    # ---- 页类型 ----
    counts = {"text": 0, "watermark_only": 0, "garbled": 0, "empty": 0}
    per_page: list[str] = []
    for page in stripped_pages:
        page_class = rules.classify_page(
            page["raw_text"],
            page["text"],
            had_watermark=page.get("had_watermark", False),
        )
        counts[page_class] += 1
        per_page.append(page_class)
        page["class"] = page_class
        page["bottom_lines"] = _bottom_lines(page)

    total = len(stripped_pages)
    report["text_layer"]["pages"] = counts
    report["text_layer"]["per_page"] = per_page
    report["text_layer"]["sampled_pages"] = [page["number"] for page in stripped_pages]
    report["text_layer"]["usable_ratio"] = round(counts["text"] / total, 2) if total else 0.0

    first20 = stripped_pages[:20]
    first20_text = sum(1 for page in first20 if page["class"] == "text")
    report["text_layer"]["first20_ratio"] = (
        round(first20_text / len(first20), 2) if first20 else 0.0
    )

    verdict = rules.verdict_from_counts(counts, total)
    report["verdict"] = verdict
    report["ok"] = verdict != "broken"

    # ---- 书签 / 目录 ----
    toc_entries = doc.get_toc()
    bookmarks = rules.classify_bookmarks(toc_entries, page_count)
    report["toc"]["bookmarks"] = bookmarks
    printed_toc_pages = rules.find_printed_toc_pages(stripped_pages, limit=TOC_SEARCH_PAGES)
    report["toc"]["printed_toc_pages"] = printed_toc_pages

    if bookmarks["manual"] >= 3:
        report["toc"]["chosen"] = "bookmarks"
    elif printed_toc_pages:
        report["toc"]["chosen"] = "printed_page"
    elif verdict in ("scanned", "garbled"):
        report["toc"]["chosen"] = "unknown"
    else:
        report["toc"]["chosen"] = "none"

    if bookmarks["total"] and bookmarks["auto_anchor"] == bookmarks["total"]:
        report["warnings"].append("书签全是自动页锚点，不能当目录用")
    if bookmarks["total"] and bookmarks["residue"] >= bookmarks["total"] / 2:
        report["warnings"].append("书签是制作残留（InDesign 源文件名），不能当目录用")

    # ---- 页码 ----
    if verdict in ("scanned", "garbled", "broken"):
        report["page_numbers"]["status"] = "unknown"
    else:
        report["page_numbers"] = rules.detect_page_numbers(stripped_pages)
    if report["page_numbers"]["status"] == "found":
        offset = report["page_numbers"]["offset"]
        report["warnings"].append(f"印刷页码偏移 {offset}（印刷页码 = 物理页号 {offset:+d}）")

    # ---- 编号注释 ----
    markers = {"circled": 0, "bracket": 0, "superscript": 0}
    numbered_notes = False
    note_samples: list[str] = []
    for page in stripped_pages:
        if page["class"] != "text":
            continue
        page_markers = rules.count_markers(page["text"], _count_superscripts(page))
        for key, value in page_markers.items():
            markers[key] += value
        if rules.has_numbered_note(page):
            numbered_notes = True
        for line in page["lines"]:
            if rules.CIRCLED_RE.search(line["text"]) or rules.BRACKET_RE.search(line["text"]):
                sample = line["text"].strip()
                if sample and sample not in note_samples:
                    note_samples.append(sample)
                if len(note_samples) >= 5:
                    break
    report["notes"] = {
        "numbered_notes": numbered_notes,
        "markers": markers,
        "sample": note_samples[:5],
    }

    # ---- 语言 ----
    report["lang"] = rules.detect_lang("\n".join(page["text"] for page in stripped_pages))

    if counts["garbled"]:
        report["warnings"].append(f"{counts['garbled']} 页文本层乱码（字体缺 ToUnicode 之类）")
    if counts["empty"]:
        report["warnings"].append(f"{counts['empty']} 页没有文本")
