"""agentflow（``docs/mcp-v1.md`` §2）测试用的夹具。

一本 10 页的小 PDF（1 页封面 + 3 个 section × 3 页正文），从 ``book_add``
一路走到 ``pack``。

**模型回答写死**在这里：抽词的回答按页给（:data:`EXTRACT_ANSWERS`），
审卡的回答按卡给（:func:`judge_answer`）。引擎不调模型，也不读 key。
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf

#: 夹具书的页面文字（1 页封面 + 9 页正文）。
PAGE_TEXTS: dict[int, str] = {
    1: "中华文明纲要",
    2: "第1课 中华文明的起源\n良渚文化位于长江下游，是新石器时代晚期的文化遗存。",
    3: "良渚文化的城址和水利系统展现了早期国家的形态。",
    4: "二里头遗址被视为早期国家的都城遗址候选之一。",
    5: "第2课 早期国家\n二里头遗址发现了宫殿建筑群和青铜器。",
    6: "良渚文化的玉器制作水平很高，代表了当时的工艺。",
    7: "二里头遗址的碳十四测年把年代定在公元前十八世纪。",
    8: "第3课 二里头遗址\n早期国家的形态在二里头遗址的宫殿基址上表现得最清楚。",
    9: "二里头遗址的宫殿基址是成组的大型夯土建筑台基。",
    10: "良渚文化的水利系统与城址一起构成了早期国家的雏形。",
}

#: 目录条目（契约 §3）——标题都出现在对应页的正文里，闸门 1 才过得去。
TOC_ENTRIES: list[dict] = [
    {"level": 1, "title": "第1课 中华文明的起源", "page": 2},
    {"level": 1, "title": "第2课 早期国家", "page": 5},
    {"level": 1, "title": "第3课 二里头遗址", "page": 8},
]

#: 正文首页 / 末页。
FIRST_BODY_PAGE = 2
LAST_BODY_PAGE = 10

PAGE_W = 595
PAGE_H = 842
CJK_FONT = "china-s"


#: 抽词的回答（写死的 JSON），按页码给。
#:
#": 解释 15–60 字。引擎只把关一件事：词必须真的出现在那一页里。
EXTRACT_ANSWERS: dict[int, str] = {
    2: json.dumps(
        {
            "keywords": [
                {
                    "front": "良渚文化",
                    "type": "name",
                    "back": (
                    "长江下游新石器时代晚期的文化遗存，"
                    "以城址、水利系统和玉器著称。"
                ),
                    "learn_value": 3,
                }
            ]
        },
        ensure_ascii=False,
    ),
    3: json.dumps(
        {
            "keywords": [
                {
                    "front": "早期国家",
                    "type": "concept",
                    "back": (
                        "已经形成政治权力与统治秩序的社会形态，"
                        "城址与水利工程是它的重要标志。"
                    ),
                    "learn_value": 3,
                }
            ]
        },
        ensure_ascii=False,
    ),
    4: json.dumps(
        {
            "keywords": [
                {
                    "front": "都城遗址",
                    "type": "term",
                    "back": "具备都城性质的遗址，是判断早期国家形成时间的实物依据之一。",
                    "learn_value": 3,
                }
            ]
        },
        ensure_ascii=False,
    ),
    5: json.dumps(
        {
            "keywords": [
                {
                    "front": "宫殿建筑群",
                    "type": "term",
                    "back": (
                        "成组的大型夯土建筑台基，"
                        "与青铜器一起构成二里头遗址的代表性发现。"
                    ),
                    "learn_value": 3,
                }
            ]
        },
        ensure_ascii=False,
    ),
    6: json.dumps(
        {
            "keywords": [
                {
                    "front": "玉器",
                    "type": "term",
                    "back": (
                        "良渚文化的玉器制作工艺，"
                        "反映了长江下游当时手工业达到的技术水平。"
                    ),
                    "learn_value": 2,
                }
            ]
        },
        ensure_ascii=False,
    ),
    7: json.dumps(
        {
            "keywords": [
                {
                    "front": "碳十四测年",
                    "type": "term",
                    "back": (
                        "用放射性碳十四测定遗物年代的方法，"
                        "据此把二里头遗址的年代定在公元前十八世纪。"
                    ),
                    "learn_value": 3,
                }
            ]
        },
        ensure_ascii=False,
    ),
    8: json.dumps(
        {
            "keywords": [
                {
                    "front": "宫殿基址",
                    "type": "term",
                    "back": "遗址上宫殿建筑的土台基址，是研究早期国家权力结构的实物材料。",
                    "learn_value": 3,
                }
            ]
        },
        ensure_ascii=False,
    ),
    9: json.dumps(
        {
            "keywords": [
                {
                    "front": "夯土建筑台基",
                    "type": "term",
                    "back": "用夯土逐层夯实筑成的大型建筑基础，是二里头宫殿建筑的典型做法。",
                    "learn_value": 2,
                }
            ]
        },
        ensure_ascii=False,
    ),
    10: json.dumps(
        {
            "keywords": [
                {
                    "front": "水利系统",
                    "type": "term",
                    "back": "良渚文化的治水设施，与城址一起被视为早期国家形态的雏形。",
                    "learn_value": 3,
                }
            ]
        },
        ensure_ascii=False,
    ),
}


#: 第 2 页的**另一份**抽词回答（两个词）——用来测「同一个 seg 重交时覆盖」。
EXTRACT_ANSWER_TWO_WORDS: str = json.dumps(
    {
        "keywords": [
            {
                "front": "良渚文化",
                "type": "name",
                "back": (
                    "长江下游新石器时代晚期的文化遗存，"
                    "以城址、水利系统和玉器著称。"
                ),
                "learn_value": 3,
            },
            {
                "front": "新石器时代",
                "type": "term",
                "back": "以磨制石器与彩陶为特征、距今约一万年前后的史前文化阶段。",
                "learn_value": 3,
            },
        ]
    },
    ensure_ascii=False,
)


def judge_answer(count: int, *, reject_first: bool = False) -> str:
    """审卡的回答（写死的 JSON）：每张卡一条判定。

    ``reject_first=True`` 时第一张卡判成 ``reject``——用来测阈值不够的情况。
    """
    verdicts = []
    for index in range(count):
        if reject_first and index == 0:
            verdicts.append(
                {
                    "index": index,
                    "kw": "reject",
                    "expl": 1,
                    "fatal": False,
                    "note": "不值得单独记",
                }
            )
            continue
        verdicts.append(
            {
                "index": index,
                "kw": "accept",
                "expl": 2,
                "fatal": False,
                "note": "词和依据都对得上",
            }
        )
    return json.dumps({"verdicts": verdicts, "missed": []}, ensure_ascii=False)


def make_pdf(path: Path, page_count: int = 10, *, bookmarks: bool = False) -> Path:
    """造一本带文字的 PDF（正文 + 封面）。

    ``bookmarks=True`` 时把 :data:`TOC_ENTRIES` 写成 PDF 读书签——这样
    ``build_toc`` 能从书签拿到目录条目，闸门 1 的四条判据都过得去
    （标题就在对应页的正文里）。不写书签时这本书没有目录，
    ``build_toc`` 只会判 ``rejected``。
    """
    doc = pymupdf.open()
    for index in range(page_count):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        text = PAGE_TEXTS.get(index + 1, "")
        if text:
            # 「china-s」一次最多 48 个汉字，夹具的句子都短。
            for line_no, line in enumerate(text.split("\n")):
                page.insert_text((72, 100 + line_no * 18), line, fontsize=12, fontname=CJK_FONT)
    if bookmarks:
        doc.set_toc([[entry["level"], entry["title"], entry["page"]] for entry in TOC_ENTRIES])
    doc.save(str(path))
    doc.close()
    return path


def write_toc(book_dir: Path) -> Path:
    """写一份 ``toc.json``（目录 + 闸门 1 的检查结果）。

    真实书里这一步是 ``bookcard toc`` 干的（目录来自 PDF 的书签或印刷目录）；
    夹具书没有书签，所以直接按契约 §3 的形状写。
    """
    payload = {
        "source": "printed",
        "entries": TOC_ENTRIES,
        "checks": {
            "pages_monotonic": True,
            "first_body_page": FIRST_BODY_PAGE,
            "covered_pages": [FIRST_BODY_PAGE, LAST_BODY_PAGE],
            "gaps": [],
        },
        "gate": {
            "status": "pending",
            "approved_by": "",
            "at": "",
            "reasons": [],
        },
    }
    path = book_dir / "toc.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
