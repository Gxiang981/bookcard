"""导入的测试（契约 §1 幂等 + §3 scene）。

三个小卡包（图片场景 / 文本场景 / 公开包）都在 ``browse_fixtures`` 里现造。
"""

import hashlib
import json

import pytest

from bookcard.browse.db import BrowseDB
from browse_fixtures import (
    FAKE_JPEG,
    IMAGE_BOOK_ID,
    TEXT_BOOK_ID,
    image_manifest,
    public_manifest,
    text_manifest,
    write_pack_dir,
    write_pack_zip,
)


@pytest.fixture
def db(tmp_path) -> BrowseDB:
    """一个空库。"""
    instance = BrowseDB(tmp_path / "browse.db")
    instance.initialize()
    return instance


def _image_pack(tmp_path):
    return write_pack_zip(
        tmp_path / "image.ttfc.zip",
        image_manifest(),
        {"images/p0012.jpg": FAKE_JPEG},
    )


def _text_pack(tmp_path):
    return write_pack_zip(tmp_path / "text.ttfc.zip", text_manifest())


def _public_pack(tmp_path):
    return write_pack_zip(tmp_path / "public.ttfc.zip", public_manifest())


# ===================== 图片场景 =====================


def test_import_image_pack(db, tmp_path):
    """图片场景的私有包：scene_mode 兜底成 image，scene 是图片块。"""
    summary = db.import_pack(_image_pack(tmp_path))
    assert summary["book_id"] == IMAGE_BOOK_ID
    assert summary["visibility"] == "private"
    assert summary["scene_mode"] == "image"
    assert summary["libraries"] == 1
    assert summary["cards"] == 2
    assert summary["images"] == 1

    tree = db.tree()
    assert len(tree) == 1
    book = tree[0]
    assert book["id"] == IMAGE_BOOK_ID
    assert book["title"] == "示例图书"
    assert book["subtitle"] == "示例出版社 2019"
    assert book["visibility"] == "private"
    assert book["scene_mode"] == "image"
    assert book["libraries"] == [
        {"id": f"{IMAGE_BOOK_ID}-s01", "name": "第一章", "count": 2}
    ]

    listing = db.cards(book_id=IMAGE_BOOK_ID)
    assert len(listing["cards"]) == 2
    assert listing["tier_counts"] == {"T1": 0, "T2": 2, "T4": 0, "T6": 0, "T7": 0}
    assert listing["cards"][0]["front"] == "alpha"
    assert listing["cards"][0]["tier"] == "T2"
    assert listing["cards"][0]["library_id"] == f"{IMAGE_BOOK_ID}-s01"

    detail = db.card_detail(listing["cards"][0]["id"])
    scene = detail["scene"]
    assert scene["kind"] == "image"
    assert scene["book"] == "示例图书"
    assert scene["page"] == 12
    assert scene["src"] == f"/images/{IMAGE_BOOK_ID}/p0012.jpg"
    assert scene["width"] == 1240
    assert scene["height"] == 1754
    assert scene["boxes"] == [{"x": 120, "y": 300, "w": 64, "h": 18}]


def test_import_extracts_images(db, tmp_path):
    """页面图抽到 ``<data_dir>/images/<book_id>/``。"""
    db.import_pack(_image_pack(tmp_path))
    extracted = db.images_dir / IMAGE_BOOK_ID / "p0012.jpg"
    assert extracted.is_file()
    assert extracted.read_bytes() == FAKE_JPEG


def test_card_without_images_has_no_scene(db, tmp_path):
    """同一本书里没图的卡 ⇒ scene 是 null。"""
    db.import_pack(_image_pack(tmp_path))
    listing = db.cards(book_id=IMAGE_BOOK_ID)
    beta = next(card for card in listing["cards"] if card["front"] == "beta")
    assert db.card_detail(beta["id"])["scene"] is None


# ===================== 文本场景 =====================


def test_import_text_pack(db, tmp_path):
    """文本场景：manifest 没写 scene_mode ⇒ 兜底成 text；scene 是文本块。"""
    summary = db.import_pack(_text_pack(tmp_path))
    assert summary["scene_mode"] == "text"

    listing = db.cards(book_id=TEXT_BOOK_ID)
    assert len(listing["cards"]) == 1
    detail = db.card_detail(listing["cards"][0]["id"])
    scene = detail["scene"]
    assert scene["kind"] == "text"
    assert scene["book"] == "示例文书"
    assert scene["page"] == 7
    assert scene["printed_page"] == "3"
    assert scene["text"] == "这里出现 gamma 这个词。"
    assert scene["highlights"] == [{"start": 3, "end": 8}]


