#!/usr/bin/env python3
"""bookcard-share —— 把卡包变成「能看能改、也能导入 App」的分享包。

用法
----
    # 建分享包（目录 + zip）
    python3 build.py build <pack.ttfc.zip> [-o OUT_DIR]

    # 把静态页导出的改动合回卡包，重建分享包
    python3 build.py apply --edits edits.json --pack <pack.ttfc.zip> [-o OUT_DIR]

例
----
    python3 build.py build \\
        ../train/data/bookcard/books/94a9b8d6b27d/pack/94a9b8d6b27d.ttfc.zip \\
        -o out/94a9b8d6b27d-share

产出
----
    out/94a9b8d6b27d-share/
        manifest.json      App 的导入格式
        index.html         自包含的查看/编辑页（双击就开）
        images/*.webp      全书页图
    out/94a9b8d6b27d-share.zip   同一个包，拖进 App 就能导入
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sharepack.build import apply_and_build, build_book, build_share  # noqa: E402
from sharepack.pack import open_pack  # noqa: E402
from sharepack.webp import QUALITY  # noqa: E402


def _print_summary(summary: dict, extra: dict | None = None) -> None:
    print(f"书名    : {summary['book']}")
    print(f"词库    : {summary['libraries']} 个")
    print(f"卡片    : {summary['cards']} 张")
    print(f"页面图  : {summary['pages']} 页（大图+缩略图）")
    print(f"目录    : {summary['toc_entries']} 条")
    print(f"图片体积: {summary['source_bytes'] / 1048576:.1f} MB → "
          f"{summary['image_bytes'] / 1048576:.1f} MB")
    print(f"静态页  : {summary['html_bytes'] / 1024:.0f} KB（自包含，双击即开）")
    print(f"输出目录: {summary['out_dir']}")
    if summary["zip"]:
        print(f"分享包  : {summary['zip']}（{summary['zip_bytes'] / 1048576:.1f} MB）")
    if extra:
        print(f"改动    : 合并 {extra.get('applied', 0)} 处"
              f"（移动 {extra.get('moved', 0)}，跳过 {extra.get('skipped', 0)}）")
        if extra.get("toc"):
            print(f"目录    : 写入 {extra['toc']} 条")


def _add_common(p: argparse.ArgumentParser, *, with_pdf: bool = True) -> None:
    p.add_argument(
        "-o", "--out", default=None, help="输出目录（默认 out/<书名>-share）"
    )
    p.add_argument("--zip", default=None, help="zip 输出路径（默认 <输出目录>.zip）")
    p.add_argument("--no-zip", action="store_true", help="不打 zip，只出目录")
    p.add_argument(
        "--quality", type=int, default=QUALITY,
        help=f"webp 质量（默认 {QUALITY}，对齐 App 导入档）",
    )
    p.add_argument("--json", action="store_true", help="只打印 JSON 摘要")
    if with_pdf:
        p.add_argument(
            "--pdf", default=None,
            help="书的 PDF。给了就把全书每一页都渲染出来（大图+缩略图），底部页条才有全书可翻",
        )
    p.add_argument(
        "--work-dir", default=None,
        help="引擎的书工作目录（$BOOKCARD_HOME/books/<fp12>/）。"
        "给了才带目录（toc.json）和印刷页 offset",
    )


def _guess_work_dir(pack: Path) -> Path | None:
    """从卡包路径反推书工作目录：``<work>/pack/<name>.ttfc.zip`` → ``<work>``。"""
    parent = pack.parent
    if parent.name == "pack":
        return parent.parent
    return None


def _guess_pdf(work_dir: Path | None, manifest: dict) -> Path | None:
    """在常见位置找 PDF（引擎把原书放在 ``train/data/src/<file_name>.pdf``）。"""
    from sharepack.pages import find_pdf

    name = str((manifest.get("book") or {}).get("file_name") or "")
    return find_pdf(work_dir, name)


def _peek_manifest(pack: Path) -> dict:
    """只读卡包的 manifest（用来找 file_name 猜 PDF 位置）。"""
    try:
        with open_pack(pack) as p:
            return p.manifest
    except Exception:  # noqa: BLE001 - 猜不到就当没有
        return {}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 兼容「直接给卡包路径」的写法：默认当 build。
    if argv and argv[0] not in ("build", "book", "apply", "-h", "--help"):
        argv.insert(0, "build")

    parser = argparse.ArgumentParser(
        prog="bookcard-share",
        description="把卡包变成分享包：manifest.json + images/*.webp + index.html",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="卡包 → 分享包")
    p_build.add_argument("pack", help="卡包（.ttfc.zip）或解开的包目录")
    _add_common(p_build)

    p_book = sub.add_parser(
        "book", help="只做「读原书」：PDF + 目录 → 一个 index.html（没有卡片）"
    )
    p_book.add_argument("--pdf", required=True, help="原书 PDF")
    p_book.add_argument(
        "--toc", default=None,
        help="目录来源：目录看图 v1 的工作目录（有 toc.json + tocv.json），"
        "或直接给一个 toc.json",
    )
    p_book.add_argument("--title", default=None, help="书名（默认取 PDF 文件名）")
    _add_common(p_book, with_pdf=False)

    p_apply = sub.add_parser("apply", help="把 edits.json 合回卡包并重建分享包")
    p_apply.add_argument("--edits", required=True, help="静态页导出的 edits.json")
    p_apply.add_argument("--pack", required=True, help="原始卡包（.ttfc.zip）")
    _add_common(p_apply)

    args = parser.parse_args(argv)

    if args.cmd == "book":
        pdf = Path(args.pdf)
        if not pdf.exists():
            print(f"PDF 不存在：{pdf}", file=sys.stderr)
            return 2
        out = Path(args.out) if args.out else Path("out") / (pdf.stem[:12] + "-book")
        try:
            result = build_book(
                pdf, args.toc, out,
                title=args.title,
                quality=args.quality,
                make_zip=not args.no_zip,
                zip_path=Path(args.zip) if args.zip else None,
            )
        except Exception as exc:  # noqa: BLE001 - CLI 边界
            print(f"构建失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        summary = result.summary
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        _print_summary(summary)
        return 0

    if args.cmd == "apply":
        pack = Path(args.pack)
        edits = Path(args.edits)
        if not pack.exists():
            print(f"卡包不存在：{pack}", file=sys.stderr)
            return 2
        if not edits.exists():
            print(f"改动文件不存在：{edits}", file=sys.stderr)
            return 2
        out = Path(args.out) if args.out else Path("out") / (pack.stem.split(".")[0] + "-edited")
        work_dir = Path(args.work_dir) if args.work_dir else _guess_work_dir(pack)
        pdf = Path(args.pdf) if args.pdf else _guess_pdf(work_dir, _peek_manifest(pack))
        try:
            result, stats = apply_and_build(
                pack, edits, out,
                quality=args.quality,
                make_zip=not args.no_zip,
                zip_path=Path(args.zip) if args.zip else None,
                pdf=pdf,
                work_dir=work_dir,
            )
        except Exception as exc:  # noqa: BLE001 - CLI 边界
            print(f"合并失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        summary = result.summary
        if args.json:
            print(json.dumps({**summary, "edits": stats}, ensure_ascii=False, indent=2))
            return 0
        _print_summary(summary, stats)
        return 0

    pack = Path(args.pack)
    if not pack.exists():
        print(f"卡包不存在：{pack}", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else Path("out") / (pack.stem.split(".")[0] + "-share")
    work_dir = Path(args.work_dir) if args.work_dir else _guess_work_dir(pack)
    pdf = Path(args.pdf) if args.pdf else _guess_pdf(work_dir, _peek_manifest(pack))

    try:
        result = build_share(
            pack, out,
            quality=args.quality,
            make_zip=not args.no_zip,
            zip_path=Path(args.zip) if args.zip else None,
            pdf=pdf,
            work_dir=work_dir,
        )
    except Exception as exc:  # noqa: BLE001 - CLI 边界
        print(f"构建失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    summary = result.summary
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
