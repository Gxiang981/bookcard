"""读卡包（``.ttfc.zip`` 或解开的包目录）。

只读，不改包。跟 ``src/bookcard/browse/db.py`` 的 ``_open_pack`` 同一套语义：
zip 里根目录一个 ``manifest.json``，图片按相对路径引用。
"""

from __future__ import annotations

import contextlib
import json
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Pack:
    """一个打开着的卡包。"""

    manifest: dict
    names: frozenset[str]
    _read: Callable[[str], bytes | None]

    def read(self, name: str) -> bytes | None:
        """读包内的一个文件（不在包里返回 ``None``）。"""
        return self._read(name)

    def image_names(self) -> list[str]:
        """包里所有图片条目（按名字排序）。"""
        return sorted(n for n in self.names if n.startswith("images/"))


@contextlib.contextmanager
def open_pack(path: str | Path) -> Iterator[Pack]:
    """打开一个卡包，产出 :class:`Pack`。

    支持两种形态：``.ttfc.zip``（zipfile）与**解开的包目录**（含 ``manifest.json``）。
    """
    p = Path(path)
    if p.is_dir():
        manifest_path = p / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"包里没有 manifest.json：{p}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        names = frozenset(
            str(q.relative_to(p).as_posix())
            for q in p.rglob("*")
            if q.is_file()
        )

        def read_dir(name: str) -> bytes | None:
            target = (p / name).resolve()
            if not str(target).startswith(str(p.resolve())):
                return None
            if not target.is_file():
                return None
            return target.read_bytes()

        yield Pack(manifest=manifest, names=names, _read=read_dir)
        return

    if not p.is_file():
        raise FileNotFoundError(f"卡包不存在：{p}")

    with zipfile.ZipFile(p) as zf:
        names = frozenset(zf.namelist())
        if "manifest.json" not in names:
            raise ValueError(f"包里没有 manifest.json：{p}")
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))

        def read_zip(name: str) -> bytes | None:
            if name not in names:
                return None
            try:
                return zf.read(name)
            except KeyError:
                return None

        yield Pack(manifest=manifest, names=names, _read=read_zip)
