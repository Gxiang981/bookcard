"""论文源的许可与出处判定（2026-11-08 两次拍板）。

**第一次拍板**（分级闸门）：短引用（带出处四要素）公开包不限论文许可；
论文图 / 完整原文 / 结论复述卡只有许可宽松（CC BY / CC BY-SA / CC0）才能进公开包。

**第二次拍板**（同日，the maintainer：「和书一样处理」）——**现行政策**：

- 论文源与书源**同规则**：公开包只放「词汇 + LLM 解释（第一句）+ 出处」，
  **不带原文逐字内容、不带图**；内部分享版（私有包）照旧带页面图、证据句和上下文；
- **许可只记录、不裁剪**：``manifest.source_license`` / ``manifest.attribution``
  记下许可名、许可地址和出处四要素；社区层按 ``nc`` / 默认许可给提示，
  作者也好知道这篇能不能商用；
- 公开包**唯一多出来的一条硬检查**：解释**不得大段照抄原文**
  （公共子串 ≥ 60 字符（纯拉丁文）或 ≥ 30 字符（含中文）⇒ 这张卡不进公开包）。

本模块只做**判定**，不做打包：许可 URI → 许可种类 → 旗标；出处四要素；
卡片性质；照抄检查。打包时怎么用见 :mod:`bookcard.pipeline.pack`。

**书本来源不动**：本模块只对 ``book["type"] == "paper"`` 生效，书本的既有行为
一字不改（README §7 已冻结）。

运行时**不引入新依赖**（只用标准库）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ===================== 许可种类 =====================

#: 宽松许可：CC BY / CC BY-SA / CC0。
KIND_CC_BY_4_0 = "cc-by-4.0"
KIND_CC_BY_SA_4_0 = "cc-by-sa-4.0"
KIND_CC_BY_NC_SA_4_0 = "cc-by-nc-sa-4.0"
KIND_CC_BY_NC_ND_4_0 = "cc-by-nc-nd-4.0"
KIND_CC0_1_0 = "cc0-1.0"
KIND_ARXIV_NONEXCLUSIVE_1_0 = "arxiv-nonexclusive-1.0"
KIND_ASSUMED_1991_2003 = "assumed-1991-2003"
#: 认不出的许可——按最严策略处理。
KIND_UNKNOWN = "unknown"

#: 所有许可种类（顺序固定）。
LICENSE_KINDS = (
    KIND_CC_BY_4_0,
    KIND_CC_BY_SA_4_0,
    KIND_CC_BY_NC_SA_4_0,
    KIND_CC_BY_NC_ND_4_0,
    KIND_CC0_1_0,
    KIND_ARXIV_NONEXCLUSIVE_1_0,
    KIND_ASSUMED_1991_2003,
    KIND_UNKNOWN,
)

#: 许可种类 → canonical URI（``unknown`` 没有 canonical URI）。
LICENSE_URI: dict[str, str] = {
    KIND_CC_BY_4_0: "http://creativecommons.org/licenses/by/4.0/",
    KIND_CC_BY_SA_4_0: "http://creativecommons.org/licenses/by-sa/4.0/",
    KIND_CC_BY_NC_SA_4_0: "http://creativecommons.org/licenses/by-nc-sa/4.0/",
    KIND_CC_BY_NC_ND_4_0: "http://creativecommons.org/licenses/by-nc-nd/4.0/",
    KIND_CC0_1_0: "http://creativecommons.org/publicdomain/zero/1.0/",
    KIND_ARXIV_NONEXCLUSIVE_1_0: "http://arxiv.org/licenses/nonexclusive-distrib/1.0/",
    KIND_ASSUMED_1991_2003: "http://arxiv.org/licenses/assumed-1991-2003/",
    KIND_UNKNOWN: "",
}

#: 宽松许可（非 NC 也非 ND）：公开包可以带 context 和结论复述卡。
PERMISSIVE_KINDS = (KIND_CC_BY_4_0, KIND_CC_BY_SA_4_0, KIND_CC0_1_0)

#: arXiv 摘要页前缀（出处四要素里的链接）。
ABS_URL_PREFIX = "https://arxiv.org/abs/"

#: 许可写法 → 许可种类（同一个种类可以有多种写法：kind 字符串本身、canonical
#: URI、https、末尾斜杠、CC 缩写）。键是**归一化后**的写法。
#:
#: **每个 kind 都把自己也列进别名**——``book.json`` 的 ``paper_license``
#: 有可能就是直接写 kind 字符串（那样写），这时不能退化成
#: ``"unknown"``：记录里的许可名、URI、策略全得准。
_ALIASES: dict[str, tuple[str, ...]] = {
    KIND_CC_BY_4_0: (
        KIND_CC_BY_4_0,
        "http://creativecommons.org/licenses/by/4.0/",
        "creativecommons.org/licenses/by/4.0",
        "cc by 4.0",
        "cc by",
    ),
    KIND_CC_BY_SA_4_0: (
        KIND_CC_BY_SA_4_0,
        "http://creativecommons.org/licenses/by-sa/4.0/",
        "creativecommons.org/licenses/by-sa/4.0",
        "cc by-sa 4.0",
        "cc by sa 4.0",
    ),
    KIND_CC_BY_NC_SA_4_0: (
        KIND_CC_BY_NC_SA_4_0,
        "http://creativecommons.org/licenses/by-nc-sa/4.0/",
        "creativecommons.org/licenses/by-nc-sa/4.0",
        "cc by-nc-sa 4.0",
        "cc by nc sa 4.0",
    ),
    KIND_CC_BY_NC_ND_4_0: (
        KIND_CC_BY_NC_ND_4_0,
        "http://creativecommons.org/licenses/by-nc-nd/4.0/",
        "creativecommons.org/licenses/by-nc-nd/4.0",
        "cc by-nc-nd 4.0",
        "cc by nc nd 4.0",
    ),
    KIND_CC0_1_0: (
        KIND_CC0_1_0,
        "http://creativecommons.org/publicdomain/zero/1.0/",
        "creativecommons.org/publicdomain/zero/1.0",
        "cc0",
        "cc0 1.0",
        "cc zero",
    ),
    KIND_ARXIV_NONEXCLUSIVE_1_0: (
        KIND_ARXIV_NONEXCLUSIVE_1_0,
        "http://arxiv.org/licenses/nonexclusive-distrib/1.0/",
        "arxiv.org/licenses/nonexclusive-distrib/1.0",
        "arxiv nonexclusive distribution license",
    ),
    KIND_ASSUMED_1991_2003: (
        KIND_ASSUMED_1991_2003,
        "http://arxiv.org/licenses/assumed-1991-2003/",
        "arxiv.org/licenses/assumed-1991-2003",
        "assumed 1991-2003 license",
    ),
    KIND_UNKNOWN: (KIND_UNKNOWN,),
}


def _normalize(text: str) -> str:
    """归一化许可写法：小写、去协议头、去分隔符（``-`` / ``_`` / 空白）。

    这样 ``https://creativecommons.org/licenses/by/4.0/``、
    ``http://creativecommons.org/licenses/by/4.0``、
    ``CC BY 4.0``、``cc-by-4.0`` 归一化成同一个键。
    """
    value = (text or "").strip().lower()
    value = value.replace("https://", "").replace("http://", "")
    value = value.removeprefix("www.")
    value = value.rstrip("/")
    return re.sub(r"[\s\-_]+", "", value)


#: 归一化写法 → 许可种类。
_KIND_BY_ALIAS: dict[str, str] = {
    _normalize(alias): kind for kind, aliases in _ALIASES.items() for alias in aliases
}


def license_kind_from_uri(uri: str) -> str:
    """许可 URI → 许可种类。

    认 https/http、末尾有无斜杠、CC 缩写写法（``CC BY 4.0`` /
    ``cc-by-nc-sa-4.0`` 等），也认 **kind 字符串本身**
    （``"arxiv-nonexclusive-1.0"`` / ``"cc-by-4.0"``——``book.json`` 的
    ``paper_license`` 有可能就是这么写的）。

    **认不出就返回 ``"unknown"``**——调用方按最严策略处理（不许蒙混过关）。
    """
    key = _normalize(uri)
    if not key:
        return KIND_UNKNOWN
    return _KIND_BY_ALIAS.get(key, KIND_UNKNOWN)


def license_flags(kind: str) -> dict[str, bool]:
    """许可种类 → 旗标：``nc`` / ``nd`` / ``sa`` / ``permissive``。

    - ``nc``：禁止商用——CC BY-NC-SA / CC BY-NC-ND；
    - ``nd``：禁止改作（NoDerivatives）——CC BY-NC-ND；
    - ``sa``：相同方式共享——CC BY-SA / CC BY-NC-SA；
    - ``permissive``：既非 NC 也非 ND（CC BY / CC BY-SA / CC0）。
    """
    value = str(kind or "")
    return {
        "nc": value in (KIND_CC_BY_NC_SA_4_0, KIND_CC_BY_NC_ND_4_0),
        "nd": value == KIND_CC_BY_NC_ND_4_0,
        "sa": value in (KIND_CC_BY_SA_4_0, KIND_CC_BY_NC_SA_4_0),
        "permissive": value in PERMISSIVE_KINDS,
    }


# ===================== 短引用的长度闸门 =====================

#: 「短引用」的字符数上限。
#:
#: 2026-11-08 第二次拍板以后，公开包**不再带任何原文逐字内容**，这两个上限
#: 不再参与公开包裁剪；留着给社区层判断「这段引用算不算短引用」。
QUOTE_MAX_CHARS = 200
#: 「短引用」的词数上限（中文按字符算）。
QUOTE_MAX_WORDS = 40

#: 出处文本的最小长度（太短的出处等于没出处）。
ATTRIBUTION_MIN_CHARS = 20

#: arXiv 新号形态：``2401.12345`` / ``2401.12345v2``。
_ARXIV_ID_RE = re.compile(r"\d{4}\.\d{4,5}(v\d+)?")


def _is_cjk(char: str) -> bool:
    """是不是汉字（中文没有空格，词数按字符算）。"""
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF  # 基本汉字
        or 0x3400 <= code <= 0x4DBF  # 扩展 A
        or 0xF900 <= code <= 0xFAFF  # 兼容汉字
        or 0x20000 <= code <= 0x2A6DF  # 扩展 B
        or 0x3040 <= code <= 0x30FF  # 日文假名
        or 0xAC00 <= code <= 0xD7AF  # 韩文
    )


def _word_count(text: str) -> int:
    """词数：按空白切分；**中文按字符算**（中文不用空格分词）。

    纯中文一句话 ``良渚文化位于长江下游`` = 9 词；纯英文 9 个单词 = 9 词。
    """
    count = 0
    for token in text.split():
        cjk = sum(1 for char in token if _is_cjk(char))
        count += max(cjk, 1)
    return count


def quote_within_limits(text: str) -> bool:
    """这段引用算不算「短引用」。

    字符数 ≤ :data:`QUOTE_MAX_CHARS` **且** 词数 ≤ :data:`QUOTE_MAX_WORDS`。
    空文本不算。

    （公开包裁剪已改为与书同规则，这个判定现在只给社区层用。）
    """
    value = (text or "").strip()
    if not value:
        return False
    return len(value) <= QUOTE_MAX_CHARS and _word_count(value) <= QUOTE_MAX_WORDS


# ===================== 出处四要素 =====================


def attribution_ok(text: str) -> bool:
    """出处文本够不够格（四要素齐全）。

    必须**同时**满足：

    1. 有 arXiv id（新号形态 ``\\d{4}\\.\\d{4,5}(v\\d+)?``）；
    2. 有链接（``arxiv.org/abs/`` 或 ``http``）；
    3. 长度 ≥ :data:`ATTRIBUTION_MIN_CHARS` 字符。
    """
    value = (text or "").strip()
    if len(value) < ATTRIBUTION_MIN_CHARS:
        return False
    if not _ARXIV_ID_RE.search(value):
        return False
    return "arxiv.org/abs/" in value or "http" in value


def _authors_text(raw: object) -> str:
    """作者字段 → 文本（``", ".join``）；没有就 ``"Unknown authors"``。"""
    if isinstance(raw, str):
        value = raw.strip()
        return value or "Unknown authors"
    if isinstance(raw, (list, tuple)):
        names = [str(item).strip() for item in raw if str(item).strip()]
        return ", ".join(names) if names else "Unknown authors"
    return "Unknown authors"


def build_attribution(book: dict) -> str:
    """从 ``book.json`` 拼出处文本。

    格式::

        {authors}. "{title}". arXiv:{arxiv_id} ({updated}). {abs_url} — License: {kind} ({uri})

    **缺项不编**：缺作者写 ``"Unknown authors"``；缺标题 / arXiv id / 更新日期
    的那一段直接不出现（不留空壳）。
    """
    data = book or {}
    authors = _authors_text(data.get("authors"))
    title = str(data.get("title") or "").strip()
    arxiv_id = str(data.get("arxiv_id") or "").strip()
    updated = str(data.get("arxiv_updated") or "").strip()
    kind = license_kind_from_uri(str(data.get("paper_license") or ""))
    uri = LICENSE_URI.get(kind, "")

    pieces = [f"{authors}."]
    if title:
        pieces.append(f'"{title}".')
    if arxiv_id:
        piece = f"arXiv:{arxiv_id}"
        if updated:
            piece += f" ({updated})"
        piece += f". {ABS_URL_PREFIX}{arxiv_id}"
        pieces.append(piece)
    pieces.append(f"— License: {kind} ({uri})")
    return " ".join(pieces)


# ===================== 照抄检查（公开包的硬检查） =====================

#: 纯拉丁文的照抄阈值：公共子串 ≥ 这个长度 ⇒ 判为照抄。
VERBATIM_RUN_LATIN = 60
#: 含汉字的照抄阈值：中文一个字顶一句，门槛低一半。
VERBATIM_RUN_CJK = 30


def _has_cjk(text: str) -> bool:
    """这段文字里有没有汉字。"""
    return any(_is_cjk(char) for char in text or "")


def _has_common_run(text: str, other: str, length: int) -> bool:
    """``text`` 里有没有长度 ≥ ``length`` 的一段**原样**出现在 ``other`` 里。"""
    if length <= 0:
        return True
    for start in range(0, len(text) - length + 1):
        if text[start : start + length] in other:
            return True
    return False


def max_common_run(text: str, other: str, *, min_len: int) -> int:
    """两段文字的最长公共子串长度（比 ``min_len`` 短就返回 0）。

    走「子串 in 另一段」的二分：长度有单调性（L 能对上，L-1 一定对得上），
    所以二分成立。**不用** O(n·m) 的 DP——页原文几千字符 × 几百张卡会太慢。
    """
    first = text or ""
    second = other or ""
    if not first or not second or min_len <= 0:
        return 0
    low = min_len
    high = min(len(first), len(second))
    best = 0
    while low <= high:
        middle = (low + high) // 2
        if _has_common_run(first, second, middle):
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return best


def is_verbatim_copy(text: str, page_text: str) -> bool:
    """解释是不是大段照抄了原文（``docs/license-v1.md`` §4 第 3 条）。

    判据：解释与页原文的公共子串——

    - 含汉字的 ≥ :data:`VERBATIM_RUN_CJK`（30）字符 ⇒ 照抄；
    - 纯拉丁文的 ≥ :data:`VERBATIM_RUN_LATIN`（60）字符 ⇒ 照抄。

    「公共子串」指解释里**原样出现在页原文里**的一段连续文字。解释里引一两个
    术语（如 ``attention mechanism``）不算照抄。
    """
    first = text or ""
    second = page_text or ""
    if not first or not second:
        return False
    # 长段照抄：60 字符（中文的长段也顺带被这一条抓到）。
    if max_common_run(first, second, min_len=VERBATIM_RUN_LATIN) >= VERBATIM_RUN_LATIN:
        return True
    # 含汉字的短段照抄：30 字符就够（中文一个字顶一句）。
    for start in range(0, len(first) - VERBATIM_RUN_CJK + 1):
        window = first[start : start + VERBATIM_RUN_CJK]
        if _has_cjk(window) and window in second:
            return True
    return False


# ===================== 卡片分享性质 =====================

#: 术语卡（term）的正面长度上限。
SHARE_MAX_FRONT_CHARS = 40
#: 术语卡（term）的背面长度上限。
SHARE_MAX_BACK_CHARS = 80
#: 正面里出现这些标点 ⇒ 是一句话（结论复述），不是术语。
SHARE_SENTENCE_END_MARKS = "。！？.!?；;"

#: 分享性质。
SHARE_TERM = "term"
SHARE_SUMMARY = "summary"


def card_share(card: dict) -> str:
    """这张卡是「术语卡」还是「结论复述卡」。

    规则：

    1. 卡上显式写了 ``card["share"]`` 且值是 ``"term"`` / ``"summary"`` ⇒ 用它；
    2. 否则启发式——正面去空白后 ≤ 40 字符、不含句末标点，且背面 ≤ 80 字符
       ⇒ ``"term"``；
    3. **判定不了/信息不足一律按 ``"summary"``**（保守：宁可少发）。

    （2026-11-08 第二次拍板以后，结论复述卡**可以进公开包**——著作权保护表达、
    不保护思想与事实，只要出处写清、不照抄原文就行。本函数现在只用于
    **统计**（术语卡 / 结论卡各多少），不参与打包裁剪。）
    """
    data = card or {}
    explicit = data.get("share")
    if isinstance(explicit, str):
        value = explicit.strip().lower()
        if value in (SHARE_TERM, SHARE_SUMMARY):
            return value

    front = str(data.get("front") or "").strip()
    back = str(data.get("back") or "").strip()
    if not front or not back:
        # 信息不足 ⇒ 保守当复述卡。
        return SHARE_SUMMARY
    if len(front) > SHARE_MAX_FRONT_CHARS:
        return SHARE_SUMMARY
    if any(char in front for char in SHARE_SENTENCE_END_MARKS):
        return SHARE_SUMMARY
    if len(back) > SHARE_MAX_BACK_CHARS:
        return SHARE_SUMMARY
    return SHARE_TERM


# ===================== 公开包策略 =====================


@dataclass(frozen=True)
class PublicPolicy:
    """许可种类 → 许可属性（**记录用，不裁剪**）。

    2026-11-08 第二次拍板以后，公开包的裁剪已改为与书源同规则（见
    :func:`public_policy` 的说明），``allow_*`` 那几个字段**不再参与裁剪**，
    留着是为了兼容第一次拍板时的调用方。
    """

    #: 允许带「短引用」（依据句）——**已停用**（公开包不再带原文逐字内容）。
    allow_quote: bool
    #: 允许带 context（原文上下文块）——**已停用**。
    allow_context: bool
    #: 允许带结论复述卡（整张卡）——**已停用**（结论卡现在可以进公开包）。
    allow_summary_cards: bool
    #: 许可禁止商用。
    nc: bool
    #: 许可禁止改作。
    nd: bool
    #: 必须带出处。
    require_attribution: bool
    #: 非商用许可。
    noncommercial: bool
    #: 许可要求相同方式共享。
    sa: bool = False
    #: 许可宽松（非 NC 非 ND）。
    permissive: bool = False


def public_policy(kind: str) -> PublicPolicy:
    """许可种类 → 许可属性（**记录用，不裁剪**）。

    2026-11-08 第二次拍板（「和书一样处理」）以后，**公开包的裁剪与书源同规则**：
    公开包只放词汇 + LLM 解释（第一句）+ 出处，不带原文逐字内容、不带图——
    许可**不决定**公开包带什么。许可仍然要**记录**：社区层按 ``nc`` /
    默认许可给发布提示，作者要知道这篇能不能商用。

    所以本表现在只关心四件事：``nc`` / ``nd`` / ``sa`` / ``noncommercial``。

    - 宽松（CC BY / CC BY-SA / CC0）：非商用标记为否；
    - CC BY-NC-SA / CC BY-NC-ND：非商用（``noncommercial`` 为真）；
    - 默认 arXiv 许可 / 认不出的许可：按「默认许可」记，旗标全否。
    """
    value = str(kind or "")
    flags = license_flags(value)

    if value in PERMISSIVE_KINDS:
        return PublicPolicy(
            allow_quote=True,
            allow_context=True,
            allow_summary_cards=True,
            nc=flags["nc"],
            nd=flags["nd"],
            require_attribution=True,
            noncommercial=False,
            sa=flags["sa"],
            permissive=flags["permissive"],
        )
    if value == KIND_CC_BY_NC_SA_4_0:
        return PublicPolicy(
            allow_quote=True,
            allow_context=True,
            allow_summary_cards=True,
            nc=True,
            nd=False,
            require_attribution=True,
            noncommercial=True,
            sa=flags["sa"],
            permissive=False,
        )
    if value == KIND_CC_BY_NC_ND_4_0:
        return PublicPolicy(
            allow_quote=True,
            allow_context=False,
            allow_summary_cards=False,
            nc=True,
            nd=True,
            require_attribution=True,
            noncommercial=True,
            sa=flags["sa"],
            permissive=False,
        )
    # 默认 arXiv 许可（nonexclusive / assumed-1991-2003）和认不出的许可：最严。
    return PublicPolicy(
        allow_quote=True,
        allow_context=False,
        allow_summary_cards=False,
        nc=flags["nc"],
        nd=flags["nd"],
        require_attribution=True,
        noncommercial=False,
        sa=flags["sa"],
        permissive=flags["permissive"],
    )


__all__ = [
    "ABS_URL_PREFIX",
    "ATTRIBUTION_MIN_CHARS",
    "KIND_ARXIV_NONEXCLUSIVE_1_0",
    "KIND_ASSUMED_1991_2003",
    "KIND_CC0_1_0",
    "KIND_CC_BY_4_0",
    "KIND_CC_BY_NC_ND_4_0",
    "KIND_CC_BY_NC_SA_4_0",
    "KIND_CC_BY_SA_4_0",
    "KIND_UNKNOWN",
    "LICENSE_KINDS",
    "LICENSE_URI",
    "PERMISSIVE_KINDS",
    "PublicPolicy",
    "QUOTE_MAX_CHARS",
    "QUOTE_MAX_WORDS",
    "SHARE_MAX_BACK_CHARS",
    "SHARE_MAX_FRONT_CHARS",
    "SHARE_SENTENCE_END_MARKS",
    "SHARE_SUMMARY",
    "SHARE_TERM",
    "VERBATIM_RUN_CJK",
    "VERBATIM_RUN_LATIN",
    "attribution_ok",
    "build_attribution",
    "card_share",
    "is_verbatim_copy",
    "license_flags",
    "license_kind_from_uri",
    "max_common_run",
    "public_policy",
    "quote_within_limits",
]
