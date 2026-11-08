"""论文源的许可记录 + 公开包的照抄检查（2026-11-08 两次拍板）。

**现行政策**（第二次拍板：「和书一样处理」）：

- 论文源与书源**同规则**：公开包只放词汇 + LLM 解释（第一句）+ 出处，
  **不带原文逐字内容、不带图**；私有包照旧带页面图、证据句和上下文；
- **许可只记录、不裁剪**（``manifest.source_license`` / ``manifest.attribution``）；
- 公开包**唯一多出来的一条硬检查**：解释**不得大段照抄原文**
  （公共子串 ≥ 60 字符（纯拉丁文）或 ≥ 30 字符（含中文）⇒ 这张卡不进公开包）。

打包行为的夹具照 ``tests/test_pipeline_pack.py`` 的写法
（``write_workdir`` / ``make_pdf`` / ``select`` / ``locate`` / ``audit``），
App 兼容校验直接复用那份 ``BulkImportBody``。
"""

import json
import zipfile

import pytest

from bookcard.pipeline.audit import audit
from bookcard.pipeline.license_gate import (
    LICENSE_KINDS,
    VERBATIM_RUN_CJK,
    VERBATIM_RUN_LATIN,
    attribution_ok,
    build_attribution,
    card_share,
    is_verbatim_copy,
    license_flags,
    license_kind_from_uri,
    max_common_run,
    public_policy,
    quote_within_limits,
)
from bookcard.pipeline.locate import locate
from bookcard.pipeline.pack import pack, read_pack_manifest
from bookcard.pipeline.select import select
from pipeline_fixtures import BOOK, candidate, make_pdf, page_rows, write_workdir
from test_pipeline_pack import BulkImportBody, _resolve_images_like_app

# ===================== 夹具 =====================

#: 术语卡（正面短、背面短 ⇒ 启发式判 term）。
TERM_CANDIDATE = candidate(1, "良渚文化", explanation="长江下游的新石器时代晚期文化。")

#: 结论复述卡（正面是一整句话 ⇒ 启发式判 summary）。
SUMMARY_CANDIDATE = candidate(
    2,
    "良渚文化的城址和水利系统展现了早期国家的形态。",
    explanation="早期国家形态的一段复述。",
)

#: 论文夹具的页原文（比默认的长，才够测照抄检查）。
PAPER_PAGES = {
    1: "良渚文化的城址和水利系统展现了早期国家的形态，是理解早期国家形成过程的关键材料。",
    2: "二里头遗址发现了宫殿建筑群和青铜器，被视为早期国家的都城遗址候选之一。",
    3: "The attention mechanism focuses on key parts.",
}

#: 解释整段照抄页原文（⇒ 公开包剔掉这张卡）。
VERBATIM_CANDIDATE = candidate(
    1,
    "良渚文化",
    explanation=PAPER_PAGES[1],
    texts=PAPER_PAGES,
)

#: 解释是自己写的，只是复述了结论（⇒ 公开包留着）。
QUOTE_CANDIDATE = candidate(
    2,
    "二里头遗址",
    explanation="二里头遗址的宫殿建筑群与青铜器，说明当时已有成规模的公共建筑与礼器生产。",
    texts=PAPER_PAGES,
)

#: 解释里引了一个英文术语（短引用 ⇒ 不算照抄）。
ATTENTION_CANDIDATE = candidate(
    3,
    "attention mechanism",
    explanation=(
        "注意力机制（attention mechanism）让模型在处理长序列时聚焦关键部分，"
        "是序列建模里最常用的结构之一。"
    ),
    texts=PAPER_PAGES,
)


def _paper_book(**overrides) -> dict:
    """论文源的 ``book.json``（带 arXiv 四要素 + 许可）。"""
    book = dict(BOOK)
    book.update(
        {
            "type": "paper",
            "title": "On Analytic Engines",
            "authors": ["Ada Lovelace"],
            "arxiv_id": "2401.12345",
            "arxiv_updated": "2024-01-05",
            "paper_license": "http://creativecommons.org/licenses/by/4.0/",
        }
    )
    book.update(overrides)
    return book


