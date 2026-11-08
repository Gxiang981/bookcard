"""bookcard 探针。

对外只有一个入口：:func:`probe`，输入一本书的路径，输出一份体检报告。
"""

from bookcard.probe.core import PROBE_VERSION, broken_report, probe

__all__ = ["PROBE_VERSION", "broken_report", "probe"]
