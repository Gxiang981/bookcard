"""bench v1 选页（规则见 docs/bench-v1.md §1）。只用标准库，结果确定。

用法::

    python3 bench/v1/select_pages.py > bench/v1/pages.json

只在建 v1 时跑一次；pages.json 入库后不再改，改规则就升 v2。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "train" / "data" / "bookcard"
VERSION = "bench-v1"

BOOKS = ["4ecda441f6d4", "2f3e20c2e34d", "dc221b94f843"]
QUOTA = {"toc": 1, "exercise": 4, "notes": 3, "body": 22}

# 练习栏目标题（整行，去空白后完全相等）。物理的「动手动脑学物理」取字是乱码，按乱码原样认。
EXERCISE_HEADINGS = {
    "4ecda441f6d4": {"探究与拓展", "问题探究", "学习拓展"},
    "2f3e20c2e34d": {"\u04ae\u0a63\u04ae\u1467\u07e5\u0fac\u1030"},
    "dc221b94f843": {"SelfCheck"},
}
# 注释/旁栏标题（整行，去空白后完全相等）；历史另数行首的圈码脚注，
# 英语数 Notes on the Text 页里的编号条目。
NOTE_HEADINGS = {
    "4ecda441f6d4": {"历史纵横", "史料阅读", "学思之窗"},
    "2f3e20c2e34d": {"小资料", "科学世界", "想想做做", "想想议议", "科学·技术·社会"},
    "dc221b94f843": set(),
}
CIRCLED = re.compile(r"^[\u2460-\u2473]")
NUMBERED = re.compile(r"^\d+\.\s")

MIN_CHARS = 150
MIN_BODY_CHARS = 300
MAX_GARBLE = 0.05


def h(fp12: str, page: int) -> str:
    return hashlib.sha1(f"{VERSION}:{fp12}:{page}".encode()).hexdigest()


def garble_rate(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 1.0
    bad = sum(
        1
        for c in chars
        if unicodedata.category(c) in ("Cc", "Co", "Cn") or 0x0400 <= ord(c) <= 0x1CFF
    )
    return bad / len(chars)


def lines(text: str) -> list[str]:
    return [re.sub(r"\s+", "", line) for line in text.split("\n")]


def note_score(fp12: str, text: str) -> int:
    ls = lines(text)
    if fp12 == "dc221b94f843":
        if not ls or ls[0] != "NotesontheText":
            return 0
        return sum(1 for raw in text.split("\n") if NUMBERED.match(raw.strip()))
    score = sum(1 for line in ls if line in NOTE_HEADINGS[fp12])
    score += sum(1 for line in ls if CIRCLED.match(line))
    return score


def select_book(fp12: str) -> dict:
    d = DATA / fp12
    book = json.loads((d / "book.json").read_text(encoding="utf-8"))
    pages = [json.loads(line) for line in (d / "pages.jsonl").open(encoding="utf-8")]
    segs: dict[int, list[str]] = {}
    for line in (d / "candidates.jsonl").open(encoding="utf-8"):
        row = json.loads(line)
        segs.setdefault(row["page"], []).append(row["seg_id"])

    def ok(p: dict, min_chars: int = MIN_CHARS) -> bool:
        return len(p["text"]) >= min_chars and garble_rate(p["text"]) <= MAX_GARBLE

    chosen: dict[int, str] = {}

    toc = [p for p in pages if p["kind"] == "toc" and ok(p)]
    for p in sorted(toc, key=lambda p: h(fp12, p["page"]))[: QUOTA["toc"]]:
        chosen[p["page"]] = "toc"

    extractable = [
        p
        for p in pages
        if p["kind"] in ("body", "exercise", "appendix") and p["page"] in segs and ok(p)
    ]

    ex_pool = [
        p
        for p in extractable
        if p["kind"] == "exercise" or set(lines(p["text"])) & EXERCISE_HEADINGS[fp12]
    ]
    for p in sorted(ex_pool, key=lambda p: h(fp12, p["page"]))[: QUOTA["exercise"]]:
        chosen[p["page"]] = "exercise"

    scored = [(note_score(fp12, p["text"]), p) for p in extractable if p["page"] not in chosen]
    scored = [(s, p) for s, p in scored if s >= 1]
    for _, p in sorted(scored, key=lambda sp: (-sp[0], h(fp12, sp[1]["page"])))[: QUOTA["notes"]]:
        chosen[p["page"]] = "notes"

    ex_pages = {p["page"] for p in ex_pool}
    body = [
        p
        for p in extractable
        if p["kind"] == "body"
        and p["page"] not in chosen
        and p["page"] not in ex_pages
        and ok(p, MIN_BODY_CHARS)
    ]
    body.sort(key=lambda p: p["page"])
    n = QUOTA["body"]
    for i in range(n):
        bin_ = body[i * len(body) // n : (i + 1) * len(body) // n]
        p = min(bin_, key=lambda p: h(fp12, p["page"]))
        chosen[p["page"]] = "body"

    by_page = {p["page"]: p for p in pages}
    out = []
    for page in sorted(chosen):
        p = by_page[page]
        out.append(
            {
                "page": page,
                "stratum": chosen[page],
                "kind": p["kind"],
                "text_sha1": p["text_sha1"],
                "seg_ids": segs.get(page, [f"book:{fp12}:p{page:04d}"]),
            }
        )
    counts = {k: sum(1 for x in out if x["stratum"] == k) for k in QUOTA}
    if counts != QUOTA:
        raise SystemExit(f"{fp12}: quota not met {counts}")
    return {"fp": book["fp"], "fp12": fp12, "title": book["title"], "pages": out}


def main() -> None:
    result = {
        "version": VERSION,
        "rules": "docs/bench-v1.md",
        "quota_per_book": QUOTA,
        "books": [select_book(fp12) for fp12 in BOOKS],
    }
    json.dump(result, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
