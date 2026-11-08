"""``bookcard toc``：写 ``toc.json``（目录 + 闸门 1，契约 §3、§6）。

目录来源优先级：**书签 → 印刷目录 → 版式（大字号标题）**。
印刷目录的页码是印刷页码，要靠偏移量换算成物理页号；偏移量先取「印刷页码 −
物理页号」的众数，再抽查 3 个条目标题在正文里的实际位置校准。

闸门 1 没过（``rejected``）就不许切章节 —— ``sections`` 那一步会硬拦。
``build_toc`` 只出 ``pending``（还没人核对过），``approved`` 只能由确认动作
（agentflow 的 ``toc_gate``）写。
"""

from __future__ import annotations

from pathlib import Path

from bookcard.engine import analysis, common, gate, tocparse
from bookcard.engine import sections as sections_mod


class MissingPdfError(RuntimeError):
    """找不到这本书的本地 PDF（目录要读页面几何）。"""


class GateNotApprovedError(RuntimeError):
    """闸门 1 没通过，不许切章节。"""


#: 老版本自动批目录时留在 ``gate.approved_by`` 里的标记。
#:
#: 这个值表示「没人核对过」——``build_sections_file`` 见到它照样拒绝切章节。
UNREVIEWED_APPROVER = "agent:unreviewed"


def calibrate_offset(
    entries: list[dict],
    page_texts: dict[int, str],
    offset: int | None,
) -> tuple[int | None, list[str]]:
    """抽查条目标题，校准印刷页码与物理页号的偏移。

    返回 ``(新偏移, 说明列表)``。抽查的标题在正文里的实际位置如果一致地
    指向另一个偏移，就采用它（并把条目的物理页号整体挪过去）。
    """
    if offset is None or not entries:
        return offset, []

    samples = gate._pick_samples(entries)
    votes: list[int] = []
    details: list[str] = []
    for entry in samples:
        expected = entry["page"]
        found = gate.find_title_page(entry["title"], page_texts, expected)
        if found is None:
            details.append(f"抽查「{entry['title']}」：第 {expected} 页及前后都没找到标题")
            continue
        details.append(f"抽查「{entry['title']}」：在第 {found} 页")
        printed = entry.get("printed_page")
        if printed is not None:
            votes.append(printed - found)

    if votes and len(set(votes)) == 1 and votes[0] != offset:
        details.append(
            f"偏移校准：{offset} → {votes[0]}（按抽查到的实际位置）"
        )
        return votes[0], details
    details.append(f"偏移校准：沿用 {offset}（抽查位置与目录一致）")
    return offset, details


def _pick_source(info: dict, pdf_path: Path) -> tuple[str, list[dict]]:
    """按「书签 → 印刷目录 → 版式」的优先级挑目录来源。"""
    bookmarks = tocparse.parse_bookmarks(pdf_path)
    if len(bookmarks) >= 3:
        return "bookmarks", bookmarks
    if info["toc_entries"]:
        return "printed", list(info["toc_entries"])
    layout = tocparse.parse_layout_toc(
        info["pages"],
        info["first_body_page"],
        info["body_font"],
        # OCR 页的字号是行框高度估的，噪声大 ⇒ 阈值放高（只认大标题）。
        ratio=(
            tocparse.OCR_LARGE_FONT_RATIO
            if info.get("text_origin") == "ocr"
            else tocparse.LARGE_FONT_RATIO
        ),
    )
    if layout:
        return "layout", layout
    return "none", []


def _is_ocr_book(page_rows: list[dict]) -> bool:
    """这本书的文字是不是 OCR 取的（契约 §2.2）。"""
    return any(str(row.get("text_origin") or "") == "ocr" for row in page_rows)


