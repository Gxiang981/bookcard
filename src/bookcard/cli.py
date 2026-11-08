"""bookcard 命令行入口。

目前的入口：

- ``bookcard --version``：打印版本号后退出（退出码 0）。
- ``bookcard probe <path> [<path> ...] [--out PATH] [--pages N] [--json]``：
  对一本书（或一批书）跑探针，stdout 打印中文摘要，``--out`` 写 JSON 报告。
- ``bookcard select|locate|audit|pack <dir>``：流水线四步（契约 §5–§7），
  逻辑在 ``bookcard.pipeline``，这里只做接线。
- ``bookcard arxiv fetch <arxiv_id> [--out DIR] [--pdf PATH] [--download] [--json]``：
  论文源（）——抓 arXiv 论文的元数据与许可，写成论文的工作目录。
- ``bookcard paper sections <dir>``：论文源（）——从论文正文认章节，
  写 ``toc.json`` + ``sections.json``（闸门只到 pending，等人拍板）。
- ``bookcard flow <子命令>``：agent 驱动的出题 / 交卷层
  （``docs/mcp-v1.md`` §2），逻辑在 ``bookcard.agentflow``，
  结果 JSON 打到 stdout，给没有 MCP 的 harness 调。
- ``bookcard tocv <子命令>``：目录看图 v1（``docs/tocv-v1.md``）——
  拼接图定位、目录页大图、草稿提交与确定性检查，逻辑在
  ``bookcard.engine.tocvision``。
- ``bookcard review-html <dir> [--sample N] [--seed S] --out <html>``：
  把一张书的工作目录（cards.jsonl / pages.jsonl / review/）做成自包含审核页，
  人工抽检后导出 §6 格式的审核记录。
- ``bookcard text <pdf> --out <dir> [--meta books.jsonl]``：
  抽一本书的全文，写 ``book.json`` + ``pages.jsonl``。
- ``bookcard toc <dir> [--pdf PDF] [--approved-by WHO]``：
  写 ``toc.json``（目录 + 闸门 1）。
- ``bookcard sections <dir>``：写 ``sections.json``（闸门 1 没过就硬拦）。
- ``bookcard extract <dir> [--sections s01,s05]``：
  抽词 + 解释抽取器（契约 §4），写 ``candidates.jsonl``。
- ``bookcard browse import <pack...> [--db PATH]`` /
  ``bookcard browse serve [--db PATH] [--port 8765] [--no-open]``：
  browse-lite 的本地库与轻量浏览页（契约 ``docs/browse-lite-v1.md``）。
- ``bookcard mcp [--home DIR]``：MCP 服务（stdio JSON-RPC，契约
  ``docs/mcp-v1.md`` §3），给 DSH / Claude Code / Codex / Devin 接。

退出码：0 全部正常；3 有坏文件（报告照样写出）；2 参数用错；1 流水线失败
（闸门没过、缺产物等）；4 闸门没通过
（text/toc/sections 的闸门 1、extract 的闸门 2）。
"""

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from bookcard import __version__, flow_cli, tocv_cli
from bookcard.browse.db import DEFAULT_DB_PATH
from bookcard.engine import ocr as ocr_mod
from bookcard.engine.report import summarize_sections, summarize_text, summarize_toc
from bookcard.engine.text import build_text
from bookcard.engine.toc import (
    GateNotApprovedError,
    MissingPdfError,
    build_sections_file,
    build_toc,
)
from bookcard.pipeline import cli as pipeline_cli
from bookcard.probe import broken_report, probe

#: 退出码：坏文件。
EXIT_BROKEN = 3

