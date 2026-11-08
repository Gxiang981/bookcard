"""§7 / §7.1 卡包 ``pack/<fp12>.ttfc.zip``（私有）/ ``pack/<fp12>.public.ttfc.zip``（公开）。

zip 内：``manifest.json`` +（图片场景才有）``images/p<4 位页号>.jpg``（150 dpi，JPEG 质量 85）。

manifest 形状见契约 §7::

    {"format": "ttfc/2", "visibility": "private",
     "book": {…book.json 全部字段…},
     "provenance": {"tool", "method", "extractor", "created_at"},
     "quality": {…quality_report.json…},
     "license": {"cards": "private", "page_images": "private"},
     "category": {"name", "description", "sort_order",
                  "libraries": [{"name", "slug", "sort_order", "cards": [...]}]}}

**两档（契约 §7.1）**：

| | 公开版 ``--edition public`` | 内部分享版 ``--edition private``（默认） |
|---|---|---|
| 背面 | 只有一句解释 | 解释全文 |
| 场景 | 无 | 按书的 ``scene_mode``：文本块 or 书页图 |
| 书页图 | **禁止**（打包时强校验） | 只有 ``scene_mode == "image"`` 时带 |
| ``source`` | 只留 ``page / printed_page / bbox / verbatim / method`` | 全部 |
| ``book.fingerprint`` | **必填**（缺失即失败） | 有就带 |
| ``license`` | ``{"cards": "CC-BY-SA-4.0", "page_images": "none"}`` | 私有授权 |

manifest 必须能通过 App 的 ``BulkImportBody``
（``00_flashcard/server-python/app/routes/imports.py``）。
**本模块不 import App 代码**：这里复制一份最小校验（字段、长度、类型），
与 App 的 pydantic 模型一一对应；单测里再用一份 pydantic 模型复核一遍。

**论文源**（``book["type"] == "paper"``）**和书源同规则**
（2026-11-08 第二次拍板）：公开包只放词汇 + LLM 解释（第一句）+ 出处，
不带原文逐字内容、不带图；私有包照旧带页面图、证据句和上下文。
**论文许可只记录、不裁剪**：``manifest.source_license`` / ``manifest.attribution``
记下许可名、许可地址和出处四要素（社区层按 ``nc`` / 默认许可给提示）。
公开包唯一多出来的一条硬检查：**解释不得大段照抄原文**（公共子串 ≥ 60 字符
（纯拉丁文）或 ≥ 30 字符（含中文）⇒ 这张卡不进公开包）。
判定规则在 :mod:`bookcard.pipeline.license_gate`。**书本来源的既有行为一字不改**
（README §7 已冻结）。

打包前必须过三道校验（契约 §7 + 本单要求）：

1. ``toc.json`` 和 ``trial.json`` 的闸门**都是** ``approved``；
2. 每张卡的 ``front`` 长度 1–500（App 限制）；
3. manifest 形状能通过 ``BulkImportBody``。
"""

from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path
from typing import Any

import pymupdf

from bookcard import __version__
from bookcard.pipeline.license_gate import (
    LICENSE_URI,
    SHARE_SUMMARY,
    PublicPolicy,
    build_attribution,
    card_share,
    is_verbatim_copy,
    license_flags,
    license_kind_from_uri,
    public_policy,
)
from bookcard.pipeline.scene import (
    DEFAULT_SCENE,
    SCENE_IMAGE,
    SCENE_TEXT,
    build_contexts,
    detect_scene_mode,
)
from bookcard.pipeline.workdir import PipelineError, WorkDir, content_fp, write_json

#: 卡包格式版本（契约 §7）。
PACK_FORMAT = "ttfc/2"

#: 两档（契约 §7.1）。
EDITION_PRIVATE = "private"
EDITION_PUBLIC = "public"
EDITIONS = (EDITION_PRIVATE, EDITION_PUBLIC)

#: 私有包的可见性 / 授权。
PACK_VISIBILITY = "private"
PRIVATE_LICENSE = {"cards": "private", "page_images": "private"}

#: 公开包的可见性 / 授权（契约 §7.1）。
PUBLIC_VISIBILITY = "public"
PUBLIC_LICENSE = {"cards": "CC-BY-SA-4.0", "page_images": "none"}

#: 公开版保留的 ``source`` 字段（契约 §7.1）。
PUBLIC_SOURCE_FIELDS = ("page", "printed_page", "bbox", "verbatim", "method")

