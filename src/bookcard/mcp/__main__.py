"""``python -m bookcard.mcp``：起 MCP 服务（stdio）。

等价于 ``bookcard mcp``——给不方便装控制台脚本的场合用。
"""

from __future__ import annotations

import sys

from bookcard.mcp.server import main

if __name__ == "__main__":
    sys.exit(main())
