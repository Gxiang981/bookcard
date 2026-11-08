"""browse-lite：抽完卡之后，在浏览器里轻量浏览 / 评分 / 改卡。

契约见 ``docs/browse-lite-v1.md``。后端零依赖：``sqlite3`` + ``http.server``。

- :mod:`bookcard.browse.db`：本地库（导入 / 查询 / 评分 / tier / 改卡）。
- :mod:`bookcard.browse.sm2`：App ``lib/core/sm2.dart`` 的逐行移植。
- :mod:`bookcard.browse.server`：``127.0.0.1`` 上的本地 HTTP 服务。
"""

from __future__ import annotations

from bookcard.browse.db import (
    DEFAULT_DB_PATH,
    TIER_TOKENS,
    BrowseDB,
    BrowseError,
    tier_of,
)
from bookcard.browse.sm2 import (
    UI_RATINGS,
    Sm2Result,
    compute_sm2,
    sm2_rating_of,
)

__all__ = [
    "DEFAULT_DB_PATH",
    "TIER_TOKENS",
    "UI_RATINGS",
    "BrowseDB",
    "BrowseError",
    "Sm2Result",
    "compute_sm2",
    "sm2_rating_of",
    "tier_of",
]
