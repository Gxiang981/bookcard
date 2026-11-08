#!/usr/bin/env python3
"""按审卡结果修正抽词答案并重交。

引擎的规则是「同一个 seg 重复交卷，后交的覆盖先交的」，所以审完发现问题，
最干净的修法是**重交这一段的修正版答案**——而不是手改 candidates.jsonl。

本脚本做两件事：
1. **删**：kw=reject 或 fatal=true 的词，从该段答案里去掉；
2. **改**：kw=boundary 且能机械修正的词，换成书上的完整固定说法；
3. **补**：审卡报出的漏标词里，挑真正值得单独记的，按原文补进对应段。

补的词必须逐字在原文里（用引擎的 locate_evidence 同款口径校验），
explanation 15–60 字。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "/src")

from bookcard.extract import card_id_for  # noqa: E402

BOOK = Path("train/data/bookcard/books/94a9b8d6b27d")
SEG_DIR = Path("/tmp/segments")
CHUNK_DIR = Path("/tmp/chunks")
EXTRACTOR = "agent:dsh:MiniMax-M3.1-Flash:ex-v1"

# ── 补卡：审卡报出的漏标词里挑出来的（核心术语，不与已有卡重复） ──
# surface -> (页码, type, evidence, explanation, learn_value)
ADDS: dict[str, tuple[int, str, str, str, int]] = {
    "运动与静止的相对性": (
        22, "concept", "人们常常巧妙地应用运动与静止的相对性为我们服务",
        "物体是运动还是静止，要相对于参照物来判断；没有参照物就无法讨论运动静止。",
        3,
    ),
    "宏观世界": (
        14, "concept", "宏观世界",
        "尺度上比分子、原子大得多的物质世界，包括人、物体、天体等日常可见的事物。",
        2,
    ),
    "分界面": (
        69, "term", "两种物质的分界面",
        "光从一种介质斜射入另一种介质时两种介质的分界面，光的折射就发生在这里。",
        2,
    ),
    "透明物体": (
        75, "term", "透明物体",
        "光能大部分穿过其表面的物体；与不透明物体相对，是判断能否成像的关键。",
        2,
    ),
    "矫正": (
        83, "term", "最常见的就是用眼镜进行矫正",
        "用凹透镜或凸透镜等光学器件把像成在视网膜上，从而纠正屈光不正、看清物体。",
        2,
    ),
    "惯性定律": (
        135, "term", "惯性定律",
        "牛顿第一定律的别名，指出一切物体都有保持原有运动状态不变的性质。",
        2,
    ),
    "力的合成": (
        139, "term", "求几个力的合力叫做力的合成。",
        "把同时作用在同一物体上的几个力等效替换为一个力的运算，替换前后作用效果相同。",
        3,
    ),
    "同一直线上二力的合成": (
        139, "concept", "同一直线上二力的合成",
        "作用在同一直线上的两个力可以等效为一个力，合力的大小与方向由这两个力决定。",
        3,
    ),
    "力的平衡": (
        142, "concept", "力的平衡",
        "物体保持静止或匀速直线运动的状态，此时所受合力为零，各力相互平衡。",
        3,
    ),
    "奥托·格里克": (
        165, "name", "奥托·格里克",
        "德国物理学家，1600 年前后发明抽气机，用实验证明空气有压强并抽出部分空气。",
        2,
    ),
    "大分子": (
        225, "term", "大分子",
        "尺度在 10⁻⁸～10⁻⁹ 米量级的粒子，如蛋白质、DNA 等生物大分子。",
        2,
    ),
    "城市区域环境噪声标准": (
        52, "term", "城市区域环境噪声标准",
        "国家对不同城市区域规定的环境噪声限值标准，用于控制噪声污染。",
        2,
    ),
    "超声金属探伤仪": (
        55, "term", "超声金属探伤仪",
        "利用超声波在金属内部反射来检测内部缺陷的仪器，常用于检验钢材质量。",
        2,
    ),
}

# ── 额外删：boundary 卡里判为重复或「整句作正面」的 ──
# 「运动定律」「物重跟质量的关系」是书上原话，保留；其余是重复或整句。
DROP_EXTRA = {
    "单位体积的质量",      # 与「密度」重复
    "作用效果",            # 与「力的作用效果」重复
    "浮沉条件",            # 与「物体的浮沉条件」重复
    "杠杆动力臂",          # 与「动力臂」重复
    "质量相同时，速度越大的物体具有的动能越大",   # 整句作正面
    "速度相同时，质量越大的物体具有的动能越大",   # 整句作正面
    "物体的动能与物体的质量和速度有关",           # 整句作正面
    "台秤",                 # 纯器材名，和 p94 那张同口径
    "克每立方米",           # 原文文本层丢了「千」字，卡面与符号 kg/m3 对不上
    "二力平衡条件",         # 与更完整的「二力平衡的条件」重复，按嵌套只留最长的
}

# ── 改：boundary 卡里能机械修正的（换成书上完整固定说法） ──
RENAME: dict[str, str] = {
    "环境噪声标准": "城市区域环境噪声标准",
    "探伤仪": "超声金属探伤仪",
}


def in_text(text: str, frag: str) -> bool:
    if frag in text:
        return True
    if re.sub(r"\s+", " ", text).find(re.sub(r"\s+", " ", frag)) >= 0:
        return True
    return re.sub(r"\s+", "", text).find(re.sub(r"\s+", "", frag)) >= 0


def load_answers() -> dict[str, list[dict]]:
    """各块的原始答案（seg_id -> keywords）。"""
    answers: dict[str, list[dict]] = {}
    for out in sorted(CHUNK_DIR.glob("*.out.json")):
        data = json.loads(out.read_text(encoding="utf-8"))
        for seg_id, payload in data.items():
            answers[seg_id] = payload.get("keywords") or []
    return answers


def page_of(seg_id: str) -> int:
    return int(seg_id.split(":")[-1].lstrip("p"))


# seg_id → sid、页号 → sid 的映射（从候选行取）
_SEG_SID: dict[str, str] = {}
_SID_BY_PAGE: dict[int, str] = {}


def _sid_of(seg_id: str) -> str:
    return _SEG_SID.get(seg_id) or _SID_BY_PAGE.get(page_of(seg_id), "s01")


def main() -> int:
    answers = load_answers()
    for line in (BOOK / "candidates.jsonl").open(encoding="utf-8"):
        row = json.loads(line)
        seg_id = str(row.get("seg_id") or "")
        sid = str(row.get("sid") or "")
        if seg_id and sid:
            _SEG_SID[seg_id] = sid
            _SID_BY_PAGE[int(str(row.get("page") or 0))] = sid
    reviews = [
        json.loads(line)
        for line in (BOOK / "review" / "full-agent.jsonl").open(encoding="utf-8")
    ]

    # 「这张卡被判 reject / fatal」的 card_id 集合。card_id 是
    # ``<fp12>:<sid>:<sha1(front)[:8]>``，所以就算卡已经被删掉、不在
    # cards.jsonl 里，也能按哈希把词对上号（否则下一轮又会被加回来）。
    rejected_ids = {
        r["card_id"] for r in reviews
        if r["kw"] == "reject" or r["fatal"]
    }
    drop_surfaces: dict[str, set[str]] = {}
    for seg_id, items in answers.items():
        sid = _sid_of(seg_id)
        for it in items:
            surface = str(it.get("surface") or "")
            if not surface:
                continue
            if card_id_for("94a9b8d6b27d", sid, surface) in rejected_ids:
                drop_surfaces.setdefault(seg_id, set()).add(surface)
    print(f"按 card_id 哈希对上要删的词：{sum(len(v) for v in drop_surfaces.values())} 个"
          f"（{len(drop_surfaces)} 段）")

    # 章首页（只有章名 + 小节名 + 页码）按版式清空，和 submit_full.py 同一口径
    dropped = renamed = added = 0
    title_touched: set[str] = set()
    for seg_id, items in list(answers.items()):
        page = page_of(seg_id)
        text = (SEG_DIR / f"p{page:04d}.txt").read_text(encoding="utf-8")
        lines = [x for x in text.split("\n") if x.strip()]
        if len(text) <= 200 and len(lines) <= 12 and any("章" in x for x in lines):
            if items:
                dropped += len(items)
            answers[seg_id] = []
            title_touched.add(seg_id)
    extra_touched: set[str] = set()
    for seg_id, items in answers.items():
        page = page_of(seg_id)
        kill = drop_surfaces.get(seg_id, set())
        keep = []
        for it in items:
            s = it.get("surface")
            if s in kill or s in DROP_EXTRA:
                dropped += 1
                extra_touched.add(seg_id)
                continue
            if s in RENAME:
                it = dict(it, surface=RENAME[s])
                renamed += 1
            keep.append(it)
        answers[seg_id] = keep

    # 补：把 ADD 的词挂到对应页的那一段上
    by_page: dict[int, list[str]] = {}
    for seg_id in answers:
        by_page.setdefault(page_of(seg_id), []).append(seg_id)

    bad = 0
    got_add: set[str] = set()
    for surface, (page, typ, evidence, expl, lv) in ADDS.items():
        text = (SEG_DIR / f"p{page:04d}.txt").read_text(encoding="utf-8")
        if not in_text(text, surface):
            print(f"✗ {surface}: 不在第 {page} 页原文里")
            bad += 1
            continue
        if not in_text(text, evidence):
            print(f"✗ {surface}: evidence 不在第 {page} 页原文里 → {evidence!r}")
            bad += 1
            continue
        n = len(re.sub(r"\s+", "", expl))
        if not (15 <= n <= 60):
            print(f"✗ {surface}: explanation {n} 字")
            bad += 1
            continue
        segs = by_page.get(page) or []
        if not segs:
            print(f"✗ {surface}: 第 {page} 页没有可用的段")
            bad += 1
            continue
        target = segs[0]
        got_add.add(target)
        new_item = {
            "surface": surface, "type": typ, "evidence": evidence,
            "explanation": expl, "learn_value": lv,
        }
        # 该段已经有这个词 → 覆盖（依据句/解释用更好的版本），不重复加
        for i, item in enumerate(answers[target]):
            if item.get("surface") == surface:
                answers[target][i] = new_item
                print(f"· {surface}: 第 {page} 页覆盖已有项")
                break
        else:
            answers[target].append(new_item)
        added += 1

    print(f"删 {dropped} 个 reject/fatal 词，改 {renamed} 个 boundary 词，补 {added} 个词")
    if bad:
        print(f"补卡校验没过 {bad} 处，先不交")
        return 1

    # 重交所有受影响的段
    touched = {s for s in drop_surfaces} | got_add | {
        s for s, items in answers.items()
        if items and any(i.get("surface") in RENAME.values() for i in items)
    }
    # DROP_EXTRA（重复卡 / 整句正面 / 纯器材名）所在的段也要重交
    touched |= extra_touched | title_touched
    # 全量重交：ADDS / DROP_EXTRA 每一轮都可能变，只重交受影响的段会留下陈旧行。
    touched = set(answers)
    print(f"要重交 {len(touched)} 段")
    ok = bad2 = 0
    for seg_id in sorted(touched):
        payload = {"keywords": answers.get(seg_id, [])}
        path = Path(f"/tmp/answers/{seg_id.split(':')[-1]}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run(
            [".venv/bin/python", "-m", "bookcard", "flow", "extract-submit", BOOK.as_posix(),
             "--seg-id", seg_id, "--output-file", str(path), "--extractor", EXTRACTOR],
            capture_output=True, text=True,
        )
        try:
            d = json.loads(r.stdout)
        except json.JSONDecodeError:
            print(f"✗ {seg_id} 提交异常 {r.stdout[:120]} {r.stderr[:120]}")
            bad2 += 1
            continue
        if d.get("ok"):
            ok += 1
            dr = d.get("dropped") or {}
            if dr.get("not_in_text") or dr.get("bad_evidence"):
                print(f"  ⚠ {seg_id} 丢弃 {dr}")
        else:
            bad2 += 1
            print(f"✗ {seg_id} {d.get('error')}")
    print(f"重交：成功 {ok} 段，失败 {bad2} 段")
    return 0 if bad2 == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