def test_import_pack_directory(db, tmp_path):
    """**解开的包目录**也能导。"""
    pack_dir = write_pack_dir(
        tmp_path / "unpacked",
        image_manifest(),
        {"images/p0012.jpg": FAKE_JPEG},
    )
    summary = db.import_pack(pack_dir)
    assert summary["book_id"] == IMAGE_BOOK_ID
    assert summary["cards"] == 2
    assert summary["images"] == 1
    card_id = db.cards(book_id=IMAGE_BOOK_ID)["cards"][0]["id"]
    assert db.card_detail(card_id)["scene"]["kind"] == "image"


# ===================== 公开包 =====================


def test_import_public_pack_has_no_scene(db, tmp_path):
    """公开包：没有 fp12 ⇒ sha1(category.name)[:12]；scene 必须是 null。"""
    summary = db.import_pack(_public_pack(tmp_path))
    expected_id = hashlib.sha1("示例公开书".encode()).hexdigest()[:12]
    assert summary["book_id"] == expected_id
    assert summary["visibility"] == "public"
    assert summary["scene_mode"] == "none"

    listing = db.cards(book_id=expected_id)
    assert len(listing["cards"]) == 1
    detail = db.card_detail(listing["cards"][0]["id"])
    assert detail["scene"] is None


def test_public_pack_extracts_no_images(db, tmp_path):
    """公开包不带书页图 ⇒ 一个字节都不落盘。"""
    db.import_pack(_public_pack(tmp_path))
    assert not (db.images_dir / db.tree()[0]["id"]).exists()


# ===================== 幂等（契约 §1） =====================


def test_reimport_keeps_progress_and_edits(db, tmp_path):
    """再导一次 = 幂等更新：进度、忽略、编辑一律保留。"""
    pack = _image_pack(tmp_path)
    db.import_pack(pack)

    listing = db.cards(book_id=IMAGE_BOOK_ID)
    card_id = listing["cards"][0]["id"]

    # 评一次分 + 改一次卡。
    db.rate(card_id, "good")
    db.edit_card(card_id, front="改过的正面", back="改过的背面", tags=["我加的标签"])

    # 再导同一个包。
    db.import_pack(pack)

    detail = db.card_detail(card_id)
    # 进度保留。
    assert detail["state"]["reviews"] == 1
    assert detail["state"]["repetition"] == 1
    assert detail["state"]["interval_days"] == 1
    # 编辑保留（包里的 front / back / tags 不许覆盖）。
    assert detail["edited"] is True
    assert detail["front"] == "改过的正面"
    assert detail["back"] == "改过的背面"
    assert detail["tags"] == ["我加的标签"]


def test_reimport_refreshes_unedited_cards(db, tmp_path):
    """没改过的卡：内容随包刷新。

    🔴 注意卡 id 的口径（契约 §2）：包里没有 ``card_id`` 时，
    id = ``<library_id>:<sha1(front)[:8]>`` ⇒ **改 front 等于换了一张卡**
    （老卡留着，不删）。所以这里改的是 back / tags。
    """
    pack = _image_pack(tmp_path)
    db.import_pack(pack)
    card_id = db.cards(book_id=IMAGE_BOOK_ID)["cards"][0]["id"]

    # 改包里的 back / tags，再导一次。
    manifest = image_manifest()
    manifest["category"]["libraries"][0]["cards"][0]["back"] = "包里的新背面"
    manifest["category"]["libraries"][0]["cards"][0]["tags"] = ["新标签"]
    write_pack_zip(pack, manifest, {"images/p0012.jpg": FAKE_JPEG})
    db.import_pack(pack)

    detail = db.card_detail(card_id)
    assert detail["back"] == "包里的新背面"
    assert detail["tags"] == ["新标签"]
    assert detail["edited"] is False


def test_card_id_follows_front_when_package_has_no_card_id(db, tmp_path):
    """包里没有 ``card_id`` ⇒ id = ``<library_id>:<sha1(front)[:8]>``（契约 §2）。"""
    db.import_pack(_image_pack(tmp_path))
    listing = db.cards(book_id=IMAGE_BOOK_ID)
    alpha = next(card for card in listing["cards"] if card["front"] == "alpha")
    expected = hashlib.sha1(b"alpha").hexdigest()[:8]
    assert alpha["id"] == f"{IMAGE_BOOK_ID}-s01:{expected}"


def test_reimport_keeps_ignored_and_mastered(db, tmp_path):
    """忽略 / 掌握标记也不许被导入冲掉。"""
    pack = _image_pack(tmp_path)
    db.import_pack(pack)
    card_id = db.cards(book_id=IMAGE_BOOK_ID)["cards"][0]["id"]

    db.set_ignored(card_id, True)
    db.rate(card_id, "easy")
    db.import_pack(pack)

    detail = db.card_detail(card_id)
    assert detail["state"]["ignored"] == 1
    assert detail["state"]["mastered"] == 1
    assert detail["tier"] == "T6"  # 忽略优先


