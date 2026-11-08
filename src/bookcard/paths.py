"""最终产物的落地位置：**默认系统「下载」文件夹**（mac / win 都是它）。

引擎的**工作目录**仍在 ``$BOOKCARD_HOME/books/<fp12>/``（那是状态，不是交付物）——
但用户真正要拿走的东西（卡包 zip、分享页 html）默认复制一份到「下载」：
用户找得到，也能直接拖进 App。

覆盖方式：环境变量 ``BOOKCARD_OUTPUT_DIR``（测试与特殊需求用）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: 覆盖「下载」文件夹的环境变量。
OUTPUT_DIR_ENV = "BOOKCARD_OUTPUT_DIR"

#: Windows 上文件名不能用的字符 + 控制字符。
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _is_windows() -> bool:
    """是不是 Windows（单独一个函数：测试要能替换它）。"""
    return os.name == "nt"


def downloads_dir() -> Path:
    """系统「下载」文件夹。

    - mac：``~/Downloads``。
    - win：``%USERPROFILE%\\Downloads``；但 Windows 的「下载」常被重定向到
      OneDrive ⇒ 依次试 ``OneDrive/Downloads``、``Downloads``，都不在就返回第一个。
    """
    home = Path.home()
    if not _is_windows():
        return home / "Downloads"
    profile = Path(os.environ.get("USERPROFILE") or home)
    for cand in (profile / "OneDrive" / "Downloads", profile / "Downloads"):
        if cand.is_dir():
            return cand
    return profile / "Downloads"


def output_dir() -> Path:
    """最终产物的落地目录（默认「下载」文件夹）。"""
    override = os.environ.get(OUTPUT_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return downloads_dir()


def safe_name(*parts: str) -> str:
    """把书名等拼成安全的文件名（去掉路径分隔符和 Windows 非法字符）。"""
    raw = "-".join(str(p) for p in parts if p)
    cleaned = _UNSAFE.sub("_", raw).strip(" .")
    return cleaned[:120] or "bookcard"
