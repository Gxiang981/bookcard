"""§6 质检：出 ``quality_report.json``。

审核记录在 ``review/*.jsonl``，一行一条（契约 §6）::

    {"card_id", "kw", "evidence", "expl", "fatal", "note",
     "reviewer": "agent:…|human:gxiang", "at"}

**人工判定覆盖 agent 判定**：同一张卡有多条记录时，取优先级最高的那条——
``human:*`` 高于 ``agent:*``；同优先级取 ``at`` 最晚的一条。

报告字段（契约 §6）::

    {"cards", "locate_rate", "verbatim_rate", "by_section": {…},
     "gates": {"toc": …},
     "cards"}

口径说明：

- ``locate_rate`` / ``verbatim_rate`` 的分母是**全部卡**（这两项程序就能算）；
- ``evidence_ok_rate`` / ``pass_rate`` 的分母是**已审核的卡**——没审过的卡
  不算「不合格」，只算「没审」。审核覆盖率可以从 ``by_section`` 各段的
  ``reviewed`` 求和得到；
"""

from __future__ import annotations

from typing import Any

from bookcard.pipeline.workdir import PipelineError, WorkDir, write_json

#: 合格判据（契约 §6）：kw == accept ∧ evidence == ok ∧ expl >= 1 ∧ fatal == false。
PASS_KW = "accept"
PASS_EVIDENCE = "ok"
PASS_EXPL = 1

#: 审核人前缀的优先级：人工覆盖 agent。
REVIEWER_PRIORITY = {"human": 2, "agent": 1}


def _reviewer_kind(reviewer: str | None) -> str:
    """``human:gxiang`` → ``human``；认不出来按 ``agent`` 算。"""
    if not reviewer:
        return "agent"
    head = str(reviewer).split(":", 1)[0].strip().lower()
    return head if head in REVIEWER_PRIORITY else "agent"


def _record_sort_key(record: dict) -> tuple[int, str]:
    """同一张卡的多条记录怎么排序：先人工后 agent，同级按 ``at`` 晚的在前。"""
    kind = _reviewer_kind(record.get("reviewer"))
    return (REVIEWER_PRIORITY.get(kind, 1), str(record.get("at") or ""))


def pick_reviews(records: list[dict]) -> dict[str, dict]:
    """审核记录 → ``{card_id: 生效的那条}``（人工覆盖 agent）。"""
    best: dict[str, dict] = {}
    for record in records:
        card_id = str(record.get("card_id") or "")
        if not card_id:
            continue
        current = best.get(card_id)
        if current is None or _record_sort_key(record) > _record_sort_key(current):
            best[card_id] = record
    return best


def _gate_status(payload: dict) -> str:
    """从 ``toc.json`` / ``trial.json`` 里取闸门状态。"""
    gate = payload.get("gate") if isinstance(payload, dict) else None
    if not isinstance(gate, dict):
        return "unknown"
    status = gate.get("status")
    return str(status) if status else "unknown"


def audit(workdir: WorkDir | str) -> dict:
    """跑一遍质检，写出 ``quality_report.json``，返回报告。"""
    work = workdir if isinstance(workdir, WorkDir) else WorkDir(workdir)
    cards = work.cards()

    total = len(cards)
    located = sum(1 for card in cards if (card.get("source") or {}).get("bbox"))
    verbatim = sum(1 for card in cards if (card.get("source") or {}).get("verbatim"))

    by_section: dict[str, dict[str, Any]] = {}
    for card in cards:
        sid = str(card.get("sid") or "")
        stats = by_section.setdefault(
            sid,
            {
                "cards": 0,
                "located": 0,
            },
        )
        stats["cards"] += 1
        if (card.get("source") or {}).get("bbox"):
            stats["located"] += 1

    for stats in by_section.values():
        stats["locate_rate"] = (
            round(stats["located"] / stats["cards"], 4) if stats["cards"] else 0.0
        )

    try:
        toc_gate = _gate_status(work.toc())
    except PipelineError:
        toc_gate = "unknown"
    report = {
        "cards": total,
        "locate_rate": round(located / total, 4) if total else 0.0,
        "verbatim_rate": round(verbatim / total, 4) if total else 0.0,
        "by_section": by_section,
        "gates": {"toc": toc_gate},
    }

    write_json(work.quality_path, report)
    return report

