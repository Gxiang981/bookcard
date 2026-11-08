"""``bookcard text``：从 PDF 抽全书文字，写 ``book.json`` + ``pages.jsonl``。

对应契约 §1（书的身份）和 §2（逐页文字），§2.1（段落界 ``breaks``），
§2.2（扫描件 OCR 取字）。

**重跑只加 ``breaks``**：``text`` 和 ``text_sha1`` 必须逐字不变——目录里已经
有 ``pages.jsonl`` 时会逐页核对，不一致直接失败（契约 §2.1）。

**OCR（契约 §2.2）**：`--ocr auto|always|never`，默认 ``auto``。
``auto`` 时探针判定 ``scanned`` 就整本书走本地 OCR；数字版书**一页都不 OCR**
（不然重跑会把现有三本书的文字改掉）。OCR 页另写 ``ocr/p<4 位页号>.json``
（行框 + 在页文字里的偏移），§5 定位和 §2.1 段落界都读它。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bookcard.engine import analysis, common
from bookcard.engine import breaks as breaks_mod
from bookcard.engine import kinds as kinds_mod
from bookcard.engine import ocr as ocr_mod
from bookcard.engine.common import META_FIELDS
from bookcard.probe import probe as probe_fn
from bookcard.probe import rules


class TextChangedError(RuntimeError):
    """重跑 ``bookcard text`` 时页文字变了（契约 §2.1：只许加 ``breaks``）。"""


def _assert_text_unchanged(out_dir: Path, rows: list[dict]) -> None:
    """核对重跑后的页文字和已有 ``pages.jsonl`` 逐字一致。

    契约 §2.1：``text`` / ``text_sha1`` 必须逐字不变；不一致即失败。
    """
    pages_path = out_dir / "pages.jsonl"
    if not pages_path.is_file():
        return

    old_rows: dict[int, dict] = {}
    with pages_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            number = row.get("page")
            if isinstance(number, int):
                old_rows[number] = row

    for row in rows:
        old = old_rows.get(int(row["page"]))
        if old is None:
            continue
        if old.get("text") != row["text"]:
            raise TextChangedError(
                f"第 {row['page']} 页的文字变了（契约 §2.1：只许加 breaks）："
                f"原来 {len(str(old.get('text') or ''))} 字，现在 {len(row['text'])} 字"
            )
        if old.get("text_sha1") and old.get("text_sha1") != row["text_sha1"]:
            raise TextChangedError(
                f"第 {row['page']} 页的 text_sha1 变了"
                f"（{old.get('text_sha1')} → {row['text_sha1']}）"
            )


def _existing_scene_mode(out_dir: Path) -> str:
    """已有的 ``book.json`` 里选定的 ``scene_mode``（没有就空字符串）。

    契约 §7.1：``scene_mode`` 是整本书一个值、用户选定的——重跑 ``text`` 不许
    把它抹掉（那是 ``pack`` 的活）。
    """
    return str(_existing_book(out_dir).get("scene_mode") or "")


def _existing_book(out_dir: Path) -> dict:
    """已有的 ``book.json``（读不出就是空字典）。"""
    path = out_dir / "book.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


#: 论文源的字段（``bookcard arxiv fetch`` 写的）：跑 ``text`` 时要保住。
PAPER_FIELDS = (
    "type",
    "arxiv_id",
    "arxiv_base_id",
    "arxiv_version",
    "arxiv_published",
    "arxiv_updated",
    "doi",
    "journal_ref",
    "categories",
    "paper_license",
    "paper_license_uri",
    "source_url",
    "attribution",
)


def _merge_paper_fields(book: dict, previous: dict) -> None:
    """把论文源的字段并进新的 ``book.json``（就地改 ``book``）。

    - ``arxiv_*`` / 许可 / 出处等字段：**已有值优先**（``text`` 抽不出来，也不该编）。
    - 标题与作者：arXiv 上的比 PDF 首页抽的准（TeX 宏、大小写），用 arXiv 的。
    - ``fp`` / ``fp12``：论文的身份跟着 arxiv fetch 给的走，工作目录名和包名才一致。
    - ``fingerprint``：用书源那套（``page_text_sha1``，App「自带书」对齐要用），
      再把 PDF 的 ``pdf_sha256`` 附上去（多一个字段，不改已有形状）。
    """
    for field in PAPER_FIELDS:
        if previous.get(field) and not book.get(field):
            book[field] = previous[field]
    for field in ("title", "authors"):
        if previous.get(field):
            book[field] = previous[field]
    if previous.get("fp"):
        book["fp"] = previous["fp"]
        book["fp12"] = previous.get("fp12") or str(previous["fp"])[:12]
    fingerprint = book.get("fingerprint")
    pdf_sha256 = (previous.get("fingerprint") or {}).get("pdf_sha256")
    if isinstance(fingerprint, dict) and pdf_sha256:
        fingerprint["pdf_sha256"] = pdf_sha256


def _should_use_ocr(pdf_path: Path, ocr_mode: str) -> tuple[bool, str]:
    """这一本书要不要走 OCR（契约 §2.2）。

    返回 ``(要不要 OCR, 探针判定)``。
    """
    if ocr_mode == ocr_mod.MODE_NEVER:
        return False, ""
    if ocr_mode == ocr_mod.MODE_ALWAYS:
        return True, ""
    verdict = str(probe_fn(pdf_path).get("verdict") or "")
    return verdict == "scanned", verdict


def build_text(
    pdf_path: str | Path,
    out_dir: str | Path,
    books_jsonl: str | Path | None = None,
    *,
    ocr_mode: str = ocr_mod.MODE_AUTO,
    ocr_engine: Any = None,
) -> dict:
    """抽一本书的文字，写出 ``book.json`` 和 ``pages.jsonl``。

    参数
    ----
    ocr_mode:
        ``auto``（默认）探针判定 ``scanned`` 才走 OCR；``always`` 整本走 OCR；
        ``never`` 一页都不 OCR（数字版书）。
    ocr_engine:
        OCR 引擎（测试用；不给就用 rapidocr 的默认引擎）。

    返回一份摘要（给 CLI 打印用）。
    """
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if ocr_mode not in ocr_mod.OCR_MODES:
        raise ValueError(
            f"--ocr 只能是 {'/'.join(ocr_mod.OCR_MODES)}，现在是 {ocr_mode!r}"
        )

    fp = common.sha1_file(pdf_path)
    fp12 = common.fp12_of(fp)
    meta = common.load_book_meta(fp, books_jsonl)

    use_ocr, verdict = _should_use_ocr(pdf_path, ocr_mode)
    if use_ocr:
        # 没装 OCR 依赖就报错并给安装命令（契约 §2.2：不静默跳过）。
        # 注入了引擎（单测）就不用查依赖了。
        if ocr_engine is None:
            ocr_mod.require_ocr()
        info = analysis.analyze_ocr(pdf_path, engine=ocr_engine)
    else:
        info = analysis.analyze(pdf_path)

    pages = info["pages"]
    printed = info["printed"]["per_page"]
    text_origin = "ocr" if use_ocr else "pdf_text"

    # ---- 页面类型 ----
    appendix_ranges, exercise_ranges = kinds_mod.entry_ranges(
        info["toc_entries"],
        info["first_body_page"],
        info["last_body_page"],
    )
    kinds = kinds_mod.classify_kinds(
        pages,
        set(info["toc_range"]),
        info["first_body_page"],
        appendix_ranges,
        exercise_ranges,
    )

    # ---- book.json ----
    text_sha1 = [common.sha1_text(page["text"]) for page in pages]
    language = meta.get("language") or ""
    if not language or language == "unknown":
        # 扫描件在清单里的语言是探针判的（探针看不到文字 ⇒ "unknown"）；
        # 现在文字取到了 ⇒ 按真文字重判一次。
        language = rules.detect_lang("\n".join(page["text"] for page in pages))
    book = {
        "fp": fp,
        "fp12": fp12,
        "file_name": meta["file_name"] or pdf_path.name,
        "nas_path": meta["nas_path"],
        "page_count": len(pages),
        "title": meta["title"],
        "subject": meta["subject"],
        "stage": meta["stage"],
        "grade": meta["grade"],
        "volume": meta["volume"],
        "publisher": meta["publisher"],
        "edition": meta["edition"],
        "language": language,
        "fingerprint": {"pages": len(pages), "page_text_sha1": text_sha1},
    }

    # 这次的元数据来源可能是空的（worktree 里没有 ``train/data/book/books.jsonl``）
    # ——那就沿用已有 ``book.json`` 里的值，别把书名、NAS 路径洗掉。
    previous = _existing_book(out_dir)
    for field in META_FIELDS:
        if not book.get(field) and previous.get(field):
            book[field] = previous[field]
    if not book["nas_path"]:
        book["nas_path"] = str(previous.get("nas_path") or "")

    # 论文源（``bookcard arxiv fetch`` 先写过 book.json）：**保住**那些字段——
    # 许可、出处四要素、arXiv 编号……重跑 ``text`` 不能把它们洗掉
    # （洗掉的话，公开包就没有出处了，闸门也认不出这是论文）。
    if previous.get("type") == "paper":
        _merge_paper_fields(book, previous)

    # ---- pages.jsonl ----
    rows = []
    for page, kind in zip(pages, kinds, strict=True):
        rows.append(
            {
                "page": page["number"],
                "printed_page": printed.get(page["number"]),
                "text": page["text"],
                "text_sha1": text_sha1[page["number"] - 1],
                "text_origin": text_origin,
                "width": round(page["width"], 2),
                "height": round(page["height"], 2),
                "kind": kind,
                "breaks": breaks_mod.compute_breaks(page["lines"]),
            }
        )
    # 契约 §2.1：重跑只许加 ``breaks``——文字变了就直接失败（先于写文件）。
    _assert_text_unchanged(out_dir, rows)

    # ``scene_mode``（契约 §7.1）是整本书选定的值，重跑 ``text`` 不许把它抹掉。
    scene_mode = _existing_scene_mode(out_dir)
    if scene_mode:
        book["scene_mode"] = scene_mode

    common.write_json(out_dir / "book.json", book)
    common.write_jsonl(out_dir / "pages.jsonl", rows)

    # ---- OCR 行框（契约 §2.2）----
    ocr_stats = info.get("ocr") or {}
    if use_ocr:
        _write_ocr_files(out_dir, pages, ocr_stats)

    return {
        "fp": fp,
        "fp12": fp12,
        "out_dir": str(out_dir),
        "page_count": len(pages),
        "kinds": kinds_mod.count_kinds(kinds),
        "printed_offset": info["offset"],
        "printed_pages": len(printed),
        "toc_range": info["toc_range"],
        "toc_entries": len(info["toc_entries"]),
        "first_body_page": info["first_body_page"],
        "last_body_page": info["last_body_page"],
        "watermarks": info["watermarks"],
        "language": language,
        "breaks": sum(len(row["breaks"]) for row in rows),
        "ocr": use_ocr,
        "ocr_mode": ocr_mode,
        "verdict": verdict,
        "ocr_pages": ocr_stats.get("pages", 0),
        "ocr_seconds": ocr_stats.get("total_seconds", 0.0),
        "ocr_lines": ocr_stats.get("lines", 0),
        "ocr_per_page": ocr_stats.get("per_page", []),
    }


def _write_ocr_files(out_dir: Path, pages: list[dict], stats: dict | None = None) -> None:
    """写 ``ocr/p<4 位页号>.json``（契约 §2.2）。

    每行记 ``text`` / ``bbox``（PDF 点）/ ``conf`` / ``start``（这一行在页文字
    ``text`` 里的偏移）。写进去的是**进了页文字的那些行**（剥掉水印和行眉行脚
    之后剩下的），所以 ``start`` 对得上 ``pages.jsonl`` 里的 ``text``。

    另写 ``ocr/timing.json``：每页 OCR 耗时和总耗时（只加不改，方便复盘）。
    """
    ocr_dir = out_dir / ocr_mod.OCR_DIRNAME
    ocr_dir.mkdir(parents=True, exist_ok=True)
    for page in pages:
        records = ocr_mod.line_records(page["lines"])
        common.write_json(ocr_dir / ocr_mod.ocr_file_name(page["number"]), records)
    if stats:
        common.write_json(
            ocr_dir / "timing.json",
            {
                "dpi": ocr_mod.OCR_DPI,
                "min_conf": ocr_mod.MIN_CONF,
                "total_seconds": stats.get("total_seconds", 0.0),
                "pages": stats.get("pages", 0),
                "lines": stats.get("lines", 0),
                "per_page": stats.get("per_page", []),
            },
        )