def _ready_workdir(
    tmp_path,
    *,
    name="book",
    book=None,
    candidates=None,
    pages=None,
    pdf_texts=None,
):
    """跑到「可以打包」的状态：挑卡 → 定位 → 质检（照 test_pipeline_pack 的写法）。"""
    candidates = candidates if candidates is not None else [TERM_CANDIDATE, SUMMARY_CANDIDATE]
    work = write_workdir(tmp_path / name, candidates=candidates, pages=pages, book=book)
    pdf = tmp_path / f"{name}.pdf"
    make_pdf(pdf, texts=pdf_texts)
    select(work)
    locate(work, pdf)
    audit(work)
    return work, pdf


def _ready_paper_workdir(tmp_path, *, name="paper", book=None):
    """论文源的工作目录（三张卡：一张照抄、一张复述、一张带短引用）。"""
    return _ready_workdir(
        tmp_path,
        name=name,
        book=book if book is not None else _paper_book(),
        candidates=[VERBATIM_CANDIDATE, QUOTE_CANDIDATE, ATTENTION_CANDIDATE],
        pages=page_rows(PAPER_PAGES),
        pdf_texts=PAPER_PAGES,
    )


def _cards_of(manifest: dict) -> list[dict]:
    """manifest 里所有的卡（摊平）。"""
    return [card for library in manifest["category"]["libraries"] for card in library["cards"]]


def _public_manifest(work) -> dict:
    """读公开包的 manifest。"""
    return read_pack_manifest(work / "pack" / "0123456789ab.public.ttfc.zip")


def _private_manifest(work) -> dict:
    """读私有包的 manifest。"""
    return read_pack_manifest(work / "pack" / "0123456789ab.ttfc.zip")


# ===================== 许可判定 =====================

_KIND_URIS = [
    ("cc-by-4.0", "http://creativecommons.org/licenses/by/4.0/"),
    ("cc-by-sa-4.0", "http://creativecommons.org/licenses/by-sa/4.0/"),
    ("cc-by-nc-sa-4.0", "http://creativecommons.org/licenses/by-nc-sa/4.0/"),
    ("cc-by-nc-nd-4.0", "http://creativecommons.org/licenses/by-nc-nd/4.0/"),
    ("cc0-1.0", "http://creativecommons.org/publicdomain/zero/1.0/"),
    ("arxiv-nonexclusive-1.0", "http://arxiv.org/licenses/nonexclusive-distrib/1.0/"),
    ("assumed-1991-2003", "http://arxiv.org/licenses/assumed-1991-2003/"),
]


@pytest.mark.parametrize(("kind", "uri"), _KIND_URIS)
def test_license_kind_from_uri_knows_every_license(kind, uri):
    """表里每种许可的 URI 都要认得出。"""
    assert license_kind_from_uri(uri) == kind
    # https 也认，末尾斜杠可有可无。
    assert license_kind_from_uri(uri.replace("http://", "https://")) == kind
    assert license_kind_from_uri(uri.rstrip("/")) == kind


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("CC BY 4.0", "cc-by-4.0"),
        ("cc-by-4.0", "cc-by-4.0"),
        ("CC BY-SA 4.0", "cc-by-sa-4.0"),
        ("CC BY-NC-SA 4.0", "cc-by-nc-sa-4.0"),
        ("CC BY-NC-ND 4.0", "cc-by-nc-nd-4.0"),
        ("CC0", "cc0-1.0"),
        ("CC0 1.0", "cc0-1.0"),
    ],
)
def test_license_kind_from_uri_accepts_cc_abbreviations(text, kind):
    """CC 缩写写法也认。"""
    assert license_kind_from_uri(text) == kind


def test_license_kind_from_uri_falls_back_to_unknown():
    """认不出就是 unknown（记录时按「默认许可」记）。"""
    assert license_kind_from_uri("") == "unknown"
    assert license_kind_from_uri("http://example.com/some-license") == "unknown"
    # 表里没有的许可（只禁改作、没禁商用）也认不出。
    assert license_kind_from_uri("CC BY-ND 4.0") == "unknown"


@pytest.mark.parametrize("kind", LICENSE_KINDS)
def test_license_kind_from_uri_maps_every_kind_string_to_itself(kind):
    """八个 kind 字符串逐个自映射（``book.json`` 里可能就直接写 kind）。"""
    assert license_kind_from_uri(kind) == kind


