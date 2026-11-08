"""依据句校正的测试（）。

LLM 给的依据句有时根本不含这个词 ⇒ 回退到页面原文里包住这个词的那一句，
并用 ``source.evidence_origin`` 记下这句的出处（现在是 ``sentence``：引擎自己从页面原文取）。
"""

from bookcard.pipeline.select import select
from bookcard.pipeline.workdir import WorkDir
from pipeline_fixtures import candidate, page_rows, write_workdir


def _run(work):
    """跑一遍 select，返回 ``cards.jsonl`` 里的卡。"""
    select(work)
    return WorkDir(work).cards()


def test_missing_evidence_falls_back_to_page_sentence(tmp_path):
    """依据句本来就是 None ⇒ 同样回退到页面原文句。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[
            candidate(1, "良渚文化", explanation="长江下游的新石器时代晚期文化。")
        ],
    )

    cards = _run(work)
    source = cards[0]["source"]

    assert source["evidence"] == "良渚文化位于长江下游，是新石器时代晚期的文化遗存。"
    assert source["evidence_origin"] == "sentence"


def test_english_sentence_spanning_lines_is_not_split(tmp_path):
    """英文句子跨行时，单个换行不算断句 ⇒ 整句一起回退。"""
    texts = {
        1: "Photosynthesis is the process by which\n"
        "plants make food using sunlight.\n"
        "Learning from mistakes makes you smarter.",
    }
    work = write_workdir(
        tmp_path / "book",
        pages=page_rows(texts),
        candidates=[
            candidate(
                1,
                "Photosynthesis",
                explanation="光合作用。",
                evidence="Learning from mistakes makes you smarter.",
                texts=texts,
            )
        ],
    )

    cards = _run(work)
    source = cards[0]["source"]

    assert source["evidence"] == (
        "Photosynthesis is the process by which\nplants make food using sunlight."
    )
    assert source["evidence_origin"] == "sentence"


def test_fallback_evidence_is_verbatim_substring_of_page_text(tmp_path):
    """回退的依据句必须是页面原文的逐字子串。"""
    texts = {
        1: "第一句在这里。\n第二句包含良渚文化这个词。\n第三句。",
    }
    work = write_workdir(
        tmp_path / "book",
        pages=page_rows(texts),
        candidates=[
            candidate(
                1,
                "良渚文化",
                explanation="解释。",
                evidence="第三句。",
                texts=texts,
            )
        ],
    )

    cards = _run(work)
    source = cards[0]["source"]

    assert source["evidence"] in texts[1]
    assert "良渚文化" in source["evidence"]


def test_existing_source_fields_are_unchanged(tmp_path):
    """契约 §7：只加字段，不改已有字段。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[
            candidate(
                1,
                "良渚文化",
                explanation="解释。",
                evidence="良渚文化位于长江下游",
            )
        ],
    )

    cards = _run(work)
    source = cards[0]["source"]

    for field in (
        "page",
        "printed_page",
        "seg_id",
        "start",
        "end",
        "evidence",
        "verbatim",
        "method",
        "bbox",
        "image",
    ):
        assert field in source
    assert source["page"] == 1
    assert source["verbatim"] is True
    assert source["method"] == "llm"
    assert source["bbox"] is None
    assert source["image"] is None