def test_import_is_idempotent_on_counts(db, tmp_path):
    """导两次，卡数 / 词库数不变。"""
    pack = _image_pack(tmp_path)
    db.import_pack(pack)
    before = db.counts()
    db.import_pack(pack)
    assert db.counts() == before


def test_manifest_meta_drops_category(db, tmp_path):
    """``manifest_meta`` = manifest 去掉 category。"""
    db.import_pack(_image_pack(tmp_path))
    conn = db.connect()
    try:
        raw = conn.execute("SELECT manifest_meta FROM books").fetchone()["manifest_meta"]
    finally:
        conn.close()
    meta = json.loads(raw)
    assert "category" not in meta
    assert meta["visibility"] == "private"
    assert meta["book"]["title"] == "示例图书"


def test_import_rejects_bad_front(db, tmp_path):
    """front 长度不合法的包直接报错（不静默吞掉）。"""
    from bookcard.browse.db import BrowseError

    manifest = image_manifest()
    manifest["category"]["libraries"][0]["cards"][0]["front"] = "x" * 501
    pack = write_pack_zip(tmp_path / "bad.ttfc.zip", manifest)
    with pytest.raises(BrowseError):
        db.import_pack(pack)


def test_import_reports_edition_change(db, tmp_path):
    """🔴 同一本书的**两个版本**（契约 §7.1 公开版 / 内部分享版）共用一个 book_id
    与一套卡 id ⇒ 后导的会覆盖先导的。导入摘要必须**说出来**。
    """
    db.import_pack(_image_pack(tmp_path))

    manifest = image_manifest()
    manifest["visibility"] = "public"
    public_pack = write_pack_zip(tmp_path / "public.ttfc.zip", manifest)

    summary = db.import_pack(public_pack)
    assert summary["visibility"] == "public"
    assert summary["previous_visibility"] == "private"
    assert summary["visibility_changed"] is True

    # 公开版的卡没有场景（公开包不带书页图 / 文本块）。
    listing = db.cards(book_id=IMAGE_BOOK_ID)
    for card in listing["cards"]:
        assert db.card_detail(card["id"])["scene"] is None


def test_import_same_edition_is_not_reported_as_change(db, tmp_path):
    """同一版本重导 ⇒ 不算版本变化。"""
    db.import_pack(_image_pack(tmp_path))
    summary = db.import_pack(_image_pack(tmp_path))
    assert summary["visibility_changed"] is False
    assert summary["previous_visibility"] == "private"


# ===================== 改卡 =====================


def test_edit_moves_card_within_the_same_book(db, tmp_path):
    """改卡可以换词库（同一本书内）。"""
    db.import_pack(_image_pack(tmp_path))
    db.import_pack(_text_pack(tmp_path))
    card_id = db.cards(book_id=IMAGE_BOOK_ID)["cards"][0]["id"]

    # 同一本书里再加一个词库，把卡挪过去。
    conn = db.connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO libraries (id, book_id, name, sort_order) VALUES (?, ?, ?, ?)",
                (f"{IMAGE_BOOK_ID}-s02", IMAGE_BOOK_ID, "第二章", 2),
            )
    finally:
        conn.close()

    detail = db.edit_card(card_id, library_id=f"{IMAGE_BOOK_ID}-s02")
    assert detail["library_id"] == f"{IMAGE_BOOK_ID}-s02"
    assert detail["edited"] is True

    # 再导一次：改过的卡留在用户挪去的词库，不被包拉回去。
    db.import_pack(_image_pack(tmp_path))
    assert db.card_detail(card_id)["library_id"] == f"{IMAGE_BOOK_ID}-s02"


def test_new_card_ease_matches_app_default(db, tmp_path):
    """新卡 ease 初值 = App 的 1.8（``sm2.dart:77``），两端排程从第一轮起一致。"""
    db.import_pack(_image_pack(tmp_path))
    card_id = db.cards(book_id=IMAGE_BOOK_ID)["cards"][0]["id"]
    assert db.card_detail(card_id)["state"]["ease"] == 1.8
    assert db.rate(card_id, "good")["state"]["ease"] == 1.9


def test_edit_cannot_move_card_to_another_book(db, tmp_path):
    """不能把卡挪到**别的书**的词库里（契约 §4：只在本书内换词库）。"""
    from bookcard.browse.db import BrowseError

    db.import_pack(_image_pack(tmp_path))
    db.import_pack(_text_pack(tmp_path))
    card_id = db.cards(book_id=IMAGE_BOOK_ID)["cards"][0]["id"]

    with pytest.raises(BrowseError):
        db.edit_card(card_id, library_id=f"{TEXT_BOOK_ID}-s01")
