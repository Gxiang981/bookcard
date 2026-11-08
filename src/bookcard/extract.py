"""extract.py — 抽词 + 解释抽取器（契约 §4）与试抽闸门 2（契约 §6）。

口径唯一来源是 ``docs/contract-v1.md``（维护者定，2026-11-06），本文件**不改提示词**，
只做流水线：

- §4 ``candidates.jsonl``：一页一行（长页按 SCHEMA.md 规则切块），逐条候选
  带 ``surface / type / start / end / explanation / evidence / learn_value``；
  **偏移由程序落**（LLM 只给 ``surface`` 和依据句原文）；
- §5 挑选规则：``norm(front)`` 去重、丢 ``learn_value == 1``、无解释不做卡；
- §6 试抽闸门 2：抽 2 个 section，逐张判定，合格率 ≥ 85% 且 fatal = 0 才 approved。

提示词是包内的 ``bookcard/prompts/extract_v1.md``（版本 ``ex-v1``），提示词正文一个字都不动；
它分两段（SYSTEM / USER），USER 里的 ``{book_title} {subject} {section_title}
{page} {text}`` 由程序替换。

LLM 只走 ``kwtrain.llm``（MiniMax，key 从环境变量读）。并发、重试、限流退避、
断点续跑沿用初标器（``kwtrain.label``）的做法：同一 ``(seg_id, text_sha1, extractor)``
已经抽过就跳过。

闸门是**代码强制**的：``trial.json.gate.status`` 不是 ``approved`` 时，全量抽取
（既不带 ``--sections`` 也不带 ``--trial``）直接拒绝执行。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import threading
import time
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from kwtrain import llm
from kwtrain.collect_book import seg_id_for, split_into_chunks

from bookcard import prompts
from bookcard.engine.softwrap import find_all_flexible, nfkc_index

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- 常量

#: 只有这三种页进抽取（契约 §2）。
EXTRACT_KINDS: tuple[str, ...] = ("body", "exercise", "appendix")

#: 一页超过这么多字就按段落切块（契约 §4 / train/SCHEMA.md）。
MAX_SEG_CHARS = 1500

#: 提示词里 SYSTEM 与 USER 的分隔标记（必须单独占一行）。
USER_SEPARATOR = "===USER==="

#: HTML 注释（提示词里的版本说明），解析前整段去掉。
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

#: 提示词里的占位符，单次替换，避免正文里的花括号被二次解释。
PLACEHOLDER_RE = re.compile(r"\{(book_title|subject|section_title|page|text)\}")

#: 三种合法的 type（GUIDE.md）。
VALID_TYPES: tuple[str, ...] = ("term", "name", "concept")

#: surface 长度上限（码点）。超过就是模型跑偏，丢掉。
MAX_SURFACE_LEN = 40

#: 单次调用的 max_tokens / thinking effort。沿用初标器的值：MiniMax-M3.1-Flash
#: 强制自适应思考，不压 effort 会把额度全烧在 thinking 上、回包没有文本。
MAX_TOKENS = 4096
EFFORT = "low"

#: 思考强度 → max_tokens。effort 越大，thinking 吃得越多，额度不够就会
#: 「回包里没有文本内容」。low 沿用初标器的 4096；medium / high 相应放大。
MAX_TOKENS_BY_EFFORT: dict[str, int] = {
    "low": 4096,
    "medium": 8192,
    "high": 16384,
}


def max_tokens_for(effort: str, override: int | None = None) -> int:
    """按思考强度给 max_tokens；``override`` 直接盖掉。"""
    if override is not None:
        return max(1, int(override))
    return MAX_TOKENS_BY_EFFORT.get(effort, MAX_TOKENS)

#: 命中限流后整个作业暂停多久（秒）。
RATE_LIMIT_PAUSE_SECONDS = 300.0

#: 限流 / 额度不足的判定关键词（小写比对）。
RATE_LIMIT_MARKERS = (
    "429",
    "rate limit",
    "rate_limit",
    "ratelimit",
    "too many requests",
    "quota",
    "insufficient",
    "额度",
    "限流",
    "配额",
)

#: 试抽抽几个 section（一个靠前、一个靠后）。
TRIAL_SECTION_COUNT = 2

#: 闸门 2 的合格率下限（契约 §6）。
TRIAL_PASS_RATE = 0.85

#: 试抽时该跳过的 section 标题（目录 / 前言 / 后记这一类）。标题去掉空白后
#: 命中其中一条就跳过——试抽要看的是**正文**，不是目录和编写组信息。
TRIAL_SKIP_TITLE_RE = re.compile(
    r"目录|前言|序言|编写说明|说明|后记|致谢|附录|索引|参考文献"
    r"|contents|preface|foreword|index|appendix",
    re.IGNORECASE,
)

#: 目录页上那些孤零零的页码（"52"）。
_TOC_PURE_PAGE_NO_RE = re.compile(r"^\d{1,3}$")

#: 目录页上行尾的页码（"第一节 走进神奇 2"）。
_TOC_TRAILING_PAGE_NO_RE = re.compile(r".*[ \t]\d{1,3}$")

#: 一页像目录的门槛：页码行至少这么多行，且占非空行的比例至少这么大。
_TOC_PAGE_MIN_LINES = 5
_TOC_PAGE_MIN_RATIO = 0.30

#: 试抽只认正文页和练习页；其余（封面 / 目录 / 空白）不算正文。
TRIAL_BODY_KINDS: tuple[str, ...] = ("body", "exercise")


class ExtractError(RuntimeError):
    """抽取流程里的可预期错误。"""


class GateBlocked(ExtractError):
    """闸门没过，不许往下走。"""


class PromptError(ExtractError):
    """提示词文件解析不了。"""


class ParseError(ExtractError):
    """模型回包解析不出候选。"""


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def text_sha1(text: str) -> str:
    """正文 UTF-8 字节的 SHA-1（小写十六进制）。与 SCHEMA.md 一致。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def is_rate_limit(exc: BaseException) -> bool:
    """判断异常是不是限流 / 额度不足（沿用初标器的口径）。"""
    message = str(exc).lower()
    return any(marker.lower() in message for marker in RATE_LIMIT_MARKERS)