#: 退出码：闸门没通过，命令不许往下走。
EXIT_GATE = 4


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="bookcard",
        description="从书本提取学习卡片",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"bookcard {__version__}",
        help="打印版本号",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    probe_parser = subparsers.add_parser(
        "probe",
        help="探针：体检一本书（文本层 / 页码 / 目录来源 / 编号注释）",
    )
    probe_parser.add_argument("paths", metavar="PATH", nargs="+", help="书文件路径")
    probe_parser.add_argument(
        "--out",
        metavar="PATH",
        default=None,
        help="把 JSON 报告写到这里：目录则每本写一个 <文件名>.probe.json，否则是单个文件",
    )
    probe_parser.add_argument(
        "--pages",
        metavar="N",
        type=int,
        default=None,
        help="只检查前 N 页（调试用），默认全书",
    )
    probe_parser.add_argument(
        "--json",
        action="store_true",
        help="stdout 输出 JSON（多本书输出数组）而不是中文摘要",
    )

    # 流水线子命令：挑卡 / 定位 / 质检 / 打包（契约 §5–§7）。
    pipeline_cli.register(subparsers)
    # agent 驱动的出题 / 交卷层（docs/mcp-v1.md §2）：JSON 输出。
    flow_cli.register(subparsers)
    # 目录看图 v1（docs/tocv-v1.md）：拼接图定位 + 草稿提交与检查。
    tocv_cli.register(subparsers)
    review_parser = subparsers.add_parser(
        "review-html",
        help="审核：把一张卡的判定和所在页做成自包含 HTML（人工抽检导出）",
    )
    review_parser.add_argument(
        "dir",
        metavar="DIR",
        help="工作目录（含 cards.jsonl / pages.jsonl / review/）",
    )
    review_parser.add_argument(
        "--sample",
        metavar="N",
        type=int,
        default=None,
        help="抽检张数（按 section 和 agent 判定分层），0 = 全部",
    )
    review_parser.add_argument(
        "--seed",
        metavar="S",
        type=int,
        default=0,
        help="抽样随机种子，默认 0",
    )
    review_parser.add_argument(
        "--out",
        metavar="HTML",
        required=True,
        help="输出 HTML 路径",
    )
    review_parser.add_argument(
        "--reviewer",
        metavar="NAME",
        default=None,
        help="人工审核人，导出时写成 human:<NAME>，默认 gxiang",
    )

    text_parser = subparsers.add_parser(
        "text",
        help="抽文字：写 book.json + pages.jsonl",
    )
    text_parser.add_argument("pdf", metavar="PDF", help="书文件路径")
    text_parser.add_argument(
        "--out",
        metavar="DIR",
        required=True,
        help="输出目录（一本书一个工作目录）",
    )
    text_parser.add_argument(
        "--meta",
        metavar="PATH",
        default=None,
        help="元数据清单 books.jsonl（默认找 train/data/book/books.jsonl）",
    )
    text_parser.add_argument(
        "--ocr",
        choices=ocr_mod.OCR_MODES,
        default=ocr_mod.MODE_AUTO,
        help=(
            "扫描件要不要走本地 OCR 取字：auto=探针判定 scanned 才 OCR（默认）、"
            "always=整本 OCR、never=一页都不 OCR（契约 §2.2）"
        ),
    )

    toc_parser = subparsers.add_parser(
        "toc",
        help="目录：写 toc.json（含闸门 1）",
    )
    toc_parser.add_argument("dir", metavar="DIR", help="工作目录")
    toc_parser.add_argument(
        "--pdf",
        metavar="PATH",
        default=None,
        help="书文件路径（默认按 book.json 的 nas_path 找本地缓存副本）",
    )
    toc_parser.add_argument(
        "--approved-by",
        metavar="WHO",
        default="agent:agent",
        help="闸门 1 的确认人",
    )

    sections_parser = subparsers.add_parser(
        "sections",
        help="切章节：写 sections.json（闸门 1 没过就硬拦）",
    )
    sections_parser.add_argument("dir", metavar="DIR", help="工作目录")

    extract_parser = subparsers.add_parser(
        "extract",
        help="抽词 + 解释抽取器：写 candidates.jsonl，跑试抽闸门 2",
    )
    extract_parser.add_argument(
        "dir",
        metavar="DIR",
        type=Path,
        help="一本书的工作目录（含 pages.jsonl / sections.json）",
    )
    extract_parser.add_argument(
        "--sections",
        default=None,
        help="只抽这些 section（逗号分隔的 sid，如 s01,s05）",
    )
    extract_parser.add_argument("--workers", type=int, default=4, help="并发线程数（默认 4）")
    extract_parser.add_argument("--limit", type=int, default=None, help="本轮最多抽多少段")
    extract_parser.add_argument(
        "--effort",
        default="low",
        help="思考强度 low/medium/high（默认 low；闸门 2 不过时按契约 §6 改参数重试）",
    )
    extract_parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="单次调用的 max_tokens（默认按 effort 取：low 4096 / medium 8192 / high 16384）",
    )
    extract_parser.add_argument(
        "--prompt",
        metavar="PATH",
        type=Path,
        default=None,
        help="提示词文件（默认包内的 bookcard/prompts/extract_v1.md）",
    )

    # browse-lite：本地库 + 轻量浏览页（契约 docs/browse-lite-v1.md §1）。
    browse_parser = subparsers.add_parser(
        "browse",
        help="浏览：把卡包导进本地库，在浏览器里轻量浏览 / 评分 / 改卡",
    )
    browse_sub = browse_parser.add_subparsers(dest="browse_command", metavar="SUBCOMMAND")

    browse_import_parser = browse_sub.add_parser(
        "import",
        help="把卡包导进本地库（幂等：进度和编辑都保留）",
    )
    browse_import_parser.add_argument(
        "packs",
        metavar="PACK",
        nargs="+",
        help="卡包（.ttfc.zip 或解开的包目录）",
    )
    browse_import_parser.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help=f"数据库路径（默认 {DEFAULT_DB_PATH}）",
    )

    browse_serve_parser = browse_sub.add_parser(
        "serve",
        help="起本地浏览服务（只监听 127.0.0.1）",
    )
    browse_serve_parser.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help=f"数据库路径（默认 {DEFAULT_DB_PATH}）",
    )
    browse_serve_parser.add_argument(
        "--port",
        metavar="PORT",
        type=int,
        default=8765,
        help="端口（默认 8765）",
    )
    browse_serve_parser.add_argument(
        "--no-open",
        action="store_true",
        help="不要自动开浏览器",
    )

    # MCP 服务（docs/mcp-v1.md §3）：stdio JSON-RPC。
    mcp_parser = subparsers.add_parser(
        "mcp",
        help="MCP 服务（stdio）：给 DSH / Claude Code / Codex / Devin 接",
    )
    mcp_parser.add_argument(
        "--home",
        metavar="DIR",
        default=None,
        help="书的工作目录根（默认 $BOOKCARD_HOME，没有就是 ~/.bookcard）",
    )

    return parser


