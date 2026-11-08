"""§5 挑卡去重的测试：去重、丢低价值、归属 section、排序、content_fp。"""

import hashlib

from bookcard.pipeline.select import select
from bookcard.pipeline.workdir import WorkDir, norm
from pipeline_fixtures import candidate, page_rows, write_workdir


def _run(work):
    """跑一遍 select，返回 ``(摘要, cards.jsonl 里的卡)``。"""
    summary = select(work)
    return summary, WorkDir(work).cards()


def _fronts(cards):
    return [card["front"] for card in cards]


def test_duplicate_front_keeps_first_occurrence(tmp_path):
    """同一个词在两页出现 ⇒ 只留第一次（最早页）那张。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[
            candidate(1, "良渚文化", explanation="长江下游的新石器时代晚期文化。"),
            candidate(2, "良渚文化", explanation="重复出现的解释。"),
        ],
    )

    summary, cards = _run(work)

    assert summary["cards"] == 1
    assert len(cards) == 1
    assert cards[0]["source"]["page"] == 1
    assert cards[0]["back"] == "长江下游的新石器时代晚期文化。"


def test_duplicate_keeps_max_learn_value(tmp_path):
    """重复词的 learn_value 取最大值（第一次出现那次仍被丢掉）。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[
            candidate(1, "良渚文化", explanation="解释。", learn_value=1),
            candidate(2, "良渚文化", explanation="另一个解释。", learn_value=3),
        ],
    )

    summary, cards = _run(work)

    assert summary["cards"] == 1
    assert len(cards) == 1
    assert cards[0]["source"]["page"] == 1


def test_low_learn_value_is_dropped(tmp_path):
    """learn_value == 1（可有可无）不做卡。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[
            candidate(1, "良渚文化", explanation="解释。", learn_value=1),
            candidate(3, "二里头遗址", explanation="解释。", learn_value=3),
        ],
    )

    summary, cards = _run(work)

    assert summary["dropped_low_value"] == 1
    assert summary["cards"] == 1
    assert _fronts(cards) == ["二里头遗址"]


def test_card_without_explanation_is_not_a_card(tmp_path):
    """没解释的词不做卡（但仍在 candidates 里）。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[
            candidate(1, "良渚文化", explanation=None),
            candidate(3, "二里头遗址", explanation="夏代都城遗址的候选。"),
        ],
    )

    summary, cards = _run(work)

    assert summary["dropped_no_explanation"] == 1
    assert summary["cards"] == 1
    assert _fronts(cards) == ["二里头遗址"]


def test_card_belongs_to_first_appearing_section(tmp_path):
    """卡归属第一次出现的 section，不是最后一次。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[
            candidate(2, "早期国家", explanation="解释。", sid="s01"),
            candidate(3, "早期国家", explanation="解释。", sid="s02"),
        ],
    )

    _, cards = _run(work)

    assert len(cards) == 1
    assert cards[0]["sid"] == "s01"
    # 卡的标签 = **书的元信息标签**（领域 / 水平 / 书上自带的 tags）
    assert cards[0]["tags"] == ["历史", "senior", "通史", "中国古代史"]


def test_sort_order_is_by_page_then_start(tmp_path):
    """section 内按页码、再按 start 排 sort_order（1 起）。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[
            candidate(2, "水利系统", explanation="解释。"),
            candidate(1, "良渚文化", explanation="解释。"),
            candidate(1, "长江下游", explanation="解释。"),
        ],
    )

    _, cards = _run(work)

    section_one = [card for card in cards if card["sid"] == "s01"]
    assert [card["front"] for card in section_one] == ["良渚文化", "长江下游", "水利系统"]
    assert [card["sort_order"] for card in section_one] == [1, 2, 3]


def test_content_fp_matches_contract_formula(tmp_path):
    """content_fp = sha256(norm(front) + '\\n' + norm(back))。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[candidate(1, "良渚文化", explanation="长江下游的新石器时代晚期文化。")],
    )

    _, cards = _run(work)

    expected = hashlib.sha256(
        f"{norm('良渚文化')}\n{norm('长江下游的新石器时代晚期文化。')}".encode()
    ).hexdigest()
    assert cards[0]["content_fp"] == expected


def test_card_id_shape(tmp_path):
    """card_id = <fp12>:<sid>:<sha1(front)[:8]>。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[candidate(1, "良渚文化", explanation="解释。")],
    )

    _, cards = _run(work)

    digest = hashlib.sha1("良渚文化".encode()).hexdigest()[:8]
    assert cards[0]["card_id"] == f"0123456789ab:s01:{digest}"


