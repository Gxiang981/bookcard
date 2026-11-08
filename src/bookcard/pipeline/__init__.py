"""bookcard 流水线：挑卡 → 定位 → 质检 → 私有包。

对应契约 v1（``docs/contract-v1.md``）§5–§7：

- :mod:`bookcard.pipeline.select` —— §5 挑卡去重，出 ``cards.jsonl``；
- :mod:`bookcard.pipeline.locate` —— §5 定位，出页面图和像素坐标框；
- :mod:`bookcard.pipeline.audit` —— §6 质检，出 ``quality_report.json``；
- :mod:`bookcard.pipeline.pack` —— §7 私有包，出 ``pack/<fp12>.ttfc.zip``。

四步都只依赖 pymupdf 和标准库；共享的工作目录读写在
:mod:`bookcard.pipeline.workdir`。
"""

from bookcard.pipeline.audit import audit
from bookcard.pipeline.locate import locate
from bookcard.pipeline.pack import PackError, pack
from bookcard.pipeline.select import select

__all__ = ["PackError", "audit", "locate", "pack", "select"]