@pytest.mark.parametrize(
    ("kind", "flags"),
    [
        ("cc-by-4.0", {"nc": False, "nd": False, "sa": False, "permissive": True}),
        ("cc-by-sa-4.0", {"nc": False, "nd": False, "sa": True, "permissive": True}),
        ("cc0-1.0", {"nc": False, "nd": False, "sa": False, "permissive": True}),
        ("cc-by-nc-sa-4.0", {"nc": True, "nd": False, "sa": True, "permissive": False}),
        ("cc-by-nc-nd-4.0", {"nc": True, "nd": True, "sa": False, "permissive": False}),
        (
            "arxiv-nonexclusive-1.0",
            {"nc": False, "nd": False, "sa": False, "permissive": False},
        ),
        ("assumed-1991-2003", {"nc": False, "nd": False, "sa": False, "permissive": False}),
        ("unknown", {"nc": False, "nd": False, "sa": False, "permissive": False}),
    ],
)
def test_license_flags_marks_nc_nd_sa_and_permissive(kind, flags):
    """旗标：nc / nd / sa / permissive。"""
    assert license_flags(kind) == flags


# ===================== 短引用（保留函数，不再裁剪） =====================


def test_quote_within_limits_accepts_short_quotes():
    """短引用（中文字符数算词数）算数。"""
    assert quote_within_limits("良渚文化位于长江下游，是新石器时代晚期的文化遗存。")
    assert quote_within_limits("a short quote")


def test_quote_within_limits_rejects_long_or_wordy_quotes():
    """超长（>200 字符）或超词数（>40 词）都不算短引用。"""
    assert not quote_within_limits("长" * 201)
    assert not quote_within_limits("abc " * 41)
    assert not quote_within_limits("")


# ===================== 出处四要素 =====================

_GOOD_ATTRIBUTION = (
    'Ada Lovelace. "On Analytic Engines". arXiv:2401.12345 (2024-01-05). '
    "https://arxiv.org/abs/2401.12345 — License: cc-by-4.0 "
    "(http://creativecommons.org/licenses/by/4.0/)"
)


def test_attribution_ok_needs_id_link_and_length():
    """出处四要素齐全才算数。"""
    assert attribution_ok(_GOOD_ATTRIBUTION)


def test_attribution_ok_rejects_missing_id():
    """没有 arXiv id ⇒ 不通过。"""
    without_id = (
        'Ada Lovelace. "On Analytic Engines". https://arxiv.org/abs/1234 — '
        "License: cc-by-4.0 (http://creativecommons.org/licenses/by/4.0/)"
    )
    assert not attribution_ok(without_id)


def test_attribution_ok_rejects_missing_link():
    """没有链接 ⇒ 不通过。"""
    without_link = (
        'Ada Lovelace. "On Analytic Engines". arXiv:2401.12345 (2024-01-05) — '
        "许可见论文首页。"
    )
    assert not attribution_ok(without_link)


def test_attribution_ok_rejects_too_short_text():
    """太短 ⇒ 不通过。"""
    assert not attribution_ok("arXiv:2401.12345")


# ===================== 卡片分享性质（只统计，不裁剪） =====================


def test_card_share_prefers_the_explicit_field():
    """卡上写了 share 就用它。"""
    assert card_share({"front": "短", "back": "短", "share": "summary"}) == "summary"
    long_front = "这是一句很长的话，长到超过四十个字符，所以启发式会判成复述卡。"
    assert card_share({"front": long_front, "back": "解释。", "share": "term"}) == "term"


def test_card_share_heuristic():
    """启发式：正面短、无句末标点、背面短 ⇒ 术语卡；否则复述卡。"""
    assert card_share({"front": "良渚文化", "back": "长江下游的新石器时代晚期文化。"}) == "term"
    assert (
        card_share(
            {
                "front": "良渚文化的城址和水利系统展现了早期国家的形态。",
                "back": "早期国家形态的一段复述。",
            }
        )
        == "summary"
    )
    assert card_share({"front": "良渚文化", "back": "长" * 81}) == "summary"


def test_card_share_falls_back_to_summary_without_enough_info():
    """判定不了/信息不足 ⇒ 保守当复述卡。"""
    assert card_share({}) == "summary"
    assert card_share({"front": "良渚文化"}) == "summary"
    assert card_share({"back": "解释。"}) == "summary"


