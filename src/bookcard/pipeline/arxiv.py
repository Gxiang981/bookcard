"""arXiv 论文源：元数据 + 许可抓取（）。

论文和书不一样：**能不能公开分发由许可说了算**，所以许可必须从 arXiv 的
abs 页上读出来，不能猜、不能编。本模块只做三件事：

1. 抓 abs 页并解析出元数据（标题、作者、版本、日期、DOI、分类……）；
2. 抓 abs 页上 ``div.abs-license`` 里的许可链接，交给
   :mod:`bookcard.pipeline.license_gate`（）映射成许可 kind；
3. 把这些拼成 ``book.json`` 的论文字段（含出处四要素）。

**只用标准库**（urllib + html.parser）**加已有的 pymupdf**，不引新依赖。
闸门判断（能不能公开包）不在这里做——那是 license_gate 的事。

许可链接在 abs 页上是绝对地址，形如::

    http://creativecommons.org/licenses/by/4.0/     → cc-by-4.0
    http://arxiv.org/licenses/nonexclusive-distrib/1.0/
    http://arxiv.org/licenses/assumed-1991-2003/      （2004 年前的文章）

本模块不自己维护映射表：``license_kind_from_uri`` 认不出就返回 ``"unknown"``。
"""

from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pymupdf

from bookcard.pipeline.license_gate import build_attribution as _license_attribution
from bookcard.pipeline.license_gate import license_kind_from_uri
from bookcard.pipeline.workdir import PipelineError

#: 抓取时用的 User-Agent（arXiv 要求带出处，别用默认的 python-urllib）。
USER_AGENT = "bookcard/0.1 (+https://arxiv.org/help/api/tou)"

#: arXiv 的站点根。
ABS_BASE = "https://arxiv.org/abs/"
PDF_BASE = "https://arxiv.org/pdf/"

#: arXiv 编号：新式 ``1706.03762``、旧式 ``hep-th/9108001`` / ``math.GT/0309136``，
#: 都可以带版本后缀 ``v7``。
_ARXIV_ID_RE = re.compile(
    r"(?:[a-z][a-z-]*(?:\.[A-Za-z]{2,})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?"
)

#: abs 页 ``<div class="dateline">`` 里的日期：「11 Jan 2022」。
_DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?,?\s+(\d{4})")

#: ``Submitted on 11 Jan 2022 ( v1 ), last revised 14 Jul 2022 (this version, v3)``
_SUBMITTED_RE = re.compile(r"Submitted on\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})")
_REVISED_RE = re.compile(r"last revised\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})")
_THIS_VERSION_RE = re.compile(r"this version,\s*v(\d+)")
_ANY_VERSION_RE = re.compile(r"\bv(\d+)\b")

#: ``citation_*`` 里的日期（``2022/01/11``）。
_SLASH_DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")

#: 分类代码：``(cs.CL)``、``(math.CO)``、``(hep-th)``、``(physics.optics)``。
_CATEGORY_RE = re.compile(r"\(([a-z][a-z-]*(?:\.[A-Za-z][A-Za-z0-9-]*)?)\)")

#: 月份英文缩写 → 数字。
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

#: HTML 里没有结束标签的标签（不能拿它们算深度）。
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class ArxivError(PipelineError):
    """arXiv 抓取里可以预期的失败（网络不通、404、编号不合法……）。

    继承 :class:`~bookcard.pipeline.workdir.PipelineError`，流水线 CLI 会
    统一把它变成「中文错误 + 非 0 退出码」。
    """