def build_toc(
    work_dir: str | Path,
    pdf_path: str | Path | None = None,
    approved_by: str | None = "agent:agent",
) -> dict:
    """生成 ``toc.json``（目录 + checks + 闸门 1）。

    ``approved_by=None`` = **还没有人核对过**：判据全过也只写 ``pending``，
    ``approved_by`` 留空；判据不过照旧写 ``rejected``。只有显式给出
    ``approved_by``（内部 CLI ``bookcard toc --approved-by`` 是显式确认）
    才可能直接写 ``approved``。

    ``gate.checks_passed`` 记的是「四条判据是否全过」，和 ``status`` 分开看：
    ``pending`` + ``checks_passed=True`` = 目录本身没问题，就等人拍板。
    """
    work_dir = Path(work_dir)
    book = common.read_json(work_dir / "book.json")
    page_rows = common.read_jsonl(work_dir / "pages.jsonl")
    page_texts = {row["page"]: row["text"] for row in page_rows}
    page_count = book.get("page_count") or len(page_rows)

    pdf = common.resolve_pdf(work_dir, book, pdf_path)
    if pdf is None:
        raise MissingPdfError(
            f"找不到 {work_dir} 对应的 PDF：目录要读页面几何。"
            "用 --pdf 指定，或者把书放进 ~/.cache/bookcard/books/"
        )

    if _is_ocr_book(page_rows):
        # 扫描件：PDF 没有文本层 ⇒ 用已经算好的 OCR 产物重放（不重跑 OCR）。
        info = analysis.analyze_ocr_workdir(work_dir)
    else:
        info = analysis.analyze(pdf)
    source, entries = _pick_source(info, pdf)

    # ---- 印刷目录要校准印刷页码 → 物理页号的偏移 ----
    calibration: list[str] = []
    offset = info["offset"]
    first_body_page = info["first_body_page"]
    last_body_page = info["last_body_page"]
    if source == "printed":
        new_offset, calibration = calibrate_offset(entries, page_texts, offset)
        if new_offset is not None and offset is not None and new_offset != offset:
            shift = offset - new_offset
            entries = [{**entry, "page": entry["page"] + shift} for entry in entries]
            first_body_page += shift
            offset = new_offset
    elif entries:
        first_body_page = min(entry["page"] for entry in entries)

    # ---- 切章节（闸门 1 的判据 2 就是按这个结果查的）----
    # 层级由用户在第二步（目录确认）时选；这里先用默认的第 1 层做自检。
    level = DEFAULT_EXTRACT_LEVEL
    section_list = sections_mod.sections_for_level(
        entries, level, first_body_page, last_body_page
    )
    calibration.insert(0, f"目录来源：{source}；切分粒度：第 {level} 层")

    gate_result = gate.evaluate(
        entries,
        section_list,
        page_count,
        first_body_page,
        last_body_page,
        page_texts,
        approved_by or "",
        extra_reasons=calibration,
    )
    checks_passed = gate_result.get("status") == "approved"
    if approved_by is None:
        # 没人核对过：判据全过也只写 pending，approved 只能由确认动作写。
        gate_result["status"] = "pending" if checks_passed else "rejected"
        gate_result["approved_by"] = ""
    gate_result["checks_passed"] = checks_passed

    problems = sections_mod.check_contiguity(section_list, first_body_page, last_body_page)
    gaps = [[start, end] for kind, start, end in problems if kind == "gap"]
    pages = [entry["page"] for entry in entries]
    monotonic = all(later >= earlier for earlier, later in zip(pages, pages[1:], strict=False))

    covered = (
        [section_list[0]["page_start"], section_list[-1]["page_end"]]
        if section_list
        else [first_body_page, first_body_page]
    )

    payload = {
        "source": source,
        "entries": [
            {"level": entry["level"], "title": entry["title"], "page": entry["page"]}
            for entry in entries
        ],
        "checks": {
            "pages_monotonic": monotonic,
            "first_body_page": first_body_page,
            "covered_pages": covered,
            "gaps": gaps,
        },
        "gate": gate_result,
    }
    common.write_json(work_dir / "toc.json", payload)

    return {
        "source": source,
        "entries": len(entries),
        "level": level,
        "sections": len(section_list),
        "first_body_page": first_body_page,
        "last_body_page": last_body_page,
        "offset": offset,
        "gate": gate_result["status"],
        "reasons": gate_result["reasons"],
        "toc_path": str(work_dir / "toc.json"),
    }


#: 抽词切章节的**默认层级** —— 用户在第二步没选就用这个。
DEFAULT_EXTRACT_LEVEL = 1


def extract_level_of(toc: dict) -> int:
    """用户确认的抽取层级（``toc.json`` 的 ``extract_level``）；没选就是第 1 层。"""
    try:
        level = int((toc or {}).get("extract_level") or DEFAULT_EXTRACT_LEVEL)
    except (TypeError, ValueError):
        level = DEFAULT_EXTRACT_LEVEL
    return max(1, level)