def _format_bytes(size: int) -> str:
    """字节数转成人能读的大小。"""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GiB"  # pragma: no cover - 上面一定会返回


def summarize(report: dict) -> str:
    """把一份报告压成几行中文摘要。"""
    info = report["file"]
    lines = [f"书：{info['name']}（{_format_bytes(info['bytes'])}，{info['page_count']} 页）"]

    if report["verdict"] == "broken":
        lines.append(f"类型：broken（{info['error']}）")
        return "\n".join(lines)

    layer = report["text_layer"]
    counts = layer["pages"]
    lines.append(
        f"类型：{report['verdict']}（可用文本层 {layer['usable_ratio']}，语言 {report['lang']}）"
    )
    lines.append(
        "页面分布："
        f"正文 {counts['text']} / 仅水印 {counts['watermark_only']}"
        f" / 乱码 {counts['garbled']} / 空白 {counts['empty']}"
    )
    watermarks = layer["watermarks"]
    lines.append(f"水印：{'；'.join(watermarks) if watermarks else '无'}")

    toc = report["toc"]
    bookmarks = toc["bookmarks"]
    lines.append(
        f"目录来源：{toc['chosen']}"
        f"（书签 {bookmarks['total']} 条：人工 {bookmarks['manual']}"
        f" / 自动锚点 {bookmarks['auto_anchor']} / 残留 {bookmarks['residue']}；"
        f"印刷目录页 {toc['printed_toc_pages']}）"
    )

    numbers = report["page_numbers"]
    if numbers["status"] == "found":
        lines.append(
            f"页码：已找到，位置 {numbers['position']}，偏移 {numbers['offset']}"
            f"（印刷页码 = 物理页号 {numbers['offset']:+d}），置信度 {numbers['confidence']}"
        )
    elif numbers["status"] == "unknown":
        lines.append("页码：未知（这本书没有可用文本层，页码要 OCR 才准）")
    else:
        lines.append("页码：没找到稳定的印刷页码")

    notes = report["notes"]
    markers = notes["markers"]
    lines.append(
        f"编号注释：{'有' if notes['numbered_notes'] else '无'}"
        f"（圈号 {markers['circled']} / 方括号 {markers['bracket']}"
        f" / 上标 {markers['superscript']}）"
    )

    warnings = report["warnings"]
    lines.append(f"警告：{'；'.join(warnings) if warnings else '无'}")
    return "\n".join(lines)


