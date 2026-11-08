"""browse-lite 的本地 HTTP 服务（只用标准库）。

契约 §4 的路由；只监听 ``127.0.0.1``。后端零依赖：
``http.server`` + ``sqlite3`` + ``json`` + ``zipfile``（契约 §0）。

- ``GET /``：前端 ``static/index.html``；**没建**就回 503 纯文本
  （前端在并行的一单里做，本单不碰）。
- ``GET /images/<book_id>/<file>``：书页图，**路径穿越一律挡住**（403）。
- ``/api/*``：JSON；出错回 4xx/5xx + ``{"error": "..."}``。

每个请求开一条自己的 SQLite 连接（``sqlite3`` 的连接不能跨线程共用，
而 ``ThreadingHTTPServer`` 一请求一线程）。
"""

from __future__ import annotations

import json
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from bookcard.browse.db import BrowseDB, BrowseError

#: 前端单文件（并行的一单里建；本单只负责在它存在时把它发出去）。
STATIC_DIR = Path(__file__).with_name("static")
INDEX_FILE = STATIC_DIR / "index.html"

#: 前端还没建时 ``GET /`` 的回文。
NOT_BUILT_MESSAGE = "frontend not built"

#: 图片的 content-type。
CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}

#: 允许的图片文件名（防穿越 / 防怪名字）。
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")

_CARD_ID = r"(?P<card_id>[^/]+)"


