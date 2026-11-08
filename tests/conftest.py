"""测试的全局装置。

**产物不许落到用户的「下载」文件夹** —— `bookcard.paths.output_dir()` 默认是
系统下载目录，测试跑一遍就会把一堆测试包倒进用户的下载夹里。测试里一律
用 `BOOKCARD_OUTPUT_DIR` 指到临时目录。
"""

from __future__ import annotations

import pytest

from bookcard.paths import OUTPUT_DIR_ENV


@pytest.fixture(autouse=True)
def _产物输出到临时目录(tmp_path_factory, monkeypatch):
    monkeypatch.setenv(OUTPUT_DIR_ENV, str(tmp_path_factory.mktemp("out")))
