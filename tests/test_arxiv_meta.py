"""arXiv 论文元数据与许可抓取的测试（）。

**不联网**：abs 页的 HTML 都是本文件里写的精简夹具（按实测的真实页面结构裁的）。
"""

import json
import sys
import types

import pytest

from pipeline_fixtures import make_pdf

# ---- 许可模块 ----
# license_gate.py 还没合进 main 时，先塞一个最小 stub，让本单能自己跑测试；
# 真模块合进来后这段自动失效（行为以真模块为准）。
try:  # pragma: no cover - 取决于 合入进度
    import bookcard.pipeline.license_gate  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    _license_gate = types.ModuleType("bookcard.pipeline.license_gate")

    _LICENSE_KINDS = (
        ("creativecommons.org/licenses/by/4.0/", "cc-by-4.0"),
        ("creativecommons.org/licenses/by-sa/4.0/", "cc-by-sa-4.0"),
        ("creativecommons.org/licenses/by-nc-sa/4.0/", "cc-by-nc-sa-4.0"),
        ("creativecommons.org/licenses/by-nc-nd/4.0/", "cc-by-nc-nd-4.0"),
        ("creativecommons.org/publicdomain/zero/1.0/", "cc0-1.0"),
        ("arxiv.org/licenses/nonexclusive-distrib/1.0/", "arxiv-nonexclusive-1.0"),
        ("arxiv.org/licenses/assumed-1991-2003/", "assumed-1991-2003"),
    )

    def _license_kind_from_uri(uri: str) -> str:
        """临时 stub：按 URI 认许可 kind（真模块在 里）。"""
        text = (uri or "").lower()
        for marker, kind in _LICENSE_KINDS:
            if marker in text:
                return kind
        return "unknown"

    def _license_flags(kind: str) -> dict:
        """临时 stub：许可的四个标记。"""
        return {
            "nc": kind in {"cc-by-nc-sa-4.0", "cc-by-nc-nd-4.0"},
            "nd": kind == "cc-by-nc-nd-4.0",
            "sa": kind in {"cc-by-sa-4.0", "cc-by-nc-sa-4.0"},
            "permissive": kind in {"cc-by-4.0", "cc0-1.0"},
        }

    _license_gate.license_kind_from_uri = _license_kind_from_uri
    _license_gate.license_flags = _license_flags
    sys.modules["bookcard.pipeline.license_gate"] = _license_gate

from bookcard.cli import main  # noqa: E402
from bookcard.pipeline.arxiv import (  # noqa: E402
    ArxivError,
    build_paper_book,
    fetch_arxiv_meta,
    paper_fingerprint,
    parse_abs_page,
    validate_arxiv_id,
)

# ---- abs 页夹具（按实测的真实页面结构裁的） ----

#: CC BY 4.0 + 有版本（v7）的论文页。
ABS_CC_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta name="citation_title" content="Attention Is All You Need" />
<meta name="citation_author" content="Vaswani, Ashish" />
<meta name="citation_author" content="Shazeer, Noam" />
<meta name="citation_date" content="2017/06/12" />
<meta name="citation_online_date" content="2023/08/07" />
<meta name="citation_pdf_url" content="https://arxiv.org/pdf/1706.03762" />
<meta name="citation_doi" content="10.48550/arXiv.1706.03762" />
<meta name="citation_arxiv_id" content="1706.03762" />
<link rel="canonical" href="https://arxiv.org/abs/1706.03762v7" />
</head>
<body>
<div id="abs">
<h1 class="title mathjax"><span class="descriptor">Title:</span> Attention Is All You Need</h1>
<div class="authors"><span class="descriptor">Authors:</span>
<a href="/search/?searchtype=author&amp;query=Vaswani,+Ashish">Ashish Vaswani</a>,
<a href="/search/?searchtype=author&amp;query=Shazeer,+Noam">Noam Shazeer</a>
</div>
<div class="dateline">[Submitted on 12 Jun 2017 ( v1 ),
last revised 7 Aug 2023 (this version, v7)]</div>
<div class="abs-license">
<a href="http://creativecommons.org/licenses/by/4.0/"
   title="Rights to this article" class="has_license">
<img alt="license icon" role="presentation"
     src="https://arxiv.org/icons/licenses/by-4.0.png"/>
