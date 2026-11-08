"""本地 HTTP 服务的测试（契约 §4）。

服务起在 ``127.0.0.1`` 的随机端口上（端口 0 ⇒ 系统挑一个空闲端口），
用 ``http.client`` 打——**不用 urllib**（urllib 会把 ``/images/../x`` 这样的路径
先规范化掉，测不出穿越防护）。
"""

import http.client
import json

import pytest

from bookcard.browse.db import BrowseDB
from bookcard.browse.server import serve_in_thread
from browse_fixtures import FAKE_JPEG, IMAGE_BOOK_ID, image_manifest, write_pack_zip


@pytest.fixture
def server(tmp_path):
    """起一个服务（图片场景包已经导进去）。"""
    db = BrowseDB(tmp_path / "browse.db")
    db.import_pack(
        write_pack_zip(
            tmp_path / "image.ttfc.zip",
            image_manifest(),
            {"images/p0012.jpg": FAKE_JPEG},
        )
    )
    httpd, thread = serve_in_thread(db, host="127.0.0.1", port=0)
    host, port = httpd.server_address[0], httpd.server_address[1]
    try:
        yield {"host": host, "port": port, "db": db}
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _call(server, method: str, path: str, body: dict | None = None) -> tuple[int, bytes]:
    """打一个请求，返回 ``(状态码, 响应体)``。"""
    conn = http.client.HTTPConnection(server["host"], server["port"], timeout=10)
    try:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def _json(server, method: str, path: str, body: dict | None = None):
    """打一个请求并解析 JSON。"""
    status, raw = _call(server, method, path, body)
    return status, json.loads(raw.decode("utf-8"))


# ===================== 只读路由 =====================


def test_tree(server):
    """``GET /api/tree``。"""
    status, payload = _json(server, "GET", "/api/tree")
    assert status == 200
    assert isinstance(payload, list)
    assert payload[0]["id"] == IMAGE_BOOK_ID
    assert payload[0]["scene_mode"] == "image"
    assert payload[0]["libraries"][0]["count"] == 2


def test_cards_by_book(server):
    """``GET /api/cards?book=...``。"""
    status, payload = _json(server, "GET", f"/api/cards?book={IMAGE_BOOK_ID}")
    assert status == 200
    assert len(payload["cards"]) == 2
    assert payload["tier_counts"] == {"T1": 0, "T2": 2, "T4": 0, "T6": 0, "T7": 0}
    assert payload["cards"][0]["tier"] == "T2"
    assert "back_preview" in payload["cards"][0]


def test_cards_by_library(server):
    """``GET /api/cards?library=...``。"""
    library_id = f"{IMAGE_BOOK_ID}-s01"
    status, payload = _json(server, "GET", f"/api/cards?library={library_id}")
    assert status == 200
    assert len(payload["cards"]) == 2


def test_cards_requires_book_or_library(server):
    """book 与 library 必须给且只给一个（都给 / 都不给都 400）。"""
    status, payload = _json(server, "GET", "/api/cards")
    assert status == 400
    assert "error" in payload

    status, payload = _json(
        server, "GET", f"/api/cards?book={IMAGE_BOOK_ID}&library={IMAGE_BOOK_ID}-s01"
    )
    assert status == 400


def test_cards_tier_filter(server):
    """``off=T1,T6``：tier_counts 统计的是**筛选前**的范围（契约 §4）。"""
    card_id = _json(server, "GET", f"/api/cards?book={IMAGE_BOOK_ID}")[1]["cards"][0]["id"]
    _json(server, "POST", f"/api/cards/{card_id}/rate", {"rating": "good"})

    # 评过 good ⇒ T4（1 天后才到期，还没到期）。
    _, payload = _json(server, "GET", f"/api/cards?book={IMAGE_BOOK_ID}")
    assert payload["tier_counts"]["T4"] == 1
    assert payload["tier_counts"]["T2"] == 1

    # off=T4 ⇒ 把那张学过的筛掉；tier_counts 仍是筛选前的数。
    status, payload = _json(server, "GET", f"/api/cards?book={IMAGE_BOOK_ID}&off=T4")
    assert status == 200
    assert len(payload["cards"]) == 1
    assert payload["cards"][0]["tier"] == "T2"
    assert payload["tier_counts"]["T4"] == 1


def test_card_detail(server):
    """``GET /api/cards/<id>``。"""
    card_id = _json(server, "GET", f"/api/cards?book={IMAGE_BOOK_ID}")[1]["cards"][0]["id"]
    status, payload = _json(server, "GET", f"/api/cards/{card_id}")
    assert status == 200
    assert payload["id"] == card_id
    assert payload["book_id"] == IMAGE_BOOK_ID
    assert payload["tier"] == "T2"
    assert payload["scene"]["kind"] == "image"
    assert payload["edited"] is False
    assert set(payload["state"]) == {
        "repetition",
        "ease",
        "interval_days",
        "next_review_at",
        "last_reviewed_at",
        "reviews",
        "lapses",
        "ignored",
        "mastered",
    }


def test_card_detail_unknown_is_404(server):
    """不存在的卡 ⇒ 404 + ``{"error": ...}``。"""
    status, payload = _json(server, "GET", "/api/cards/nope")
    assert status == 404
    assert "error" in payload


# ===================== 写路由 =====================


def test_rate(server):
    """``POST /api/cards/<id>/rate``：走 SM-2，state 变。"""
    card_id = _json(server, "GET", f"/api/cards?book={IMAGE_BOOK_ID}")[1]["cards"][0]["id"]
    status, payload = _json(server, "POST", f"/api/cards/{card_id}/rate", {"rating": "good"})
    assert status == 200
    assert payload["state"]["reviews"] == 1
    assert payload["state"]["repetition"] == 1
    assert payload["state"]["interval_days"] == 1
    assert payload["state"]["mastered"] == 0
    # 评 good ⇒ 1 天后才到期 ⇒ T4（学过），不是 T1。
    assert payload["tier"] == "T4"


