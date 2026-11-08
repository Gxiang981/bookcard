#!/usr/bin/env python3
"""试抽审卡：写判定 → 交卷。

判定口径（``src/bookcard/prompts/judge_v1.md``）：
- kw: accept / boundary / reject
- evidence: ok / missing / wrong
- expl: 2 / 1 / 0
- fatal: true / false
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

BOOK = "train/data/bookcard/books/94a9b8d6b27d"
REVIEWER = "agent:dsh:MiniMax-M3.1-Flash"

BATCHES: dict[str, dict] = {
    # b000：p10 / p11 / p226
    "b000-27479af9": {
        "verdicts": [
            {"index": 0, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "章首题词，署名在下一行"},
            {"index": 1, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "专名，依据在原文"},
            {"index": 2, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "现象术语，解释准确"},
            {"index": 3, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "术语准确"},
            {"index": 4, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "器件术语，解释准确"},
            {"index": 5, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "现象术语，解释到位"},
            {"index": 6, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "仪器术语，解释准确"},
            {"index": 7, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "物质术语，解释准确"},
        ],
        "missed": [],
    },
    # b001：p226 / p227 / p228
    "b001-c0303611": {
        "verdicts": [
            {"index": 0, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "原文作疑问句，去掉疑问词成术语"},
            {"index": 1, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "物质术语，解释准确"},
            {"index": 2, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "概念准确，依据是图注"},
            {"index": 3, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "实验器材，解释准确"},
            {"index": 4, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "仪器术语，解释准确"},
            {"index": 5, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "部件术语，解释准确"},
            {"index": 6, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "概念准确，依据是图注"},
            {"index": 7, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "原文作疑问句，去掉疑问词成术语"},
        ],
        "missed": [{"surface": "分子", "page": 226}],
    },
    # b002：p229 / p230
    "b002-c41cf467": {
        "verdicts": [
            {"index": 0, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "物态术语，解释准确"},
            {"index": 1, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "物态术语，解释准确"},
            {"index": 2, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "物态术语，解释准确"},
            {"index": 3, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "术语准确，依据跨行可定位"},
            {"index": 4, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "概念准确"},
            {"index": 5, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "本章核心术语，解释准确"},
            {"index": 6, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "术语准确，依据跨行可定位"},
            {"index": 7, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "术语准确"},
        ],
        "missed": [],
    },
    # b003：p230 / p231
    "b003-3c02f34d": {
        "verdicts": [
            {"index": 0, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "核心术语，解释准确"},
            {"index": 1, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "人名，解释准确"},
            {"index": 2, "kw": "reject", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "本身是成语，与「扩散」重复，不值得单独记"},
            {"index": 3, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "术语准确"},
            {"index": 4, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "术语准确，依据跨行可定位"},
            {"index": 5, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "人名，解释准确"},
            {"index": 6, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "专名，解释准确"},
            {"index": 7, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "运动学术语，解释准确"},
        ],
        "missed": [{"surface": "行星", "page": 231}, {"surface": "月球", "page": 231}],
    },
    # b004：p231
    "b004-0d1b6b7c": {
        "verdicts": [
            {"index": 0, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "术语准确，依据跨行可定位"},
            {"index": 1, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "人名，解释准确"},
            {"index": 2, "kw": "accept", "evidence": "ok", "expl": 2, "fatal": False,
             "note": "定律名，解释补充了公认常识"},
        ],
        "missed": [],
    },
}


def main() -> int:
    for batch_id, payload in BATCHES.items():
        path = Path(f"/tmp/judge/{batch_id}.json")
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run(
            [".venv/bin/python", "-m", "bookcard", "flow", "judge-submit", BOOK,
             "--batch-id", batch_id, "--output-file", str(path), "--reviewer", REVIEWER],
            capture_output=True, text=True,
        )
        try:
            d = json.loads(r.stdout)
        except json.JSONDecodeError:
            print(f"{batch_id} 提交失败：{r.stdout[:200]} {r.stderr[:200]}")
            continue
        print(f"{batch_id} → {'ok' if d.get('ok') else 'ERR ' + str(d.get('error'))}"
              f" | judged {d.get('judged')} | qualified {d.get('qualified')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