<span>view license</span></a></div>
<table>
<tr><td class="tablecell jref">Advances in Neural Information Processing Systems 30 (2017)</td></tr>
<tr><td class="tablecell subjects"><span class="primary-subject">
Computation and Language (cs.CL)</span>; Machine Learning (cs.LG)</td></tr>
</table>
</div>
</body>
</html>
"""

#: arXiv 自家默认许可（nonexclusive-distrib）+ v3。
ABS_ARXIV_LICENSE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta name="citation_title" content="Web App Math" />
<meta name="citation_author" content="Zhmoginov, Andrey" />
<meta name="citation_date" content="2022/01/11" />
<meta name="citation_online_date" content="2022/07/14" />
<meta name="citation_pdf_url" content="https://arxiv.org/pdf/2201.04182" />
<meta name="citation_arxiv_id" content="2201.04182" />
</head>
<body>
<div id="abs">
<h1 class="title mathjax"><span class="descriptor">Title:</span> Web App Math</h1>
<div class="authors"><span class="descriptor">Authors:</span>
<a href="/search/?searchtype=author&amp;query=Zhmoginov,+Andrey">Andrey Zhmoginov</a>
</div>
<div class="dateline">[Submitted on 11 Jan 2022 ( v1 ),
last revised 14 Jul 2022 (this version, v3)]</div>
<div class="abs-license">
<a href="http://arxiv.org/licenses/nonexclusive-distrib/1.0/">view license</a></div>
</div>
</body>
</html>
"""

#: 2004 年前的文章：assumed 许可 + 旧式编号 + dateline 里没有 v 后缀。
ABS_ASSUMED_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta name="citation_title" content="Nonlinear Superposition of Infinite Sequences" />
<meta name="citation_author" content="Mann, Richard" />
<meta name="citation_date" content="1991/08/14" />
<meta name="citation_pdf_url" content="https://arxiv.org/pdf/hep-th/9108001" />
<meta name="citation_arxiv_id" content="hep-th/9108001" />
</head>
<body>
<div id="abs">
<h1 class="title mathjax"><span class="descriptor">Title:</span>
Nonlinear Superposition of Infinite Sequences</h1>
<div class="authors"><span class="descriptor">Authors:</span>
<a href="/search/?searchtype=author&amp;query=Mann,+Richard">Richard Mann</a>
</div>
<div class="dateline">[Submitted on 14 Aug 1991]</div>
<div class="abs-license">
<a href="http://arxiv.org/licenses/assumed-1991-2003/">view license</a></div>
</div>
</body>
</html>
"""

#: 页面上没有许可块（认不出 ⇒ unknown）。
ABS_NO_LICENSE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta name="citation_title" content="No License Here" />
<meta name="citation_author" content="Someone, Else" />
<meta name="citation_arxiv_id" content="2301.00001" />
</head>
<body>
<div id="abs">
<h1 class="title mathjax"><span class="descriptor">Title:</span> No License Here</h1>
</div>
</body>
</html>
"""

#: 什么元数据都没有的页面：解析不到的字段必须留空，不能编。
ABS_EMPTY_HTML = "<html><body><div id='abs'></div></body></html>"


def _fake_meta(html: str):
    """造一个假的抓取函数（测试里顶掉联网）。"""

    def _fetch(arxiv_id, *, timeout=20.0):
        return parse_abs_page(html)

    return _fetch


# ---- 解析 ----