#: 公开版 ``book`` 去掉的本地字段（契约 §7.1：本机路径、原始文件名不外发）。
PUBLIC_BOOK_DROP_FIELDS = ("nas_path", "file_name")

#: 公开版：解释取到第一个句末标点为止（契约 §7.1）。
SENTENCE_END_MARKS = "。！？.!?"

#: 场景值是谁定的（契约 §7.1）：``"user"`` 用户/harness 当场问过用户 /
#: ``"default"`` 用的默认场景 / ``"cli"`` 命令行显式传了 ``--scene``。
SCENE_CHOSEN_BY_DEFAULT = "default"
SCENE_CHOSEN_BY_CLI = "cli"
SCENE_CHOSEN_BY_USER = "user"
SCENE_CHOSEN_BY_ALL = (SCENE_CHOSEN_BY_USER, SCENE_CHOSEN_BY_CLI, SCENE_CHOSEN_BY_DEFAULT)

#: App 的限制（``_CardIn.front``）。
MAX_FRONT_LEN = 500

#: App 的限制（``_CatIn.name`` / ``_LibIn.name``）。
MAX_NAME_LEN = 255

#: zip 里出现这些扩展名就算「有图片」（公开版禁止）。
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif")



class PackError(PipelineError):
    """打包前的校验没过（闸门没过、字段超限、形状不对）。"""


# ===================== manifest 校验（对应 App 的 BulkImportBody） =====================


def _fail(message: str) -> None:
    raise PackError(message)


def _check_str(value: Any, field: str, *, min_len: int = 0, max_len: int | None = None) -> str:
    if not isinstance(value, str):
        _fail(f"{field} 必须是字符串（现在是 {type(value).__name__}）")
    if len(value) < min_len:
        _fail(f"{field} 不能为空")
    if max_len is not None and len(value) > max_len:
        _fail(f"{field} 超过 {max_len} 字符（{len(value)}）")
    return value


#: learn_value → App 的 difficulty（1–5）。
#: 1 可有可无 → 2（T1）、2 值得学 → 3（T2）、3 核心概念 → 4（T3）。
_DIFFICULTY_BY_LEARN_VALUE = {1: 2, 2: 3, 3: 4}


