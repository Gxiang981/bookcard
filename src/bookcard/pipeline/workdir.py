"""书的工作目录：产物读写（契约 v1 §0）。

一本书一个目录，目录里各产物的名字由契约固定::

    <home>/books/<fp12>/          # <home> = $BOOKCARD_HOME（没设就是 ~/.bookcard）
      book.json  pages.jsonl  toc.json  sections.json
      candidates.jsonl  trial.json  cards.jsonl
      review/  quality_report.json  pack/  images/

本模块只管「读进来 / 写出去」，**不做业务判断**——判断分别在
:mod:`bookcard.pipeline.select`、:mod:`bookcard.pipeline.locate`、
:mod:`bookcard.pipeline.audit`、:mod:`bookcard.pipeline.pack`。

偏移口径统一：``text[start:end] == surface``（Python 码点下标）。
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any

#: 契约里固定的产物文件名。
BOOK_FILE = "book.json"
PAGES_FILE = "pages.jsonl"
TOC_FILE = "toc.json"
SECTIONS_FILE = "sections.json"
CANDIDATES_FILE = "candidates.jsonl"
TRIAL_FILE = "trial.json"
CARDS_FILE = "cards.jsonl"
REVIEW_DIR = "review"
QUALITY_FILE = "quality_report.json"
PACK_DIR = "pack"
IMAGES_DIR = "images"
OCR_DIR = "ocr"

#: 页面图在包内和工作目录里都用这个相对路径前缀。
IMAGE_PREFIX = "images/"


class PipelineError(Exception):
    """流水线里可以预期的失败（缺产物、闸门没过、格式不对）。"""


def norm(text: str) -> str:
    """归一化：NFKC → 去首尾空白 → 小写。

    契约 §5 的去重键和 ``content_fp`` 都用它。英文因此大小写不敏感，
    中文没有大小写所以不受影响。
    """
    return unicodedata.normalize("NFKC", text or "").strip().lower()


def text_sha1(text: str) -> str:
    """正文 UTF-8 字节的 SHA-1（小写十六进制）。"""
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def content_fp(front: str, back: str) -> str:
    """卡的内容指纹：``sha256(norm(front) + "\\n" + norm(back))``（契约 §5）。"""
    payload = f"{norm(front)}\n{norm(back)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_json(path: str | Path) -> Any:
    """读一个 JSON 文件。"""
    file_path = Path(path)
    if not file_path.exists():
        raise PipelineError(f"文件不存在：{file_path}")
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PipelineError(f"JSON 解析失败：{file_path}：{exc}") from exc


def write_json(path: str | Path, payload: Any) -> Path:
    """写一个 JSON 文件（UTF-8、中文不转义、两空格缩进）。"""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return file_path


def read_jsonl(path: str | Path) -> list[dict]:
    """读一个 JSONL 文件；空行跳过。"""
    file_path = Path(path)
    if not file_path.exists():
        raise PipelineError(f"文件不存在：{file_path}")
    rows: list[dict] = []
    for lineno, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise PipelineError(f"JSONL 解析失败：{file_path}:{lineno}：{exc}") from exc
    return rows


def write_jsonl(path: str | Path, rows: list[dict]) -> Path:
    """写一个 JSONL 文件。"""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    file_path.write_text(body, encoding="utf-8")
    return file_path


class WorkDir:
    """一本书的工作目录。

    只做路径拼装和产物读写；业务判断在各个 ``pipeline`` 子模块里。
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def __fspath__(self) -> str:
        return str(self.root)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"WorkDir({str(self.root)!r})"

    # ---- 路径 ----

    @property
    def book_path(self) -> Path:
        return self.root / BOOK_FILE

    @property
    def pages_path(self) -> Path:
        return self.root / PAGES_FILE

    @property
    def toc_path(self) -> Path:
        return self.root / TOC_FILE

    @property
    def sections_path(self) -> Path:
        return self.root / SECTIONS_FILE

    @property
    def candidates_path(self) -> Path:
        return self.root / CANDIDATES_FILE

    @property
    def trial_path(self) -> Path:
        return self.root / TRIAL_FILE

    @property
    def cards_path(self) -> Path:
        return self.root / CARDS_FILE

    @property
    def review_dir(self) -> Path:
        return self.root / REVIEW_DIR

    @property
    def quality_path(self) -> Path:
        return self.root / QUALITY_FILE

    @property
    def pack_dir(self) -> Path:
        return self.root / PACK_DIR

    @property
    def images_dir(self) -> Path:
        return self.root / IMAGES_DIR

    @property
    def ocr_dir(self) -> Path:
        """OCR 行框目录（契约 §2.2：``ocr/p<4 位页号>.json``）。"""
        return self.root / OCR_DIR

    def pack_path(self, fp12: str) -> Path:
        """私有包路径：``pack/<fp12>.ttfc.zip``。"""
        return self.pack_dir / f"{fp12}.ttfc.zip"

    # ---- 读 ----

    def book(self) -> dict:
        """读 ``book.json``。"""
        return read_json(self.book_path)

    def pages(self) -> list[dict]:
        """读 ``pages.jsonl``。"""
        return read_jsonl(self.pages_path)

    def sections(self) -> list[dict]:
        """读 ``sections.json``。"""
        payload = read_json(self.sections_path)
        if not isinstance(payload, list):
            raise PipelineError(f"sections.json 必须是数组：{self.sections_path}")
        return payload

    def candidates(self) -> list[dict]:
        """读 ``candidates.jsonl``。"""
        return read_jsonl(self.candidates_path)

    def cards(self) -> list[dict]:
        """读 ``cards.jsonl``。"""
        return read_jsonl(self.cards_path)

    def toc(self) -> dict:
        """读 ``toc.json``。"""
        return read_json(self.toc_path)

    def trial(self) -> dict:
        """读 ``trial.json``。"""
        return read_json(self.trial_path)

    def quality(self) -> dict:
        """读 ``quality_report.json``。"""
        return read_json(self.quality_path)

    def review_records(self) -> Iterator[dict]:
        """读 ``review/*.jsonl`` 里的所有审核记录。

        目录不存在就是没有审核记录（返回空，不报错）。
        """
        if not self.review_dir.is_dir():
            return
        for path in sorted(self.review_dir.glob("*.jsonl")):
            if path.name.startswith("."):
                continue
            yield from read_jsonl(path)
