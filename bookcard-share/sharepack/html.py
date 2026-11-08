"""把模板 + 数据 → 一个自包含的 HTML。

抄的是同仓里 ``src/bookcard/review.py`` 的 ``render_html``（:681-704）那套做法：
CSS / JS / 数据全部内联，不联网、不依赖 CDN、不依赖任何后端。

防 ``</script>`` 提前闭合的处理也一样：JSON 里的 ``<`` ``>`` ``&`` 换成
``\\u003c`` ``\\u003e`` ``\\u0026``（JSON 里 \\uXXXX 是合法的，转义后浏览器
解析出来的还是原字符）。
"""

from __future__ import annotations

import json
from pathlib import Path

#: 模板与静态资源所在目录（跟本模块同级）。
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

#: 内嵌数据的 script 标签 id（前端按它取数据）。
DATA_ID = "bookcard-data"


def _escape_json(payload: object) -> str:
    """JSON → 可以安全放进 ``<script type="application/json">`` 的文本。"""
    text = json.dumps(payload, ensure_ascii=False)
    return (
        text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )


def render_html(payload: dict, *, title: str | None = None) -> str:
    """把数据渲染成单个自包含 HTML（CSS / JS / 数据全部内联）。

    产出的页面在 ``file://`` 下双击就能开：没有 fetch、没有外链字体、
    没有外链图片（书页图走包内相对路径）。
    """
    template = (ASSETS_DIR / "template.html").read_text(encoding="utf-8")
    css = (ASSETS_DIR / "page.css").read_text(encoding="utf-8")
    # 本地数据层放前面（它定义 api / tierOf 等），抄来的页面逻辑放后面。
    local_api = (ASSETS_DIR / "local-api.js").read_text(encoding="utf-8")
    page_js = (ASSETS_DIR / "page.js").read_text(encoding="utf-8")
    js = local_api + "\n" + page_js

    if title is None:
        book = payload.get("book") or {}
        title = f"{book.get('title') or 'BookCard'} · BookCard"

    return (
        template.replace("{{TITLE}}", title)
        .replace("/*{{CSS}}*/", css)
        .replace("/*{{JS}}*/", js)
        .replace("{{DATA}}", _escape_json(payload))
    )