# ===================== 许可属性表（记录用） =====================


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (
            "cc-by-4.0",
            dict(
                allow_quote=True,
                allow_context=True,
                allow_summary_cards=True,
                nc=False,
                nd=False,
                require_attribution=True,
                noncommercial=False,
            ),
        ),
        (
            "cc-by-sa-4.0",
            dict(
                allow_quote=True,
                allow_context=True,
                allow_summary_cards=True,
                nc=False,
                nd=False,
                require_attribution=True,
                noncommercial=False,
            ),
        ),
        (
            "cc0-1.0",
            dict(
                allow_quote=True,
                allow_context=True,
                allow_summary_cards=True,
                nc=False,
                nd=False,
                require_attribution=True,
                noncommercial=False,
            ),
        ),
        (
            "cc-by-nc-sa-4.0",
            dict(
                allow_quote=True,
                allow_context=True,
                allow_summary_cards=True,
                nc=True,
                nd=False,
                require_attribution=True,
                noncommercial=True,
            ),
        ),
        (
            "cc-by-nc-nd-4.0",
            dict(
                allow_quote=True,
                allow_context=False,
                allow_summary_cards=False,
                nc=True,
                nd=True,
                require_attribution=True,
                noncommercial=True,
            ),
        ),
        (
            "arxiv-nonexclusive-1.0",
            dict(
                allow_quote=True,
                allow_context=False,
                allow_summary_cards=False,
                nc=False,
                nd=False,
                require_attribution=True,
                noncommercial=False,
            ),
        ),
        (
            "assumed-1991-2003",
            dict(
                allow_quote=True,
                allow_context=False,
                allow_summary_cards=False,
                nc=False,
                nd=False,
                require_attribution=True,
                noncommercial=False,
            ),
        ),
        (
            "unknown",
            dict(
                allow_quote=True,
                allow_context=False,
                allow_summary_cards=False,
                nc=False,
                nd=False,
                require_attribution=True,
                noncommercial=False,
            ),
        ),
    ],
)
def test_public_policy_follows_the_table(kind, expected):
    """许可属性表每一行都要对得上（记录用，不再裁剪）。"""
    policy = public_policy(kind)
    for field, value in expected.items():
        assert getattr(policy, field) is value


# ===================== 出处文本 =====================


def test_build_attribution_writes_the_four_elements():
    """出处文本：作者 / 标题 / arXiv id / 链接 + 许可。"""
    text = build_attribution(_paper_book())
    assert text.startswith('Ada Lovelace. "On Analytic Engines". arXiv:2401.12345 (2024-01-05).')
    assert "https://arxiv.org/abs/2401.12345" in text
    assert "License: cc-by-4.0 (http://creativecommons.org/licenses/by/4.0/)" in text


def test_build_attribution_marks_unknown_authors_and_skips_missing_fields():
    """缺项不编：缺作者写 Unknown authors，缺 arXiv id 那一段不出现。"""
    assert "Unknown authors" in build_attribution(_paper_book(authors=[]))
    text = build_attribution({"paper_license": "http://creativecommons.org/licenses/by/4.0/"})
    assert "Unknown authors" in text
    assert "arXiv:" not in text


# ===================== 照抄检查 =====================


def test_max_common_run_finds_the_longest_common_run():
    """最长公共子串：中文和拉丁文都要算得对。"""
    page = PAPER_PAGES[1]
    assert max_common_run(page, page, min_len=1) == len(page)
    assert max_common_run("解释里的良渚文化三个字", page, min_len=1) == 4
    assert max_common_run("abcdefghij", "xxabcdefghijyy", min_len=1) == 10


def test_max_common_run_returns_zero_below_min_len():
    """比 min_len 短就当没有（0）。"""
    page = PAPER_PAGES[1]
    assert max_common_run("良渚文化", page, min_len=10) == 0
    assert max_common_run("良渚文化", page, min_len=4) == 4
    assert max_common_run("", page, min_len=1) == 0
    assert max_common_run("良渚文化", "", min_len=1) == 0


def test_is_verbatim_copy_flags_long_chinese_runs():
    """含中文：公共子串 ≥ 30 字符 ⇒ 照抄。"""
    page = PAPER_PAGES[1]
    assert is_verbatim_copy(page, page)
    assert is_verbatim_copy("前面几个字" + page + "后面几个字", page)
    # 不到 30 字就不算照抄。
    assert not is_verbatim_copy(page[: VERBATIM_RUN_CJK - 1], page)


