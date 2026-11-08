"""卡包 → 分享包。

一条流水线，三样产物：

1. ``images/*.webp``  —— 全书页图，按 App 的原生格式存（webp q95，幂等直通）
2. ``manifest.json``  —— App 的导入格式（图片路径已改写成 webp）
3. ``index.html``     —— 自包含的查看/编辑页

再打成 zip：解压能看能改，拖进 App 能导入。
"""

from __future__ import annotations

import copy
import json
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .html import render_html
from .pack import open_pack
from .pages import render_book
from .payload import build_payload
from .toc import load_toc, toc_from_libraries
from .webp import QUALITY, to_webp_bytes, webp_name

#: 包内目录名。
IMAGES_DIR = "images"

#: 缩略图目录名（底部页条用）。
THUMBS_DIR = "thumbs"


@dataclass
class ShareResult:
    """一次构建的产物与统计。"""

    out_dir: Path
    manifest: dict
    payload: dict
    html_bytes: int
    images: list[tuple[str, str]] = field(default_factory=list)
    source_bytes: int = 0
    image_bytes: int = 0
    zip_path: Path | None = None
    zip_bytes: int = 0

    @property
    def summary(self) -> dict[str, Any]:
        toc = self.payload.get("toc") or {}
        return {
            "out_dir": str(self.out_dir),
            "book": self.payload.get("book", {}).get("title"),
            "cards": len(self.payload.get("cards", [])),
            "libraries": len(self.payload.get("libraries", [])),
            "images": len(self.images),
            "pages": len(self.payload.get("pages") or []),
            "toc_entries": len(toc.get("entries") or []),
            "toc_offset": toc.get("offset"),
            "source_bytes": self.source_bytes,
            "image_bytes": self.image_bytes,
            "html_bytes": self.html_bytes,
            "zip": str(self.zip_path) if self.zip_path else None,
            "zip_bytes": self.zip_bytes,
        }


def rewrite_manifest_images(manifest: dict, image_map: dict[str, str]) -> dict:
    """把 manifest 里所有图片路径改写成 webp。

    改两处：``card.images[].file``（App 真正读的那处）和 ``card.source.image``
    （BookCard 自己的元数据，顺手改干净）。
    """
    out = copy.deepcopy(manifest)
    for library in (out.get("category") or {}).get("libraries") or []:
        for card in library.get("cards") or []:
            for image in card.get("images") or []:
                if not isinstance(image, dict):
                    continue
                old = str(image.get("file") or "")
                if old in image_map:
                    image["file"] = image_map[old]
            source = card.get("source")
            if isinstance(source, dict):
                old = str(source.get("image") or "")
                if old in image_map:
                    source["image"] = image_map[old]
    return out


