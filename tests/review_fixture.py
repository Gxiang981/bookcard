"""手写夹具：一本书的工作目录（3 个 section、30 张卡、各种 agent 判定）。

数据是手写的：正文句子、关键词、解释、依据句都是定死的中文教材体，
用来给 ``bookcard review-html`` 生成一个能看出问题（高亮、判定、分层抽样、
汇总数字）的审核页，也给单测当输入。

文件布局照 ``docs/contract-v1.md`` §0：

    <dir>/book.json
    <dir>/sections.json
    <dir>/pages.jsonl
    <dir>/cards.jsonl
    <dir>/review/agent.jsonl
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Any

FP = "3f9a1c2e7b10"
FP12 = FP[:12]

BOOK: dict[str, Any] = {
    "fp": FP,
    "fp12": FP12,
    "file_name": "中外历史纲要（上）.pdf",
    "nas_path": "~/books/history/中外历史纲要（上）.pdf",
    "page_count": 206,
    "title": "中外历史纲要（上）",
    "subject": "历史",
    "stage": "senior",
    "grade": "",
    "volume": "上",
    "publisher": "人民教育出版社",
    "edition": "2019",
    "language": "zh",
}

SECTIONS: list[dict[str, Any]] = [
    {
        "sid": "s01",
        "title": "第1课 中华文明的起源与早期国家",
        "level": 2,
        "page_start": 10,
        "page_end": 13,
        "sort_order": 1,
    },
    {
        "sid": "s02",
        "title": "第2课 春秋战国的社会变化",
        "level": 2,
        "page_start": 14,
        "page_end": 16,
        "sort_order": 2,
    },
    {
        "sid": "s03",
        "title": "第3课 从汉至元政治制度的演变",
        "level": 2,
        "page_start": 17,
        "page_end": 18,
        "sort_order": 3,
    },
]

# (section 序号, 页码, 印刷页码, 关键词, type, 解释, 依据句, agent 判定, agent 理由)
#
# agent 判定是 (kw, evidence, expl, fatal, note)；None 表示 agent 没审这张卡。
ENTRIES: list[tuple[Any, ...]] = [
    (
        0,
        10,
        "2",
        "良渚文化",
        "name",
        "长江下游的新石器时代晚期文化，以大型城址、水利系统和玉器著称，被视为早期国家形态的代表。",
        "良渚文化距今约五千多年，其城址与水利系统被视为早期国家形态的代表。",
        ("accept", "ok", 2, False, "原文逐字出现，解释准确，依据句确实在讲它。"),
    ),
    (
        0,
        10,
        "2",
        "二里头遗址",
        "name",
        "位于河南偃师的遗址，出土宫殿基址与青铜礼器，被认为与夏代都城相关。",
        "二里头遗址发现了宫殿基址与青铜礼器，被认为与夏代都城相关。",
        ("accept", "ok", 2, False, "解释有增补但不出原文，判 2 分。"),
    ),
    (
        0,
        10,
        "2",
        "宗法制",
        "concept",
        "以血缘关系的亲疏分配政治权利的制度，是早期国家维系统治的重要安排。",
        "宗法制以血缘关系维系政治秩序，是早期国家的重要制度安排。",
        ("accept", "ok", 1, False, "解释基本对，但啰嗦重复依据句，判 1 分。"),
    ),
    (
        0,
        11,
        "3",
        "分封制",
        "concept",
        "周代把土地和人民分封给宗亲与功臣，建立诸侯国以拱卫王室。",
        "分封制把土地和人民分封给宗亲功臣，以巩固周王朝的统治。",
        ("boundary", "ok", 2, False, "取了全称，但教材里也单说「分封」，算边界情况。"),
    ),
    (
        0,
        11,
        "3",
        "井田制",
        "term",
        "把土地划成九块的土地制度，中间一块为公田，其余为私田。",
        "井田制把土地划成九块，以中间一块供公田，四周为私田。",
        ("accept", "ok", 0, False, "解释只是把依据句换个说法，没有新信息，判 0 分。"),
    ),
    (
        0,
        11,
        "3",
        "青铜器",
        "term",
        "商周时期以青铜铸造的礼器与工具，象征权力与等级。",
        "商代青铜器的铸造工艺达到高峰，成为权力与礼制的象征。",
        ("reject", "ok", 2, False, "泛称，任何朝代都能用，不值得单独做卡。"),
    ),
    (
        0,
        12,
        "4",
        "甲骨文",
        "term",
        "刻在龟甲兽骨上的文字，是目前所见最早的成熟汉字体系。",
        "甲骨文是刻在龟甲兽骨上的文字，记录了商王的占卜与政务。",
        ("accept", "ok", 2, False, "关键词清楚，依据句对得上。"),
    ),
    (
        0,
        12,
        "4",
        "都江堰",
        "name",
        "战国时期李冰主持修建的水利工程，兼具防洪与灌溉之利，沿用至今。",
        "都江堰由李冰主持修建，兼具防洪与灌溉之利，延续至今。",
        ("accept", "ok", 2, False, "标准专名，解释补充了「战国」和「沿用至今」，没问题。"),
    ),
    (
        0,
        12,
        "4",
        "铁制农具",
        "term",
        "以铁打造的耕作工具，春秋时期推广后大幅提高了土地产出。",
        "春秋时期铁制农具的推广显著提高了土地的生产效率。",
        ("accept", "missing", 2, False, "解释没问题，但这条没给依据句，记 missing。"),
    ),
    (
        0,
        13,
        "5",
        "牛耕",
        "term",
        "用牛牵引农具耕作，普及后个体小农的耕作能力明显提升。",
        "牛耕的普及使个体小农的耕作能力大幅提升。",
        ("accept", "ok", 2, False, "合格。"),
    ),
    (
        0,
        13,
        "5",
        "士族",
        "name",
        "魏晋南北朝时期凭借门第与清议左右政治的豪族群体。",
        "魏晋南北朝的士族凭借门第与清议主导政治与社会。",
        ("accept", "ok", 2, False, "合格。"),
    ),
    (
        0,
        13,
        "5",
        "九品中正制",
        "concept",
        "魏晋时期按家世划分品级选官的制度，初期整顿吏治，后期流于形式。",
        "九品中正制以家世定品级，初期稳定秩序，后期流于形式。",
        ("accept", "wrong", 2, False, "依据句讲的是选官标准，不是制度的兴衰，依据对不上。"),
    ),
    (
        1,
        14,
        "6",
        "商鞅变法",
        "name",
        "战国时期秦国推行的一系列改革，奖励耕战、废井田，奠定了秦国强盛的基础。",
        "商鞅变法奖励耕战，废井田、开阡陌，奠定了秦国的制度基础。",
        ("accept", "ok", 2, False, "合格。"),
    ),
    (
        1,
        14,
        "6",
        "军功爵制",
        "concept",
        "按战功授爵的制度，打击了旧贵族的特权，是战国变法的常见内容。",
        "军功爵制按战功授爵，削弱了旧贵族的特权。",
        ("accept", "ok", 2, False, "合格。"),
    ),
    (
        1,
        14,
        "6",
        "什伍连坐",
        "term",
        "把百姓编成什伍并互相监督连坐的治安制度，加强国家对基层的控制。",
        "什伍连坐把百姓编组互相监督，强化了国家对社会基层的控制。",
        ("accept", "ok", 2, False, "合格。"),
    ),
    (
        1,
        14,
        "6",
        "户籍制度",
        "term",
        "登记人口与土地以征收赋税、征发徭役的国家制度。",
        "秦朝以户籍制度登记人口与土地，为赋税和徭役提供依据。",
        ("accept", "ok", 1, False, "解释太短，且没有点出「秦朝」，判 1 分。"),
    ),
    (
        1,
        15,
        "7",
        "崇文抑武",
        "concept",
        "宋代重文轻武的国策，以文官治国换取政权内部的稳定。",
        "宋朝推行崇文抑武之策，以文官治国换取政局稳定。",
        ("reject", "ok", 2, False, "四字是政策概括，脱离原文单看太泛，不做卡。"),
    ),
    (
        1,
        15,
        "7",
        "榷场",
        "term",
        "宋代由官府管理边境贸易的市场，交换茶马与绢帛。",
        "宋代的榷场是官府管理边境贸易的市场。",
        ("accept", "ok", 2, False, "合格。"),
    ),
    (
        1,
        15,
        "7",
        "辽夏金",
        "name",
        "与北宋并立的辽、夏、金三个政权，构成了当时的周边格局。",
        "辽夏金三个政权与北宋并立，形成了多边格局。",
        ("accept", "ok", 2, False, "合格。"),
    ),
    (
        1,
        16,
        "8",
        "行省",
        "name",
        "元朝在地方设置的中书省派出机构，奠定了后世行政区划的基础。",
        "元朝在地方设行中书省，行省制度奠定了后世行政区划的基础。",
        ("accept", "ok", 2, False, "合格。"),
    ),
    (
        1,
        16,
        "8",
        "宣政院",
        "name",
        "元朝管理全国佛教事务与西藏地区军政事务的机构。",
        "元朝设宣政院管理全国佛教事务与西藏地区的军政事务。",
        ("accept", "ok", 2, False, "合格。"),
    ),
    (
        1,
        16,
        "8",
        "澎湖巡检司",
        "name",
        "元朝设置的管理澎湖与琉球的机构，是历史上中央政府正式管辖台湾的开始。",
        "元朝设澎湖巡检司管理澎湖和琉球，这是历史上对台湾的正式管辖。",
        ("accept", "ok", 2, False, "合格。"),
    ),
    (
        2,
        17,
        "9",
        "三省六部制",
        "concept",
        "把决策权分给中书、门下、尚书三省并由六部执行，以提高行政效能。",
        "三省六部制将决策权分散于中书、门下和尚书三省，以提高行政效能。",
        ("accept", "ok", 2, False, "合格。"),
    ),
    (
        2,
        17,
        "9",
        "科举制",
        "concept",
        "以考试成绩选拔官员的制度，把读书人纳入官僚体系，扩大了统治基础。",
        "科举制以考试选官，把读书人纳入官僚体系，扩大了统治基础。",
        ("accept", "ok", 2, False, "合格。"),
    ),
    (
        2,
        17,
        "9",
        "尚书省",
        "name",
        "唐代执行政令的机构，下设吏户礼兵刑工六部分理政务。",
        "尚书省是执行政令的机构，下设六部分理政务。",
        ("accept", "ok", 2, False, "合格。"),
    ),
    (
        2,
        18,
        "10",
        "军机处",
        "name",
        "清朝初年设立的机构，军机大臣跪受笔录，君主专制进一步加强。",
        "军机处设立于清朝初年，军机大臣跪受笔录，皇权进一步加强。",
        ("boundary", "ok", 2, False, "「军机处」本身没毛病，但应该和「内阁」合看，暂记边界。"),
    ),
    (
        2,
        18,
        "10",
        "内阁",
        "name",
        "明朝中期以后协助皇帝处理政务的核心机构。",
        "明朝中期以后，内阁成为协助皇帝处理政务的核心机构。",
        ("accept", "ok", 2, False, "合格。"),
    ),
    (
        2,
        18,
        "10",
        "大一统",
        "concept",
        "秦朝建立的中国历史上第一个统一的多民族中央集权国家。",
        "秦朝建立了中国历史上第一个统一的多民族国家，实现了大一统。",
        ("accept", "ok", 2, False, "合格。"),
    ),
    (
        2,
        18,
        "10",
        "中央集权",
        "concept",
        "把权力集中到中央、对地方实行严格控制的政治体制。",
        "中央集权通过把权力集中于中央，强化了国家对地方的控制。",
        ("accept", "ok", 2, True, "解释说中央集权始于秦朝，与前一句矛盾，属事实错误。"),
    ),
    (
        2,
        18,
        "10",
        "君主专制",
        "concept",
        "皇权凌驾于一切制度之上、官僚也难以制约君主权力的状态。",
        "君主专制发展到顶峰时，皇权凌驾于一切制度之上。",
        None,
    ),
]


def _norm(text: str) -> str:
    """§5 的 norm：NFKC、去首尾空白、英文小写。"""
    return unicodedata.normalize("NFKC", text).strip().lower()


def content_fp(front: str, back: str) -> str:
    """§5 的 content_fp = sha256(norm(front) + '\\n' + norm(back))。"""
    payload = f"{_norm(front)}\n{_norm(back)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def build_demo_dir(dest: Path) -> Path:
    """在 ``dest`` 下铺出一套完整的工作目录，返回该目录。"""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    # ---- 页正文：同一页的卡片句子拼在一起，偏移按这个正文算 ----
    page_texts: dict[int, str] = {}
    for entry in ENTRIES:
        page = entry[1]
        sentence = entry[6]
        page_texts.setdefault(page, "")
        page_texts[page] += sentence

    pages: list[dict[str, Any]] = []
    for page in sorted(page_texts):
        text = page_texts[page]
        pages.append(
            {
                "page": page,
                "printed_page": next(str(e[2]) for e in ENTRIES if e[1] == page),
                "text": text,
                "text_sha1": hashlib.sha1(text.encode("utf-8")).hexdigest(),
                "text_origin": "pdf_text",
                "width": 595.0,
                "height": 842.0,
                "kind": "body",
            }
        )

    cards: list[dict[str, Any]] = []
    agent_rows: list[dict[str, Any]] = []
    for index, entry in enumerate(ENTRIES):
        section_index, page, printed_page, front, card_type, back, evidence, verdict = entry
        text = page_texts[page]
        kw_start = text.index(front)
        kw_end = kw_start + len(front)
        sid = SECTIONS[section_index]["sid"]
        card_id = f"{FP12}:{sid}:{content_fp(front, back)[:8]}"
        cards.append(
            {
                "card_id": card_id,
                "sid": sid,
                "front": front,
                "back": back,
                "type": card_type,
                "tags": ["历史", SECTIONS[section_index]["title"]],
                "sort_order": index,
                "source": {
                    "page": page,
                    "printed_page": printed_page,
                    "seg_id": f"book:{FP12}:p{page:04d}",
                    "start": kw_start,
                    "end": kw_end,
                    "evidence": evidence,
                    "verbatim": True,
                    "method": "llm",
                },
                "content_fp": content_fp(front, back),
            }
        )
        if verdict is not None:
            kw, evidence_verdict, expl, fatal, note = verdict
            agent_rows.append(
                {
                    "card_id": card_id,
                    "kw": kw,
                    "evidence": evidence_verdict,
                    "expl": expl,
                    "fatal": fatal,
                    "note": note,
                    "reviewer": "agent:agent",
                    "at": "2026-11-06T09:30:00+08:00",
                }
            )

    (dest / "book.json").write_text(
        json.dumps(BOOK, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (dest / "sections.json").write_text(
        json.dumps(SECTIONS, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_jsonl(dest / "pages.jsonl", pages)
    _write_jsonl(dest / "cards.jsonl", cards)
    _write_jsonl(dest / "review" / "agent.jsonl", agent_rows)
    return dest


if __name__ == "__main__":  # pragma: no cover - 手工生成夹具时用
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".tmp-demo")
    build_demo_dir(target)
    print(f"夹具已写到 {target}")