def _difficulty_of(learn_value: object) -> int:
    """卡的学习价值 → App 的难度档位（1–5）。没有 learn_value 就给中间档。"""
    try:
        value = int(learn_value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 3
    return _DIFFICULTY_BY_LEARN_VALUE.get(value, 3)


def _check_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{field} 必须是整数（现在是 {type(value).__name__}）")
    return value


def _check_optional_int(value: Any, field: str) -> None:
    if value is None:
        return
    _check_int(value, field)


def _check_optional_str(value: Any, field: str, *, max_len: int | None = None) -> None:
    if value is None:
        return
    _check_str(value, field, max_len=max_len)


def validate_manifest(manifest: dict) -> None:
    """校验 manifest 能通过 App 的 ``BulkImportBody``。

    字段和限制逐条照抄 App 的 pydantic 模型（``_CatIn`` / ``_LibIn`` /
    ``_CardIn`` / ``_ImageIn`` / ``_Occurrence``）。**额外字段不报错**——
    App 的 pydantic 默认忽略它们，契约 §7 的 v2 字段就是这么活的。
    """
    if not isinstance(manifest, dict):
        _fail("manifest 必须是对象")

    category = manifest.get("category")
    if not isinstance(category, dict):
        _fail("manifest 缺 category")
    _check_str(category.get("name"), "category.name", min_len=1, max_len=MAX_NAME_LEN)
    _check_optional_str(category.get("description"), "category.description")
    _check_int(category.get("sort_order", 0), "category.sort_order")

    libraries = category.get("libraries")
    if not isinstance(libraries, list):
        _fail("category.libraries 必须是数组")

    for lib_index, library in enumerate(libraries):
        prefix = f"category.libraries[{lib_index}]"
        if not isinstance(library, dict):
            _fail(f"{prefix} 必须是对象")
        _check_str(library.get("name"), f"{prefix}.name", min_len=1, max_len=MAX_NAME_LEN)
        _check_optional_str(library.get("slug"), f"{prefix}.slug", max_len=MAX_NAME_LEN)
        _check_optional_str(library.get("description"), f"{prefix}.description")
        _check_int(library.get("sort_order", 0), f"{prefix}.sort_order")

        cards = library.get("cards")
        if not isinstance(cards, list):
            _fail(f"{prefix}.cards 必须是数组")

        for card_index, card in enumerate(cards):
            card_prefix = f"{prefix}.cards[{card_index}]"
            if not isinstance(card, dict):
                _fail(f"{card_prefix} 必须是对象")
            _check_str(
                card.get("front"),
                f"{card_prefix}.front",
                min_len=1,
                max_len=MAX_FRONT_LEN,
            )
            _check_str(card.get("back", ""), f"{card_prefix}.back")
            _check_optional_str(card.get("phonetic"), f"{card_prefix}.phonetic")
            _check_optional_str(card.get("pos"), f"{card_prefix}.pos")
            _check_int(card.get("difficulty", 1), f"{card_prefix}.difficulty")
            _check_int(card.get("sort_order", 0), f"{card_prefix}.sort_order")

            tags = card.get("tags", [])
            if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                _fail(f"{card_prefix}.tags 必须是字符串数组")

            images = card.get("images", [])
            if not isinstance(images, list):
                _fail(f"{card_prefix}.images 必须是数组")
            for image_index, image in enumerate(images):
                image_prefix = f"{card_prefix}.images[{image_index}]"
                if not isinstance(image, dict):
                    _fail(f"{image_prefix} 必须是对象")
                # App 的 _ImageIn 要 content_base64；zip 流程里由 `file` 还原。
                file_name = image.get("file")
                if file_name is None:
                    _fail(f"{image_prefix} 缺 file（zip 内图片路径）")
                _check_str(file_name, f"{image_prefix}.file", min_len=1)
                _check_optional_str(image.get("alt_text"), f"{image_prefix}.alt_text")
                _check_optional_str(image.get("caption"), f"{image_prefix}.caption")
                _check_optional_int(image.get("width"), f"{image_prefix}.width")
                _check_optional_int(image.get("height"), f"{image_prefix}.height")

                occurrences = image.get("occurrences", [])
                if not isinstance(occurrences, list):
                    _fail(f"{image_prefix}.occurrences 必须是数组")
                for occ_index, occurrence in enumerate(occurrences):
                    occ_prefix = f"{image_prefix}.occurrences[{occ_index}]"
                    if not isinstance(occurrence, dict):
                        _fail(f"{occ_prefix} 必须是对象")
                    for axis in ("x", "y", "w", "h"):
                        _check_int(occurrence.get(axis), f"{occ_prefix}.{axis}")
                    _check_optional_int(occurrence.get("page"), f"{occ_prefix}.page")
                    score = occurrence.get("score")
                    if score is not None and not isinstance(score, (int, float)):
                        _fail(f"{occ_prefix}.score 必须是数字")


# ===================== manifest 组装 =====================


def _category_description(book: dict) -> str:
    """词库描述：``<出版社 版次>``（缺的留空，不编）。"""
    parts = [str(book.get(key) or "").strip() for key in ("publisher", "edition")]
    return " ".join(part for part in parts if part)


def _extractor_of(work: WorkDir, cards: list[dict]) -> str:
    """抽取器标识：优先取候选行上的 ``extractor``，否则从卡的 ``source.method`` 推。"""
    try:
        for line in work.candidates():
            extractor = line.get("extractor")
            if extractor:
                return str(extractor)
    except PipelineError:
        pass
    for card in cards:
        method = (card.get("source") or {}).get("method")
        if method:
            return str(method)
    return "llm"


def _method_of(cards: list[dict]) -> str:
    """``provenance.method``：默认 ``llm``。"""
    for card in cards:
        method = (card.get("source") or {}).get("method")
        if method:
            return str(method)
    return "llm"


# ===================== 公开版的裁剪 =====================


def _is_cjk(char: str) -> bool:
    """是不是汉字（用来认英文点号是不是句末）。"""
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF  # 基本汉字
        or 0x3400 <= code <= 0x4DBF  # 扩展 A
        or 0xF900 <= code <= 0xFAFF  # 兼容汉字
        or 0x20000 <= code <= 0x2A6DF  # 扩展 B
        or 0x3040 <= code <= 0x30FF  # 日文假名
        or 0xAC00 <= code <= 0xD7AF  # 韩文
    )


