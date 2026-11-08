"""抽词 + 解释抽取器（契约 §4）与试抽闸门 2（契约 §6）的测试。

覆盖验收要求的那几条：

- 落偏移：多处出现、重叠取最长；
- evidence 找不到计数；
- 闸门强制（没有 ``trial.json.gate.status == "approved"`` 时全量抽取拒绝执行）；
- 反推 labels 与 SCHEMA.md 一致（``text[start:end] == surface``）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookcard import extract
from bookcard.cli import main
from bookcard.engine.softwrap import find_all_flexible
from bookcard.extract import locate_evidence

# --------------------------------------------------------------------------- 夹具


def _write_pages(book_dir: Path, pages: list[dict]) -> None:
    book_dir.mkdir(parents=True, exist_ok=True)
    with (book_dir / "pages.jsonl").open("w", encoding="utf-8") as fh:
        for page in pages:
            fh.write(json.dumps(page, ensure_ascii=False) + "\n")


def _write_sections(book_dir: Path, sections: list[dict]) -> None:
    book_dir.mkdir(parents=True, exist_ok=True)
    (book_dir / "sections.json").write_text(
        json.dumps(sections, ensure_ascii=False), encoding="utf-8"
    )


def _write_book(book_dir: Path, **meta) -> None:
    book_dir.mkdir(parents=True, exist_ok=True)
    payload = {"fp12": "abcdef123456", "title": "测试书", "subject": "历史"}
    payload.update(meta)
    (book_dir / "book.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _page(number: int, text: str, kind: str = "body") -> dict:
    return {
        "page": number,
        "printed_page": str(number),
        "text": text,
        "text_sha1": extract.text_sha1(text),
        "text_origin": "pdf_text",
        "width": 595.0,
        "height": 842.0,
        "kind": kind,
    }


@pytest.fixture
def book_dir(tmp_path: Path) -> Path:
    """一本两页、两个 section 的小书。"""
    target = tmp_path / "abcdef123456"
    _write_book(target)
    _write_pages(
        target,
        [
            _page(1, "良渚文化是长江下游的新石器时代晚期文化。"),
            _page(2, "秦始皇统一六国，建立了中央集权制度。"),
        ],
    )
    _write_sections(
        target,
        [
            {
                "sid": "s01",
                "title": "第1课",
                "level": 2,
                "page_start": 1,
                "page_end": 1,
                "sort_order": 1,
            },
            {
                "sid": "s02",
                "title": "第2课",
                "level": 2,
                "page_start": 2,
                "page_end": 2,
                "sort_order": 2,
            },
        ],
    )
    return target


# --------------------------------------------------------------------------- 落偏移


def test_place_items_multi_occurrence_每处都落偏移():
    """同一个词出现多处，每处都落一个 item，解释只挂第一处。"""
    text = "光合作用是光合作用的基础。"
    keyword = extract.Keyword(
        front="光合作用",
        type="term",
        back="n. 光合作用；植物把光能转成化学能的过程。",
        learn_value=3,
    )

    items, not_in_text = extract.place_items(text, [keyword])

    assert not_in_text == 0
    assert [(item["start"], item["end"]) for item in items] == [(0, 4), (5, 9)]
    # 解释和依据只挂第一处。
    assert items[0]["back"] is not None
    assert items[1]["back"] is None


def test_place_items_overlap_取最长():
    """重叠的 span 保留最长的（SCHEMA.md 的落位规则）。"""
    text = "这是一件光合作用的事情。"
    keywords = [
        extract.Keyword(
            front="光合",
            type="term",
            back="光合作用的一部分。",
            learn_value=2,
        ),
        extract.Keyword(
            front="光合作用",
            type="term",
            back="植物利用光能的过程。",
            learn_value=3,
        ),
    ]

    items, not_in_text = extract.place_items(text, keywords)

    assert not_in_text == 0
    assert [item["front"] for item in items] == ["光合作用"]
    assert items[0]["start"] == 4
    assert items[0]["end"] == 8
    # 被更长的词盖住的那个，解释也跟着长的走。
    assert items[0]["back"] == "植物利用光能的过程。"


def test_place_items_equal_length_取靠前的():
    """等长的重叠 span 保留靠前的那个（"乙甲" 被 "甲乙" 盖掉）。"""
    text = "甲乙甲乙"
    keywords = [
        extract.Keyword(
            front="甲乙", type="term", back="前", learn_value=2
        ),
        extract.Keyword(
            front="乙甲", type="term", back="后", learn_value=2
        ),
    ]
    items, _ = extract.place_items(text, keywords)
    # "甲乙" 在 0 和 2 两处，互不重叠，都留；"乙甲" 在 1 和 3，与 "甲乙" 重叠，丢掉。
    assert [item["front"] for item in items] == ["甲乙", "甲乙"]
    assert [(item["start"], item["end"]) for item in items] == [(0, 2), (2, 4)]
    # 解释只挂在该词第一处。
    assert items[0]["back"] == "前"
    assert items[1]["back"] is None


def test_place_items_not_in_text_计数():
    """找不到原文的 surface 丢弃并计数。"""
    text = "这是一句正文。"
    keywords = [
        extract.Keyword(
            front="不在原文里的词",
            type="term",
            back="编的",
            learn_value=2,
        )
    ]

    items, not_in_text = extract.place_items(text, keywords)

    assert items == []
    assert not_in_text == 1


def test_locate_evidence_空白折叠后仍能定位():
    """模型把换行压成空格时，依据句仍能定位到原文里的真实区间。"""
    text = "良渚文化\n以玉器著称。"
    located = extract.locate_evidence(text, "良渚文化 以玉器著称。")
    assert located is not None
    start, end = located
    assert text[start:end] == "良渚文化\n以玉器著称。"


def test_locate_evidence_换行直接接上也能定位():
    """模型把换行直接接上（不留空格）时，依据句仍能定位——教材正文换行很常见。"""
    text = "新石器时代是指以\n磨制方法制作石器的时代。"
    located = extract.locate_evidence(text, "新石器时代是指以磨制方法制作石器的时代。")
    assert located is not None
    start, end = located
    assert text[start:end] == "新石器时代是指以\n磨制方法制作石器的时代。"


def test_locate_evidence_原文里没有_返回None():
    """依据句原文里真没有 → 返回 None（计 bad_evidence）。"""
    assert extract.locate_evidence("这是一句正文。", "完全另一句话") is None
    assert extract.locate_evidence("这是一句正文。", None) is None


def test_place_items_span_满足_text_start_end_等于_surface():
    """落下来的偏移必须满足 SCHEMA.md 的不变量。"""
    text = "秦始皇统一六国，建立了中央集权制度。"
    keywords = [
        extract.Keyword(
            front="秦始皇",
            type="name",
            back="秦朝的开国皇帝。",
            learn_value=3,
        ),
        extract.Keyword(
            front="中央集权制度",
            type="term",
            back="中央权力集中的政治制度。",
            learn_value=2,
        ),
    ]
    items, _ = extract.place_items(text, keywords)
    for item in items:
        assert text[item["start"] : item["end"]] == item["front"]


# --------------------------------------------------------------------------- 跨行落位


def test_find_all_flexible_跨行的词也找得到():
    """PDF 的硬折行不该把词判成 ``not_in_text``。"""
    text = "……研究量子\n力学的……"

    hits = extract.find_all_flexible(text, "量子力学")

    assert len(hits) == 1
    start, end = hits[0]
    assert text[start:end].replace("\n", "") == "量子力学"


def test_place_items_跨行的词落位():
    """跨行的词落出 item（偏移跨 ``\\n``），``not_in_text`` 为 0。"""
    text = "……研究量子\n力学的……"
    keywords = [
        extract.Keyword(
            front="量子力学",
            type="term",
            back="描述微观物质运动规律的物理学分支。",
            learn_value=3,
        )
    ]

    items, not_in_text = extract.place_items(text, keywords)

    assert not_in_text == 0
    assert len(items) == 1
    item = items[0]
    assert text[item["start"] : item["end"]].replace("\n", "") == "量子力学"


def test_find_all_flexible_英文跨行按空格算():
    """拉丁字母之间的折行算一个空格：``hard\\nline`` 匹配 ``hard line``。"""
    assert extract.find_all_flexible("hard\nline", "hard line") == [(0, 9)]
    # 不猜断词：``hardline`` 匹配不上。
    assert extract.find_all_flexible("hard\nline", "hardline") == []


def test_find_all_flexible_精确命中不走放宽():
    """精确命中存在时，结果与逐字找完全一致（不放宽匹配）。"""
    text = "量子力学很重要。量子\n力学也有意义。"

    hits = extract.find_all_flexible(text, "量子力学")

    assert hits == [(0, 4)]


# --------------------------------------------------------------------------- 切段


def test_build_segments_只抽三种页():
    """只有 body / exercise / appendix 的页进抽取。"""
    pages = [
        extract.Page(page=1, printed_page="1", text="正文页", text_sha1="x", kind="body"),
        extract.Page(page=2, printed_page="2", text="练习页", text_sha1="x", kind="exercise"),
        extract.Page(page=3, printed_page="3", text="附录页", text_sha1="x", kind="appendix"),
        extract.Page(page=4, printed_page="4", text="目录页", text_sha1="x", kind="toc"),
        extract.Page(page=5, printed_page="5", text="封面", text_sha1="x", kind="cover"),
        extract.Page(page=6, printed_page="6", text="前言", text_sha1="x", kind="front"),
    ]
    sections = [
        extract.Section(
            sid="s01", title="第1课", level=2, page_start=1, page_end=6, sort_order=1
        )
    ]
    segments = extract.build_segments(pages, sections, "abcdef123456")
    assert [segment.page for segment in segments] == [1, 2, 3]


def test_build_segments_长页切块():
    """超过 1500 字的页按段落切块，chunk 从 1 起，seg_id 追加 c<块号>。"""
    long_text = "\n".join(["这是第一段文字。" * 40 for _ in range(6)])
    pages = [extract.Page(page=1, printed_page="1", text=long_text, text_sha1="x", kind="body")]
    sections = [
        extract.Section(
            sid="s01", title="第1课", level=2, page_start=1, page_end=1, sort_order=1
        )
    ]
    segments = extract.build_segments(pages, sections, "abcdef123456")
    assert len(segments) > 1
    assert segments[0].chunk == 1
    assert segments[0].seg_id == "book:abcdef123456:p0001c1"
    assert segments[0].seg_id.startswith("book:abcdef123456:p0001")
    # 每块都不超过 1500 字。
    assert all(len(segment.text) <= 1500 for segment in segments)


def test_build_segments_不切块的页_chunk_是零():
    """不切块的页：chunk = 0，seg_id 不带 c 后缀。"""
    pages = [extract.Page(page=7, printed_page="7", text="短正文", text_sha1="x", kind="body")]
    sections = [
        extract.Section(
            sid="s01", title="第1课", level=2, page_start=1, page_end=10, sort_order=1
        )
    ]
    segments = extract.build_segments(pages, sections, "abcdef123456")
    assert segments[0].chunk == 0
    assert segments[0].seg_id == "book:abcdef123456:p0007"


# --------------------------------------------------------------------------- 挑卡


def test_select_cards_去重_丢低价值_按规则排序():
    """§5 挑选规则：去重、丢 learn_value == 1、无解释不做卡。"""
    rows = [
        {
            "seg_id": "book:abcdef123456:p0001",
            "page": 1,
            "sid": "s01",
            "items": [
                {
                    "surface": "良渚文化",
                    "type": "name",
                    "start": 0,
                    "end": 4,
                    "explanation": "长江下游的新石器时代晚期文化。",
                    "evidence": {"start": 0, "end": 4},
                    "learn_value": 3,
                },
                {
                    "surface": "方法",
                    "type": "term",
                    "start": 5,
                    "end": 7,
                    "explanation": "一种做法。",
                    "evidence": None,
                    "learn_value": 1,
                },
                {
                    "surface": "没有解释",
                    "type": "term",
                    "start": 8,
                    "end": 12,
                    "explanation": None,
                    "evidence": None,
                    "learn_value": 3,
                },
            ],
        },
        {
            "seg_id": "book:abcdef123456:p0002",
            "page": 2,
            "sid": "s02",
            "items": [
                {
                    "surface": "良渚文化",
                    "type": "name",
                    "start": 0,
                    "end": 4,
                    "explanation": "重复出现的解释。",
                    "evidence": None,
                    "learn_value": 2,
                }
            ],
        },
    ]

    cards = extract.select_cards(rows, "abcdef123456")

    assert [card.front for card in cards] == ["良渚文化"]
    # 只留第一次出现（最早页），learn_value 取最大值。
    assert cards[0].page == 1
    assert cards[0].learn_value == 3
    assert cards[0].back == "长江下游的新石器时代晚期文化。"


def test_norm_front_归一化():
    """norm(front)：NFKC、去首尾空白、英文小写。"""
    assert extract.norm_front("  ＰＨＯＴＯ  ") == "photo"
    assert extract.norm_front("良渚文化") == "良渚文化"


def test_断点续跑_同一键不重复抽(book_dir: Path, monkeypatch):
    """同一 (seg_id, text_sha1, extractor) 已经抽过就跳过。"""
    calls: list[str] = []

    def _fake(system, user, **kwargs):
        calls.append(user)
        return json.dumps({"keywords": []}, ensure_ascii=False)

    monkeypatch.setattr(extract.llm, "complete", _fake)

    assert main(["extract", str(book_dir), "--sections", "s01"]) == 0
    assert main(["extract", str(book_dir), "--sections", "s01"]) == 0
    assert len(calls) == 1


def test_只抽指定section(book_dir: Path, monkeypatch):
    """--sections 只抽指定的 section。"""
    calls: list[str] = []

    def _fake(system, user, **kwargs):
        calls.append(user)
        return json.dumps({"keywords": []}, ensure_ascii=False)

    monkeypatch.setattr(extract.llm, "complete", _fake)

    assert main(["extract", str(book_dir), "--sections", "s02"]) == 0
    assert len(calls) == 1
    assert "第2课" in calls[0]


#: 一页目录：条目行 + 一堆孤零零的页码（页码行占非空行的一半）。
TOC_PAGE_TEXT = (
    "第一章 打开物理世界的大门\n"
    "第一节 走进神奇\n"
    "第二节 探索之路\n"
    "第三章 声的世界\n"
    "第四节 超声与次声\n"
    "52\n83\n100\n124\n141"
)

#: 一页普通正文。
BODY_PAGE_TEXT = "良渚文化是长江下游的新石器时代晚期文化，代表了当时的制玉工艺。"


def _trial_pages(spec: list[tuple[int, str, str]]) -> list[extract.Page]:
    """按 ``(页码, kind, 文字)`` 造几页。"""
    return [
        extract.Page(
            page=number,
            printed_page=None,
            text=text,
            text_sha1=extract.text_sha1(text),
            kind=kind,
        )
        for number, kind, text in spec
    ]


# --------------------------------------------------------------------------- 提示词


def test_parse_prompt_版本与占位符():
    """提示词文件切成 system / user 两段，版本号取自文件名。"""
    prompt_path = extract.default_prompt_path()
    prompt = extract.parse_prompt(prompt_path)
    assert prompt.version == "ex-v2"
    assert "keywords" in prompt.system
    rendered = prompt.render(
        book_title="中外历史纲要",
        subject="历史",
        section_title="第1课",
        page=7,
        text="正文",
    )
    assert "书名：中外历史纲要" in rendered
    assert "正文" in rendered
    assert "{book_title}" not in rendered


def test_提示词正文没被改过():
    """提示词是维护者的原文件，本单不许改（改动要升版本号）。"""
    prompt_path = extract.default_prompt_path()
    raw = prompt_path.read_text(encoding="utf-8")
    assert "ex-v2" in raw
    assert "===USER===" in raw
    # 提示词跟着包走（契约 §1）：路径在包内，不在仓库根的 prompts/ 下。
    assert prompt_path.name == "extract_v2.md"
    assert prompt_path.parent.name == "prompts"
    # 提示词里出现的占位符必须都能被 render 替换掉。
    assert set(extract.PLACEHOLDER_RE.findall(raw)) <= {
        "book_title",
        "subject",
        "section_title",
        "page",
        "text",
    }


# --------------------------------------------------------------------- 兼容码位 / 坏字


def test_兼容表意文字_模型写标准码位也命中():
    """有些 PDF 的文本层用兼容码位（理=U+F9E4），模型写的是标准码位（U+7406）。

    逐字、折叠空白、全去空白都找不到 → 卡会被静默丢掉。
    """
    text = "能够感知其环境（包括数字和物理环境），根据这\n些感知和预设。"
    compat = text.replace("理", "理")
    assert find_all_flexible(compat, "物理环境") == [(13, 17)]
    assert locate_evidence(compat, "物理环境") == (13, 17)


def test_连字_ﬂ归一成fl也命中():
    text = "反思（Reﬂection）：智能体能够自我批评其行为和输出。"
    assert find_all_flexible(text, "Reflection")
    assert locate_evidence(text, "反思（Reflection）：智能体能够自我批评其行为和输出。")


def test_坏字_原文标点映坏时证据也能对上():
    """PDF 把「（」映成了 U+054B「Ջ」，配对的「）」还整个丢了。

    模型抄的是正常标点 —— 逐字对不上，但那段本来就在。
    """
    text = "涵盖从基础（如顺序操作结构化Ջ 提示链、外部交互Ջ 工具使用）到高\n级主题。"
    evidence = "涵盖从基础（如顺序操作结构化（提示链）、外部交互（工具使用））到高级主题。"
    located = locate_evidence(text, evidence)
    assert located is not None
    assert "提示链" in text[located[0] : located[1]]
