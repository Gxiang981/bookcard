#!/usr/bin/env python3
"""从书的工作目录生成**最终分享页**（书态 + 目录 + 卡片流）。

数据源和最终卡包**同一份** ``pack/<fp12>.ttfc.zip`` 里的 manifest.json ——
页面看到的词库和卡，跟导入 App 的是同一套，不会两边不一致。

用法：
    python3 build_share_page.py <书的工作目录> [书名] [学科]
"""
from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, "/bookcard-share")

from sharepack.html import render_html  # noqa: E402
from sharepack.toc import _toc_pages as _tocv_pages  # noqa: E402


def _scene(
    card: dict,
    book_title: str,
    page_images: dict[int, str],
) -> dict:
    """卡在书页上的位置（图片场景）。

    卡包里位置放在 ``images[].occurrences``（App 的形状），这里换成分享包的
    页面图路径（``pages/pXXXX.webp``，和 html 同级）。
    """
    source = card.get("source") or {}
    images = card.get("images") or []
    if not images:
        return {}
    image = images[0]
    occurrences = image.get("occurrences") or []
    if not occurrences:
        return {}
    page = int(occurrences[0].get("page") or source.get("page") or 0)
    src = page_images.get(page)
    if not page or not src:
        return {}
    return {
        "kind": "image",
        "book": book_title,
        "page": page,
        "printed_page": source.get("printed_page"),
        "src": src,
        "width": image.get("width") or 0,
        "height": image.get("height") or 0,
        "boxes": [
            {"x": occ["x"], "y": occ["y"], "w": occ["w"], "h": occ["h"]}
            for occ in occurrences
            if occ.get("x") is not None
        ],
    }


def main() -> int:
    book_dir = Path(sys.argv[1]).expanduser()
    title = sys.argv[2] if len(sys.argv) > 2 else ""
    subject = sys.argv[3] if len(sys.argv) > 3 else ""

    book = json.loads((book_dir / "book.json").read_text(encoding="utf-8"))
    toc = json.loads((book_dir / "toc.json").read_text(encoding="utf-8"))
    pages_index = json.loads((book_dir / "pages-index.json").read_text(encoding="utf-8"))
    pages = pages_index["pages"]

    fp12 = book["fp12"]
    pack = book_dir / "pack" / f"{fp12}.ttfc.zip"
    if not pack.exists():
        print(f"还没打包：{pack} 不存在（先跑 bookcard flow pack）")
        return 1

    sections = json.loads((book_dir / "sections.json").read_text(encoding="utf-8"))
    section_items = sections["sections"] if isinstance(sections, dict) else sections
    by_slug = {str(s.get("sid")): s for s in section_items}

    manifest = json.loads(zipfile.ZipFile(pack).open("manifest.json").read().decode("utf-8"))
    category = manifest.get("category") or {}
    book_meta = manifest.get("book") or {}
    book_title = title or book_meta.get("title") or book.get("title") or fp12

    page_images = {
        int(page["page"]): page.get("image") or "" for page in pages if page.get("page")
    }

    libraries = []
    cards = []
    for lib in category.get("libraries") or []:
        lib_id = f"{fp12}-{lib.get('slug', '').rsplit('-', 1)[-1]}"
        sid = str(lib.get("slug", "")).rsplit("-", 1)[-1]
        section = by_slug.get(sid) or {}
        libraries.append(
            {
                "id": lib_id,
                "name": lib.get("name") or lib_id,
                "sort_order": lib.get("sort_order") or 0,
                "book_id": fp12,
                "count": len(lib.get("cards") or []),
                # 章节的页范围 —— 页面用它把目录条目对上词库
                "sid": sid,
                "page_start": section.get("page_start"),
                "page_end": section.get("page_end"),
            }
        )
        for index, card in enumerate(lib.get("cards") or []):
            source = card.get("source") or {}
            cards.append(
                {
                    "id": f"{lib_id}:{index:08d}",
                    "library_id": lib_id,
                    # 卡所在的页 —— 页面靠它把目录条目映射到卡片
                    "page": source.get("page"),
                    "front": card.get("front") or "",
                    "back": card.get("back") or "",
                    "tags": [book_title, lib.get("name") or lib_id],
                    "sort_order": index + 1,
                    "scene": _scene(card, book_title, page_images),
                }
            )

    payload = {
        "book_only": False,
        "book": {
            "id": fp12,
            "title": book_title,
            "subtitle": subject or book_meta.get("subject") or book.get("subject") or "",
            "visibility": manifest.get("visibility") or "private",
            "scene_mode": manifest.get("scene_mode") or "image",
            "fp12": fp12,
            "page_count": book.get("page_count") or len(pages),
            "domain": book.get("domain") or "",
            "level": book.get("level") or "",
            "tags": book.get("tags") or [],
        },
        "libraries": libraries,
        "cards": cards,
        "toc": {
            "source": toc.get("source") or "",
            "offset": toc.get("offset"),
            # 抽词切章节用的层级（用户在第二步确认的）—— 页面默认就按它显示目录
            "extract_level": toc.get("extract_level") or 1,
            "entries": toc.get("entries") or [],
            "pages": toc.get("pages") or _tocv_pages(book_dir),
        },
        "pages": pages,
    }

    html = render_html(payload)
    # 页面图（pages/ thumbs/）和 html 同级 —— 分享包直接整个目录发走
    out_dir = book_dir
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    print(
        f"分享页：{out_dir / 'index.html'}（{len(html) // 1024} KB，"
        f"{len(pages)} 页，{len(libraries)} 个词库，{len(cards)} 张卡）"
    )
    share_dir = _export_share(book_dir, out_dir)
    if share_dir:
        print(f"分享包（下载文件夹）：{share_dir}")
    return 0


def _export_share(book_dir: Path, out_dir: Path) -> str:
    """把分享页整份复制到「下载」文件夹（html + 页面图）。

    分享页是**一个目录**（index.html 和 pages/ 同级），发走时要整个发 ——
    所以复制目录，不是只拷 html。
    """
    sys.path.insert(0, "/src")
    from bookcard.paths import output_dir, safe_name  # noqa: PLC0415

    title = ""
    try:
        meta = json.loads((book_dir / "book.json").read_text(encoding="utf-8"))
        title = str(meta.get("title") or "")
    except Exception:
        title = ""
    stem = safe_name(title, book_dir.name)
    target = output_dir() / stem
    try:
        target.mkdir(parents=True, exist_ok=True)
        (target / "index.html").write_text(
            (out_dir / "index.html").read_text(encoding="utf-8"), encoding="utf-8"
        )
        for extra in ("pages-index.json",):
            src = out_dir / extra
            if src.exists():
                (target / extra).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        pages_src = out_dir / "pages"
        pages_dst = target / "pages"
        if pages_src.is_dir():
            if pages_dst.exists():
                shutil.rmtree(pages_dst)
            shutil.copytree(pages_src, pages_dst)
        return str(target)
    except Exception as exc:
        print(f"分享包导出失败（不影响工作目录里的分享页）：{exc}")
        return ""


if __name__ == "__main__":
    raise SystemExit(main())
