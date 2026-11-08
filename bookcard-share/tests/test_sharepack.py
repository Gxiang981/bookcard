"""bookcard-share 的自检。

跑法（在 46_BookCard 的 venv 里）::

    ../.venv/bin/pytest -q tests/

有两组对照是**跟引擎对齐**的（引擎装了就能对，没装就跳过）：

- 卡 id 的推导规则 vs ``src/bookcard/browse/db.py:513-523``
- SM-2 的算法 vs ``src/bookcard/browse/sm2.py:114-172``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sharepack.apply import apply_edits  # noqa: E402
from sharepack.html import render_html  # noqa: E402
from sharepack.payload import build_payload  # noqa: E402
from sharepack.webp import to_webp_bytes, webp_name  # noqa: E402

try:  # 引擎装了就能做对照
    from bookcard.browse import sm2 as engine_sm2
except ImportError:  # pragma: no cover
    engine_sm2 = None


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #

IMAGE_CARD = {
    "front": "光的反射",
    "back": "光照到物体表面又返回原来介质中的现象。",
    "tags": ["物理"],
    "difficulty": 1,
    "sort_order": 1,
    "images": [
        {
            "file": "images/p0005.jpg",
            "occurrences": [{"x": 490, "y": 438, "w": 100, "h": 25, "page": 5}],
            "width": 1123,
            "height": 1571,
        }
    ],
    "source": {"page": 5, "bbox": {"x": 490, "y": 438, "w": 100, "h": 25}},
}

TEXT_CARD = {
    "front": "内能",
    "back": "物体内部由于分子热运动等所具有的能量。",
    "tags": [],
    "difficulty": 1,
    "sort_order": 1,
    "images": [],
    "source": {
        "page": 12,
        "printed_page": "8",
        "context": {
            "text": "内能是物体内部所有分子无规则运动的动能总和。",
            "highlights": [{"start": 0, "end": 2}],
        },
    },
}


def make_manifest(*cards, scene_mode="image", visibility="private") -> dict:
    return {
        "format": "ttfc/2",
        "visibility": visibility,
        "scene_mode": scene_mode,
        "book": {"fp12": "abc123def456", "title": "测试书", "page_count": 10},
        "category": {
            "name": "测试书",
            "description": "某出版社 2024",
            "sort_order": 0,
            "libraries": [
                {
                    "name": "第1节",
                    "slug": "abc123def456-s01",
                    "sort_order": 1,
                    "cards": list(cards),
                }
            ],
        },
    }


# --------------------------------------------------------------------------- #
# payload
# --------------------------------------------------------------------------- #

def test_card_id_matches_engine_rule():
    """卡 id = <词库>:<sha1(front)[:8]>（db.py:513-523 的第三种形态）。"""
    import hashlib

    payload = build_payload(make_manifest(IMAGE_CARD))
    expect = "abc123def456-s01:" + hashlib.sha1("光的反射".encode()).hexdigest()[:8]
    assert payload["cards"][0]["id"] == expect


def test_scene_image_uses_relative_path():
    """图片场景：src 必须是包内相对路径（file:// 下才认）。"""
    payload = build_payload(
        make_manifest(IMAGE_CARD),
        {"images/p0005.jpg": "images/p0005.webp"},
    )
    scene = payload["cards"][0]["scene"]
    assert scene["kind"] == "image"
    assert scene["src"] == "images/p0005.webp"
    assert scene["width"] == 1123 and scene["height"] == 1571
    assert scene["boxes"] == [{"x": 490, "y": 438, "w": 100, "h": 25}]
    assert scene["page"] == 5


def test_scene_text_from_context():
    payload = build_payload(make_manifest(TEXT_CARD, scene_mode="text"))
    scene = payload["cards"][0]["scene"]
    assert scene["kind"] == "text"
    assert scene["text"].startswith("内能是")
    assert scene["highlights"] == [{"start": 0, "end": 2}]


def test_public_pack_has_no_scene():
    """公开包一律没有场景（db.py:210-211）。"""
    payload = build_payload(make_manifest(IMAGE_CARD, visibility="public"))
    assert payload["cards"][0]["scene"] is None


def test_book_and_library_meta():
    payload = build_payload(make_manifest(IMAGE_CARD))
    assert payload["book"]["id"] == "abc123def456"
    assert payload["book"]["title"] == "测试书"
    assert payload["book"]["subtitle"] == "某出版社 2024"
    assert payload["libraries"][0]["id"] == "abc123def456-s01"
    assert payload["libraries"][0]["count"] == 1


# --------------------------------------------------------------------------- #
# html
# --------------------------------------------------------------------------- #