def _is_sentence_end(text: str, index: int) -> bool:
    """这个位置的标点算不算**句末**标点。

    英文点号 ``.`` 有两种用法：句末，和契约 §4 的**词性标签**
    （``n. 发明；创造`` / ``phrase. 与小组一起学习``）。照字面切会把
    英语教材里绝大多数解释切成 ``n.``（实测三本真书里有一本 87% 的卡
    撞上这个）。判据：

    1. 点号后面（跳过空白）不是中文字符 ⇒ 才算句末；
    2. 点号前面是**单个**拉丁字母（``e.g.`` 的第二个点）⇒ 缩写，不算句末。
    """
    mark = text[index]
    if mark != ".":
        return True

    rest = text[index + 1 :].lstrip()
    if rest and _is_cjk(rest[0]):
        return False

    prefix = text[:index]
    if prefix and prefix[-1].isascii() and prefix[-1].isalpha():
        head = prefix[:-1]
        if not head or not (head[-1].isascii() and head[-1].isalpha()):
            return False  # 单个字母 + 点号 ⇒ 缩写（e.g. / n.）
    return True


def first_sentence(text: str) -> str:
    """取到第一个句末标点为止（含标点）——公开版的背面（契约 §7.1）。"""
    value = (text or "").strip()
    if not value:
        return ""
    for index, char in enumerate(value):
        if char in SENTENCE_END_MARKS and _is_sentence_end(value, index):
            return value[: index + 1]
    return value


def _public_source(source: dict) -> dict:
    """公开版的 ``source``：只留 ``page / printed_page / bbox / verbatim / method``。"""
    return {field: source.get(field) for field in PUBLIC_SOURCE_FIELDS}


# ===================== 论文源（2026-11-08 两次拍板：和书一样处理） =====================

#: 论文源在 ``book.json`` 里的类型标记。
PAPER_SOURCE_TYPE = "paper"


def _is_paper(book: dict) -> bool:
    """这本书是不是论文源（``book["type"] == "paper"``）。"""
    return str((book or {}).get("type") or "") == PAPER_SOURCE_TYPE


def _paper_gate(book: dict) -> tuple[str, PublicPolicy | None]:
    """论文源的许可记录：``(许可种类, 许可属性)``。

    许可种类来自 ``book["paper_license"]``（写这个字段）；缺字段
    或认不出 ⇒ ``"unknown"``。

    **许可只记录、不裁剪**（2026-11-08 第二次拍板）：公开包的裁剪与书源同规则，
    这里返回的属性只写进 manifest（社区层按 ``nc`` / 默认许可给提示）。

    **书本源返回 ``("", None)``**——既有行为一个字都不改（README §7 已冻结）。
    """
    if not _is_paper(book):
        return "", None
    kind = license_kind_from_uri(str((book or {}).get("paper_license") or ""))
    return kind, public_policy(kind)


def _attribution_of(book: dict) -> str:
    """出处文本：``book["attribution"]`` 优先，否则按四要素拼。"""
    existing = (book or {}).get("attribution")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    return build_attribution(book)


def _page_texts(work: WorkDir) -> dict[int, str]:
    """页号 → 页原文（照抄检查用）。

    没有 ``pages.jsonl`` 或读不出来 ⇒ 空字典 ⇒ 照抄检查**跳过**（不误杀）。
    """
    try:
        pages = work.pages()
    except PipelineError:
        return {}
    texts: dict[int, str] = {}
    for row in pages:
        page = row.get("page")
        text = str(row.get("text") or "")
        if isinstance(page, int) and text:
            texts[page] = text
    return texts


def _is_verbatim(card: dict, back: str, page_texts: dict[int, str]) -> bool:
    """解释是不是大段照抄了原文（``docs/license-v1.md`` §4 第 3 条）。

    公开包唯一多出来的硬检查：解释整段与**该卡所在页原文**的公共子串太长 ⇒
    这张卡不进公开包（否则等于把原文塞回公开包）。

    **查不到该卡所在页的原文 ⇒ 跳过检查**（不误杀）。
    """
    page = (card.get("source") or {}).get("page")
    if not isinstance(page, int):
        return False
    page_text = page_texts.get(page)
    if not page_text:
        return False
    return is_verbatim_copy(back, page_text)


def _require_fingerprint(book: dict) -> None:
    """公开版必须带 ``book.fingerprint``（契约 §7.1：缺失即失败）。"""
    fingerprint = book.get("fingerprint")
    if not fingerprint:
        raise PackError("公开版必须有 book.fingerprint（缺了就发不了）")
    if isinstance(fingerprint, dict) and not fingerprint:
        raise PackError("公开版的 book.fingerprint 是空的")


