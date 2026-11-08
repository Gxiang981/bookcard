"""tier 的测试（契约 §4）。

判定顺序（照抄 App 的 ``lib/core/front_tier.dart::resolveFrontTierFromCardFields``）：

``ignored`` ⇒ **T6** · ``mastered`` ⇒ **T7** · ``reviews == 0`` ⇒ **T2**（新）·
``next_review_at <= now`` ⇒ **T1**（到期）· 其余 ⇒ **T4**（学过）。
"""

import pytest

from bookcard.browse.db import tier_of

NOW = 1_700_000_000_000


def _state(**kwargs) -> dict:
    """造一份 state（默认：没评过、没忽略、没掌握）。"""
    base = {
        "ignored": 0,
        "mastered": 0,
        "reviews": 0,
        "next_review_at": None,
    }
    base.update(kwargs)
    return base


def test_tier_t1_due():
    """T1（到期）：评过、没到期时间点已过。"""
    assert tier_of(_state(reviews=1, next_review_at=NOW - 1), NOW) == "T1"


def test_tier_t1_due_when_next_review_is_null():
    """评过但没有到期时间 ⇒ 也算到期（App 的 ``nextReviewAtMs == null ⇒ T1``）。"""
    assert tier_of(_state(reviews=1, next_review_at=None), NOW) == "T1"


def test_tier_t2_new():
    """T2（新）：一次都没评过。"""
    assert tier_of(_state(), NOW) == "T2"


def test_tier_t4_learned():
    """T4（学过）：评过、还没到期。"""
    assert tier_of(_state(reviews=2, next_review_at=NOW + 60_000), NOW) == "T4"


def test_tier_t6_ignored():
    """T6（忽略）。"""
    assert tier_of(_state(ignored=1), NOW) == "T6"


def test_tier_t7_mastered():
    """T7（掌握）。"""
    assert tier_of(_state(mastered=1, reviews=1, next_review_at=NOW + 60_000), NOW) == "T7"


def test_ignored_wins_over_mastered():
    """🔴 忽略 + 掌握 ⇒ **T6**（忽略优先）。"""
    assert tier_of(_state(ignored=1, mastered=1, reviews=3), NOW) == "T6"


def test_mastered_wins_over_new():
    """掌握 + 一次没评过 ⇒ **T7**（掌握优先于 T2）。"""
    assert tier_of(_state(mastered=1, reviews=0), NOW) == "T7"


def test_mastered_wins_over_due():
    """掌握 + 已到期 ⇒ **T7**。"""
    assert tier_of(_state(mastered=1, reviews=2, next_review_at=NOW - 1), NOW) == "T7"


@pytest.mark.parametrize(
    ("state", "want"),
    [
        ({"ignored": 1}, "T6"),
        ({"mastered": 1}, "T7"),
        ({}, "T2"),
        ({"reviews": 1, "next_review_at": NOW - 1}, "T1"),
        ({"reviews": 1, "next_review_at": NOW + 1}, "T4"),
    ],
)
def test_tier_order(state, want):
    """判定顺序：忽略 > 掌握 > 新 > 到期 > 学过。"""
    assert tier_of(_state(**state), NOW) == want
