"""``bookcard flow`` 子命令：:mod:`bookcard.agentflow` 的命令行薄包装。

每个函数一个同名子命令（函数名里的下划线换成连字符），结果 **JSON 打到
stdout**，日志走 stderr——给没有 MCP 的 harness 直接调。

流程（契约 §0 / ``docs/mcp-v1.md`` §2）::

    book-add → toc-review → toc-gate → sections-build
             → extract-next → extract-submit ×N
             → extract-next(full) → extract-submit ×N
             → cards-build → pack

每步之后调一次 ``bookcard flow status DIR``，看 ``next`` 提示下一步。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bookcard import agentflow

#: 命令名 → 一句话说明。
COMMANDS: tuple[tuple[str, str], ...] = (
    ("book-add", "加一本书：probe + text + 场景建议（§2.4）"),
    ("status", "当前走到哪一步、闸门的状态、下一步该干什么（§2.4）"),
    ("toc-review", "把目录摊开给 agent 核对：条目 + 目录页原文（§2.3）"),
    ("toc-gate", "闸门 1 拍板：批准 / 驳回（§2.3）"),
    ("sections-build", "切章节（闸门 1 没过就拒绝，§2.4）"),
    ("extract-next", "出抽词题：一页一段（§2.1）"),
    ("extract-submit", "交抽词的卷：解析、落位、写 candidates.jsonl（§2.1）"),
    ("cards-build", "挑卡 → 定位 → 质检（§2.4）"),
    ("pack", "打包：内部分享版的场景由用户选（§2.4）"),
)

#: 退出码：业务失败（闸门没过、解析失败……）。
EXIT_FAILED = 1


def _add_dir(parser: argparse.ArgumentParser, help_text: str = "书的工作目录") -> None:
    parser.add_argument("dir", metavar="DIR", type=Path, help=help_text)


def _add_output(parser: argparse.ArgumentParser) -> None:
    """交卷的两种给法：文件（推荐）或直接给文本。"""
    parser.add_argument(
        "--output-file",
        metavar="PATH",
        default=None,
        help="agent 的回答写在这个文件里（JSON）",
    )
    parser.add_argument(
        "--output",
        metavar="TEXT",
        default=None,
        help="agent 的回答直接给文本（和 --output-file 二选一）",
    )


def register(subparsers: argparse._SubParsersAction) -> None:
    """把 ``flow`` 的子命令注册到 ``bookcard`` 的子命令表里。"""
    parser = subparsers.add_parser(
        "flow",
        help="agent 驱动的出题 / 交卷层（JSON 输出，给 harness 调）",
    )
    flow_sub = parser.add_subparsers(dest="flow_command", metavar="SUBCOMMAND")

    for name, help_text in COMMANDS:
        sub = flow_sub.add_parser(name, help=help_text)

        if name == "book-add":
            sub.add_argument("pdf", metavar="PDF", type=Path, help="书 PDF 路径")
            sub.add_argument(
                "--home",
                metavar="DIR",
                default=None,
                help="书的工作目录根（默认 $BOOKCARD_HOME，没有就是 ~/.bookcard）",
            )
            sub.add_argument(
                "--books-jsonl",
                metavar="PATH",
                default=None,
                help="元数据清单 books.jsonl（契约 §1）",
            )
            continue

        _add_dir(sub)

        if name == "toc-gate":
            group = sub.add_mutually_exclusive_group(required=True)
            group.add_argument("--approve", action="store_true", help="批准")
            group.add_argument("--reject", action="store_true", help="驳回")
            sub.add_argument(
                "--approved-by",
                metavar="WHO",
                required=True,
                help="确认人：agent:<harness>:<模型名>",
            )
            sub.add_argument("--notes", metavar="TEXT", default="", help="理由 / 说明")
            sub.add_argument(
                "--level",
                metavar="N",
                type=int,
                default=1,
                help="抽词切章节用目录的第几层（用户在第二步确认的层级；默认 1）",
            )

        if name == "extract-next":
            sub.add_argument(
                "--max-segments",
                metavar="N",
                type=int,
                default=agentflow.DEFAULT_MAX_SEGMENTS,
                help="本轮最多出几段（默认 4）",
            )
            sub.add_argument(
                "--level",
                metavar="N[,N]",
                default=None,
                help=(
                    "词库按目录的第几层分（可多层，如 1,2）。"
                    "抽词按页走，改层级不用重抽。"
                ),
            )

        if name == "extract-submit":
            sub.add_argument("--seg-id", metavar="ID", required=True, help="段 id")
            _add_output(sub)
            sub.add_argument(
                "--extractor",
                metavar="WHO",
                required=True,
                help="agent:<harness>:<模型名>:<提示词版本>",
            )

        if name == "pack":
            sub.add_argument(
                "--edition",
                choices=agentflow.EDITIONS,
                default=agentflow.EDITION_PRIVATE,
                help="卡包档位：private=内部分享版（默认），public=公开版",
            )
            sub.add_argument(
                "--scene",
                choices=agentflow.SCENES,
                default=None,
                help="可选：text=文本块，image=书页图（默认 image）",
            )


def _read_output(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """交卷的回答：``--output-file`` 或 ``--output``。

    返回 ``(回答, 错误)``——两者有一个是 ``None``。
    """
    output_file = getattr(args, "output_file", None)
    if output_file:
        path = Path(output_file)
        if not path.exists():
            return None, f"回答文件不存在：{path}"
        return path.read_text(encoding="utf-8"), None
    output = getattr(args, "output", None)
    if output is None:
        return None, "没拿到回答：给 --output-file PATH 或 --output TEXT 其中之一"
    return output, None


def _run(args: argparse.Namespace) -> dict:
    """跑一个 flow 子命令，返回要打到 stdout 的 JSON 载荷。"""
    command = args.flow_command

    if command == "book-add":
        return agentflow.book_add(
            args.pdf,
            home=args.home,
            books_jsonl=args.books_jsonl,
        )

    if command == "status":
        return agentflow.status(args.dir)
    if command == "toc-review":
        return agentflow.toc_review(args.dir)
    if command == "toc-gate":
        return agentflow.toc_gate(
            args.dir,
            approve=args.approve,
            approved_by=args.approved_by,
            notes=args.notes,
            level=getattr(args, "level", 1),
        )
    if command == "sections-build":
        return agentflow.sections_build(args.dir)
    if command == "extract-next":
        return agentflow.extract_next(
            args.dir,
            max_segments=args.max_segments,
            level=getattr(args, "level", None),
        )
    if command == "extract-submit":
        output, problem = _read_output(args)
        if problem:
            return {"ok": False, "error": problem}
        return agentflow.extract_submit(args.dir, args.seg_id, output, args.extractor)
    if command == "cards-build":
        return agentflow.cards_build(args.dir)
    if command == "pack":
        # 显式传了 --scene 就是命令行定的（契约 §7.1：记 "cli"）。
        return agentflow.pack_from_cli(
            args.dir,
            edition=args.edition,
            scene=args.scene,
        )

    return {"ok": False, "error": f"未知子命令：{command}"}


def run(args: argparse.Namespace) -> int:
    """跑一个 flow 子命令：JSON 打到 stdout，返回退出码。"""
    if getattr(args, "flow_command", None) is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "用法：bookcard flow <子命令> [参数]",
                    "commands": [name for name, _ in COMMANDS],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    payload = _run(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return EXIT_FAILED if payload.get("ok") is False else 0