def _write_scene_mode(
    work: WorkDir,
    scene_mode: str,
    *,
    scene_suggested: str | None = None,
    scene_chosen_by: str | None = None,
) -> dict:
    """把 ``scene_mode`` 写进 ``book.json``（契约 §7.1：整本书一个值）。

    顺带写 ``scene_suggested``（默认场景，恒为 ``image``）和 ``scene_chosen_by``
    （这个值是谁定的：``"user"`` 用户选的 / ``"cli"`` 命令行定的 /
    ``"default"`` 用的默认场景）。
    """
    book = work.book()
    # 旧的手改产物脚本（已退役）留过一条 ``scene_chosen_by_note``——契约里没有这个
    # 字段，它和新的取值会互相矛盾（取值说 "user"，备注说「非用户亲选」）。
    # 引擎重写取值时把它清掉：同一个包里不许有两种说法。
    stale_note = "scene_chosen_by_note" in book
    unchanged = (
        book.get("scene_mode") == scene_mode
        and (scene_suggested is None or book.get("scene_suggested") == scene_suggested)
        and (scene_chosen_by is None or book.get("scene_chosen_by") == scene_chosen_by)
        and not stale_note
    )
    if unchanged:
        return book
    book["scene_mode"] = scene_mode
    if scene_suggested is not None:
        book["scene_suggested"] = scene_suggested
    if scene_chosen_by is not None:
        book["scene_chosen_by"] = scene_chosen_by
        book.pop("scene_chosen_by_note", None)
    write_json(work.book_path, book)
    return book


def _assert_no_images(manifest: dict) -> None:
    """公开版：manifest 里不许有图（硬校验）。"""
    for library in manifest.get("category", {}).get("libraries", []):
        for card in library.get("cards", []):
            if card.get("images"):
                raise PackError(f"公开版不许带页面图：{card.get('front')!r}")


def _assert_zip_has_no_images(target: Path) -> None:
    """公开版：zip 里出现任何图片即失败（契约 §7.1）。"""
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
    for name in names:
        lowered = name.lower()
        if lowered.startswith("images/") or lowered.endswith(IMAGE_SUFFIXES):
            raise PackError(f"公开版的 zip 里不许有图片：{name}")


