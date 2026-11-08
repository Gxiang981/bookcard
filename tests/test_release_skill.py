"""发布目录（``release/skill/``）必须和真源（``skills/bookcard/``）一致。

发布目录是**生成物** ✓ —— 改了 skill 忘了重新生成，GitHub 上发出去的就是旧的 ✓。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "release" / "build_skill.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BUILD), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def test_发布目录是最新的():
    out = _run("--check")
    assert out.returncode == 0, out.stdout + out.stderr


def test_发布目录里有安装说明和介绍():
    skill = ROOT / "release" / "skill"
    assert (skill / "SKILL.md").is_file()
    assert (skill / "INSTALL.md").is_file()
    assert (skill / "README.md").is_file()
    assert (skill / "harness" / "dsh" / "bookcard.patch.yml").is_file()
