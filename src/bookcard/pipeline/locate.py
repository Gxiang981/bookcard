"""§5 定位：页面上找出这个词在哪，填 ``source.bbox`` / ``source.image``。

做法（契约 §5）：

- 把**用到的页**渲染成 150 dpi JPEG（质量 85）到 ``<dir>/images/p<4 位页号>.jpg``；
- 在该页 PDF 上 ``page.search_for(front)`` 找框；多处时取与 ``start``
  序号对应的那处（找不到就取第一处）；
- 把 PDF 点的坐标换算成渲染图像素坐标（``scale = dpi / 72``）；
- 找不到就把 ``bbox`` 留 ``null``，计入 ``locate_rate``。

**扫描件（契约 §2.2）**：``text_origin == "ocr"`` 的页没有文本层，
``search_for`` 找不着 ⇒ 改用 ``ocr/p<4 位页号>.json`` 里的行框：
按词的起止偏移找到它落在哪一行，再**按字符数在行框内等比切**出词的框。
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pymupdf

from bookcard.engine import ocr as ocr_mod
from bookcard.engine.softwrap import find_all_flexible
from bookcard.pipeline.workdir import PipelineError, WorkDir, write_jsonl

#: 契约 §5/§7：页面图统一 150 dpi。
RENDER_DPI = 150

#: 契约 §7：JPEG 质量 85。
JPEG_QUALITY = 85

#: PDF 点 → 像素的放大系数。
PIXEL_SCALE = RENDER_DPI / 72.0

#: 本机缓存的书副本放这里（AGENTS.md 约定）。
BOOKS_CACHE_DIR = Path.home() / ".cache" / "bookcard" / "books"


#: CJK 字体的 em 框很大（12pt 的字能给出 34pt 的框）——
#: 高亮框会盖住上下两行。这里按字号重算上下边界，只留字本身那么高。
ASCENT_RATIO = 0.88
DESCENT_RATIO = 0.22


def _page_fonts(page: pymupdf.Page) -> list[tuple[tuple[float, float, float, float], float, float]]:
    """页面里每个 span 的 ``(bbox, 字号, 基线 y)``。"""
    out: list[tuple[tuple[float, float, float, float], float, float]] = []
    try:
        data = page.get_text("dict")
    except Exception:  # noqa: BLE001
        return out
    for block in data.get("blocks") or []:
        for line in block.get("lines") or []:
            for span in line.get("spans") or []:
                size = float(span.get("size") or 0)
                origin = span.get("origin") or (0.0, 0.0)
                bbox = tuple(float(v) for v in (span.get("bbox") or (0, 0, 0, 0)))
                out.append((bbox, size, float(origin[1])))
    return out


def tight_rect(
    rect: tuple[float, float, float, float],
    fonts: list[tuple[tuple[float, float, float, float], float, float]],
) -> tuple[float, float, float, float]:
    """把 PDF 给的框收紧到文字实际高度。

    PyMuPDF 的 ``search_for`` 返回的是**字体的 em 框**——中文（SourceHanSans 之类）
    的 em 框高达 2.8 倍字号，直接拿来画高亮会盖住上下两行。
    这里找到跟这个框重叠最多的那个 span，按字号和基线重算上下边界；
    找不到就原样返回。
    """
    x0, y0, x1, y1 = rect
    picked: tuple[float, float] | None = None      # (字号, 基线 y)
    picked_key: tuple[float, float, float] | None = None
    center = (y0 + y1) / 2
    for bbox, size, baseline in fonts:
        if not size:
            continue
        bx0, by0, bx1, by1 = bbox
        ox = min(x1, bx1) - max(x0, bx0)
        oy = min(y1, by1) - max(y0, by0)
        if ox <= 0 or oy <= 0:
            continue
        # 重叠面积最大者优先；面积一样时取基线更近的（就是同一行那个 span）
        key = (ox * oy, -abs(baseline - center), size)
        if picked_key is None or key > picked_key:
            picked_key = key
            picked = (size, baseline)
    if picked is None:
        return (x0, y0, x1, y1)
    size, baseline = picked
    return (x0, baseline - size * ASCENT_RATIO, x1, baseline + size * DESCENT_RATIO)


def rect_to_pixel(rect: tuple[float, float, float, float]) -> dict[str, int]:
    """PDF 点坐标 → 150 dpi 像素坐标（左上原点 + 宽高）。

    输入是 ``(x0, y0, x1, y1)``（PyMuPDF 的 rect 口径，右下为 ``x1/y1``），
    输出是契约 §5 的 ``{"x", "y", "w", "h"}``。
    """
    x0, y0, x1, y1 = rect
    scale = PIXEL_SCALE
    x = int(round(x0 * scale))
    y = int(round(y0 * scale))
    w = int(round((x1 - x0) * scale))
    h = int(round((y1 - y0) * scale))
    return {"x": x, "y": y, "w": w, "h": h}


def image_name(page: int) -> str:
    """页面图文件名：``p0012.jpg``（4 位页号，契约 §7）。"""
    return f"p{page:04d}.jpg"


def image_relpath(page: int) -> str:
    """页面图在工作目录/包内的相对路径：``images/p0012.jpg``。"""
    return f"images/{image_name(page)}"


def resolve_pdf(work: WorkDir, pdf: str | Path | None = None) -> Path:
    """找书 PDF：显式路径 → ``<dir>/book.pdf`` → 本机缓存 ``~/.cache/bookcard/books``。"""
    if pdf is not None:
        path = Path(pdf)
        if not path.exists():
            raise PipelineError(f"PDF 不存在：{path}")
        return path

    local = work.root / "book.pdf"
    if local.exists():
        return local

    fp12 = ""
    try:
        fp12 = str(work.book().get("fp12") or "")
    except PipelineError:
        fp12 = ""
    if fp12:
        cached = BOOKS_CACHE_DIR / f"{fp12}.pdf"
        if cached.exists():
            return cached

    raise PipelineError(
        "找不到书 PDF：请用 --pdf 指定，或放到 "
        f"{work.root / 'book.pdf'} / {BOOKS_CACHE_DIR / '<fp12>.pdf'}"
    )


def _occurrence_ordinal(page_text: str, front: str, start: int) -> int:
    """这个词在页文字里是第几次出现（0 起）。

    用来在 ``search_for`` 的多个结果里挑对应的那一处。
    兼容码位（理=U+F9E4）也算命中 —— 用容错匹配。
    """
    if not page_text or not front:
        return 0
    offsets = [start_offset for start_offset, _ in find_all_flexible(page_text, front)]
    if not offsets:
        return 0
    try:
        return offsets.index(start)
    except ValueError:
        return 0


def _same_word(raw: str, front: str) -> bool:
    """原文那段和卡正面是不是同一个词（只是码位不同）。

    兼容表意文字（理=U+F9E4）归一后等于标准的「理」⇒ 是同一个词 ✓。
    """
    return unicodedata.normalize("NFKC", raw) == unicodedata.normalize("NFKC", front)


def _raw_form(page_text: str, start: int, end: int) -> str:
    """候选在原文里的那段字（**原文的码位**）。

    模型写的是标准码位，原文可能是兼容表意文字（理=U+F9E4）；
    用原文这段去搜 PDF 才搜得到。
    """
    if not page_text or not (0 <= start < end <= len(page_text)):
        return ""
    return page_text[start:end]


def _render_page(page: pymupdf.Page, target: Path, dpi: int, quality: int) -> tuple[int, int]:
    """渲染一页成 JPEG，返回 ``(宽, 高)``（像素）。"""
    pixmap = page.get_pixmap(dpi=dpi)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(pixmap.tobytes("jpeg", jpg_quality=quality))
    return pixmap.width, pixmap.height


# ===================== 扫描件：从 OCR 行框里算 =====================


def load_ocr_records(work: WorkDir) -> dict[int, list[dict]]:
    """读 ``ocr/p<4 位页号>.json``：``{页号: 行记录}``。

    没有这个目录就是没有 OCR（数字版书）——返回空字典。
    """
    records: dict[int, list[dict]] = {}
    directory = work.ocr_dir
    if not directory.is_dir():
        return records
    for path in sorted(directory.glob("p*.json")):
        stem = path.stem
        if not stem.startswith("p") or not stem[1:].isdigit():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, list):
            records[int(stem[1:])] = payload
    return records


def ocr_word_rect(
    records: list[dict],
    start: int,
    length: int,
) -> tuple[float, float, float, float] | None:
    """词在 OCR 行框里的位置（PDF 点）。

    口径在 :func:`bookcard.engine.ocr.word_rect`（契约 §2.2：按字符数等比切），
    这里只是转出去，测试和别的模块都从这一处拿。
    """
    return ocr_mod.word_rect(records, start, length)


def locate(
    workdir: WorkDir | str,
    pdf: str | Path | None = None,
    *,
    dpi: int = RENDER_DPI,
    quality: int = JPEG_QUALITY,
) -> dict:
    """给每张卡定位，填 ``source.bbox`` / ``source.image``，写回 ``cards.jsonl``。

    返回统计摘要（卡数、定位成功率、渲染页数等）。
    """
    work = workdir if isinstance(workdir, WorkDir) else WorkDir(workdir)
    cards = work.cards()
    pdf_path = resolve_pdf(work, pdf)

    page_texts = {}
    ocr_pages: set[int] = set()
    try:
        for row in work.pages():
            number = int(row.get("page"))
            page_texts[number] = row.get("text") or ""
            if str(row.get("text_origin") or "") == "ocr":
                ocr_pages.add(number)
    except PipelineError:
        page_texts = {}

    ocr_records = load_ocr_records(work)

    needed_pages = sorted({int(card["source"]["page"]) for card in cards if card.get("source")})
    rendered: dict[int, tuple[int, int]] = {}

    doc = pymupdf.open(pdf_path)
    try:
        for number in needed_pages:
            index = number - 1
            if index < 0 or index >= doc.page_count:
                continue
            page = doc[index]
            rendered[number] = _render_page(
                page, work.images_dir / image_name(number), dpi, quality
            )

        fonts_cache: dict[int, list] = {}

        located = 0
        for card in cards:
            source = card.get("source") or {}
            number = source.get("page")
            front = card.get("front") or ""
            bbox = None
            if isinstance(number, int) and 0 < number <= doc.page_count:
                if number in ocr_pages:
                    # 扫描件：没有文本层 ⇒ 从 OCR 行框里算（契约 §2.2）。
                    rect = ocr_word_rect(
                        ocr_records.get(number, []),
                        int(source.get("start") or 0),
                        len(front),
                    )
                    if rect is not None:
                        bbox = rect_to_pixel(rect)
                        located += 1
                else:
                    page = doc[number - 1]
                    rects = page.search_for(front) if front else []
                    raw = ""
                    if not rects:
                        # 原文可能是兼容码位（理=U+F9E4）—— 用原文那段字再搜一次。
                        raw = _raw_form(
                            page_texts.get(number, ""),
                            int(source.get("start") or 0),
                            int(source.get("end") or 0),
                        )
                        # 只有「确实是同一个词、只是码位不同」才用原文那段
                        if raw and raw != front and _same_word(raw, front):
                            rects = page.search_for(raw)
                    if rects:
                        ordinal = _occurrence_ordinal(
                            page_texts.get(number, ""), raw or front, int(source.get("start") or 0)
                        )
                        rect = rects[ordinal] if ordinal < len(rects) else rects[0]
                        # PDF 的框是字体的 em 框（中文能到字号的 2.8 倍）——
                        # 收紧到文字实际高度，高亮才不会盖住上下两行。
                        if number not in fonts_cache:
                            fonts_cache[number] = _page_fonts(page)
                        bbox = rect_to_pixel(tight_rect(tuple(rect), fonts_cache[number]))
                        located += 1
            source["bbox"] = bbox
            source["image"] = image_relpath(number) if number in rendered else None
            card["source"] = source
    finally:
        doc.close()

    write_jsonl(work.cards_path, cards)

    return {
        "cards": len(cards),
        "located": located,
        "locate_rate": round(located / len(cards), 4) if cards else 0.0,
        "rendered_pages": len(rendered),
        "images_dir": str(work.images_dir),
        "ocr_pages": len(ocr_pages),
    }
