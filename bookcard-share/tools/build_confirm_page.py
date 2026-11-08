#!/usr/bin/env python3
"""从书的工作目录生成「待确认」页面（书态：全书页面 + 目录，可编辑）。

这是第二步的产物：用户翻书、核对目录、改完导出 edits.json。
页面图和最终打包用的是**同一套** ``<book>/pages/``。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/bookcard-share")

from sharepack.html import render_html  # noqa: E402
from sharepack.toc import _toc_pages as _tocv_pages  # noqa: E402


def _read_book(book_dir: Path) -> dict:
    """读 book.json；整本取字还没做完时，退到「看图目录」记的状态。

    批量跑第二步时，扫描件的整本取字要几小时，确认页不能等它 ——
    书名、页数这些「看图目录」那边已经记了（tocv.json）。
    """
    book_file = book_dir / "book.json"
    if book_file.is_file():
        return json.loads(book_file.read_text(encoding="utf-8"))
    tocv_file = book_dir / "tocv" / "tocv.json"
    if tocv_file.is_file():
        state = json.loads(tocv_file.read_text(encoding="utf-8"))
        return {
            "fp12": book_dir.name,
            "title": "",
            "subject": "",
            "page_count": int(state.get("page_count") or 0),
        }
    raise SystemExit(f"书的工作目录里没有 book.json，也没有 tocv/tocv.json：{book_dir}")


def main() -> int:
    book_dir = Path(sys.argv[1]).expanduser()
    title = sys.argv[2] if len(sys.argv) > 2 else ""
    subject = sys.argv[3] if len(sys.argv) > 3 else ""
    # index.html 和 pages/、thumbs/ 同级 —— 和最终卡包的布局一致
    out = book_dir

    book = _read_book(book_dir)
    toc = json.loads((book_dir / "toc.json").read_text(encoding="utf-8"))
    pages = json.loads((book_dir / "pages-index.json").read_text(encoding="utf-8"))["pages"]

    payload = {
        "book_only": True,
        "book": {
            "id": book["fp12"],
            "title": title or book.get("title") or book["fp12"],
            "subtitle": subject or book.get("subject") or "",
            "visibility": "private",
            "scene_mode": "image",
            "fp12": book["fp12"],
            "page_count": book["page_count"],
            # agent 识别书名时一起写进来的领域 / 水平 / 标签
            "domain": book.get("domain") or "",
            "level": book.get("level") or "",
            "tags": book.get("tags") or [],
        },
        "libraries": [],
        "cards": [],
        "toc": {
            "source": toc.get("source") or "",
            "offset": toc.get("offset"),
            "entries": toc.get("entries") or [],
            # 目录所在的页：优先看工作目录里看图 v1 记下的 tocv.json
            "pages": toc.get("pages") or _tocv_pages(book_dir),
        },
        "pages": pages,
    }
    html = render_html(payload)
    (out / "index.html").write_text(html, encoding="utf-8")
    print(f"确认页：{out / 'index.html'}（{len(html) // 1024} KB，{len(pages)} 页，"
          f"{len(payload['toc']['entries'])} 条目录，offset={payload['toc']['offset']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
