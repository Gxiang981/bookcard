"""``bookcard tocv`` 子命令：目录看图 v1 的命令行薄包装。

契约见 ``docs/tocv-v1.md``。引擎的活都在 :mod:`bookcard.engine.tocvision`，
这里只做接线：

- ``bookcard tocv init <pdf> --out <dir>``
- ``bookcard tocv sheet <dir> [--first N] [--last N] [--cols N] [--rows N] [--json]``
- ``bookcard tocv locate <dir> --pages 4,5,6 --by <agent-id>``
- ``bookcard tocv pages <dir> --pages 4,5,6 [--dpi N] [--json]``
- ``bookcard tocv submit <dir> --draft <file> --by <agent-id>``
- ``bookcard tocv score <dir> --gold <gold.json>``
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bookcard.engine import tocvision

#: 退出码：命令本身失败。
EXIT_FAILED = 1

#: 退出码：参数用错。
EXIT_USAGE = 2


def _parse_pages(text: str) -> list[int]:
    """``"4,5,6"`` → ``[4, 5, 6]``。"""
    numbers: list[int] = []
    for chunk in str(text).replace("，", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            numbers.append(int(chunk))
        except ValueError as exc:
            raise ValueError(f"页码 {chunk!r} 不是整数") from exc
    if not numbers:
        raise ValueError("--pages 至少要一个页码")
    return numbers


def register(subparsers: argparse._SubParsersAction) -> None:
    """把 ``tocv`` 的子命令注册到 ``bookcard`` 的子命令表里。"""
    parser = subparsers.add_parser(
        "tocv",
        help="目录看图 v1：拼接图定位 + 目录页大图 + 草稿提交与检查（docs/tocv-v1.md）",
    )
    sub = parser.add_subparsers(dest="tocv_command", metavar="SUBCOMMAND")

    init_parser = sub.add_parser("init", help="建工作目录的状态文件（幂等）")
    init_parser.add_argument("pdf", metavar="PDF", type=Path, help="书 PDF 路径")
    init_parser.add_argument(
        "--out",
        metavar="DIR",
        required=True,
        type=Path,
        help="输出目录（一本书一个工作目录）",
    )

    sheet_parser = sub.add_parser("sheet", help="多页缩略图拼接图（角上印物理页号）")
    sheet_parser.add_argument("dir", metavar="DIR", type=Path, help="工作目录")
    sheet_parser.add_argument(
        "--first",
        metavar="N",
        type=int,
        default=tocvision.DEFAULT_FIRST,
        help=f"起始物理页（默认 {tocvision.DEFAULT_FIRST}）",
    )
    sheet_parser.add_argument(
        "--last",
        metavar="N",
        type=int,
        default=tocvision.DEFAULT_LAST,
        help=f"结束物理页（默认 {tocvision.DEFAULT_LAST}；目录可能在书后，指定任意区间）",
    )
    sheet_parser.add_argument(
        "--cols",
        metavar="N",
        type=int,
        default=tocvision.DEFAULT_COLS,
        help=f"每张图几列（默认 {tocvision.DEFAULT_COLS}）",
    )
    sheet_parser.add_argument(
        "--rows",
        metavar="N",
        type=int,
        default=tocvision.DEFAULT_ROWS,
        help=f"每张图几行（默认 {tocvision.DEFAULT_ROWS}）",
    )
    sheet_parser.add_argument("--json", action="store_true", help="stdout 输出 JSON")

    locate_parser = sub.add_parser("locate", help="记下模型判断的目录页")
    locate_parser.add_argument("dir", metavar="DIR", type=Path, help="工作目录")
    locate_parser.add_argument(
        "--pages",
        metavar="N,N",
        required=True,
        help="目录页的物理页号，逗号分隔（可以不连续）",
    )
    locate_parser.add_argument(
        "--by",
        metavar="AGENT",
        required=True,
        help="判断的 agent 标识（agent:<harness>:<模型名>）",
    )

    pages_parser = sub.add_parser("pages", help="把指定页渲染成大图")
    pages_parser.add_argument("dir", metavar="DIR", type=Path, help="工作目录")
    pages_parser.add_argument(
        "--pages",
        metavar="N,N",
        required=True,
        help="要渲染的物理页号，逗号分隔",
    )
    pages_parser.add_argument(
        "--dpi",
        metavar="N",
        type=int,
        default=tocvision.DEFAULT_DPI,
        help=f"渲染 dpi（默认 {tocvision.DEFAULT_DPI}；长边最多 2000px）",
    )
    pages_parser.add_argument("--json", action="store_true", help="stdout 输出 JSON")

    submit_parser = sub.add_parser("submit", help="交草稿：换算物理页 + 跑检查")
    submit_parser.add_argument("dir", metavar="DIR", type=Path, help="工作目录")
    submit_parser.add_argument(
        "--draft",
        metavar="PATH",
        required=True,
        type=Path,
        help="模型写的草稿（toc_draft.json）",
    )
    submit_parser.add_argument(
        "--by",
        metavar="AGENT",
        required=True,
        help="提交人：agent:<harness>:<模型名>",
    )

    score_parser = sub.add_parser("score", help="和 gold 对照，算准确率")
    score_parser.add_argument("dir", metavar="DIR", type=Path, help="工作目录")
    score_parser.add_argument(
        "--gold",
        metavar="PATH",
        required=True,
        type=Path,
        help="金标准（gold.json，条目用 physical_page）",
    )


def run(args: argparse.Namespace) -> int:
    """跑一个 tocv 子命令，返回退出码。"""
    command = getattr(args, "tocv_command", None)
    if command is None:
        print(
            "用法：bookcard tocv <子命令> [参数]\n"
            "子命令：init / sheet / locate / pages / submit / score"
        )
        return EXIT_USAGE

    try:
        if command == "init":
            state = tocvision.init_workdir(args.pdf, args.out)
            total = state.get("page_count") or 0
            with_text = len(state.get("text_layer_pages") or [])
            print(
                f"{'已存在，没重建' if not state.get('created') else '建好了'}"
                f"：{args.out}（{total} 页，文本层 {with_text} 页，"
                f"类型 {state.get('kind')}）"
            )
            return 0

        if command == "sheet":
            result = tocvision.render_sheet(
                args.dir,
                first=args.first,
                last=args.last,
                cols=args.cols,
                rows=args.rows,
            )
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                for chunk in result["sheets"]:
                    print(
                        f"{chunk['path']}"
                        f"（第 {chunk['first']}–{chunk['last']} 页，"
                        f"{chunk['width']}×{chunk['height']}px）"
                    )
            return 0

        if command == "locate":
            pages = _parse_pages(args.pages)
            result = tocvision.locate_pages(args.dir, pages, by=args.by)
            print(f"目录页：{result['toc_pages']}（{args.by}）")
            return 0

        if command == "pages":
            pages = _parse_pages(args.pages)
            result = tocvision.render_pages(args.dir, pages, dpi=args.dpi)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                for item in result["pages"]:
                    extra = f" + {item['text_path']}" if item["text_path"] else ""
                    print(f"{item['path']}（{item['width']}×{item['height']}px）{extra}")
            return 0

        if command == "submit":
            result = tocvision.submit_draft(args.dir, args.draft, by=args.by)
            for check in result["checks"]:
                mark = "过" if check["passed"] else "不过"
                print(f"{check['id']} {check['name']}：{mark} —— {check['reason']}")
            print(
                f"第 {result['round']} 轮提交：{result['status']}"
                f"（{result['failed']} 条没过；闸门 {result['gate_status']}）"
            )
            print(f"目录：{result['toc_path']}")
            print(f"报告：{result['report_path']}")
            return 0 if result["ok"] else EXIT_FAILED

        if command == "score":
            payload = tocvision.score_draft(args.dir, args.gold)
            print(tocvision.summarize_score(payload))
            return 0

    except (tocvision.TocvStateError, tocvision.DraftError, ValueError) as exc:
        print(f"{exc}")
        return EXIT_USAGE
    except FileNotFoundError as exc:
        print(f"文件找不到：{exc}")
        return EXIT_USAGE
    except Exception as exc:  # 兜底：别把栈打到 stdout
        print(f"跑 tocv {command} 失败：{type(exc).__name__}: {exc}")
        return EXIT_FAILED

    print(f"未知子命令：{command}")
    return EXIT_USAGE
