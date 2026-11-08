"""bookcard-share —— 把一张卡包变成「能看能改、也能导入 App」的分享包。

一个分享包（zip）里就三样东西：

- ``manifest.json``   —— App 的导入格式（``BulkImportBody`` 形状）
- ``images/*.webp``   —— 全书页图，按 App 的原生格式存（webp）
- ``index.html``      —— 自包含的查看/编辑页，双击就开，零依赖

同一个 zip：解压能看能改，拖进 Flashcard App 能导入。图片只有一份。
"""

from .apply import apply_edits
from .build import ShareResult, apply_and_build, build_share
from .html import render_html
from .pack import Pack, open_pack
from .payload import build_payload
from .webp import QUALITY, to_webp_bytes, webp_name

__all__ = [
    "build_share",
    "apply_and_build",
    "apply_edits",
    "ShareResult",
    "open_pack",
    "Pack",
    "build_payload",
    "render_html",
    "to_webp_bytes",
    "webp_name",
    "QUALITY",
]
