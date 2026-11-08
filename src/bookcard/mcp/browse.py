"""进程内的浏览服务（``browse_open``，契约 §3 / ``docs/browse-lite-v1.md``）。

``browse_open`` 做两件事：

1. 把这本书的卡包导进浏览库（``~/.bookcard/browse/browse.db``，幂等）；
2. 在 MCP 进程里起一条线程跑浏览服务，**只监听 127.0.0.1**，
   端口被占就顺延，返回 URL。

重复调用**复用已起的服务**（同一个库只起一个服务）。

浏览服务自己的 HTTP 日志走 **stderr**——stdout 是 MCP 的 JSON-RPC 通道，
不能混。
"""

from __future__ import annotations

import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from bookcard.browse.db import DEFAULT_DB_PATH, BrowseDB, BrowseError
from bookcard.browse.server import BrowseHandler

#: 起服务的默认端口（被占就顺延）。
DEFAULT_PORT = 8765

#: 端口顺延的次数上限。
MAX_PORT_ATTEMPTS = 20

#: 只监听本机。
HOST = "127.0.0.1"


def _log_to_stderr(self: BrowseHandler, format: str, *args: Any) -> None:  # noqa: A002
    """浏览服务的请求日志走 stderr（stdout 归 JSON-RPC）。"""
    print(f"[browse] {self.address_string()} {format % args}", file=sys.stderr)


def _packs_of(book_dir: Path) -> list[Path]:
    """这本书已经打好的包（内部分享版优先）。"""
    pack_dir = book_dir / "pack"
    if not pack_dir.is_dir():
        return []
    packs = sorted(p for p in pack_dir.glob("*.ttfc.zip") if not p.name.startswith("."))
    private = [pack for pack in packs if not pack.name.endswith(".public.ttfc.zip")]
    return private or packs


class BrowseRunner:
    """进程内已经起着的浏览服务（按库的路径复用）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._servers: dict[str, dict[str, Any]] = {}

    # -- 内部 ------------------------------------------------------------

    def _start(self, db: BrowseDB, db_path: Path) -> tuple[ThreadingHTTPServer, str]:
        """起一个服务；端口被占就顺延。"""
        db.initialize()
        handler = type(
            "McpBrowseHandler",
            (BrowseHandler,),
            {"db": db, "log_message": _log_to_stderr},
        )
        last_error: OSError | None = None
        for offset in range(MAX_PORT_ATTEMPTS):
            port = DEFAULT_PORT + offset
            try:
                httpd = ThreadingHTTPServer((HOST, port), handler)
            except OSError as exc:
                last_error = exc
                continue
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            return httpd, f"http://{HOST}:{httpd.server_address[1]}/"
        raise BrowseError(
            f"起浏览服务失败：{DEFAULT_PORT} 到 {DEFAULT_PORT + MAX_PORT_ATTEMPTS - 1} 端口都被占了"
            f"（{last_error}）"
        )

    # -- 对外 ------------------------------------------------------------

    def open_book(self, book_dir: str | Path, db_path: str | Path | None = None) -> dict[str, Any]:
        """把一本书的包导进浏览库并起浏览服务，返回 URL。"""
        path = Path(book_dir)
        packs = _packs_of(path)
        if not packs:
            raise BrowseError(
                f"这本书还没打好包：{path / 'pack'} 里没有 .ttfc.zip。"
                "下一步：先调 pack 工具出卡包，再调 browse_open。"
            )

        target = Path(db_path).expanduser() if db_path else DEFAULT_DB_PATH
        db = BrowseDB(target)

        imported: list[dict[str, Any]] = []
        for pack in packs:
            try:
                imported.append(db.import_pack(pack))
            except BrowseError as exc:
                raise BrowseError(f"导入卡包失败（{pack.name}）：{exc}") from exc

        key = str(target)
        with self._lock:
            running = self._servers.get(key)
            reused = running is not None
            if running is None:
                httpd, url = self._start(db, target)
                running = {"httpd": httpd, "url": url}
                self._servers[key] = running
            else:
                url = str(running["url"])

        return {
            "url": url,
            "db": str(target),
            "reused": reused,
            "imported": [
                {
                    "pack": str(pack),
                    "title": summary.get("title"),
                    "book_id": summary.get("book_id"),
                    "libraries": summary.get("libraries"),
                    "cards": summary.get("cards"),
                    "images": summary.get("images"),
                    "scene_mode": summary.get("scene_mode"),
                    "visibility": summary.get("visibility"),
                }
                for pack, summary in zip(packs, imported, strict=True)
            ],
        }

    def close_all(self) -> None:
        """把所有浏览服务停掉（进程退出时用）。"""
        with self._lock:
            for running in self._servers.values():
                httpd = running.get("httpd")
                if httpd is not None:
                    try:
                        httpd.shutdown()
                        httpd.server_close()
                    except Exception:  # noqa: BLE001 - 停不掉也不能影响退出
                        pass
            self._servers.clear()