def test_is_verbatim_copy_flags_long_latin_runs():
    """纯拉丁文：公共子串 ≥ 60 字符 ⇒ 照抄。"""
    page = "The attention mechanism focuses on key parts of long sequences."
    assert is_verbatim_copy(page, page)
    # 差一个字符就不到 60。
    assert not is_verbatim_copy(page[: VERBATIM_RUN_LATIN - 1], page)


def test_is_verbatim_copy_lets_short_quotes_through():
    """引一两个术语不算照抄。"""
    explanation = "注意力机制（attention mechanism）让模型聚焦长序列的关键部分。"
    assert not is_verbatim_copy(explanation, PAPER_PAGES[3])
    assert not is_verbatim_copy("良渚文化是长江下游的新石器时代晚期文化遗存。", PAPER_PAGES[1])


def test_is_verbatim_copy_ignores_empty_inputs():
    """空文本不判照抄（也不误杀）。"""
    assert not is_verbatim_copy("", PAPER_PAGES[1])
    assert not is_verbatim_copy(PAPER_PAGES[1], "")


# ===================== 打包：论文源公开包 = 书源公开包 =====================


def test_paper_public_pack_has_the_same_shape_as_the_book_public_pack(tmp_path):
    """论文源的公开包 = 书源的公开包：没有 evidence、没有 context、没有图。"""
    work, pdf = _ready_workdir(tmp_path, name="paper", book=_paper_book())
    book_work, book_pdf = _ready_workdir(tmp_path, name="book", book=dict(BOOK))

    pack(work, pdf=pdf, edition="public")
    pack(book_work, pdf=book_pdf, edition="public")

    paper_card = _cards_of(_public_manifest(work))[0]
    book_card = _cards_of(_public_manifest(book_work))[0]
    assert set(paper_card["source"]) == set(book_card["source"])
    assert set(paper_card["source"]) == {"page", "printed_page", "bbox", "verbatim", "method"}
    assert "evidence" not in paper_card["source"]
    assert "context" not in paper_card["source"]
    assert paper_card["images"] == []


def test_paper_public_pack_content_does_not_depend_on_the_license(tmp_path):
    """许可只记录、不裁剪：宽松许可和默认 arXiv 许可的公开包内容一样。"""
    permissive, permissive_pdf = _ready_workdir(
        tmp_path, name="permissive", book=_paper_book()
    )
    default, default_pdf = _ready_workdir(
        tmp_path,
        name="default",
        book=_paper_book(paper_license="http://arxiv.org/licenses/nonexclusive-distrib/1.0/"),
    )

    pack(permissive, pdf=permissive_pdf, edition="public")
    pack(default, pdf=default_pdf, edition="public")

    permissive_manifest = _public_manifest(permissive)
    default_manifest = _public_manifest(default)
    # 卡的内容一模一样。
    assert permissive_manifest["category"] == default_manifest["category"]
    # 记下来的许可不同。
    assert permissive_manifest["source_license"]["kind"] == "cc-by-4.0"
    assert permissive_manifest["source_license"]["permissive"] is True
    assert default_manifest["source_license"]["kind"] == "arxiv-nonexclusive-1.0"
    assert default_manifest["source_license"]["permissive"] is False
    assert permissive_manifest["policy"]["gate"] == "same-as-book"
    assert default_manifest["policy"]["gate"] == "same-as-book"


def test_public_pack_without_license_field_records_unknown_but_keeps_cards(tmp_path):
    """许可字段缺失 ⇒ 记成 unknown，但**不裁剪**（卡照旧全在）。"""
    book = _paper_book()
    book.pop("paper_license")
    work, pdf = _ready_workdir(tmp_path, book=book)

    summary = pack(work, pdf=pdf, edition="public")

    manifest = _public_manifest(work)
    assert summary["source_license"] == "unknown"
    assert summary["dropped_cards"] == 0
    assert summary["dropped_verbatim"] == 0
    assert len(_cards_of(manifest)) == 2
    assert manifest["source_license"]["kind"] == "unknown"
    assert manifest["source_license"]["uri"] == ""
    assert manifest["policy"]["source_kind"] == "unknown"


def test_public_pack_records_the_license_and_attribution(tmp_path):
    """论文源的公开包要记许可和出处（书本源不记）。"""
    work, pdf = _ready_workdir(tmp_path, book=_paper_book())

    summary = pack(work, pdf=pdf, edition="public")

    manifest = _public_manifest(work)
    assert summary["source_license"] == "cc-by-4.0"
    assert manifest["source_license"] == {
        "kind": "cc-by-4.0",
        "uri": "http://creativecommons.org/licenses/by/4.0/",
        "nc": False,
        "nd": False,
        "sa": False,
        "permissive": True,
        "noncommercial": False,
    }
    assert manifest["attribution"].startswith("Ada Lovelace.")
    assert manifest["policy"] == {
        "gate": "same-as-book",
        "source_kind": "cc-by-4.0",
        "nc": False,
        "nd": False,
        "sa": False,
        "permissive": True,
        "noncommercial": False,
        "dropped_verbatim": 0,
        "term_cards": 1,
        "summary_cards": 1,
    }
    assert summary["term_cards"] == 1
    assert summary["summary_cards"] == 1


def test_public_pack_with_arxiv_kind_string_records_the_real_license(tmp_path):
    """``paper_license`` 直接写 kind 字符串 ⇒ manifest 记的许可名和 URI 都要准。"""
    book = _paper_book(paper_license="arxiv-nonexclusive-1.0")
    work, pdf = _ready_workdir(tmp_path, book=book)

    summary = pack(work, pdf=pdf, edition="public")

    manifest = _public_manifest(work)
    assert summary["source_license"] == "arxiv-nonexclusive-1.0"
    assert manifest["source_license"]["kind"] == "arxiv-nonexclusive-1.0"
    assert manifest["source_license"]["uri"]
    assert manifest["source_license"]["uri"].startswith("http://arxiv.org/licenses/")
    assert manifest["policy"]["source_kind"] == "arxiv-nonexclusive-1.0"
    assert "License: arxiv-nonexclusive-1.0" in manifest["attribution"]


# ===================== 打包：照抄检查 =====================


def test_public_pack_drops_cards_that_copy_the_original_text(tmp_path):
    """解释整段照抄原文 ⇒ 这张卡不进公开包，计数要准。"""
    work, pdf = _ready_paper_workdir(tmp_path)

    summary = pack(work, pdf=pdf, edition="public")

    manifest = _public_manifest(work)
    fronts = [card["front"] for card in _cards_of(manifest)]
    assert "良渚文化" not in fronts
    assert fronts == ["二里头遗址", "attention mechanism"]
    assert summary["dropped_verbatim"] == 1
    assert summary["dropped_cards"] == 1
    assert summary["cards"] == 2
    assert manifest["policy"]["dropped_verbatim"] == 1


def test_public_pack_keeps_cards_with_a_short_quote(tmp_path):
    """解释里引一两个术语 ⇒ 不算照抄，卡照旧在公开包里。"""
    work, pdf = _ready_paper_workdir(tmp_path)

    summary = pack(work, pdf=pdf, edition="public")

    fronts = [card["front"] for card in _cards_of(_public_manifest(work))]
    assert "attention mechanism" in fronts
    assert summary["dropped_verbatim"] == 1


