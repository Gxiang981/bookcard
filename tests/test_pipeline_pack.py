"""§7 私有卡包的测试：闸门、front 长度、manifest 校验、zip 结构。

manifest 校验这里用**从 App 抄来的最小 pydantic 模型**复核一遍——
**不 import App 的代码**（那是另一个仓库的运行时依赖）。
模型逐字段照抄 ``00_flashcard/server-python/app/routes/imports.py`` 里的
``_Occurrence`` / ``_ImageIn`` / ``_CardIn`` / ``_LibIn`` / ``_CatIn`` /
``BulkImportBody``，并照抄 App 端点「把 ``file`` 还原成 ``content_base64``」
那一步。
"""

import base64
import json
import zipfile
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from bookcard.pipeline.audit import audit
from bookcard.pipeline.locate import locate
from bookcard.pipeline.pack import PackError, pack, read_pack_manifest
from bookcard.pipeline.select import select
from bookcard.pipeline.workdir import WorkDir, content_fp
from pipeline_fixtures import (
    BOOK,
    assert_scene_chosen_by_consistent,
    candidate,
    make_pdf,
    write_workdir,
)

# ===================== 从 App 抄来的最小 pydantic 模型 =====================


class _Occurrence(BaseModel):
    """一个词在某张图里的位置（坐标已是 x/y/w/h）。"""

    x: int
    y: int
    w: int
    h: int
    page: int | None = None
    score: float | None = None


class _ImageIn(BaseModel):
    content_base64: str = Field(min_length=1)
    alt_text: str | None = None
    caption: str | None = None
    occurrences: list[_Occurrence] = Field(default_factory=list)
    width: int | None = None
    height: int | None = None


class _CardIn(BaseModel):
    front: str = Field(min_length=1, max_length=500)
    back: str = ""
    phonetic: str | None = None
    pos: str | None = None
    tags: list[str] = Field(default_factory=list)
    difficulty: int = 1
    images: list[_ImageIn] = Field(default_factory=list)
    sort_order: int = 0


class _LibIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=255)
    description: str | None = None
    sort_order: int = 0
    cards: list[_CardIn] = Field(default_factory=list)


class _CatIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    sort_order: int = 0
    libraries: list[_LibIn] = Field(default_factory=list)


class BulkImportBody(BaseModel):
    """App 的导入请求体（只留 ``category``）。"""

    category: _CatIn


def _resolve_images_like_app(manifest: dict, archive: zipfile.ZipFile) -> dict:
    """照抄 App 端点：把 ``file`` 还原成 ``content_base64``。"""

    def resolve(node: dict) -> None:
        for image in node.get("images", []) or []:
            file_name = image.get("file")
            if not file_name:
                continue
            if file_name not in archive.namelist():
                raise AssertionError(f"image not in zip: {file_name}")
            image["content_base64"] = base64.b64encode(archive.read(file_name)).decode("ascii")
            image.pop("file", None)

    for library in (manifest.get("category", {}) or {}).get("libraries", []) or []:
        for card in library.get("cards", []) or []:
            resolve(card)
    return manifest


# ===================== 夹具 =====================


def _ready_workdir(tmp_path, candidates=None, **kwargs):
    """跑到「可以打包」的状态：挑卡 → 定位 → 质检。"""
    candidates = candidates or [candidate(1, "良渚文化", explanation="解释。")]
    work = write_workdir(tmp_path / "book", candidates=candidates, **kwargs)
    pdf = tmp_path / "book.pdf"
    make_pdf(pdf)
    select(work)
    locate(work, pdf)
    audit(work)
    return work, pdf


# ===================== 测试 =====================


