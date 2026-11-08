#!/usr/bin/env python3
"""全量抽卡交卷：读各块的答案 → 逐字校验 → 提交给引擎。

校验（引擎那边也会查，但先自己查一遍，省得来回）：
- surface / evidence 必须在该段原文里（evidence 允许空白折叠 / 空白全去两级退化）
- explanation 15–60 字
- 同一段里 surface 不重复
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

BOOK = "train/data/bookcard/books/94a9b8d6b27d"
EXTRACTOR = "agent:dsh:MiniMax-M3.1-Flash:ex-v1"
SEG_DIR = Path("/tmp/segments")
CHUNK_DIR = Path("/tmp/chunks")


def in_text(text: str, frag: str) -> bool:
    if frag in text:
        return True
    if re.sub(r"\s+", " ", text).find(re.sub(r"\s+", " ", frag)) >= 0:
        return True
    return re.sub(r"\s+", "", text).find(re.sub(r"\s+", "", frag)) >= 0


def main() -> int:
    only = sys.argv[1:] or None
    answers: dict[str, list[dict]] = {}
    for out in sorted(CHUNK_DIR.glob("*.out.json")):
        if only and out.stem.split(".")[0] not in only:
            continue
        data = json.loads(out.read_text(encoding="utf-8"))
        for seg_id, payload in data.items():
            answers[seg_id] = payload.get("keywords") or []

    print(f"读到 {len(answers)} 段的答案")

    # 章首页（只有章名 + 小节名 + 页码）按版式处理，不抽词。
    # 各块对这一类的处理不一致（有的标了小节名），这里统一清空。
    cleared = []
    for seg_id in list(answers):
        page = seg_id.split(":")[-1]
        text = (SEG_DIR / f"{page}.txt").read_text(encoding="utf-8")
        lines = [x for x in text.split("\n") if x.strip()]
        if len(text) <= 200 and len(lines) <= 12 and any("章" in x for x in lines):
            if answers[seg_id]:
                cleared.append((page, [k.get("surface") for k in answers[seg_id]]))
            answers[seg_id] = []
    if cleared:
        print(f"章首页按版式清空 {len(cleared)} 段：")
        for page, kws in cleared:
            print(f"  p{page}: {kws}")

    problems: list[str] = []
    fixed = 0
    for seg_id, items in answers.items():
        page = seg_id.split(":")[-1]
        text = (SEG_DIR / f"{page}.txt").read_text(encoding="utf-8")
        seen: set[str] = set()
        clean: list[dict] = []
        for it in items:
            surface = str(it.get("surface") or "")
            if not surface:
                problems.append(f"{seg_id}: 空 surface")
                continue
            if surface in seen:
                continue          # 同段内重复，直接丢
            if not in_text(text, surface):
                problems.append(f"{seg_id}: surface 不在原文 → {surface!r}")
                continue
            ev = str(it.get("evidence") or "")
            if not ev:
                problems.append(f"{seg_id}: 没写 evidence → {surface!r}")
            elif not in_text(text, ev):
                problems.append(f"{seg_id}: evidence 不在原文 → {ev!r}")
                fixed += 1
            expl = str(it.get("explanation") or "")
            n = len(re.sub(r"\s+", "", expl))
            if not (15 <= n <= 60):
                problems.append(f"{seg_id}: explanation {n} 字 → {surface!r}")
            seen.add(surface)
            clean.append(it)
        answers[seg_id] = clean

    print(f"校验问题 {len(problems)} 处")
    for p in problems[:40]:
        print("  ⚠", p)
    if len(problems) > 40:
        print(f"  … 还有 {len(problems) - 40} 处")

    total_kw = sum(len(v) for v in answers.values())
    print(f"共 {total_kw} 个词，{len(answers)} 段")

    ok = bad = 0
    dropped_total = {"not_in_text": 0, "bad_evidence": 0}
    for seg_id, items in sorted(answers.items()):
        path = Path(f"/tmp/answers/{seg_id.split(':')[-1]}.json")
        path.write_text(json.dumps({"keywords": items}, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run(
            [".venv/bin/python", "-m", "bookcard", "flow", "extract-submit", BOOK,
             "--seg-id", seg_id, "--output-file", str(path), "--extractor", EXTRACTOR],
            capture_output=True, text=True,
        )
        try:
            d = json.loads(r.stdout)
        except json.JSONDecodeError:
            print(f"✗ {seg_id} 提交异常：{r.stdout[:120]} {r.stderr[:120]}")
            bad += 1
            continue
        if d.get("ok"):
            ok += 1
            dr = d.get("dropped") or {}
            dropped_total["not_in_text"] += dr.get("not_in_text", 0)
            dropped_total["bad_evidence"] += dr.get("bad_evidence", 0)
        else:
            bad += 1
            print(f"✗ {seg_id} {d.get('error')}")
    print(f"\n提交：成功 {ok} 段，失败 {bad} 段")
    print(f"引擎丢弃：{dropped_total}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
