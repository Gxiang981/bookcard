"""judge.py — 试抽的卡片判定（契约 §6「卡片判定」）。

闸门 2 要求对试抽产生的**全部**卡逐张判定，四个维度：

- ``kw`` = ``accept`` / ``boundary`` / ``reject``（口径同 ``train/GUIDE.md``）；
- ``evidence`` = ``ok`` / ``missing`` / ``wrong``；
- ``expl`` = ``2`` / ``1`` / ``0``；
- ``fatal`` = ``true`` / ``false``。

合格 = ``kw == accept`` 且 ``evidence == ok`` 且 ``expl >= 1`` 且 ``fatal == false``。

判定分两层：

1. **确定性检查**（程序判，不问模型）：front 是否逐字在原文里、依据句是否能在
   原文里定位、解释字数、解释是不是只是照抄依据句；
2. **模型辅助**（问 LLM）：这个词值不值得学（GUIDE.md 口径）、解释准不准
   （§6 的 2/1/0）、有没有事实错误。

**程序的结果覆盖模型的判断**：模型说「依据在原文里」但程序在原文里定位不到，
就按 ``missing`` 记（§6 的判据是「依据句在原文里」，这条程序说了算）。
每张卡的判定和理由都落进 ``review/trial-agent.jsonl``，``reviewer`` 写明是谁。

用法::

    python -m bookcard.judge <一本书的工作目录> [--batch 8] [--reviewer agent:agent]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from kwtrain import llm

from bookcard import prompts
from bookcard.engine.softwrap import find_all_flexible
from bookcard.extract import (
    TRIAL_PASS_RATE,
    BookContext,
    build_segments,
    load_book_context,
    locate_evidence,
)

logger = logging.getLogger(__name__)

#: 解释字数的合格区间（契约 §4：15–60 字）。
EXPL_MIN = 15
EXPL_MAX = 60

#: 单次判定的 max_tokens / effort（沿用初标器的经验）。
MAX_TOKENS = 4096
EFFORT = "low"

#: 模型一次判几张卡。
DEFAULT_BATCH = 8

#: 模型回复里用的枚举值。
KW_VALUES = ("accept", "boundary", "reject")
EVIDENCE_VALUES = ("ok", "missing", "wrong")

#: 模型没给出判定时写进 ``note`` 的说法（``apply_deterministic`` 填的默认值）。
#: benchmark 侧靠它认出「这条没判出来」，好单独重试。
NO_VERDICT_NOTE = "模型没给判定，按不合格记"


def _read_system_prompt() -> str:
    """读包内的审卡提示词 ``bookcard/prompts/judge_v1.md``。

    提示词正文原本是这个模块里的常量 ``SYSTEM``，现在**逐字**搬到包内的
    文件里（契约 §1 / ``docs/mcp-v1.md`` §1），提示词跟着包走。
    文字一个字都不改——单测 ``tests/test_prompts.py`` 拿原始常量逐字比对。
    """
    text = prompts.read_prompt(prompts.JUDGE_PROMPT_NAME)
    # 文件末尾留了一个换行（POSIX 文本文件惯例），读出来时去掉，
    # 让 SYSTEM 与原来的常量**逐字相等**。
    return text[:-1] if text.endswith("\n") else text


#: 审卡提示词（契约 §6 的「卡片判定」）。内容在包内的 ``prompts/judge_v1.md``。
SYSTEM = _read_system_prompt()


@dataclass
class Card:
    """待判定的卡。"""

    card_id: str
    sid: str
    front: str
    back: str
    type: str
    learn_value: int
    page: int
    printed_page: str | None
    seg_id: str
    start: int
    end: int
    evidence: str | None
    page_text: str = ""
    seg_text: str = ""

    @property
    def front_in_text(self) -> bool:
        """词是不是原文里的？

        用**容错匹配**（跨 PDF 硬折行、兼容码位都算命中）——
        朴素子串判断会把「软件设\n计模式」这种正常的词判成「不是原文」，
        白白判成严重错误。
        """
        return bool(find_all_flexible(self.page_text, self.front))

    @property
    def evidence_in_text(self) -> bool:
        if not self.evidence:
            return False
        return locate_evidence(self.page_text, self.evidence) is not None

    @property
    def explanation_is_copy(self) -> bool:
        """解释是不是只是把依据原句复述一遍。"""
        if not self.evidence:
            return False
        normal_expl = re.sub(r"\s+", "", self.back)
        normal_ev = re.sub(r"\s+", "", self.evidence)
        if not normal_expl or not normal_ev:
            return False
        return normal_expl in normal_ev or normal_ev in normal_expl


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_trial_cards(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    cards = data.get("cards") if isinstance(data, dict) else data
    return [card for card in (cards or []) if isinstance(card, dict)]


def build_cards(context: BookContext, rows: list[dict[str, Any]]) -> list[Card]:
    """把 ``review/trial-cards.json`` 的卡配上原文（判定要用原文）。"""
    segments = build_segments(context.pages, context.sections, context.fp12)
    seg_text = {segment.seg_id: segment.text for segment in segments}
    page_text = {page.page: page.text for page in context.pages}
    cards: list[Card] = []
    for row in rows:
        seg_id = str(row.get("seg_id") or "")
        page = int(row.get("page") or 0)
        cards.append(
            Card(
                card_id=str(row.get("card_id") or ""),
                sid=str(row.get("sid") or ""),
                front=str(row.get("front") or ""),
                back=str(row.get("back") or ""),
                type=str(row.get("type") or ""),
                learn_value=int(row.get("learn_value") or 2),
                page=page,
                printed_page=row.get("printed_page"),
                seg_id=seg_id,
                start=int(row.get("start") or 0),
                end=int(row.get("end") or 0),
                evidence=row.get("evidence"),
                page_text=page_text.get(page, ""),
                seg_text=seg_text.get(seg_id, ""),
            )
        )
    return cards


def render_batch(cards: Sequence[Card]) -> str:
    """把一组卡渲染成给模型看的样子（带书页原文）。"""
    blocks: list[str] = []
    for index, card in enumerate(cards):
        blocks.append(
            f"[{index}] front：{card.front}\n"
            f"    back：{card.back}\n"
            f"    evidence：{card.evidence or '（无）'}\n"
            f"    page：{card.page}"
        )
    pages = sorted({card.page for card in cards})
    original = "\n\n".join(
        f"===== 第 {page} 页原文 =====\n" + text for page, text in _page_texts(cards, pages)
    )
    return "待判定的卡：\n" + "\n".join(blocks) + "\n\n" + original


def _page_texts(cards: Sequence[Card], pages: Sequence[int]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for page in pages:
        text = ""
        for card in cards:
            if card.page == page:
                text = card.page_text
                break
        out.append((page, text))
    return out


def parse_verdicts(raw: str, cards: Sequence[Card]) -> dict[str, dict[str, Any]]:
    """解析模型回包，按 ``index`` 对回卡。"""
    from bookcard.extract import ParseError  # 复用同一套容错解析思路

    data = None
    for candidate in (raw, _strip_fence(raw)):
        try:
            data = json.loads(candidate)
            break
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    if not isinstance(data, dict):
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
            except (json.JSONDecodeError, TypeError, ValueError):
                data = None
    if not isinstance(data, dict):
        raise ParseError("判定回包里找不到 JSON 对象")

    out: dict[str, dict[str, Any]] = {}
    for item in data.get("verdicts") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(cards):
            continue
        card = cards[index]
        out[card.card_id] = {
            "kw": _one_of(item.get("kw"), KW_VALUES, "reject"),
            "evidence": _one_of(item.get("evidence"), EVIDENCE_VALUES, "missing"),
            "expl": _expl(item.get("expl")),
            "fatal": bool(item.get("fatal")),
            "note": str(item.get("note") or "")[:120],
        }
    missed = data.get("missed")
    if isinstance(missed, list):
        for item in missed:
            if isinstance(item, dict) and item.get("surface"):
                out.setdefault("__missed__", {}).setdefault("items", []).append(
                    {"surface": str(item["surface"]), "page": item.get("page")}
                )
    return out


def _strip_fence(raw: str) -> str:
    match = re.search(r"```[A-Za-z0-9_+-]*\s*(.*?)```", raw, re.DOTALL)
    return match.group(1).strip() if match else raw.strip()


def _one_of(value: Any, allowed: Sequence[str], fallback: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else fallback


def _expl(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number in (0, 1, 2) else 0


def apply_deterministic(cards: Sequence[Card], verdicts: dict[str, dict[str, Any]]) -> None:
    """程序判定的部分覆盖模型的判断（就地改）。"""
    for card in cards:
        verdict = verdicts.get(card.card_id)
        if verdict is None:
            # 契约 §6 的 kw 只有 accept / boundary / reject 三个取值；
            # 模型没判出来的，按「不做卡」记（reject），并在理由里说明。
            verdicts[card.card_id] = {
                "kw": "reject",
                "evidence": "missing",
                "expl": 0,
                "fatal": False,
                "note": NO_VERDICT_NOTE,
            }
            verdict = verdicts[card.card_id]
        notes: list[str] = [verdict["note"]] if verdict["note"] else []

        if not card.front_in_text:
            # front 不是原文 → 严重错误。
            verdict["fatal"] = True
            verdict["kw"] = "reject" if verdict["kw"] != "boundary" else verdict["kw"]
            notes.append("front 不是原文")

        if not card.evidence:
            verdict["evidence"] = "missing"
            notes.append("没有依据句")
        elif not card.evidence_in_text:
            verdict["evidence"] = "missing"
            notes.append("依据句在原文里找不到")
        elif verdict["evidence"] == "missing":
            verdict["evidence"] = "ok"
            notes.append("依据句能在原文里定位")

        length = len(re.sub(r"\s+", "", card.back))
        if length < EXPL_MIN or length > EXPL_MAX:
            verdict["expl"] = min(verdict["expl"], 1)
            notes.append(f"解释 {length} 字，超出 {EXPL_MIN}–{EXPL_MAX} 字")
        if card.explanation_is_copy:
            verdict["expl"] = min(verdict["expl"], 1)
            notes.append("解释只是照抄依据原句")
        if verdict["fatal"]:
            verdict["expl"] = 0

        verdict["note"] = "；".join(notes)[:200]


def missed_items(verdicts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """从判定结果里取出「漏掉的值得学的词」（契约 §6 的 ``missed``）。"""
    return list(verdicts.get("__missed__", {}).get("items", []))


def build_records(
    cards: Sequence[Card],
    verdicts: dict[str, dict[str, Any]],
    *,
    reviewer: str = "agent:agent",
) -> list[dict[str, Any]]:
    """把判定落成审核记录（``review/*.jsonl`` 的一行，契约 §6）。

    引擎自己出题、agent 交卷的路径（:mod:`bookcard.agentflow`）和内部直接调
    模型的路径共用这一份，免得两处口径不一样。
    """
    records: list[dict[str, Any]] = []
    for card in cards:
        verdict = verdicts[card.card_id]
        records.append(
            {
                "card_id": card.card_id,
                "kw": verdict["kw"],
                "evidence": verdict["evidence"],
                "expl": verdict["expl"],
                "fatal": bool(verdict["fatal"]),
                "note": verdict["note"],
                "reviewer": reviewer,
                "at": _now_iso(),
            }
        )
    return records


def judge(
    context: BookContext,
    cards: Sequence[Card],
    *,
    batch_size: int = DEFAULT_BATCH,
    reviewer: str = "agent:agent",
    workers: int = 4,
    max_tokens: int = MAX_TOKENS,
    effort: str = EFFORT,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """判定一批卡，返回 ``(审核记录, 漏掉的词)``。

    ``max_tokens`` / ``effort`` 给评审模型用，默认沿用初标器的 4096 / low，
    老调用不受影响。benchmark 的评审模型思考强度更高（``docs/bench-v1.md`` §0），
    调这两个参数就行，不用改这里的常量。
    """
    batches = [list(cards[i : i + batch_size]) for i in range(0, len(cards), batch_size)]
    results: dict[str, dict[str, Any]] = {}
    lock = threading.Lock()

    def run_batch(batch: Sequence[Card]) -> None:
        user = render_batch(batch)
        try:
            raw = llm.complete(
                SYSTEM, user, max_tokens=max_tokens, temperature=0, effort=effort
            )
            parsed = parse_verdicts(raw, batch)
        except Exception as exc:  # noqa: BLE001 - 判不了就记失败，别中断整批
            logger.warning("一组 %s 张卡的判定失败：%s", len(batch), exc)
            parsed = {}
        with lock:
            results.update(parsed)

    if workers <= 1:
        for batch in batches:
            run_batch(batch)
    else:
        threads: list[threading.Thread] = []
        cursor = 0
        cursor_lock = threading.Lock()

        def worker() -> None:
            nonlocal cursor
            while True:
                with cursor_lock:
                    if cursor >= len(batches):
                        return
                    batch = batches[cursor]
                    cursor += 1
                run_batch(batch)

        threads = [threading.Thread(target=worker, name=f"judge-{i}") for i in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    apply_deterministic(cards, results)

    records = build_records(cards, results, reviewer=reviewer)
    missed = missed_items(results)
    return records, missed


def write_records(path: Path, records: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bookcard.judge",
        description="试抽的卡片判定（契约 §6「卡片判定」）",
    )
    parser.add_argument("dir", type=Path, help="一本书的工作目录")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH, help="一次判几张卡")
    parser.add_argument("--workers", type=int, default=4, help="并发线程数")
    parser.add_argument("--reviewer", default="agent:agent", help="审核人标记")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    context = load_book_context(args.dir)
    cards_path = context.review_dir / "trial-cards.json"
    rows = load_trial_cards(cards_path)
    if not rows:
        print(f"{cards_path} 没有待判定的卡：先跑 bookcard extract <dir> --trial")
        return 2
    cards = build_cards(context, rows)
    records, missed = judge(
        context,
        cards,
        batch_size=args.batch,
        reviewer=args.reviewer,
        workers=args.workers,
    )
    out = context.review_dir / "trial-agent.jsonl"
    write_records(out, records)
    if missed:
        (context.review_dir / "trial-missed.json").write_text(
            json.dumps(missed, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    qualified = sum(
        1
        for record in records
        if record["kw"] == "accept"
        and record["evidence"] == "ok"
        and record["expl"] >= 1
        and not record["fatal"]
    )
    fatal = sum(1 for record in records if record["fatal"])
    print(
        f"判定 {len(records)} 张卡：合格 {qualified}"
        f"（{qualified / len(records):.1%}），fatal {fatal} → {out}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())


#: 原文里可能被整个丢掉的右括号（PDF 字形映射坏了时常见）
_CLOSABLE = "）】》」』〕)]｝"

#: 依据句两端可能被模型套上的引号 / 书名号，剥掉再找。
_EVIDENCE_WRAPPERS = "「」『』“”\"'《》〈〉（）()【】[]"


_GARBLE_RE = re.compile(r"[\ue000-\uf8ff\u0530-\u058f]")















# --------------------------------------------------------------------------- 试抽与审核


def write_trial_cards(path: Path, cards: Sequence[Card]) -> int:
    """把待判定的卡写成 JSON（给 agent 逐张判定用）。"""
    payload = {
        "created_at": _now_iso(),
        "cards": [
            {
                "card_id": card.card_id,
                "sid": card.sid,
                "front": card.front,
                "back": card.back,
                "type": card.type,
                "learn_value": card.learn_value,
                "page": card.page,
                "seg_id": card.seg_id,
                "start": card.start,
                "end": card.end,
                "evidence": card.evidence,
            }
            for card in cards
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(cards)




def load_verdicts(path: Path) -> list[dict[str, Any]]:
    """读审核记录 ``review/*.jsonl``（人工判定覆盖 agent 判定）。"""
    verdicts: list[dict[str, Any]] = []
    if not path.exists():
        return verdicts
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                verdicts.append(row)
    return verdicts




def apply_verdicts(
    cards: Sequence[Card],
    verdicts: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把审核记录落到每张卡上（人工判定覆盖 agent 判定）。"""
    by_id: dict[str, dict[str, Any]] = {}
    for verdict in verdicts:
        card_id = str(verdict.get("card_id") or "")
        if not card_id:
            continue
        reviewer = str(verdict.get("reviewer") or "")
        current = by_id.get(card_id)
        if current is None or reviewer.startswith("human:"):
            by_id[card_id] = verdict

    out: list[dict[str, Any]] = []
    for card in cards:
        verdict = by_id.get(card.card_id)
        if verdict is None:
            out.append(
                {
                    "card_id": card.card_id,
                    "front": card.front,
                    "sid": card.sid,
                    "page": card.page,
                    "kw": "unsure",
                    "evidence": "missing",
                    "expl": 0,
                    "fatal": False,
                    "note": "未判定",
                    "reviewer": "",
                    "at": "",
                    "judged": False,
                }
            )
            continue
        kw = str(verdict.get("kw") or "unsure")
        evidence = str(verdict.get("evidence") or "missing")
        try:
            expl = int(verdict.get("expl"))
        except (TypeError, ValueError):
            expl = 0
        out.append(
            {
                "card_id": card.card_id,
                "front": card.front,
                "sid": card.sid,
                "page": card.page,
                "kw": kw,
                "evidence": evidence,
                "expl": expl,
                "fatal": bool(verdict.get("fatal")),
                "note": str(verdict.get("note") or ""),
                "reviewer": str(verdict.get("reviewer") or ""),
                "at": str(verdict.get("at") or ""),
                "judged": True,
            }
        )
    return out




def is_qualified(verdict: dict[str, Any]) -> bool:
    """合格 = ``kw == accept`` 且 ``evidence == ok`` 且 ``expl >= 1`` 且 ``fatal == false``。"""
    return (
        verdict.get("kw") == "accept"
        and verdict.get("evidence") == "ok"
        and int(verdict.get("expl") or 0) >= 1
        and not verdict.get("fatal")
    )




def summarize_trial(
    judged: Sequence[dict[str, Any]],
    missed: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """汇总试抽结果：合格率、fatal 数、闸门状态。

    ``missed`` 是漏掉的值得学的词（契约 §6：surface + page）。
    """
    total = len(judged)
    if total == 0:
        return {
            "cards": 0,
            "judged": 0,
            "qualified": 0,
            "pass_rate": 0.0,
            "fatal": 0,
            "missed": list(missed or []),
            "gate": {
                "status": "pending",
                "approved_by": "",
                "at": "",
                "reasons": ["没有待判定的卡"],
            },
        }
    qualified = sum(1 for verdict in judged if is_qualified(verdict))
    fatal = sum(1 for verdict in judged if verdict.get("fatal"))
    unjudged = [verdict for verdict in judged if not verdict.get("judged")]
    pass_rate = qualified / total
    reasons = [
        f"试抽 {total} 张卡，合格 {qualified} 张，合格率 {pass_rate:.1%}"
        f"（门槛 {TRIAL_PASS_RATE:.0%}）",
        f"严重错误（事实错误 / 非原文）{fatal} 张（门槛 0）",
    ]
    if unjudged:
        reasons.append(f"还有 {len(unjudged)} 张卡没判定")
    approved = pass_rate >= TRIAL_PASS_RATE and fatal == 0 and not unjudged
    return {
        "cards": total,
        "judged": total - len(unjudged),
        "qualified": qualified,
        "pass_rate": round(pass_rate, 4),
        "fatal": fatal,
        "missed": list(missed or []),
        "gate": {
            "status": "approved" if approved else "rejected",
            "approved_by": "",
            "at": _now_iso(),
            "reasons": reasons,
        },
    }




def write_trial(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
