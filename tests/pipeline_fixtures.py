"""流水线（契约 §5–§7）测试用的夹具。

书工作目录、候选行、页面 PDF 全部在这里现造，仓库里不放二进制文件。
口径和真流水线一致：偏移是 Python 码点下标，``text[start:end] == surface``。
"""

import hashlib
import json
import zipfile
from pathlib import Path

import pymupdf

#: 夹具用的书指纹（前 12 位）。
FP12 = "0123456789ab"

#: 契约 §1 的 book.json。
BOOK = {
    "fp": FP12 + "0" * 28,
    "fp12": FP12,
    "file_name": "示例教科书.pdf",
    "nas_path": "~/books/示例/示例教科书.pdf",
    "page_count": 4,
    "title": "示例教科书",
    "subject": "历史",
    "stage": "senior",
    "grade": "",
    "volume": "上",
    "publisher": "人民教育出版社",
    "edition": "2019",
    "language": "zh",
    "domain": "历史",
    "level": "senior",
    "tags": ["通史", "中国古代史"],
    "fingerprint": {"pages": 4, "page_text_sha1": []},
}

#: 契约 §3 的 sections.json。
SECTIONS = [
    {
        "sid": "s01",
        "title": "第1课 中华文明的起源",
        "level": 2,
        "page_start": 1,
        "page_end": 2,
        "sort_order": 1,
    },
    {
        "sid": "s02",
        "title": "第2课 早期国家",
        "level": 2,
        "page_start": 3,
        "page_end": 4,
        "sort_order": 2,
    },
]

#: 契约 §2 的 pages.jsonl 的文字部分。
#: 「早期国家」故意横跨 s01（第 2 页）和 s02（第 3 页），用来测归属规则。
PAGE_TEXTS = {
    1: "良渚文化位于长江下游，是新石器时代晚期的文化遗存。",
    2: "良渚文化的城址和水利系统展现了早期国家的形态。",
    3: "二里头遗址被视为早期国家的都城遗址候选之一。",
    4: "二里头遗址发现了宫殿建筑群和青铜器。",
}

PAGE_W = 595
PAGE_H = 842
CJK_FONT = "china-s"


def sha1(text: str) -> str:
    """正文的 SHA-1。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def page_rows(texts: dict[int, str] | None = None) -> list[dict]:
    """造 ``pages.jsonl`` 的行。"""
    table = texts or PAGE_TEXTS
    return [
        {
            "page": number,
            "printed_page": str(number),
            "text": text,
            "text_sha1": sha1(text),
            "text_origin": "pdf_text",
            "width": float(PAGE_W),
            "height": float(PAGE_H),
            "kind": "body",
        }
        for number, text in sorted(table.items())
    ]


def candidate(
    page: int,
    surface: str,
    *,
    explanation: str | None,
    type_: str = "term",
    learn_value: int = 3,
    evidence: str | None = None,
    sid: str | None = None,
    chunk: int = 0,
    texts: dict[int, str] | None = None,
) -> dict:
    """造一条 ``candidates.jsonl``（一行一页，里面放一个 item）。"""
    table = texts or PAGE_TEXTS
    text = table[page]
    start = text.index(surface)
    item = {
        "front": surface,
        "type": type_,
        "start": start,
        "end": start + len(surface),
        "back": explanation,
        "learn_value": learn_value,
    }
    # 依据句已经不由模型给了（ex-v2）；这个参数留着只是为了老测试能跑。
    return {
        "seg_id": f"book:{FP12}:p{page:04d}",
        "page": page,
        "chunk": chunk,
        "sid": sid or ("s01" if page <= 2 else "s02"),
        "text_sha1": sha1(text),
        "extractor": "llm:minimax:MiniMax-M3.1-Flash:ex-v1",
        "scope": "full",
        "items": [item],
        "dropped": {"not_in_text": 0},
        "created_at": "2026-11-06T00:00:00Z",
    }


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_workdir(
    root: Path,
    *,
    candidates: list[dict],
    pages: list[dict] | None = None,
    sections: list[dict] | None = None,
    book: dict | None = None,
    toc_status: str = "approved",
    trial_status: str = "approved",
    toc_file: bool = True,
    trial_file: bool = True,
) -> Path:
    """造一个完整的工作目录，返回目录路径。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / "book.json", book or BOOK)
    _write_jsonl(root / "pages.jsonl", pages or page_rows())
    _write_json(root / "sections.json", sections or SECTIONS)
    _write_jsonl(root / "candidates.jsonl", candidates)
    if toc_file:
        entries = [
            {"level": 1, "title": section["title"], "page": section["page_start"]}
            for section in (sections or SECTIONS)
        ]
        _write_json(
            root / "toc.json",
            {
                "source": "bookmarks",
                "entries": entries,
                "checks": {"pages_monotonic": True},
                "gate": {
                    "status": toc_status,
                    "approved_by": "agent:agent",
                    "at": "2026-11-06T00:00:00Z",
                    "reasons": ["夹具"],
                },
            },
        )
    if trial_file:
        _write_json(
            root / "trial.json",
            {
                "sections": ["s01", "s02"],
                "cards": [],
                "gate": {
                    "status": trial_status,
                    "approved_by": "agent:agent",
                    "at": "2026-11-06T00:00:00Z",
                    "reasons": ["夹具"],
                },
            },
        )
    return root


def make_pdf(path: Path, texts: dict[int, str] | None = None, page_count: int = 4) -> Path:
    """造一本带文字的 PDF（150 dpi 渲染的底稿）。"""
    table = texts or PAGE_TEXTS
    doc = pymupdf.open()
    for index in range(page_count):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        text = table.get(index + 1, "")
        if text:
            # 「china-s」一次最多 48 个汉字，夹具的句子都短。
            page.insert_text((72, 100), text, fontsize=12, fontname=CJK_FONT)
    doc.save(str(path))
    doc.close()
    return path


def assert_scene_chosen_by_consistent(book_dir: Path, expected: str) -> dict:
    """场景来源三处一致（契约 §7.1）。

    ``book.json.scene_chosen_by``、``manifest.provenance.scene_chosen_by``、
    ``manifest.book.scene_chosen_by`` 必须都是 ``expected``——同一个包里
    不许有两种说法（的 D4b 就是两处不一致）。

    解包私有包看 manifest，返回 manifest 供调用方接着断言。
    """
    root = Path(book_dir)
    book = json.loads((root / "book.json").read_text(encoding="utf-8"))
    fp12 = str(book.get("fp12") or "")
    pack_path = root / "pack" / f"{fp12}.ttfc.zip"
    with zipfile.ZipFile(pack_path) as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))

    assert book.get("scene_chosen_by") == expected, book.get("scene_chosen_by")
    assert manifest["provenance"].get("scene_chosen_by") == expected
    assert manifest["book"].get("scene_chosen_by") == expected
    return manifest