def build_manifest(
    work: WorkDir,
    *,
    edition: str = EDITION_PRIVATE,
    scene_mode: str = SCENE_IMAGE,
    scene_override: str | None = None,
    detection: dict | None = None,
    contexts: dict[str, dict] | None = None,
    scene_chosen_by: str | None = None,
) -> tuple[dict, list[str]]:
    """组装 manifest，返回 ``(manifest, 用到的页面图相对路径列表)``。

    调用方负责在写包之前跑 :func:`validate_manifest`。

    参数
    ----
    edition:
        ``private``（默认）或 ``public``（契约 §7.1）。
    scene_mode:
        ``text`` / ``image``——私有版按这个二选一（文本块 or 书页图）。
    scene_override:
        用户用 ``--scene`` 覆盖时的值（写进 ``provenance.scene_override``）。
    detection:
        自动判定的过程数据（写进 ``provenance.scene_detection``，方便审计）。
    contexts:
        ``card_id → source.context``（文本场景用）。
    scene_chosen_by:
        这个场景值是谁定的：``"user"``（用户选的）/ ``"cli"``（命令行定的）/
        ``"default"``（用的默认场景）。
    """
    if edition not in EDITIONS:
        raise PackError(f"edition 只能是 {' 或 '.join(EDITIONS)}（现在是 {edition!r}）")

    book = work.book()
    sections = work.sections()
    cards = work.cards()
    quality = work.quality()

    fp12 = str(book.get("fp12") or "")
    if not fp12:
        raise PackError(f"book.json 里没有 fp12：{work.book_path}")

    contexts = contexts or {}

    # 论文源：许可与出处只**记录**；公开包多一条「解释不得大段照抄原文」的硬检查。
    # 书本源是 ``("", None)``——行为不变。
    license_kind, policy = _paper_gate(book)
    attribution = _attribution_of(book) if policy is not None else ""
    dropped_verbatim = 0
    term_cards = 0
    summary_cards = 0
    page_texts = _page_texts(work) if policy is not None else {}

    cards_by_sid: dict[str, list[dict]] = {}
    for card in cards:
        cards_by_sid.setdefault(str(card.get("sid") or ""), []).append(card)

    # 页面图尺寸（150 dpi 渲染出来的像素宽高）——用 pymupdf 读，不引入 PIL。
    image_sizes: dict[str, tuple[int, int]] = {}
    for path in sorted(work.images_dir.glob("*.jpg")):
        try:
            pixmap = pymupdf.Pixmap(str(path))
        except Exception:  # noqa: BLE001 - 读不出尺寸就当没有（App 允许缺省）
            continue
        image_sizes[path.name] = (pixmap.width, pixmap.height)

    used_images: list[str] = []
    libraries: list[dict] = []
    for order_index, section in enumerate(sections, 1):
        sid = str(section.get("sid") or "")
        section_cards = cards_by_sid.get(sid, [])
        if not section_cards:
            continue

        manifest_cards: list[dict] = []
        for card in sorted(section_cards, key=lambda item: int(item.get("sort_order") or 0)):
            raw_source = dict(card.get("source") or {})
            source = raw_source
            back = str(card.get("back") or "")
            images: list[dict] = []

            if policy is not None:
                # 卡片性质统计（术语卡 / 结论复述卡）——**只统计，不裁剪**：
                # 结论复述卡可以进公开包（出处写清、不照抄即可）。
                if card_share(card) == SHARE_SUMMARY:
                    summary_cards += 1
                else:
                    term_cards += 1

            if edition == EDITION_PUBLIC:
                # 论文源：解释大段照抄原文 ⇒ 这张卡不进公开包（docs/license-v1.md §4）。
                if policy is not None and _is_verbatim(card, back, page_texts):
                    dropped_verbatim += 1
                    continue
                # 公开版：背面只有一句解释；source 只留 5 个字段；不带图。
                # **论文源与书源走同一条路**（2026-11-08 第二次拍板）。
                back = first_sentence(back)
                source = _public_source(raw_source)
            elif scene_mode == SCENE_TEXT:
                # 内部分享版 + 文本场景：加 context，不带页面图。
                context = contexts.get(str(card.get("card_id") or ""))
                if context:
                    source["context"] = context
            else:
                # 内部分享版 + 图片场景：同 §7 现状——页面图 + occurrences。
                image_rel = source.get("image")
                if image_rel:
                    image_name = str(image_rel).split("/")[-1]
                    size = image_sizes.get(image_name)
                    occurrences: list[dict] = []
                    bbox = source.get("bbox")
                    if isinstance(bbox, dict):
                        occurrences.append(
                            {
                                "x": int(bbox.get("x") or 0),
                                "y": int(bbox.get("y") or 0),
                                "w": int(bbox.get("w") or 0),
                                "h": int(bbox.get("h") or 0),
                                "page": source.get("page"),
                            }
                        )
                    image_entry: dict[str, Any] = {
                        "file": str(image_rel),
                        "occurrences": occurrences,
                    }
                    if size:
                        image_entry["width"] = int(size[0])
                        image_entry["height"] = int(size[1])
                    images.append(image_entry)
                    if str(image_rel) not in used_images:
                        used_images.append(str(image_rel))

            manifest_cards.append(
                {
                    "front": card.get("front"),
                    "back": back,
                    "tags": list(card.get("tags") or []),
                    # 难度：learn_value（1=可有可无 / 2=值得学 / 3=核心）→ App 的 1–5。
                    # App 服务端按 difficulty 换 tier（1–2→T1、3→T2、4→T3、5→T4），
                    # 写死 1 会让所有卡都落 T1。
                    "difficulty": _difficulty_of(card.get("learn_value")),
                    "sort_order": int(card.get("sort_order") or 0),
                    "images": images,
                    "source": source,
                    "content_fp": content_fp(str(card.get("front") or ""), back),
                }
            )

        # 整节的卡都被闸门剔掉了（论文源 + 严许可）⇒ 不留空章节。
        if not manifest_cards:
            continue

        libraries.append(
            {
                "name": str(section.get("title") or sid),
                "slug": f"{fp12}-{sid}",
                "sort_order": order_index,
                "cards": manifest_cards,
            }
        )

    provenance = {
        "tool": f"bookcard {__version__}",
        "method": _method_of(cards),
        "extractor": _extractor_of(work, cards),
        "created_at": int(time.time() * 1000),
    }
    if scene_override:
        provenance["scene_override"] = {
            "requested": scene_override,
            "detected": (detection or {}).get("mode"),
        }
    if scene_chosen_by:
        provenance["scene_chosen_by"] = scene_chosen_by
    if detection:
        provenance["scene_detection"] = {
            "verdict": detection.get("verdict"),
            "text_origin_ratio": detection.get("text_origin_ratio"),
            "median_image_coverage": detection.get("median_image_coverage"),
            "text_eligible": detection.get("text_eligible"),
            "reasons": list(detection.get("reasons") or []),
        }
    # 建议值恒为默认场景（契约 §7.1：三条判据只作 text_eligible 参考）。
    provenance["scene_suggested"] = DEFAULT_SCENE

    manifest = {
        "format": PACK_FORMAT,
        "visibility": PUBLIC_VISIBILITY if edition == EDITION_PUBLIC else PACK_VISIBILITY,
        "scene_mode": scene_mode,
        "book": (
            {key: value for key, value in book.items() if key not in PUBLIC_BOOK_DROP_FIELDS}
            if edition == EDITION_PUBLIC
            else book
        ),
        "provenance": provenance,
        "quality": quality,
        "license": dict(PUBLIC_LICENSE if edition == EDITION_PUBLIC else PRIVATE_LICENSE),
        "category": {
            "name": str(book.get("title") or fp12),
            "description": _category_description(book),
            "sort_order": 0,
            "libraries": libraries,
        },
    }

    # 论文源：manifest 顶层记一笔许可、出处和本次打包的判定结果（书本源不写这些键）。
    if policy is not None:
        flags = license_flags(license_kind)
        manifest["source_license"] = {
            "kind": license_kind,
            "uri": LICENSE_URI.get(license_kind, ""),
            "nc": flags["nc"],
            "nd": flags["nd"],
            "sa": flags["sa"],
            "permissive": flags["permissive"],
            "noncommercial": policy.noncommercial,
        }
        manifest["attribution"] = attribution
        # policy 是**记录性**的：公开包的裁剪与书源同规则，许可不决定公开包带什么。
        manifest["policy"] = {
            "gate": "same-as-book",
            "source_kind": license_kind,
            "nc": flags["nc"],
            "nd": flags["nd"],
            "sa": flags["sa"],
            "permissive": flags["permissive"],
            "noncommercial": policy.noncommercial,
            "dropped_verbatim": dropped_verbatim,
            "term_cards": term_cards,
            "summary_cards": summary_cards,
        }
    return manifest, used_images