class _AbsPageParser(HTMLParser):
    """从 arXiv 的 abs 页里抠出我们要的那几块。

    只用标准库的 :mod:`html.parser`；找不到的字段就留空，不编。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metas: dict[str, list[str]] = {}
        self.links: list[tuple[str, str]] = []
        self.license_hrefs: list[str] = []
        self.author_links: list[str] = []
        self.buffers: dict[str, list[str]] = {}
        self._capture: str | None = None
        self._capture_depth = 0
        self._depth = 0

    # ---- HTMLParser 回调 ----

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """开始标签：收 meta / link，决定要不要开始捕获某一块。"""
        if tag not in _VOID_TAGS:
            self._depth += 1
        attributes = {key.lower(): (value or "") for key, value in attrs}

        if tag == "meta":
            name = attributes.get("name", "").lower()
            if name:
                self.metas.setdefault(name, []).append(attributes.get("content", ""))
        elif tag == "link":
            self.links.append((attributes.get("rel", "").lower(), attributes.get("href", "")))

        if self._capture is None:
            capture = self._capture_target(tag, attributes)
            if capture is not None:
                self._capture = capture
                self._capture_depth = self._depth
                self.buffers.setdefault(capture, [])
        elif tag == "a":
            href = attributes.get("href", "")
            if self._capture == "license":
                self.license_hrefs.append(href)
            elif self._capture == "authors":
                self.author_links.append(href)

    def handle_endtag(self, tag: str) -> None:
        """结束标签：块捕获到头了就收工。"""
        if tag in _VOID_TAGS:
            return
        if self._capture is not None and self._depth <= self._capture_depth:
            self._capture = None
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        """正文：往当前捕获的块里塞。"""
        if self._capture is not None:
            self.buffers.setdefault(self._capture, []).append(data)

    # ---- 内部 ----

    @staticmethod
    def _capture_target(tag: str, attributes: dict[str, str]) -> str | None:
        """这个标签要不要开始捕获（返回捕获块的名字）。"""
        classes = attributes.get("class", "").split()
        if tag == "div":
            if "abs-license" in classes:
                return "license"
            if "authors" in classes:
                return "authors"
            if "dateline" in classes:
                return "dateline"
            if "submission-history" in classes:
                return "history"
        elif tag == "h1" and "title" in classes:
            return "title"
        elif tag == "td":
            if "jref" in classes:
                return "jref"
            if "subjects" in classes:
                return "subjects"
        elif tag == "span" and "primary-subject" in classes:
            return "primary"
        return None

    def text_of(self, name: str) -> str:
        """某个捕获块的纯文本（拼起来、压空白）。"""
        raw = "".join(self.buffers.get(name, []))
        return re.sub(r"\s+", " ", raw).strip()


def _meta_values(parser: _AbsPageParser, name: str) -> list[str]:
    """取某个 ``<meta name=...>`` 的所有非空值。"""
    return [value.strip() for value in parser.metas.get(name, []) if value.strip()]


def _first_meta(parser: _AbsPageParser, name: str) -> str:
    """取某个 ``<meta name=...>`` 的第一个值（没有就是空串）。"""
    values = _meta_values(parser, name)
    return values[0] if values else ""


def _normalize_date(text: str) -> str:
    """把常见日期写法归一成 ``YYYY-MM-DD``；认不出就返回空串。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    match = _SLASH_DATE_RE.search(raw)
    if match:
        year, month, day = (int(part) for part in match.groups())
        return f"{year:04d}-{month:02d}-{day:02d}"
    match = _DATE_RE.search(raw)
    if match:
        day, month_name, year = match.groups()
        month = _MONTHS.get(month_name.lower())
        if month:
            return f"{int(year):04d}-{month:02d}-{int(day):02d}"
    return ""


def _normalize_author(name: str) -> str:
    """arXiv 的 meta 里作者是「姓, 名」，出处里写成「名 姓」更好读。

    ``"Zhmoginov, Andrey"`` → ``"Andrey Zhmoginov"``；没有逗号的原样保留。
    """
    raw = (name or "").strip()
    if "," not in raw:
        return raw
    surname, _, given = raw.partition(",")
    surname = surname.strip()
    given = given.strip()
    if not surname or not given:
        return raw
    return f"{given} {surname}"


def _extract_id(text: str) -> str:
    """从一段文本（URL 或编号）里抠出 arXiv 编号。"""
    match = _ARXIV_ID_RE.search(text or "")
    return match.group(0) if match else ""


def _split_version(arxiv_id: str) -> tuple[str, int]:
    """``"1706.03762v7"`` → ``("1706.03762", 7)``；没有 v 后缀就是 ``(id, 1)``。"""
    raw = (arxiv_id or "").strip()
    match = re.match(r"^(?P<base>.+)v(?P<version>\d+)$", raw)
    if not match:
        return raw, 1
    return match.group("base"), int(match.group("version"))


def _parse_categories(*texts: str) -> list[str]:
    """从分类栏里抠分类代码（``cs.CL`` 这种），去重且保持顺序。"""
    found: list[str] = []
    for text in texts:
        for code in _CATEGORY_RE.findall(text or ""):
            if code not in found:
                found.append(code)
    return found


