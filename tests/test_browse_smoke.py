"""真数据冒烟：把真数据目录下的卡包导进临时库。

目录不在（本机没跑过流水线）就跳过。跑的时候会把书 / 词库 / 卡的数量打出来，
交付评论里报的就是这份数。

卡包在新位置（``books/<fp12>/pack/``）和旧位置（``<fp12>/pack/``）都可能，
两处都扫。

🔴 真包会被并行的流水线那一单重打（公开版 / 内部分享版、文本场景 / 图片场景
都可能换），所以这里**不写死**某种包形态，只按包里**实际**的内容核对。
"""

from pathlib import Path

import pytest

from bookcard.browse.db import BrowseDB

#: 真数据目录（主目录，不在 worktree 里 —— worktree 会被删）。
REAL_DATA_DIR = Path("/train/data/bookcard")

pytestmark = pytest.mark.skipif(
    not REAL_DATA_DIR.is_dir(),
    reason=f"真数据目录不在：{REAL_DATA_DIR}",
)


def _packs() -> list[Path]:
    """真数据目录下的所有卡包（新位置 + 旧位置，含公开版）。"""
    packs = set(REAL_DATA_DIR.glob("*/pack/*.ttfc.zip"))
    packs |= set(REAL_DATA_DIR.glob("books/*/pack/*.ttfc.zip"))
    return sorted(packs)


def _check_scenes(db: BrowseDB, book_id: str, limit: int = 50) -> set[str]:
    """核一张书的 scene：要么 null，要么文本 / 图片场景且指向真实文件。"""
    kinds: set[str] = set()
    listing = db.cards(book_id=book_id)
    assert listing["cards"]
    checked = 0
    for card in listing["cards"]:
        scene = db.card_detail(card["id"])["scene"]
        if scene is None:
            continue
        kinds.add(scene["kind"])
        if scene["kind"] == "image":
            file_name = scene["src"].rsplit("/", 1)[-1]
            assert (db.images_dir / book_id / file_name).is_file()
            assert isinstance(scene["boxes"], list)
        elif scene["kind"] == "text":
            assert scene["text"]
            assert isinstance(scene["highlights"], list)
        else:
            pytest.fail(f"认不出来的 scene kind：{scene['kind']}")
        checked += 1
        if checked >= limit:
            break
    return kinds


def test_real_packs_import(tmp_path):
    """每个真卡包单独导一遍：书 / 词库 / 卡都有数，scene 自洽。"""
    packs = _packs()
    if not packs:
        pytest.skip(f"{REAL_DATA_DIR} 里没有卡包")

    book_ids = set()
    kinds: set[str] = set()
    for pack in packs:
        db = BrowseDB(tmp_path / f"{pack.stem}.db")
        db.initialize()
        summary = db.import_pack(pack)
        assert summary["cards"] > 0
        assert summary["libraries"] > 0
        book_ids.add(summary["book_id"])
        kinds |= _check_scenes(db, summary["book_id"])
        print(
            f"  - {pack.name}：{summary['visibility']} / 场景 {summary['scene_mode']} / "
            f"{summary['libraries']} 词库 / {summary['cards']} 卡 / {summary['images']} 图"
        )

    print(f"\n[冒烟] {len(packs)} 个卡包 → {len(book_ids)} 本书；场景种类 {sorted(kinds)}")


def test_real_packs_all_in_one_db(tmp_path):
    """全部导进同一个库：合计数对得上。"""
    packs = _packs()
    if not packs:
        pytest.skip(f"{REAL_DATA_DIR} 里没有卡包")

    db = BrowseDB(tmp_path / "browse.db")
    db.initialize()
    summaries = [db.import_pack(pack) for pack in packs]

    counts = db.counts()
    distinct_books = len({summary["book_id"] for summary in summaries})
    print(
        f"\n[冒烟] 导入 {len(packs)} 个卡包："
        f"书 {counts['books']} / 词库 {counts['libraries']} / 卡 {counts['cards']}"
    )
    for summary in summaries:
        print(
            f"  - {summary['book_id']}《{summary['title']}》："
            f"{summary['libraries']} 词库 / {summary['cards']} 卡 / "
            f"{summary['images']} 图 / 场景 {summary['scene_mode']} / {summary['visibility']}"
        )

    # 同一本书的公开版 / 内部分享版共用一个 book_id ⇒ 书数按**去重**算。
    assert counts["books"] == distinct_books
    assert counts["libraries"] > 0
    assert counts["cards"] > 0

    # 每本书都要能在树上看到，且卡能取出来。
    tree = db.tree()
    assert len(tree) == distinct_books
    for book in tree:
        assert book["scene_mode"] in ("text", "image", "none")
        listing = db.cards(book_id=book["id"])
        assert listing["cards"]
        assert sum(listing["tier_counts"].values()) == len(listing["cards"])