class BrowseHandler(BaseHTTPRequestHandler):
    """browse-lite 的请求处理（一个类一个库，库挂在类属性上）。"""

    server_version = "bookcard-browse/1.0"
    protocol_version = "HTTP/1.1"

    #: 由 :func:`make_server` 挂上。
    db: BrowseDB

    # ---------- 基础工具 ----------

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - 覆写父类签名
        """日志压成一行（默认实现会打一堆 BaseHTTPRequestHandler 的噪音）。"""
        print(f"[browse] {self.address_string()} {format % args}")

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        """回一段二进制（带 Content-Length，HTTP/1.1 要它）。"""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload: dict | list) -> None:
        """回 JSON（UTF-8）。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_error_json(self, status: int, message: str) -> None:
        """回 ``{"error": "..."}``。"""
        self._send_json(status, {"error": message})

    def _read_json_body(self) -> dict:
        """读请求体并解析成对象（不是对象就 400）。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise BrowseError("Content-Length 不合法") from exc
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BrowseError(f"请求体不是合法 JSON：{exc}") from exc
        if not isinstance(payload, dict):
            raise BrowseError("请求体必须是 JSON 对象")
        return payload

    # ---------- 路由 ----------

    def do_GET(self) -> None:  # noqa: N802 - 父类命名
        """GET。"""
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/" or path == "/index.html":
                self._serve_index()
                return
            if path.startswith("/images/"):
                self._serve_image(path)
                return
            if path == "/api/tree":
                self._send_json(HTTPStatus.OK, self.db.tree())
                return
            if path == "/api/cards":
                self._serve_cards(parse_qs(parsed.query))
                return
            match = re.fullmatch(r"/api/cards/" + _CARD_ID, path)
            if match:
                self._send_json(HTTPStatus.OK, self._card_detail(match.group("card_id")))
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, f"没有这个接口：{path}")
        except BrowseError as exc:
            self._send_error_json(exc.status, str(exc))
        except Exception as exc:  # noqa: BLE001 - 兜底：别把服务打挂
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")

    def do_POST(self) -> None:  # noqa: N802 - 父类命名
        """POST（评分 / 忽略）。"""
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            match = re.fullmatch(r"/api/cards/" + _CARD_ID + r"/rate", path)
            if match:
                payload = self._read_json_body()
                rating = payload.get("rating")
                if not isinstance(rating, str):
                    raise BrowseError("缺 rating")
                self._send_json(HTTPStatus.OK, self.db.rate(match.group("card_id"), rating))
                return
            match = re.fullmatch(r"/api/cards/" + _CARD_ID + r"/ignore", path)
            if match:
                payload = self._read_json_body()
                ignored = payload.get("ignored")
                if not isinstance(ignored, bool):
                    raise BrowseError("缺 ignored（要 true / false）")
                self._send_json(HTTPStatus.OK, self.db.set_ignored(match.group("card_id"), ignored))
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, f"没有这个接口：{path}")
        except BrowseError as exc:
            self._send_error_json(exc.status, str(exc))
        except Exception as exc:  # noqa: BLE001 - 兜底
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")

    def do_PUT(self) -> None:  # noqa: N802 - 父类命名
        """PUT（改卡）。"""
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            match = re.fullmatch(r"/api/cards/" + _CARD_ID, path)
            if match:
                payload = self._read_json_body()
                tags = payload.get("tags")
                if tags is not None and not isinstance(tags, list):
                    raise BrowseError("tags 必须是数组")
                front = payload.get("front")
                back = payload.get("back")
                library_id = payload.get("library_id")
                if front is not None and not isinstance(front, str):
                    raise BrowseError("front 必须是字符串")
                if back is not None and not isinstance(back, str):
                    raise BrowseError("back 必须是字符串")
                if library_id is not None and not isinstance(library_id, str):
                    raise BrowseError("library_id 必须是字符串")
                self._send_json(
                    HTTPStatus.OK,
                    self.db.edit_card(
                        match.group("card_id"),
                        front=front,
                        back=back,
                        tags=tags,
                        library_id=library_id,
                    ),
                )
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, f"没有这个接口：{path}")
        except BrowseError as exc:
            self._send_error_json(exc.status, str(exc))
        except Exception as exc:  # noqa: BLE001 - 兜底
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")

    # ---------- 各路由的活儿 ----------

    def _serve_index(self) -> None:
        """``GET /``：前端单文件；没建就 503。"""
        if not INDEX_FILE.is_file():
            self._send_bytes(
                HTTPStatus.SERVICE_UNAVAILABLE,
                NOT_BUILT_MESSAGE.encode("utf-8"),
                "text/plain; charset=utf-8",
            )
            return
        self._send_bytes(HTTPStatus.OK, INDEX_FILE.read_bytes(), "text/html; charset=utf-8")

    def _serve_image(self, path: str) -> None:
        """``GET /images/<book_id>/<file>``：书页图（路径穿越一律挡住）。"""
        segments = [segment for segment in path.split("/") if segment != ""]
        # 期望：["images", book_id, file]
        if len(segments) != 3 or segments[0] != "images":
            self._send_error_json(HTTPStatus.NOT_FOUND, "图片路径不对")
            return
        book_id, file_name = segments[1], segments[2]
        if not _SAFE_SEGMENT.match(book_id) or not _SAFE_SEGMENT.match(file_name):
            self._send_error_json(HTTPStatus.FORBIDDEN, "路径不合法")
            return
        if ".." in book_id or ".." in file_name:
            self._send_error_json(HTTPStatus.FORBIDDEN, "路径不合法")
            return
        target = self.db.images_dir / book_id / file_name
        try:
            resolved = target.resolve()
            base = self.db.images_dir.resolve()
        except OSError:  # pragma: no cover - 解析不了就当没有
            resolved, base = target, self.db.images_dir
        if base != resolved and base not in resolved.parents:
            self._send_error_json(HTTPStatus.FORBIDDEN, "路径不合法")
            return
        if not target.is_file():
            self._send_error_json(HTTPStatus.NOT_FOUND, "没有这张图")
            return
        content_type = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self._send_bytes(HTTPStatus.OK, target.read_bytes(), content_type)

    def _serve_cards(self, query: dict[str, list[str]]) -> None:
        """``GET /api/cards``：book 或 library 二选一 + tier 筛选。"""
        book = (query.get("book") or [None])[0]
        library = (query.get("library") or [None])[0]
        off_raw = (query.get("off") or [""])[0]
        off = [token for token in off_raw.split(",") if token.strip()]
        self._send_json(
            HTTPStatus.OK,
            self.db.cards(book_id=book, library_id=library, off=off),
        )

    def _card_detail(self, card_id: str) -> dict:
        """``GET /api/cards/<id>``。"""
        detail = self.db.card_detail(card_id)
        if detail is None:
            raise BrowseError(f"卡不存在：{card_id}", status=HTTPStatus.NOT_FOUND)
        return detail


def make_server(db: BrowseDB, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """造一个服务（不 start）。"""
    db.initialize()
    handler = type("BoundBrowseHandler", (BrowseHandler,), {"db": db})
    return ThreadingHTTPServer((host, port), handler)


def serve(
    db: BrowseDB,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
) -> None:
    """起服务并一直跑（Ctrl-C 停）。

    ``open_browser`` 为真时，服务起来后用默认浏览器打开首页。
    """
    httpd = make_server(db, host=host, port=port)
    actual_port = httpd.server_address[1]
    url = f"http://{host}:{actual_port}/"
    print(f"[browse] 数据库：{db.db_path}")
    print(f"[browse] 打开：{url}")
    if open_browser:
        threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("[browse] 停了")
    finally:
        httpd.server_close()


def _open_browser(url: str) -> None:
    """用默认浏览器打开（失败就当没这回事，不能让服务起不来）。"""
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - 浏览器起不来不影响服务
        pass


def serve_in_thread(
    db: BrowseDB,
    host: str = "127.0.0.1",
    port: int = 0,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """后台起服务（自测用）：端口传 0 让系统挑一个空闲端口。

    返回 ``(server, thread)``。
    """
    httpd = make_server(db, host=host, port=port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread
