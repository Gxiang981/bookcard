"""browse-lite 测试用的夹具：现造三个小卡包（图片场景 / 文本场景 / 公开包）。

仓库里不放二进制文件，全部在这里造（与 ``tests/pipeline_fixtures.py`` 一个路子）。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

#: 图片场景包的书 id。
IMAGE_BOOK_ID = "aaaa00001111"
#: 文本场景包的书 id。
TEXT_BOOK_ID = "bbbb00002222"
#: 公开包的书 id（没有 fp12 ⇒ 走 sha1(category.name)[:12] 的兜底）。
PUBLIC_BOOK_ID = "cccc00003333"

#: 一张假的 JPEG（只需要几个魔数字节，服务端按扩展名发 content-type）。
FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32 + b"\xff\xd9"


def _manifest(
    *,
    title: str,
    visibility: str = "private",
    scene_mode: str | None = None,
    fp12: str | None = None,
    libraries: list[dict] | None = None,
    description: str = "",
) -> dict:
    """造一份 manifest。"""
    book: dict = {"title": title, "language": "zh"}
    if fp12:
        book["fp12"] = fp12
    manifest = {
        "format": "ttfc/2",
        "visibility": visibility,
        "book": book,
        "provenance": {"tool": "bookcard test", "method": "llm", "created_at": 1793600000000},
        "license": {"cards": "private" if visibility == "private" else "CC-BY-SA-4.0",
                    "page_images": "private" if visibility == "private" else "none"},
        "category": {
            "name": title,
            "description": description,
            "sort_order": 0,
            "libraries": libraries or [],
        },
    }
    if scene_mode:
        manifest["scene_mode"] = scene_mode
    return manifest


def image_manifest() -> dict:
    """图片场景的私有包：一张卡带页面图 + bbox。"""
    return _manifest(
        title="示例图书",
        description="示例出版社 2019",
        fp12=IMAGE_BOOK_ID,
        libraries=[
            {
                "name": "第一章",
                "slug": f"{IMAGE_BOOK_ID}-s01",
                "sort_order": 1,
                "cards": [
                    {
                        "front": "alpha",
                        "back": "阿尔法。",
                        "tags": ["词"],
                        "sort_order": 1,
                        "images": [
                            {
                                "file": "images/p0012.jpg",
                                "width": 1240,
                                "height": 1754,
                                "occurrences": [{"x": 120, "y": 300, "w": 64, "h": 18, "page": 12}],
                            }
                        ],
                        "source": {
                            "page": 12,
                            "printed_page": "8",
                            "verbatim": True,
                            "method": "llm",
                        },
                    },
                    {
                        "front": "beta",
                        "back": "贝塔。",
                        "tags": [],
                        "sort_order": 2,
                        "images": [],
                        "source": {"page": 13, "printed_page": "9", "method": "llm"},
                    },
                ],
            }
        ],
    )


def text_manifest() -> dict:
    """文本场景的私有包：一张卡带 ``source.context``（没有图片）。"""
    return _manifest(
        title="示例文书",
        fp12=TEXT_BOOK_ID,
        libraries=[
            {
                "name": "第一章",
                "slug": f"{TEXT_BOOK_ID}-s01",
                "sort_order": 1,
                "cards": [
                    {
                        "front": "gamma",
                        "back": "伽马。",
                        "tags": ["词"],
                        "sort_order": 1,
                        "images": [],
                        "source": {
                            "page": 7,
                            "printed_page": "3",
                            "method": "llm",
                            "context": {
                                "text": "这里出现 gamma 这个词。",
                                "highlights": [{"start": 3, "end": 8}],
                            },
                        },
                    }
                ],
            }
        ],
    )


def public_manifest() -> dict:
    """公开包：没有图片、没有 context ⇒ scene 必须是 null。"""
    return _manifest(
        title="示例公开书",
        visibility="public",
        libraries=[
            {
                "name": "第一章",
                "slug": "public-s01",
                "sort_order": 1,
                "cards": [
                    {
                        "front": "delta",
                        "back": "德尔塔。",
                        "tags": [],
                        "sort_order": 1,
                        "images": [],
                        "source": {"page": 3, "printed_page": "1", "method": "llm"},
                    }
                ],
            }
        ],
    )


def write_pack_zip(
    path: str | Path,
    manifest: dict,
    images: dict[str, bytes] | None = None,
) -> Path:
    """写一个 ``.ttfc.zip`` 卡包。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        for name, data in (images or {}).items():
            archive.writestr(name, data)
    return target


def write_pack_dir(
    path: str | Path,
    manifest: dict,
    images: dict[str, bytes] | None = None,
) -> Path:
    """写一个**解开的**卡包目录（``manifest.json`` + ``images/``）。"""
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for name, data in (images or {}).items():
        image_path = target / name
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(data)
    return target
