"""skill 文案和 MCP 工具表要对得上。

工具数改过、skill 文案没跟上（写过「十个工具」，实际 14 个），实跑时 agent 少用了工具。
所以在这里钉死：工具个数和工具名都从代码里取，文案对不上就红。
"""

from __future__ import annotations

import re
from pathlib import Path

from bookcard.mcp.tools import TOOLS

#: 仓库里的 skill（worktree 里也是这个相对位置）。
SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "bookcard" / "SKILL.md"

#: 中文数字（到二十，够写工具个数了）。
_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _cn_to_int(text: str) -> int:
    """「十」「十四」「二十」这样的中文数字转成 int。"""
    if "十" not in text:
        return _CN_DIGITS[text]
    head, _, tail = text.partition("十")
    tens = _CN_DIGITS[head] if head else 1
    ones = _CN_DIGITS[tail] if tail else 0
    return tens * 10 + ones


def test_工具数和skill文案写的一样():
    """skill 开头写的工具个数 = 工具表里的条数。"""
    text = SKILL_PATH.read_text(encoding="utf-8")
    counts = re.findall(r"(\d+|[一二三四五六七八九十]+) 个工具", text)
    assert counts, "skill 里没写「几个工具」"
    wanted = len(TOOLS)
    parsed = {int(c) if c.isdigit() else _cn_to_int(c) for c in counts}
    assert wanted in parsed, f"skill 写的工具个数 {sorted(parsed)} 和实际 {wanted} 对不上"


def test_每个工具名skill里都有():
    """工具表里的每个工具，skill 里都要提到——漏一个 agent 就不知道有它。"""
    text = SKILL_PATH.read_text(encoding="utf-8")
    missing = [tool["name"] for tool in TOOLS if tool["name"] not in text]
    assert missing == [], f"skill 里没提到这些工具：{missing}"


def test_skill里的目录口径和契约一致():
    """skill 写的书目录位置 = 契约 §0 的位置。"""
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "$BOOKCARD_HOME/books/<fp12>/" in text
    assert "$BOOKCARD_HOME/<fp12>/" in text  # 旧位置也要写明（只读兼容）
