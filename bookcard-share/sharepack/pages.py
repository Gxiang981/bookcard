"""从 PDF 渲染全书页面 —— 大图（给场景用）+ 缩略图（给底部页条用）。

**为什么渲染而不是直接用包里的图**：包里只带了「有卡的页」（这本 199 页 / 全书
239 页）。要显示「本书的每页」，缺的页得从 PDF 补。

**重渲染是安全的**：实测从同一份 PDF 按 150dpi 重渲染出来的 jpg，与包里已有的
jpg **逐字节一致**（同一份 pymupdf 渲染 + 同样的 jpg_quality=85）⇒ 转出来的
webp 也一致 ⇒ App 侧算出来的 sha256 不变。已有的图不会因为重新出片而"换身份"。

渲染参数跟引擎的 ``src/bookcard/pipeline/locate.py`` 保持一致：

- 大图：``RENDER_DPI = 150``、JPEG 质量 85 → 再转 webp（质量对齐 App 的
  ``IMPORT_QUALITY = 95``）
- 缩略图：按目标宽度反算 dpi，直接出小图（不从大图缩，省内存和时间）
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .webp import to_webp_bytes

#: 大图 dpi —— 必须跟引擎一致，否则 bbox 坐标对不上。
FULL_DPI = 150

#: 大图先编成 jpeg 再转 webp（跟引擎的链路一模一样）。
JPEG_QUALITY = 85

#: 缩略图的目标宽度（像素）。底部页条的格子约 110px 宽，给 2x 留余量。
THUMB_WIDTH = 280

#: 缩略图的 webp 质量。
THUMB_QUALITY = 72


@dataclass(frozen=True)
class RenderedPage:
    """一页的两份图。"""

    page: int
    full: bytes
    thumb: bytes
    width: int
    height: int


def render_book(
    pdf_path: str | Path,
    page_numbers: list[int] | None = None,
    *,
    full_dpi: int = FULL_DPI,
    thumb_width: int = THUMB_WIDTH,
    quality: int = 95,
    thumb_quality: int = THUMB_QUALITY,
    progress=None,
) -> dict[int, RenderedPage]:
    """渲染 PDF 的页面。

    ``page_numbers`` 给 None ⇒ 全书每一页都渲。
    ``progress`` 是 ``fn(done, total)`` 的回调（给 CLI 报进度用）。
    """
    import pymupdf

    pdf_path = Path(pdf_path)
    doc = pymupdf.open(pdf_path)
    try:
        pages = page_numbers if page_numbers is not None else list(range(1, doc.page_count + 1))
        out: dict[int, RenderedPage] = {}
        total = len(pages)
        for done, number in enumerate(pages, start=1):
            if number < 1 or number > doc.page_count:
                continue
            page = doc[number - 1]

            # 大图：跟引擎同参数 ⇒ 字节一致
            pix = page.get_pixmap(dpi=full_dpi)
            jpg = pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY)
            full = to_webp_bytes(jpg, quality=quality)
            width, height = pix.width, pix.height

            # 缩略图：按目标宽度反算 dpi，直接出小图
            rect = page.rect
            scale = thumb_width / rect.width if rect.width else 1.0
            tpix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
            tjpg = tpix.tobytes("jpeg", jpg_quality=80)
            thumb = to_webp_bytes(tjpg, quality=thumb_quality)

            out[number] = RenderedPage(
                page=number, full=full, thumb=thumb, width=width, height=height
            )
            if progress is not None:
                progress(done, total)
        return out
    finally:
        doc.close()


def find_pdf(work_dir: str | Path | None, file_name: str | None) -> Path | None:
    """在几个常见位置找书的 PDF。

    引擎的目录布局是::

        train/data/src/<file_name>.pdf          ← 原书 PDF
        train/data/bookcard/books/<fp12>/       ← 书工作目录

    所以从工作目录往上找几层，看有没有 ``src/``。
    """
    if not work_dir:
        return None
    work = Path(work_dir)
    name = file_name or ""
    candidates: list[Path] = []
    if name:
        base = work
        for _ in range(4):
            base = base.parent
            candidates.append(base / "src" / name)
            candidates.append(base / name)
    candidates.append(work / "book.pdf")
    for c in candidates:
        if c.is_file():
            return c
    return None