# --------------------------------------------------------------------------- 提示词


@dataclass(frozen=True)
class Prompt:
    """一份解析好的提示词。"""

    system: str
    user: str
    version: str

    def render(
        self,
        *,
        book_title: str,
        subject: str,
        section_title: str,
        page: int,
        text: str,
    ) -> str:
        """把占位符换成这一段的内容。"""
        values = {
            "book_title": book_title,
            "subject": subject,
            "section_title": section_title,
            "page": str(page),
            "text": text,
        }
        return PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], self.user)


def version_from_filename(name: str) -> str:
    """``extract_v1.md`` → ``ex-v1``；版本号必须跟着文件名走。"""
    stem = Path(name).stem
    match = re.search(r"extract_v(\d+)", stem)
    if match:
        return f"ex-v{match.group(1)}"
    safe = re.sub(r"[^0-9A-Za-z]+", "-", stem).strip("-").lower()
    return f"ex-{safe or 'unknown'}"


def parse_prompt(path: Path) -> Prompt:
    """读提示词文件，切成 system / user 两段。

    注释（``<!-- -->``）整段去掉；``===USER===`` 必须单独占一行。
    """
    raw = path.read_text(encoding="utf-8")
    without_comments = COMMENT_RE.sub("", raw)
    parts = re.split(rf"(?m)^{re.escape(USER_SEPARATOR)}\s*$", without_comments)
    if len(parts) != 2:
        raise PromptError(
            f"{path} 里没有单独一行的 {USER_SEPARATOR}，切不出 system / user 两段"
        )
    system = parts[0].strip()
    user = parts[1].strip()
    if not system or not user:
        raise PromptError(f"{path} 的 system 或 user 段是空的")
    return Prompt(system=system, user=user, version=version_from_filename(path.name))


def default_prompt_path() -> Path:
    """默认提示词路径：**包内**的 ``bookcard/prompts/extract_v1.md``。

    提示词跟着包走（契约 §1）：用 :mod:`importlib.resources` 定位，
    不再依赖仓库根的 ``prompts/`` 目录——装成 wheel / ``uv tool install``
    之后一样能读到。版本号仍由文件名决定（``ex-v1``）。
    """
    return prompts.prompt_path(prompts.EXTRACT_PROMPT_NAME)


def extractor_id(version: str) -> str:
    """``llm:minimax:<模型>:<提示词版本>``（契约 §4 的 ``extractor`` 字段）。"""
    return f"llm:minimax:{llm.model()}:{version}"


# --------------------------------------------------------------------------- 输入


@dataclass(frozen=True)
class Page:
    """一页（契约 §2）。"""

    page: int
    printed_page: str | None
    text: str
    text_sha1: str
    kind: str
    width: float = 0.0
    height: float = 0.0


@dataclass(frozen=True)
class Section:
    """一个 section = App 词库（契约 §3）。"""

    sid: str
    title: str
    level: int
    page_start: int
    page_end: int
    sort_order: int


@dataclass(frozen=True)
class Segment:
    """待抽的一段（契约 §4 的 ``seg_id`` 口径）。"""

    seg_id: str
    page: int
    chunk: int
    sid: str
    text: str
    text_sha1: str
    section_title: str

    @property
    def key(self) -> tuple[str, str]:
        """断点续跑用的键（不含 extractor）。"""
        return (self.seg_id, self.text_sha1)


def load_pages(path: Path) -> list[Page]:
    """读 ``pages.jsonl``。坏行告警跳过。"""
    pages: list[Page] = []
    if not path.exists():
        raise ExtractError(f"找不到 {path}：先跑取字（bookcard text）")
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("%s 第 %s 行不是 JSON，跳过：%s", path, lineno, exc)
                continue
            if not isinstance(row, dict) or not isinstance(row.get("text"), str):
                logger.warning("%s 第 %s 行形状不对，跳过", path, lineno)
                continue
            pages.append(
                Page(
                    page=int(row.get("page") or 0),
                    printed_page=row.get("printed_page"),
                    text=row["text"],
                    text_sha1=str(row.get("text_sha1") or ""),
                    kind=str(row.get("kind") or ""),
                    width=float(row.get("width") or 0.0),
                    height=float(row.get("height") or 0.0),
                )
            )
    return pages


