"""卡包 manifest → 静态页内嵌数据。

口径逐条抄自引擎的 ``src/bookcard/browse/db.py``：

- **卡 id**（db.py:513-523）：``source.card_id`` → ``card.card_id`` →
  ``<library_id>:sha1(front)[:8]``。真实包里前两个都没有，实际总是第三种。
- **场景**（db.py:196-266）：
  - 公开包一律没有场景；
  - ``source.context`` 在 ⇒ 文本场景；
  - 否则 ``card.images[0]`` 在 ⇒ 图片场景；
  - 都没有 ⇒ 没有场景。
- **书 id**（db.py:341）：``book.fp12``，缺失则 ``sha1(category.name)[:12]``。
- **词库 id**（db.py:362）：``library.slug``，缺失则 ``<book_id>-lib<NN>``。

跟引擎的差别只有一处：**图片路径是相对路径**（``images/p0005.webp``），
静态页在 ``file://`` 下打开，相对路径才认得出来。
"""

from __future__ import annotations

import hashlib
from typing import Any

#: 引擎里 db.py 的常量。
MAX_FRONT_LEN = 500


def _sha1_hex(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _book_id(manifest: dict) -> str:
    book = manifest.get("book") or {}
    fp12 = book.get("fp12")
    if fp12:
        return str(fp12)
    category = manifest.get("category") or {}
    return _sha1_hex(str(category.get("name") or ""))[:12]


def _library_id(book_id: str, library: dict, index: int) -> str:
    slug = library.get("slug")
    if slug:
        return str(slug)
    return f"{book_id}-lib{index:02d}"


def _card_id(library_id: str, card: dict, source: Any) -> str:
    """db.py:513-523 的逐条口径。"""
    if isinstance(source, dict):
        candidate = source.get("card_id")
        if candidate:
            return str(candidate)
    candidate = card.get("card_id")
    if candidate:
        return str(candidate)
    return f"{library_id}:{_sha1_hex(str(card.get('front') or ''))[:8]}"


def _build_scene(
    *,
    title: str,
    book_id: str,
    visibility: str,
    card: dict,
    image_map: dict[str, str],
) -> dict | None:
    """db.py:196-266 的口径，只是 ``src`` 换成包内相对路径。"""
    if visibility == "public":
        return None

    source = card.get("source") or {}
    if not isinstance(source, dict):
        source = {}

    context = source.get("context")
    if isinstance(context, dict) and context.get("text"):
        highlights = context.get("highlights")
        if not isinstance(highlights, list):
            highlights = []
        return {
            "kind": "text",
            "book": title,
            "page": source.get("page"),
            "printed_page": source.get("printed_page"),
            "text": context.get("text"),
            "highlights": highlights,
        }

    images = card.get("images") or []
    if not images:
        return None
    image = images[0]
    if not isinstance(image, dict):
        return None
    file_name = str(image.get("file") or "")
    if not file_name:
        return None

    occurrences = image.get("occurrences") or []
    boxes: list[dict] = []
    page = source.get("page")
    for occurrence in occurrences:
        if not isinstance(occurrence, dict):
            continue
        boxes.append(
            {
                "x": occurrence.get("x"),
                "y": occurrence.get("y"),
                "w": occurrence.get("w"),
                "h": occurrence.get("h"),
            }
        )
    if occurrences and isinstance(occurrences[0], dict):
        page = occurrences[0].get("page", page)

    # 包内相对路径：静态页在 file:// 下打开，只认相对路径。
    src = image_map.get(file_name, file_name)

    return {
        "kind": "image",
        "book": title,
        "page": page,
        "src": src,
        "width": image.get("width"),
        "height": image.get("height"),
        "boxes": boxes,
    }


def build_payload(
    manifest: dict,
    image_map: dict[str, str] | None = None,
    *,
    toc: dict | None = None,
    pages: list[dict] | None = None,
) -> dict:
    """manifest → 静态页内嵌数据。

    ``image_map`` 是「包内原路径 → 包内新路径」的映射（jpg → webp）。
    ``toc`` 是目录（``{"source","offset","entries"}``）。
    ``pages`` 是全书每页的图（``[{"page","thumb","image","width","height"}]``）。
    """
    image_map = image_map or {}
    category = manifest.get("category") or {}
    book = manifest.get("book") or {}
    visibility = str(manifest.get("visibility") or "private")

    book_id = _book_id(manifest)
    title = str(book.get("title") or category.get("name") or book_id)
    subtitle = str(category.get("description") or "")
    scene_mode = str(manifest.get("scene_mode") or "")

    libraries: list[dict] = []
    cards: list[dict] = []

    for index, library in enumerate(category.get("libraries") or [], start=1):
        lib_id = _library_id(book_id, library, index)
        lib_name = str(library.get("name") or lib_id)
        lib_cards = library.get("cards") or []
        libraries.append(
            {
                "id": lib_id,
                "name": lib_name,
                "sort_order": int(library.get("sort_order") or index),
                "book_id": book_id,
                "count": len(lib_cards),
            }
        )
        for card in lib_cards:
            front = str(card.get("front") or "")
            if not front:
                continue
            source = card.get("source")
            tags = card.get("tags")
            cards.append(
                {
                    "id": _card_id(lib_id, card, source),
                    "library_id": lib_id,
                    "front": front,
                    "back": str(card.get("back") or ""),
                    "tags": [str(t) for t in tags] if isinstance(tags, list) else [],
                    "sort_order": int(card.get("sort_order") or 0),
                    "scene": _build_scene(
                        title=title,
                        book_id=book_id,
                        visibility=visibility,
                        card=card,
                        image_map=image_map,
                    ),
                }
            )

    if not scene_mode:
        kinds = {c["scene"]["kind"] for c in cards if c["scene"]}
        scene_mode = "image" if "image" in kinds else ("text" if "text" in kinds else "none")

    return {
        "book": {
            "id": book_id,
            "title": title,
            "subtitle": subtitle,
            "visibility": visibility,
            "scene_mode": scene_mode,
            "fp12": str(book.get("fp12") or book_id),
            "page_count": book.get("page_count"),
        },
        "libraries": libraries,
        "cards": cards,
        "toc": toc,
        "pages": pages or [],
    }
