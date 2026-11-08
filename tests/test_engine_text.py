"""``bookcard text`` 的测试：写 ``breaks``，且 ``text`` / ``text_sha1`` 不变。

契约 §2.1：重跑 ``bookcard text`` 只会加 ``breaks``，``text`` 和 ``text_sha1``
必须逐字不变；不一致即失败。
"""

import json

import pytest

from bookcard.engine.text import TextChangedError, build_text
from pipeline_fixtures import make_pdf


def _rows(work_dir):
    path = work_dir / "pages.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_text_writes_breaks_for_every_page(tmp_path):
    """每一页都带 ``breaks`` 字段。"""
    pdf = make_pdf(tmp_path / "book.pdf")

    build_text(pdf, tmp_path / "out")

    rows = _rows(tmp_path / "out")
    assert rows
    assert all("breaks" in row for row in rows)
    assert all(isinstance(row["breaks"], list) for row in rows)


def test_text_is_identical_when_rerun(tmp_path):
    """重跑一次：``text`` 和 ``text_sha1`` 逐字不变。"""
    pdf = make_pdf(tmp_path / "book.pdf")
    build_text(pdf, tmp_path / "out")
    before = [(row["text"], row["text_sha1"]) for row in _rows(tmp_path / "out")]

    build_text(pdf, tmp_path / "out")
    after = [(row["text"], row["text_sha1"]) for row in _rows(tmp_path / "out")]

    assert before == after


def test_text_fails_when_the_page_text_changed(tmp_path):
    """页文字变了 ⇒ 直接失败（契约 §2.1），不写坏产物。"""
    pdf = make_pdf(tmp_path / "book.pdf")
    work = tmp_path / "out"
    build_text(pdf, work)
    rows = _rows(work)
    rows[0]["text"] = "这一页的文字被人改过了"
    (work / "pages.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    with pytest.raises(TextChangedError):
        build_text(pdf, work)


def test_text_keeps_the_chosen_scene_mode(tmp_path):
    """重跑 ``text`` 不许抹掉 ``book.json`` 里选定的 ``scene_mode``。"""
    pdf = make_pdf(tmp_path / "book.pdf")
    work = tmp_path / "out"
    build_text(pdf, work)
    book = json.loads((work / "book.json").read_text(encoding="utf-8"))
    book["scene_mode"] = "text"
    (work / "book.json").write_text(
        json.dumps(book, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    build_text(pdf, work)

    assert json.loads((work / "book.json").read_text(encoding="utf-8"))["scene_mode"] == "text"


def test_text_keeps_the_book_metadata_when_the_source_is_missing(tmp_path):
    """这次拿不到元数据（worktree 里没有 ``books.jsonl``）⇒ 不许把书名洗掉。"""
    pdf = make_pdf(tmp_path / "book.pdf")
    work = tmp_path / "out"
    build_text(pdf, work)
    book = json.loads((work / "book.json").read_text(encoding="utf-8"))
    book["title"] = "示例教科书"
    book["nas_path"] = "~/books/示例/示例教科书.pdf"
    (work / "book.json").write_text(
        json.dumps(book, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    build_text(pdf, work)

    updated = json.loads((work / "book.json").read_text(encoding="utf-8"))
    assert updated["title"] == "示例教科书"
    assert updated["nas_path"] == "~/books/示例/示例教科书.pdf"


def test_text_keeps_the_paper_fields(tmp_path):
    """论文源：重跑 ``text`` 不许把 arxiv fetch 写的许可、出处、编号洗掉。"""
    pdf = make_pdf(tmp_path / "book.pdf")
    work = tmp_path / "out"
    work.mkdir()
    (work / "book.json").write_text(
        json.dumps(
            {
                "type": "paper",
                "fp": "a" * 40,
                "fp12": "a" * 12,
                "title": "Attention Is All You Need",
                "authors": ["Ashish Vaswani"],
                "arxiv_id": "1706.03762v7",
                "arxiv_version": 7,
                "paper_license": "arxiv-nonexclusive-1.0",
                "paper_license_uri": "http://arxiv.org/licenses/nonexclusive-distrib/1.0/",
                "attribution": 'Vaswani et al. "Attention Is All You Need". arXiv:1706.03762v7.',
                "fingerprint": {"pages": 15, "pdf_sha256": "b" * 64},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    build_text(pdf, work)

    book = json.loads((work / "book.json").read_text(encoding="utf-8"))
    assert book["type"] == "paper"
    assert book["arxiv_id"] == "1706.03762v7"
    assert book["paper_license"] == "arxiv-nonexclusive-1.0"
    assert book["attribution"].startswith("Vaswani et al.")
    assert book["authors"] == ["Ashish Vaswani"]
    # 身份跟着 arxiv fetch 给的走（工作目录名、卡包名都靠它）。
    assert book["fp12"] == "a" * 12
    # 指纹是书源那套（页文本 hash），另附 PDF 的 sha256。
    assert book["fingerprint"]["page_text_sha1"]
    assert book["fingerprint"]["pdf_sha256"] == "b" * 64