def test_pack_refuses_when_toc_gate_not_approved(tmp_path):
    """闸门 1 没批准 ⇒ 不许打包。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[candidate(1, "良渚文化", explanation="解释。")],
        toc_status="pending",
    )
    select(work)

    with pytest.raises(PackError, match="toc.json"):
        pack(work)


def test_pack_refuses_when_gate_file_missing(tmp_path):
    """闸门文件都没有 ⇒ 不许打包。"""
    work = write_workdir(
        tmp_path / "book",
        candidates=[candidate(1, "良渚文化", explanation="解释。")],
        toc_file=False,
        trial_file=False,
    )
    select(work)

    with pytest.raises(PackError, match="闸门"):
        pack(work)


def test_pack_refuses_front_longer_than_500_chars(tmp_path):
    """front 超过 App 的 500 字符上限 ⇒ 不许打包。"""
    work, pdf = _ready_workdir(tmp_path)
    cards = WorkDir(work).cards()
    cards[0]["front"] = "长" * 501
    WorkDir(work).cards_path.write_text(
        "".join(json.dumps(card, ensure_ascii=False) + "\n" for card in cards),
        encoding="utf-8",
    )

    with pytest.raises(PackError, match="500"):
        pack(work, pdf=pdf, scene="image")


def test_pack_writes_zip_with_manifest_and_images(tmp_path):
    """zip 里是 manifest.json + 页面图。"""
    work, pdf = _ready_workdir(tmp_path)

    summary = pack(work, pdf=pdf, scene="image")

    assert summary["cards"] == 1
    assert summary["images"] == 1
    pack_path = work / "pack" / "0123456789ab.ttfc.zip"
    assert pack_path.exists()
    with zipfile.ZipFile(pack_path) as archive:
        names = set(archive.namelist())
    assert "manifest.json" in names
    assert "images/p0001.jpg" in names


def test_manifest_passes_apps_bulk_import_body(tmp_path):
    """manifest 能通过 App 的 BulkImportBody（抄一份最小 pydantic 模型）。"""
    work, pdf = _ready_workdir(tmp_path)
    pack(work, pdf=pdf, scene="image")
    pack_path = work / "pack" / "0123456789ab.ttfc.zip"

    with zipfile.ZipFile(pack_path) as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        resolved = _resolve_images_like_app(manifest, archive)

    body = BulkImportBody(**resolved)

    assert body.category.name == "示例教科书"
    assert body.category.libraries[0].name == "第1课 中华文明的起源"
    assert body.category.libraries[0].slug == "0123456789ab-s01"
    assert body.category.libraries[0].cards[0].front == "良渚文化"
    assert body.category.libraries[0].cards[0].images[0].content_base64


def test_manifest_keeps_v2_fields_the_app_ignores(tmp_path):
    """v2 字段（format/visibility/book/provenance/…）照契约留在 manifest 里。"""
    work, pdf = _ready_workdir(tmp_path)
    pack(work, pdf=pdf, scene="image")

    manifest = read_pack_manifest(work / "pack" / "0123456789ab.ttfc.zip")

    assert manifest["format"] == "ttfc/2"
    assert manifest["visibility"] == "private"
    assert manifest["book"]["fp12"] == "0123456789ab"
    assert manifest["license"] == {"cards": "private", "page_images": "private"}
    assert manifest["provenance"]["tool"].startswith("bookcard ")
    assert manifest["provenance"]["created_at"] > 0
    assert manifest["quality"]["cards"] == 1
    assert manifest["category"]["description"] == "人民教育出版社 2019"


def test_null_bbox_still_carries_page_image(tmp_path):
    """bbox 为 null ⇒ occurrences 为空，但页面图照带。"""
    work, pdf = _ready_workdir(tmp_path)
    cards = WorkDir(work).cards()
    cards[0]["source"]["bbox"] = None
    WorkDir(work).cards_path.write_text(
        "".join(json.dumps(card, ensure_ascii=False) + "\n" for card in cards),
        encoding="utf-8",
    )

    pack(work, pdf=pdf, scene="image")
    manifest = read_pack_manifest(work / "pack" / "0123456789ab.ttfc.zip")
    image = manifest["category"]["libraries"][0]["cards"][0]["images"][0]

    assert image["file"] == "images/p0001.jpg"
    assert image["occurrences"] == []
    assert image["width"] == 1240
    assert image["height"] == 1755


def test_occurrences_carry_pixel_bbox_and_page(tmp_path):
    """occurrences 带像素坐标和页号。"""
    work, pdf = _ready_workdir(tmp_path)
    pack(work, pdf=pdf, scene="image")

    manifest = read_pack_manifest(work / "pack" / "0123456789ab.ttfc.zip")
    occurrence = manifest["category"]["libraries"][0]["cards"][0]["images"][0]["occurrences"][0]

    assert occurrence["x"] >= 0
    assert occurrence["w"] > 0
    assert occurrence["page"] == 1


def test_pack_keeps_source_and_content_fp(tmp_path):
    """卡上带 source 和 content_fp（App 会忽略，但契约要求有）。"""
    work, pdf = _ready_workdir(tmp_path)
    pack(work, pdf=pdf, scene="image")

    manifest = read_pack_manifest(work / "pack" / "0123456789ab.ttfc.zip")
    card = manifest["category"]["libraries"][0]["cards"][0]

    assert card["source"]["page"] == 1
    assert card["content_fp"]


# ===================== §7.1 两档：公开版 / 内部分享版 =====================


def _public_pack_path(work):
    return work / "pack" / "0123456789ab.public.ttfc.zip"


def test_private_image_scene_keeps_todays_behaviour(tmp_path):
    """内部分享版 + 图片场景 = 今天的行为：页面图 + occurrences，没有 context。"""
    work, pdf = _ready_workdir(tmp_path)

    summary = pack(work, pdf=pdf, scene="image")

    assert summary["edition"] == "private"
    assert summary["images"] == 1
    manifest = read_pack_manifest(work / "pack" / "0123456789ab.ttfc.zip")
    card = manifest["category"]["libraries"][0]["cards"][0]
    assert card["images"][0]["file"] == "images/p0001.jpg"
    assert "context" not in card["source"]


def test_private_text_scene_adds_context_and_drops_images(tmp_path):
    """内部分享版 + 文本场景：加 context，不带页面图。"""
    work, pdf = _ready_workdir(tmp_path)

    summary = pack(work, pdf=pdf, scene="text")

    assert summary["scene_mode"] == "text"
    assert summary["images"] == 0
    with zipfile.ZipFile(work / "pack" / "0123456789ab.ttfc.zip") as archive:
        names = set(archive.namelist())
    assert names == {"manifest.json"}

    manifest = read_pack_manifest(work / "pack" / "0123456789ab.ttfc.zip")
    card = manifest["category"]["libraries"][0]["cards"][0]
    assert card["images"] == []
    context = card["source"]["context"]
    assert "良渚文化" in context["text"]
    assert context["highlights"]
    hit = context["highlights"][0]
    assert context["text"][hit["start"] : hit["end"]] == "良渚文化"


def test_pack_without_scene_uses_the_default_image_scene(tmp_path):
    """不给 ``--scene`` 时走默认场景（image，the maintainer 2026-11-06）。"""
    work, pdf = _ready_workdir(tmp_path)

    summary = pack(work, pdf=pdf)

    assert summary["scene_detected"] == "image"
    assert summary["scene_mode"] == "image"
    assert summary["scene_chosen_by"] == "default"
    assert summary["images"] == 1


def test_pack_without_scene_records_default_as_the_chooser(tmp_path):
    """不传场景时 ``scene_chosen_by`` 记 ``default``（不是 ``cli``）。"""
    work, pdf = _ready_workdir(tmp_path)

    pack(work, pdf=pdf)

    manifest = assert_scene_chosen_by_consistent(work, "default")
    assert manifest["provenance"]["scene_suggested"] == "image"
    assert manifest["book"]["scene_suggested"] == "image"


def test_scene_override_is_recorded_in_provenance(tmp_path):
    """--scene 覆盖时，provenance.scene_override 记一笔。"""
    work, pdf = _ready_workdir(tmp_path)

    pack(work, pdf=pdf, scene="image")

    manifest = read_pack_manifest(work / "pack" / "0123456789ab.ttfc.zip")
    assert manifest["provenance"]["scene_override"] == {"requested": "image", "detected": "image"}
    assert manifest["scene_mode"] == "image"


def test_scene_mode_lands_in_manifest_and_book_json(tmp_path):
    """scene_mode 写进 manifest 顶层和 book.json（契约 §7.1）。"""
    work, pdf = _ready_workdir(tmp_path)

    pack(work, pdf=pdf, scene="text")

    manifest = read_pack_manifest(work / "pack" / "0123456789ab.ttfc.zip")
    assert manifest["scene_mode"] == "text"
    assert manifest["book"]["scene_mode"] == "text"
    assert WorkDir(work).book()["scene_mode"] == "text"


def test_public_pack_uses_the_public_file_name_and_visibility(tmp_path):
    """公开版叫 <fp12>.public.ttfc.zip，visibility = public。"""
    work, pdf = _ready_workdir(tmp_path)

    summary = pack(work, pdf=pdf, edition="public")

    assert summary["pack"].endswith("0123456789ab.public.ttfc.zip")
    assert _public_pack_path(work).exists()
    manifest = read_pack_manifest(_public_pack_path(work))
    assert manifest["visibility"] == "public"
    assert manifest["license"] == {"cards": "CC-BY-SA-4.0", "page_images": "none"}


def test_public_pack_back_is_one_sentence(tmp_path):
    """公开版的背面只有一句解释。"""
    work, pdf = _ready_workdir(
        tmp_path,
        candidates=[
            candidate(
                1,
                "良渚文化",
                explanation="长江下游的新石器时代晚期文化。它以水利系统著称。",
            )
        ],
    )

    pack(work, pdf=pdf, edition="public")

    manifest = read_pack_manifest(_public_pack_path(work))
    card = manifest["category"]["libraries"][0]["cards"][0]
    assert card["back"] == "长江下游的新石器时代晚期文化。"


def test_public_pack_keeps_the_word_class_prefix(tmp_path):
    """英文词性标签（``n. ``）不是句末，背面不能被切成 ``n.``。"""
    work, pdf = _ready_workdir(
        tmp_path,
        candidates=[
            candidate(
                1,
                "良渚文化",
                explanation="n. 口语技能；本段指用英语开口表达并加以改善的能力。",
            )
        ],
    )

    pack(work, pdf=pdf, edition="public")

    manifest = read_pack_manifest(_public_pack_path(work))
    card = manifest["category"]["libraries"][0]["cards"][0]
    assert card["back"] == "n. 口语技能；本段指用英语开口表达并加以改善的能力。"


def test_public_pack_strips_source_fields(tmp_path):
    """公开版的 source 只留 page / printed_page / bbox / verbatim / method。"""
    work, pdf = _ready_workdir(tmp_path)

    pack(work, pdf=pdf, edition="public")

    manifest = read_pack_manifest(_public_pack_path(work))
    card = manifest["category"]["libraries"][0]["cards"][0]
    assert set(card["source"]) == {"page", "printed_page", "bbox", "verbatim", "method"}
    assert "evidence" not in card["source"]
    assert "context" not in card["source"]
    assert "nas_path" not in manifest["book"]
    assert "file_name" not in manifest["book"]
    assert manifest["book"]["fingerprint"]


def test_public_pack_has_no_images(tmp_path):
    """公开版：zip 里不许有任何图片。"""
    work, pdf = _ready_workdir(tmp_path)

    pack(work, pdf=pdf, edition="public")

    with zipfile.ZipFile(_public_pack_path(work)) as archive:
        names = archive.namelist()
    assert names == ["manifest.json"]
    manifest = read_pack_manifest(_public_pack_path(work))
    for library in manifest["category"]["libraries"]:
        for card in library["cards"]:
            assert card["images"] == []


def test_public_pack_requires_the_book_fingerprint(tmp_path):
    """公开版必须有 book.fingerprint（缺了就失败）。"""
    book = dict(BOOK)
    book.pop("fingerprint")
    work, pdf = _ready_workdir(tmp_path, book=book)

    with pytest.raises(PackError, match="fingerprint"):
        pack(work, pdf=pdf, edition="public")


def test_public_pack_content_fp_follows_the_truncated_back(tmp_path):
    """公开版的 content_fp 按截断后的背面算。"""
    work, pdf = _ready_workdir(
        tmp_path,
        candidates=[
            candidate(1, "良渚文化", explanation="长江下游的新石器时代晚期文化。它以水利系统著称。")
        ],
    )

    pack(work, pdf=pdf, edition="public")

    manifest = read_pack_manifest(_public_pack_path(work))
    card = manifest["category"]["libraries"][0]["cards"][0]
    assert card["content_fp"] == content_fp("良渚文化", card["back"])


def test_public_pack_passes_apps_bulk_import_body(tmp_path):
    """公开版也要能过 App 的 BulkImportBody（老 App 能导入）。"""
    work, pdf = _ready_workdir(tmp_path)

    pack(work, pdf=pdf, edition="public")

    with zipfile.ZipFile(_public_pack_path(work)) as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        resolved = _resolve_images_like_app(manifest, archive)

    body = BulkImportBody(**resolved)

    assert body.category.name == "示例教科书"
    assert body.category.libraries[0].cards[0].front == "良渚文化"
    assert body.category.libraries[0].cards[0].images == []


def test_private_pack_passes_apps_bulk_import_body_with_context(tmp_path):
    """内部分享版 + 文本场景也要能过 App 的 BulkImportBody。"""
    work, pdf = _ready_workdir(tmp_path)

    pack(work, pdf=pdf, scene="text")

    with zipfile.ZipFile(work / "pack" / "0123456789ab.ttfc.zip") as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        resolved = _resolve_images_like_app(manifest, archive)

    body = BulkImportBody(**resolved)

    assert body.category.libraries[0].cards[0].front == "良渚文化"
    assert body.category.libraries[0].cards[0].images == []


def test_pack_refuses_an_unknown_edition(tmp_path):
    """档位写错 ⇒ 直接失败。"""
    work, pdf = _ready_workdir(tmp_path)

    with pytest.raises(PackError, match="edition"):
        pack(work, pdf=pdf, edition="secret")


def test_pack_refuses_an_unknown_scene(tmp_path):
    """场景写错 ⇒ 直接失败。"""
    work, pdf = _ready_workdir(tmp_path)

    with pytest.raises(PackError, match="scene"):
        pack(work, pdf=pdf, scene="hologram")

# ===================== §7.1 场景来源（scene_chosen_by） =====================


def test_pack_refuses_an_unknown_scene_chosen_by(tmp_path):
    """``scene_chosen_by`` 写错 ⇒ 直接失败。"""
    work, pdf = _ready_workdir(tmp_path)

    with pytest.raises(PackError, match="scene_chosen_by"):
        pack(work, pdf=pdf, scene="text", scene_chosen_by="hologram")


def test_重打包会清掉旧的手改备注(tmp_path):
    """退役的手改脚本留过 ``scene_chosen_by_note``——引擎重打包时清掉它。

    备注说「非用户亲选」，取值说 ``"user"``，一个包里两种说法＝不许留。
    """
    work, pdf = _ready_workdir(tmp_path)
    book_path = Path(work) / "book.json"
    book = json.loads(book_path.read_text(encoding="utf-8"))
    book["scene_chosen_by_note"] = "非用户亲选：headless 无人值守。"
    book_path.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")

    pack(work, pdf=pdf, scene="text", scene_chosen_by="user")

    assert "scene_chosen_by_note" not in json.loads(book_path.read_text(encoding="utf-8"))
    manifest = assert_scene_chosen_by_consistent(work, "user")
    assert "scene_chosen_by_note" not in manifest["book"]