def reslice_sections(work_dir: str | Path, levels: list[int]) -> dict:
    """按给定层级重切章节，写 ``sections.json``。

    抽词是按页走的，所以换层级不用重抽 —— 只是把「词库怎么分」重切一遍。
    闸门 1 没过不许切（还是那条规矩）。
    """
    work_dir = Path(work_dir)
    toc = common.read_json(work_dir / "toc.json")
    gate_result = toc.get("gate") or {}
    if gate_result.get("status") != "approved":
        reasons = "；".join(gate_result.get("reasons", [])) or "没有理由"
        raise GateNotApprovedError(
            f"闸门 1 没通过（{gate_result.get('status')}），不许切章节：{reasons}"
        )
    approved_by = str(gate_result.get("approved_by") or "")
    if not approved_by or approved_by == UNREVIEWED_APPROVER:
        raise GateNotApprovedError(
            "闸门 1 没通过（approved_by 为空），不许切章节：目录还没有人核对过"
        )
    checks = toc.get("checks") or {}
    first_body_page = int(checks.get("first_body_page", 1))
    covered = checks.get("covered_pages") or [first_body_page, first_body_page]
    last_body_page = int(covered[1])
    entries = toc.get("entries") or []
    body_entries = [
        e for e in entries if str(e.get("kind") or "body") != "front" and e.get("page")
    ]
    if body_entries:
        first_body_page = min(int(e["page"]) for e in body_entries)
    section_list = sections_mod.sections_for_level(
        entries, levels[0], first_body_page, last_body_page
    )
    rows = sections_mod.to_sections_file(section_list)
    common.write_json(work_dir / "sections.json", rows)
    return {
        "levels": levels,
        "sections": len(rows),
        "first_body_page": first_body_page,
        "last_body_page": last_body_page,
    }


def build_sections_file(work_dir: str | Path) -> dict:
    """生成 ``sections.json``。

    闸门 1 没通过就硬拦（契约 §0：「闸门不跳」）。``status == "approved"``
    之外，**没人确认过**（``approved_by`` 为空，或者是老版本自动批留下的
    ``agent:unreviewed``）也照样拦——不许跳过闸门 1 直接切章节。
    """
    work_dir = Path(work_dir)
    toc = common.read_json(work_dir / "toc.json")
    gate_result = toc.get("gate") or {}
    if gate_result.get("status") != "approved":
        reasons = "；".join(gate_result.get("reasons", [])) or "没有理由"
        raise GateNotApprovedError(
            f"闸门 1 没通过（{gate_result.get('status')}），不许切章节：{reasons}"
        )
    approved_by = str(gate_result.get("approved_by") or "")
    if not approved_by or approved_by == UNREVIEWED_APPROVER:
        raise GateNotApprovedError(
            f"闸门 1 没通过（approved_by={approved_by or '空'}），不许切章节："
            "目录还没有人核对过，要先调 toc_gate 批准"
        )

    checks = toc.get("checks") or {}
    first_body_page = int(checks.get("first_body_page", 1))
    covered = checks.get("covered_pages") or [first_body_page, first_body_page]
    last_body_page = int(covered[1])

    entries = toc.get("entries") or []
    # 前置页（目录页、前言、译者序…）不是正文：正文从第一条非前置条目开始。
    # （契约：「目录页不参与提取」——目录页既不切章节，也不抽词。）
    front_entries = [
        e for e in entries if str(e.get("kind") or "body") == "front" and e.get("page")
    ]
    body_entries = [
        e for e in entries if str(e.get("kind") or "body") != "front" and e.get("page")
    ]
    if front_entries and body_entries:
        # 有前置页 → 正文从第一条正文条目开始（目录页那几页不算正文）
        first_body_page = min(int(e["page"]) for e in body_entries)
    level = extract_level_of(toc)
    section_list = sections_mod.sections_for_level(
        entries, level, first_body_page, last_body_page
    )
    rows = sections_mod.to_sections_file(section_list)
    common.write_json(work_dir / "sections.json", rows)

    return {
        "level": level,
        "sections": len(rows),
        "first_body_page": first_body_page,
        "last_body_page": last_body_page,
        "sections_path": str(work_dir / "sections.json"),
        "sample": rows[:5],
    }
