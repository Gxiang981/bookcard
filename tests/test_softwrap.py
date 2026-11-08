"""engine.softwrap：跨硬折行的命中判定（``span_matches``）。"""

from bookcard.engine.softwrap import span_matches


def test_span_matches_exact():
    """逐字相等 ⇒ 命中。"""
    text = "我们研究量子力学的应用。"
    assert span_matches(text, 4, 8, "量子力学") is True


def test_span_matches_cjk_soft_wrap():
    """中文软折行直接接上：``量子\\n力学`` 算 ``量子力学``。"""
    text = "我们研究量子\n力学的应用。"
    assert span_matches(text, 4, 9, "量子力学") is True


def test_span_matches_latin_soft_wrap():
    """拉丁折行空一格：``hard\\nline`` 算 ``hard line``、不算 ``hardline``。"""
    assert span_matches("hard\nline", 0, 9, "hard line") is True
    assert span_matches("hard\nline", 0, 9, "hardline") is False


def test_span_matches_out_of_range():
    """区间越界 / 空区间 ⇒ 不命中。"""
    assert span_matches("abc", -1, 2, "ab") is False
    assert span_matches("abc", 1, 9, "bc") is False
    assert span_matches("abc", 2, 2, "") is False


def test_span_matches_non_newline_mismatch():
    """切片里没有 ``\\n`` 的错位 ⇒ 不命中（软折行只管跨行）。"""
    assert span_matches("量子力学", 0, 4, "量子力X") is False
    assert span_matches("量 子力学", 0, 5, "量子力学") is False
