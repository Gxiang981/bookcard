"""论文源章节结构识别的测试（）。

**不联网**：论文的 pages.jsonl 都是本文件里现造的。
"""

import json
import zipfile

from bookcard.cli import main
from bookcard.pipeline.audit import audit
from bookcard.pipeline.locate import locate
from bookcard.pipeline.pack import pack
from bookcard.pipeline.paper_sections import build_paper_toc, detect_paper_sections
from bookcard.pipeline.select import select
from pipeline_fixtures import candidate, make_pdf, write_workdir

#: 一份论文式的 pages.jsonl（6 页）：
#:
#: - 第 1 页（front）：标题、作者、Abstract、摘要正文，外加一行「1 Introduction」
#:   ——模拟论文首页的目录式重复；
#: - 第 2 页（body）：真正的「1 Introduction」；
#: - 第 3 页（body）：「2 Related Work」；
#: - 第 4 页（body）：「2.1 Attention」（自己起一页 ⇒ level 2）；
#: - 第 5 页（body）：「3 Method」；
#: - 第 6 页（body）：「References」+ 参考文献里带编号的行。
PAPER_PAGES = [
    {
        "page": 1,
        "printed_page": "1",
        "kind": "front",
        "text_origin": "pdf_text",
        "text": (
            "Attention Is All You Need\n"
            "Ashish Vaswani, Noam Shazeer\n"
            "Abstract\n"
            "We propose the Transformer, a new simple network architecture "
            "based solely on attention mechanisms.\n"
            "1 Introduction\n"
            "Recurrent neural networks have been firmly established as state of "
            "the art models in sequence modeling.\n"
        ),
    },
    {
        "page": 2,
        "printed_page": "2",
        "kind": "body",
        "text_origin": "pdf_text",
        "text": (
            "1 Introduction\n"
            "Recurrent neural networks have been firmly established as state of "
            "the art models in sequence modeling and sequence transduction tasks.\n"
        ),
    },
    {
        "page": 3,
        "printed_page": "3",
        "kind": "body",
        "text_origin": "pdf_text",
        "text": (
            "2 Related Work\n"
            "Recurrent, convolutional, and fully connected layers are all related "
            "to each other in one important way.\n"
        ),
    },
    {
        "page": 4,
        "printed_page": "4",
        "kind": "body",
        "text_origin": "pdf_text",
        "text": (
            "2.1 Attention\n"
            "An attention function can be described as mapping a query to an "
            "output of values.\n"
        ),
    },
    {
        "page": 5,
        "printed_page": "5",
        "kind": "body",
        "text_origin": "pdf_text",
        "text": (
            "3 Method\n"
            "In this section we present the architecture of our Transformer.\n"
        ),
    },
    {
        "page": 6,
        "printed_page": "6",
        "kind": "body",
        "text_origin": "pdf_text",
        "text": (
            "References\n"
            "1 Vaswani, A. Attention is all you need. NeurIPS, 2017.\n"
            "2 Smith, J. A survey of sequence models. Journal, 2020.\n"
            "3 Brown, T. Language models are few-shot learners. 2020.\n"
        ),
    },
]


def _page(page: int, text: str, kind: str = "body") -> dict:
    """造一页 pages.jsonl 的行。"""
    return {
        "page": page,
        "printed_page": str(page),
        "kind": kind,
        "text_origin": "pdf_text",
        "text": text,
    }


def _page_with_breaks(page: int, paragraphs: list[str], kind: str = "body") -> dict:
    """造一页：每段一行，段与段之间是换行；``breaks`` 记段落界的偏移。

    真论文的 pages.jsonl 就长这样（``breaks`` 是契约 §2.1 的段落界）。
    """
    text = "\n".join(paragraphs)
    breaks: list[int] = []
    offset = 0
    for paragraph in paragraphs[:-1]:
        offset += len(paragraph)
        breaks.append(offset)
        offset += 1
    row = _page(page, text, kind)
    row["breaks"] = breaks
    return row