def _parse_dateline(dateline: str) -> dict[str, Any]:
    """解析 abs 页的 ``<div class="dateline">``。

    实测的样子::

        [Submitted on 11 Jan 2022 ( v1 ), last revised 14 Jul 2022 (this version, v3)]

    老文章只有 ``[Submitted on 14 Aug 1991]``（没有 v 后缀 ⇒ 版本 1）。
    """
    raw = dateline or ""
    published = ""
    match = _SUBMITTED_RE.search(raw)
    if match:
        published = _normalize_date(match.group(1))
    updated = ""
    match = _REVISED_RE.search(raw)
    if match:
        updated = _normalize_date(match.group(1))
    version = 0
    match = _THIS_VERSION_RE.search(raw)
    if match:
        version = int(match.group(1))
    else:
        versions = [int(item) for item in _ANY_VERSION_RE.findall(raw)]
        version = max(versions) if versions else 0
    return {"published": published, "updated": updated, "version": version}


def parse_abs_page(html: str) -> dict:
    """解析 arXiv 的 abs 页，**不联网**（纯函数）。

    实测 abs 页上有的东西：

    - ``<meta name="citation_title">`` / ``citation_author``（多个，形如
      ``"Zhmoginov, Andrey"``，即「姓, 名」）/ ``citation_date``（提交日）
      / ``citation_online_date``（最后修订日）/ ``citation_doi``
      / ``citation_pdf_url`` / ``citation_arxiv_id``；
    - ``<div class="dateline">`` 里的版本和日期；
    - ``<div class="abs-license">`` 里的许可链接（第一个 ``<a href>``）；
    - ``<td class="tablecell jref">``（期刊出处）、``<td class="tablecell subjects">``
      （分类）、作者块。

    解析不到的字段一律留空字符串 / 空列表，**不编**。

    返回::

        {
            "arxiv_id", "base_id", "version", "title", "authors",
            "published", "updated", "doi", "journal_ref", "categories",
            "license_uri", "license_kind", "pdf_url", "abs_url",
        }
    """
    parser = _AbsPageParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # pragma: no cover - HTML 残缺不该让整页失败
        pass

    dateline = _parse_dateline(parser.text_of("dateline"))
    history = _parse_dateline(parser.text_of("history"))

    # ---- 编号 ----
    base_id = ""
    version = 0
    for candidate in _meta_values(parser, "citation_arxiv_id"):
        found = _extract_id(candidate)
        if found:
            base_id, id_version = _split_version(found)
            version = id_version
            break
    if not base_id:
        for candidate in _meta_values(parser, "citation_pdf_url"):
            found = _extract_id(candidate)
            if found:
                base_id, id_version = _split_version(found)
                version = id_version
                break
    if not base_id:
        for _rel, href in parser.links:
            if "arxiv.org" not in href:
                continue
            found = _extract_id(href)
            if found:
                base_id, id_version = _split_version(found)
                version = id_version
                break
    if not base_id:
        found = _extract_id(html or "")
        if found:
            base_id, id_version = _split_version(found)
            version = id_version

    # 版本：dateline 的「this version, vN」最准，其次编号自带的 v 后缀。
    if dateline["version"]:
        version = dateline["version"]
    elif history["version"]:
        version = history["version"]
    elif not version:
        version = 1

    arxiv_id = base_id if version <= 1 else f"{base_id}v{version}"

    # ---- 元数据 ----
    title = _first_meta(parser, "citation_title")
    if not title:
        title = re.sub(r"^\s*Title:\s*", "", parser.text_of("title"))

    authors = [_normalize_author(name) for name in _meta_values(parser, "citation_author")]
    if not authors:
        authors = [name.strip() for name in parser.author_links if name.strip()]

    published = _normalize_date(_first_meta(parser, "citation_date"))
    updated = _normalize_date(_first_meta(parser, "citation_online_date"))
    published = published or dateline["published"] or history["published"]
    updated = updated or dateline["updated"] or history["updated"] or published

    doi = _first_meta(parser, "citation_doi")
    journal_ref = parser.text_of("jref") or _first_meta(parser, "citation_journal_title")

    categories = _parse_categories(
        parser.text_of("subjects"),
        parser.text_of("primary"),
        _first_meta(parser, "citation_arxiv_category"),
    )

    # ---- 许可 ----
    license_uri = ""
    for href in parser.license_hrefs:
        candidate = href.strip()
        if candidate:
            license_uri = candidate
            break
    license_kind = license_kind_from_uri(license_uri) if license_uri else "unknown"

    # ---- 链接 ----
    pdf_url = _first_meta(parser, "citation_pdf_url")
    if not pdf_url and arxiv_id:
        pdf_url = f"{PDF_BASE}{arxiv_id}"
    abs_url = f"{ABS_BASE}{arxiv_id}" if arxiv_id else ""

    return {
        "arxiv_id": arxiv_id,
        "base_id": base_id,
        "version": version,
        "title": title,
        "authors": authors,
        "published": published,
        "updated": updated,
        "doi": doi,
        "journal_ref": journal_ref,
        "categories": categories,
        "license_uri": license_uri,
        "license_kind": license_kind,
        "pdf_url": pdf_url,
        "abs_url": abs_url,
    }


