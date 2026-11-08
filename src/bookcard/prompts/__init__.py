"""包内提示词（契约 §1 / ``docs/mcp-v1.md`` §1）。

提示词**跟着包走**：``extract_v1.md`` / ``judge_v1.md`` 放在
``src/bookcard/prompts/`` 下，按 package data 打包，用
:mod:`importlib.resources` 读——不依赖仓库根的 ``prompts/`` 目录，
装成 wheel / ``uv tool install`` 之后一样能读到。

口径：

- ``extract_v1.md`` —— 抽词提示词，版本号 ``ex-v1``（**内容一个字都不动**，
  改了就升版本）；分两段（SYSTEM / USER），分隔标记是单独一行的 ``===USER===``。
- ``judge_v1.md`` —— 试抽审卡提示词，内容是从 ``bookcard.judge`` 的
  ``SYSTEM`` 常量**逐字**搬过来的（契约 §6 的「卡片判定」）。
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

#: 抽词提示词的文件名（版本号 ``ex-v1`` 从文件名来）。
EXTRACT_PROMPT_NAME = "extract_v2.md"

#: 审卡提示词的文件名。
JUDGE_PROMPT_NAME = "judge_v1.md"


def read_prompt(name: str) -> str:
    """读包内一份提示词的**正文**（UTF-8 文本）。

    用 :mod:`importlib.resources`，装成 zip 也能读。
    """
    resource = resources.files(__package__).joinpath(name)
    return resource.read_text(encoding="utf-8")


def prompt_path(name: str) -> Path:
    """包内一份提示词在本机文件系统上的路径。

    正常安装（pip / uv tool / editable）都落在磁盘上，直接给路径；
    装成 zip 的话没有真路径，抛 :class:`FileNotFoundError`——
    这时候用 :func:`read_prompt` 读正文。
    """
    resource = resources.files(__package__).joinpath(name)
    path = Path(str(resource))
    if not path.is_file():
        raise FileNotFoundError(
            f"包内的提示词 {name!r} 没有落在文件系统上（装成了 zip？）：{resource}"
        )
    return path