def test_source_carries_evidence_and_verbatim(tmp_path):
    """source 带依据句原文、原文标记、页码、seg_id。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[
            candidate(
                1,
                "良渚文化",
                explanation="长江下游的新石器时代晚期文化。",
            )
        ],
    )

    _, cards = _run(work)

    source = cards[0]["source"]
    assert source["evidence"] == "良渚文化位于长江下游，是新石器时代晚期的文化遗存。"
    assert source["verbatim"] is True
    assert source["page"] == 1
    assert source["printed_page"] == "1"
    assert source["seg_id"] == "book:0123456789ab:p0001"
    assert source["method"] == "llm"
    assert source["bbox"] is None
    assert source["image"] is None


def test_select_writes_cards_jsonl(tmp_path):
    """cards.jsonl 写到工作目录里。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[candidate(1, "良渚文化", explanation="解释。")],
    )

    summary = select(work)

    assert summary["cards_path"] == str(work / "cards.jsonl")
    assert (work / "cards.jsonl").exists()
    assert len(WorkDir(work).cards()) == 1


def test_english_front_is_deduplicated_case_insensitively(tmp_path):
    """英文大小写不同按同一个词去重（norm 里英文小写）。"""
    texts = {
        1: "photosynthesis is the process by which plants make food.",
        2: "Photosynthesis is the process by which plants make food.",
    }
    work = write_workdir(
        tmp_path / "book",
        pages=page_rows(texts),
        sections=[
            {
                "sid": "s01",
                "title": "Unit 1",
                "level": 2,
                "page_start": 1,
                "page_end": 2,
                "sort_order": 1,
            }
        ],
        candidates=[
            candidate(1, "photosynthesis", explanation="光合作用。", texts=texts),
            candidate(2, "Photosynthesis", explanation="小写的同一个词。", texts=texts),
        ],
    )

    _, cards = _run(work)

    assert len(cards) == 1
    assert cards[0]["front"] == "photosynthesis"
    assert cards[0]["source"]["page"] == 1


def test_chunk_relative_offsets_are_mapped_to_page_offsets(tmp_path):
    """上游对超长页切块时，item 偏移是 chunk 内偏移 ⇒ 要换算成页内偏移。

    chunk 文字 = 页文字里连续两段各去空白后用 ``\\n`` 拼接；行上的
    ``text_sha1`` 是 chunk 文字的 sha1。
    """
    import hashlib

    para_one = "良渚文化位于长江下游，是新石器时代晚期的文化遗存。"
    para_two = "良渚文化的城址和水利系统展现了早期国家的形态。"
    page_text = f"{para_one}\n{para_two}"
    chunk_text = f"{para_one}\n{para_two}"

    work = write_workdir(
        tmp_path / "book",
        pages=[
            {
                "page": 1,
                "printed_page": "1",
                "text": page_text,
                "text_sha1": hashlib.sha1(page_text.encode()).hexdigest(),
                "kind": "body",
            }
        ],
        sections=[
            {
                "sid": "s01",
                "title": "第1课",
                "level": 2,
                "page_start": 1,
                "page_end": 1,
                "sort_order": 1,
            }
        ],
        candidates=[
            {
                "seg_id": "book:0123456789ab:p0001c1",
                "page": 1,
                "chunk": 1,
                "sid": "s01",
                # chunk 文字的 sha1，不是页文字的
                "text_sha1": hashlib.sha1(chunk_text.encode()).hexdigest(),
                "extractor": "llm:minimax:MiniMax-M3.1-Flash:ex-v1",
                "scope": "full",
                "items": [
                    {
                        "surface": "良渚文化",
                        "type": "name",
                        "start": 0,
                        "end": 4,
                        "explanation": "长江下游的新石器时代晚期文化。",
        
                        "learn_value": 3,
                    }
                ],
                "dropped": {"not_in_text": 0},
                "created_at": "2026-11-06T00:00:00Z",
            }
        ],
    )

    _, cards = _run(work)
    card = cards[0]

    # 页内偏移：页文字里「良渚文化」在第 0 位 ⇒ start/end 落在页坐标上
    assert card["source"]["start"] == 0
    assert card["source"]["end"] == 4
    assert card["source"]["verbatim"] is True
    # 依据句从 chunk 文字里切出来
    assert card["source"]["evidence"] == para_one