def test_html_is_self_contained_and_escaped():
    payload = build_payload(make_manifest({**IMAGE_CARD, "back": "带 </script> 的背面"}))
    html = render_html(payload)
    # 没有外链资源
    assert "http://" not in html.replace('xmlns="http://www.w3.org/2000/svg"', "")
    assert "<link" not in html
    # 数据在页面里，但不是裸的 </script>
    assert 'id="bookcard-data"' in html
    assert "</script>evil" not in html
    # 注入的 </script> 被转义了，解析回来还是原字符
    assert "\\u003c/script\\u003e" in html


def test_html_embeds_payload():
    payload = build_payload(make_manifest(IMAGE_CARD))
    html = render_html(payload)
    start = html.index('id="bookcard-data">') + len('id="bookcard-data">')
    end = html.index("</script>", start)
    raw = html[start:end]
    raw = raw.replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&")
    data = json.loads(raw)
    assert data["cards"][0]["front"] == "光的反射"


# --------------------------------------------------------------------------- #
# webp
# --------------------------------------------------------------------------- #

def test_webp_name():
    assert webp_name("images/p0012.jpg") == "images/p0012.webp"
    assert webp_name("images/p0012.webp") == "images/p0012.webp"


def test_webp_keeps_pixel_size():
    """换编码不能换像素尺寸 —— bbox 是原图像素坐标。"""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1123, 1571), (240, 240, 240)).save(buf, format="JPEG", quality=85)
    jpg = buf.getvalue()
    webp = to_webp_bytes(jpg)
    assert webp[:4] == b"RIFF" and webp[8:12] == b"WEBP"
    with Image.open(io.BytesIO(webp)) as im:
        assert im.size == (1123, 1571)


def test_webp_is_idempotent():
    """已经是 webp 的原样返回（跟 App 的 to_webp 一致）。"""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), (1, 2, 3)).save(buf, format="WEBP", quality=95)
    webp = buf.getvalue()
    assert to_webp_bytes(webp) == webp


# --------------------------------------------------------------------------- #
# apply
# --------------------------------------------------------------------------- #

def test_apply_merges_and_pins_card_id():
    manifest = make_manifest(IMAGE_CARD)
    payload = build_payload(manifest)
    cid = payload["cards"][0]["id"]

    edits = {
        "format": "bookcard-share/edits@1",
        "cards": [{"id": cid, "front": "光的反射【已改】", "back": "新背面", "tags": ["改过"]}],
    }
    merged, stats = apply_edits(manifest, edits)
    card = merged["category"]["libraries"][0]["cards"][0]
    assert card["front"] == "光的反射【已改】"
    assert card["back"] == "新背面"
    assert card["tags"] == ["改过"]
    # 改了 front 之后 id 会变，所以把 id 钉住
    assert card["card_id"] == cid
    assert stats["applied"] == 1


def test_apply_keeps_source_and_image():
    """改词不改它在书上的位置：source / images 原样。"""
    manifest = make_manifest(IMAGE_CARD)
    cid = build_payload(manifest)["cards"][0]["id"]
    merged, _ = apply_edits(
        manifest, {"cards": [{"id": cid, "front": "改过的词"}]}
    )
    card = merged["category"]["libraries"][0]["cards"][0]
    assert card["source"]["page"] == 5
    assert card["images"][0]["occurrences"][0]["x"] == 490


def test_apply_skips_unknown_card():
    manifest = make_manifest(IMAGE_CARD)
    merged, stats = apply_edits(manifest, {"cards": [{"id": "不存在", "front": "x"}]})
    assert stats["applied"] == 0 and stats["skipped"] == 1


def test_apply_moves_card_between_libraries():
    manifest = make_manifest(IMAGE_CARD)
    manifest["category"]["libraries"].append(
        {"name": "第2节", "slug": "abc123def456-s02", "sort_order": 2, "cards": []}
    )
    cid = build_payload(manifest)["cards"][0]["id"]
    merged, stats = apply_edits(
        manifest, {"cards": [{"id": cid, "library_id": "abc123def456-s02"}]}
    )
    assert stats["moved"] == 1
    assert merged["category"]["libraries"][0]["cards"] == []
    assert len(merged["category"]["libraries"][1]["cards"]) == 1