def load_sections(path: Path) -> list[Section]:
    """读 ``sections.json``。"""
    if not path.exists():
        raise ExtractError(f"找不到 {path}：先跑切章节（bookcard sections）")
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("sections") or []
    sections: list[Section] = []
    for row in data:
        if not isinstance(row, dict) or not row.get("sid"):
            continue
        sections.append(
            Section(
                sid=str(row["sid"]),
                title=str(row.get("title") or ""),
                level=int(row.get("level") or 1),
                page_start=int(row.get("page_start") or 0),
                page_end=int(row.get("page_end") or 0),
                sort_order=int(row.get("sort_order") or 0),
            )
        )
    return sections


def section_for_page(sections: Sequence[Section], page: int) -> Section | None:
    """某一页属于哪个 section；不属于任何 section 返回 ``None``。"""
    for section in sections:
        if section.page_start <= page <= section.page_end:
            return section
    return None


def build_segments(
    pages: Sequence[Page],
    sections: Sequence[Section],
    fp12: str,
) -> list[Segment]:
    """把页切成待抽的段（契约 §4）。

    - 只抽 ``kind ∈ {body, exercise, appendix}`` 的页；
    - 不属于任何 section 的页跳过（section 覆盖不了的页不该抽）；
    - 页正文超过 ``MAX_SEG_CHARS`` 字时按段落切块，``chunk`` 从 1 起，
      ``seg_id`` 追加 ``c<块号>``；不切块的页 ``chunk`` 是 0，``seg_id`` 不带后缀。
    """
    segments: list[Segment] = []
    for page in pages:
        if page.kind not in EXTRACT_KINDS:
            continue
        if not page.text.strip():
            continue
        section = section_for_page(sections, page.page)
        if section is None:
            continue
        if len(page.text) <= MAX_SEG_CHARS:
            segments.append(
                Segment(
                    seg_id=seg_id_for(fp12, page.page, None),
                    page=page.page,
                    chunk=0,
                    sid=section.sid,
                    text=page.text,
                    text_sha1=text_sha1(page.text),
                    section_title=section.title,
                )
            )
            continue
        for index, chunk_text in enumerate(
            split_into_chunks(page.text, MAX_SEG_CHARS), start=1
        ):
            segments.append(
                Segment(
                    seg_id=seg_id_for(fp12, page.page, index),
                    page=page.page,
                    chunk=index,
                    sid=section.sid,
                    text=chunk_text,
                    text_sha1=text_sha1(chunk_text),
                    section_title=section.title,
                )
            )
    return segments


# --------------------------------------------------------------------------- 回包解析


@dataclass(frozen=True)
class Keyword:
    """模型给的一条候选（还不带偏移）。

    字段跟 App 的卡对齐：``front``（正面）/ ``back``（背面）。
    """

    front: str
    type: str
    back: str | None
    learn_value: int

    # 旧字段名（surface / explanation / evidence）保留成别名 ——
    # 基准工具（kwtrain）和老产出还用它们。
    @property
    def surface(self) -> str:
        return self.front

    @property
    def explanation(self) -> str | None:
        return self.back

    @property
    def evidence(self) -> str | None:
        return None


def _loads(raw: str) -> Any | None:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def strip_code_fence(raw: str) -> str:
    """剥掉 Markdown 代码块围栏，取出块里的内容。"""
    match = re.search(r"```[A-Za-z0-9_+-]*\s*(.*?)```", raw, re.DOTALL)
    if match:
        return match[1].strip()
    return raw.strip()


def _brace_slice(raw: str) -> str | None:
    """取第一个 ``{`` 到最后一个 ``}`` 之间的内容。"""
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    return raw[start : end + 1]


def _keywords_from(data: Any) -> list[Any] | None:
    """从解析出来的 JSON 里取出候选列表；形状不对返回 ``None``。"""
    if isinstance(data, dict):
        items = data.get("keywords")
        if not isinstance(items, list):
            return None
        return items
    if isinstance(data, list):
        return data
    return None