def validate_arxiv_id(arxiv_id: str) -> str:
    """检查编号像不像 arXiv 编号；不像就报错（中文）。"""
    raw = (arxiv_id or "").strip()
    if not raw:
        raise ArxivError("arXiv 编号不能为空（示例：1706.03762、1706.03762v7、hep-th/9108001）")
    if not _ARXIV_ID_RE.fullmatch(raw):
        raise ArxivError(
            f"arXiv 编号不合法：{raw!r}"
            "（示例：1706.03762、1706.03762v7、hep-th/9108001、math.GT/0309136）"
        )
    return raw


def abs_url_for(arxiv_id: str) -> str:
    """编号 → abs 页地址（旧式编号里的 ``/`` 不能转义掉）。"""
    quoted = urllib.parse.quote(arxiv_id.strip(), safe="/")
    return f"{ABS_BASE}{quoted}"


def fetch_abs_page(arxiv_id: str, *, timeout: float = 20.0) -> str:
    """抓 arXiv 的 abs 页，返回 HTML 文本。

    只用标准库 urllib；网络失败、404、编号不合法都给中文错误。
    """
    raw_id = validate_arxiv_id(arxiv_id)
    url = abs_url_for(raw_id)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ArxivError(f"arXiv 上没有这篇论文（404）：{raw_id}") from exc
        raise ArxivError(f"arXiv 页面请求失败（HTTP {exc.code}）：{url}") from exc
    except urllib.error.URLError as exc:
        raise ArxivError(f"arXiv 页面请求失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise ArxivError(f"arXiv 页面请求超时（{timeout} 秒）：{url}") from exc
    except OSError as exc:
        raise ArxivError(f"arXiv 页面请求失败：{exc}") from exc
    return payload.decode("utf-8", errors="replace")


def fetch_arxiv_meta(arxiv_id: str, *, timeout: float = 20.0) -> dict:
    """抓 + 解析：拿到一篇论文的元数据和许可。"""
    return parse_abs_page(fetch_abs_page(arxiv_id, timeout=timeout))


def download_pdf(arxiv_id: str, dest: Path, *, timeout: float = 60.0) -> Path:
    """把论文 PDF 下载到 ``dest``（先抓 abs 页解析出 PDF 地址）。"""
    meta = fetch_arxiv_meta(arxiv_id, timeout=timeout)
    pdf_url = str(meta.get("pdf_url") or "") or f"{PDF_BASE}{meta.get('arxiv_id') or arxiv_id}"
    request = urllib.request.Request(pdf_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        raise ArxivError(f"论文 PDF 下载失败（HTTP {exc.code}）：{pdf_url}") from exc
    except urllib.error.URLError as exc:
        raise ArxivError(f"论文 PDF 下载失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise ArxivError(f"论文 PDF 下载超时（{timeout} 秒）：{pdf_url}") from exc
    except OSError as exc:
        raise ArxivError(f"论文 PDF 下载失败：{exc}") from exc
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def paper_fingerprint(pdf_path: str | Path) -> dict:
    """论文 PDF 的指纹：``{"pages": 页数, "pdf_sha256": sha256}``。"""
    path = Path(pdf_path)
    if not path.is_file():
        raise ArxivError(f"PDF 不存在：{path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    document = pymupdf.open(path)
    try:
        pages = document.page_count
    finally:
        document.close()
    return {"pages": pages, "pdf_sha256": digest}


def build_attribution(meta: dict) -> str:
    """出处文本（作者、标题、arXiv 编号+版本、链接、许可名+许可地址）。

    形如::

        Ashish Vaswani, Noam Shazeer. "Attention Is All You Need".
        arXiv:1706.03762v7 (2023-08-02). https://arxiv.org/abs/1706.03762v7
        — License: cc-by-4.0 (http://creativecommons.org/licenses/by/4.0/)

    **格式只有一处实现**：由 :func:`bookcard.pipeline.license_gate.build_attribution`
    负责（许可闸门那边也要用同一份格式），这里只把抓回来的 meta 字段翻译成
    ``book.json`` 的字段名再交过去。
    """
    return _license_attribution(
        {
            "authors": meta.get("authors"),
            "title": meta.get("title"),
            "arxiv_id": meta.get("arxiv_id"),
            "arxiv_updated": meta.get("updated") or meta.get("published"),
            "paper_license": meta.get("license_kind") or meta.get("license_uri"),
        }
    )


def _is_empty(value: Any) -> bool:
    """空值判定（合并 book.json 时用）。"""
    return value is None or value == "" or value == [] or value == {}


def _merge_book(new: dict, existing: dict | None) -> dict:
    """把新算的字段并进已有的 book.json：**不覆盖调用方已有的值**。

    existing 里有的字段（``fingerprint`` / ``scene_mode`` / ``fp``……）原样保留，
    没有的（或空的）才用新算的；existing 里额外有的键也一并留着。
    """
    merged: dict = dict(existing or {})
    for key, value in new.items():
        if _is_empty(merged.get(key)):
            merged[key] = value
    fp = str(merged.get("fp") or "")
    if fp and _is_empty(merged.get("fp12")):
        merged["fp12"] = fp[:12]
    return merged


def build_paper_book(
    meta: dict,
    *,
    pdf_path: Path | None = None,
    existing: dict | None = None,
) -> dict:
    """把论文元数据拼成 book.json 的内容（``type: "paper"``）。

    字段与既有 book.json 兼容：既有字段缺就补空字符串，**不覆盖调用方已有值**。

    - ``fp`` = ``sha1("arxiv:<arxiv_id>:<pdf_sha256 或空>")``（40 位），
      ``fp12`` = 前 12 位——工作目录靠它认书，必须有；
    - ``fingerprint`` 只有拿到 PDF 才有（没 PDF 就不给这个键，不填假值）。
    """
    arxiv_id = str(meta.get("arxiv_id") or "")
    base_id = str(meta.get("base_id") or "")
    version = meta.get("version")
    try:
        version_number = int(version)
    except (TypeError, ValueError):
        version_number = 1

    fingerprint: dict | None = None
    if pdf_path is not None:
        fingerprint = paper_fingerprint(pdf_path)
    pdf_sha256 = str((fingerprint or {}).get("pdf_sha256") or "")

    fp = hashlib.sha1(f"arxiv:{arxiv_id}:{pdf_sha256}".encode()).hexdigest()

    book: dict[str, Any] = {
        "type": "paper",
        "title": str(meta.get("title") or ""),
        "authors": [str(name) for name in (meta.get("authors") or [])],
        "arxiv_id": arxiv_id,
        "arxiv_base_id": base_id,
        "arxiv_version": version_number,
        "arxiv_published": str(meta.get("published") or ""),
        "arxiv_updated": str(meta.get("updated") or ""),
        "doi": str(meta.get("doi") or ""),
        "journal_ref": str(meta.get("journal_ref") or ""),
        "categories": [str(code) for code in (meta.get("categories") or [])],
        "paper_license": str(meta.get("license_kind") or "unknown"),
        "paper_license_uri": str(meta.get("license_uri") or ""),
        "source_url": str(meta.get("abs_url") or (f"{ABS_BASE}{arxiv_id}" if arxiv_id else "")),
        "attribution": build_attribution(meta),
        "fp": fp,
        "fp12": fp[:12],
        "language": "",
        "publisher": "",
        "edition": "",
        "subject": "",
    }
    if fingerprint is not None:
        book["fingerprint"] = fingerprint

    return _merge_book(book, existing)
