"""browse-lite 的本地库：导入卡包、查询、评分（SM-2）、tier、改卡。

契约见 ``docs/browse-lite-v1.md``（§1 目录 / §2 schema / §3 scene / §4 API）。

## 这一层的边界

- **只读卡包**（``.ttfc.zip`` 或解开的包目录），只写本地 SQLite + 图片目录。
- 引擎不依赖 ``00_flashcard`` 的数据库或服务；SM-2 抄的是 App 的 Dart 源码
  （见 :mod:`bookcard.browse.sm2`），不是抄 App 的库。
- 公开包（``visibility == "public"``）**不抽图片、不带 scene**（契约 §7.1）。

## 幂等导入（契约 §1）

同一个包再导一次 = 幂等更新：书 / 词库 / 卡的内容随包刷新，
但**用户的进度、忽略状态、编辑一律保留**：

- ``card_state`` / ``review_log`` 导入时只补新的，不覆盖已有的；
- ``cards.edited == 1`` 的卡：front / back / tags 保留用户改过的，不被包覆盖。

包里删掉的卡 / 词库**不删**（删了等于把用户的进度扔掉）—— 契约没要求删，
这里选择留着。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import time
import zipfile
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

from bookcard.browse.sm2 import UI_RATINGS, compute_sm2, sm2_rating_of

#: 默认数据库位置（契约 §1）。
DEFAULT_DB_PATH = Path.home() / ".bookcard" / "browse" / "browse.db"

#: App 的 front 长度限制（``pack.py`` 抄的 ``_CardIn.front``）。
MAX_FRONT_LEN = 500

#: ``/api/cards`` 里 ``back_preview`` 的长度（截断后加省略号）。
BACK_PREVIEW_LEN = 200

#: 参与筛选的 5 个 tier（App 的 ``kToggleTiers``：不含 T3 / T5）。
TIER_TOKENS = ("T1", "T2", "T4", "T6", "T7")

#: ``scene_mode`` 的三个取值（契约 §7.1）。
SCENE_MODES = ("text", "image", "none")


class BrowseError(Exception):
    """导入 / 查询出错。

    ``status`` 是对应的 HTTP 状态码（默认 400；找不到东西用 404）。
    """

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ===================== 小工具 =====================


def _now_ms() -> int:
    """当前时间（毫秒）。"""
    return int(time.time() * 1000)


def _sha1_hex(text: str) -> str:
    """UTF-8 文本的 sha1 十六进制。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _is_inside(base: Path, target: Path) -> bool:
    """``target`` 是否在 ``base`` 里面（解完符号链接再比，防穿越）。"""
    try:
        base_resolved = base.resolve()
        target_resolved = target.resolve()
    except OSError:  # pragma: no cover - 解析不了就当不在里面
        return False
    return base_resolved == target_resolved or base_resolved in target_resolved.parents


def _json_loads_or_none(raw: str | None):
    """JSON 字段解析；坏了就当没有（不让一行脏数据炸掉整个列表）。"""
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def tier_of(state: dict, now_ms: int) -> str:
    """算 tier（契约 §4，顺序照抄 App 的 ``resolveFrontTierFromCardFields``）。

    ``ignored`` ⇒ T6 · ``mastered`` ⇒ T7 · ``reviews == 0`` ⇒ T2 ·
    ``next_review_at <= now`` ⇒ T1 · 其余 ⇒ T4。
    """
    if state.get("ignored"):
        return "T6"
    if state.get("mastered"):
        return "T7"
    if int(state.get("reviews") or 0) == 0:
        return "T2"
    next_review_at = state.get("next_review_at")
    if next_review_at is None or next_review_at <= now_ms:
        return "T1"
    return "T4"


def _back_preview(back: str) -> str:
    """背面预览（截断 + 省略号）。"""
    text = back or ""
    if len(text) <= BACK_PREVIEW_LEN:
        return text
    return text[:BACK_PREVIEW_LEN] + "…"


# ===================== 读包 =====================


