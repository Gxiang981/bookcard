"""``bookcard select|locate|audit|pack|arxiv`` 的命令行接线。

逻辑放在 :mod:`bookcard.pipeline` 的各个模块里，这里只负责**注册子命令**
和**打中文摘要**——``src/bookcard/cli.py`` 只多两行调用。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import ModuleType

from bookcard.pipeline.audit import audit
from bookcard.pipeline.locate import locate
from bookcard.pipeline.pack import (
    EDITION_PRIVATE,
    EDITIONS,
    SCENE_CHOSEN_BY_CLI,
    pack,
)
from bookcard.pipeline.paper_sections import build_paper_toc, detect_paper_sections
from bookcard.pipeline.scene import SCENE_IMAGE, SCENE_TEXT
from bookcard.pipeline.select import select
from bookcard.pipeline.workdir import PipelineError, WorkDir, read_json, write_json

#: 命令名 → 一句话说明（``bookcard --help`` 里看）。
COMMANDS: tuple[tuple[str, str], ...] = (
    ("select", "挑卡去重：candidates.jsonl → cards.jsonl（契约 §5）"),
    ("locate", "定位：渲染页面图 + 填 bbox 像素坐标（契约 §5）"),
    ("audit", "质检：出 quality_report.json（契约 §6）"),
    (
        "pack",
        "打包：出卡包 <fp12>.ttfc.zip（私有）/ <fp12>.public.ttfc.zip（公开，契约 §7.1）",
    ),
    ("arxiv", "论文源：抓 arXiv 论文的元数据与许可（）"),
    ("paper", "论文源：从正文认章节，写 toc.json + sections.json（）"),
)

#: 流水线里可预期的失败（闸门没过、缺产物……）的退出码。
EXIT_FAILED = 1


def register(subparsers: argparse._SubParsersAction) -> None:
    """把子命令注册到 ``bookcard`` 的子命令表里。"""
    for name, help_text in COMMANDS:
        parser = subparsers.add_parser(name, help=help_text)
        if name == "arxiv":
            # 论文源（）：``bookcard arxiv fetch <arxiv_id>``。
            _register_arxiv(parser)
            continue
        if name == "paper":
            # 论文源（）：``bookcard paper sections <dir>``。
            _register_paper(parser)
            continue
        parser.add_argument("dir", metavar="DIR", help="书的工作目录（契约 §0）")
        if name == "locate":
            parser.add_argument(
                "--pdf",
                metavar="PATH",
                default=None,
                help="书 PDF 路径（默认找 <dir>/book.pdf 或 ~/.cache/bookcard/books/<fp12>.pdf）",
            )
        if name == "pack":
            parser.add_argument(
                "--edition",
                choices=EDITIONS,
                default=EDITION_PRIVATE,
                help="卡包档位：private=内部分享版（默认），public=公开版（契约 §7.1）",
            )
            parser.add_argument(
                "--scene",
                choices=(SCENE_TEXT, SCENE_IMAGE),
                default=None,
                help="覆盖自动判定的场景（text=文本块，image=书页图；契约 §7.1）",
            )
            parser.add_argument(
                "--pdf",
                metavar="PATH",
                default=None,
                help="书 PDF 路径（判图片覆盖率要用；默认找 <dir>/book.pdf 或本机缓存）",
            )


def _register_arxiv(parser: argparse.ArgumentParser) -> None:
    """挂 ``arxiv`` 的子命令（目前只有 ``fetch``）。"""
    sub = parser.add_subparsers(dest="arxiv_command", metavar="SUBCOMMAND")
    fetch = sub.add_parser("fetch", help="抓一篇论文的元数据与许可")
    fetch.add_argument(
        "arxiv_id",
        metavar="ARXIV_ID",
        help="arXiv 编号（如 1706.03762、1706.03762v7、hep-th/9108001）",
    )
    fetch.add_argument(
        "--out",
        metavar="DIR",
        default=None,
        help="论文的工作目录：把论文字段合并写进 DIR/book.json（目录必须已存在）",
    )
    fetch.add_argument(
        "--pdf",
        metavar="PATH",
        default=None,
        help="本地 PDF 路径：用它算指纹（pages + pdf_sha256）",
    )
    fetch.add_argument(
        "--download",
        action="store_true",
        help="下载 PDF 到 <--out 目录>/book.pdf，再算指纹",
    )
    fetch.add_argument("--json", action="store_true", help="stdout 输出 JSON（给 agent 用）")


def _register_paper(parser: argparse.ArgumentParser) -> None:
    """挂 ``paper`` 的子命令（论文源：目前只有 ``sections``）。"""
    sub = parser.add_subparsers(dest="paper_command", metavar="SUBCOMMAND")
    sections = sub.add_parser(
        "sections",
        help="从正文认章节：写 toc.json + sections.json（闸门仍是 pending）",
    )
    sections.add_argument("dir", metavar="DIR", help="论文的工作目录（含 pages.jsonl）")


def _format_bytes(size: int) -> str:
    """字节数转成人能读的大小。"""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GiB"  # pragma: no cover - 上面一定会返回


def _percent(value: float) -> str:
    """小数 → 百分比字符串。"""
    return f"{value * 100:.1f}%"


def _run_select(work: WorkDir) -> int:
    summary = select(work)
    print(
        f"挑卡：{summary['cards']} 张卡，{summary['sections']} 个 section"
        f"（去重合并 {summary['duplicates_merged']}，"
        f"丢低价值 {summary['dropped_low_value']}，"
        f"没解释 {summary['dropped_no_explanation']}）"
    )
    print(f"cards.jsonl：{summary['cards_path']}")
    return 0


def _run_locate(work: WorkDir, pdf: str | None) -> int:
    summary = locate(work, pdf)
    print(
        f"定位：{summary['cards']} 张卡，定位成功 {summary['located']}"
        f"（{_percent(summary['locate_rate'])}），渲染 {summary['rendered_pages']} 页"
    )
    print(f"images：{summary['images_dir']}")
    return 0


def _run_audit(work: WorkDir) -> int:
    report = audit(work)
    print(
        f"质检：{report['cards']} 张卡，"
        f"定位率 {_percent(report['locate_rate'])}，"
        f"原文率 {_percent(report['verbatim_rate'])}"
    )
    print(
        f"闸门：toc={report['gates']['toc']}，trial={report['gates']['trial']}"
    )
    print(f"quality_report.json：{work.quality_path}")
    return 0


def _run_pack(work: WorkDir, edition: str, scene: str | None, pdf: str | None) -> int:
    # 显式传了 --scene 就是命令行定的（契约 §7.1：记 "cli"）；没传走默认场景
    # （记 "default"）。
    summary = pack(
        work,
        edition=edition,
        scene=scene,
        pdf=pdf,
        scene_chosen_by=SCENE_CHOSEN_BY_CLI if scene else None,
    )
    print(
        f"打包（{summary['edition']}）：{summary['cards']} 张卡，"
        f"{summary['libraries']} 个词库，{summary['images']} 张页面图"
    )
    print(f"场景：{summary['scene_mode']}（判定 {summary['scene_detected']}）")
    for reason in summary["scene_reasons"]:
        print(f"  · {reason}")
    if summary["scene_override"]:
        print(f"  · 用户用 --scene {summary['scene_override']} 覆盖")
    print(f"  · 场景由 {summary['scene_chosen_by']} 定")
    contexts = summary.get("contexts") or {}
    if contexts:
        print(
            f"文本块：{contexts.get('with_context', 0)} 张卡有 context，"
            f"{contexts.get('without_context', 0)} 张没有"
        )
    print(f"包：{summary['pack']}（{_format_bytes(summary['bytes'])}）")
    return 0


def _run_arxiv(args: argparse.Namespace) -> int:
    """``bookcard arxiv fetch``（）。

    许可模块（的 ``license_gate``）还没合进来时，这里给中文提示，
    不让整个 CLI 挂掉。
    """
    try:
        from bookcard.pipeline import arxiv as arxiv_mod
    except ImportError as exc:  # pragma: no cover - 取决于 合入进度
        print(f"arxiv 失败：缺少许可模块（{exc}）")
        return EXIT_FAILED
    try:
        return _run_arxiv_fetch(args, arxiv_mod)
    except PipelineError as exc:
        print(f"arxiv 失败：{exc}")
        return EXIT_FAILED


def _run_arxiv_fetch(args: argparse.Namespace, arxiv_mod: ModuleType) -> int:
    """抓一篇论文的元数据与许可（``arxiv fetch``）。"""
    if getattr(args, "arxiv_command", None) != "fetch":
        print(
            "用法：bookcard arxiv fetch <arxiv_id> "
            "[--out DIR] [--pdf PATH] [--download] [--json]"
        )
        return 2

    arxiv_id = arxiv_mod.validate_arxiv_id(args.arxiv_id)

    out_dir = Path(args.out) if args.out else None
    if out_dir is not None and not out_dir.is_dir():
        raise arxiv_mod.ArxivError(
            f"工作目录不存在：{out_dir}（本命令不建工作目录，先自己建好）"
        )

    meta = arxiv_mod.fetch_arxiv_meta(arxiv_id)

    pdf_path: Path | None = None
    if args.download:
        if out_dir is None:
            raise arxiv_mod.ArxivError("--download 要配 --out（PDF 下到 <目录>/book.pdf）")
        pdf_path = arxiv_mod.download_pdf(meta["arxiv_id"], out_dir / "book.pdf")
    elif args.pdf:
        pdf_path = Path(args.pdf)
        if not pdf_path.is_file():
            raise arxiv_mod.ArxivError(f"PDF 不存在：{pdf_path}")

    existing: dict = {}
    if out_dir is not None:
        book_path = out_dir / "book.json"
        if book_path.is_file():
            loaded = read_json(book_path)
            if isinstance(loaded, dict):
                existing = loaded

    book = arxiv_mod.build_paper_book(meta, pdf_path=pdf_path, existing=existing)

    if out_dir is not None:
        write_json(out_dir / "book.json", book)

    if args.json:
        print(json.dumps(book, ensure_ascii=False, indent=2))
    else:
        print(_format_arxiv_summary(book, out_dir))
    return 0


def _format_arxiv_summary(book: dict, out_dir: Path | None) -> str:
    """论文元数据的中文摘要。"""
    lines = [f"论文：{book.get('title') or '（没解析出标题）'}"]
    authors = book.get("authors") or []
    lines.append(f"作者：{', '.join(authors) if authors else '（没解析出作者）'}")
    arxiv_id = book.get("arxiv_id") or ""
    version = book.get("arxiv_version")
    lines.append(
        f"编号：arXiv:{arxiv_id}"
        f"（基号 {book.get('arxiv_base_id') or '-'}，版本 {version if version else '-'}）"
    )
    published = book.get("arxiv_published") or "-"
    updated = book.get("arxiv_updated") or "-"
    lines.append(f"日期：提交 {published}，更新 {updated}")
    kind = book.get("paper_license") or "unknown"
    license_uri = book.get("paper_license_uri") or ""
    lines.append(f"许可：{kind}" + (f"（{license_uri}）" if license_uri else ""))
    lines.append(f"出处：{book.get('attribution') or '-'}")
    fingerprint = book.get("fingerprint")
    if fingerprint:
        lines.append(
            f"指纹：{fingerprint.get('pages')} 页，pdf_sha256 {fingerprint.get('pdf_sha256')}"
        )
    if out_dir is not None:
        lines.append(f"book.json：{out_dir / 'book.json'}（fp12 {book.get('fp12') or '-'}）")
    return "\n".join(lines)


def _run_paper(args: argparse.Namespace) -> int:
    """``bookcard paper sections``（）。"""
    if getattr(args, "paper_command", None) != "sections":
        print("用法：bookcard paper sections <dir>")
        return 2
    try:
        return _run_paper_sections(args)
    except PipelineError as exc:
        print(f"paper 失败：{exc}")
        return EXIT_FAILED


def _run_paper_sections(args: argparse.Namespace) -> int:
    """从论文正文认章节，写 ``toc.json`` + ``sections.json``。

    闸门只到 ``pending``：目录还得有人拍板（批准走既有的 ``toc_gate``）。
    """
    work = WorkDir(args.dir)
    if not work.root.is_dir():
        raise PipelineError(f"工作目录不存在：{work.root}")
    if not work.pages_path.is_file():
        raise PipelineError(f"缺 pages.jsonl：{work.pages_path}")

    pages = work.pages()
    sections = detect_paper_sections(pages)
    toc = build_paper_toc(sections, pages)
    write_json(work.toc_path, toc)
    write_json(work.sections_path, sections)

    print(_format_paper_summary(work, sections, toc))
    if not sections:
        print("没认出章节标题行——论文的目录结构只能从正文里认，先看看 pages.jsonl 的文字")
        return EXIT_FAILED
    return 0


def _format_paper_summary(work: WorkDir, sections: list[dict], toc: dict) -> str:
    """论文章节的中文摘要。"""
    lines = [f"论文章节：认出 {len(sections)} 节"]
    for row in sections:
        subheadings = row.get("subheadings") or []
        extra = f"；子标题：{'、'.join(subheadings)}" if subheadings else ""
        lines.append(
            f"  {row['sid']}  {row['title']}（L{row['level']}，"
            f"{row['page_start']}–{row['page_end']} 页{extra}）"
        )
    gate_result = toc.get("gate") or {}
    lines.append(
        f"目录：{work.toc_path}（gate={gate_result.get('status')}，"
        "等人拍板；批准走 toc_gate）"
    )
    lines.append(f"章节：{work.sections_path}")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    """跑一个流水线子命令，返回退出码。"""
    if args.command == "arxiv":
        return _run_arxiv(args)
    if args.command == "paper":
        return _run_paper(args)
    work = WorkDir(args.dir)
    try:
        if args.command == "select":
            return _run_select(work)
        if args.command == "locate":
            return _run_locate(work, getattr(args, "pdf", None))
        if args.command == "audit":
            return _run_audit(work)
        if args.command == "pack":
            return _run_pack(
                work,
                getattr(args, "edition", EDITION_PRIVATE),
                getattr(args, "scene", None),
                getattr(args, "pdf", None),
            )
    except PipelineError as exc:
        print(f"{args.command} 失败：{exc}")
        return EXIT_FAILED
    print(f"未知命令：{args.command}")  # pragma: no cover - argparse 已经拦过
    return 2
