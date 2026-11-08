#!/usr/bin/env python3
"""把全书最终卡渲染成审卡批次（给 agent 判）。

引擎的 `judge_next` 只发**试抽**那批卡（闸门 2 用）。要全书 QC，就用同一套
`select_cards` + `render_batch` 把 517 张最终卡全渲染出来。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "src")

from bookcard.extract import Page, Section, load_candidates
from bookcard.judge import build_cards, render_batch

BOOK = "train/data/bookcard/books/94a9b8d6b27d"
OUT = Path("/tmp/jf")
BATCH = 8


def main() -> int:
    pages = [
        Page(page=r["page"], printed_page=r.get("printed_page"), text=r["text"],
             text_sha1=r.get("text_sha1", ""), kind=r.get("kind", ""),
             width=r.get("width") or 0.0, height=r.get("height") or 0.0)
        for r in (
            json.loads(line)
            for line in open(f"{BOOK}/pages.jsonl", encoding="utf-8")
        )
    ]
    secs = [
        Section(sid=s["sid"], title=s["title"], level=s["level"],
                 page_start=s["page_start"], page_end=s["page_end"], sort_order=s["sort_order"])
        for s in json.load(open(f"{BOOK}/sections.json", encoding="utf-8"))
    ]
    rows = list(load_candidates(Path(f"{BOOK}/candidates.jsonl")))
    # cards.jsonl 就是挑卡后的最终卡；用引擎的 build_cards 配上页原文再渲染。
    # cards.jsonl 的字段结构跟 trial-cards.json 不同（page/evidence 在 source 里），
    # 这里适配成 build_cards 要的形状。
    final_rows = []
    for line in open(f"{BOOK}/cards.jsonl", encoding="utf-8"):
        r = json.loads(line)
        src = r.get("source") or {}
        final_rows.append({
            "card_id": r.get("card_id"),
            "sid": r.get("sid"),
            "front": r.get("front"),
            "back": r.get("back"),
            "type": r.get("type"),
            "learn_value": r.get("learn_value") or 2,
            "page": src.get("page"),
            "printed_page": src.get("printed_page"),
            "seg_id": src.get("seg_id"),
            "start": src.get("start"),
            "end": src.get("end"),
            "evidence": src.get("evidence"),
        })
    ctx = type("Ctx", (), {})()
    ctx.pages = pages
    ctx.sections = secs
    ctx.fp12 = "94a9b8d6b27d"
    cards = build_cards(ctx, final_rows)
    print(f"最终卡 {len(cards)} 张（候选 {len(rows)} 段）")

    os.makedirs(OUT, exist_ok=True)
    for f in OUT.glob("*.in.txt"):
        f.unlink()
    meta_all = []
    for i in range(0, len(cards), BATCH):
        part = cards[i:i + BATCH]
        idx = i // BATCH
        name = f"{idx:03d}"
        (OUT / f"{name}.in.txt").write_text(render_batch(part), encoding="utf-8")
        meta = [
            {"index": j, "card_id": str(c.card_id), "front": c.front, "page": c.page}
            for j, c in enumerate(part)
        ]
        (OUT / f"{name}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        meta_all.append({"batch": name, "cards": meta})
    (OUT / "all.json").write_text(
        json.dumps(meta_all, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"批次 {len(meta_all)} 个，每批 {BATCH} 张 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