def _write_reports(reports: Sequence[dict], out: str | None) -> None:
    """把报告写到 ``--out`` 指定的文件或目录。

    约定：``--out`` 以 ``.json`` 结尾就是文件，否则是目录（目录不存在就建）。
    """
    if out is None:
        return

    target = Path(out)
    is_dir = out.endswith(os.sep) or target.is_dir() or target.suffix.lower() != ".json"

    if is_dir:
        target.mkdir(parents=True, exist_ok=True)
        for report in reports:
            name = Path(report["file"]["name"]).stem or "book"
            path = target / f"{name}.probe.json"
            path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return

    if len(reports) > 1:
        raise ValueError("多本书时 --out 必须是一个目录")

    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(reports[0], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口，返回进程退出码。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in {"select", "locate", "audit", "pack", "arxiv", "paper"}:
        return pipeline_cli.run(args)
    if args.command == "flow":
        return flow_cli.run(args)
    if args.command == "tocv":
        return tocv_cli.run(args)
    if args.command == "review-html":
        from bookcard.review import cmd_review_html

        return cmd_review_html(args)

    if args.command == "text":
        return _run_text(args)
    if args.command == "toc":
        return _run_toc(args)
    if args.command == "sections":
        return _run_sections(args)

    if args.command == "extract":
        # 逻辑都在 bookcard.extract 里，这里只转参数。
        from bookcard import extract

        return extract.main(
            [
                str(args.dir),
                *([] if args.sections is None else ["--sections", args.sections]),
                "--workers",
                str(args.workers),
                "--effort",
                args.effort,
                *([] if args.max_tokens is None else ["--max-tokens", str(args.max_tokens)]),
                *([] if args.limit is None else ["--limit", str(args.limit)]),
                *([] if args.prompt is None else ["--prompt", str(args.prompt)]),
            ]
        )

    if args.command == "browse":
        return _run_browse(args)

    if args.command == "mcp":
        from bookcard.mcp import server as mcp_server

        return mcp_server.serve(["--home", args.home] if args.home else [])

    if args.command != "probe":
        parser.error(f"未知命令：{args.command}")
        return 2  # pragma: no cover - parser.error 会直接退出

    if args.pages is not None and args.pages <= 0:
        parser.error("--pages 必须是正整数")
        return 2  # pragma: no cover - parser.error 会直接退出

    reports = []
    for path in args.paths:
        try:
            reports.append(probe(path, pages=args.pages))
        except Exception as exc:  # 兜底：单本书出问题不能打断整批
            reports.append(broken_report(path, f"探针异常：{type(exc).__name__}: {exc}"))

    try:
        _write_reports(reports, args.out)
    except (OSError, ValueError) as exc:
        print(f"写报告失败：{exc}")
        return 2

    if args.json:
        payload = reports[0] if len(reports) == 1 else reports
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for report_ in reports:
            print(summarize(report_))
            print()

    if any(report_["verdict"] == "broken" for report_ in reports):
        return EXIT_BROKEN
    return 0


def _run_browse(args: argparse.Namespace) -> int:
    """``bookcard browse import|serve``（契约 docs/browse-lite-v1.md §1）。"""
    from bookcard.browse.db import BrowseDB, BrowseError
    from bookcard.browse.server import serve

    if args.browse_command is None:
        print("用法：bookcard browse import <pack...> | bookcard browse serve")
        return 2

    db = BrowseDB(args.db or DEFAULT_DB_PATH)

    if args.browse_command == "import":
        try:
            db.initialize()
            for pack in args.packs:
                summary = db.import_pack(pack)
                print(
                    f"导入《{summary['title']}》（{summary['book_id']}）："
                    f"{summary['libraries']} 个词库 / {summary['cards']} 张卡 / "
                    f"{summary['images']} 张图 / 场景 {summary['scene_mode']} / "
                    f"{summary['visibility']}"
                )
                if summary["visibility_changed"]:
                    print(
                        f"  ⚠️ 这本书库里已经是 {summary['previous_visibility']} 版了，"
                        f"这次导入的 {summary['visibility']} 版覆盖了它"
                        "（同一个库放不下同一本书的两个版本）。"
                    )
        except BrowseError as exc:
            print(f"导入失败：{exc}")
            return EXIT_BROKEN
        except OSError as exc:
            print(f"导入失败：{exc}")
            return EXIT_BROKEN
        return 0

    if args.browse_command == "serve":
        try:
            serve(db, port=args.port, open_browser=not args.no_open)
        except OSError as exc:
            print(f"起服务失败：{exc}")
            return EXIT_BROKEN
        return 0

    print(f"未知子命令：{args.browse_command}")
    return 2


def _run_text(args: argparse.Namespace) -> int:
    """``bookcard text``。"""
    try:
        summary = build_text(
            args.pdf,
            args.out,
            args.meta,
            ocr_mode=getattr(args, "ocr", ocr_mod.MODE_AUTO),
        )
    except ocr_mod.OCRUnavailableError as exc:
        print(f"抽文字失败：{exc}")
        return EXIT_BROKEN
    except Exception as exc:
        print(f"抽文字失败：{type(exc).__name__}: {exc}")
        return EXIT_BROKEN
    print(summarize_text(summary))
    return 0


def _run_toc(args: argparse.Namespace) -> int:
    """``bookcard toc``。"""
    try:
        summary = build_toc(args.dir, args.pdf, args.approved_by)
    except MissingPdfError as exc:
        print(f"目录失败：{exc}")
        return EXIT_BROKEN
    except Exception as exc:
        print(f"目录失败：{type(exc).__name__}: {exc}")
        return EXIT_BROKEN
    print(summarize_toc(summary))
    return 0 if summary["gate"] == "approved" else EXIT_GATE


def _run_sections(args: argparse.Namespace) -> int:
    """``bookcard sections``。"""
    try:
        summary = build_sections_file(args.dir)
    except GateNotApprovedError as exc:
        print(f"闸门 1 没通过，不许切章节：{exc}")
        return EXIT_GATE
    except Exception as exc:
        print(f"切章节失败：{type(exc).__name__}: {exc}")
        return EXIT_BROKEN
    print(summarize_sections(summary))
    return 0
