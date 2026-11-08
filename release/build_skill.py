#!/usr/bin/env python3
"""组装**发布用**的 skill 目录（``release/skill/``）。

真源只有一份：``skills/bookcard/``。这个脚本把它原样拷到发布目录，
外加两份手写文件（``INSTALL.md`` / ``README.md``，不覆盖）。

    python3 release/build_skill.py          # 生成
    python3 release/build_skill.py --check  # 只校验发布目录是不是最新的（CI 用）
"""

from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "skills" / "bookcard"
DST = ROOT / "release" / "skill"

#: 手写、不覆盖的文件。
KEEP = {"INSTALL.md", "README.md"}


def _copy() -> None:
    DST.mkdir(parents=True, exist_ok=True)
    for item in SRC.rglob("*"):
        rel = item.relative_to(SRC)
        if rel.parts and rel.parts[0] in KEEP:
            continue
        target = DST / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
    print(f"发布目录已更新：{DST.relative_to(ROOT)}")


def _check() -> int:
    """发布目录和真源不一致就报错（防止改了 skill 忘了同步）。"""
    bad: list[str] = []
    for item in SRC.rglob("*"):
        rel = item.relative_to(SRC)
        if rel.parts and rel.parts[0] in KEEP:
            continue
        target = DST / rel
        if item.is_dir():
            if not target.is_dir():
                bad.append(f"缺目录 {rel}")
        elif not target.exists() or not filecmp.cmp(item, target, shallow=False):
            bad.append(f"内容不一致 {rel}")
    for item in DST.rglob("*"):
        rel = item.relative_to(DST)
        if item.is_file() and not (SRC / rel).exists() and rel.name not in KEEP:
            bad.append(f"发布目录里多出来的 {rel}")
    if bad:
        print("发布目录不是最新的：")
        for line in bad:
            print("  -", line)
        print("跑 python3 release/build_skill.py 重新生成")
        return 1
    print("发布目录是最新的")
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(_check())
    _copy()