def _clean_text(value: Any) -> str | None:
    """把模型给的文本字段收拾干净；不是字符串或空了返回 ``None``。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _normalize_keyword(item: Any) -> Keyword | None:
    """把一条原始候选收拾成 :class:`Keyword`；不合格返回 ``None``。"""
    if not isinstance(item, dict):
        return None
    # 新字段是 front / back；旧的 surface / explanation 也认（老产出还能读）
    surface = _clean_text(item.get("front") or item.get("surface"))
    if not surface or len(surface) > MAX_SURFACE_LEN:
        return None
    ktype = item.get("type")
    if ktype not in VALID_TYPES:
        return None
    explanation = _clean_text(item.get("back") or item.get("explanation"))
    raw_value = item.get("learn_value")
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        learn_value = 2
    else:
        learn_value = min(3, max(1, raw_value))
    return Keyword(
        front=surface,
        type=str(ktype),
        back=explanation,
        learn_value=learn_value,
    )


def parse_keywords(raw: str) -> list[Keyword]:
    """从模型回包里取出候选列表。

    顺序：直接 ``json.loads`` → 剥代码块 → 取第一个 ``{`` 到最后一个 ``}``。
    都不行抛 :class:`ParseError`。
    """
    candidates: list[str] = [raw]
    stripped = strip_code_fence(raw)
    if stripped != raw.strip():
        candidates.append(stripped)
    for candidate in (stripped, raw):
        slice_ = _brace_slice(candidate)
        if slice_ is not None:
            candidates.append(slice_)
    for candidate in candidates:
        data = _loads(candidate)
        if data is None:
            continue
        items = _keywords_from(data)
        if items is None:
            continue
        out: list[Keyword] = []
        seen: set[str] = set()
        for item in items:
            keyword = _normalize_keyword(item)
            if keyword is None:
                continue
            if keyword.front in seen:
                # 同一个词列了多次：GUIDE 说只列一次，不算丢弃。
                continue
            seen.add(keyword.front)
            out.append(keyword)
        return out
    raise ParseError("回包里找不到 keywords 列表")




def locate_evidence(text: str, evidence: str | None) -> tuple[int, int] | None:
    """在正文里找依据句，返回偏移；找不到返回 ``None``。

    逐字找不到时，退两级再找：

    1. **空白折叠**成单个空格再找——模型把原文里的换行压成空格；
    2. **空白全去掉**再找——模型把原文里的换行直接接上（教材正文换行很常见，
       模型几乎总会这样接），这一级能把绝大多数「其实在原文里」的依据找回来。

    两级退化都记着字符到原下标的映射，找到的偏移仍然指向原文的真实区间。
    """
    if not evidence:
        return None
    needle = evidence.strip().strip(_EVIDENCE_WRAPPERS).strip()
    if not needle:
        return None

    index = text.find(needle)
    if index != -1:
        return (index, index + len(needle))

    normalized_text, text_map = _collapse_whitespace(text)
    normalized_needle, _ = _collapse_whitespace(needle)
    normalized_needle = normalized_needle.strip()
    if normalized_needle:
        found = normalized_text.find(normalized_needle)
        if found != -1:
            start = text_map[found]
            end = text_map[found + len(normalized_needle) - 1] + 1
            return (start, end)

    compact_text, compact_map = _drop_whitespace(text)
    compact_needle, _ = _drop_whitespace(needle)
    if compact_needle:
        found = compact_text.find(compact_needle)
        if found != -1:
            start = compact_map[found]
            end = compact_map[found + len(compact_needle) - 1] + 1
            return (start, end)

    # 坏字兜底：原文里被映射坏的标点（U+054B 之类），模型抄的是正常标点。
    # 坏字位置当成**通配符** —— 原文的「（」映成了 Ջ，模型抄的仍是「（」。
    garbled_text, gmap = _drop_garbled(text)
    garbled_needle, _ = _drop_garbled(needle)
    if garbled_needle:
        found = garbled_text.find(garbled_needle)
        if found != -1:
            return (gmap[found], gmap[found + len(garbled_needle) - 1] + 1)
    wildcard = _find_with_garbled(text, needle)
    if wildcard is not None:
        return wildcard

    # 兼容码位兜底：文本层用兼容表意文字（理=U+F9E4）时，模型写的是标准码位。
    # 归一后按上面三级再找一遍，偏移换算回原文。
    n_text = nfkc_index(text)
    n_needle = nfkc_index(needle)
    if n_text and n_needle:
        nt, n_start, n_end = n_text
        nn, _, _ = n_needle
        found = nt.find(nn)
        if found != -1:
            return (n_start[found], n_end[found + len(nn) - 1])
        cn, cmap = _collapse_whitespace(nt)
        cn2, _ = _collapse_whitespace(nn)
        cn2 = cn2.strip()
        if cn2:
            found = cn.find(cn2)
            if found != -1:
                return (
                    n_start[cmap[found]],
                    n_end[cmap[found + len(cn2) - 1]],
                )
        dn, dmap = _drop_whitespace(nt)
        dn2, _ = _drop_whitespace(nn)
        if dn2:
            found = dn.find(dn2)
            if found != -1:
                return (
                    n_start[dmap[found]],
                    n_end[dmap[found + len(dn2) - 1]],
                )
    return None


# --------------------------------------------------------------------------- 落位


def find_all(text: str, needle: str) -> list[int]:
    """``needle`` 在 ``text`` 里的所有出现位置（码点下标，允许互相重叠）。"""
    hits: list[int] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index == -1:
            return hits
        hits.append(index)
        start = index + 1


def _collapse_whitespace(text: str) -> tuple[str, list[int]]:
    """空白折叠成单个空格，同时记下每个归一字符对应的原下标。"""
    out: list[str] = []
    index_map: list[int] = []
    prev_space = False
    for index, char in enumerate(text):
        if char.isspace():
            if prev_space:
                continue
            out.append(" ")
            index_map.append(index)
            prev_space = True
            continue
        out.append(char)
        index_map.append(index)
        prev_space = False
    return "".join(out), index_map


def _drop_whitespace(text: str) -> tuple[str, list[int]]:
    """把空白全部去掉，同时记下每个保留字符对应的原下标。"""
    out: list[str] = []
    index_map: list[int] = []
    for index, char in enumerate(text):
        if char.isspace():
            continue
        out.append(char)
        index_map.append(index)
    return "".join(out), index_map


#: PDF 文本层里「字形映射坏了」的怪字符：私用区 + 亚美尼亚字母区
#: （有的 PDF 把「（」「，」这类标点映成了 U+054B「Ջ」）。
#: 原文里带这种字，模型抄出来的却是正常标点 —— 逐字对不上。
#: 匹配时两边都把它去掉（跟空白容错同一级）。
_GARBLE_RE = re.compile(r"[\ue000-\uf8ff\u0530-\u058f]")



def _find_with_garbled(text: str, needle: str) -> tuple[int, int] | None:
    """带**通配**的查找：原文里被映射坏的字符（U+054B 之类）位置，匹配任意一个字符。

    有些 PDF 把「（」「，」这类标点映成了看不出原样的怪字符；模型抄证据句时
    抄的是正常标点 —— 逐字对不上，但那个位置本来就该对上。

    空白照旧折叠（证据句常跨 PDF 的硬折行），偏移换算回原文。
    """
    if not needle:
        return None
    if not any(_GARBLE_RE.match(ch) for ch in text):
        return None
    collapsed, cmap = _drop_whitespace(text)
    needle_c, _ = _drop_whitespace(needle)
    needle_c = needle_c.strip()
    if not needle_c:
        return None
    # needle 可能比原文还长（原文丢了字符）—— 所以从 0 扫到文末
    for start in range(0, len(collapsed) + 1):
        ti = start
        ok = True
        for ch in needle_c:
            if ti >= len(collapsed):
                ok = False
                break
            got = collapsed[ti]
            if got == ch:
                ti += 1
                continue
            if _GARBLE_RE.match(got):
                # 坏字顶掉一个字符（PDF 里「（」常被映成「Ջ 」）
                ti += 1
                continue
            if ch in _CLOSABLE:
                # 原文里配对的右括号被整个丢掉了 —— needle 的右括号允许「没有对应」
                continue
            ok = False
            break
        if ok:
            return (cmap[start], cmap[min(ti, len(cmap)) - 1] + 1)
    return None


def _drop_garbled(text: str) -> tuple[str, list[int]]:
    """把坏字去掉，同时记下每个保留字符对应的原下标。"""
    out: list[str] = []
    index_map: list[int] = []
    for index, char in enumerate(text):
        if _GARBLE_RE.match(char):
            continue
        out.append(char)
        index_map.append(index)
    return "".join(out), index_map


#: 原文里可能被整个丢掉的右括号（PDF 字形映射坏了时常见）
_CLOSABLE = "）】》」』〕)]｝"

#: 依据句两端可能被模型套上的引号 / 书名号，剥掉再找。
_EVIDENCE_WRAPPERS = "「」『』“”\"'《》〈〉（）()【】[]"


def place_items(
    text: str,
    keywords: Sequence[Keyword],
) -> tuple[list[dict[str, Any]], int, int]:
    """把候选落成 item（契约 §4）。

    返回 ``(items, not_in_text)``：

    - ``surface`` 在正文里的**每一处**都落一个 item，偏移由程序落；
    - 抽词段的文字带着 PDF 的硬换行，跨行的词也算命中（偏移可以跨 ``\\n``，
      见 :func:`find_all_flexible`）；
    - 重叠保留最长的，等长保留靠前的（SCHEMA.md 的落位规则）；
    - 解释和依据只挂在该词**第一处**（最靠前的存活 occurrence），
      其余出现只有 ``start/end``（``explanation`` 与 ``evidence`` 都是 ``null``）；
    - 找不到原文的 surface 丢弃，计 ``not_in_text``；
    - 依据句在正文里找不到 → ``evidence`` 置 ``null``，计 ``bad_evidence``（该词照留）。
    """
    not_in_text = 0

    # 1) 每个 surface 的所有出现位置。
    occurrences: list[tuple[int, int, Keyword]] = []
    seen: set[str] = set()
    for keyword in keywords:
        if keyword.front in seen:
            continue
        seen.add(keyword.front)
        hits = find_all_flexible(text, keyword.front)
        if not hits:
            not_in_text += 1
            continue
        for start, end in hits:
            occurrences.append((start, end, keyword))

    # 2) 重叠保留最长的，等长保留靠前的。
    occurrences.sort(key=lambda item: (-(item[1] - item[0]), item[0]))
    chosen: list[tuple[int, int, Keyword]] = []
    for candidate in occurrences:
        start, end = candidate[0], candidate[1]
        if any(not (end <= other[0] or other[1] <= start) for other in chosen):
            continue
        chosen.append(candidate)
    chosen.sort(key=lambda item: item[0])

    # 3) 解释和依据挂在每个词第一处（最靠前的存活 occurrence）。
    first_index: dict[str, int] = {}
    for index, (_, _, keyword) in enumerate(chosen):
        first_index.setdefault(keyword.front, index)

    items: list[dict[str, Any]] = []
    for index, (start, end, keyword) in enumerate(chosen):
        explanation: str | None = None
        if first_index[keyword.front] == index:
            explanation = keyword.back
        items.append(
            {
                "front": keyword.front,
                "type": keyword.type,
                "start": start,
                "end": end,
                "back": explanation,
                "learn_value": keyword.learn_value,
            }
        )
    return items, not_in_text


# --------------------------------------------------------------------------- 挑选（§5）


def norm_front(text: str) -> str:
    """卡正面归一化：NFKC、去首尾空白、英文小写（契约 §5）。"""
    return unicodedata.normalize("NFKC", text).strip().lower()


@dataclass
class Card:
    """试抽阶段临时挑出来的卡（§5 规则）。"""

    card_id: str
    sid: str
    front: str
    back: str
    type: str
    learn_value: int
    page: int
    printed_page: str | None
    seg_id: str
    start: int
    end: int
    sort_order: int = 0


def card_id_for(fp12: str, sid: str, front: str) -> str:
    """``<fp12>:<sid>:<sha1(front)[:8]>``（契约 §5）。"""
    digest = hashlib.sha1(front.encode("utf-8")).hexdigest()[:8]
    return f"{fp12}:{sid}:{digest}"


def select_cards(
    rows: Sequence[dict[str, Any]],
    fp12: str,
    texts: dict[str, str] | None = None,
) -> list[Card]:
    """按 §5 规则从候选里挑卡。

    规则：

    1. 一本书内按 ``norm(front)`` 去重，**只留第一次出现**（最早页）那张；
       ``learn_value`` 取最大值；
    2. 丢 ``learn_value == 1``；
    3. 卡归属第一次出现的 section；section 内按页码、再按 ``start`` 排 ``sort_order``；
    4. ``back`` = ``explanation``；没解释的词不做卡（但仍在 candidates 里进训练）。
    """
    # 每个词保留「最早页 + 最靠前偏移」那一条，learn_value 取最大。
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        seg_id = str(row.get("seg_id") or "")
        text = texts.get(seg_id, "") if texts else ""
        page = int(row.get("page") or 0)
        for item in row.get("items") or []:
            if not isinstance(item, dict):
                continue
            surface = item.get("front") or item.get("surface")
            explanation = item.get("back") or item.get("explanation")
            if not isinstance(surface, str) or not surface.strip():
                continue
            if not isinstance(explanation, str) or not explanation.strip():
                # 没解释的不做卡（§5 规则 4）。
                continue
            learn_value = int(item.get("learn_value") or 2)
            key = norm_front(surface)
            current = best.get(key)
            if current is None:
                best[key] = {
                    "front": surface,
                    "back": explanation,
                    "type": str(item.get("type") or "term"),
                    "learn_value": learn_value,
                    "page": page,
                    "seg_id": seg_id,
                    "start": int(item.get("start") or 0),
                    "end": int(item.get("end") or 0),
                    "sid": str(row.get("sid") or ""),
                    "printed_page": row.get("printed_page"),
                    "text": text,
                }
                continue
            current["learn_value"] = max(current["learn_value"], learn_value)
            if (page, int(item.get("start") or 0)) < (
                current["page"],
                current["start"],
            ):
                current.update(
                    {
                        "front": surface,
                        "back": explanation,
                        "type": str(item.get("type") or "term"),
                        "page": page,
                        "seg_id": seg_id,
                        "start": int(item.get("start") or 0),
                        "end": int(item.get("end") or 0),
                        "sid": str(row.get("sid") or ""),
                        "printed_page": row.get("printed_page"),
                        "text": text,
                    }
                )

    cards: list[Card] = []
    for entry in best.values():
        if entry["learn_value"] == 1:
            continue
        cards.append(
            Card(
                card_id=card_id_for(fp12, entry["sid"], entry["front"]),
                sid=entry["sid"],
                front=entry["front"],
                back=entry["back"],
                type=entry["type"],
                learn_value=entry["learn_value"],
                page=entry["page"],
                printed_page=entry.get("printed_page"),
                seg_id=entry["seg_id"],
                start=entry["start"],
                end=entry["end"],
            )
        )

    # section 内按页码、再按 start 排 sort_order；先按 section 排，section 之间
    # 按第一次出现的页码（§5 规则 3）。
    section_order: dict[str, int] = {}
    for card in cards:
        if card.sid not in section_order:
            section_order[card.sid] = card.page
    ordered_sections = sorted(section_order, key=lambda sid: (section_order[sid], sid))
    cards.sort(
        key=lambda card: (
            ordered_sections.index(card.sid),
            card.page,
            card.start,
            card.front,
        )
    )
    for index, card in enumerate(cards, start=1):
        card.sort_order = index
    return cards


# --------------------------------------------------------------------------- 闸门


def read_gate(book_dir: Path) -> dict[str, Any] | None:
    """读 ``trial.json`` 里的闸门状态；没有文件返回 ``None``。"""
    path = book_dir / "trial.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    gate = data.get("gate") if isinstance(data, dict) else None
    return gate if isinstance(gate, dict) else None


def _page_looks_like_toc(text: str) -> bool:
    """这一页像不像目录页。

    目录页的两种样子：孤零零的页码行（"52"），或者行尾带页码的条目行
    （"第一节 走进神奇 2"）。页码行至少 :data:`_TOC_PAGE_MIN_LINES` 行、
    且占非空行的比例至少 :data:`_TOC_PAGE_MIN_RATIO` 才算像目录。
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    pure = sum(1 for line in lines if _TOC_PURE_PAGE_NO_RE.match(line))
    trailing = sum(1 for line in lines if _TOC_TRAILING_PAGE_NO_RE.match(line))
    hits = max(pure, trailing)
    return hits >= _TOC_PAGE_MIN_LINES and hits / len(lines) >= _TOC_PAGE_MIN_RATIO