def test_public_pack_does_not_drop_cards_without_page_text(tmp_path):
    """查不到该卡所在页的原文 ⇒ 跳过检查，不误杀。"""
    work, pdf = _ready_paper_workdir(tmp_path)
    # 把卡的页号改成一个 pages.jsonl 里没有的页 ⇒ 查不到原文。
    cards = [
        json.loads(line)
        for line in (work / "cards.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for card in cards:
        card["source"]["page"] = 99
    (work / "cards.jsonl").write_text(
        "".join(json.dumps(card, ensure_ascii=False) + "\n" for card in cards),
        encoding="utf-8",
    )

    summary = pack(work, pdf=pdf, edition="public")

    fronts = [card["front"] for card in _cards_of(_public_manifest(work))]
    assert "良渚文化" in fronts
    assert summary["dropped_verbatim"] == 0
    assert summary["dropped_cards"] == 0


def test_private_pack_does_not_check_verbatim(tmp_path):
    """私有包是作者自己的，不做照抄检查（照抄的卡也留着）。"""
    work, pdf = _ready_paper_workdir(tmp_path)

    summary = pack(work, pdf=pdf, edition="private", scene="text")

    manifest = _private_manifest(work)
    fronts = [card["front"] for card in _cards_of(manifest)]
    assert "良渚文化" in fronts
    assert len(fronts) == 3
    assert summary["dropped_verbatim"] == 0
    assert summary["dropped_cards"] == 0
    assert manifest["policy"]["dropped_verbatim"] == 0


# ===================== 打包：私有包（论文源）一切照旧 =====================


def test_private_paper_pack_keeps_evidence_and_context(tmp_path):
    """私有包：依据句、context 都照旧（私有包不受公开包的闸门约束）。"""
    work, pdf = _ready_workdir(tmp_path, book=_paper_book())

    summary = pack(work, pdf=pdf, edition="private", scene="text")

    manifest = _private_manifest(work)
    cards = _cards_of(manifest)
    assert summary["dropped_cards"] == 0
    assert summary["source_license"] == "cc-by-4.0"
    assert len(cards) == 2
    for card in cards:
        assert card["source"]["evidence"]
        assert card["source"]["context"]["text"]


def test_private_paper_pack_keeps_page_images(tmp_path):
    """私有包 + 图片场景：页面图照旧带（公开包才禁图）。"""
    work, pdf = _ready_workdir(tmp_path, book=_paper_book())

    summary = pack(work, pdf=pdf, edition="private", scene="image")

    manifest = _private_manifest(work)
    card = manifest["category"]["libraries"][0]["cards"][0]
    assert summary["images"] == 2
    assert card["images"][0]["file"] == "images/p0001.jpg"
    assert card["source"]["evidence"]


# ===================== 打包：书本来源一字不改 =====================


def test_book_source_public_pack_is_unchanged(tmp_path):
    """书本来源（没有 type 字段）⇒ 公开包和现在一模一样。"""
    work, pdf = _ready_workdir(tmp_path, book=dict(BOOK))

    summary = pack(work, pdf=pdf, edition="public")

    manifest = _public_manifest(work)
    card = manifest["category"]["libraries"][0]["cards"][0]
    assert set(card["source"]) == {"page", "printed_page", "bbox", "verbatim", "method"}
    assert "evidence" not in card["source"]
    assert "context" not in card["source"]
    assert "source_license" not in manifest
    assert "attribution" not in manifest
    assert "policy" not in manifest
    assert summary["source_license"] == ""
    assert summary["dropped_cards"] == 0
    assert summary["dropped_verbatim"] == 0
    assert summary["term_cards"] == 0
    assert summary["summary_cards"] == 0


def test_book_source_private_pack_is_unchanged(tmp_path):
    """书本来源的私有包也不受影响（图、证据句、context 照旧）。"""
    work, pdf = _ready_workdir(tmp_path, book=dict(BOOK))

    summary = pack(work, pdf=pdf, edition="private", scene="text")

    manifest = _private_manifest(work)
    card = manifest["category"]["libraries"][0]["cards"][0]
    assert card["source"]["evidence"]
    assert card["source"]["context"]["text"]
    assert summary["dropped_cards"] == 0
    assert summary["dropped_verbatim"] == 0
    assert summary["term_cards"] == 0
    assert summary["summary_cards"] == 0
    assert "policy" not in manifest


# ===================== App 兼容 =====================


def test_public_paper_pack_passes_apps_bulk_import_body(tmp_path):
    """论文源的公开包也要能过 App 的 BulkImportBody（老 App 能导入）。"""
    work, pdf = _ready_workdir(tmp_path, book=_paper_book())

    pack(work, pdf=pdf, edition="public")

    with zipfile.ZipFile(work / "pack" / "0123456789ab.public.ttfc.zip") as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        resolved = _resolve_images_like_app(manifest, archive)

    body = BulkImportBody(**resolved)

    assert body.category.name == "On Analytic Engines"
    assert body.category.libraries[0].cards[0].front == "良渚文化"
    assert body.category.libraries[0].cards[0].images == []


def test_private_paper_pack_passes_apps_bulk_import_body(tmp_path):
    """论文源的私有包也要能过 App 的 BulkImportBody。"""
    work, pdf = _ready_workdir(tmp_path, book=_paper_book())

    pack(work, pdf=pdf, edition="private", scene="image")

    with zipfile.ZipFile(work / "pack" / "0123456789ab.ttfc.zip") as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        resolved = _resolve_images_like_app(manifest, archive)

    body = BulkImportBody(**resolved)

    assert body.category.libraries[0].cards[0].front == "良渚文化"
    assert body.category.libraries[0].cards[0].images[0].content_base64
