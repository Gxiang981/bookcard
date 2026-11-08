#!/usr/bin/env python3
"""合并全书审卡判定 → 写 ``review/full-agent.jsonl``。

引擎的 `judge_next` 只发试抽那批（闸门 2 用）。这一步把全书 517 张最终卡的
判定落成审核记录，格式与引擎的 `judge.build_records` 一致，
`review/*.jsonl` 会被 `audit()` 全读进去，质检报告因此覆盖全书。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

BOOK = Path("train/data/bookcard/books/94a9b8d6b27d")
JF = Path("/tmp/jf")
REVIEWER = "agent:dsh:MiniMax-M3.1-Flash"
ALLOWED_KW = {"accept", "boundary", "reject"}
ALLOWED_EV = {"ok", "missing", "wrong"}


def main() -> int:
    all_batches = json.loads((JF / "all.json").read_text(encoding="utf-8"))
    records: list[dict] = []
    problems: list[str] = []
    missed: list[dict] = []
    dist = {"kw": {}, "expl": {}, "fatal": 0}

    for batch in all_batches:
        name = batch["batch"]
        out_path = JF / f"{name}.out.json"
        if not out_path.exists():
            problems.append(f"{name}: 缺 out.json")
            continue
        verdicts = json.loads(out_path.read_text(encoding="utf-8"))
        meta = batch["cards"]
        if len(verdicts) != len(meta):
            problems.append(f"{name}: 判定 {len(verdicts)} 条 ≠ 卡 {len(meta)} 张")
        seen_idx = set()
        for v in verdicts:
            idx = v.get("index")
            if idx in seen_idx:
                problems.append(f"{name}: index {idx} 重复")
                continue
            seen_idx.add(idx)
            if idx is None or idx >= len(meta):
                problems.append(f"{name}: index {idx} 越界")
                continue
            kw = str(v.get("kw") or "")
            ev = str(v.get("evidence") or "")
            try:
                expl = int(v.get("expl"))
            except (TypeError, ValueError):
                expl = -1
            if kw not in ALLOWED_KW:
                problems.append(f"{name}[{idx}]: kw 非法 {kw!r}")
                continue
            if ev not in ALLOWED_EV:
                problems.append(f"{name}[{idx}]: evidence 非法 {ev!r}")
                continue
            if expl not in (0, 1, 2):
                problems.append(f"{name}[{idx}]: expl 非法 {expl!r}")
                continue
            note = re.sub(r"\s+", "", str(v.get("note") or ""))[:20]
            records.append({
                "card_id": meta[idx]["card_id"],
                "kw": kw,
                "evidence": ev,
                "expl": expl,
                "fatal": bool(v.get("fatal")),
                "note": note,
                "reviewer": REVIEWER,
                "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            })
            dist["kw"][kw] = dist["kw"].get(kw, 0) + 1
            dist["expl"][expl] = dist["expl"].get(expl, 0) + 1
            if v.get("fatal"):
                dist["fatal"] += 1
        missed_path = JF / f"{name}.missed.json"
        if missed_path.exists():
            try:
                items = json.loads(missed_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                items = []
            for it in items or []:
                if isinstance(it, dict) and it.get("surface"):
                    missed.append({
                        "surface": str(it["surface"]),
                        "page": it.get("page"),
                        "note": str(it.get("note") or "")[:80],
                    })

    print(f"判定 {len(records)} 张，覆盖 {len(all_batches)} 批")
    print(f"kw 分布：{dist['kw']}")
    print(f"expl 分布：{dist['expl']}   fatal：{dist['fatal']}")
    if problems:
        print(f"⚠ 问题 {len(problems)} 处：")
        for p in problems[:30]:
            print("  ", p)
        if len(problems) > 30:
            print(f"  … 还有 {len(problems) - 30} 处")

    out = BOOK / "review" / "full-agent.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"写入 {out}（{len(records)} 行）")

    if missed:
        mp = BOOK / "review" / "full-missed.json"
        mp.write_text(json.dumps(missed, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"漏标词 {len(missed)} 个 → {mp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
