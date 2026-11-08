"""长任务的 progress 通知（``docs/mcp-v1.md`` §3）。

长任务（OCR、定位、打包）**同步执行**，执行期间按页发
``notifications/progress``——客户端给了 ``progressToken`` 才发。

进度从哪里来：MCP 层**不重写流程逻辑**（只用 :mod:`bookcard.agentflow` 的现有
函数），所以进度按**落盘的工件**计数：

============ ==================================================
任务         计数的工件
============ ==================================================
``book_add`` ``<book_dir>/ocr/p*.json``（契约 §2.2 的逐页 OCR 行框）
``cards_build`` ``<book_dir>/images/p*.jpg``（定位逐页渲染的书页图）
``pack``      卡包 zip 的字节数（图片是定位那步渲染好的，这步只写 zip）
============ ==================================================

扫描件的 OCR 单还没并进来时，``ocr/`` 目录不会出现——这时进度停在 0。
不管中间发没发过，任务结束都会再补一条「完成」通知（取最后一次
``counter()`` 的值；计数挂了就用已发的最后一条），进度只增不减。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

#: 轮询间隔（秒）。
POLL_INTERVAL = 0.2


class ProgressReporter:
    """发 ``notifications/progress`` 的小东西。

    没有 ``progressToken`` 就什么都不发（客户端没要求进度）。
    """

    def __init__(
        self,
        send: Callable[[dict[str, Any]], None],
        token: str | int | None = None,
    ) -> None:
        self._send = send
        self.token = token

    @property
    def enabled(self) -> bool:
        """客户端要不要进度。"""
        return self.token is not None

    def emit(
        self,
        progress: float,
        total: float | None = None,
        message: str = "",
    ) -> None:
        """发一条进度通知。"""
        if not self.enabled:
            return
        params: dict[str, Any] = {"progressToken": self.token, "progress": progress}
        if total is not None:
            params["total"] = total
        if message:
            params["message"] = message
        self._send({"jsonrpc": "2.0", "method": "notifications/progress", "params": params})


def run_with_progress(
    reporter: ProgressReporter,
    counter: Callable[[], tuple[float, float | None, str]],
    func: Callable[[], Any],
    *,
    interval: float = POLL_INTERVAL,
) -> Any:
    """跑一个长任务，同时按 ``counter()`` 报的进度发通知。

    ``counter()`` 返回 ``(progress, total, message)``；``total`` 可以是
    ``None``（不知道总量）。没有 progressToken 时直接同步跑，不开线程。
    """
    if not reporter.enabled:
        return func()

    box: dict[str, Any] = {}

    def worker() -> None:
        try:
            box["value"] = func()
        except BaseException as exc:  # noqa: BLE001 - 原样抛给调用方
            box["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    last: tuple[float, float | None, str] | None = None
    while thread.is_alive():
        try:
            current = counter()
        except Exception:  # noqa: BLE001 - 计数失败不该拖垮任务
            current = None
        if current is not None and current != last:
            reporter.emit(current[0], current[1], current[2])
            last = current
        thread.join(interval)
    thread.join()

    if "error" in box:
        raise box["error"]

    # 完成标记：不管中间发没发过，结束都补一条。计数挂了就用最后一条。
    try:
        final = counter()
    except Exception:  # noqa: BLE001 - 计数失败不该拖垮任务
        final = None
    if final is None:
        final = last
    if final is not None:
        progress, total, message = final
        if last is not None and progress < last[0]:
            progress = last[0]
        reporter.emit(progress, total, message)
    return box.get("value")
