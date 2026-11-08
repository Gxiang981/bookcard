"""SM-2：App ``lib/core/sm2.dart`` + ``lib/core/rating.dart`` 的**逐行移植**。

契约 §4：「UI 值 → SM-2 值、SM-2 算法、interval / ease 更新**逐行**照抄 App
``lib/core/sm2.dart`` + ``lib/core/rating.dart``（有交叉对拍测试：同输入同输出）」。

移植时**不动** App 的两处语义（两边一旦漂移，两端的排程就永久分叉）：

1. **falsy 初值**（Dart ``sm2.dart:99-101``）：``ease == 0`` 落成 ``1.8``，
   不是 ``0``。Python 的 ``or`` 与 Dart 的 ``== 0`` 语义一致，照抄。
2. **舍入**（Dart ``sm2.dart:154``）：``(x * 10).round() / 10`` —— Dart 的
   ``double.round()`` 是**四舍五入到远离零的一侧**（``2.5 → 3``），
   **不是** Python 内置 ``round()`` 的银行家舍入（``round(2.5) == 2``）。
   ease 落在 1.3–2.5 之间，正好会踩到这个差别，所以这里自己实现。

App 的 UI 评分值有五个（``rating.dart:49-65``）：

===============  ==================  ============
UI 值             含义                SM-2 值
===============  ==================  ============
``good``          懂了                ``good``
``easy``          太简单了            ``good``
``again_not_understood``  没看懂      ``again``
``again_not_remembered``  懂了没记住  ``again``
``again``         旧值（兜底）        ``again``
===============  ==================  ============

browse-lite 的四颗键是 ``unclear`` / ``forgot`` / ``good`` / ``easy``
（契约 §4），前两颗对应 App 的 ``again_not_understood`` / ``again_not_remembered``，
折回规则完全一致（``rating.dart:71-81``）。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

#: App 的 ease 初值（``sm2.dart:77`` / ``sm2.dart:100``）。
DEFAULT_EASE_FACTOR = 1.8

#: App 的 ``learning_rate`` 默认值（``sm2.dart:79``）—— 恒为 1。
DEFAULT_LEARNING_RATE = 1

#: SM-2 只认两档（``sm2.dart:82-84``）。
SM2_RATINGS = ("again", "good")

#: browse-lite 的四颗评分键（契约 §4）。
UI_RATINGS = ("unclear", "forgot", "good", "easy")

#: browse-lite 的 UI 值 → App 的 UI 值（``rating.dart:49-65``）。
BROWSE_TO_APP_RATING = {
    "unclear": "again_not_understood",
    "forgot": "again_not_remembered",
    "good": "good",
    "easy": "easy",
}

#: App 的五个 UI 值 → SM-2 值（``rating.dart:71-81`` 的 ``sm2RatingOf``）。
_APP_TO_SM2 = {
    "again_not_understood": "again",
    "again_not_remembered": "again",
    "again": "again",
    "good": "good",
    "easy": "good",
}


class Sm2Error(ValueError):
    """SM-2 不认的评分值。"""


@dataclass(frozen=True)
class Sm2Result:
    """一次 SM-2 算完的结果（对应 Dart 的 ``Sm2Result``）。"""

    repetition: int
    ease_factor: float
    interval_days: int
    next_review_at_ms: int


def _round_half_away_from_zero(value: float) -> int:
    """Dart 的 ``double.round()``：四舍五入，``.5`` 远离零。

    Python 内置 ``round()`` 是银行家舍入（``round(2.5) == 2``、``round(1.5) == 2``），
    和 Dart 不一样 ⇒ 这里自己写，不能用 ``round()``。
    """
    if value >= 0:
        return math.floor(value + 0.5)
    return math.ceil(value - 0.5)


def _round1_decimal(value: float) -> float:
    """Dart ``sm2.dart:149-155`` 的 ``_round1Decimal``：保留一位小数。"""
    return _round_half_away_from_zero(value * 10) / 10


def sm2_rating_of(ui_rating: str) -> str:
    """UI 评分值 → SM-2 评分值（``'again' | 'good'``）。

    移植 App ``rating.dart:71-81`` 的 ``sm2RatingOf``：

    - ``unclear`` / ``forgot``（= App 的 ``again_not_understood`` /
      ``again_not_remembered``）→ ``again``；
    - ``good`` / ``easy`` → ``good``；
    - 未知值**原样返回**（App 的兜底口径，不抛）。

    App 的旧值 ``again`` 也认（直接落到 ``again``）。
    """
    app_rating = BROWSE_TO_APP_RATING.get(ui_rating, ui_rating)
    return _APP_TO_SM2.get(app_rating, app_rating)


def compute_sm2(
    rating: str,
    current_repetition: int = 0,
    current_ease_factor: float = DEFAULT_EASE_FACTOR,
    current_interval_days: int = 0,
    learning_rate: int = DEFAULT_LEARNING_RATE,
    now_ms: int | None = None,
) -> Sm2Result:
    """算一次 SM-2（逐行对应 Dart ``sm2.dart:74-145`` 的 ``computeSm2``）。

    参数与 Dart 同名同义；``rating`` 只接受 ``'again'`` / ``'good'``
    （先过 :func:`sm2_rating_of` 折回）。
    """
    if rating not in SM2_RATINGS:
        raise Sm2Error(f"unsupported rating: {rating}")

    now = now_ms if now_ms is not None else int(time.time() * 1000)

    # 🔴 Dart `sm2.dart:99-101`：这三行初值**必须复刻 Python 的 falsy 语义**。
    #    `ease == 0` 落成 1.8（不是 0），否则 `again` 得 1.3、`good` 得 0.1，
    #    与 App 两端算出不同的 interval_days ⇒ 同步后永久分叉。
    rep = 0 if current_repetition == 0 else current_repetition
    ease = DEFAULT_EASE_FACTOR if current_ease_factor == 0 else current_ease_factor
    interval = 0 if current_interval_days == 0 else current_interval_days

    if rating == "again":
        # Dart `sm2.dart:103-116`
        rep += 1
        raw_ease = max(1.3, ease - 0.1)
        ease = _round1_decimal(raw_ease)
        if interval > 0:
            interval = max(1, math.floor(interval / 2))
        else:
            interval = 1
    else:
        # Dart `sm2.dart:117-136`（'good'）
        loops = max(1, min(learning_rate, 5))
        prev_interval = interval
        for _ in range(loops):
            rep += 1
            raw_ease = min(2.5, ease + 0.1)
            ease = _round1_decimal(raw_ease)
            if rep == 1:
                interval = 1
            elif rep == 2:
                interval = 3
            else:
                raw_interval = _round_half_away_from_zero(prev_interval * ease)
                interval = min(365, max(1, raw_interval))
            prev_interval = interval

    # Dart `sm2.dart:138`
    next_review_at_ms = now + interval * 86400 * 1000
    return Sm2Result(
        repetition=rep,
        ease_factor=ease,
        interval_days=interval,
        next_review_at_ms=next_review_at_ms,
    )
