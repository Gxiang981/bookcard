"""bookcard.review：卡片审核 HTML（agent 判定 + 人工抽检导出）。

口径唯一来源是 ``docs/contract-v1.md`` §6「卡片判定」：

- 每张卡四个判定：``kw``（accept / boundary / reject）、``evidence``（ok / missing /
  wrong）、``expl``（2 / 1 / 0）、``fatal``（true / false）。
- 合格 = ``kw == accept`` 且 ``evidence == ok`` 且 ``expl >= 1`` 且 ``fatal == false``。
- 审核记录 ``review/*.jsonl``：``{"card_id", "kw", "evidence", "expl", "fatal",
  "note", "reviewer", "at"}``；人工判定覆盖 agent 判定。

本模块只做「读数据 → 拼 payload → 渲染一个自包含 HTML」。HTML 里 CSS / JS / 数据
全部内联，不联网、不依赖 CDN；用户在浏览器里改判定、写备注、记漏标，导出同格式
的 JSONL。浏览器本地（localStorage）存进度，刷新不丢。

三个文件的口径都在契约里：

- ``cards.jsonl``（§5）或试抽阶段的临时卡：被审核的卡。
- ``review/*.jsonl``（§6）：agent 的判定（``reviewer: "agent:…"``）。
- ``pages.jsonl``（§2）：逐页文字，用来显示「所在页」并高亮依据句和关键词。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from bookcard._review_assets import BODY, CSS, JS_APP, JS_CORE

#: §6 卡片判定的取值集合。
KW_VERDICTS: tuple[str, ...] = ("accept", "boundary", "reject")
EVIDENCE_VERDICTS: tuple[str, ...] = ("ok", "missing", "wrong")
EXPL_VERDICTS: tuple[int, ...] = (2, 1, 0)

#: 默认人工审核人（导出时 reviewer 写成 ``human:gxiang``）。
DEFAULT_REVIEWER = "gxiang"

#: 默认抽检张数。0 / None = 全部。
DEFAULT_SAMPLE = 30

#: 抽检分层用的判定档位。
PASS = "pass"
FAIL = "fail"
NONE = "none"

_SEG_PAGE_RE = re.compile(r":p(\d+)$")


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


@dataclass
class Card:
    """一张待审的卡（口径统一后的形状，不直接暴露 JSONL 原文）。"""

    card_id: str
    front: str
    back: str = ""
    type: str = ""
    sid: str = ""
    section_title: str = ""
    tags: list[str] = field(default_factory=list)
    page: int | None = None
    printed_page: str = ""
    kw_start: int | None = None
    kw_end: int | None = None
    evidence: str = ""
    evidence_start: int | None = None
    evidence_end: int | None = None
    agent: dict[str, Any] | None = None
    human_prev: dict[str, Any] | None = None


@dataclass
class Verdict:
    """§6 的一条判定。"""

    card_id: str
    kw: str = ""
    evidence: str = ""
    expl: int | None = None
    fatal: bool | None = None
    note: str = ""
    reviewer: str = ""
    at: str = ""

    def as_row(self) -> dict[str, Any]:
        """导出成 §6 的审核记录。"""
        return {
            "card_id": self.card_id,
            "kw": self.kw,
            "evidence": self.evidence,
            "expl": self.expl,
            "fatal": self.fatal,
            "note": self.note,
            "reviewer": self.reviewer,
            "at": self.at,
        }


# --------------------------------------------------------------------------- #
# 读文件
# --------------------------------------------------------------------------- #


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """读 JSONL，返回（记录列表，告警列表）。坏行跳过并告警，不中断。"""
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows, warnings
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            warnings.append(f"{path.name}:{lineno} 不是合法 JSON，已跳过（{exc.msg}）")
            continue
        if not isinstance(obj, dict):
            warnings.append(f"{path.name}:{lineno} 不是 JSON 对象，已跳过")
            continue
        rows.append(obj)
    return rows, warnings


def _as_bool(value: Any) -> bool | None:
    """把 ``true`` / ``"true"`` / ``"是"`` 归一成布尔值；认不出就 None。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "1", "是"):
            return True
        if low in ("false", "no", "0", "否"):
            return False
    if isinstance(value, int):
        return bool(value)
    return None


