#!/usr/bin/env python3
"""试抽交卷：写答案 → 逐字校验 → 提交。

校验：surface 和 evidence 都必须在该页原文里（evidence 允许引擎那两级退化：
空白折叠 / 空白全去掉）。校验不过就不交，直接报出来。
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

BOOK = "train/data/bookcard/books/94a9b8d6b27d"
EXTRACTOR = "agent:dsh:MiniMax-M3.1-Flash:ex-v1"

ANSWERS: dict[int, list[dict]] = {
    227: [
        {"surface": "二氧化氮", "type": "term",
         "evidence": "红棕色二氧化氮气体",
         "explanation": "红棕色有毒的气体，本页用它与空气对照，观察气体分子不停运动而发生的扩散。",
         "learn_value": 2},
        {"surface": "气体分子的运动", "type": "concept",
         "evidence": "图 11-15　气体分子的运动",
         "explanation": "气体分子永不停息地做无规则运动，表现为气体能自发扩散到整个容器中。",
         "learn_value": 2},
    ],
    228: [
        {"surface": "铅块", "type": "term",
         "evidence": "铅块能被拉开吗？",
         "explanation": "表面洁净的两块铅压紧后能承受很大拉力而不分开，用来说明分子间存在引力。",
         "learn_value": 2},
        {"surface": "针筒", "type": "term",
         "evidence": "利用针筒抽取半筒水",
         "explanation": "带活塞的筒状注射器，本页用它抽水并推压活塞，观察水能否被压缩。",
         "learn_value": 2},
        {"surface": "活塞", "type": "term",
         "evidence": "然后用力推入活塞",
         "explanation": "针筒内可往复推拉的塞子，推入时把筒内的液体压缩。",
         "learn_value": 2},
        {"surface": "液体分子的运动", "type": "concept",
         "evidence": "图 11-16　液体分子的运动",
         "explanation": "液体分子也在永不停息地运动，表现为墨水在水中逐渐扩散开来。",
         "learn_value": 2},
        {"surface": "分子之间存在作用力", "type": "concept",
         "evidence": "分子之间存在作用力吗",
         "explanation": "分子之间同时存在引力和斥力，二者共同决定物质的状态和性质。",
         "learn_value": 3},
    ],
    229: [
        {"surface": "固态", "type": "term",
         "evidence": "物质的状态常分为固态、液态和气态",
         "explanation": "物质的一种状态，分子间作用力较强，有确定的体积和形状，不能流动。",
         "learn_value": 2},
        {"surface": "液态", "type": "term",
         "evidence": "在液体中，分子间力的作用较弱",
         "explanation": "物质的一种状态，分子间作用力较弱，没有确定形状但占有一定体积，能够流动。",
         "learn_value": 2},
        {"surface": "气态", "type": "term",
         "evidence": "在气体中，分子间力的作用更弱了",
         "explanation": "物质的一种状态，分子间作用力最弱，分子能自由运动，既无形状也无确定体积。",
         "learn_value": 2},
        {"surface": "引力", "type": "term",
         "evidence": "（引力和斥力）",
         "explanation": "分子之间相互吸引的力，使物体很难被拉开。",
         "learn_value": 2},
        {"surface": "斥力", "type": "term",
         "evidence": "（引力和斥力）",
         "explanation": "分子之间相互排斥的力，使物体很难被压缩。",
         "learn_value": 2},
        {"surface": "无规则运动", "type": "concept",
         "evidence": "分子都在永不停息地做无规则运动",
         "explanation": "分子永不停息地做没有固定规律的杂乱运动，是扩散等现象的原因。",
         "learn_value": 2},
        {"surface": "分子动理论", "type": "term",
         "evidence": "物理学中称之为分子动理论",
         "explanation": "认为物质由大量分子组成、分子间存在作用力且永不停息做无规则运动的理论。",
         "learn_value": 3},
    ],
    230: [
        {"surface": "分子引力", "type": "term",
         "evidence": "可以用玻璃做分子引力实验吗",
         "explanation": "分子之间的吸引力，本页用它解释煤堆深处黑色物质扩散到地面的现象。",
         "learn_value": 2},
        {"surface": "扩散", "type": "term",
         "evidence": "扩散到地面的土层中了",
         "explanation": "分子不停做无规则运动，使不同物质彼此渗透的现象，如花香四溢。",
         "learn_value": 3},
        {"surface": "花香四溢", "type": "concept",
         "evidence": "解释花香四溢的现象",
         "explanation": "花香分子不断做无规则运动扩散到空气中，使人到处闻到香味的现象。",
         "learn_value": 2},
        {"surface": "王冕", "type": "name",
         "evidence": "元代画家及诗人王冕",
         "explanation": "元代画家兼诗人，以画梅闻名，书中引用了他的咏梅诗句。",
         "learn_value": 2},
    ],
    231: [
        {"surface": "地心说", "type": "term",
         "evidence": "提出了地心说",
         "explanation": "认为地球居于宇宙中心、其他星体绕地球运转的古代宇宙模型。",
         "learn_value": 2},
        {"surface": "日心说", "type": "term",
         "evidence": "提出“日心说”（图11-22）",
         "explanation": "认为太阳是宇宙中心、行星绕太阳运转的宇宙模型，能更简洁地描述行星运动。",
         "learn_value": 3},
        {"surface": "托勒密", "type": "name",
         "evidence": "托勒密在前人观测和",
         "explanation": "古希腊天文学家，在前人观测与理论基础上提出了地心说。",
         "learn_value": 2},
        {"surface": "文艺复兴运动", "type": "name",
         "evidence": "文艺复兴运动解放了人的思想",
         "explanation": "16 世纪中期欧洲的思想解放运动，促成人们进入科学探索宇宙的时代。",
         "learn_value": 2},
        {"surface": "哥白尼", "type": "name",
         "evidence": "哥白尼通过几十年的观察与分析",
         "explanation": "波兰天文学家，提出日心说，认为行星绕太阳做匀速圆周运动。",
         "learn_value": 2},
        {"surface": "匀速圆周运动", "type": "term",
         "evidence": "都绕太阳做匀速圆周运动",
         "explanation": "物体沿圆周以恒定速率运动的运动方式，哥白尼用它描述行星绕日运动。",
         "learn_value": 2},
        {"surface": "卫星", "type": "term",
         "evidence": "月球是地球的卫星",
         "explanation": "绕行星运行的天体，本书中指绕地球运行的月球。",
         "learn_value": 2},
        {"surface": "开普勒", "type": "name",
         "evidence": "开普勒（J. Kepler，1571—1630）根据前人的",
         "explanation": "德国天文学家，根据前人观测和研究提出了太阳系行星运动的三大定律。",
         "learn_value": 2},
        {"surface": "Kepler", "type": "name",
         "evidence": "开普勒（J. Kepler，1571—1630）",
         "explanation": "n. 开普勒的英文名，德国天文学家，提出行星运动三大定律。",
         "learn_value": 1},
        {"surface": "太阳系行星运动的三大定律", "type": "term",
         "evidence": "提出了太阳系行星运动的三大定律",
         "explanation": "开普勒总结的行星绕日运动规律，含轨道、面积和周期三大定律。",
         "learn_value": 3},
    ],
}


def page_text(page: int) -> str:
    for line in open(f"{BOOK}/pages.jsonl", encoding="utf-8"):
        row = json.loads(line)
        if row["page"] == page:
            return row["text"]
    raise KeyError(page)


def in_text(text: str, frag: str) -> bool:
    """engine 的两级退化：原样 → 空白折叠 → 空白全去。"""
    if frag in text:
        return True
    if re.sub(r"\s+", " ", text).find(re.sub(r"\s+", " ", frag)) >= 0:
        return True
    return re.sub(r"\s+", "", text).find(re.sub(r"\s+", "", frag)) >= 0


def main() -> int:
    bad = 0
    for page, items in ANSWERS.items():
        text = page_text(page)
        for it in items:
            for field in ("surface", "evidence"):
                if not in_text(text, it[field]):
                    print(f"✗ p{page:04d} {field} 不在原文里：{it[field]!r}")
                    bad += 1
            n = len(it["explanation"])
            if not (15 <= n <= 60):
                print(f"✗ p{page:04d} explanation 字数 {n} 不在 15–60：{it['surface']}")
                bad += 1
    if bad:
        print(f"校验没过 {bad} 处，不交卷")
        return 1

    for page, items in ANSWERS.items():
        payload = {"keywords": items}
        path = Path(f"/tmp/ans-p{page:04d}.json")
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        seg = f"book:94a9b8d6b27d:p{page:04d}"
        r = subprocess.run(
            [".venv/bin/python", "-m", "bookcard", "flow", "extract-submit", BOOK,
             "--seg-id", seg, "--output-file", str(path), "--extractor", EXTRACTOR],
            capture_output=True, text=True,
        )
        try:
            d = json.loads(r.stdout)
        except json.JSONDecodeError:
            print(f"p{page:04d} 提交失败：{r.stdout[:200]} {r.stderr[:200]}")
            continue
        print(f"p{page:04d} → {'ok' if d.get('ok') else 'ERR ' + str(d.get('error'))}"
              f" | items {d.get('items')} | dropped {d.get('dropped')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
