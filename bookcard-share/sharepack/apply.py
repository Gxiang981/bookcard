"""把静态页导出的 ``edits.json`` 合回卡包。

静态页改的是浏览器里的数据，改不动 zip 里的文件（本子项目也不打算让浏览器
去改自己的文件）。所以回��走一条显式的往返：

    静态页 ──导出──▶ edits.json ──本模块──▶ 新 manifest ──▶ 新分享包 ──▶ App

合回的口径：

- 只改 ``front`` / ``back`` / ``tags`` / ``library_id``（跟 App 的改卡口径一致）。
- **不动** ``source``（页码、bbox、依据句）—— 改词不改它在书上的位置。
- 改过 ``front`` 的卡，id 会变（id = ``<词库>:<sha1(front)[:8]>``）。所以给
  这些卡钉一个 ``card_id`` 字段，把 id 固定住 —— 引擎的 id 推导
  （``db.py:513-523``）本来就优先认 ``card.card_id``，App 那边不认这个字段
  （pydantic 多余字段直接丢），互不影响。
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any


def _sha1_hex(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _card_id(library_id: str, card: dict, source: Any) -> str:
    """引擎的 id 推导（db.py:513-523）。"""
    if isinstance(source, dict):
        candidate = source.get("card_id")
        if candidate:
            return str(candidate)
    candidate = card.get("card_id")
    if candidate:
        return str(candidate)
    return f"{library_id}:{_sha1_hex(str(card.get('front') or ''))[:8]}"


def apply_edits(manifest: dict, edits: dict) -> tuple[dict, dict]:
    """把 ``edits`` 合进 ``manifest``。

    返回 ``(新 manifest, 统计)``。
    """
    if not isinstance(edits, dict):
        raise ValueError("edits 必须是一个 JSON 对象")
    fmt = str(edits.get("format") or "")
    if fmt and not fmt.startswith("bookcard-share/edits"):
        raise ValueError(f"不是 bookcard-share 的改动文件：{fmt}")

    out = copy.deepcopy(manifest)
    libraries = (out.get("category") or {}).get("libraries") or []

    # 先按 id 找卡：id 是从 manifest 现算的（front 改了 id 就变了，
    # 所以下面要边算边比）。
    index: dict[str, tuple[dict, dict]] = {}   # card_id → (library, card)
    for library in libraries:
        for card in library.get("cards") or []:
            lib_key = str(library.get("slug") or library.get("name") or "")
            cid = _card_id(lib_key, card, card.get("source"))
            index[cid] = (library, card)

    stats = {"applied": 0, "skipped": 0, "moved": 0}

    # 目录（TOC）：整段替换。目录不是卡，是一本书的结构，合回时整体覆盖。
    toc = edits.get("toc")
    if isinstance(toc, dict):
        entries = []
        for raw in toc.get("entries") or []:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            if not title:
                continue
            page = raw.get("page")
            try:
                page = int(page) if page is not None else None
            except (TypeError, ValueError):
                page = None
            try:
                level = int(raw.get("level") or 1)
            except (TypeError, ValueError):
                level = 1
            entries.append({"level": level, "title": title, "page": page})
        offset = toc.get("offset")
        try:
            offset = int(offset) if offset is not None else None
        except (TypeError, ValueError):
            offset = None
        previous = out.get("toc") if isinstance(out.get("toc"), dict) else {}
        out["toc"] = {
            "source": str(toc.get("source") or previous.get("source") or ""),
            "offset": offset,
            "entries": entries,
        }
        stats["toc"] = len(entries)

    for ov in edits.get("cards") or []:
        if not isinstance(ov, dict):
            continue
        cid = str(ov.get("id") or "")
        hit = index.get(cid)
        if hit is None:
            stats["skipped"] += 1
            continue
        library, card = hit

        # 改 front 前先钉住 id —— 否则改完 front，id 就变了。
        card.setdefault("card_id", cid)

        if isinstance(ov.get("front"), str):
            card["front"] = ov["front"]
        if isinstance(ov.get("back"), str):
            card["back"] = ov["back"]
        if isinstance(ov.get("tags"), list):
            card["tags"] = [str(t) for t in ov["tags"]]

        target_lib = ov.get("library_id")
        if isinstance(target_lib, str) and target_lib and target_lib != library.get("slug"):
            dest = next(
                (lib for lib in libraries if lib.get("slug") == target_lib),
                None,
            )
            if dest is not None and dest is not library:
                library.get("cards").remove(card)
                dest.setdefault("cards", []).append(card)
                stats["moved"] += 1

        stats["applied"] += 1

    return out, stats