def build_share(
    pack_path: str | Path,
    out_dir: str | Path,
    *,
    quality: int = QUALITY,
    make_zip: bool = True,
    zip_path: str | Path | None = None,
    manifest_override: dict | None = None,
    pdf: str | Path | None = None,
    work_dir: str | Path | None = None,
) -> ShareResult:
    """把一个卡包变成分享包（目录 + 可选 zip）。

    参数
    ----
    pack_path:
        ``.ttfc.zip`` 或解开的包目录。
    out_dir:
        分享包目录（会被清空重建）。
    quality:
        webp 质量，默认 95（对齐 App 的 ``IMPORT_QUALITY``）。
    make_zip:
        是否额外打一个 zip（App 只吃 zip）。
    zip_path:
        zip 输出路径，默认 ``<out_dir>.zip``。
    manifest_override:
        用它当 manifest（图片仍从包里取）。``apply`` 用这个把改动合进去。
    pdf:
        书的 PDF。给了就把**全书每一页**都渲染出来（大图 + 缩略图），
        底部页条才有全书可翻。实测重渲染出来的字节与包里已有的图一致。
    work_dir:
        引擎的书工作目录（``$BOOKCARD_HOME/books/<fp12>/``）。给了才会带目录
        （``toc.json``）和印刷页 offset。
    """
    pack_path = Path(pack_path)
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    with open_pack(pack_path) as pack:
        manifest = manifest_override if manifest_override is not None else pack.manifest
        source_bytes = 0
        image_bytes = 0
        images: list[tuple[str, str]] = []
        image_map: dict[str, str] = {}
        pages: list[dict] = []

        # ---- 1. 图片 ----
        target_dir = out / IMAGES_DIR
        thumb_dir = out / THUMBS_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        thumb_dir.mkdir(parents=True, exist_ok=True)

        if pdf:
            # 有 PDF：渲染全书每一页（大图 + 缩略图）。
            # 与包里已有的图逐字节一致 ⇒ App 侧算出的 sha 不变。
            rendered = render_book(pdf)
            for number, rp in sorted(rendered.items()):
                base = f"p{number:04d}"
                (target_dir / f"{base}.webp").write_bytes(rp.full)
                (thumb_dir / f"{base}.webp").write_bytes(rp.thumb)
                image_bytes += len(rp.full) + len(rp.thumb)
                pages.append(
                    {
                        "page": number,
                        "thumb": f"{THUMBS_DIR}/{base}.webp",
                        "image": f"{IMAGES_DIR}/{base}.webp",
                        "width": rp.width,
                        "height": rp.height,
                    }
                )
            # 包里引用的图片路径 → webp（文件名不变，只换后缀）
            for name in pack.image_names():
                base = Path(name).stem
                image_map[name] = f"{IMAGES_DIR}/{base}.webp"
                images.append((name, image_map[name]))
        else:
            # 没 PDF：只有包里那几页
            for name in pack.image_names():
                data = pack.read(name)
                if data is None:
                    continue
                new_name = webp_name(name)
                new_data = to_webp_bytes(data, quality=quality)
                target = target_dir / Path(new_name).name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(new_data)
                images.append((name, f"{IMAGES_DIR}/{Path(new_name).name}"))
                image_map[name] = f"{IMAGES_DIR}/{Path(new_name).name}"
                source_bytes += len(data)
                image_bytes += len(new_data)

        # ---- 2. manifest：图片路径改写成 webp + 带上目录 ----
        new_manifest = rewrite_manifest_images(manifest, image_map)
        # 目录的优先级：
        #   1) manifest 里已经有的（apply 合回来的用户改稿）—— 最高，
        #      不能被工作目录里的原始 toc.json 盖掉；
        #   2) 工作目录的 toc.json（原始抽取结果）；
        #   3) 拿词库当目录兜底（没有页码，但至少有条目可编辑）。
        existing = new_manifest.get("toc")
        toc = existing if isinstance(existing, dict) and existing.get("entries") else None
        if toc is None and work_dir:
            toc = load_toc(work_dir)
        if toc is None:
            toc = toc_from_libraries(
                [
                    {"name": lib.get("name")}
                    for lib in (new_manifest.get("category") or {}).get("libraries") or []
                ]
            )
        if toc:
            # 目录跟着包走：App 不认这个字段（多余字段直接丢），
            # 但分享包自己带着，apply 能把它合回去。
            new_manifest["toc"] = {
                "source": toc.get("source") or "",
                "offset": toc.get("offset"),
                "entries": toc.get("entries") or [],
            }

        # ---- 3. 静态页：内嵌数据 ----
        payload = build_payload(new_manifest, image_map, toc=toc, pages=pages)
        html = render_html(payload)
        (out / "index.html").write_text(html, encoding="utf-8")

        # ---- 4. manifest.json（App 的导入格式） ----
        (out / "manifest.json").write_text(
            json.dumps(new_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    result = ShareResult(
        out_dir=out,
        manifest=new_manifest,
        payload=payload,
        html_bytes=len(html.encode("utf-8")),
        images=images,
        source_bytes=source_bytes,
        image_bytes=image_bytes,
    )

    if make_zip:
        result.zip_path = _make_zip(out, Path(zip_path) if zip_path else out.with_suffix(".zip"))
        result.zip_bytes = result.zip_path.stat().st_size
    return result


def _make_zip(src_dir: Path, zip_path: Path) -> Path:
    """把分享包目录打成 zip。

    图片用 ``ZIP_STORED``（webp 已经是压缩格式，再 deflate 一遍是白费 CPU），
    manifest / html 用 ``ZIP_DEFLATED``。
    """
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w") as zf:
        for path in sorted(src_dir.rglob("*")):
            if not path.is_file():
                continue
            arcname = path.relative_to(src_dir).as_posix()
            compress = (
                zipfile.ZIP_STORED
                if arcname.startswith((f"{IMAGES_DIR}/", f"{THUMBS_DIR}/"))
                else zipfile.ZIP_DEFLATED
            )
            zf.write(path, arcname, compress_type=compress)
    return zip_path


def apply_and_build(
    pack_path: str | Path,
    edits_path: str | Path,
    out_dir: str | Path,
    *,
    quality: int = QUALITY,
    make_zip: bool = True,
    zip_path: str | Path | None = None,
    pdf: str | Path | None = None,
    work_dir: str | Path | None = None,
) -> tuple[ShareResult, dict]:
    """把静态页导出的 ``edits.json`` 合回卡包，再重新生成分享包。

    返回 ``(构建结果, 合并统计)``。
    """
    from .apply import apply_edits

    edits = json.loads(Path(edits_path).read_text(encoding="utf-8"))
    with open_pack(pack_path) as pack:
        merged, stats = apply_edits(pack.manifest, edits)
    result = build_share(
        pack_path,
        out_dir,
        quality=quality,
        make_zip=make_zip,
        zip_path=zip_path,
        manifest_override=merged,
        pdf=pdf,
        work_dir=work_dir,
    )
    return result, stats


def build_book(
    pdf: str | Path,
    toc_source: str | Path | None,
    out_dir: str | Path,
    *,
    title: str | None = None,
    quality: int = QUALITY,
    make_zip: bool = True,
    zip_path: str | Path | None = None,
) -> ShareResult:
    """只做「读原书」：一本书 → 一个 index.html（没有卡片、没有词库）。

    输入只要两样：

    - ``pdf`` —— 原书；
    - ``toc_source`` —— 目录。可以是「目录看图 v1」的工作目录（有 ``toc.json``
      + ``tocv.json``，印刷页号能算出来），也可以直接给一个 ``toc.json``。

    产出的页面只有大图 + 目录 + 缩略图条，左栏和卡片流程都不出现。
    """
    from .toc import load_toc_vision

    pdf = Path(pdf)
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    (out / IMAGES_DIR).mkdir(parents=True, exist_ok=True)
    (out / THUMBS_DIR).mkdir(parents=True, exist_ok=True)

    rendered = render_book(pdf)
    pages: list[dict] = []
    image_bytes = 0
    for number, rp in sorted(rendered.items()):
        base = f"p{number:04d}"
        (out / IMAGES_DIR / f"{base}.webp").write_bytes(rp.full)
        (out / THUMBS_DIR / f"{base}.webp").write_bytes(rp.thumb)
        image_bytes += len(rp.full) + len(rp.thumb)
        pages.append(
            {
                "page": number,
                "thumb": f"{THUMBS_DIR}/{base}.webp",
                "image": f"{IMAGES_DIR}/{base}.webp",
                "width": rp.width,
                "height": rp.height,
            }
        )

    toc = load_toc_vision(toc_source) if toc_source else None
    book_id = pdf.stem[:12] if pdf.stem else "book"
    book_title = title or pdf.stem
    manifest = {
        "format": "ttfc/2",
        "visibility": "private",
        "scene_mode": "image",
        "book": {"fp12": book_id, "title": book_title, "page_count": len(pages)},
        "category": {"name": book_title, "description": "", "sort_order": 0, "libraries": []},
    }
    if toc:
        manifest["toc"] = {
            "source": toc.get("source") or "",
            "offset": toc.get("offset"),
            "entries": toc.get("entries") or [],
        }

    payload = {
        "book_only": True,
        "book": {
            "id": book_id,
            "title": book_title,
            "subtitle": "",
            "visibility": "private",
            "scene_mode": "image",
            "fp12": book_id,
            "page_count": len(pages),
        },
        "libraries": [],
        "cards": [],
        "toc": toc,
        "pages": pages,
    }

    html = render_html(payload)
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = ShareResult(
        out_dir=out,
        manifest=manifest,
        payload=payload,
        html_bytes=len(html.encode("utf-8")),
        image_bytes=image_bytes,
    )
    if make_zip:
        result.zip_path = _make_zip(out, Path(zip_path) if zip_path else out.with_suffix(".zip"))
        result.zip_bytes = result.zip_path.stat().st_size
    return result
