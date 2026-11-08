"""bench-v1 金标准人工抽检页（``docs/bench-v1.md`` §2 第 5 条）的测试。

不读真书：``tmp_path`` 里造一本小书（按层铺开页数），跑完整的选页 → 重建 → 打包
→ 出 HTML → 出选页清单。覆盖几条容易出错的口径：

- 每本书正好 6 页（练习 1 / 注释多 1 / 正文 4），目录页不进；
- 同样的输入跑两次结果一样，选页顺序和 sha1 排序一致；
- 一页切成多块时，所有块都进 ``segments.jsonl``；
- ``text_sha1`` 对不上、gold span 和原文对不上，一律报错停下；
- ``labels.jsonl`` 的 spans 和 ``gold.jsonl`` 一致；没有金标准词的页也有一行
  （``spans: []``）；
- ``audit_pages.json`` 里不含书页文字。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from kwtrain import bench_audit, bench_gold

# --------------------------------------------------------------------------- 假书

FP12 = "abcdef123456"
FP = FP12 + "0" * 28
TITLE = "测试书"

#: 每一层的页（页号 → 层）。练习 2 页、注释多 2 页、正文 6 页，够抽 1 / 1 / 4。
STRATUM_BY_PAGE: dict[int, str] = {
    1: "toc",
    2: "exercise",
    3: "exercise",
    4: "notes",
    5: "notes",
    6: "body",
    7: "body",
    8: "body",
    9: "body",
    10: "body",
    11: "body",
}

#: 正文页（会切成两块）。
BODY_PAGES = [page for page, stratum in STRATUM_BY_PAGE.items() if stratum != "toc"]


def _filler() -> str:
    return "测试正文内容" * 150


def _page_text(page: int) -> str:
    """一页正文：两段，长到会被切成两块。"""
    return f"第{page}页第一段。{_filler()}\n第{page}页第二段。{_filler()}"


def _chunk_texts(page: int) -> list[str]:
    """这一页切出来的两块（顺序和 ``split_into_chunks`` 一致）。"""
    return [f"第{page}页第一段。{_filler()}", f"第{page}页第二段。{_filler()}"]


def _toc_text() -> str:
    return "目 录\n第一章 走进化学世界\n第二章 我们周围的空气"


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _sort_key(fp12: str, page: int) -> str:
    """抽检排序键（照口径独立写一遍，不调模块里的函数）。"""
    return hashlib.sha1(f"bench-v1:audit:{fp12}:{page}".encode()).hexdigest()


def _seg_ids(page: int) -> list[str]:
    if STRATUM_BY_PAGE[page] == "toc":
        return [f"book:{FP12}:p{page:04d}"]
    return [f"book:{FP12}:p{page:04d}c{chunk}" for chunk in (1, 2)]


def _expected_selection() -> list[tuple[int, str]]:
    """按口径独立算出该抽哪些页（层内按 sha1 从小到大取）。"""
    out: list[tuple[int, str]] = []
    for stratum, count in bench_audit.AUDIT_QUOTA.items():
        pages = [page for page, s in STRATUM_BY_PAGE.items() if s == stratum]
        pages.sort(key=lambda page: _sort_key(FP12, page))
        out.extend((page, stratum) for page in pages[:count])
    return out


#: 金标准留空的页（抽检页里挑一页，验证「没有金标准词的页照样进」）。
EMPTY_PAGE = _expected_selection()[0][0]


def _selected_seg_ids() -> list[str]:
    """按口径算出的该抽的段 id。"""
    out: list[str] = []
    for page, _stratum in _expected_selection():
        out.extend(_seg_ids(page))
    return out


def _write_book(data_root: Path) -> Path:
    book_dir = data_root / FP12
    book_dir.mkdir(parents=True, exist_ok=True)
    (book_dir / "book.json").write_text(
        json.dumps(
            {
                "fp": FP,
                "fp12": FP12,
                "title": TITLE,
                "subject": "历史",
                "language": "zh",
                "page_count": len(STRATUM_BY_PAGE),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    pages = []
    for page in sorted(STRATUM_BY_PAGE):
        text = _toc_text() if STRATUM_BY_PAGE[page] == "toc" else _page_text(page)
        pages.append(
            {
                "page": page,
                "printed_page": str(page),
                "text": text,
                "text_sha1": _sha1(text),
                "kind": "toc" if STRATUM_BY_PAGE[page] == "toc" else "body",
                "width": 595.0,
                "height": 841.0,
            }
        )
    (book_dir / "pages.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in pages) + "\n",
        encoding="utf-8",
    )
    (book_dir / "sections.json").write_text(
        json.dumps(
            [
                {
                    "sid": "s01",
                    "title": "第一章",
                    "level": 1,
                    "page_start": min(BODY_PAGES),
                    "page_end": max(BODY_PAGES),
                    "sort_order": 1,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return book_dir


def _page_sha1(page: int) -> str:
    if STRATUM_BY_PAGE[page] == "toc":
        return _sha1(_toc_text())
    return _sha1(_page_text(page))


def _write_pages(pages_path: Path, *, sha1_override: dict[int, str] | None = None) -> Path:
    overrides = sha1_override or {}
    payload = {
        "version": "bench-v1",
        "books": [
            {
                "fp": FP,
                "fp12": FP12,
                "title": TITLE,
                "pages": [
                    {
                        "page": page,
                        "stratum": STRATUM_BY_PAGE[page],
                        "kind": "toc" if STRATUM_BY_PAGE[page] == "toc" else "body",
                        "text_sha1": overrides.get(page, _page_sha1(page)),
                        "seg_ids": _seg_ids(page),
                    }
                    for page in sorted(STRATUM_BY_PAGE)
                ],
            }
        ],
    }
    pages_path.parent.mkdir(parents=True, exist_ok=True)
    pages_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return pages_path


def _gold_row(page: int, chunk: int) -> dict[str, Any]:
    text = _chunk_texts(page)[chunk - 1]
    surface = f"第{page}页"
    spans: list[dict[str, Any]] = []
    if page != EMPTY_PAGE:
        spans.append({"start": 0, "end": len(surface), "surface": surface, "type": "term"})
    return {
        "seg_id": f"book:{FP12}:p{page:04d}c{chunk}",
        "fp12": FP12,
        "page": page,
        "chunk": chunk,
        "stratum": STRATUM_BY_PAGE[page],
        "text_sha1": _sha1(text),
        "spans": spans,
    }


def _write_gold(gold_path: Path) -> Path:
    rows = [_gold_row(page, chunk) for page in BODY_PAGES for chunk in (1, 2)]
    gold_path.parent.mkdir(parents=True, exist_ok=True)
    gold_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return gold_path


def _setup(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "data": tmp_path / "data",
        "pages": tmp_path / "pages.json",
        "gold": tmp_path / "gold.jsonl",
        "out": tmp_path / "out",
        "list": tmp_path / "audit_pages.json",
    }
    _write_book(paths["data"])
    _write_pages(paths["pages"])
    _write_gold(paths["gold"])
    return paths


def _run(paths: dict[str, Path]) -> dict[str, Any]:
    return bench_audit.build_audit(
        paths["pages"],
        paths["gold"],
        paths["data"],
        paths["out"],
        paths["list"],
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# --------------------------------------------------------------------------- 测试


def test_每本书正好六页且分层是1_1_4(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    summary = _run(paths)
    assert summary["pages"] == 6
    payload = json.loads(paths["list"].read_text(encoding="utf-8"))
    assert len(payload["books"]) == 1
    book = payload["books"][0]
    assert book["fp12"] == FP12
    strata = [row["stratum"] for row in book["pages"]]
    assert strata.count("exercise") == 1
    assert strata.count("notes") == 1
    assert strata.count("body") == 4
    assert "toc" not in strata
    # 目录页没被抽中。
    assert all(row["page"] != 1 for row in book["pages"])


def test_选页顺序和sha1排序一致(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    _run(paths)
    payload = json.loads(paths["list"].read_text(encoding="utf-8"))
    book = payload["books"][0]
    assert [(row["page"], row["stratum"]) for row in book["pages"]] == _expected_selection()
    for row in book["pages"]:
        assert row["sort_key"] == _sort_key(FP12, row["page"])
    # 层内 sha1 从小到大。
    for stratum in bench_audit.AUDIT_QUOTA:
        keys = [row["sort_key"] for row in book["pages"] if row["stratum"] == stratum]
        assert keys == sorted(keys)


def test_同样的输入跑两次结果一样(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    _run(paths)
    first_list = json.loads(paths["list"].read_text(encoding="utf-8"))
    first_segments = _read_jsonl(paths["out"] / "segments.jsonl")
    first_labels = _read_jsonl(paths["out"] / "labels.jsonl")

    paths2 = dict(paths)
    paths2["out"] = tmp_path / "out2"
    paths2["list"] = tmp_path / "audit_pages2.json"
    _run(paths2)

    second_list = json.loads(paths2["list"].read_text(encoding="utf-8"))
    second_segments = _read_jsonl(paths2["out"] / "segments.jsonl")
    second_labels = _read_jsonl(paths2["out"] / "labels.jsonl")

    for payload in (first_list, second_list):
        payload.pop("generated_at", None)
    for rows in (first_labels, second_labels):
        for row in rows:
            row.pop("created_at", None)
    assert first_list == second_list
    assert first_segments == second_segments
    assert first_labels == second_labels


def test_一页多块时所有块都进segments(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    summary = _run(paths)
    segments = _read_jsonl(paths["out"] / "segments.jsonl")
    seg_ids = {row["seg_id"] for row in segments}
    payload = json.loads(paths["list"].read_text(encoding="utf-8"))
    for book in payload["books"]:
        for row in book["pages"]:
            assert row["chunks"] == len(row["seg_ids"])
            for seg_id in row["seg_ids"]:
                assert seg_id in seg_ids
    # 抽中的每一页都被切成了两块，所以段数多于页数。
    assert summary["segments"] == 12
    assert summary["segments"] > summary["pages"]
    # 同一页的块按块号递增。
    by_page: dict[int, list[int]] = {}
    for row in segments:
        by_page.setdefault(row["source"]["page"], []).append(row["source"]["chunk"])
    assert all(chunks == sorted(chunks) for chunks in by_page.values())
    assert all(len(chunks) == 2 for chunks in by_page.values())


def test_text_sha1对不上时报错停下(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    _write_pages(paths["pages"], sha1_override={2: "0" * 40})
    with pytest.raises(bench_gold.BenchGoldError):
        _run(paths)


def test_gold的text_sha1对不上时报错停下(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    rows = _read_jsonl(paths["gold"])
    target = _selected_seg_ids()[0]
    for row in rows:
        if row["seg_id"] == target:
            row["text_sha1"] = "0" * 40
    paths["gold"].write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(bench_audit.BenchAuditError):
        _run(paths)


def test_gold的span和原文对不上时报错停下(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    rows = _read_jsonl(paths["gold"])
    selected = set(_selected_seg_ids())
    for row in rows:
        if row["seg_id"] in selected and row["spans"]:
            row["spans"][0]["end"] = row["spans"][0]["start"] + 99
            break
    else:  # pragma: no cover - 假数据里一定有带词的抽检段
        raise AssertionError("没有带词的抽检段")
    paths["gold"].write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(bench_audit.BenchAuditError):
        _run(paths)


def test_labels的spans和gold一致(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    summary = _run(paths)
    gold = {row["seg_id"]: row for row in _read_jsonl(paths["gold"])}
    labels = _read_jsonl(paths["out"] / "labels.jsonl")
    selected = _selected_seg_ids()
    assert len(labels) == len(selected)
    assert {row["seg_id"] for row in labels} == set(selected)
    for row in labels:
        gold_row = gold[row["seg_id"]]
        assert row["text_sha1"] == gold_row["text_sha1"]
        assert row["labeler"] == "bench:v1:gold"
        assert row["scope"] == "full"
        assert row["spans"] == [
            {
                "start": span["start"],
                "end": span["end"],
                "surface": span["surface"],
                "type": span["type"],
            }
            for span in gold_row["spans"]
        ]
    assert summary["spans"] == sum(len(gold[seg_id]["spans"]) for seg_id in selected)


def test_没有金标准词的页也有一行(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    _run(paths)
    labels = {row["seg_id"]: row for row in _read_jsonl(paths["out"] / "labels.jsonl")}
    empty_segs = [f"book:{FP12}:p{EMPTY_PAGE:04d}c{chunk}" for chunk in (1, 2)]
    for seg_id in empty_segs:
        assert seg_id in labels
        assert labels[seg_id]["spans"] == []


def test_audit_pages里不含书页文字(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    _run(paths)
    raw = paths["list"].read_text(encoding="utf-8")
    assert "测试正文内容" not in raw
    assert "第一段" not in raw
    for page in STRATUM_BY_PAGE:
        assert f"第{page}页第一段" not in raw
    payload = json.loads(raw)
    for book in payload["books"]:
        for row in book["pages"]:
            assert "text" not in row
            assert set(row) == {
                "page",
                "stratum",
                "seg_ids",
                "chunks",
                "gold_words",
                "sort_key",
            }


def test_HTML是自包含的且段数span数对得上(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    summary = _run(paths)
    html_path = paths["out"] / "bench-v1-audit.html"
    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")
    assert "<script" in html and "kwtrain-data" in html
    assert "http://" not in html.replace("http://www.w3.org", "")
    assert summary["html_bytes"] == html_path.stat().st_size
    segments = _read_jsonl(paths["out"] / "segments.jsonl")
    labels = _read_jsonl(paths["out"] / "labels.jsonl")
    assert summary["segments"] == len(segments)
    assert summary["spans"] == sum(len(row["spans"]) for row in labels)
    # 段落文件里每段都带 book_fp / page / chunk / stratum / text_origin。
    for row in segments:
        assert row["domain"] == "book"
        assert row["lang"] in ("zh", "en")
        assert row["source"]["text_origin"] == "pdf_text"
        assert row["source"]["book_fp"] == FP12
        assert "chunk" in row["source"] and "stratum" in row["source"]