# --------------------------------------------------------------------------- 作业状态


def candidate_row(
    segment: Segment,
    extractor: str,
    items: list[dict[str, Any]],
    not_in_text: int,
) -> dict[str, Any]:
    """``candidates.jsonl`` 的一行（契约 §4）；内部抽词和 agentflow 交卷共用。"""
    return {
        "seg_id": segment.seg_id,
        "page": segment.page,
        "chunk": segment.chunk,
        "sid": segment.sid,
        "text_sha1": segment.text_sha1,
        "extractor": extractor,
        "scope": "full",
        "items": items,
        "dropped": {"not_in_text": not_in_text},
        "created_at": _now_iso(),
    }


class JobState:
    """跨线程共享的作业状态：写锁、计数器、限流暂停。"""

    def __init__(self, *, out_path: Path, extractor: str) -> None:
        self.out_path = out_path
        self.extractor = extractor
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.pause_until = 0.0
        self.last_error = ""
        self.extracted_total = 0
        self.extracted_this_run = 0
        self.failed = 0
        self.item_total = 0
        self.dropped_not_in_text = 0
        self.done_keys: set[tuple[str, str, str]] = set()

    # -- 断点续跑 ----------------------------------------------------------

    def is_done(self, segment: Segment) -> bool:
        return (segment.seg_id, segment.text_sha1, self.extractor) in self.done_keys

    def mark_done(self, segment: Segment) -> None:
        with self.lock:
            self.done_keys.add((segment.seg_id, segment.text_sha1, self.extractor))

    # -- 限流 ---------------------------------------------------------------

    def note_rate_limit(self, message: str) -> None:
        with self.lock:
            self.last_error = message[:500]
            self.pause_until = max(self.pause_until, time.monotonic() + RATE_LIMIT_PAUSE_SECONDS)

    def wait_pause(self) -> None:
        while True:
            with self.lock:
                remaining = self.pause_until - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 1.0))

    # -- 落盘 ---------------------------------------------------------------

    def write_row(self, row: dict[str, Any]) -> None:
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with self.write_lock:
            with self.out_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()

    def summary(self) -> dict[str, Any]:
        with self.lock:
            return {
                "extracted_total": self.extracted_total,
                "extracted_this_run": self.extracted_this_run,
                "failed": self.failed,
                "items": self.item_total,
                "dropped": {
                    "not_in_text": self.dropped_not_in_text,
                },
                "last_error": self.last_error,
            }