def _write_pages(work_dir, pages) -> None:
    """把 pages.jsonl 写进工作目录。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in pages)
    (work_dir / "pages.jsonl").write_text(body, encoding="utf-8")


# ---- 认章节 ----


def test_detect_paper_sections_shape():
    """sections.json 的形状、层级、页码范围。"""
    sections = detect_paper_sections(PAPER_PAGES)

    assert [row["sid"] for row in sections] == ["s01", "s02", "s03", "s04", "s05", "s06"]
    assert [row["title"] for row in sections] == [
        "Abstract",
        "1 Introduction",
        "2 Related Work",
        "2.1 Attention",
        "3 Method",
        "References",
    ]
    # level 由编号层级定：2.1 ⇒ 2，其余一级
    assert [row["level"] for row in sections] == [1, 1, 1, 2, 1, 1]
    # page_end = 下一节的 page_start - 1；末节到最后一页
    assert [row["page_start"] for row in sections] == [1, 2, 3, 4, 5, 6]
    assert [row["page_end"] for row in sections] == [1, 2, 3, 4, 5, 6]
    assert [row["sort_order"] for row in sections] == [1, 2, 3, 4, 5, 6]


def test_detect_paper_sections_pages_are_contiguous():
    """页码连续、无空洞无重叠。"""
    sections = detect_paper_sections(PAPER_PAGES)

    for index, row in enumerate(sections):
        assert row["page_end"] >= row["page_start"]
        if index:
            previous = sections[index - 1]
            assert row["page_start"] == previous["page_end"] + 1


def test_detect_paper_sections_ignores_front_page_duplicate():
    """首页目录式重复的编号行不算章节（正文页那次优先）。"""
    sections = detect_paper_sections(PAPER_PAGES)

    intro = [row for row in sections if row["title"] == "1 Introduction"]
    assert len(intro) == 1
    # 认的是正文页（第 2 页）那次，不是首页那次
    assert intro[0]["page_start"] == 2


def test_detect_paper_sections_ignores_numbered_reference_lines():
    """参考文献里的编号行不是章节。"""
    sections = detect_paper_sections(PAPER_PAGES)

    titles = [row["title"] for row in sections]
    assert "1 Vaswani, A. Attention is all you need. NeurIPS, 2017." not in titles
    assert "2 Smith, J. A survey of sequence models. Journal, 2020." not in titles
    assert len(sections) == 6


def test_detect_paper_sections_same_page_keeps_first_heading():
    """同一页上两个标题只认第一个（页码不许重叠），子标题记进 subheadings。"""
    pages = [
        _page(1, "2 Related Work\n2.1 Attention\n正文第一段。\n"),
        _page(2, "正文第二段。\n"),
    ]

    sections = detect_paper_sections(pages)

    assert [row["title"] for row in sections] == ["2 Related Work"]
    assert sections[0]["page_start"] == 1
    assert sections[0]["page_end"] == 2
    # 被跳过的子标题不丢：记进那一节
    assert sections[0]["subheadings"] == ["2.1 Attention"]


def test_detect_paper_sections_subheadings_empty_by_default():
    """没有子标题时 subheadings 是空列表。"""
    sections = detect_paper_sections(PAPER_PAGES)

    assert all(row["subheadings"] == [] for row in sections)


def test_detect_paper_sections_without_headings_returns_empty():
    """一个标题都没认出来就返回空列表——不许瞎编章节。"""
    pages = [
        _page(1, "这是一页没有标题的正文。\n第二行也是正文。\n"),
        _page(2, "还是正文。\n"),
    ]

    assert detect_paper_sections(pages) == []


def test_detect_paper_sections_ignores_numbered_lines_deep_in_page():
    """页首 20 行以外的编号行不算标题。"""
    filler = "\n".join(f"这是第 {index} 行正文。" for index in range(25))
    pages = [_page(1, f"{filler}\n4 Experiments\n正文。\n")]

    assert detect_paper_sections(pages) == []


def test_detect_paper_sections_ignores_year_and_list_items():
    """年份开头的行、编号列表项都不算标题。"""
    pages = [
        _page(
            1,
            "2023. 这一行以年份开头，不是标题。\n1. 2 这是编号列表项。\n真正的标题行在这里。\n",
        )
    ]

    assert detect_paper_sections(pages) == []


# ---- 真论文的样子（LaTeX 双栏：编号和标题分成两行 + 段落界） ----


def test_detect_paper_sections_latex_two_line_form():
    """LaTeX 双栏：编号独占一行、标题在下一行，也认。"""
    pages = [_page_with_breaks(1, ["1", "Introduction", "这是正文第一段，写得很长很长。"])]

    sections = detect_paper_sections(pages)

    assert [row["title"] for row in sections] == ["1 Introduction"]
    assert sections[0]["level"] == 1


def test_detect_paper_sections_uses_paragraph_breaks():
    """有段落界时靠「标题自己占一段」来认（页中间的标题也认得到）。"""
    paragraphs = [f"这是第 {index} 段正文。" for index in range(10)]
    paragraphs += ["3.2", "Attention", "这是第 11 段正文。"]
    pages = [_page_with_breaks(1, paragraphs)]

    sections = detect_paper_sections(pages)

    assert [row["title"] for row in sections] == ["3.2 Attention"]
    assert sections[0]["level"] == 2


def test_detect_paper_sections_rejects_table_rows():
    """表格里的数字行不是章节（数字太大、标题带方括号引文）。"""
    pages = [
        _page_with_breaks(
            1,
            [
                "23.75",
                "Deep-Att + PosUnk [39]",
                "25.7",
                "big",
                "5.2",
                "Transformer Base [12]",
                "这一行是正文。",
            ],
        )
    ]

    assert detect_paper_sections(pages) == []


def test_detect_paper_sections_rejects_equation_fragments():
    """公式碎片（「2 ln k」）不是章节，也不能把真章节的编号抢走。"""
    pages = [
        _page_with_breaks(
            1,
            [
                "1. Introduction",
                "这是正文第一段。",
                "2 ln k",
                "这是正文第二段。",
            ],
        ),
        _page_with_breaks(
            2,
            [
                "2. Derivation of Black String Solutions",
                "这是正文第三段。",
            ],
        ),
    ]

    sections = detect_paper_sections(pages)

    # 真章节胜出（同一个编号只认一次），公式碎片不算
    assert [row["title"] for row in sections] == [
        "1. Introduction",
        "2. Derivation of Black String Solutions",
    ]


def test_detect_paper_sections_allows_chinese_titles():
    """中文标题没有大小写，不受「首字母大写」这条管。"""
    pages = [_page_with_breaks(1, ["1", "绪论", "这是正文第一段。"])]

    sections = detect_paper_sections(pages)

    assert [row["title"] for row in sections] == ["1 绪论"]


def test_detect_paper_sections_needs_its_own_paragraph():
    """标题行没有结束一个段落 ⇒ 不认（段落中间的行不算标题）。"""
    paragraphs = ["2 相关工作这一段写得很长很长很长，" + "补足长度。" * 10]
    paragraphs += ["这是下一段正文。"]
    pages = [_page_with_breaks(1, paragraphs)]

    assert detect_paper_sections(pages) == []


# ---- toc.json ----


def test_build_paper_toc_shape():
    """toc.json 是契约 §3 的形状。"""
    sections = detect_paper_sections(PAPER_PAGES)

    toc = build_paper_toc(sections, PAPER_PAGES)

    assert toc["source"] == "layout"
    assert toc["entries"][0] == {"level": 1, "title": "Abstract", "page": 1}
    assert toc["checks"]["pages_monotonic"] is True
    assert toc["checks"]["first_body_page"] == 1
    assert toc["checks"]["covered_pages"] == [1, 6]
    assert toc["checks"]["gaps"] == []


def test_build_paper_toc_gate_is_pending():
    """闸门只到 pending：approved_by 空、at 空、绝不写 approved。"""
    sections = detect_paper_sections(PAPER_PAGES)

    toc = build_paper_toc(sections, PAPER_PAGES)
    gate_result = toc["gate"]

    assert gate_result["status"] == "pending"
    assert gate_result["status"] != "approved"
    assert gate_result["approved_by"] == ""
    assert gate_result["at"] == ""
    assert gate_result["checks_passed"] is True


def test_build_paper_toc_without_sections_is_rejected():
    """没认出章节 ⇒ 判据不过 ⇒ rejected（不是 approved）。"""
    toc = build_paper_toc([], PAPER_PAGES)

    assert toc["gate"]["status"] == "rejected"
    assert toc["gate"]["checks_passed"] is False
    assert toc["entries"] == []


# ---- CLI ----


def test_cli_paper_sections_writes_files(tmp_path, capsys):
    """``bookcard paper sections <dir>``：写 toc.json + sections.json。"""
    work = tmp_path / "paper"
    _write_pages(work, PAPER_PAGES)

    code = main(["paper", "sections", str(work)])

    assert code == 0
    sections = json.loads((work / "sections.json").read_text(encoding="utf-8"))
    assert [row["sid"] for row in sections] == ["s01", "s02", "s03", "s04", "s05", "s06"]
    assert sections[3]["title"] == "2.1 Attention"
    assert sections[3]["level"] == 2

    toc = json.loads((work / "toc.json").read_text(encoding="utf-8"))
    assert toc["gate"]["status"] == "pending"
    assert toc["gate"]["approved_by"] == ""
    assert toc["entries"][1]["title"] == "1 Introduction"

    out = capsys.readouterr().out
    assert "认出 6 节" in out
    assert "gate=pending" in out


def test_cli_paper_sections_missing_pages_fails(tmp_path, capsys):
    """缺 pages.jsonl ⇒ 中文报错 + 退出码 1。"""
    work = tmp_path / "paper"
    work.mkdir()

    code = main(["paper", "sections", str(work)])

    assert code == 1
    assert "pages.jsonl" in capsys.readouterr().out


def test_cli_paper_sections_missing_dir_fails(tmp_path, capsys):
    """目录不存在 ⇒ 中文报错 + 退出码 1。"""
    code = main(["paper", "sections", str(tmp_path / "没有这个目录")])

    assert code == 1
    assert "工作目录不存在" in capsys.readouterr().out


def test_cli_paper_sections_without_headings_fails(tmp_path, capsys):
    """没认出章节 ⇒ 退出码 1（但 toc.json / sections.json 照样写出来）。"""
    work = tmp_path / "paper"
    _write_pages(work, [_page(1, "这一页没有标题。\n"), _page(2, "还是没有。\n")])

    code = main(["paper", "sections", str(work)])

    assert code == 1
    assert "没认出章节标题行" in capsys.readouterr().out
    assert json.loads((work / "sections.json").read_text(encoding="utf-8")) == []
    toc = json.loads((work / "toc.json").read_text(encoding="utf-8"))
    assert toc["gate"]["status"] == "rejected"


def test_subheadings_stay_out_of_the_manifest(tmp_path):
    """subheadings 只在 sections.json 里，不进 manifest（App 不认这个字段）。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[candidate(1, "良渚文化", explanation="解释。")],
    )
    pdf = tmp_path / "book.pdf"
    make_pdf(pdf)
    select(work)
    locate(work, pdf)
    audit(work)

    sections_path = work / "sections.json"
    sections = json.loads(sections_path.read_text(encoding="utf-8"))
    for row in sections:
        row["subheadings"] = ["2.1 Attention"]
    sections_path.write_text(
        json.dumps(sections, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    pack(work, pdf=pdf, scene="image")

    with zipfile.ZipFile(work / "pack" / "0123456789ab.ttfc.zip") as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    libraries = (manifest.get("category") or {}).get("libraries") or []
    assert libraries
    for library in libraries:
        assert "subheadings" not in library
        assert set(library) <= {"name", "slug", "sort_order", "cards"}