def test_rate_easy_marks_mastered(server):
    """``easy`` ⇒ mastered=1（契约 §4 / App ）。"""
    card_id = _json(server, "GET", f"/api/cards?book={IMAGE_BOOK_ID}")[1]["cards"][0]["id"]
    _, payload = _json(server, "POST", f"/api/cards/{card_id}/rate", {"rating": "easy"})
    assert payload["state"]["mastered"] == 1
    assert payload["tier"] == "T7"


def test_rate_unclear_clears_mastered(server):
    """``unclear`` ⇒ mastered=0。"""
    card_id = _json(server, "GET", f"/api/cards?book={IMAGE_BOOK_ID}")[1]["cards"][0]["id"]
    _json(server, "POST", f"/api/cards/{card_id}/rate", {"rating": "easy"})
    _, payload = _json(server, "POST", f"/api/cards/{card_id}/rate", {"rating": "unclear"})
    assert payload["state"]["mastered"] == 0


def test_rate_bad_rating_is_400(server):
    """不认识的评分值 ⇒ 400。"""
    card_id = _json(server, "GET", f"/api/cards?book={IMAGE_BOOK_ID}")[1]["cards"][0]["id"]
    status, payload = _json(server, "POST", f"/api/cards/{card_id}/rate", {"rating": "great"})
    assert status == 400
    assert "error" in payload


def test_rate_unknown_card_is_404(server):
    """给不存在的卡评分 ⇒ 404。"""
    status, payload = _json(server, "POST", "/api/cards/nope/rate", {"rating": "good"})
    assert status == 404
    assert "error" in payload


def test_ignore(server):
    """``POST /api/cards/<id>/ignore``。"""
    card_id = _json(server, "GET", f"/api/cards?book={IMAGE_BOOK_ID}")[1]["cards"][0]["id"]
    status, payload = _json(server, "POST", f"/api/cards/{card_id}/ignore", {"ignored": True})
    assert status == 200
    assert payload["state"]["ignored"] == 1
    assert payload["tier"] == "T6"

    _, payload = _json(server, "POST", f"/api/cards/{card_id}/ignore", {"ignored": False})
    assert payload["state"]["ignored"] == 0
    assert payload["tier"] == "T2"


def test_edit_card(server):
    """``PUT /api/cards/<id>``：改完 ``edited=1``。"""
    card_id = _json(server, "GET", f"/api/cards?book={IMAGE_BOOK_ID}")[1]["cards"][0]["id"]
    status, payload = _json(
        server,
        "PUT",
        f"/api/cards/{card_id}",
        {"front": "新正面", "back": "新背面", "tags": ["标签"]},
    )
    assert status == 200
    assert payload["front"] == "新正面"
    assert payload["back"] == "新背面"
    assert payload["tags"] == ["标签"]
    assert payload["edited"] is True


def test_edit_front_too_long_is_400(server):
    """front 超过 500 字符 ⇒ 400。"""
    card_id = _json(server, "GET", f"/api/cards?book={IMAGE_BOOK_ID}")[1]["cards"][0]["id"]
    status, payload = _json(server, "PUT", f"/api/cards/{card_id}", {"front": "x" * 501})
    assert status == 400
    assert "error" in payload


def test_edit_empty_front_is_400(server):
    """front 为空 ⇒ 400。"""
    card_id = _json(server, "GET", f"/api/cards?book={IMAGE_BOOK_ID}")[1]["cards"][0]["id"]
    status, _ = _json(server, "PUT", f"/api/cards/{card_id}", {"front": ""})
    assert status == 400


def test_edit_unknown_card_is_404(server):
    """改不存在的卡 ⇒ 404。"""
    status, payload = _json(server, "PUT", "/api/cards/nope", {"front": "x"})
    assert status == 404
    assert "error" in payload


# ===================== 静态资源 =====================


def test_index_without_frontend_is_503(server, tmp_path, monkeypatch):
    """前端还没建 ⇒ ``GET /`` 回 503 纯文本。"""
    monkeypatch.setattr("bookcard.browse.server.INDEX_FILE", tmp_path / "missing.html")
    status, body = _call(server, "GET", "/")
    assert status == 503
    assert b"frontend not built" in body


def test_index_serves_the_built_frontend(server):
    """前端建好了 ⇒ ``GET /`` 回 index.html。"""
    status, body = _call(server, "GET", "/")
    assert status == 200
    assert b"<html" in body.lower()


def test_image(server):
    """``GET /images/<book_id>/<file>``。"""
    status, body = _call(server, "GET", f"/images/{IMAGE_BOOK_ID}/p0012.jpg")
    assert status == 200
    assert body == FAKE_JPEG


def test_image_unknown_is_404(server):
    """没有这张图 ⇒ 404。"""
    status, _ = _call(server, "GET", f"/images/{IMAGE_BOOK_ID}/nope.jpg")
    assert status == 404


def test_image_path_traversal_is_blocked(server):
    """🔴 路径穿越 ⇒ 403 / 404（绝不能把文件发出去）。"""
    for path in (
        "/images/../x",
        "/images/..%2Fx",
        f"/images/{IMAGE_BOOK_ID}/../../etc/passwd",
    ):
        status, body = _call(server, "GET", path)
        assert status in (403, 404), path
        assert b"root:" not in body


def test_unknown_route_is_404(server):
    """没有这个接口 ⇒ 404 + JSON。"""
    status, payload = _json(server, "GET", "/api/nope")
    assert status == 404
    assert "error" in payload
