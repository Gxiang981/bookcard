"""§2.1 合并口径 ``reflow`` 的测试。

口径（契约 §2.1）：

- 段落界 ``\\n`` 保留成 ``\\n``；
- 软折行：两侧都是 CJK ⇒ 删掉；有一侧是拉丁字母或数字 ⇒ 换成一个空格；
  行尾是 ``-`` 且两侧都是小写拉丁字母 ⇒ 删掉 ``\\n``、保留 ``-``；
- 行首行尾的空白压掉，连续空格压成一个；
- ``offset_map[i]`` = ``out[i]`` 在原 ``text`` 里的偏移。
"""

from bookcard.engine.reflow import reflow, reflow_span

# ===================== 合并口径 =====================


def test_chinese_soft_wrap_is_removed():
    """中文软折行 ⇒ 直接接上，不留空格。"""
    text = "分子之间既有引力又有斥力。当固体被压缩\n时，分子间的距离变小，作用力表现为斥力"

    merged, _ = reflow(text, [])

    assert merged == "分子之间既有引力又有斥力。当固体被压缩时，分子间的距离变小，作用力表现为斥力"


def test_mixed_cjk_and_latin_becomes_a_space():
    """中英混排的软折行 ⇒ 中间空一格。"""
    text = "第一章 Chapter\nOne 讲的是分子"

    merged, _ = reflow(text, [])

    assert merged == "第一章 Chapter One 讲的是分子"


def test_latin_soft_wrap_becomes_a_space():
    """英文软折行 ⇒ 空一格。"""
    merged, _ = reflow("the water is\nvery important", [])

    assert merged == "the water is very important"


def test_hyphen_at_line_end_keeps_the_hyphen():
    """行尾是 ``-`` 且两侧都是小写拉丁字母 ⇒ 删掉换行、保留 ``-``（不猜断词）。"""
    merged, _ = reflow("inter-\nnational", [])

    assert merged == "inter-national"


def test_hyphen_is_not_removed_when_the_next_word_is_capitalized():
    """下一段不是小写字母 ⇒ 走普通软折行（空格）。"""
    merged, _ = reflow("inter-\nNational", [])

    assert merged == "inter- National"


def test_paragraph_break_is_kept():
    """段落界保留成换行。"""
    text = "第一段。\n第二段。"

    merged, _ = reflow(text, [4])

    assert merged == "第一段。\n第二段。"


def test_without_breaks_every_newline_is_a_paragraph_break():
    """没有 ``breaks`` 的老数据 ⇒ 所有换行都当段落界（不合并）。"""
    text = "第一行\n第二行"

    merged, _ = reflow(text, None)

    assert merged == text


def test_line_ends_are_trimmed_and_spaces_collapse():
    """行首行尾的空白压掉，连续空格压成一个。"""
    merged, _ = reflow("  前面有空格   中间也有   后面也有  ", [])

    assert merged == "前面有空格 中间也有 后面也有"


# ===================== offset_map =====================


def test_offset_map_points_back_to_the_original_text():
    """``offset_map[i]`` = ``out[i]`` 在原 ``text`` 里的偏移。"""
    text = "分子之间既有引力\n又有斥力。"
    merged, offset_map = reflow(text, [])

    assert len(merged) == len(offset_map)
    for index, char in enumerate(merged):
        if char == " ":
            continue  # 插进去的空格没有原文对应
        assert text[offset_map[index]] == char


def test_offset_map_survives_a_paragraph_break():
    """段落界那个换行也在映射里。"""
    text = "第一段。\n第二段。"
    merged, offset_map = reflow(text, [4])

    assert merged == text
    assert [offset_map[index] for index in range(len(merged))] == list(range(len(text)))


def test_reflow_span_matches_the_slice_of_the_whole_page():
    """``reflow_span`` 的结果 = 整页合并后切出来的同一段（逐字一致）。"""
    text = "第一行写满了\n第二行也写满\n第三行缩进了"
    breaks = [len("第一行写满了\n第二行也写满")]
    merged, offset_map = reflow(text, breaks)

    span = reflow_span(text, breaks, 0, len("第一行写满了\n第二行也写满"))

    assert span == "第一行写满了第二行也写满"
    assert span in merged