@contextlib.contextmanager
def _open_pack(path: Path) -> Iterator[tuple[dict, Callable[[str], bytes | None]]]:
    """打开一个卡包，产出 ``(manifest, 读图片的函数)``。

    支持两种形态：``.ttfc.zip``（zipfile）与**解开的包目录**（含 ``manifest.json``）。
    """
    if path.is_dir():
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            raise BrowseError(f"包里没有 manifest.json：{path}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BrowseError(f"manifest.json 解析失败：{path}：{exc}") from exc

        def read_from_dir(rel: str) -> bytes | None:
            target = path / rel
            if not target.is_file() or not _is_inside(path, target):
                return None
            return target.read_bytes()

        yield manifest, read_from_dir
        return

    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "manifest.json" not in names:
                raise BrowseError(f"zip 里没有 manifest.json：{path}")
            try:
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise BrowseError(f"manifest.json 解析失败：{path}：{exc}") from exc

            def read_from_zip(rel: str) -> bytes | None:
                if rel not in names:
                    return None
                return archive.read(rel)

            yield manifest, read_from_zip
        return

    raise BrowseError(f"认不出来的东西（既不是卡包目录，也不是 zip）：{path}")


# ===================== scene（契约 §3） =====================


def _fallback_scene_mode(libraries: Sequence[dict]) -> str:
    """包里没写 ``scene_mode`` 时的兜底（契约 §3）。

    有卡的 ``source.context`` ⇒ ``text``；有卡带图 ⇒ ``image``；都没有 ⇒ ``none``。
    """
    has_context = False
    has_images = False
    for library in libraries:
        for card in library.get("cards") or []:
            source = card.get("source") or {}
            if isinstance(source, dict) and source.get("context"):
                has_context = True
            if card.get("images"):
                has_images = True
    if has_context:
        return "text"
    if has_images:
        return "image"
    return "none"


def _build_scene(
    *,
    title: str,
    book_id: str,
    visibility: str,
    card: dict,
) -> dict | None:
    """按契约 §3 给一张卡算 ``scene``（没有就是 ``None``）。

    - **公开包一律 ``None``**（公开版不带场景，也不带书页图）。
    - ``card.source.context`` 在 ⇒ 文本场景；
    - 否则 ``card.images[0]`` 在 ⇒ 图片场景；
    - 都没有 ⇒ ``None``。
    """
    if visibility == "public":
        return None

    source = card.get("source") or {}
    if not isinstance(source, dict):
        source = {}

    context = source.get("context")
    if isinstance(context, dict) and context.get("text"):
        highlights = context.get("highlights")
        if not isinstance(highlights, list):
            highlights = []
        return {
            "kind": "text",
            "book": title,
            "page": source.get("page"),
            "printed_page": source.get("printed_page"),
            "text": context.get("text"),
            "highlights": highlights,
        }

    images = card.get("images") or []
    if not images:
        return None
    image = images[0]
    if not isinstance(image, dict):
        return None
    file_name = str(image.get("file") or "")
    if not file_name:
        return None

    occurrences = image.get("occurrences") or []
    boxes: list[dict] = []
    page = source.get("page")
    for occurrence in occurrences:
        if not isinstance(occurrence, dict):
            continue
        boxes.append(
            {
                "x": occurrence.get("x"),
                "y": occurrence.get("y"),
                "w": occurrence.get("w"),
                "h": occurrence.get("h"),
            }
        )
    if occurrences and isinstance(occurrences[0], dict):
        page = occurrences[0].get("page", page)

    return {
        "kind": "image",
        "book": title,
        "page": page,
        "src": f"/images/{book_id}/{os.path.basename(file_name)}",
        "width": image.get("width"),
        "height": image.get("height"),
        "boxes": boxes,
    }


# ===================== 库 =====================


class BrowseDB:
    """browse-lite 的本地库（SQLite + 图片目录）。"""

    def __init__(self, db_path: str | Path, images_dir: str | Path | None = None):
        self.db_path = Path(db_path).expanduser()
        self.images_dir = (
            Path(images_dir).expanduser()
            if images_dir is not None
            else self.db_path.parent / "images"
        )
        self._initialized = False

    # ---------- 连接 ----------

    def connect(self) -> sqlite3.Connection:
        """开一条连接（每个请求一条；``sqlite3`` 连接不能跨线程共用）。"""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        """建库建表（WAL 模式）。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        schema = (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")
        conn = self.connect()
        try:
            conn.executescript(schema)
            conn.execute("PRAGMA journal_mode = WAL")
            conn.commit()
        finally:
            conn.close()
        self._initialized = True

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            self.initialize()

    # ---------- 导入（契约 §1） ----------

    def import_pack(self, path: str | Path) -> dict:
        """导入一个卡包（``.ttfc.zip`` 或包目录），幂等。

        返回摘要：``book_id / title / libraries / cards / images / created``。
        """
        self._ensure_initialized()
        pack_path = Path(path).expanduser()
        with _open_pack(pack_path) as (manifest, read_bytes):
            summary = self._import_manifest(manifest, read_bytes)
        summary["pack"] = str(pack_path)
        return summary

    def _import_manifest(self, manifest: dict, read_bytes: Callable[[str], bytes | None]) -> dict:
        """把一份 manifest 写进库（幂等）。"""
        if not isinstance(manifest, dict):
            raise BrowseError("manifest 必须是对象")
        category = manifest.get("category")
        if not isinstance(category, dict):
            raise BrowseError("manifest 缺 category")
        libraries_in = category.get("libraries")
        if not isinstance(libraries_in, list):
            raise BrowseError("category.libraries 必须是数组")

        book = manifest.get("book") or {}
        if not isinstance(book, dict):
            book = {}
        category_name = str(category.get("name") or "")
        title = str(book.get("title") or category_name).strip()
        book_id = str(book.get("fp12") or "").strip() or _sha1_hex(category_name)[:12]
        visibility = str(manifest.get("visibility") or "private").strip().lower()
        if visibility not in ("public", "private"):
            raise BrowseError(f"visibility 认不出来：{visibility}")

        scene_mode = str(manifest.get("scene_mode") or "").strip().lower()
        if scene_mode not in SCENE_MODES:
            scene_mode = _fallback_scene_mode(libraries_in)

        manifest_meta = {key: value for key, value in manifest.items() if key != "category"}
        now = _now_ms()

        previous_visibility = self._book_visibility(book_id)

        image_files: list[str] = []
        card_rows: list[tuple] = []
        library_rows: list[tuple] = []

        for lib_index, library in enumerate(libraries_in):
            if not isinstance(library, dict):
                raise BrowseError(f"category.libraries[{lib_index}] 必须是对象")
            library_id = str(library.get("slug") or "").strip() or f"{book_id}-lib{lib_index:02d}"
            library_rows.append(
                (
                    library_id,
                    book_id,
                    str(library.get("name") or library_id),
                    int(library.get("sort_order") or 0),
                )
            )

            for card_index, card in enumerate(library.get("cards") or []):
                if not isinstance(card, dict):
                    raise BrowseError(
                        f"category.libraries[{lib_index}].cards[{card_index}] 必须是对象"
                    )
                front = str(card.get("front") or "")
                if not 1 <= len(front) <= MAX_FRONT_LEN:
                    raise BrowseError(
                        f"front 长度 {len(front)} 不合法（1–{MAX_FRONT_LEN}）："
                        f"{library_id}#{card_index}"
                    )
                source = card.get("source") or {}
                card_id = self._card_id(library_id, card, source, front)
                tags = card.get("tags")
                if not isinstance(tags, list):
                    tags = []
                scene = _build_scene(
                    title=title,
                    book_id=book_id,
                    visibility=visibility,
                    card=card,
                )
                card_rows.append(
                    (
                        card_id,
                        library_id,
                        front,
                        str(card.get("back") or ""),
                        json.dumps(tags, ensure_ascii=False),
                        int(card.get("sort_order") or 0),
                        json.dumps(source, ensure_ascii=False) if source else None,
                        json.dumps(scene, ensure_ascii=False) if scene else None,
                        now,
                    )
                )
                if visibility == "private":
                    for image in card.get("images") or []:
                        if isinstance(image, dict) and image.get("file"):
                            file_name = str(image["file"])
                            if file_name not in image_files:
                                image_files.append(file_name)

        # --- 写库 ---
        conn = self.connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO books
                      (id, title, subtitle, language, visibility, scene_mode,
                       sort_order, manifest_meta, imported_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      title = excluded.title,
                      subtitle = excluded.subtitle,
                      language = excluded.language,
                      visibility = excluded.visibility,
                      scene_mode = excluded.scene_mode,
                      sort_order = excluded.sort_order,
                      manifest_meta = excluded.manifest_meta,
                      imported_at = excluded.imported_at
                    """,
                    (
                        book_id,
                        title,
                        str(category.get("description") or ""),
                        str(book.get("language") or ""),
                        visibility,
                        scene_mode,
                        int(category.get("sort_order") or 0),
                        json.dumps(manifest_meta, ensure_ascii=False),
                        now,
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO libraries (id, book_id, name, sort_order)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      book_id = excluded.book_id,
                      name = excluded.name,
                      sort_order = excluded.sort_order
                    """,
                    library_rows,
                )
                conn.executemany(
                    """
                    INSERT INTO cards
                      (id, library_id, front, back, tags, sort_order, source, scene,
                       edited, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      library_id = CASE WHEN cards.edited = 1
                        THEN cards.library_id ELSE excluded.library_id END,
                      front = CASE WHEN cards.edited = 1 THEN cards.front ELSE excluded.front END,
                      back = CASE WHEN cards.edited = 1 THEN cards.back ELSE excluded.back END,
                      tags = CASE WHEN cards.edited = 1 THEN cards.tags ELSE excluded.tags END,
                      sort_order = excluded.sort_order,
                      source = excluded.source,
                      scene = excluded.scene,
                      updated_at = excluded.updated_at
                    """,
                    card_rows,
                )
                # 状态只补新的：已有的进度 / 忽略 / 掌握一律不动。
                conn.executemany(
                    "INSERT OR IGNORE INTO card_state (card_id) VALUES (?)",
                    [(row[0],) for row in card_rows],
                )
        finally:
            conn.close()

        images_written = self._write_images(book_id, image_files, read_bytes)

        return {
            "book_id": book_id,
            "title": title,
            "visibility": visibility,
            "scene_mode": scene_mode,
            "libraries": len(library_rows),
            "cards": len(card_rows),
            "images": images_written,
            # 🔴 同一本书的**两个版本**（契约 §7.1 的公开版 / 内部分享版）共用一个
            #    book_id 与一套卡 id ⇒ 后导的那个会覆盖前一个的内容（场景会没）。
            #    这里把它显式报出来，别让用户以为是自己记错了。
            "previous_visibility": previous_visibility,
            "visibility_changed": previous_visibility is not None
            and previous_visibility != visibility,
        }

    def _book_visibility(self, book_id: str) -> str | None:
        """这本书在库里已经是哪个版本（没导过就 ``None``）。"""
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT visibility FROM books WHERE id = ?", (book_id,)
            ).fetchone()
        finally:
            conn.close()
        return row["visibility"] if row is not None else None

    @staticmethod
    def _card_id(library_id: str, card: dict, source: object, front: str) -> str:
        """卡的 id：``source.card_id`` → ``card.card_id`` → ``<library_id>:<sha1(front)[:8]>``。"""
        if isinstance(source, dict):
            candidate = source.get("card_id")
            if candidate:
                return str(candidate)
        candidate = card.get("card_id")
        if candidate:
            return str(candidate)
        return f"{library_id}:{_sha1_hex(front)[:8]}"

    def _write_images(
        self,
        book_id: str,
        image_files: Sequence[str],
        read_bytes: Callable[[str], bytes | None],
    ) -> int:
        """把包里的页面图抽到 ``<images_dir>/<book_id>/``。"""
        if not image_files:
            return 0
        target_dir = self.images_dir / book_id
        target_dir.mkdir(parents=True, exist_ok=True)
        written = 0
        for file_name in image_files:
            data = read_bytes(file_name)
            if data is None:
                continue
            target = target_dir / os.path.basename(file_name)
            if not _is_inside(target_dir, target):  # pragma: no cover - basename 不会穿越
                continue
            target.write_bytes(data)
            written += 1
        return written

    # ---------- 查询（契约 §4） ----------

    def tree(self) -> list[dict]:
        """``/api/tree``：书 → 词库 → 卡数。"""
        self._ensure_initialized()
        conn = self.connect()
        try:
            books = conn.execute(
                "SELECT id, title, subtitle, visibility, scene_mode FROM books "
                "ORDER BY sort_order, title, id"
            ).fetchall()
            result = []
            for book in books:
                libraries = conn.execute(
                    """
                    SELECT l.id AS id, l.name AS name, COUNT(c.id) AS count
                    FROM libraries l
                    LEFT JOIN cards c ON c.library_id = l.id
                    WHERE l.book_id = ?
                    GROUP BY l.id, l.name, l.sort_order
                    ORDER BY l.sort_order, l.id
                    """,
                    (book["id"],),
                ).fetchall()
                result.append(
                    {
                        "id": book["id"],
                        "title": book["title"],
                        "subtitle": book["subtitle"],
                        "visibility": book["visibility"],
                        "scene_mode": book["scene_mode"],
                        "libraries": [
                            {"id": lib["id"], "name": lib["name"], "count": lib["count"]}
                            for lib in libraries
                        ],
                    }
                )
            return result
        finally:
            conn.close()

    def cards(
        self,
        *,
        book_id: str | None = None,
        library_id: str | None = None,
        off: Sequence[str] = (),
    ) -> dict:
        """``/api/cards``：按 book 或 library 取卡，带 tier 筛选。

        ``tier_counts`` 统计的是**筛选前**的整个范围（契约 §4）。
        """
        self._ensure_initialized()
        if bool(book_id) == bool(library_id):
            raise BrowseError("book 与 library 必须给且只给一个")

        off_set = {str(token).upper() for token in off if str(token).strip()}
        now = _now_ms()

        conn = self.connect()
        try:
            if library_id:
                scope_sql = "WHERE c.library_id = ?"
                scope_args: tuple = (library_id,)
            else:
                scope_sql = "WHERE l.book_id = ?"
                scope_args = (book_id,)
            rows = conn.execute(
                f"""
                SELECT c.id AS id, c.front AS front, c.back AS back, c.library_id AS library_id,
                       c.sort_order AS sort_order,
                       COALESCE(s.ignored, 0) AS ignored,
                       COALESCE(s.mastered, 0) AS mastered,
                       COALESCE(s.reviews, 0) AS reviews,
                       s.next_review_at AS next_review_at
                FROM cards c
                JOIN libraries l ON l.id = c.library_id
                LEFT JOIN card_state s ON s.card_id = c.id
                {scope_sql}
                ORDER BY l.sort_order, c.sort_order, c.id
                """,
                scope_args,
            ).fetchall()
        finally:
            conn.close()

        tier_counts = {token: 0 for token in TIER_TOKENS}
        cards = []
        for row in rows:
            tier = tier_of(dict(row), now)
            if tier in tier_counts:
                tier_counts[tier] += 1
            if tier in off_set:
                continue
            cards.append(
                {
                    "id": row["id"],
                    "front": row["front"],
                    "back_preview": _back_preview(row["back"]),
                    "tier": tier,
                    "library_id": row["library_id"],
                    "sort_order": row["sort_order"],
                }
            )
        return {"cards": cards, "tier_counts": tier_counts}

    def card_detail(self, card_id: str) -> dict | None:
        """``/api/cards/<id>``：一张卡的全部字段（含 state 与 scene）。"""
        self._ensure_initialized()
        conn = self.connect()
        try:
            row = conn.execute(
                """
                SELECT c.id AS id, c.library_id AS library_id, l.book_id AS book_id,
                       c.front AS front, c.back AS back, c.tags AS tags,
                       c.scene AS scene, c.edited AS edited,
                       COALESCE(s.repetition, 0) AS repetition,
                       COALESCE(s.ease, 1.8) AS ease,
                       COALESCE(s.interval_days, 0) AS interval_days,
                       s.next_review_at AS next_review_at,
                       s.last_reviewed_at AS last_reviewed_at,
                       COALESCE(s.reviews, 0) AS reviews,
                       COALESCE(s.lapses, 0) AS lapses,
                       COALESCE(s.ignored, 0) AS ignored,
                       COALESCE(s.mastered, 0) AS mastered
                FROM cards c
                JOIN libraries l ON l.id = c.library_id
                LEFT JOIN card_state s ON s.card_id = c.id
                WHERE c.id = ?
                """,
                (card_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return self._detail_from_row(row)

    def _detail_from_row(self, row: sqlite3.Row) -> dict:
        """把一行查询结果拼成 API 的卡详情。"""
        state = {
            "repetition": row["repetition"],
            "ease": row["ease"],
            "interval_days": row["interval_days"],
            "next_review_at": row["next_review_at"],
            "last_reviewed_at": row["last_reviewed_at"],
            "reviews": row["reviews"],
            "lapses": row["lapses"],
            "ignored": row["ignored"],
            "mastered": row["mastered"],
        }
        tags = _json_loads_or_none(row["tags"])
        return {
            "id": row["id"],
            "library_id": row["library_id"],
            "book_id": row["book_id"],
            "front": row["front"],
            "back": row["back"],
            "tags": tags if isinstance(tags, list) else [],
            "tier": tier_of(state, _now_ms()),
            "scene": _json_loads_or_none(row["scene"]),
            "state": state,
            "edited": bool(row["edited"]),
        }

    # ---------- 写操作（契约 §4） ----------

    def rate(self, card_id: str, rating: str) -> dict:
        """给一张卡评分：走 SM-2，写 ``card_state`` + ``review_log``。"""
        self._ensure_initialized()
        if rating not in UI_RATINGS:
            raise BrowseError(f"rating 只能是 {', '.join(UI_RATINGS)}：{rating}")

        conn = self.connect()
        try:
            row = conn.execute(
                """
                SELECT c.id AS id,
                       COALESCE(s.repetition, 0) AS repetition,
                       COALESCE(s.ease, 1.8) AS ease,
                       COALESCE(s.interval_days, 0) AS interval_days,
                       COALESCE(s.reviews, 0) AS reviews,
                       COALESCE(s.lapses, 0) AS lapses,
                       COALESCE(s.ignored, 0) AS ignored,
                       COALESCE(s.mastered, 0) AS mastered
                FROM cards c
                LEFT JOIN card_state s ON s.card_id = c.id
                WHERE c.id = ?
                """,
                (card_id,),
            ).fetchone()
            if row is None:
                raise BrowseError(f"卡不存在：{card_id}", status=404)

            now = _now_ms()
            sm2_rating = sm2_rating_of(rating)
            result = compute_sm2(
                rating=sm2_rating,
                current_repetition=row["repetition"],
                current_ease_factor=row["ease"],
                current_interval_days=row["interval_days"],
                now_ms=now,
            )

            # 掌握标记（契约 §4）：`easy` ⇒ mastered=1；`unclear` / `forgot` ⇒ mastered=0；
            # `good` **不动**（App 的口径：Good 不标已掌握，也不取消已掌握 ——
            # App `test/gaox293_easy_marks_mastered_test.dart` ④）。
            if rating == "easy":
                mastered = 1
            elif rating in ("unclear", "forgot"):
                mastered = 0
            else:
                mastered = row["mastered"]

            lapses = row["lapses"] + (1 if sm2_rating == "again" else 0)

            with conn:
                conn.execute(
                    """
                    INSERT INTO card_state
                      (card_id, repetition, ease, interval_days, next_review_at,
                       last_reviewed_at, reviews, lapses, ignored, mastered)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(card_id) DO UPDATE SET
                      repetition = excluded.repetition,
                      ease = excluded.ease,
                      interval_days = excluded.interval_days,
                      next_review_at = excluded.next_review_at,
                      last_reviewed_at = excluded.last_reviewed_at,
                      reviews = excluded.reviews,
                      lapses = excluded.lapses,
                      mastered = excluded.mastered
                    """,
                    (
                        card_id,
                        result.repetition,
                        result.ease_factor,
                        result.interval_days,
                        result.next_review_at_ms,
                        now,
                        row["reviews"] + 1,
                        lapses,
                        row["ignored"],
                        mastered,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO review_log (card_id, rating, sm2, reviewed_at, interval_days)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (card_id, rating, sm2_rating, now, result.interval_days),
                )
        finally:
            conn.close()

        detail = self.card_detail(card_id)
        if detail is None:  # pragma: no cover - 上面刚查到过
            raise BrowseError(f"卡不存在：{card_id}", status=404)
        return detail

    def set_ignored(self, card_id: str, ignored: bool) -> dict:
        """忽略 / 取消忽略。"""
        self._ensure_initialized()
        conn = self.connect()
        try:
            exists = conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone()
            if exists is None:
                raise BrowseError(f"卡不存在：{card_id}", status=404)
            with conn:
                conn.execute(
                    """
                    INSERT INTO card_state (card_id, ignored) VALUES (?, ?)
                    ON CONFLICT(card_id) DO UPDATE SET ignored = excluded.ignored
                    """,
                    (card_id, 1 if ignored else 0),
                )
        finally:
            conn.close()

        detail = self.card_detail(card_id)
        if detail is None:  # pragma: no cover - 上面刚查到过
            raise BrowseError(f"卡不存在：{card_id}", status=404)
        return detail

    def edit_card(
        self,
        card_id: str,
        *,
        front: str | None = None,
        back: str | None = None,
        tags: Sequence[str] | None = None,
        library_id: str | None = None,
    ) -> dict:
        """改卡（front / back / tags / 词库），改完 ``edited = 1``。"""
        self._ensure_initialized()
        conn = self.connect()
        try:
            row = conn.execute(
                """
                SELECT c.id AS id, c.library_id AS library_id, l.book_id AS book_id
                FROM cards c JOIN libraries l ON l.id = c.library_id
                WHERE c.id = ?
                """,
                (card_id,),
            ).fetchone()
            if row is None:
                raise BrowseError(f"卡不存在：{card_id}", status=404)

            updates: dict[str, object] = {}

            if front is not None:
                text = str(front)
                if not 1 <= len(text) <= MAX_FRONT_LEN:
                    raise BrowseError(f"front 长度必须是 1–{MAX_FRONT_LEN}（现在是 {len(text)}）")
                updates["front"] = text
            if back is not None:
                updates["back"] = str(back)
            if tags is not None:
                clean = [str(tag) for tag in tags]
                updates["tags"] = json.dumps(clean, ensure_ascii=False)
            if library_id is not None:
                target = conn.execute(
                    "SELECT book_id FROM libraries WHERE id = ?", (library_id,)
                ).fetchone()
                if target is None:
                    raise BrowseError(f"词库不存在：{library_id}")
                if target["book_id"] != row["book_id"]:
                    raise BrowseError("只能挪到同一本书里的词库")
                updates["library_id"] = library_id

            assignments = ", ".join(f"{key} = ?" for key in updates)
            with conn:
                if assignments:
                    conn.execute(
                        f"UPDATE cards SET {assignments}, edited = 1, updated_at = ? WHERE id = ?",
                        (*updates.values(), _now_ms(), card_id),
                    )
                else:
                    conn.execute(
                        "UPDATE cards SET edited = 1, updated_at = ? WHERE id = ?",
                        (_now_ms(), card_id),
                    )
        finally:
            conn.close()

        detail = self.card_detail(card_id)
        if detail is None:  # pragma: no cover - 上面刚查到过
            raise BrowseError(f"卡不存在：{card_id}", status=404)
        return detail

    # ---------- 杂项 ----------

    def counts(self) -> dict:
        """书 / 词库 / 卡 / 评分记录的数量（自测与冒烟用）。"""
        self._ensure_initialized()
        conn = self.connect()
        try:
            return {
                "books": conn.execute("SELECT COUNT(*) AS n FROM books").fetchone()["n"],
                "libraries": conn.execute("SELECT COUNT(*) AS n FROM libraries").fetchone()["n"],
                "cards": conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"],
                "reviews": conn.execute("SELECT COUNT(*) AS n FROM review_log").fetchone()["n"],
            }
        finally:
            conn.close()
