"""MCP server（``docs/mcp-v1.md`` §3）：stdio 上的 JSON-RPC 2.0，标准库实现。

协议要点：

- 一行一条消息，JSON-RPC 2.0；
- 实现 ``initialize`` / ``notifications/initialized`` / ``ping`` /
  ``tools/list`` / ``tools/call``；
- ``protocolVersion`` 回客户端请求的版本（支持 ``2025-06-18`` 和 ``2025-03-26``）；
- **stdout 只走协议消息**，日志一律写 stderr；
- 通知（没有 ``id`` 的消息）**不回包**；
- 工具出错回 ``isError: true``，文字用中文并写明下一步；协议级错误走 JSON-RPC error。
"""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Sequence
from typing import Any, TextIO

from bookcard import __version__
from bookcard.mcp.progress import ProgressReporter
from bookcard.mcp.tools import ToolBox, UnknownToolError

#: 支持的协议版本（契约 §3）。
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = ("2025-06-18", "2025-03-26")

#: 客户端没给版本（或给了不支持的版本）时回这个。
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

#: 服务器名字（MCP 的 ``serverInfo.name``）。
SERVER_NAME = "bookcard"

#: JSON-RPC 2.0 的错误码。
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

#: ``initialize`` 里带给 agent 的用法说明（agent 看得见的一段话）。
INSTRUCTIONS = (
    "从书里提取学习卡片。流程：book_add → toc_review / toc_gate（目录闸门）→ "
    "sections_build → extract_next / extract_submit（逐段抽词，直到 segments 为空）→ "
    "cards_build → pack → browse_open。每做完一步调 book_status 看 next。"
    "抽词用目录的第几层由用户在核对目录时定：toc_gate 传 level（默认 1）；"
    "extract_next 也可以带 level 重切词库（一次只能一层）。"
    "场景默认走书页图（image），不用问用户；用户主动要文本块时 pack 才传 "
    "scene=text、scene_chosen_by=user。"
    "抽词由你自己的模型作答，引擎不调模型、不读 key。"
    "只有一道闸门（目录确认），不许跳过；闸门没过就把问题讲给用户。"
)