def test_cross_line_surface_still_verbatim(tmp_path):
    """跨硬折行落位的词：``量子\\n力学`` 仍算原文、照常取依据句。

    上游 ``place_items`` 落出来的偏移可以跨 ``\\n``（原文坐标），select 不能
    因为 ``text[start:end] != surface`` 就把它判成非原文。
    """
    from bookcard import extract

    text = "我们研究量子\n力学的应用。"
    items, not_in_text = extract.place_items(
        text,
        [
            extract.Keyword(
                front="量子力学",
                type="term",
                back="研究物质最基本层面的物理学分支。",
                learn_value=3,
            )
        ],
    )
    assert not_in_text == 0
    item = items[0]
    assert text[item["start"] : item["end"]] == "量子\n力学"

    row = candidate(1, "我们研究", explanation="占位。", texts={1: text})
    row["items"] = [item]

    work = write_workdir(
        tmp_path / "book",
        candidates=[row],
        pages=page_rows({1: text}),
    )

    _, cards = _run(work)

    assert len(cards) == 1
    source = cards[0]["source"]
    assert source["verbatim"] is True
    assert source["start"] == item["start"]
    assert source["end"] == item["end"]
    assert source["evidence"] == "我们研究量子\n力学的应用。"
    assert source["evidence_origin"] == "sentence"


def test_cross_line_surface_in_chunk_still_verbatim(tmp_path):
    """chunk 里的跨行词：chunk→页偏移换算后仍算原文、取到页内依据句。"""
    from bookcard import extract

    page_text = "前言略。\n\n我们研究量子\n力学的应用。"
    chunk_text = "我们研究量子\n力学的应用。"
    items, not_in_text = extract.place_items(
        chunk_text,
        [
            extract.Keyword(
                front="量子力学",
                type="term",
                back="研究物质最基本层面的物理学分支。",
                learn_value=3,
            )
        ],
    )
    assert not_in_text == 0
    item = items[0]
    assert chunk_text[item["start"] : item["end"]] == "量子\n力学"

    row = candidate(1, "我们研究", explanation="占位。", texts={1: page_text})
    row["text_sha1"] = hashlib.sha1(chunk_text.encode()).hexdigest()
    row["items"] = [item]

    work = write_workdir(
        tmp_path / "book",
        candidates=[row],
        pages=page_rows({1: page_text}),
    )

    _, cards = _run(work)

    assert len(cards) == 1
    source = cards[0]["source"]
    assert source["verbatim"] is True
    assert page_text[source["start"] : source["end"]] == "量子\n力学"
    assert source["evidence"] == "我们研究量子\n力学的应用。"
    assert source["evidence_origin"] == "sentence"


def test_select_card_goes_to_one_library(tmp_path):
    """一页只进一个词库（打包的目录结构是唯一的）。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[candidate(1, "良渚文化", explanation="解释。")],
        sections=[
            {
                "sid": "s01",
                "title": "第 1 章 母题",
                "level": 1,
                "page_start": 1,
                "page_end": 10,
                "sort_order": 1,
            },
            {
                "sid": "s02",
                "title": "第 1 章 母题 | 第一节 起源",
                "level": 2,
                "page_start": 1,
                "page_end": 4,
                "sort_order": 2,
            },
            {
                "sid": "s03",
                "title": "第 1 章 母题 | 第二节 发展",
                "level": 2,
                "page_start": 5,
                "page_end": 10,
                "sort_order": 3,
            },
        ],
    )

    summary = select(work)
    cards = WorkDir(work).cards()

    # 这一页在 s01 和 s02 里 —— 只进第一个命中的 s01
    assert summary["cards"] == 1
    assert cards[0]["sid"] == "s01"
    assert cards[0]["front"] == "良渚文化"


def test_select_page_not_in_first_section_goes_to_its_own(tmp_path):
    """页落在别的 section 里时，进那个 section。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[candidate(1, "良渚文化", explanation="解释。")],
        sections=[
            {
                "sid": "s01",
                "title": "第 1 章 母题",
                "level": 1,
                "page_start": 1,
                "page_end": 10,
                "sort_order": 1,
            },
            {
                "sid": "s02",
                "title": "第 1 章 母题 | 第一节 起源",
                "level": 2,
                "page_start": 5,
                "page_end": 10,
                "sort_order": 2,
            },
        ],
    )

    summary = select(work)

    assert summary["cards"] == 1
    assert WorkDir(work).cards()[0]["sid"] == "s01"
