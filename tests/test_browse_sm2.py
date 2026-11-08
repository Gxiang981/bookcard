"""SM-2 的对拍测试：同输入同输出，抄的是 App 的 Dart 源码。

每一行期望值都是**照着 Dart 手算**的，注释里写清对应的是 Dart 哪几行
（``00_flashcard/app-flutter/lib/core/sm2.dart``）。

Dart 的算法（照抄 ``sm2.dart:74-145``）：

- 初值（``sm2.dart:99-101``）：``rep`` 为 0 就还是 0；``ease`` **等于 0 时落成 1.8**
  （不是 0 —— 这是 Python ``or`` 的 falsy 语义，App 专门在注释里强调过）；
  ``interval`` 为 0 就还是 0。
- ``again``（``sm2.dart:103-116``）：
  ``rep += 1``；``ease = round1(max(1.3, ease - 0.1))``；
  ``interval = interval > 0 ? max(1, floor(interval / 2)) : 1``。
- ``good``（``sm2.dart:117-136``）：
  ``loops = max(1, min(learning_rate, 5))``，每轮 ``rep += 1``、
  ``ease = round1(min(2.5, ease + 0.1))``；
  ``rep == 1`` ⇒ ``interval = 1``；``rep == 2`` ⇒ ``interval = 3``；
  否则 ``interval = min(365, max(1, round(prev_interval * ease)))``。
- ``next_review_at = now + interval * 86400 * 1000``（``sm2.dart:138``）。

``round1`` = ``(x * 10).round() / 10``（``sm2.dart:154``）——
Dart 的 ``double.round()`` 是**远离零**的四舍五入（``2.5 → 3``），
**不是** Python 内置 ``round()`` 的银行家舍入（``round(2.5) == 2``）。
"""

import math

import pytest

from bookcard.browse.sm2 import (
    DEFAULT_EASE_FACTOR,
    UI_RATINGS,
    compute_sm2,
    sm2_rating_of,
)

#: (当前 repetition, 当前 ease, 当前 interval, 评分, 期望 repetition, 期望 ease, 期望 interval)
#: 期望值全部照 Dart 手算；注释里写 Dart 行号。
CASES = [
    # ── 全新卡：good ⇒ ease 1.8→1.9（min(2.5, 1.8+0.1)），rep==1 ⇒ interval 1 ──
    # Dart sm2.dart:99-101（初值）+ 117-127（good 分支）
    (0, 1.8, 0, "good", 1, 1.9, 1),
    # ── 全新卡：again ⇒ ease max(1.3, 1.8-0.1)=1.7；interval 为 0 ⇒ 1 ──
    # Dart sm2.dart:103-116
    (0, 1.8, 0, "again", 1, 1.7, 1),
    # ── 第二轮 good：ease min(2.5, 1.9+0.1)=2.0；rep==2 ⇒ interval 3 ──
    # Dart sm2.dart:125-127
    (1, 1.9, 1, "good", 2, 2.0, 3),
    # ── 第二轮 again：ease max(1.3, 1.9-0.1)=1.8；interval=1 ⇒ max(1, floor(0.5))=1 ──
    # Dart sm2.dart:106-116
    (1, 1.9, 1, "again", 2, 1.8, 1),
    # ── 第三轮 good：ease 2.1；interval = round(prev 3 × 2.1) = round(6.3) = 6 ──
    # Dart sm2.dart:129-133
    (2, 2.0, 3, "good", 3, 2.1, 6),
    # ── 第三轮 again：ease max(1.3, 2.0-0.1)=1.9；interval=3 ⇒ max(1, floor(1.5))=1 ──
    # Dart sm2.dart:106-116
    (2, 2.0, 3, "again", 3, 1.9, 1),
    # ── 第四轮 good：ease 2.2；interval = round(6 × 2.2) = round(13.2) = 13 ──
    # Dart sm2.dart:129-133
    (3, 2.1, 6, "good", 4, 2.2, 13),
    # ── 第四轮 again：ease 2.0；interval=6 ⇒ max(1, floor(3)) = 3 ──
    # Dart sm2.dart:106-116
    (3, 2.1, 6, "again", 4, 2.0, 3),
    # ── 第五轮 good：ease 2.3；interval = round(13 × 2.3) = round(29.9) = 30 ──
    # Dart sm2.dart:129-133
    (4, 2.2, 13, "good", 5, 2.3, 30),
    # ── 第六轮 good：ease 2.4；interval = round(30 × 2.4) = round(72.0) = 72 ──
    # Dart sm2.dart:129-133
    (5, 2.3, 30, "good", 6, 2.4, 72),
    # ── 第七轮 good：ease 封顶 min(2.5, 2.4+0.1)=2.5；interval = round(72 × 2.5) = 180 ──
    # Dart sm2.dart:122-124（封顶）+ 129-133
    (6, 2.4, 72, "good", 7, 2.5, 180),
    # ── 第八轮 again：ease max(1.3, 2.5-0.1)=2.4；interval=180 ⇒ max(1, floor(90)) = 90 ──
    # Dart sm2.dart:106-116
    (7, 2.5, 180, "again", 8, 2.4, 90),
    # ── 第八轮 good：ease 封顶 2.5；interval = min(365, round(180 × 2.5)=450) = 365 ──
    # Dart sm2.dart:129-133（min(365, …) 的封顶）
    (7, 2.5, 180, "good", 8, 2.5, 365),
    # ── interval 封顶 365：round(300 × 2.5) = 750 ⇒ min(365, 750) = 365 ──
    # Dart sm2.dart:132
    (10, 2.5, 300, "good", 11, 2.5, 365),
    # ── ease 下限 1.3：max(1.3, 1.3-0.1) = 1.3（不是 1.2）──
    # Dart sm2.dart:106
    (0, 1.3, 0, "again", 1, 1.3, 1),
    # ── ease 上限 2.5：min(2.5, 2.5+0.1) = 2.5（不是 2.6）──
    # Dart sm2.dart:123
    (0, 2.5, 0, "good", 1, 2.5, 1),
    # ── 🔴 falsy 初值：ease 落成 1.8，不是 0 ──
    # Dart sm2.dart:100（`currentEaseFactor == 0 ? 1.8 : ...`）——App 注释里点名过的坑：
    # 老的 Dart 版没复刻这个语义，again 会得 1.3、good 会得 0.1，两端算出不同的
    # interval_days ⇒ 同步后永久分叉。
    (0, 0, 0, "again", 1, 1.7, 1),
    (0, 0, 0, "good", 1, 1.9, 1),
]


@pytest.mark.parametrize(
    ("rep", "ease", "interval", "rating", "want_rep", "want_ease", "want_interval"),
    CASES,
)
def test_sm2_matches_dart(rep, ease, interval, rating, want_rep, want_ease, want_interval):
    """逐行对拍：期望值是照 Dart 手算的。"""
    result = compute_sm2(
        rating,
        current_repetition=rep,
        current_ease_factor=ease,
        current_interval_days=interval,
        now_ms=1_000_000,
    )
    assert result.repetition == want_rep
    assert result.ease_factor == pytest.approx(want_ease)
    assert result.interval_days == want_interval


def test_sm2_learning_rate_loops():
    """``learning_rate`` 的多轮分支（Dart sm2.dart:119-135）。

    ``loops = max(1, min(learning_rate, 5))`` = 3 ⇒ 连转三轮：
    rep 1→interval 1；rep 2→interval 3；rep 3→interval round(3 × 2.1) = 6。
    """
    result = compute_sm2(
        "good",
        current_repetition=0,
        current_ease_factor=1.8,
        current_interval_days=0,
        learning_rate=3,
        now_ms=0,
    )
    assert result.repetition == 3
    assert result.ease_factor == pytest.approx(2.1)
    assert result.interval_days == 6


def test_sm2_next_review_at():
    """``next_review_at = now + interval × 86400 × 1000``（Dart sm2.dart:138）。"""
    result = compute_sm2("good", now_ms=1_000_000)
    assert result.interval_days == 1
    assert result.next_review_at_ms == 1_000_000 + 86400 * 1000


def test_sm2_rejects_unknown_rating():
    """SM-2 只认两档（Dart sm2.dart:82-84 抛 ArgumentError）。"""
    with pytest.raises(ValueError):
        compute_sm2("easy")


def test_round1_decimal_is_half_away_from_zero():
    """``_round1Decimal`` 是远离零的四舍五入（``sm2.dart:154``）。

    Python 内置 ``round()`` 是银行家舍入（``round(2.5) == 2``）⇒ 不能用。
    """
    from bookcard.browse.sm2 import _round1_decimal

    assert _round1_decimal(1.25) == 1.3  # Dart: (12.5).round() = 13
    assert _round1_decimal(1.35) == 1.4  # Dart: (13.5).round() = 14
    assert _round1_decimal(2.45) == 2.5  # Dart: (24.5).round() = 25
    assert math.floor(2.5) == 2  # 提醒：内置 round 是银行家舍入


def test_default_ease_factor_matches_app():
    """App 的 ease 初值是 1.8（``sm2.dart:77``），不是 2.5。"""
    assert DEFAULT_EASE_FACTOR == 1.8


# ===================== UI 值 → SM-2 值 =====================


@pytest.mark.parametrize(
    ("ui_rating", "want"),
    [
        ("unclear", "again"),
        ("forgot", "again"),
        ("good", "good"),
        ("easy", "good"),
    ],
)
def test_sm2_rating_of_browse_values(ui_rating, want):
    """browse-lite 的四颗键折回 SM-2（App ``rating.dart:71-81``）。"""
    assert sm2_rating_of(ui_rating) == want
    assert ui_rating in UI_RATINGS


@pytest.mark.parametrize(
    ("app_rating", "want"),
    [
        ("again_not_understood", "again"),
        ("again_not_remembered", "again"),
        ("again", "again"),
        ("good", "good"),
        ("easy", "good"),
    ],
)
def test_sm2_rating_of_app_values(app_rating, want):
    """App 自己的五个 UI 值也照抄（``rating.dart:71-81``）。"""
    assert sm2_rating_of(app_rating) == want


def test_sm2_rating_of_unknown_returns_as_is():
    """未知值**原样返回**（App 的兜底口径，不抛）。"""
    assert sm2_rating_of("whatever") == "whatever"
