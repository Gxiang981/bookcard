"""读书的目录（TOC）和印刷页 offset。

数据来自引擎的**书工作目录**（``$BOOKCARD_HOME/books/<fp12>/``）：

- ``toc.json``   —— 目录条目：``{"level":1, "title":"…", "page":9}``
- ``sections.json`` —— 切出来的词库（``page_start`` / ``page_end``）
- ``pages.jsonl`` —— 每页的 ``printed_page``（印刷页号；扫描件常常探不到）

**offset 是什么**：印刷页号 = PDF 页号 − offset，**offset 是前置页数（正数）**。引擎在
``src/bookcard/engine/analysis.py`` 里算它（``_printed_offset`` /
``_header_page_offset``）。扫描件没有页眉页脚可认时算不出来（这本是 null），
所以界面上要能让人手填。

**目录要可编辑**：编辑结果存在分享包的 manifest 里（``manifest.toc``），
App 不认这个字段（pydantic 多余字段直接丢），但分享包自己带着，
``build.py apply`` 能把它合回去。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def detect_offset(work_dir: str | Path | None) -> int | None:
    """从 ``pages.jsonl`` 推 offset（前置页数 = PDF 页 − 印刷页，正数）。

    取出现次数最多的那个差值；探不到就是 ``None``。
    """
    if not work_dir:
        return None
    pages_file = Path(work_dir) / "pages.jsonl"
    if not pages_file.is_file():
        return None
    diffs: Counter[int] = Counter()
    try:
        with pages_file.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                printed = row.get("printed_page")
                number = row.get("page")
                if printed is None or number is None:
                    continue
                try:
                    diffs[abs(int(number) - int(printed))] += 1
                except (TypeError, ValueError):
                    continue
    except OSError:
        return None
    if not diffs:
        return None
    return diffs.most_common(1)[0][0]


def _toc_pages(work_dir: str | Path | None) -> list[int]:
    """目录所在的物理页（看图 v1 的 locate 记在 tocv.json 里）。"""
    if not work_dir:
        return []
    path = Path(work_dir) / "tocv.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    pages = data.get("toc_pages")
    if not isinstance(pages, list):
        return []
    out = []
    for n in pages:
        try:
            out.append(int(n))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def load_toc(work_dir: str | Path | None) -> dict | None:
    """读目录。

    返回 ``{"source","offset","entries"}``；读不到返回 ``None``。
    """
    if not work_dir:
        return None
    work = Path(work_dir)
    toc = _read_json(work / "toc.json")
    if not isinstance(toc, dict):
        return None
    entries = []
    for raw in toc.get("entries") or []:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        try:
            page = int(raw.get("page"))
        except (TypeError, ValueError):
            continue
        try:
            level = int(raw.get("level") or 1)
        except (TypeError, ValueError):
            level = 1
        item = {"level": level, "title": title, "page": page}
        # 「目录看图 v1」的条目自带印刷页（printed_page）；带上它，
        # 页面上的「印刷 N」就是书上印的那个数，不用靠 offset 换算。
        printed = raw.get("printed_page")
        if printed is not None:
            try:
                item["printed_page"] = int(printed)
            except (TypeError, ValueError):
                pass
        entries.append(item)

    # offset 优先用 toc.json 里写好的（目录看图 v1 从草稿的印刷页算出来的），
    # 没有才从 pages.jsonl 的「印刷页 − PDF 页」里探测。
    offset = None
    raw_offset = toc.get("offset")
    if raw_offset not in (None, ""):
        try:
            offset = int(raw_offset)
        except (TypeError, ValueError):
            offset = None
    if offset is None:
        diffs = [
            abs(e["page"] - e["printed_page"])
            for e in entries
            if e.get("printed_page") is not None
        ]
        if diffs:
            offset = max(set(diffs), key=diffs.count)
    if offset is None:
        offset = detect_offset(work)
    return {
        "source": str(toc.get("source") or ""),
        "offset": offset,
        "entries": entries,
        # 目录所在的页 —— 左栏「目录」那一行显示并跳过去。
        # toc.json 里写了就用它（引擎记的），没写才去看工作目录的 tocv.json。
        "pages": toc.get("pages") or _toc_pages(work),
    }


def toc_from_libraries(libraries: list[dict]) -> dict | None:
    """没有 ``toc.json`` 时的兜底：用词库当目录。

    词库没有页码（manifest 里没有），所以这一路的页号是空的 —— 界面上仍然
    可以编辑、可以补页码。
    """
    entries = []
    for lib in libraries:
        name = str(lib.get("name") or "").strip()
        if not name:
            continue
        entries.append({"level": 1, "title": name, "page": None})
    if not entries:
        return None
    return {"source": "libraries", "offset": None, "entries": entries}


def _vision_toc_pages(toc_dir: str | Path) -> list[int]:
    """看图 v1 的工作目录里，locate 记下的目录页。"""
    path = Path(toc_dir) / "tocv.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for n in data.get("toc_pages") or []:
        try:
            out.append(int(n))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def load_toc_vision(path: str | Path) -> dict | None:
    """读「目录看图 v1」（``bookcard tocv``）的产出。

    ``path`` 可以是：

    - **tocv 工作目录**（里面有 ``toc.json`` + ``tocv.json``）—— 最好，
      ``tocv.json`` 的 ``last_draft`` 里同时有**印刷页**和**物理页**，
      印刷页号（offset）能直接算出来；
    - 直接给 ``toc.json``（只有物理页，offset 算不出来）。

    目录看图 v1 的产出比规则解析好得多：标题是照目录页抄的，层级和页码
    都准（引擎会跑 C1–C5 五条判据，不过就 ``retry``）。
    """
    p = Path(path)
    toc_file = (p / "toc.json") if p.is_dir() else p
    if not toc_file.is_file():
        return None
    toc = _read_json(toc_file)
    if not isinstance(toc, dict):
        return None

    def _norm(raw: dict) -> dict | None:
        title = str(raw.get("title") or "").strip()
        if not title:
            return None
        try:
            page = int(raw.get("page"))
        except (TypeError, ValueError):
            return None
        try:
            level = int(raw.get("level") or 1)
        except (TypeError, ValueError):
            level = 1
        printed = raw.get("printed_page")
        try:
            printed = int(printed) if printed is not None else None
        except (TypeError, ValueError):
            printed = None
        item = {"level": level, "title": title, "page": page}
        if printed is not None:
            item["printed_page"] = printed
        return item

    entries = [e for e in (_norm(raw) for raw in toc.get("entries") or []) if e]

    # 草稿（last_draft）里带印刷页 → 能算 offset，也能把印刷页贴回条目
    state = _read_json(p / "tocv.json") if p.is_dir() else None
    if isinstance(state, dict):
        draft = state.get("last_draft") or {}
        drafted = [e for e in (_norm(raw) for raw in draft.get("entries") or []) if e]
        if len(drafted) == len(entries) and drafted:
            for base, extra in zip(entries, drafted, strict=True):
                if base["title"] == extra["title"] and "printed_page" in extra:
                    base["printed_page"] = extra["printed_page"]

    offsets = [
        e["page"] - e["printed_page"]
        for e in entries
        if e.get("printed_page") is not None
    ]
    offset = None
    if offsets:
        offset = max(set(offsets), key=offsets.count)   # 众数

    return {
        "source": str(toc.get("source") or "vision"),
        "offset": offset,
        "entries": entries,
        # 目录所在的页（locate 记下的）—— 左栏「目录」那一行显示并跳过去
        "pages": _vision_toc_pages(path),
    }