class MCPServer:
    """MCP 的 stdio 服务端。"""

    def __init__(
        self,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        home: str | None = None,
    ) -> None:
        self.stdin = stdin if stdin is not None else sys.stdin
        self.stdout = stdout if stdout is not None else sys.stdout
        self.stderr = stderr if stderr is not None else sys.stderr
        self.toolbox = ToolBox(home=home)
        self._write_lock = threading.Lock()
        self._protocol_version = DEFAULT_PROTOCOL_VERSION

    # ------------------------------------------------------------------ 收 / 发

    def _send(self, payload: dict[str, Any]) -> None:
        """发一条 JSON-RPC 消息（加锁：进度通知是另起线程发的）。"""
        line = json.dumps(payload, ensure_ascii=False)
        with self._write_lock:
            self.stdout.write(line + "\n")
            self.stdout.flush()

    def _log(self, message: str) -> None:
        """日志只写 stderr（stdout 归协议）。"""
        print(f"[bookcard-mcp] {message}", file=self.stderr, flush=True)

    # ------------------------------------------------------------------ 分发

    def handle(self, message: Any) -> dict[str, Any] | None:
        """处理一条消息；返回要回的消息，**通知不回包**（返回 ``None``）。"""
        if not isinstance(message, dict):
            return self._error(None, INVALID_REQUEST, "请求必须是 JSON 对象。")

        method = message.get("method")
        has_id = "id" in message
        msg_id = message.get("id")
        is_notification = not has_id

        if not isinstance(method, str) or not method:
            if is_notification:
                return None
            return self._error(msg_id, INVALID_REQUEST, "请求缺少 method。")

        params = message.get("params")
        if not isinstance(params, dict):
            params = {}

        handler = self._methods().get(method)
        if handler is None:
            if is_notification:
                self._log(f"忽略未知通知：{method}")
                return None
            return self._error(
                msg_id,
                METHOD_NOT_FOUND,
                f"没有这个方法：{method}。支持的方法：{'、'.join(sorted(self._methods()))}。",
            )

        try:
            result = handler(params, msg_id)
        except UnknownToolError as exc:
            return self._error(msg_id, INVALID_PARAMS, str(exc))
        except Exception as exc:  # noqa: BLE001 - 协议层错误也要说清楚
            self._log(f"方法 {method} 出错：{type(exc).__name__}: {exc}")
            if is_notification:
                return None
            return self._error(msg_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _methods(self) -> dict[str, Any]:
        """方法表（每次现取：``_meta`` 之类的状态变了不影响）。"""
        return {
            "initialize": self._handle_initialize,
            "notifications/initialized": self._handle_initialized,
            "ping": self._handle_ping,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
        }

    # ------------------------------------------------------------------ 方法

    def _handle_initialize(self, params: dict[str, Any], msg_id: Any) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
            self._protocol_version = requested
        else:
            self._protocol_version = DEFAULT_PROTOCOL_VERSION
        self._log(
            f"initialize：客户端要 {requested!r}，回 {self._protocol_version}"
            f"（客户端：{params.get('clientInfo')}）"
        )
        return {
            "protocolVersion": self._protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
            "instructions": INSTRUCTIONS,
        }

    def _handle_initialized(self, params: dict[str, Any], msg_id: Any) -> dict[str, Any]:
        """``notifications/initialized``：客户端说准备好了。"""
        self._log("客户端已初始化")
        return {}

    def _handle_ping(self, params: dict[str, Any], msg_id: Any) -> dict[str, Any]:
        """``ping``：回空对象。"""
        return {}

    def _handle_tools_list(self, params: dict[str, Any], msg_id: Any) -> dict[str, Any]:
        """``tools/list``：工具清单。"""
        return {"tools": self.toolbox.list_tools()}

    def _handle_tools_call(self, params: dict[str, Any], msg_id: Any) -> dict[str, Any]:
        """``tools/call``：调一个工具。"""
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise UnknownToolError("调用工具时必须给 name。")
        arguments = params.get("arguments")
        if arguments is not None and not isinstance(arguments, dict):
            raise UnknownToolError("arguments 必须是对象。")

        token = self._progress_token(params)
        reporter = ProgressReporter(self._send, token)
        return self.toolbox.call(name, arguments, reporter)

    def _progress_token(self, params: dict[str, Any]) -> Any:
        """progressToken 只认这一次请求的 ``_meta``（MCP 里是 per-request 的）。"""
        meta = params.get("_meta")
        if isinstance(meta, dict) and meta.get("progressToken") is not None:
            return meta["progressToken"]
        return None

    # ------------------------------------------------------------------ 错误

    def _error(self, msg_id: Any, code: int, message: str) -> dict[str, Any]:
        """一条 JSON-RPC 错误。"""
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        }

    # ------------------------------------------------------------------ 主循环

    def serve(self) -> int:
        """读 stdin、回 stdout；到 EOF 就退。"""
        for line in self.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                self._send(self._error(None, PARSE_ERROR, f"这不是合法的 JSON：{exc}"))
                continue
            response = self.handle(message)
            if response is not None:
                self._send(response)
        self.toolbox.browse.close_all()
        return 0


def serve(argv: Sequence[str] | None = None) -> int:
    """起 MCP 服务（stdio）。"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="bookcard mcp",
        description="BookCard 的 MCP 服务（stdio）",
    )
    parser.add_argument(
        "--home",
        metavar="DIR",
        default=None,
        help="书的工作目录根（默认 $BOOKCARD_HOME，没有就是 ~/.bookcard）",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    server = MCPServer(home=args.home)
    try:
        return server.serve()
    except KeyboardInterrupt:
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    """``bookcard mcp`` 的入口。"""
    return serve(argv)
