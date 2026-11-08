"""§2.1 段落界 ``breaks`` 的测试：四条几何判据各一个用例。

判据（契约 §2.1）——一行满足任一条，它后面的 ``\\n`` 就是段落界：

1. 这一行右端离版心右边界超过 1.5 个字宽（行没写满）；
2. 下一行首缩进超过 1.5 个字宽；
3. 下一行和这一行的行距超过本页中位行距的 1.6 倍，或字号相差超过 15%；
4. 下一行属于另一个 text block，且两块左边界相差超过 1.5 个字宽。
"""

from bookcard.engine.breaks import compute_breaks, join_lines


def line(text, x0, y0, x1, y1, size=12.0, block=0):
    """造一行（几何 + 文字）。"""
    return {
        "text": text,
        "bbox": (x0, y0, x1, y1),
        "size": size,
        "block": block,
    }


def newline_offset(lines):
    """第一个换行符在页文字里的偏移。"""
    return len(join_lines(lines[:1]))


# ===================== 判据 1：行没写满 =====================


def test_break_when_the_line_does_not_fill_the_column():
    """这一行没写满（右端离版心右边界超过 1.5 个字宽）⇒ 段落界。"""
    lines = [
        line("这一行没写满", 68, 100, 200, 112),
        line("这一行写满了", 68, 120, 400, 132),
    ]

    breaks = compute_breaks(lines)

    assert breaks == [newline_offset(lines)]


def test_no_break_when_the_line_fills_the_column():
    """写满的行不是段落界（右边界的 90 分位就是它自己）。"""
    lines = [
        line("这一行写满了", 68, 100, 400, 112),
        line("这一行也写满", 68, 120, 400, 132),
    ]

    assert compute_breaks(lines) == []


def test_no_break_for_a_narrower_measure_inside_the_column():
    """窄栏（同一列里有一段排得比别处窄）⇒ 那些行不算"没写满"。

    英语教材的阅读材料就是这样：正文按整宽排，中间一段按窄栏排。
    只看"离版心右边界多远"会把窄栏的每一行都判成段落界。
    """
    lines = [
        line("窄栏第一行", 102, 100, 320, 112),
        line("窄栏第二行", 102, 120, 320, 132),
        line("窄栏第三行", 102, 140, 314, 152),
        line("整宽的一行", 102, 160, 462, 172),
    ]

    assert compute_breaks(lines) == []


def test_break_when_the_line_is_shorter_than_the_line_above_it():
    """窄栏的最后一行（比上一行短很多）⇒ 段落界。"""
    lines = [
        line("窄栏第一行", 102, 100, 320, 112),
        line("窄栏第二行", 102, 120, 320, 132),
        line("窄栏的最后一行短", 102, 140, 257, 152),
        line("整宽的一行", 102, 160, 462, 172),
    ]

    breaks = compute_breaks(lines)

    # 段落界在窄栏**最后一行**的末尾。
    assert breaks == [len(join_lines(lines[:3]))]


# ===================== 判据 2：下一行首缩进 =====================


def test_break_when_the_next_line_is_indented():
    """下一行首缩进超过 1.5 个字宽 ⇒ 段落界。"""
    lines = [
        line("上一行顶格排", 68, 100, 400, 112),
        line("下一行缩进两字", 92, 120, 400, 132),
    ]

    breaks = compute_breaks(lines)

    assert breaks == [newline_offset(lines)]


def test_no_break_when_the_next_line_is_not_indented():
    """下一行不缩进 ⇒ 不是段落界。"""
    lines = [
        line("上一行顶格排", 68, 100, 400, 112),
        line("下一行也顶格", 68, 120, 400, 132),
    ]

    assert compute_breaks(lines) == []


# ===================== 判据 3：行距 / 字号 =====================


def test_break_when_the_line_spacing_jumps():
    """下一行的行距超过本页中位行距的 1.6 倍 ⇒ 段落界（标题、图注换块）。"""
    lines = [
        line("第一行", 68, 100, 400, 112),
        line("第二行", 68, 120, 400, 132),
        line("第三行", 68, 140, 400, 152),
        line("第四行行距突然变大", 68, 220, 400, 232),
    ]

    breaks = compute_breaks(lines)

    assert breaks == [len(join_lines(lines[:3]))]


def test_break_when_the_font_size_changes():
    """下一行字号相差超过 15% ⇒ 段落界。"""
    lines = [
        line("正文一行", 68, 100, 400, 112, size=12.0),
        line("标题一行", 68, 120, 400, 132, size=16.0),
    ]

    breaks = compute_breaks(lines)

    assert breaks == [newline_offset(lines)]


def test_no_break_when_the_font_size_is_almost_the_same():
    """字号相差不到 15% ⇒ 不是段落界。"""
    lines = [
        line("正文一行", 68, 100, 400, 112, size=12.0),
        line("另一行", 68, 120, 400, 132, size=12.6),
    ]

    assert compute_breaks(lines) == []


# ===================== 判据 4：换块（分栏、旁栏） =====================


def test_break_when_the_next_line_is_in_another_column():
    """换块且两块左边界相差超过 1.5 个字宽 ⇒ 段落界。"""
    lines = [
        line("右栏一行", 300, 100, 460, 112, block=0),
        line("左栏一行", 68, 100, 400, 112, block=1),
    ]

    breaks = compute_breaks(lines)

    assert breaks == [newline_offset(lines)]


def test_no_break_when_the_block_changes_but_the_left_edge_is_the_same():
    """换块但左边界没差 ⇒ 不是段落界（只是同一列里的另一块）。"""
    lines = [
        line("正文一行", 68, 100, 400, 112, block=0),
        line("下一行", 68, 100, 400, 112, block=1),
    ]

    assert compute_breaks(lines) == []


# ===================== 别的 =====================


def test_single_line_page_has_no_breaks():
    """只有一行 ⇒ 没有换行 ⇒ 没有段落界。"""
    assert compute_breaks([line("只有一行", 68, 100, 400, 112)]) == []


def test_breaks_point_at_newlines_in_the_page_text():
    """算出来的偏移必须落在页文字的换行符上。"""
    lines = [
        line("第一段第一行没写满", 68, 100, 200, 112),
        line("第一段第二行写满了", 68, 120, 400, 132),
        line("第二段第一行缩进了", 92, 140, 400, 152),
    ]
    text = join_lines(lines)

    breaks = compute_breaks(lines)

    assert breaks
    assert all(text[offset] == "\n" for offset in breaks)
    assert breaks == sorted(breaks)