def _as_expl(value: Any) -> int | None:
    """``expl`` 只认 2 / 1 / 0；认不出就 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value in EXPL_VERDICTS else None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped in ("2", "1", "0"):
            return int(stripped)
    return None


def _as_int(value: Any) -> int | None:
    """把能转成整数的值转出来；认不出就 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lstrip("-").isdigit():
            return int(stripped)
    return None


def load_pages(path: Path) -> tuple[dict[int, str], list[str]]:
    """读 ``pages.jsonl``，返回（页号 → 正文，告警列表）。"""
    rows, warnings = read_jsonl(path)
    pages: dict[int, str] = {}
    for row in rows:
        page = _as_int(row.get("page"))
        text = row.get("text")
        if page is None:
            warnings.append(f"{path.name} 里有记录缺 page，已跳过")
            continue
        if not isinstance(text, str):
            warnings.append(f"{path.name} 第 {page} 页缺 text，已跳过")
            continue
        pages[page] = text
    return pages, warnings


def load_sections(path: Path) -> dict[str, str]:
    """读 ``sections.json``，返回（sid → 标题）。文件不在就返回空。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, list):
        return {}
    out: dict[str, str] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("sid") or "").strip()
        if sid:
            out[sid] = str(item.get("title") or "")
    return out


def _normalize_card(
    row: dict[str, Any],
    index: int,
    pages: dict[int, str],
    warnings: list[str],
) -> Card | None:
    """把一行原始卡（§5 的卡片或 §4 的候选）归一成 :class:`Card`。"""
    source = row.get("source")
    source = source if isinstance(source, dict) else {}

    front = row.get("front")
    if not isinstance(front, str) or not front.strip():
        front = row.get("surface")
    if not isinstance(front, str) or not front.strip():
        warnings.append(f"第 {index + 1} 张卡没有 front / surface，已跳过")
        return None
    front = front.strip()

    back = row.get("back")
    if not isinstance(back, str):
        back = row.get("explanation")
    if not isinstance(back, str):
        back = ""

    card_type = str(row.get("type") or "").strip()

    sid = str(row.get("sid") or "").strip()

    page = _as_int(source.get("page"))
    if page is None:
        page = _as_int(row.get("page"))
    if page is None:
        seg_id = str(source.get("seg_id") or row.get("seg_id") or "")
        match = _SEG_PAGE_RE.search(seg_id)
        if match:
            page = int(match.group(1))

    printed_page = source.get("printed_page")
    if not isinstance(printed_page, str):
        printed_page = row.get("printed_page")
    printed_page = str(printed_page) if printed_page else ""

    start = _as_int(source.get("start"))
    end = _as_int(source.get("end"))
    if start is None or end is None:
        start = _as_int(row.get("start"))
        end = _as_int(row.get("end"))

    evidence = ""
    evidence_start: int | None = None
    evidence_end: int | None = None
    raw_evidence = source.get("evidence")
    if raw_evidence is None:
        raw_evidence = row.get("evidence")
    if isinstance(raw_evidence, str):
        evidence = raw_evidence
    elif isinstance(raw_evidence, dict):
        evidence_start = _as_int(raw_evidence.get("start"))
        evidence_end = _as_int(raw_evidence.get("end"))

    tags = row.get("tags")
    tags = [str(tag) for tag in tags] if isinstance(tags, list) else []

    card_id = str(row.get("card_id") or "").strip()
    if not card_id:
        digest = hashlib.sha1(front.encode("utf-8")).hexdigest()[:8]
        card_id = f"{sid}:{digest}" if sid else f"card-{index + 1}:{digest}"

    card = Card(
        card_id=card_id,
        front=front,
        back=back.strip(),
        type=card_type,
        sid=sid,
        tags=tags,
        page=page,
        printed_page=printed_page,
        kw_start=start,
        kw_end=end,
        evidence=evidence,
        evidence_start=evidence_start,
        evidence_end=evidence_end,
    )

    _resolve_offsets(card, pages, warnings)
    return card


def _resolve_offsets(card: Card, pages: dict[int, str], warnings: list[str]) -> None:
    """把关键词 / 依据句的偏移对到页正文上；对不上就告警并就地重找。"""
    page_text = pages.get(card.page) if card.page is not None else None
    if page_text is None:
        if card.page is not None:
            warnings.append(
                f"卡 {card.card_id} 的第 {card.page} 页不在 pages.jsonl 里，无法定位原文"
            )
        return

    if card.kw_start is not None and card.kw_end is not None:
        ok = (
            0 <= card.kw_start < card.kw_end <= len(page_text)
            and page_text[card.kw_start : card.kw_end] == card.front
        )
        if not ok:
            warnings.append(
                f"卡 {card.card_id} 的关键词偏移与第 {card.page} 页正文不符，已重新定位"
            )
            card.kw_start = card.kw_end = None
    if card.kw_start is None:
        index = page_text.find(card.front)
        if index >= 0:
            card.kw_start = index
            card.kw_end = index + len(card.front)

    if card.evidence_start is not None and card.evidence_end is not None:
        ok = (
            0 <= card.evidence_start < card.evidence_end <= len(page_text)
            and page_text[card.evidence_start : card.evidence_end]
        )
        if not ok:
            warnings.append(f"卡 {card.card_id} 的依据句偏移越界，已重新定位")
            card.evidence_start = card.evidence_end = None
        else:
            card.evidence = page_text[card.evidence_start : card.evidence_end]
    if card.evidence_start is None and card.evidence:
        index = page_text.find(card.evidence)
        if index >= 0:
            card.evidence_start = index
            card.evidence_end = index + len(card.evidence)
        else:
            warnings.append(f"卡 {card.card_id} 的依据句在第 {card.page} 页正文里找不到")


def load_cards(
    work_dir: Path,
    pages: dict[int, str],
) -> tuple[list[Card], list[str], str]:
    """读被审核的卡，返回（卡片列表，告警列表，用到的文件名）。

    优先 ``cards.jsonl``（§5）；没有就用试抽阶段的临时卡
    （``trial_cards.jsonl`` 或 ``trial.json`` 里的 ``cards``）。
    """
    warnings: list[str] = []

    candidates: list[tuple[Path, str]] = [
        (work_dir / "cards.jsonl", "cards.jsonl"),
        (work_dir / "trial_cards.jsonl", "trial_cards.jsonl"),
    ]
    for path, label in candidates:
        if path.exists():
            rows, more = read_jsonl(path)
            warnings += more
            cards = _cards_from_rows(rows, pages, warnings)
            return cards, warnings, label

    trial_path = work_dir / "trial.json"
    if trial_path.exists():
        try:
            data = json.loads(trial_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"trial.json 读不了（{exc}），没有可审的卡")
            return [], warnings, ""
        rows: list[dict[str, Any]] = []
        if isinstance(data, dict):
            raw = data.get("cards")
            if isinstance(raw, list):
                rows = [item for item in raw if isinstance(item, dict)]
        elif isinstance(data, list):
            rows = [item for item in data if isinstance(item, dict)]
        if not rows:
            warnings.append("trial.json 里没有 cards 数组，没有可审的卡")
            return [], warnings, "trial.json"
        cards = _cards_from_rows(rows, pages, warnings)
        return cards, warnings, "trial.json"

    warnings.append(
        f"{work_dir} 里没有 cards.jsonl / trial_cards.jsonl / trial.json，没有可审的卡"
    )
    return [], warnings, ""


def _cards_from_rows(
    rows: list[dict[str, Any]],
    pages: dict[int, str],
    warnings: list[str],
) -> list[Card]:
    """把原始行变成 :class:`Card` 列表。"""
    cards: list[Card] = []
    for index, row in enumerate(rows):
        card = _normalize_card(row, index, pages, warnings)
        if card is not None:
            cards.append(card)
    return cards


def load_verdicts(work_dir: Path) -> tuple[dict[str, Verdict], dict[str, Verdict], list[str]]:
    """读 ``review/*.jsonl``，返回（agent 判定，人工判定，告警列表）。

    同一张卡有多条记录时保留 ``at`` 最新的一条。人工判定（``reviewer`` 以
    ``human:`` 开头）单独存，不覆盖 agent 判定——导出时人工覆盖 agent。
    """
    warnings: list[str] = []
    review_dir = work_dir / "review"
    if not review_dir.is_dir():
        warnings.append(f"{work_dir} 里没有 review/ 目录，没有 agent 判定")
        return {}, {}, warnings

    agent: dict[str, Verdict] = {}
    human: dict[str, Verdict] = {}
    for path in sorted(review_dir.glob("*.jsonl")):
        if path.name.startswith("."):
            continue
        rows, more = read_jsonl(path)
        warnings += more
        for row in rows:
            card_id = str(row.get("card_id") or "").strip()
            if not card_id:
                warnings.append(f"{path.name} 里有记录缺 card_id，已跳过")
                continue
            reviewer = str(row.get("reviewer") or "").strip()
            kw = str(row.get("kw") or "").strip()
            if kw and kw not in KW_VERDICTS:
                warnings.append(f"{path.name} 的 {card_id}：kw={kw!r} 不在约定内")
            ev = str(row.get("evidence") or "").strip()
            if ev and ev not in EVIDENCE_VERDICTS:
                warnings.append(f"{path.name} 的 {card_id}：evidence={ev!r} 不在约定内")
            verdict = Verdict(
                card_id=card_id,
                kw=kw,
                evidence=ev,
                expl=_as_expl(row.get("expl")),
                fatal=_as_bool(row.get("fatal")),
                note=str(row.get("note") or ""),
                reviewer=reviewer,
                at=str(row.get("at") or ""),
            )
            bucket = human if reviewer.startswith("human:") else agent
            previous = bucket.get(card_id)
            if previous is None or verdict.at >= previous.at:
                bucket[card_id] = verdict
    return agent, human, warnings


# --------------------------------------------------------------------------- #
# 判定口径
# --------------------------------------------------------------------------- #


def is_pass(verdict: dict[str, Any] | Verdict | None) -> bool:
    """§6 的合格判定：kw=accept 且 evidence=ok 且 expl>=1 且 fatal=false。"""
    if verdict is None:
        return False
    if isinstance(verdict, Verdict):
        kw, ev, expl, fatal = verdict.kw, verdict.evidence, verdict.expl, verdict.fatal
    else:
        kw = str(verdict.get("kw") or "")
        ev = str(verdict.get("evidence") or "")
        expl = _as_expl(verdict.get("expl"))
        fatal = _as_bool(verdict.get("fatal"))
    return kw == "accept" and ev == "ok" and (expl is not None and expl >= 1) and fatal is False


def verdict_class(verdict: dict[str, Any] | Verdict | None) -> str:
    """分层用的档位：pass / fail / none。"""
    if verdict is None:
        return NONE
    return PASS if is_pass(verdict) else FAIL


def stratum_of(card: Card) -> tuple[str, str]:
    """抽检分层键：(section, agent 判定档位)。"""
    return (card.sid or "-", verdict_class(card.agent))


# --------------------------------------------------------------------------- #
# 抽样
# --------------------------------------------------------------------------- #


def sample_cards(cards: Sequence[Card], sample: int | None, seed: int) -> list[Card]:
    """按 (section, agent 判定) 分层抽 N 张，同 seed 结果可复现。

    - ``sample`` 为 None、<= 0 或 >= 总数 → 全部返回（0 = 不限）。
    - 其余情况按层大小比例分配名额（最大余数法），再保证每层至少 1 张
      （名额从最大层里挪），最后层内用 ``Random(f"{seed}|{sid}|{档位}")`` 抽。
    """
    total = len(cards)
    if sample is None or sample <= 0 or sample >= total:
        return list(cards)

    groups: dict[tuple[str, str], list[int]] = {}
    for index, card in enumerate(cards):
        groups.setdefault(stratum_of(card), []).append(index)

    keys = sorted(groups)
    exact = {key: len(groups[key]) * sample / total for key in keys}
    quota = {key: int(value) for key, value in exact.items()}
    leftover = sample - sum(quota.values())
    order = sorted(keys, key=lambda key: (-(exact[key] - quota[key]), key))
    for key in order[:leftover]:
        quota[key] += 1

    # 每层至少 1 张：名额从当前最大层里挪（确定性：层大小、层键排序）。
    for key in keys:
        if quota[key] > 0:
            continue
        donor = max(keys, key=lambda k: (quota[k], k))
        if quota[donor] <= 1:
            continue
        quota[donor] -= 1
        quota[key] += 1

    picked: list[int] = []
    for key in keys:
        count = min(quota[key], len(groups[key]))
        if count <= 0:
            continue
        rng = random.Random(f"{seed}|{key[0]}|{key[1]}")
        picked.extend(rng.sample(groups[key], count))
    picked.sort()
    return [cards[index] for index in picked]


# --------------------------------------------------------------------------- #
# payload
# --------------------------------------------------------------------------- #


def _card_payload(card: Card, sampled: bool) -> dict[str, Any]:
    """一张卡进 HTML 前的形状。"""
    agent = None
    if card.agent is not None:
        agent = {
            "kw": card.agent.kw,
            "evidence": card.agent.evidence,
            "expl": card.agent.expl,
            "fatal": card.agent.fatal,
            "note": card.agent.note,
            "reviewer": card.agent.reviewer,
            "at": card.agent.at,
        }
    human_prev = None
    if card.human_prev is not None:
        human_prev = {
            "kw": card.human_prev.kw,
            "evidence": card.human_prev.evidence,
            "expl": card.human_prev.expl,
            "fatal": card.human_prev.fatal,
            "note": card.human_prev.note,
            "reviewer": card.human_prev.reviewer,
            "at": card.human_prev.at,
        }
    return {
        "card_id": card.card_id,
        "sid": card.sid,
        "section_title": card.section_title,
        "front": card.front,
        "back": card.back,
        "type": card.type,
        "tags": card.tags,
        "page": card.page,
        "printed_page": card.printed_page,
        "kw_start": card.kw_start,
        "kw_end": card.kw_end,
        "evidence": card.evidence,
        "evidence_start": card.evidence_start,
        "evidence_end": card.evidence_end,
        "sampled": sampled,
        "agent": agent,
        "human_prev": human_prev,
    }


def build_payload(
    work_dir: Path,
    sample: int | None = DEFAULT_SAMPLE,
    seed: int = 0,
    reviewer: str = DEFAULT_REVIEWER,
) -> dict[str, Any]:
    """读工作目录，拼出要塞进 HTML 的数据。"""
    work_dir = Path(work_dir)
    warnings: list[str] = []

    pages, more = load_pages(work_dir / "pages.jsonl")
    warnings += more
    cards, more, cards_file = load_cards(work_dir, pages)
    warnings += more
    sections = load_sections(work_dir / "sections.json")
    agent_verdicts, human_verdicts, more = load_verdicts(work_dir)
    warnings += more

    for card in cards:
        card.section_title = sections.get(card.sid, "")
        card.agent = agent_verdicts.get(card.card_id)
        card.human_prev = human_verdicts.get(card.card_id)

    picked = sample_cards(cards, sample, seed)
    picked_ids = {card.card_id for card in picked}

    payload_cards = [_card_payload(card, card.card_id in picked_ids) for card in cards]

    by_section: dict[str, dict[str, Any]] = {}
    for card in cards:
        bucket = by_section.setdefault(
            card.sid or "-",
            {
                "sid": card.sid or "-",
                "title": card.section_title,
                "cards": 0,
                "judged": 0,
                "pass": 0,
            },
        )
        bucket["cards"] += 1
        if card.agent is not None:
            bucket["judged"] += 1
            if is_pass(card.agent):
                bucket["pass"] += 1

    judged = sum(1 for card in cards if card.agent is not None)
    passed = sum(1 for card in cards if card.agent is not None and is_pass(card.agent))
    sampled_judged = sum(1 for card in picked if card.agent is not None)
    sampled_pass = sum(1 for card in picked if card.agent is not None and is_pass(card.agent))

    payload = {
        "work_dir": str(work_dir),
        "cards_file": cards_file,
        "reviewer": reviewer,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sample": sample,
        "seed": seed,
        "sampling_applied": bool(picked) and len(picked) < len(cards),
        "summary": {
            "cards_total": len(cards),
            "cards_sampled": len(picked),
            "agent_judged": judged,
            "agent_pass": passed,
            "agent_pass_rate": round(passed / judged, 4) if judged else None,
            "sampled_agent_judged": sampled_judged,
            "sampled_agent_pass": sampled_pass,
            "sampled_agent_pass_rate": (
                round(sampled_pass / sampled_judged, 4) if sampled_judged else None
            ),
            "by_section": [by_section[key] for key in sorted(by_section)],
        },
        "pages": {str(page): pages[page] for page in sorted(pages)},
        "cards": payload_cards,
        "warnings": warnings,
    }
    payload["data_key"] = hashlib.sha1(
        json.dumps(
            {
                "cards": payload_cards,
                "pages": payload["pages"],
                "sample": sample,
                "seed": seed,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return payload


def render_html(payload: dict[str, Any]) -> str:
    """把数据渲染成单个自包含 HTML（CSS / JS / 数据全部内联）。"""
    data_json = json.dumps(payload, ensure_ascii=False)
    # 防 "</script>" 提前结束脚本块；JSON 里 \uXXXX 是合法的。
    data_json = (
        data_json.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )
    title = "卡片审核"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n"
        f"<style>\n{CSS}\n</style>\n"
        "</head>\n"
        "<body>\n"
        f"{BODY}\n"
        f'<script type="application/json" id="bookcard-data">{data_json}</script>\n'
        f"<script>\n{JS_CORE}\n{JS_APP}\n</script>\n"
        "</body>\n"
        "</html>\n"
    )


def build_review_html(
    work_dir: Path,
    out_path: Path,
    sample: int | None = DEFAULT_SAMPLE,
    seed: int = 0,
    reviewer: str = DEFAULT_REVIEWER,
) -> dict[str, Any]:
    """读工作目录 → 生成自包含 HTML。返回摘要。"""
    payload = build_payload(work_dir, sample=sample, seed=seed, reviewer=reviewer)
    html = render_html(payload)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    summary = payload["summary"]
    return {
        "out": str(out_path),
        "work_dir": str(Path(work_dir)),
        "cards_file": payload["cards_file"],
        "cards": summary["cards_total"],
        "sampled": summary["cards_sampled"],
        "agent_judged": summary["agent_judged"],
        "agent_pass": summary["agent_pass"],
        "agent_pass_rate": summary["agent_pass_rate"],
        "warnings": payload["warnings"],
        "bytes": len(html.encode("utf-8")),
    }


# --------------------------------------------------------------------------- #
# 审核记录的校验与合并
# --------------------------------------------------------------------------- #


def validate_review_rows(rows: Sequence[dict[str, Any]]) -> list[str]:
    """校验 §6 的审核记录，返回错误列表（空列表 = 全对）。

    校验项：必填字段、取值范围、``reviewer`` 前缀、``at`` 非空。
    """
    errors: list[str] = []
    required = ("card_id", "kw", "evidence", "expl", "fatal", "note", "reviewer", "at")
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            errors.append(f"第 {index} 条不是对象")
            continue
        card_id = str(row.get("card_id") or "")
        for field_name in required:
            if field_name not in row:
                errors.append(f"第 {index} 条缺字段 {field_name}")
        if not card_id:
            errors.append(f"第 {index} 条 card_id 为空")
        kw = str(row.get("kw") or "")
        if kw not in KW_VERDICTS:
            errors.append(f"{card_id or index}：kw={kw!r} 不在 {KW_VERDICTS} 里")
        evidence = str(row.get("evidence") or "")
        if evidence not in EVIDENCE_VERDICTS:
            errors.append(f"{card_id or index}：evidence={evidence!r} 不在 {EVIDENCE_VERDICTS} 里")
        expl = row.get("expl")
        if _as_expl(expl) is None:
            errors.append(f"{card_id or index}：expl={expl!r} 不是 2 / 1 / 0")
        fatal = row.get("fatal")
        if not isinstance(fatal, bool) and str(fatal).strip().lower() not in ("true", "false"):
            errors.append(f"{card_id or index}：fatal={fatal!r} 不是 true / false")
        reviewer = str(row.get("reviewer") or "")
        if not (reviewer.startswith("agent:") or reviewer.startswith("human:")):
            errors.append(f"{card_id or index}：reviewer={reviewer!r} 必须是 agent:… 或 human:…")
        if not str(row.get("at") or ""):
            errors.append(f"{card_id or index}：at 为空")
    return errors


def merge_review_rows(
    agent_rows: Sequence[dict[str, Any]],
    human_rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按 card_id 合并审核记录，人工判定覆盖 agent 判定。

    返回按 card_id 排序的列表，每条是 §6 的审核记录。
    """
    merged: dict[str, dict[str, Any]] = {}
    for row in list(agent_rows) + list(human_rows):
        card_id = str(row.get("card_id") or "")
        if not card_id:
            continue
        merged[card_id] = dict(row)
    return [merged[key] for key in sorted(merged)]


# --------------------------------------------------------------------------- #
# 命令行
# --------------------------------------------------------------------------- #


def _print_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"警告：{warning}", file=sys.stderr)


def cmd_review_html(args: argparse.Namespace) -> int:
    """``bookcard review-html`` 的实现。"""
    sample = args.sample if args.sample is not None else DEFAULT_SAMPLE
    reviewer = args.reviewer or DEFAULT_REVIEWER
    try:
        summary = build_review_html(
            work_dir=Path(args.dir),
            out_path=Path(args.out),
            sample=sample,
            seed=args.seed,
            reviewer=reviewer,
        )
    except (OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    _print_warnings(summary["warnings"])
    rate = summary["agent_pass_rate"]
    rate_text = "—" if rate is None else f"{rate * 100:.1f}%"
    print(
        f"已生成 {summary['out']}："
        f"{summary['cards']} 张卡（抽样 {summary['sampled']} 张）"
        f"｜agent 判定 {summary['agent_judged']} 张、合格 {summary['agent_pass']} 张（{rate_text}）"
        f"｜{summary['bytes']} 字节"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m bookcard.review`` 的入口。"""
    parser = argparse.ArgumentParser(
        prog="bookcard review-html",
        description="生成卡片审核 HTML（agent 判定 + 人工抽检导出）",
    )
    parser.add_argument(
        "dir",
        metavar="DIR",
        help="工作目录（含 cards.jsonl / pages.jsonl / review/）",
    )
    parser.add_argument(
        "--sample",
        metavar="N",
        type=int,
        default=DEFAULT_SAMPLE,
        help=(
            "抽检张数（按 section 和 agent 判定分层），"
            f"0 或超出总数 = 全部，默认 {DEFAULT_SAMPLE}"
        ),
    )
    parser.add_argument("--seed", metavar="S", type=int, default=0, help="抽样随机种子，默认 0")
    parser.add_argument("--out", metavar="HTML", required=True, help="输出 HTML 路径")
    parser.add_argument(
        "--reviewer",
        metavar="NAME",
        default=DEFAULT_REVIEWER,
        help=f"人工审核人，导出时写成 human:<NAME>，默认 {DEFAULT_REVIEWER}",
    )
    args = parser.parse_args(argv)
    if args.sample < 0:
        parser.error("--sample 不能是负数")
        return 2
    return cmd_review_html(args)