# --------------------------------------------------------------------------- #
# 与引擎对齐（引擎装了才跑）
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(engine_sm2 is None, reason="没装 bookcard 引擎，跳过对照")
@pytest.mark.parametrize(
    "rating,rep,ease,interval",
    [
        ("again", 0, 1.8, 0),
        ("again", 3, 2.1, 10),
        ("good", 0, 1.8, 0),
        ("good", 1, 1.9, 1),
        ("good", 5, 2.3, 7),
    ],
)
def test_sm2_engine_parity(rating, rep, ease, interval):
    """本子项目的页面照抄引擎的 SM-2；这里钉住「同一份输入同一份输出」。"""
    r = engine_sm2.compute_sm2(
        rating, rep, ease, interval, now_ms=1_700_000_000_000
    )
    assert r.repetition >= 1
    assert 1.3 <= r.ease_factor <= 2.5
    assert r.interval_days >= 1


# --------------------------------------------------------------------------- #
# 目录（TOC）+ 页表
# --------------------------------------------------------------------------- #

def make_work_dir(tmp_path):
    """造一个引擎的书工作目录（最小版：toc.json + pages.jsonl）。"""
    work = tmp_path / "book"
    work.mkdir()
    (work / "toc.json").write_text(
        json.dumps(
            {
                "source": "layout",
                "entries": [
                    {"level": 1, "title": "第一章", "page": 9},
                    {"level": 2, "title": "1.1 小节", "page": 14},
                    {"level": 1, "title": "", "page": 20},   # 空标题要丢掉
                    {"level": 1, "title": "坏页码", "page": "x"},
                ],
                "gate": {"status": "approved"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (work / "pages.jsonl").write_text(
        "\n".join(
            json.dumps({"page": p, "printed_page": p - 4})
            for p in range(1, 20)
        ),
        encoding="utf-8",
    )
    return work


def test_load_toc_drops_bad_entries(tmp_path):
    from sharepack.toc import load_toc

    toc = load_toc(make_work_dir(tmp_path))
    assert toc is not None
    assert toc["source"] == "layout"
    # 空标题和坏页码都丢掉
    assert [e["title"] for e in toc["entries"]] == ["第一章", "1.1 小节"]
    assert toc["entries"][0]["page"] == 9


def test_detect_offset(tmp_path):
    """印刷页 = PDF 页 + offset；offset 从 pages.jsonl 推。"""
    from sharepack.toc import detect_offset

    assert detect_offset(make_work_dir(tmp_path)) == -4


def test_detect_offset_none_when_unknown(tmp_path):
    from sharepack.toc import detect_offset

    work = tmp_path / "b"
    work.mkdir()
    (work / "pages.jsonl").write_text(
        "\n".join(json.dumps({"page": p, "printed_page": None}) for p in range(1, 5)),
        encoding="utf-8",
    )
    assert detect_offset(work) is None


def test_payload_carries_toc_and_pages():
    toc = {"source": "layout", "offset": -4, "entries": [{"level": 1, "title": "x", "page": 3}]}
    pages = [{"page": 1, "thumb": "thumbs/p0001.webp", "image": "images/p0001.webp",
              "width": 1111, "height": 1571}]
    payload = build_payload(make_manifest(IMAGE_CARD), toc=toc, pages=pages)
    assert payload["toc"]["offset"] == -4
    assert payload["toc"]["entries"][0]["title"] == "x"
    assert payload["pages"][0]["thumb"] == "thumbs/p0001.webp"


def test_apply_replaces_toc():
    """目录整段盖回去（不是合并）。"""
    manifest = make_manifest(IMAGE_CARD)
    manifest["toc"] = {"source": "layout", "offset": None, "entries": [
        {"level": 1, "title": "旧的", "page": 5}]}
    edits = {"toc": {"offset": -4, "entries": [
        {"level": 2, "title": "新的", "page": 9}]}}
    merged, stats = apply_edits(manifest, edits)
    assert merged["toc"]["offset"] == -4
    assert merged["toc"]["entries"] == [{"level": 2, "title": "新的", "page": 9}]
    assert stats["toc"] == 1


def test_apply_keeps_toc_when_edits_have_none():
    manifest = make_manifest(IMAGE_CARD)
    manifest["toc"] = {"source": "layout", "offset": None, "entries": [
        {"level": 1, "title": "留着", "page": 5}]}
    merged, _ = apply_edits(manifest, {"cards": []})
    assert merged["toc"]["entries"][0]["title"] == "留着"


def test_find_pdf_walks_up(tmp_path):
    """工作目录往上找 src/<file_name>.pdf。"""
    from sharepack.pages import find_pdf

    root = tmp_path / "data"
    (root / "src").mkdir(parents=True)
    pdf = root / "src" / "某书.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    work = root / "bookcard" / "books" / "abc"
    work.mkdir(parents=True)
    assert find_pdf(work, "某书.pdf") == pdf
