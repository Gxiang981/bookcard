"""MCP 工具表（``docs/mcp-v1.md`` §3）。

每个工具都是 :mod:`bookcard.agentflow` 的**薄包装**：参数转过去、返回值转回来。
**不在这一层重写流程逻辑**，一道闸门（目录确认）原样透出。

出错时回 ``isError: true``，错误文字用中文，**写明下一步怎么做**。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from bookcard import agentflow
from bookcard.browse.db import BrowseError
from bookcard.mcp.browse import BrowseRunner
from bookcard.mcp.progress import ProgressReporter, run_with_progress

#: JSON 对象的类型（工具参数和返回值都是它）。
JSON = dict[str, Any]

#: ``book`` 参数的形状：书指纹前 12 位（小写十六进制）。
FP12_RE = re.compile(r"^[0-9a-f]{12}$")

#: 审卡一批几张卡的上限（防 agent 要一个巨大的批）。
MAX_BATCH_SIZE = 64

#: 一轮出题最多几段的上限。
MAX_SEGMENTS = 32


class ToolError(Exception):
    """工具用错了（参数缺失、格式不对、状态不对）。"""


class UnknownToolError(Exception):
    """没有这个工具——协议层错误（JSON-RPC ``-32602``）。"""


# --------------------------------------------------------------------------- 取参数


def _require_str(args: dict[str, Any], key: str, hint: str = "") -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"缺少参数 {key!r}{('：' + hint) if hint else ''}")
    return value.strip()


def _optional_str(args: dict[str, Any], key: str, default: str = "") -> str:
    value = args.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ToolError(f"参数 {key!r} 必须是字符串")
    return value.strip()


def _optional_int(args: dict[str, Any], key: str, default: int) -> int:
    value = args.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"参数 {key!r} 必须是整数")
    return value


def _require_bool(args: dict[str, Any], key: str) -> bool:
    value = args.get(key)
    if not isinstance(value, bool):
        raise ToolError(f"参数 {key!r} 必须是 true 或 false（批准用 true，驳回用 false）")
    return value


# --------------------------------------------------------------------------- 工具表

_BOOK_SCHEMA = {
    "type": "string",
    "description": (
        "书的指纹前 12 位（fp12，小写十六进制）。book_add 的返回值里有；"
        "book_list 可以列出所有书的 fp12。"
    ),
    "pattern": "^[0-9a-f]{12}$",
}


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    """造一条工具定义（JSON Schema 写全，契约 §3）。"""
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
    }


#: 工具清单（顺序 = ``tools/list`` 里的顺序）。
TOOLS: list[dict[str, Any]] = [
    _tool(
        "book_add",
        (
            "把一本书加进来：探针 + 取字（扫描件自动 OCR）。返回 fp12、书名、页数、"
            "探针结论、是否走了 OCR、场景建议 scene_suggested（默认 image）和"
            "判定理由 text_eligible。"
            "场景默认走书页图（image），不用问用户；用户主动要文本块时，"
            "pack 时才传 scene=text、scene_chosen_by=user。然后调 toc_review 核对目录。"
        ),
        {
            "pdf_path": {
                "type": "string",
                "description": "书 PDF 的路径（本机可读就行，不限 NAS 还是本地缓存）。",
            },
            "books_jsonl": {
                "type": "string",
                "description": (
                    "可选：元数据清单 books.jsonl（契约 §1 的 title / subject 等从这儿来）。"
                    "不给就留空，不编。"
                ),
            },
        },
        ["pdf_path"],
    ),
    _tool(
        "book_list",
        (
            "列出工作目录里的所有书，每本带当前状态和下一步该做什么。"
            "扫两处：新位置 $BOOKCARD_HOME/books/<fp12>/ 和旧位置 "
            "$BOOKCARD_HOME/<fp12>/（早期版本的书，只读兼容不搬）；"
            "每本带 location（books = 新位置，legacy = 旧位置）。"
        ),
        {},
    ),
    _tool(
        "book_status",
        (
            "当前走到哪一步、目录闸门的状态、各计数，外加一句 next："
            "下一步该调哪个工具。每做完一步都看一次。"
        ),
        {"book": _BOOK_SCHEMA},
        ["book"],
    ),
    _tool(
        "toc_review",
        (
            "把目录摊开给核对：目录条目、目录页原文、闸门 1 的检查结果和当前状态。"
            "逐条核对标题和页码（契约 §3 / §6 的判据）；"
            "有疑问就讲给用户，别自己改目录。下一步：核对没问题后调 toc_gate。"
        ),
        {"book": _BOOK_SCHEMA},
        ["book"],
    ),
    _tool(
        "toc_gate",
        (
            "闸门 1（目录）的拍板：approve=true 批准，false 驳回。"
            "批准前程序会重跑契约 §6 的确定性判据，不过就不许批准。"
            "level = 抽词切章节用目录的第几层（用户确认的层级，默认 1）。"
            "approved_by 写 agent:<harness>:<模型名>，notes 里逐条写理由。"
        ),
        {
            "book": _BOOK_SCHEMA,
            "approve": {
                "type": "boolean",
                "description": "true = 批准目录，false = 驳回。",
            },
            "approved_by": {
                "type": "string",
                "description": (
                    "确认人，格式 agent:<harness>:<模型名>，"
                    "例如 agent:dsh:MiniMax-M3.1-Flash。"
                ),
            },
            "notes": {
                "type": "string",
                "description": "理由：逐条对照契约 §3 / §6 的判据写清楚。",
            },
            "level": {
                "type": "integer",
                "minimum": 1,
                "description": (
                    "抽词切章节用目录的第几层 —— 用户在第二步确认的层级。"
                    "不传就是第 1 层。"
                ),
            },
        },
        ["book", "approve", "approved_by"],
    ),
    _tool(
        "sections_build",
        "切章节（写 sections.json）。闸门 1 没过就拒绝。",
        {"book": _BOOK_SCHEMA},
        ["book"],
    ),
    _tool(
        "extract_next",
        (
            "出抽词题：把页面文字套进提示词给 agent 作答。"
            "返回 system（提示词）、segments（每段一个 seg_id / page / section_title / user）、"
            "remaining、done。同一段已出题未交卷的不会重复出（30 分钟租约）。"
        ),
        {
            "book": _BOOK_SCHEMA,
            "level": {
                "type": "string",
                "description": (
                    "词库按目录的第几层分（一次只能一层，如 \"1\"）。"
                    "抽词是按页走的，改层级不用重抽 —— 这一步只是把词库重切一遍。"
                    "不传就沿用 toc_gate 里定的那层。"
                ),
            },
            "max_segments": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_SEGMENTS,
                "description": "这一轮最多出几段（默认 4）。",
            },
        },
        ["book"],
    ),
    _tool(
        "extract_submit",
        (
            "交抽词的卷：解析 agent 的回答、落位、写 candidates.jsonl。"
            "output 是 agent 按提示词给出的 JSON（{\"keywords\": [...]}）。"
            "extractor 写 agent:<harness>:<模型名>:<提示词版本>，例如 "
            "agent:dsh:MiniMax-M3.1-Flash:ex-v2。"
            "解析失败会返回 retry=true——重答这一段再交。"
        ),
        {
            "book": _BOOK_SCHEMA,
            "seg_id": {"type": "string", "description": "段 id（extract_next 给的）。"},
            "output": {"type": "string", "description": "agent 的回答（JSON 文本）。"},
            "extractor": {
                "type": "string",
                "description": "抽词的人：agent:<harness>:<模型名>:<提示词版本>。",
            },
        },
        ["book", "seg_id", "output", "extractor"],
    ),
    _tool(
        "cards_build",
        "挑卡 → 定位 → 质检（写 cards.jsonl 和 quality_report.json）。全量抽完才能调。",
        {"book": _BOOK_SCHEMA},
        ["book"],
    ),
    _tool(
        "pack",
        (
            "打包。edition=private 是内部分享版；edition=public 是公开版"
            "（不许带书页图）。"
            "场景默认走书页图（image），不用问用户：只在用户主动要文本块时"
            "才传 scene=text、scene_chosen_by=user。"
            "打包前目录闸门要已通过。"
        ),
        {
            "book": _BOOK_SCHEMA,
            "edition": {
                "type": "string",
                "enum": list(agentflow.EDITIONS),
                "description": "private = 内部分享版，public = 公开版。",
            },
            "scene": {
                "type": "string",
                "enum": list(agentflow.SCENES),
                "default": agentflow.DEFAULT_SCENE,
                "description": (
                    "可选：text = 文本块，image = 书页图（默认 image）。"
                    "只在用户主动要文本块时才传 text。公开版不用这个参数。"
                ),
            },
            "scene_chosen_by": {
                "type": "string",
                "enum": list(agentflow.SCENE_CHOSEN_BY),
                "default": agentflow.SCENE_CHOSEN_BY_DEFAULT,
                "description": (
                    "可选：user = 当场问过用户；default = 用的默认场景"
                    "（默认）。公开版不用这个参数。"
                ),
            },
        },
        ["book", "edition"],
    ),
    _tool(
        "browse_open",
        (
            "把这本书的卡包导进本地浏览库，在本机起浏览服务，返回 URL。"
            "只监听 127.0.0.1，端口被占就顺延；重复调用复用已起的服务。"
        ),
        {"book": _BOOK_SCHEMA},
        ["book"],
    ),
]


#: 每个工具出错时给的「下一步」。
NEXT_HINTS: dict[str, str] = {
    "book_add": "先调 book_add 把这本书加进来，拿到 fp12 之后往下走。",
    "book_list": "先调 book_add 把书加进来。",
    "book_status": "按 next 提示调下一步的工具；每做完一步再调一次 book_status。",
    "toc_review": "逐条核对目录后调 toc_gate 拍板；有问题就讲给用户。",
    "toc_gate": "目录没过闸门 1 时不能切章节：核对目录后重新调 toc_gate。",
    "sections_build": "闸门 1 没过：先调 toc_review 核对目录，再调 toc_gate 批准。",
    "extract_next": "先确认目录闸门过了（toc_gate），再出题。",
    "extract_submit": "解析失败会带 retry=true：按提示词重答这一段再交一次。",
    "cards_build": "还有段没抽完时不能挑卡：先调 extract_next 把全书抽完（segments 返空）。",
    "pack": "打包前目录闸门要过；内部分享版默认带书页图，不用问用户。",
    "browse_open": "这本书还没打包：先调 pack，再调 browse_open。",
}


# --------------------------------------------------------------------------- 工具箱


class ToolBox:
    """工具的接线：参数 → :mod:`bookcard.agentflow` → 返回值。"""

    def __init__(self, home: str | Path | None = None, browse: BrowseRunner | None = None) -> None:
        self.home = Path(home).expanduser() if home else agentflow.default_home()
        self.browse = browse or BrowseRunner()

    # -- 目录 ------------------------------------------------------------

    def books_dir(self) -> Path:
        """书的工作目录根（规范位置：``<home>/books/``）。"""
        return self.home / "books"

    def legacy_book_dir(self, fp12: str) -> Path:
        """旧位置：``<home>/<fp12>/``。

        早期版本把书直接放在 home 根下（没有 ``books/`` 这一层）。这些书**不搬**，
        引擎照常认，见 :meth:`book_dir`。
        """
        return self.home / fp12

    def book_dir(self, fp12: str) -> Path:
        """一本书的工作目录（按 fp12 找）。

        规范位置是 ``<home>/books/<fp12>/``。旧位置 ``<home>/<fp12>/``（目录里有
        ``book.json``）也认——**不搬数据**，只是能查到、能接着走。

        新书一律写新位置（``book_add`` 走 :meth:`books_dir`）。
        """
        if not FP12_RE.match(fp12 or ""):
            raise ToolError(
                f"book 参数 {fp12!r} 不对：要的是书指纹前 12 位（fp12，小写十六进制，12 个字符）。"
                f"下一步：{NEXT_HINTS['book_status']}"
            )
        path = self.books_dir() / fp12
        if path.is_dir():
            return path
        legacy = self.legacy_book_dir(fp12)
        if legacy.is_dir() and (legacy / "book.json").is_file():
            return legacy
        return path

    def _existing_book_dir(self, args: dict[str, Any]) -> Path:
        fp12 = _require_str(args, "book", "要书指纹前 12 位（fp12）。")
        path = self.book_dir(fp12)
        if not path.is_dir():
            raise ToolError(
                f"没有这本书：{path} 不存在（旧位置 {self.legacy_book_dir(fp12)} 也没有）。"
                f"下一步：{NEXT_HINTS['book_status']}"
            )
        return path

    # -- 工具表 ----------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        """``tools/list`` 的内容。"""
        return [dict(tool) for tool in TOOLS]

    # -- 调用 ------------------------------------------------------------

    def call(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        reporter: ProgressReporter | None = None,
    ) -> dict[str, Any]:
        """调一个工具，返回 MCP 的 ``tools/call`` 结果。"""
        args = arguments if isinstance(arguments, dict) else {}
        handler = self._handler(name)
        if handler is None:
            raise UnknownToolError(
                f"没有叫 {name!r} 的工具。可用的工具：{'、'.join(tool['name'] for tool in TOOLS)}。"
            )

        report = reporter or ProgressReporter(lambda _payload: None)
        try:
            payload = handler(self, args, report)
        except ToolError as exc:
            return _error_result(str(exc), NEXT_HINTS.get(name, ""))
        except BrowseError as exc:
            return _error_result(f"浏览服务出错：{exc}", NEXT_HINTS.get(name, ""))
        except Exception as exc:  # noqa: BLE001 - 出错要变成 isError，不抛到协议层
            return _error_result(
                f"工具 {name} 执行出错：{type(exc).__name__}: {exc}",
                NEXT_HINTS.get(name, ""),
            )

        if isinstance(payload, dict) and payload.get("ok") is False:
            error = str(payload.get("error") or "未知错误")
            extra = ""
            if payload.get("retry"):
                extra = "\n这一条需要重答：按提示词重做这一段/这一批，再交一次。"
            return _error_result(error, NEXT_HINTS.get(name, ""), extra)
        return _ok_result(payload)

    # -- 各个工具的接线 --------------------------------------------------

    def _handler(self, name: str):
        return {
            "book_add": ToolBox._tool_book_add,
            "book_list": ToolBox._tool_book_list,
            "book_status": ToolBox._tool_book_status,
            "toc_review": ToolBox._tool_toc_review,
            "toc_gate": ToolBox._tool_toc_gate,
            "sections_build": ToolBox._tool_sections_build,
            "extract_next": ToolBox._tool_extract_next,
            "extract_submit": ToolBox._tool_extract_submit,
            "cards_build": ToolBox._tool_cards_build,
            "pack": ToolBox._tool_pack,
            "browse_open": ToolBox._tool_browse_open,
        }.get(name)

    # -- §2.4 加书 -------------------------------------------------------

    def _tool_book_add(self, args: JSON, report: ProgressReporter) -> JSON:
        pdf_path = _require_str(args, "pdf_path", "要书 PDF 的路径。")
        books_jsonl = _optional_str(args, "books_jsonl", "")
        pages = _page_count(pdf_path)
        # 书的目录名就是 fp12（PDF 全字节的 SHA-1 前 12 位），先算出来才能盯着它看进度。
        fp12 = _fp12_of(pdf_path)
        book_dir = self.books_dir() / fp12

        def counter() -> tuple[float, float | None, str]:
            done = len(list((book_dir / "ocr").glob("p*.json")))
            return float(done), float(pages), f"取字中：已处理 {done} 页 / 共 {pages} 页"

        return run_with_progress(
            report,
            counter,
            lambda: agentflow.book_add(
                pdf_path,
                home=self.home,
                books_jsonl=books_jsonl or None,
            ),
        )

    # -- §2.4 其余步骤 ---------------------------------------------------

    def _tool_book_list(self, args: JSON, report: ProgressReporter) -> JSON:
        books: list[dict[str, Any]] = []
        seen: set[str] = set()
        # 先扫新位置再扫旧位置：同一个 fp12 两处都有时只列新位置那个。
        for location, root in (("books", self.books_dir()), ("legacy", self.home)):
            if not root.is_dir():
                continue
            for path in sorted(root.iterdir()):
                if not path.is_dir() or not FP12_RE.match(path.name):
                    continue
                if path.name in seen:
                    continue
                # 只认真书：目录里得有 book.json（home 根下的别的目录不算）。
                if not (path / "book.json").is_file():
                    continue
                state = agentflow.status(path)
                books.append(
                    {
                        "fp12": path.name,
                        "title": state.get("title", ""),
                        "step": state.get("step", ""),
                        "gates": state.get("gates", {}),
                        "counts": state.get("counts", {}),
                        "next": state.get("next", ""),
                        "location": location,
                    }
                )
                seen.add(path.name)
        return {"ok": True, "home": str(self.home), "books": books, "count": len(books)}

    def _tool_book_status(self, args: JSON, report: ProgressReporter) -> JSON:
        return agentflow.status(self._existing_book_dir(args))

    def _tool_toc_review(self, args: JSON, report: ProgressReporter) -> JSON:
        return agentflow.toc_review(self._existing_book_dir(args))

    def _tool_toc_gate(self, args: JSON, report: ProgressReporter) -> JSON:
        path = self._existing_book_dir(args)
        approve = _require_bool(args, "approve")
        approved_by = _require_str(args, "approved_by", "格式 agent:<harness>:<模型名>。")
        notes = _optional_str(args, "notes", "")
        level = _optional_int(args, "level", 1)
        return agentflow.toc_gate(
            path, approve=approve, approved_by=approved_by, notes=notes, level=level
        )

    def _tool_sections_build(self, args: JSON, report: ProgressReporter) -> JSON:
        return agentflow.sections_build(self._existing_book_dir(args))

    # -- §2.1 抽词 -------------------------------------------------------

    def _tool_extract_next(self, args: JSON, report: ProgressReporter) -> JSON:
        path = self._existing_book_dir(args)
        max_segments = _optional_int(args, "max_segments", agentflow.DEFAULT_MAX_SEGMENTS)
        level = args.get("level")
        if level is not None:
            level = str(level)
        return agentflow.extract_next(path, max_segments=max_segments, level=level)

    def _tool_extract_submit(self, args: JSON, report: ProgressReporter) -> JSON:
        path = self._existing_book_dir(args)
        seg_id = _require_str(args, "seg_id", "段 id 来自 extract_next。")
        output = _require_str(args, "output", "agent 的回答（JSON 文本）。")
        extractor = _require_str(args, "extractor", "格式 agent:<harness>:<模型名>:<提示词版本>。")
        return agentflow.extract_submit(path, seg_id, output, extractor)

    # -- §2.4 挑卡定位质检、打包、浏览 -----------------------------------

    def _tool_cards_build(self, args: JSON, report: ProgressReporter) -> JSON:
        path = self._existing_book_dir(args)
        total = _pages_of_cards(path)

        def counter() -> tuple[float, float | None, str]:
            done = len(list((path / "images").glob("p*.jpg")))
            return float(done), float(total), f"定位中：已渲染 {done} 页 / 共 {total} 页"

        return run_with_progress(report, counter, lambda: agentflow.cards_build(path))

    def _tool_pack(self, args: JSON, report: ProgressReporter) -> JSON:
        path = self._existing_book_dir(args)
        edition = _require_str(args, "edition", "只能是 private 或 public。")
        scene = _optional_str(args, "scene", "") or None
        scene_chosen_by = _optional_str(args, "scene_chosen_by", "") or None

        def counter() -> tuple[float, float | None, str]:
            size = sum(item.stat().st_size for item in (path / "pack").glob("*.ttfc.zip"))
            return float(size), None, f"打包中：已写 {size // 1024} KB"

        return run_with_progress(
            report,
            counter,
            lambda: agentflow.pack(
                path,
                edition=edition,
                scene=scene,
                scene_chosen_by=scene_chosen_by,
            ),
        )

    def _tool_browse_open(self, args: JSON, report: ProgressReporter) -> JSON:
        path = self._existing_book_dir(args)
        result = self.browse.open_book(path)
        return {"ok": True, **result}


# --------------------------------------------------------------------------- 小工具


def _page_count(pdf_path: str) -> int:
    """书有多少页（只用来报进度，读不动内容）。"""
    try:
        import pymupdf

        with pymupdf.open(pdf_path) as doc:
            return int(doc.page_count)
    except Exception:  # noqa: BLE001 - 读不出来就不报总数
        return 0


def _fp12_of(pdf_path: str) -> str:
    """书指纹前 12 位（工作目录名）。"""
    from bookcard.engine import common

    try:
        return common.fp12_of(common.sha1_file(pdf_path))
    except OSError:
        return ""


def _pages_of_cards(book_dir: Path) -> int:
    """卡分布在多少页上（定位这一步要渲染多少页）。"""
    cards_path = book_dir / "cards.jsonl"
    if not cards_path.is_file():
        return 0
    pages: set[int] = set()
    try:
        for line in cards_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            source = row.get("source") or {}
            number = source.get("page")
            if isinstance(number, int):
                pages.add(number)
    except (OSError, json.JSONDecodeError):
        return 0
    return len(pages)


def _text_block(payload: Any) -> dict[str, Any]:
    """工具结果的一个文本块。"""
    text = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, ensure_ascii=False, indent=2)
    )
    return {"type": "text", "text": text}


def _ok_result(payload: Any) -> dict[str, Any]:
    """成功的结果。"""
    return {"content": [_text_block(payload)], "isError": False}


def _error_result(message: str, hint: str = "", extra: str = "") -> dict[str, Any]:
    """出错的结果（``isError: true``），文字用中文并写明下一步。"""
    lines = [f"出错了：{message}"]
    if extra:
        lines.append(extra.strip())
    if hint:
        lines.append(f"下一步：{hint}")
    return {"content": [_text_block("\n".join(lines))], "isError": True}
