"""§6 质检的测试：合格率、原文率、定位率、闸门状态、人工覆盖 agent。"""

import json

from bookcard.pipeline.audit import audit, pick_reviews
from bookcard.pipeline.select import select
from bookcard.pipeline.workdir import WorkDir
from pipeline_fixtures import candidate, write_workdir


def _review(work, records, name="agent-review.jsonl"):
    """写一条审核记录文件。"""
    review_dir = work / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / name).write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _review_record(card_id, **overrides):
    """一条合格记录（各维度都按契约 §6 的「合格」来）。"""
    record = {
        "card_id": card_id,
        "kw": "accept",
        "evidence": "ok",
        "expl": 2,
        "fatal": False,
        "note": "",
        "reviewer": "agent:agent",
        "at": "2026-11-06T01:00:00Z",
    }
    record.update(overrides)
    return record


def test_pick_reviews_prefers_human_over_agent():
    """人工判定覆盖 agent 判定。"""
    records = [
        _review_record("c1", reviewer="agent:agent", expl=0),
        _review_record("c1", reviewer="human:gxiang", expl=2, at="2026-11-06T02:00:00Z"),
    ]

    picked = pick_reviews(records)

    assert picked["c1"]["reviewer"] == "human:gxiang"
    assert picked["c1"]["expl"] == 2


def test_pick_reviews_takes_latest_within_same_reviewer():
    """同一优先级取 ``at`` 最晚的一条。"""
    records = [
        _review_record("c1", expl=0, at="2026-11-06T01:00:00Z"),
        _review_record("c1", expl=2, at="2026-11-06T03:00:00Z"),
    ]

    picked = pick_reviews(records)

    assert picked["c1"]["expl"] == 2


def test_audit_counts_locate_rate(tmp_path):
    """定位率按有 bbox 的卡算（分母是全部卡）。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[
            candidate(1, "良渚文化", explanation="解释。"),
            candidate(3, "二里头遗址", explanation="解释。"),
        ],
    )
    select(work)
    cards = WorkDir(work).cards()
    cards[0]["source"]["bbox"] = {"x": 1, "y": 2, "w": 3, "h": 4}
    WorkDir(work).cards_path.write_text(
        "".join(json.dumps(card, ensure_ascii=False) + "\n" for card in cards),
        encoding="utf-8",
    )

    report = audit(work)

    assert report["locate_rate"] == 0.5
