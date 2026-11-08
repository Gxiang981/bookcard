"""``bookcard browse import|serve`` 的接线测试。"""

import json

from bookcard.cli import main
from browse_fixtures import FAKE_JPEG, image_manifest, write_pack_zip


def test_browse_import(tmp_path, capsys):
    """``bookcard browse import <pack> --db <path>``：导进去并打印摘要。"""
    pack = write_pack_zip(
        tmp_path / "image.ttfc.zip",
        image_manifest(),
        {"images/p0012.jpg": FAKE_JPEG},
    )
    db_path = tmp_path / "browse.db"
    code = main(["browse", "import", str(pack), "--db", str(db_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "导入" in out
    assert db_path.is_file()

    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        books = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        cards = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    finally:
        conn.close()
    assert books == 1
    assert cards == 2


def test_browse_import_bad_pack(tmp_path, capsys):
    """坏包 ⇒ 退出码 3（EXIT_BROKEN）+ 中文报错。"""
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    code = main(["browse", "import", str(bad), "--db", str(tmp_path / "browse.db")])
    assert code == 3
    assert "导入失败" in capsys.readouterr().out


def test_browse_without_subcommand(capsys):
    """``bookcard browse`` 少子命令 ⇒ 提示 + 退出码 2。"""
    code = main(["browse"])
    assert code == 2
    assert "用法" in capsys.readouterr().out


def test_browse_serve_help_has_flags():
    """``browse serve`` 的旗标齐全（--db / --port / --no-open）。"""
    import argparse

    from bookcard.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(
        ["browse", "serve", "--db", "/tmp/x.db", "--port", "9999", "--no-open"]
    )
    assert isinstance(args, argparse.Namespace)
    assert args.browse_command == "serve"
    assert args.db == "/tmp/x.db"
    assert args.port == 9999
    assert args.no_open is True


def test_api_shape_matches_contract(tmp_path):
    """接口形状照契约 §4（前端那一单靠这个对齐）。"""
    from bookcard.browse.db import BrowseDB

    pack = write_pack_zip(
        tmp_path / "image.ttfc.zip",
        image_manifest(),
        {"images/p0012.jpg": FAKE_JPEG},
    )
    db = BrowseDB(tmp_path / "browse.db")
    db.import_pack(pack)

    tree = db.tree()
    assert set(tree[0]) == {"id", "title", "subtitle", "visibility", "scene_mode", "libraries"}
    assert set(tree[0]["libraries"][0]) == {"id", "name", "count"}

    listing = db.cards(book_id=tree[0]["id"])
    assert set(listing) == {"cards", "tier_counts"}
    assert set(listing["cards"][0]) == {
        "id",
        "front",
        "back_preview",
        "tier",
        "library_id",
        "sort_order",
    }
    assert set(listing["tier_counts"]) == {"T1", "T2", "T4", "T6", "T7"}

    detail = db.card_detail(listing["cards"][0]["id"])
    assert set(detail) == {
        "id",
        "library_id",
        "book_id",
        "front",
        "back",
        "tags",
        "tier",
        "scene",
        "state",
        "edited",
    }
    assert json.dumps(detail, ensure_ascii=False)  # 能序列化
