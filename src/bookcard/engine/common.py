"""引擎公共件：书指纹、元数据、PDF 定位、JSON 读写。

契约见 ``docs/contract-v1.md``。本模块只做「找数据」的事，不碰抽取逻辑。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

#: 元数据清单的默认位置（相对仓库根）。
DEFAULT_BOOKS_JSONL = "train/data/book/books.jsonl"

#: 环境变量：指定元数据清单（优先于默认位置）。
BOOKS_JSONL_ENV = "BOOKCARD_BOOKS_JSONL"

#: 本地书缓存目录。命名规则与 ``kwtrain.collect_book.cache_path_for`` 一致：
#: NAS 路径的 sha1 前 16 位，避免中文名和空格出问题。
CACHE_DIR = Path.home() / ".cache" / "bookcard" / "books"

#: 元数据字段：从 ``books.jsonl`` 带过来的那一组。缺的留空字符串，不编。
META_FIELDS = (
    "title",
    "subject",
    "stage",
    "grade",
    "volume",
    "publisher",
    "edition",
    "file_name",
    "nas_path",
    "language",
)


def sha1_file(path: str | Path) -> str:
    """PDF 全部字节的 SHA-1（小写十六进制，40 位）—— 书指纹。"""
    digest = hashlib.sha1()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha1_text(text: str) -> str:
    """文本的 SHA-1（UTF-8）。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def fp12_of(fp: str) -> str:
    """书指纹前 12 位（工作目录名）。"""
    return fp[:12]


def cache_pdf_path(nas_path: str) -> Path:
    """NAS 路径 → 本地缓存副本路径。

    规则与 ``kwtrain.collect_book.cache_path_for`` 相同（同一份实现放在这里，
    免得引擎反过来依赖训练侧包）。
    """
    key = hashlib.sha1(nas_path.encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{key}.pdf"


def find_books_jsonl(explicit: str | Path | None = None) -> Path | None:
    """找元数据清单。

    顺序：命令行给的 → 环境变量 ``BOOKCARD_BOOKS_JSONL`` → 仓库根下的
    ``train/data/book/books.jsonl``。找不到就返回 ``None``（元数据全空）。
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get(BOOKS_JSONL_ENV)
    if env:
        candidates.append(Path(env))
    # src 布局：src/bookcard/engine/common.py → 仓库根
    repo_root = Path(__file__).resolve().parents[3]
    candidates.append(repo_root / DEFAULT_BOOKS_JSONL)

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _row_value(row: dict, field: str) -> str:
    """取一行元数据里的某个字段。

    ``language`` 在清单里是 ``probe.lang``（探针判出来的语言），别处是平铺字段。
    """
    if field == "language":
        value = row.get("language") or (row.get("probe") or {}).get("lang") or ""
        return str(value)
    return str(row.get(field) or "")


def load_book_meta(fp: str, books_jsonl: str | Path | None) -> dict[str, str]:
    """按 ``book_fp`` 查一行元数据。

    查不到、或清单里没有的字段，都留空字符串 —— 契约 §1：「缺的留空字符串，不编」。
    """
    empty = {field: "" for field in META_FIELDS}
    path = Path(books_jsonl) if books_jsonl else None
    if path is None or not path.is_file():
        return empty

    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("book_fp") != fp:
                continue
            return {field: _row_value(row, field) for field in META_FIELDS}
    return empty


def resolve_pdf(
    work_dir: str | Path,
    book: dict[str, Any],
    explicit: str | Path | None = None,
) -> Path | None:
    """定位这本书的本地 PDF。

    顺序：命令行给的 → ``book.json`` 的 ``nas_path`` 推出来的缓存副本 →
    工作目录里的 PDF。找不到返回 ``None``（这时目录来源和书签都不可用）。
    """
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None

    nas_path = str(book.get("nas_path") or "")
    if nas_path:
        cached = cache_pdf_path(nas_path)
        if cached.is_file():
            return cached

    for candidate in sorted(Path(work_dir).glob("*.pdf")):
        if candidate.is_file():
            return candidate
    return None


def write_json(path: str | Path, payload: Any) -> Path:
    """写 JSON（UTF-8、缩进 2、不转义中文）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def read_json(path: str | Path) -> Any:
    """读 JSON。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> Path:
    """写 JSONL（一行一个 JSON 对象）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return target


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """读 JSONL。"""
    rows: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows
