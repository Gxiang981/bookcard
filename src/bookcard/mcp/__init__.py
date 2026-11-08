"""MCP server（``docs/mcp-v1.md`` §3）：stdio JSON-RPC，标准库自己实现。

对外形态之一：``bookcard mcp``（stdio）。**不加运行时依赖**——不引 ``mcp`` SDK，
协议用标准库的 ``json`` / ``sys`` / ``threading`` 实现（契约 §0）。

模块分工：

- :mod:`bookcard.mcp.server`：JSON-RPC 2.0 的收发与消息分发；
- :mod:`bookcard.mcp.tools`：工具表（JSON Schema）与到
  :mod:`bookcard.agentflow` 的接线（**薄包装**，不重写流程逻辑）；
- :mod:`bookcard.mcp.progress`：长任务的 ``notifications/progress``；
- :mod:`bookcard.mcp.browse`：进程内起浏览服务（``browse_open``）。

工具出错时回 ``isError: true``，错误文字用中文并写明下一步怎么做；不往协议层抛。
"""

from __future__ import annotations

from bookcard.mcp.server import MCPServer, main, serve

__all__ = ["MCPServer", "main", "serve"]