# ===================== 闸门 =====================


def check_gates(work: WorkDir) -> dict:
    """检查目录闸门 approved；没过直接抛 :class:`PackError`。

    **闸门不跳**——没有 ``toc.json.gate.status == "approved"`` 不许切章节、不许打包。
    （抽词本身没有第二道闸门：词必须真的出现在原文里，由引擎把关。）
    """
    statuses: dict[str, str] = {}
    for name, filename in (("toc", "toc.json"),):
        path = work.root / filename
        if not path.exists():
            raise PackError(f"缺少 {filename}，不能打包（闸门未过）")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PackError(f"{filename} 解析失败：{exc}") from exc
        gate = payload.get("gate") if isinstance(payload, dict) else None
        status = str((gate or {}).get("status") or "unknown")
        statuses[name] = status
        if status != "approved":
            raise PackError(f"{filename} 的闸门是 {status}，不是 approved，不能打包")
    return statuses


# ===================== 打包 =====================


def public_pack_path(work: WorkDir, fp12: str) -> Path:
    """公开包路径：``pack/<fp12>.public.ttfc.zip``（契约 §7.1）。"""
    return work.pack_dir / f"{fp12}.public.ttfc.zip"


def pack(
    workdir: WorkDir | str,
    *,
    edition: str = EDITION_PRIVATE,
    scene: str | None = None,
    pdf: str | Path | None = None,
    scene_chosen_by: str | None = None,
) -> dict:
    """打包卡包，返回摘要。

    参数
    ----
    edition:
        ``private``（默认，契约 §7.1 的内部分享版）或 ``public``（公开版）。
    scene:
        ``text`` / ``image``——覆盖默认场景；覆盖时写进
        ``provenance.scene_override``。不传就用 :data:`DEFAULT_SCENE`。
    pdf:
        书 PDF 路径（判定图片覆盖率要用；默认按 ``locate.resolve_pdf`` 找）。
    scene_chosen_by:
        这个场景值是谁定的：``"user"``（用户选的）/ ``"cli"``（命令行定的）/
        ``"default"``（用的默认场景）。写进 ``book.json`` 和
        ``provenance.scene_chosen_by``（契约 §7.1）。
    """
    work = workdir if isinstance(workdir, WorkDir) else WorkDir(workdir)

    if edition not in EDITIONS:
        raise PackError(f"edition 只能是 {' 或 '.join(EDITIONS)}（现在是 {edition!r}）")
    if scene is not None and scene not in (SCENE_TEXT, SCENE_IMAGE):
        raise PackError(f"scene 只能是 {SCENE_TEXT} 或 {SCENE_IMAGE}（现在是 {scene!r}）")
    if scene_chosen_by is not None and scene_chosen_by not in SCENE_CHOSEN_BY_ALL:
        raise PackError(
            f"scene_chosen_by 只能是 {' 或 '.join(SCENE_CHOSEN_BY_ALL)}"
            f"（现在是 {scene_chosen_by!r}）"
        )

    gates = check_gates(work)

    # ---- scene_mode：默认场景（可覆盖）----
    detection = detect_scene_mode(work, pdf)
    detected = str(detection.get("mode") or DEFAULT_SCENE)
    scene_mode = scene if scene else DEFAULT_SCENE

    # 场景值是谁定的：显式传了场景就是命令行定的（agent 路径会传 "user"）；
    # 没传就是默认值（契约 §7.1：默认走图片场景）。
    chosen_by = scene_chosen_by or SCENE_CHOSEN_BY_DEFAULT

    # scene_mode / scene_suggested / scene_chosen_by 写进 book.json（契约 §7.1）。
    book = _write_scene_mode(
        work,
        scene_mode,
        scene_suggested=DEFAULT_SCENE,
        scene_chosen_by=chosen_by,
    )

    if edition == EDITION_PUBLIC:
        _require_fingerprint(book)

    # 论文源的许可记录（2026-11-08 两次拍板）；书本源是空串——行为不变。
    license_kind, _policy = _paper_gate(book)

    contexts: dict[str, dict] = {}
    context_stats: dict[str, Any] = {}
    if edition == EDITION_PRIVATE and scene_mode == SCENE_TEXT:
        contexts, context_stats = build_contexts(work, work.cards())

    manifest, used_images = build_manifest(
        work,
        edition=edition,
        scene_mode=scene_mode,
        scene_override=scene,
        detection=detection,
        contexts=contexts,
        scene_chosen_by=chosen_by,
    )
    validate_manifest(manifest)

    if edition == EDITION_PUBLIC:
        _assert_no_images(manifest)

    # 包内的图片必须都在工作目录里真的存在。
    for rel in used_images:
        source_path = work.root / rel
        if not source_path.exists():
            raise PackError(f"页面图不存在：{source_path}（先跑 bookcard locate）")

    fp12 = str(manifest["book"].get("fp12") or "")
    target = public_pack_path(work, fp12) if edition == EDITION_PUBLIC else work.pack_path(fp12)
    target.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        for rel in used_images:
            archive.write(work.root / rel, rel)

    if edition == EDITION_PUBLIC:
        _assert_zip_has_no_images(target)

    card_count = sum(len(lib["cards"]) for lib in manifest["category"]["libraries"])
    policy_block = manifest.get("policy") or {}
    dropped_verbatim = int(policy_block.get("dropped_verbatim") or 0)
    return {
        "pack": str(target),
        "edition": edition,
        "bytes": target.stat().st_size,
        "cards": card_count,
        "libraries": len(manifest["category"]["libraries"]),
        "images": len(used_images),
        "scene_mode": scene_mode,
        "scene_detected": detected,
        "scene_override": scene,
        "scene_chosen_by": chosen_by,
        "scene_reasons": list(detection.get("reasons") or []),
        "contexts": context_stats,
        "gates": gates,
        # 论文源的许可记录与本次打包的判定结果；书本源是空串 / 0（既有键一个都不改）。
        "source_license": license_kind,
        # 公开包里被剔掉的卡总数（论文源现在只因「照抄原文」剔）。
        "dropped_cards": dropped_verbatim,
        "dropped_verbatim": dropped_verbatim,
        "term_cards": int(policy_block.get("term_cards") or 0),
        "summary_cards": int(policy_block.get("summary_cards") or 0),
    }


def read_pack_manifest(pack_path: str | Path) -> dict:
    """从 ``.ttfc.zip`` 里读回 manifest（自测/验收用）。"""
    with zipfile.ZipFile(Path(pack_path)) as archive:
        return json.loads(archive.read("manifest.json").decode("utf-8"))