def load_done_keys(path: Path, extractor: str) -> set[tuple[str, str, str]]:
    """读已有的 ``candidates.jsonl``，取出已完成的 ``(seg_id, text_sha1, extractor)``。"""
    done: set[tuple[str, str, str]] = set()
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            done.add(
                (
                    str(row.get("seg_id")),
                    str(row.get("text_sha1")),
                    str(row.get("extractor")),
                )
            )
    return done


# --------------------------------------------------------------------------- 抽取


@dataclass
class BookContext:
    """一本书的工作目录（契约 §0 的目录布局）。"""

    book_dir: Path
    fp12: str
    title: str
    subject: str
    pages: list[Page] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)

    @property
    def pages_path(self) -> Path:
        return self.book_dir / "pages.jsonl"

    @property
    def sections_path(self) -> Path:
        return self.book_dir / "sections.json"

    @property
    def book_path(self) -> Path:
        return self.book_dir / "book.json"

    @property
    def candidates_path(self) -> Path:
        return self.book_dir / "candidates.jsonl"

    @property
    def trial_path(self) -> Path:
        return self.book_dir / "trial.json"

    @property
    def review_dir(self) -> Path:
        return self.book_dir / "review"


def load_book_context(book_dir: Path) -> BookContext:
    """读一本书的工作目录：``book.json`` + ``pages.jsonl`` + ``sections.json``。"""
    book_dir = Path(book_dir)
    if not book_dir.is_dir():
        raise ExtractError(f"{book_dir} 不是目录")
    meta: dict[str, Any] = {}
    if book_dir.joinpath("book.json").exists():
        try:
            meta = json.loads(book_dir.joinpath("book.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("book.json 读不了（忽略）：%s", exc)
            meta = {}
    fp12 = str(meta.get("fp12") or book_dir.name)
    pages = load_pages(book_dir / "pages.jsonl")
    sections = load_sections(book_dir / "sections.json")
    return BookContext(
        book_dir=book_dir,
        fp12=fp12,
        title=str(meta.get("title") or ""),
        subject=str(meta.get("subject") or ""),
        pages=pages,
        sections=sections,
    )


def extract_segments(
    context: BookContext,
    segments: Sequence[Segment],
    prompt: Prompt,
    *,
    workers: int = 1,
    extractor: str | None = None,
    effort: str = EFFORT,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """抽一批段，写 ``candidates.jsonl``（断点续跑）。

    ``effort`` 是 MiniMax 的思考强度（``low`` / ``medium`` / ``high``）。闸门 2
    不过时，契约 §6 允许「改提示词或参数重试」——改 ``effort`` 就是改参数：
    解释里的事实错误多半是 thinking 太浅导致的。

    返回本次的统计。
    """
    extractor = extractor or extractor_id(prompt.version)
    state = JobState(out_path=context.candidates_path, extractor=extractor)
    state.done_keys = load_done_keys(context.candidates_path, extractor)

    todo = [segment for segment in segments if not state.is_done(segment)]
    logger.info(
        "待抽 %s 段（已完成 %s 段），extractor=%s",
        len(todo),
        len(segments) - len(todo),
        extractor,
    )

    def extract_one(segment: Segment) -> None:
        user = prompt.render(
            book_title=context.title,
            subject=context.subject,
            section_title=segment.section_title,
            page=segment.page,
            text=segment.text,
        )
        while True:
            state.wait_pause()
            try:
                raw = llm.complete(
                    prompt.system,
                    user,
                    max_tokens=max_tokens_for(effort, max_tokens),
                    temperature=0,
                    effort=effort,
                )
                break
            except llm.LLMError as exc:
                if is_rate_limit(exc):
                    logger.warning("命中限流，暂停 %s 秒：%s", int(RATE_LIMIT_PAUSE_SECONDS), exc)
                    state.note_rate_limit(str(exc))
                    continue
                logger.warning("段 %s 调用失败：%s", segment.seg_id, exc)
                with state.lock:
                    state.failed += 1
                    state.last_error = str(exc)[:500]
                return

        try:
            keywords = parse_keywords(raw)
        except ParseError as exc:
            logger.warning("段 %s 回包解析失败：%s", segment.seg_id, exc)
            with state.lock:
                state.failed += 1
                state.last_error = str(exc)[:500]
            return

        items, not_in_text = place_items(segment.text, keywords)
        state.write_row(candidate_row(segment, extractor, items, not_in_text))
        with state.lock:
            state.extracted_total += 1
            state.extracted_this_run += 1
            state.item_total += len(items)
            state.dropped_not_in_text += not_in_text
        state.mark_done(segment)

    if workers <= 1:
        for segment in todo:
            extract_one(segment)
    else:
        cursor = 0
        index_lock = threading.Lock()

        def worker() -> None:
            nonlocal cursor
            while True:
                with index_lock:
                    if cursor >= len(todo):
                        return
                    segment = todo[cursor]
                    cursor += 1
                extract_one(segment)

        threads = [
            threading.Thread(target=worker, name=f"extract-{index}")
            for index in range(max(1, workers))
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    return state.summary()


def load_candidates(path: Path) -> list[dict[str, Any]]:
    """读 ``candidates.jsonl``（坏行跳过）。"""
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def build_text_index(segments: Sequence[Segment]) -> dict[str, str]:
    """``seg_id`` → 正文，给挑选和审核用。"""
    return {segment.seg_id: segment.text for segment in segments}


# --------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bookcard extract",
        description="抽词 + 解释抽取器（契约 §4）与试抽闸门 2（契约 §6）",
    )
    parser.add_argument("dir", type=Path, help="一本书的工作目录（含 pages.jsonl / sections.json）")
    parser.add_argument(
        "--sections",
        default=None,
        help="只抽这些 section（逗号分隔的 sid，如 s01,s05）",
    )
    parser.add_argument("--workers", type=int, default=4, help="并发线程数（默认 4）")
    parser.add_argument("--limit", type=int, default=None, help="本轮最多抽多少段")
    parser.add_argument(
        "--effort",
        default=EFFORT,
        help=f"思考强度 low/medium/high（默认 {EFFORT}；闸门 2 不过时按契约 §6 改参数重试）",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="单次调用的 max_tokens（默认按 effort 取：low 4096 / medium 8192 / high 16384）",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        default=None,
        help="提示词文件（默认包内的 bookcard/prompts/extract_v1.md）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        prompt = parse_prompt(args.prompt or default_prompt_path())
        context = load_book_context(args.dir)
    except (ExtractError, OSError) as exc:
        print(f"抽取没跑起来：{exc}")
        return 2

    segments = build_segments(context.pages, context.sections, context.fp12)

    if args.sections:
        wanted = {part.strip() for part in args.sections.split(",") if part.strip()}
        segments = [segment for segment in segments if segment.sid in wanted]
        if not segments:
            print(f"这些 section 里没有可抽的页：{args.sections}")
            return 2

    if args.limit is not None:
        segments = segments[: max(0, args.limit)]

    context.book_dir.mkdir(parents=True, exist_ok=True)
    stats = extract_segments(
        context,
        segments,
        prompt,
        workers=args.workers,
        effort=args.effort,
        max_tokens=args.max_tokens,
    )
    print(
        f"本轮抽了 {stats['extracted_this_run']} 段（新 {stats['items']} 个词），"
        f"失败 {stats['failed']} 段，丢弃（原文里找不到这个词）："
        f"{stats['dropped']['not_in_text']}"
    )

    return 0