def test_parse_cc_license_page():
    """CC BY 4.0 的 abs 页：元数据、版本、许可都要对。"""
    meta = parse_abs_page(ABS_CC_HTML)

    assert meta["arxiv_id"] == "1706.03762v7"
    assert meta["base_id"] == "1706.03762"
    assert meta["version"] == 7
    assert meta["title"] == "Attention Is All You Need"
    # meta 里的作者是「姓, 名」，出处里写成「名 姓」
    assert meta["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert meta["published"] == "2017-06-12"
    assert meta["updated"] == "2023-08-07"
    assert meta["doi"] == "10.48550/arXiv.1706.03762"
    assert meta["journal_ref"] == "Advances in Neural Information Processing Systems 30 (2017)"
    assert meta["categories"] == ["cs.CL", "cs.LG"]
    assert meta["license_uri"] == "http://creativecommons.org/licenses/by/4.0/"
    assert meta["license_kind"] == "cc-by-4.0"
    assert meta["pdf_url"] == "https://arxiv.org/pdf/1706.03762"
    assert meta["abs_url"] == "https://arxiv.org/abs/1706.03762v7"


def test_parse_arxiv_default_license():
    """arXiv 自家默认许可 ⇒ arxiv-nonexclusive-1.0。"""
    meta = parse_abs_page(ABS_ARXIV_LICENSE_HTML)

    assert meta["license_kind"] == "arxiv-nonexclusive-1.0"
    assert meta["license_uri"] == "http://arxiv.org/licenses/nonexclusive-distrib/1.0/"
    assert meta["arxiv_id"] == "2201.04182v3"
    assert meta["version"] == 3
    assert meta["published"] == "2022-01-11"
    assert meta["updated"] == "2022-07-14"
    assert meta["authors"] == ["Andrey Zhmoginov"]


def test_parse_assumed_license_old_id():
    """2004 年前的文章：assumed 许可 + 旧式编号 + 版本 1。"""
    meta = parse_abs_page(ABS_ASSUMED_HTML)

    assert meta["license_kind"] == "assumed-1991-2003"
    assert meta["arxiv_id"] == "hep-th/9108001"
    assert meta["base_id"] == "hep-th/9108001"
    assert meta["version"] == 1
    assert meta["published"] == "1991-08-14"
    # 没有修订日期 ⇒ 退回提交日期
    assert meta["updated"] == "1991-08-14"


def test_parse_without_license_is_unknown():
    """页面上没有许可块 ⇒ unknown，不许瞎猜。"""
    meta = parse_abs_page(ABS_NO_LICENSE_HTML)

    assert meta["license_kind"] == "unknown"
    assert meta["license_uri"] == ""
    assert meta["arxiv_id"] == "2301.00001"
    assert meta["version"] == 1


def test_parse_missing_fields_stay_empty():
    """解析不到的字段留空，不编。"""
    meta = parse_abs_page(ABS_EMPTY_HTML)

    assert meta["title"] == ""
    assert meta["authors"] == []
    assert meta["published"] == ""
    assert meta["updated"] == ""
    assert meta["doi"] == ""
    assert meta["journal_ref"] == ""
    assert meta["categories"] == []
    assert meta["license_kind"] == "unknown"
    assert meta["arxiv_id"] == ""


def test_version_from_id_when_no_dateline():
    """没有 dateline 时，版本从编号自带的 v 后缀来。"""
    html = """<html><head>
<meta name="citation_arxiv_id" content="2301.00001v2" />
</head><body></body></html>"""

    meta = parse_abs_page(html)

    assert meta["arxiv_id"] == "2301.00001v2"
    assert meta["base_id"] == "2301.00001"
    assert meta["version"] == 2


def test_validate_arxiv_id():
    """编号不合法要报中文错。"""
    assert validate_arxiv_id("1706.03762") == "1706.03762"
    assert validate_arxiv_id("1706.03762v7") == "1706.03762v7"
    assert validate_arxiv_id("hep-th/9108001") == "hep-th/9108001"

    with pytest.raises(ArxivError) as excinfo:
        validate_arxiv_id("不是编号")
    assert "不合法" in str(excinfo.value)


def test_fetch_arxiv_meta_is_parse_plus_fetch(monkeypatch):
    """fetch = 抓 + 解析（这里把抓的部分顶掉）。"""
    monkeypatch.setattr("bookcard.pipeline.arxiv.fetch_abs_page", lambda *a, **k: ABS_CC_HTML)

    meta = fetch_arxiv_meta("1706.03762")

    assert meta["arxiv_id"] == "1706.03762v7"
    assert meta["license_kind"] == "cc-by-4.0"


# ---- book.json 字段 ----


def test_build_paper_book_fp_is_stable():
    """fp 是 40 位 sha1、fp12 是前 12 位，同输入同输出。"""
    meta = parse_abs_page(ABS_CC_HTML)

    first = build_paper_book(meta)
    second = build_paper_book(meta)

    assert len(first["fp"]) == 40
    assert len(first["fp12"]) == 12
    assert first["fp12"] == first["fp"][:12]
    assert first["fp"] == second["fp"]
    assert first["fp12"] == second["fp12"]
    assert first["type"] == "paper"


def test_build_paper_book_attribution_format():
    """出处四要素：作者、标题、arXiv 编号+版本、链接、许可名+许可地址。"""
    meta = parse_abs_page(ABS_CC_HTML)

    book = build_paper_book(meta)

    assert book["attribution"] == (
        "Ashish Vaswani, Noam Shazeer. "
        '"Attention Is All You Need". '
        "arXiv:1706.03762v7 (2023-08-07). "
        "https://arxiv.org/abs/1706.03762v7 — "
        "License: cc-by-4.0 (http://creativecommons.org/licenses/by/4.0/)"
    )
    assert book["source_url"] == "https://arxiv.org/abs/1706.03762v7"
    assert book["paper_license"] == "cc-by-4.0"
    assert book["paper_license_uri"] == "http://creativecommons.org/licenses/by/4.0/"


def test_build_paper_book_without_pdf_has_no_fingerprint():
    """没 PDF 就不给 fingerprint 这个键，不填假值。"""
    book = build_paper_book(parse_abs_page(ABS_CC_HTML))

    assert "fingerprint" not in book
    # 其它既有字段缺就补空字符串
    assert book["language"] == ""
    assert book["publisher"] == ""
    assert book["edition"] == ""
    assert book["subject"] == ""


def test_build_paper_book_with_pdf(tmp_path):
    """有 PDF：页数 + sha256 都算出来。"""
    pdf = make_pdf(tmp_path / "paper.pdf", page_count=3)
    meta = parse_abs_page(ABS_CC_HTML)

    book = build_paper_book(meta, pdf_path=pdf)

    assert book["fingerprint"]["pages"] == 3
    assert len(book["fingerprint"]["pdf_sha256"]) == 64
    # 有 PDF 之后 fp 才算得准
    assert book["fp"] != build_paper_book(meta)["fp"]


def test_paper_fingerprint(tmp_path):
    """PDF 指纹：页数 + sha256。"""
    pdf = make_pdf(tmp_path / "paper.pdf", page_count=2)

    fingerprint = paper_fingerprint(pdf)

    assert fingerprint["pages"] == 2
    assert len(fingerprint["pdf_sha256"]) == 64


def test_build_paper_book_keeps_existing_values():
    """合并时不覆盖调用方已有的值（fingerprint / scene_mode / fp……）。"""
    meta = parse_abs_page(ABS_CC_HTML)
    existing = {
        "fp": "a" * 40,
        "fp12": "a" * 12,
        "title": "旧标题",
        "language": "zh",
        "scene_mode": "text",
        "fingerprint": {"pages": 9, "pdf_sha256": "b" * 64},
    }

    book = build_paper_book(meta, existing=existing)

    assert book["fp"] == "a" * 40
    assert book["fp12"] == "a" * 12
    assert book["title"] == "旧标题"
    assert book["language"] == "zh"
    assert book["scene_mode"] == "text"
    assert book["fingerprint"] == {"pages": 9, "pdf_sha256": "b" * 64}
    # existing 里没有的字段用新算的
    assert book["type"] == "paper"
    assert book["arxiv_id"] == "1706.03762v7"


def test_build_paper_book_fills_missing_fp():
    """existing 里有别的字段但没有 fp/fp12 时，要补上。"""
    meta = parse_abs_page(ABS_CC_HTML)

    book = build_paper_book(meta, existing={"title": "旧标题"})

    assert len(book["fp"]) == 40
    assert book["fp12"] == book["fp"][:12]


# ---- CLI ----


def test_cli_fetch_prints_chinese_summary(capsys, monkeypatch):
    """不带 --out：只打中文摘要。"""
    monkeypatch.setattr(
        "bookcard.pipeline.arxiv.fetch_arxiv_meta",
        _fake_meta(ABS_CC_HTML),
    )

    code = main(["arxiv", "fetch", "1706.03762"])

    assert code == 0
    out = capsys.readouterr().out
    assert "论文：Attention Is All You Need" in out
    assert "作者：Ashish Vaswani, Noam Shazeer" in out
    assert "编号：arXiv:1706.03762v7" in out
    assert "日期：提交 2017-06-12，更新 2023-08-07" in out
    assert "许可：cc-by-4.0（http://creativecommons.org/licenses/by/4.0/）" in out
    assert "出处：" in out


def test_cli_fetch_json_output(capsys, monkeypatch):
    """--json：stdout 是合法 JSON（给 agent 用）。"""
    monkeypatch.setattr(
        "bookcard.pipeline.arxiv.fetch_arxiv_meta",
        _fake_meta(ABS_CC_HTML),
    )

    code = main(["arxiv", "fetch", "1706.03762", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["type"] == "paper"
    assert payload["arxiv_id"] == "1706.03762v7"
    assert payload["paper_license"] == "cc-by-4.0"
    assert payload["fp12"] == payload["fp"][:12]


def test_cli_fetch_writes_book_json(tmp_path, capsys, monkeypatch):
    """--out：把论文字段写进 DIR/book.json。"""
    monkeypatch.setattr(
        "bookcard.pipeline.arxiv.fetch_arxiv_meta",
        _fake_meta(ABS_CC_HTML),
    )
    work = tmp_path / "work"
    work.mkdir()

    code = main(["arxiv", "fetch", "1706.03762", "--out", str(work)])

    assert code == 0
    book = json.loads((work / "book.json").read_text(encoding="utf-8"))
    assert book["type"] == "paper"
    assert book["title"] == "Attention Is All You Need"
    assert book["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert book["arxiv_id"] == "1706.03762v7"
    assert book["arxiv_base_id"] == "1706.03762"
    assert book["arxiv_version"] == 7
    assert book["arxiv_published"] == "2017-06-12"
    assert book["arxiv_updated"] == "2023-08-07"
    assert book["paper_license"] == "cc-by-4.0"
    assert len(book["fp"]) == 40
    assert book["fp12"] == book["fp"][:12]
    assert "book.json" in capsys.readouterr().out


def test_cli_fetch_keeps_existing_book_fields(tmp_path, monkeypatch):
    """--out 不覆盖已有 book.json 里的字段，缺 fp/fp12 才补。"""
    monkeypatch.setattr(
        "bookcard.pipeline.arxiv.fetch_arxiv_meta",
        _fake_meta(ABS_CC_HTML),
    )
    work = tmp_path / "work"
    work.mkdir()
    (work / "book.json").write_text(
        json.dumps({"title": "旧标题", "language": "zh", "scene_mode": "text"}, ensure_ascii=False),
        encoding="utf-8",
    )

    code = main(["arxiv", "fetch", "1706.03762", "--out", str(work)])

    assert code == 0
    book = json.loads((work / "book.json").read_text(encoding="utf-8"))
    assert book["title"] == "旧标题"
    assert book["language"] == "zh"
    assert book["scene_mode"] == "text"
    assert book["type"] == "paper"
    assert len(book["fp"]) == 40
    assert book["fp12"] == book["fp"][:12]


def test_cli_fetch_downloads_pdf_and_fingerprints(tmp_path, monkeypatch):
    """--download：下 PDF 到 <out>/book.pdf，再算指纹。"""
    monkeypatch.setattr(
        "bookcard.pipeline.arxiv.fetch_arxiv_meta",
        _fake_meta(ABS_CC_HTML),
    )

    def _fake_download(arxiv_id, dest, **kwargs):
        make_pdf(dest, page_count=3)
        return dest

    monkeypatch.setattr("bookcard.pipeline.arxiv.download_pdf", _fake_download)
    work = tmp_path / "work"
    work.mkdir()

    code = main(["arxiv", "fetch", "1706.03762", "--out", str(work), "--download"])

    assert code == 0
    assert (work / "book.pdf").is_file()
    book = json.loads((work / "book.json").read_text(encoding="utf-8"))
    assert book["fingerprint"]["pages"] == 3


def test_cli_fetch_missing_out_dir_fails(tmp_path, capsys, monkeypatch):
    """目录不存在就报错（本命令不建工作目录）。"""
    monkeypatch.setattr(
        "bookcard.pipeline.arxiv.fetch_arxiv_meta",
        _fake_meta(ABS_CC_HTML),
    )

    code = main(["arxiv", "fetch", "1706.03762", "--out", str(tmp_path / "没有这个目录")])

    assert code != 0
    assert "工作目录不存在" in capsys.readouterr().out


def test_cli_fetch_download_without_out_fails(capsys, monkeypatch):
    """--download 必须配 --out。"""
    monkeypatch.setattr(
        "bookcard.pipeline.arxiv.fetch_arxiv_meta",
        _fake_meta(ABS_CC_HTML),
    )

    code = main(["arxiv", "fetch", "1706.03762", "--download"])

    assert code != 0
    assert "--download 要配 --out" in capsys.readouterr().out


def test_cli_fetch_network_error_fails(capsys, monkeypatch):
    """网络失败要给中文错，退出码非 0。"""

    def _boom(*args, **kwargs):
        raise ArxivError("arXiv 页面请求失败：网络不通")

    monkeypatch.setattr("bookcard.pipeline.arxiv.fetch_arxiv_meta", _boom)

    code = main(["arxiv", "fetch", "1706.03762"])

    assert code != 0
    assert "arXiv 页面请求失败" in capsys.readouterr().out


def test_cli_fetch_invalid_id_fails(capsys, monkeypatch):
    """编号不合法要在联网之前就拦下来。"""

    def _boom(*args, **kwargs):
        raise AssertionError("不该联网")

    monkeypatch.setattr("bookcard.pipeline.arxiv.fetch_arxiv_meta", _boom)

    code = main(["arxiv", "fetch", "不是编号"])

    assert code != 0
    assert "不合法" in capsys.readouterr().out
