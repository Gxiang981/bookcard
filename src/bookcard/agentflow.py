"""agentflow.py — 引擎侧的「出题 / 交卷」层（``docs/mcp-v1.md`` §2）。

the maintainer 2026-11-06 拍板：抽词和审卡统一用**用户自己 harness 里 agent 的模型**，
引擎不调任何 LLM、不读任何 key、不内置云端 key。agent 拿到页面文字和提示词，
自己写出结果，再交回引擎校验落盘。

这一层只做确定性的事：

- **出题**：把页面文字套进提示词（:func:`extract_next`）；
- **交卷**：校验 agent 的回答，解析、落位、落盘（:func:`extract_submit` /
  :func:`judge_submit`）；
- **闸门**：目录闸门的批准与状态（:func:`toc_gate`）；
- **其余步骤**：把现有流水线（select / locate / audit / pack）包一层
  （:func:`sections_build` / :func:`cards_build` / :func:`pack`）。

MCP server 和 skill 是这一层的薄包装，``bookcard flow`` 子命令也是。

**不复制逻辑**：分段、落位、挑卡、判定、汇总、挑卡去重、定位、质检、打包
全部复用 :mod:`bookcard.extract`、:mod:`bookcard.judge`、
:mod:`bookcard.pipeline` 里的现成函数；需要拆公共函数的地方就地拆
（见 :func:`bookcard.judge.build_records`）。

所有函数都以书的工作目录为第一个参数，返回**可 JSON 序列化的 dict**；
出问题的返回 ``{"ok": False, "error": "…"}``（不抛异常），方便 MCP / CLI
直接转给 agent。
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from bookcard import prompts
from bookcard.engine import common
from bookcard.engine.text import build_text
from bookcard.engine.toc import (
    DEFAULT_EXTRACT_LEVEL,
    GateNotApprovedError,
    build_sections_file,
    build_toc,
    reslice_sections,
)
from bookcard.extract import (
    BookContext,
    Keyword,
    ParseError,
    Segment,
    build_segments,
    candidate_row,
    load_book_context,
    load_candidates,
    parse_keywords,
    parse_prompt,
    place_items,
)
from bookcard.pipeline.audit import audit
from bookcard.pipeline.locate import locate
from bookcard.pipeline.pack import (
    EDITION_PRIVATE,
    EDITION_PUBLIC,
    EDITIONS,
    SCENE_CHOSEN_BY_CLI,
    SCENE_CHOSEN_BY_DEFAULT,
    SCENE_CHOSEN_BY_USER,
    PackError,
)
from bookcard.pipeline.pack import (
    pack as pipeline_pack,
)
from bookcard.pipeline.scene import (
    DEFAULT_SCENE,
    SCENE_IMAGE,
    SCENE_TEXT,
    detect_scene_mode,
)
from bookcard.pipeline.select import select
from bookcard.pipeline.workdir import PipelineError, WorkDir
from bookcard.probe import probe

# --------------------------------------------------------------------------- 常量

#: 书的工作目录根（``docs/mcp-v1.md`` §1）。
HOME_ENV = "BOOKCARD_HOME"

#: ``BOOKCARD_HOME`` 没设时的默认值。
DEFAULT_HOME = Path("~/.bookcard")

#: 作业状态文件（出题租约等）。
STATE_FILENAME = "agentflow.json"

#: 出题租约：30 分钟（``docs/mcp-v1.md`` §2.1）。
LEASE_SECONDS = 30 * 60

#: 抽词时的默认分段数（``extract_next`` 的 ``max_segments``）。
DEFAULT_MAX_SEGMENTS = 4

#: 审卡时一批几张卡。
DEFAULT_BATCH_SIZE = 8

#: ``extractor`` 的格式：``agent:<harness>:<模型名>:<提示词版本>``（§2.1）。
EXTRACTOR_RE = re.compile(r"^agent:[^:\s]+:[^:\s]+:[^:\s]+$")

#: ``approved_by`` / ``reviewer`` 的格式：``agent:<harness>:<模型名>``（§2.2）。
AGENT_ID_RE = re.compile(r"^agent:[^:\s]+:[^:\s]+$")

#: 场景二选一（契约 §7.1）。
SCENES = (SCENE_TEXT, SCENE_IMAGE)

#: ``scene_chosen_by`` 的合法取值（契约 §7.1）：``"user"`` / ``"default"``。
SCENE_CHOSEN_BY = (SCENE_CHOSEN_BY_USER, SCENE_CHOSEN_BY_DEFAULT)


# --------------------------------------------------------------------------- 小工具


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(moment: datetime | None = None) -> str:
    return (moment or _now()).isoformat(timespec="seconds")


def _fail(error: str, **extra: Any) -> dict[str, Any]:
    """统一的失败返回。"""
    return {"ok": False, "error": error, **extra}


def default_home() -> Path:
    """书的工作目录根：``$BOOKCARD_HOME``，没设就是 ``~/.bookcard``。"""
    return Path(os.environ.get(HOME_ENV) or DEFAULT_HOME).expanduser()


def _context(book_dir: str | Path) -> BookContext:
    return load_book_context(Path(book_dir))


# --------------------------------------------------------------------------- 作业状态


class FlowState:
    """引擎侧的作业状态（``agentflow.json``）：出题租约 + 这本书的 PDF 路径。

    租约的用处（``docs/mcp-v1.md`` §2.1）：**已出题未交卷**的段不重复出题，
    除非超过 30 分钟没交——防止 agent 反复拿同一段题，也防止出题了却丢掉了。
    """

    def __init__(self, path: Path, payload: dict[str, Any]) -> None:
        self.path = path
        self.payload = payload

    @classmethod
    def load(cls, book_dir: str | Path) -> FlowState:
        path = Path(book_dir) / STATE_FILENAME
        payload: dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                loaded = None
            if isinstance(loaded, dict):
                payload = loaded
        payload.setdefault("version", 1)
        return cls(path, payload)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # -- 租约 ------------------------------------------------------------

    def _issued(self, namespace: str) -> dict[str, Any]:
        bucket = self.payload.get(namespace)
        if not isinstance(bucket, dict):
            bucket = {}
            self.payload[namespace] = bucket
        issued = bucket.get("issued")
        if not isinstance(issued, dict):
            issued = {}
            bucket["issued"] = issued
        return issued

    def lease_is_live(self, namespace: str, key: str) -> bool:
        """这个 key 的租约是不是还在（30 分钟内）。"""
        stamp = self._issued(namespace).get(key)
        if not isinstance(stamp, str):
            return False
        try:
            moment = datetime.fromisoformat(stamp)
        except ValueError:
            return False
        if moment.tzinfo is None:
            moment = moment.astimezone()
        return (_now() - moment).total_seconds() < LEASE_SECONDS

    def issue(self, namespace: str, key: str) -> None:
        """记一笔「已出题」。"""
        self._issued(namespace)[key] = _iso()

    def release(self, namespace: str, key: str) -> None:
        """交卷之后把租约还回去（这一段可以再出题）。"""
        self._issued(namespace).pop(key, None)


# --------------------------------------------------------------------------- 校验


def _check_extractor(extractor: str) -> str | None:
    """``extractor`` 必须形如 ``agent:<harness>:<模型名>:<提示词版本>``。"""
    value = (extractor or "").strip()
    if not EXTRACTOR_RE.match(value):
        return (
            f"extractor 格式不合规：{extractor!r}；"
            "要写成 agent:<harness>:<模型名>:<提示词版本>，"
            "例如 agent:claude-code:claude-sonnet-4-5:ex-v1"
        )
    return None


def _check_agent_id(value: str, field: str) -> str | None:
    """``approved_by`` / ``reviewer`` 必须形如 ``agent:<harness>:<模型名>``。"""
    text = (value or "").strip()
    if not AGENT_ID_RE.match(text):
        return (
            f"{field} 格式不合规：{value!r}；"
            "要写成 agent:<harness>:<模型名>，例如 agent:claude-code:claude-sonnet-4-5"
        )
    return None


# --------------------------------------------------------------------------- 产物读写


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _submitted_seg_ids(book_dir: Path) -> set[str]:
    """已经交过卷的段（``candidates.jsonl`` 里有的 ``seg_id``）。"""
    return {str(row.get("seg_id") or "") for row in load_candidates(book_dir / "candidates.jsonl")}


def _upsert_candidate_row(book_dir: Path, row: dict[str, Any]) -> None:
    """把一行候选写进 ``candidates.jsonl``；**同一个 seg 重复交卷，后交的覆盖先交的**。

    落盘格式与 :class:`bookcard.extract.JobState` 一致（契约 §4 的行格式）。
    """
    path = book_dir / "candidates.jsonl"
    kept = [
        old
        for old in load_candidates(path)
        if str(old.get("seg_id") or "") != str(row.get("seg_id") or "")
    ]
    kept.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in kept),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- §2.4 加书


def book_add(
    pdf_path: str | Path,
    *,
    home: str | Path | None = None,
    books_jsonl: str | Path | None = None,
) -> dict[str, Any]:
    """把一本书加进来：probe + text（扫描件自动 OCR），返回书的身份和场景信息。

    场景按契约 §7.1 **默认走图片场景**（``scene_suggested == "image"``）；
    三条判据只作参考，结论放在 ``text_eligible`` 里。

    参数
    ----
    pdf_path:
        书 PDF 的路径。
    home:
        书的工作目录根；默认 ``$BOOKCARD_HOME``（没设就是 ``~/.bookcard``）。
        书落在 ``<home>/books/<fp12>/``。
    books_jsonl:
        元数据清单（契约 §1 的 ``title`` / ``subject`` 等从这儿来）。

    同一本书再加一次是**幂等**的：``fp12`` 由 PDF 全字节 SHA-1 决定，
    目录还是那个目录，``book.json`` / ``pages.jsonl`` 重写一遍。
    """
    pdf = Path(pdf_path)
    if not pdf.exists():
        return _fail(f"书文件不存在：{pdf}")

    report = probe(pdf)
    verdict = str(report.get("verdict") or "")

    fp = common.sha1_file(pdf)
    fp12 = common.fp12_of(fp)
    root = Path(home).expanduser() if home else default_home()
    book_dir = root / "books" / fp12
    book_dir.mkdir(parents=True, exist_ok=True)

    # ---- 取字（扫描件的 OCR 由 ``build_text`` 负责）----
    try:
        build_text(pdf, book_dir, books_jsonl)
    except Exception as exc:  # noqa: BLE001 - 取字失败要把原因说清楚
        return _fail(f"取字失败：{type(exc).__name__}: {exc}", fp12=fp12, book_dir=str(book_dir))

    book = _read_json(book_dir / "book.json")
    pages = common.read_jsonl(book_dir / "pages.jsonl")
    ocr_pages = sum(1 for row in pages if str(row.get("text_origin") or "") == "ocr")
    used_ocr = ocr_pages > 0

    warnings: list[str] = []
    if verdict == "scanned" and not used_ocr:
        warnings.append(
            "探针判定是 scanned，但页面上没有 OCR 结果："
            "扫描件要装可选依赖 bookcard[ocr] 才会走 OCR 取字"
        )

    # ---- 场景建议值（契约 §7.1：默认 image，三条判据只作 text_eligible 参考）----
    detection = detect_scene_mode(WorkDir(book_dir), pdf)

    book["scene_suggested"] = DEFAULT_SCENE
    book["scene_suggested_reasons"] = list(detection.get("reasons") or [])
    _write_json(book_dir / "book.json", book)

    # ---- 记下 PDF 路径：后面 locate / 场景判定都要用 ----
    state = FlowState.load(book_dir)
    state.payload["pdf_path"] = str(pdf.resolve())
    state.save()

    return {
        "ok": True,
        "fp12": fp12,
        "book_dir": str(book_dir),
        "title": str(book.get("title") or ""),
        "subject": str(book.get("subject") or ""),
        "page_count": int(book.get("page_count") or len(pages)),
        "verdict": verdict,
        "ocr": used_ocr,
        "ocr_pages": ocr_pages,
        "scene_suggested": DEFAULT_SCENE,
        "text_eligible": bool(detection.get("text_eligible")),
        "scene_reasons": list(detection.get("reasons") or []),
        "scene_detection": {
            "verdict": detection.get("verdict"),
            "text_origin_ratio": detection.get("text_origin_ratio"),
            "median_image_coverage": detection.get("median_image_coverage"),
            "text_eligible": detection.get("text_eligible"),
            "reasons": list(detection.get("reasons") or []),
        },
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- §2.4 状态


def _gate_status(book_dir: Path, filename: str) -> str:
    payload = _read_json(book_dir / filename)
    gate = payload.get("gate")
    if not isinstance(gate, dict):
        return "pending"
    return str(gate.get("status") or "pending")


def status(book_dir: str | Path) -> dict[str, Any]:
    """当前走到哪一步、目录闸门的状态、各计数，外加一句 ``next``。

    ``next`` 写明 agent 下一步该调哪个工具——skill 让 agent 每步之后都看它。
    """
    path = Path(book_dir)
    if not path.is_dir():
        return _fail(f"目录不存在：{path}")

    book = _read_json(path / "book.json")
    if not book:
        return {
            "ok": True,
            "step": "book_add",
            "gates": {"toc": "pending"},
            "counts": {},
            "next": "先调 book_add 把这本书加进来。",
        }

    toc_gate = _gate_status(path, "toc.json")

    try:
        context = _context(path)
    except Exception:  # noqa: BLE001 - 目录不完整也要能报状态
        context = None

    counts: dict[str, Any] = {}
    segments: list[Segment] = []
    if context is not None:
        segments = build_segments(context.pages, context.sections, context.fp12)
        counts["pages"] = len(context.pages)
        counts["sections"] = len(context.sections)
        counts["segments"] = len(segments)
    counts["candidates"] = len(load_candidates(path / "candidates.jsonl"))
    cards_path = path / "cards.jsonl"
    counts["cards"] = len(load_candidates(cards_path))
    pack_dir = path / "pack"
    counts["packed"] = bool(list(pack_dir.glob("*.ttfc.zip"))) if pack_dir.is_dir() else False

    submitted = _submitted_seg_ids(path)
    todo = [segment for segment in segments if segment.seg_id not in submitted]

    step = "unknown"
    next_step = "先调 status 看看当前状态。"
    if toc_gate != "approved":
        step = "toc_review"
        next_step = (
            "调 toc_review 拿到目录条目和目录页原文，逐条核对标题和页码；"
            "确认没问题后调 toc_gate（--approve --approved-by agent:<harness>:<模型>）"
            "——目录只到 pending，调了 toc_gate 才算过闸门 1。"
        )
    elif not (path / "sections.json").exists():
        step = "sections_build"
        next_step = "闸门 1 已过，调 sections_build 切章节。"
    elif todo:
        step = "extract_next"
        next_step = (
            f"还有 {len(todo)} 段没抽：调 extract_next 出题，"
            "答完用 extract_submit 交卷。"
        )
    elif not cards_path.exists():
        step = "cards_build"
        next_step = "抽完了，调 cards_build 挑卡、定位、质检。"
    elif not counts["packed"]:
        step = "pack"
        next_step = "调 pack 出卡包（内部分享版要先问用户要书页图还是文本块）。"
    else:
        step = "done"
        next_step = "做完了。可以调 browse_open 把包导进浏览库。"

    return {
        "ok": True,
        "fp12": str(book.get("fp12") or path.name),
        "title": str(book.get("title") or ""),
        "step": step,
        "gates": {"toc": toc_gate},
        "counts": counts,
        "remaining": len(todo),
        "next": next_step,
    }


# --------------------------------------------------------------------------- §2.3 目录（闸门 1）


def _ensure_toc(book_dir: Path) -> dict[str, Any]:
    """``toc.json`` 不在就建一个（目录判定是确定性的，不需要模型）。

    建出来的目录只到 ``pending``：闸门 1 要 agent 逐条核对后调
    :func:`toc_gate` 才写 ``approved``（不许自动批）。
    """
    path = book_dir / "toc.json"
    if path.exists():
        return _read_json(path)
    state = FlowState.load(book_dir)
    pdf = state.payload.get("pdf_path")
    try:
        build_toc(book_dir, pdf, approved_by=None)
    except Exception:  # noqa: BLE001 - 建不出来就把空壳给出去，后面 gate 会拦
        return {}
    return _read_json(path)


def toc_review(book_dir: str | Path) -> dict[str, Any]:
    """把目录摊给 agent 核对：条目 + 目录页原文 + 闸门 1 的状态。

    agent 对照目录页原文逐条核对标题和页码，按契约 §3 / §6 的判据决定；
    不通过就把问题讲给用户（v1 不提供改目录的工具）。
    """
    path = Path(book_dir)
    if not path.is_dir():
        return _fail(f"目录不存在：{path}")

    toc = _ensure_toc(path)
    if not toc:
        return _fail(
            "目录还没建出来：toc.json 不在，也建不出来。"
            "确认这本书的 PDF 在（bookcard flow book-add 时记下的路径还在不在）。"
        )

    entries = toc.get("entries") or []
    toc_pages = [
        {"page": int(row.get("page") or 0), "text": str(row.get("text") or "")}
        for row in common.read_jsonl(path / "pages.jsonl")
        if str(row.get("kind") or "") == "toc"
    ]
    return {
        "ok": True,
        "entries": entries,
        "toc_pages_text": toc_pages,
        "checks": toc.get("checks") or {},
        "gate": toc.get("gate") or {},
    }


def toc_gate(
    book_dir: str | Path,
    approve: bool,
    approved_by: str,
    notes: str = "",
    level: int = DEFAULT_EXTRACT_LEVEL,
) -> dict[str, Any]:
    """闸门 1 的拍板：批准 / 驳回。

    ``level`` = **抽词切章节用哪一层**（用户在第二步确认的层级），
    写进 ``toc.json`` 的 ``extract_level``；不传就是第 1 层。

    批准前会重跑契约 §6 的**确定性**判据（页码单调、覆盖无空洞、
    抽查标题在正文里）——有一条不过就不许标 approved。
    """
    path = Path(book_dir)
    problem = _check_agent_id(approved_by, "approved_by")
    if problem:
        return _fail(problem)

    toc = _ensure_toc(path)
    if not toc:
        return _fail("toc.json 不在：先调 toc_review 把目录建出来。")

    gate = toc.get("gate") if isinstance(toc.get("gate"), dict) else {}
    reasons = [str(reason) for reason in (gate.get("reasons") or [])]

    if approve:
        verdict = _recheck_toc_gate(path, toc, level=level)
        if not verdict["ok"]:
            return _fail(
                "闸门 1 的确定性判据没过，不许标 approved："
                + "；".join(verdict["reasons"]),
                reasons=verdict["reasons"],
                gate={
                    **gate,
                    "status": "rejected",
                    "reasons": verdict["reasons"],
                    "checks_passed": False,
                },
            )
        reasons = verdict["reasons"]
        checks_passed = True
    else:
        reasons = [notes] if notes else ["agent 驳回：目录有问题"]
        reasons.extend(str(reason) for reason in (gate.get("reasons") or []))
        # 驳回时没重跑判据，沿用建目录时记下的结果（老 toc.json 没这字段就当没过）。
        checks_passed = bool(gate.get("checks_passed"))

    new_gate = {
        "status": "approved" if approve else "rejected",
        "approved_by": approved_by if approve else "",
        "at": _iso(),
        "reasons": reasons,
        "notes": notes,
        "checks_passed": checks_passed,
    }
    toc["gate"] = new_gate
    if approve:
        # 抽词切章节用哪一层 —— 用户在第二步确认的层级（不选就是第 1 层）
        toc["extract_level"] = max(1, int(level or DEFAULT_EXTRACT_LEVEL))
    _write_json(path / "toc.json", toc)
    return {
        "ok": True,
        "gate": new_gate,
        "entries": len(toc.get("entries") or []),
        "extract_level": toc.get("extract_level", DEFAULT_EXTRACT_LEVEL),
    }


def _page_texts_for_gate(book_dir: Path) -> dict[int, str]:
    """闸门 1 要的页文字。

    优先用整本取字的 ``pages.jsonl``；**没有的话退到「看图目录」自己 OCR 过的缓存**
    （``<book>/tocv/ocr/*.json``）—— 判据 3 只抽查 3 条标题在不在页上，
    而看图目录那一步已经把用到的页 OCR 过了（扫描件整本取字要几小时，
    不该为了批个闸门把整本等完）。
    """
    from bookcard.engine import ocr as ocr_mod

    texts: dict[int, str] = {}
    pages_file = book_dir / "pages.jsonl"
    if pages_file.is_file():
        for row in common.read_jsonl(pages_file):
            number = row.get("page")
            text = str(row.get("text") or "")
            if number and text:
                texts[int(number)] = text
    if texts:
        return texts

    cache_dir = book_dir / "tocv" / ocr_mod.OCR_DIRNAME
    if cache_dir.is_dir():
        for path in sorted(cache_dir.glob("*.json")):
            number = _page_number_of_cache(path.name)
            if number is None:
                continue
            try:
                payload = common.read_json(path)
            except Exception:  # noqa: BLE001 - 缓存坏了就跳过
                continue
            if isinstance(payload, list):
                text = ocr_mod.page_text_of(payload)
                if text:
                    texts[number] = text
    if texts:
        return texts

    # 数字版还没取字 ⇒ 直接读 PDF 的文本层（不用等整本取字跑完）
    tocv_state = _read_json(book_dir / "tocv" / "tocv.json") if (
        book_dir / "tocv" / "tocv.json"
    ).is_file() else {}
    pdf_path = str(tocv_state.get("pdf_path") or "")
    if pdf_path and Path(pdf_path).is_file():
        from bookcard.engine import analysis as analysis_mod

        try:
            info = analysis_mod.analyze(pdf_path)
        except Exception:  # noqa: BLE001 - 读不出来就当没有文字
            info = {}
        for page in info.get("pages") or []:
            number = page.get("number")
            text = str(page.get("text") or "")
            if number and text:
                texts[int(number)] = text
    return texts


def _page_number_of_cache(name: str) -> int | None:
    """``p0007.json`` → 7。"""
    stem = Path(name).stem
    if not stem.startswith("p"):
        return None
    try:
        return int(stem[1:])
    except ValueError:
        return None


def _recheck_toc_gate(
    book_dir: Path, toc: dict[str, Any], level: int = DEFAULT_EXTRACT_LEVEL
) -> dict[str, Any]:
    """重跑契约 §6 闸门 1 的确定性判据（按用户确认的层级切章节）。"""
    from bookcard.engine import gate as gate_mod
    from bookcard.engine import sections as sections_mod

    entries = [entry for entry in (toc.get("entries") or []) if isinstance(entry, dict)]
    checks = toc.get("checks") if isinstance(toc.get("checks"), dict) else {}
    book = _read_json(book_dir / "book.json") if (book_dir / "book.json").is_file() else {}
    page_count = int(book.get("page_count") or 0)
    if not page_count:
        # 整本取字还没做（扫描件要几小时）⇒ 用「看图目录」记的页数
        tocv_state = _read_json(book_dir / "tocv" / "tocv.json") if (
            book_dir / "tocv" / "tocv.json"
        ).is_file() else {}
        page_count = int(tocv_state.get("page_count") or 0)
    page_texts = _page_texts_for_gate(book_dir)
    first_body_page = int(checks.get("first_body_page") or 1)
    covered = checks.get("covered_pages") or [first_body_page, first_body_page]
    last_body_page = int(covered[1])

    section_list = sections_mod.sections_for_level(
        entries, level, first_body_page, last_body_page
    )
    verdict = gate_mod.evaluate(
        entries,
        section_list,
        page_count,
        first_body_page,
        last_body_page,
        page_texts,
        "agent:recheck",
    )
    reasons = list(verdict.get("reasons") or [])
    return {"ok": verdict.get("status") == "approved", "reasons": reasons, "level": level}


# --------------------------------------------------------------------------- §2.4 切章节


def sections_build(book_dir: str | Path) -> dict[str, Any]:
    """切章节（闸门 1 没过就拒绝）。"""
    path = Path(book_dir)
    try:
        summary = build_sections_file(path)
    except GateNotApprovedError as exc:
        return _fail(f"闸门 1 没通过，不许切章节：{exc}")
    except (PipelineError, OSError) as exc:
        return _fail(f"切章节失败：{exc}")
    return {"ok": True, **summary}


# --------------------------------------------------------------------------- §2.1 抽词


def _segments_of(context: BookContext) -> list[Segment]:
    return build_segments(context.pages, context.sections, context.fp12)


def _extract_prompt() -> tuple[Any, str]:
    """抽词提示词（包内 ``prompts/extract_v1.md``）。"""
    path = prompts.prompt_path(prompts.EXTRACT_PROMPT_NAME)
    prompt = parse_prompt(path)
    return prompt, prompt.version


def _parse_levels(level: str | list[int] | int) -> list[int] | None:
    """``"1,2"`` / ``[1, 2]`` / ``1`` → ``[1, 2]``；写不对返回 None。"""
    if isinstance(level, int):
        return [max(1, level)]
    if isinstance(level, (list, tuple)):
        items = list(level)
    else:
        items = str(level).replace("，", ",").split(",")
    out: list[int] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        try:
            number = int(text)
        except ValueError:
            return None
        if number < 1:
            return None
        if number not in out:
            out.append(number)
    return out or None


def extract_next(
    book_dir: str | Path,
    max_segments: int = DEFAULT_MAX_SEGMENTS,
    level: str | list[int] | None = None,
) -> dict[str, Any]:
    """出抽词题：把页面文字套进提示词，交给 agent。

    ``level`` = **词库按目录的第几层分**（可以给多层，比如 ``"1,2"``）。
    抽词是按页走的，所以这一步改层级不用重抽——它只是把章节（= 词库）
    按新层级重切一遍。

    **已出题未交卷**的段不重复出，除非超过 30 分钟没交（租约，状态存
    ``agentflow.json``）。
    """
    path = Path(book_dir)
    if level is not None:
        levels = _parse_levels(level)
        if levels is None:
            return _fail("level 写得不对：要层级数字，比如 1。")
        if len(levels) > 1:
            return _fail(
                "一次只能选一层（目录结构要唯一）：给了 "
                + ",".join(str(x) for x in levels)
                + "。换个层级再来一次。"
            )
        try:
            reslice = reslice_sections(path, levels)
        except Exception as exc:  # noqa: BLE001
            return _fail(f"按层级 {levels} 重切章节失败：{exc}")
        if not reslice.get("sections"):
            return _fail(f"按层级 {levels} 切不出章节。")
    try:
        context = _context(path)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"读工作目录失败：{exc}")

    segments = _segments_of(context)

    submitted = _submitted_seg_ids(path)
    state = FlowState.load(path)

    prompt, version = _extract_prompt()
    out: list[dict[str, Any]] = []
    for segment in segments:
        if segment.seg_id in submitted:
            continue
        if state.lease_is_live("extract", segment.seg_id):
            continue
        out.append(
            {
                "seg_id": segment.seg_id,
                "page": segment.page,
                "section_title": segment.section_title,
                "user": prompt.render(
                    book_title=context.title,
                    subject=context.subject,
                    section_title=segment.section_title,
                    page=segment.page,
                    text=segment.text,
                ),
            }
        )
        if len(out) >= max(0, int(max_segments)):
            break

    for item in out:
        state.issue("extract", item["seg_id"])
    if out:
        state.save()

    done = len([segment for segment in segments if segment.seg_id in submitted])
    return {
        "ok": True,
        "prompt_version": version,
        "system": prompt.system,
        "segments": out,
        "remaining": max(0, len(segments) - done),
        "done": done,
    }


def extract_submit(
    book_dir: str | Path,
    seg_id: str,
    output: str,
    extractor: str,
) -> dict[str, Any]:
    """交抽词的卷：解析 → 落位 → 写 ``candidates.jsonl``。

    ``extractor`` 形如 ``agent:<harness>:<模型名>:<提示词版本>``，不合格式就拒收。
    解析失败返回 ``{"ok": False, "error": …, "retry": True}``——agent 重答这一段。
    """
    path = Path(book_dir)
    problem = _check_extractor(extractor)
    if problem:
        return _fail(problem)

    try:
        context = _context(path)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"读工作目录失败：{exc}")

    segments = {segment.seg_id: segment for segment in _segments_of(context)}
    segment = segments.get(seg_id)
    if segment is None:
        return _fail(
            f"seg_id {seg_id!r} 不在这本书的段里："
            f"重新调 extract_next 拿这一版的 seg_id"
        )

    try:
        keywords: list[Keyword] = parse_keywords(output)
    except ParseError as exc:
        return _fail(str(exc), retry=True)

    items, not_in_text = place_items(segment.text, keywords)
    _upsert_candidate_row(
        path, candidate_row(segment, extractor, items, not_in_text)
    )

    state = FlowState.load(path)
    state.release("extract", segment.seg_id)
    state.save()

    return {
        "ok": True,
        "seg_id": segment.seg_id,
        "items": len(items),
        "dropped": {"not_in_text": not_in_text},
    }


# ----------------------------------------------------------------- §2.4 挑卡定位质检


def _pdf_for(book_dir: Path) -> str | None:
    """这本书的 PDF 路径（``book_add`` 记在 ``agentflow.json`` 里）。"""
    state = FlowState.load(book_dir)
    pdf = state.payload.get("pdf_path")
    return str(pdf) if pdf else None


def cards_build(book_dir: str | Path) -> dict[str, Any]:
    """挑卡 → 定位 → 质检（契约 §5 / §6）。

    **全量抽完才能调**：还有段没交卷就拒绝。
    """
    path = Path(book_dir)
    try:
        context = _context(path)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"读工作目录失败：{exc}")

    segments = _segments_of(context)
    submitted = _submitted_seg_ids(path)
    todo = [segment.seg_id for segment in segments if segment.seg_id not in submitted]
    if todo:
        return _fail(
            f"还有 {len(todo)} 段没抽完，不许挑卡："
            "先调 extract_next（--mode full）出题、extract_submit 交卷。"
        )

    work = WorkDir(path)
    try:
        select_summary = select(work)
        locate_summary = locate(work, _pdf_for(path))
        report = audit(work)
    except PipelineError as exc:
        return _fail(f"挑卡 / 定位 / 质检失败：{exc}")
    except Exception as exc:  # noqa: BLE001
        return _fail(f"挑卡 / 定位 / 质检失败：{type(exc).__name__}: {exc}")

    return {
        "ok": True,
        "select": select_summary,
        "locate": locate_summary,
        "quality": report,
    }


# --------------------------------------------------------------------------- §2.4 打包


def pack(
    book_dir: str | Path,
    edition: str = EDITION_PRIVATE,
    scene: str | None = None,
    scene_chosen_by: str | None = None,
) -> dict[str, Any]:
    """打包。

    **内部分享版（``private``）默认走图片场景**（the maintainer 2026-11-06 拍板）：
    ``scene`` 不再必填，不传就用 :data:`bookcard.pipeline.scene.DEFAULT_SCENE`，
    不用问用户。

    ``scene_chosen_by`` 只认 ``"user"``（harness 当场问过用户）和 ``"default"``
    （用的默认场景），其他值直接报错。没传时：传了 ``scene`` 也记 ``"default"``
    ——只有 harness 当场问过用户才该传 ``"user"``。公开版保持 ``"cli"`` 不变。
    """
    path = Path(book_dir)
    if edition not in EDITIONS:
        return _fail(f"edition 只能是 {' 或 '.join(EDITIONS)}（现在是 {edition!r}）")
    if scene is not None and scene not in SCENES:
        return _fail(f"scene 只能是 {SCENE_TEXT} 或 {SCENE_IMAGE}（现在是 {scene!r}）")
    if scene_chosen_by is not None and scene_chosen_by not in SCENE_CHOSEN_BY:
        return _fail(
            f"scene_chosen_by 只能是 {' 或 '.join(SCENE_CHOSEN_BY)}"
            f"（现在是 {scene_chosen_by!r}）"
        )

    if edition == EDITION_PRIVATE:
        chosen_by = scene_chosen_by or SCENE_CHOSEN_BY_DEFAULT
    else:
        chosen_by = SCENE_CHOSEN_BY_CLI

    return _pack(path, edition, scene, chosen_by)


def pack_from_cli(
    book_dir: str | Path,
    edition: str = EDITION_PRIVATE,
    scene: str | None = None,
) -> dict[str, Any]:
    """命令行打包入口：显式 ``--scene`` 记 ``"cli"``（契约 §7.1）。"""
    path = Path(book_dir)
    if edition not in EDITIONS:
        return _fail(f"edition 只能是 {' 或 '.join(EDITIONS)}（现在是 {edition!r}）")
    if scene is not None and scene not in SCENES:
        return _fail(f"scene 只能是 {SCENE_TEXT} 或 {SCENE_IMAGE}（现在是 {scene!r}）")

    if edition == EDITION_PRIVATE:
        chosen_by = SCENE_CHOSEN_BY_CLI if scene else SCENE_CHOSEN_BY_DEFAULT
    else:
        chosen_by = SCENE_CHOSEN_BY_CLI

    return _pack(path, edition, scene, chosen_by)


def _pack(
    path: Path,
    edition: str,
    scene: str | None,
    chosen_by: str,
) -> dict[str, Any]:
    work = WorkDir(path)
    try:
        summary = pipeline_pack(
            work,
            edition=edition,
            scene=scene,
            pdf=_pdf_for(path),
            scene_chosen_by=chosen_by,
        )
    except PackError as exc:
        return _fail(f"打包失败：{exc}")
    except PipelineError as exc:
        return _fail(f"打包失败：{exc}")

    book = _read_json(path / "book.json")
    return {
        "ok": True,
        **summary,
        "book": {
            "fp12": str(book.get("fp12") or ""),
            "scene_mode": book.get("scene_mode"),
            "scene_suggested": book.get("scene_suggested"),
            "scene_chosen_by": book.get("scene_chosen_by"),
        },
        "output": _export_pack(path, summary, book),
    }


def _export_pack(path: Path, summary: dict[str, Any], book: dict) -> dict[str, Any]:
    """把卡包复制一份到「下载」文件夹。

    工作目录里那份是**状态**（browse_open 从那儿读），用户真正要拿走的是
    「下载」里这份 —— 找得到，也能直接拖进 App。
    """
    src = summary.get("pack")
    if not src:
        return {}
    source = Path(str(src))
    if not source.exists():
        return {}
    try:
        from .paths import output_dir, safe_name

        title = str(book.get("title") or book.get("name") or "")
        fp12 = str(book.get("fp12") or "")
        edition = str(summary.get("edition") or "")
        stem = safe_name(title, fp12)
        if edition == EDITION_PUBLIC:
            stem = f"{stem}.public"
        target = output_dir() / f"{stem}.ttfc.zip"
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return {"pack": str(target), "dir": str(target.parent)}
    except Exception as exc:  # 导出失败不该让打包失败
        return {"error": str(exc)}
