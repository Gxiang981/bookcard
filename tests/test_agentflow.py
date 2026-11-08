"""agentflow（``docs/mcp-v1.md`` §2）的测试：agent 驱动的出题 / 交卷层。

覆盖验收要求的那几条：

- 一本几页的小 PDF 夹具，从 ``book_add`` 走到 ``pack``（模型回答写死在夹具里，
  引擎不调模型、不读 key）；
- 闸门 1 没过时 ``sections_build`` 被拒；
- 闸门 2 没过时 ``extract_next(full)`` 被拒；
- 交卷解析失败时返回 ``retry``；
- 同一个 seg 重交时覆盖；
- 租约过期后重新出题。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agentflow_fixtures import (
    EXTRACT_ANSWER_TWO_WORDS,
    EXTRACT_ANSWERS,
    make_pdf,
    write_toc,
)
from bookcard import agentflow
from bookcard.cli import main
from pipeline_fixtures import assert_scene_chosen_by_consistent

#: 交卷时用的 agent 标识（格式：agent:<harness>:<模型名>[:<提示词版本>]）。
EXTRACTOR = "agent:test-harness:test-model:ex-v1"
REVIEWER = "agent:test-harness:test-model"


# --------------------------------------------------------------------------- 辅助


def _add_book(tmp_path: Path, *, bookmarks: bool = False) -> tuple[Path, dict]:
    """加一本书，返回 ``(书的工作目录, book_add 的返回)``。

    ``bookmarks=True`` 时 PDF 里有读书签，``build_toc`` 能拿到目录条目
    （闸门 1 的四条判据才过得去）。
    """
    pdf = tmp_path / "book.pdf"
    if not pdf.exists():
        make_pdf(pdf, bookmarks=bookmarks)
    result = agentflow.book_add(pdf, home=tmp_path / "home")
    assert result["ok"], result
    return Path(result["book_dir"]), result


def _ready_book(tmp_path: Path) -> Path:
    """加书 + 目录过闸门 1 + 切章节（抽词之前的起点）。"""
    book_dir, _ = _add_book(tmp_path)
    write_toc(book_dir)
    gate = agentflow.toc_gate(book_dir, approve=True, approved_by=REVIEWER)
    assert gate["ok"], gate
    sections = agentflow.sections_build(book_dir)
    assert sections["ok"], sections
    return book_dir


def _submit(book_dir: Path, segments) -> int:
    """把一批段交上去（模型回答按页取夹具里的）。"""
    done = 0
    for segment in segments:
        answer = EXTRACT_ANSWERS[segment["page"]]
        result = agentflow.extract_submit(book_dir, segment["seg_id"], answer, EXTRACTOR)
        assert result["ok"], result
        done += 1
    return done


def _extract_all(book_dir: Path, pending=None) -> int:
    """把所有段抽完，返回交卷的段数。

    ``pending`` 是**已经出题但还没交**的段（租约还占着，不交就不会再出），
    先把它们交上去，再把剩下的抽完。
    """
    done = _submit(book_dir, pending or [])
    while True:
        nxt = agentflow.extract_next(book_dir, max_segments=4)
        assert nxt["ok"], nxt
        if not nxt["segments"]:
            return done
        done += _submit(book_dir, nxt["segments"])


# --------------------------------------------------------------------------- 加书


def test_加书返回身份和场景建议(tmp_path):
    """``book_add`` 返回 fp12、标题、页数、verdict、是否 OCR 和场景信息。"""
    book_dir, result = _add_book(tmp_path)

    assert result["ok"] is True
    assert len(result["fp12"]) == 12
    assert result["page_count"] == 10
    assert result["verdict"] == "digital"
    assert result["ocr"] is False
    # 场景默认走图片场景；三条判据只作 text_eligible 参考。
    assert result["scene_suggested"] == "image"
    assert result["text_eligible"] is True
    detection = result["scene_detection"]
    assert detection["verdict"] == "digital"
    assert detection["text_origin_ratio"] == 1.0
    assert detection["median_image_coverage"] == 0.0
    assert detection["text_eligible"] is True
    assert detection["reasons"]
    assert "默认走图片场景" in result["scene_reasons"][0]

    book = json.loads((book_dir / "book.json").read_text(encoding="utf-8"))
    assert book["scene_suggested"] == result["scene_suggested"]


def test_同一本书再加一次是幂等的(tmp_path):
    """同一本书再加一次，fp12 和目录都不变。"""
    first_dir, first = _add_book(tmp_path)
    second_dir, second = _add_book(tmp_path)
    assert first["fp12"] == second["fp12"]
    assert first_dir == second_dir


# --------------------------------------------------------------------------- 全流程


def test_从加书走到出包(tmp_path):
    """一本小 PDF，从 ``book_add`` 一路走到 ``pack``（模型回答写死）。"""
    book_dir, _ = _add_book(tmp_path)

    # ---- 闸门 1：目录 ----
    write_toc(book_dir)
    review = agentflow.toc_review(book_dir)
    assert review["ok"]
    assert len(review["entries"]) == 3
    assert review["toc_pages_text"] == []  # 夹具书没有目录页
    gate = agentflow.toc_gate(book_dir, approve=True, approved_by=REVIEWER)
    assert gate["ok"]
    assert gate["gate"]["status"] == "approved"
    assert gate["gate"]["approved_by"] == REVIEWER

    sections = agentflow.sections_build(book_dir)
    assert sections["ok"]
    assert sections["sections"] == 3

    # ---- 闸门 2：试抽 → 审卡 ----
    nxt = agentflow.extract_next(book_dir)
    assert nxt["ok"]
    assert nxt["prompt_version"] == "ex-v2"
    assert nxt["system"]
    assert len(nxt["segments"]) == 4  # max_segments 默认 4
    assert all("本页文字" in segment["user"] for segment in nxt["segments"])

    _extract_all(book_dir, pending=nxt["segments"])

    # ---- 全量 ----
    assert _extract_all(book_dir) == 0  # 前面已经抽完了

    # ---- 挑卡 → 定位 → 质检 ----
    built = agentflow.cards_build(book_dir)
    assert built["ok"], built
    assert built["quality"]["cards"] == 9
    assert built["quality"]["locate_rate"] == 1.0

    # ---- 打包（内部分享版，用户主动选了文本块）----
    packed = agentflow.pack(
        book_dir, edition="private", scene="text", scene_chosen_by="user"
    )
    assert packed["ok"], packed
    assert packed["cards"] == 9
    assert packed["libraries"] == 3
    assert packed["scene_chosen_by"] == "user"

    target = Path(packed["pack"])
    assert target.exists()
    manifest = assert_scene_chosen_by_consistent(book_dir, "user")
    assert manifest["visibility"] == "private"
    assert manifest["scene_mode"] == "text"
    assert manifest["provenance"]["scene_suggested"] == "image"
    assert manifest["book"]["scene_suggested"] == "image"

    # 状态走到最后一步。
    state = agentflow.status(book_dir)
    assert state["step"] == "done"
    assert state["gates"] == {"toc": "approved"}


# --------------------------------------------------------------------------- 闸门


def test_目录只出pending不许自动批(tmp_path):
    """``book_add`` → ``toc_review``：闸门 1 只到 ``pending``，``approved_by`` 留空。

    建目录是确定性的，但**批不批是另一回事**——不许自动批。
    """
    book_dir, _ = _add_book(tmp_path, bookmarks=True)

    review = agentflow.toc_review(book_dir)
    assert review["ok"], review
    assert review["gate"]["status"] == "pending"
    assert review["gate"]["approved_by"] == ""

    toc = json.loads((book_dir / "toc.json").read_text(encoding="utf-8"))
    assert toc["gate"]["status"] == "pending"
    assert toc["gate"]["approved_by"] == ""
    assert toc["gate"]["checks_passed"] is True


def test_没过闸门1不许切章节(tmp_path):
    """不调 ``toc_gate`` 直接 ``sections_build``：拒绝，``sections.json`` 不落盘。"""
    book_dir, _ = _add_book(tmp_path, bookmarks=True)
    assert agentflow.toc_review(book_dir)["ok"]

    result = agentflow.sections_build(book_dir)
    assert result["ok"] is False
    assert "闸门 1" in result["error"]
    assert not (book_dir / "sections.json").exists()


def test_过了闸门1才让切章节(tmp_path):
    """``toc_gate(approve=True)`` 之后 ``sections_build`` 才成功。"""
    book_dir, _ = _add_book(tmp_path, bookmarks=True)
    assert agentflow.toc_review(book_dir)["ok"]

    gate = agentflow.toc_gate(book_dir, approve=True, approved_by=REVIEWER)
    assert gate["ok"], gate
    assert gate["gate"]["status"] == "approved"
    assert gate["gate"]["approved_by"] == REVIEWER
    assert gate["gate"]["checks_passed"] is True

    built = agentflow.sections_build(book_dir)
    assert built["ok"], built
    assert built["sections"] == 3


def test_闸门1驳回后还是不许切章节(tmp_path):
    """agent 驳回闸门 1：``sections_build`` 照样拒绝。"""
    book_dir, _ = _add_book(tmp_path, bookmarks=True)
    assert agentflow.toc_review(book_dir)["ok"]

    gate = agentflow.toc_gate(book_dir, approve=False, approved_by=REVIEWER, notes="目录有问题")
    assert gate["ok"], gate
    assert gate["gate"]["status"] == "rejected"
    assert gate["gate"]["approved_by"] == ""

    result = agentflow.sections_build(book_dir)
    assert result["ok"] is False
    assert "闸门 1" in result["error"]
    assert not (book_dir / "sections.json").exists()


def test_状态提示指向toc_gate(tmp_path):
    """闸门 1 没批时，``status`` 的 ``next`` 要指向 ``toc_gate``。"""
    book_dir, _ = _add_book(tmp_path, bookmarks=True)
    assert agentflow.toc_review(book_dir)["ok"]

    state = agentflow.status(book_dir)
    assert state["step"] == "toc_review"
    assert state["gates"]["toc"] == "pending"
    assert "toc_gate" in state["next"]


def test_闸门1没过时切章节被拒(tmp_path):
    """闸门 1（目录）没过，``sections_build`` 直接拒绝。"""
    book_dir, _ = _add_book(tmp_path)
    write_toc(book_dir)  # gate 还是 pending

    result = agentflow.sections_build(book_dir)
    assert result["ok"] is False
    assert "闸门 1" in result["error"]
    assert not (book_dir / "sections.json").exists()


def test_确认人格式不合规就拒收(tmp_path):
    """``approved_by`` 必须形如 ``agent:<harness>:<模型名>``。"""
    book_dir, _ = _add_book(tmp_path)
    write_toc(book_dir)

    for bad in ("agent:agent", "agent", "agent::model", ""):
        result = agentflow.toc_gate(book_dir, approve=True, approved_by=bad)
        assert result["ok"] is False, bad
        assert "approved_by" in result["error"]


# --------------------------------------------------------------------------- 交卷


def test_交卷解析失败时返回重试(tmp_path):
    """agent 的回包解析不出来时，返回 ``retry`` 让它重答这一段。"""
    book_dir = _ready_book(tmp_path)
    nxt = agentflow.extract_next(book_dir)
    seg_id = nxt["segments"][0]["seg_id"]

    result = agentflow.extract_submit(book_dir, seg_id, "这不是 JSON", EXTRACTOR)
    assert result["ok"] is False
    assert result["retry"] is True
    assert "keywords" in result["error"]

    # 抽出器标识不合规也拒收。
    bad = agentflow.extract_submit(book_dir, seg_id, EXTRACT_ANSWERS[2], "llm:minimax:x:ex-v2")
    assert bad["ok"] is False
    assert "extractor" in bad["error"]


def test_同一个seg重交时覆盖(tmp_path):
    """同一个 seg 重复交卷：后交的覆盖先交的。"""
    book_dir = _ready_book(tmp_path)
    nxt = agentflow.extract_next(book_dir)
    segment = next(s for s in nxt["segments"] if s["page"] == 2)

    first = agentflow.extract_submit(
        book_dir, segment["seg_id"], EXTRACT_ANSWERS[2], EXTRACTOR
    )
    assert first["items"] == 1

    second = agentflow.extract_submit(
        book_dir, segment["seg_id"], EXTRACT_ANSWER_TWO_WORDS, EXTRACTOR
    )
    assert second["items"] == 2

    rows = [
        json.loads(line)
        for line in (book_dir / "candidates.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    same = [row for row in rows if row["seg_id"] == segment["seg_id"]]
    assert len(same) == 1  # 只剩一行
    assert len(same[0]["items"]) == 2  # 后交的覆盖先交的


def test_租约过期后重新出题(tmp_path):
    """已出题未交卷的段不重复出；超过 30 分钟就重新出。"""
    book_dir = _ready_book(tmp_path)

    first = agentflow.extract_next(book_dir)
    assert len(first["segments"]) == 4
    leased = [segment["seg_id"] for segment in first["segments"]]

    # 剩下的段还能出（没出过的当然能出）。
    second = agentflow.extract_next(book_dir)
    assert len(second["segments"]) == 4

    # 前面出过的段租约还占着；只有没出过的第 9 段还能出。
    third = agentflow.extract_next(book_dir)
    assert len(third["segments"]) == 1
    assert third["done"] == 0

    # 把第一批的租约改成 40 分钟前 ⇒ 过期 ⇒ 重新出题。
    state_path = book_dir / "agentflow.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    expired = (datetime.now().astimezone() - timedelta(minutes=40)).isoformat(
        timespec="seconds"
    )
    for seg_id in leased:
        state["extract"]["issued"][seg_id] = expired
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    fourth = agentflow.extract_next(book_dir)
    assert [segment["seg_id"] for segment in fourth["segments"]] == leased


# --------------------------------------------------------------------------- 打包


def _packable_book(tmp_path: Path) -> Path:
    """过两道闸门 + 全量抽完 + 挑卡定位质检，走到能 ``pack`` 的状态。"""
    book_dir = _ready_book(tmp_path)
    _extract_all(book_dir)
    _extract_all(book_dir)
    assert agentflow.cards_build(book_dir)["ok"]
    return book_dir


def test_内部分享版不传场景走默认图片场景(tmp_path):
    """不传 ``scene`` 就走默认场景（image），``scene_chosen_by`` 记 ``default``。

    the maintainer 2026-11-06 拍板：处理书一律默认用书页图，不用问用户。
    """
    book_dir = _ready_book(tmp_path)
    _extract_all(book_dir)
    _extract_all(book_dir)
    assert agentflow.cards_build(book_dir)["ok"]

    result = agentflow.pack(book_dir, edition="private")
    assert result["ok"] is True, result
    assert result["scene_mode"] == "image"
    assert result["scene_chosen_by"] == "default"

    manifest = assert_scene_chosen_by_consistent(book_dir, "default")
    assert manifest["scene_mode"] == "image"


def test_内部分享版传场景和用户选定(tmp_path):
    """``scene="text"`` + ``scene_chosen_by="user"`` ⇒ 两处都记 ``user``。"""
    book_dir = _ready_book(tmp_path)
    _extract_all(book_dir)
    _extract_all(book_dir)
    assert agentflow.cards_build(book_dir)["ok"]

    result = agentflow.pack(
        book_dir, edition="private", scene="text", scene_chosen_by="user"
    )
    assert result["ok"] is True, result
    assert result["scene_mode"] == "text"
    assert result["scene_chosen_by"] == "user"

    manifest = assert_scene_chosen_by_consistent(book_dir, "user")
    assert manifest["scene_mode"] == "text"


def test_传了场景但没说用户选过就记default(tmp_path):
    """只传 ``scene`` 不传 ``scene_chosen_by`` ⇒ 记 ``default``（不是 ``user``）。"""
    book_dir = _ready_book(tmp_path)
    _extract_all(book_dir)
    _extract_all(book_dir)
    assert agentflow.cards_build(book_dir)["ok"]

    result = agentflow.pack(book_dir, edition="private", scene="text")
    assert result["ok"] is True, result
    assert result["scene_mode"] == "text"
    assert result["scene_chosen_by"] == "default"

    manifest = assert_scene_chosen_by_consistent(book_dir, "default")
    assert manifest["scene_mode"] == "text"


def test_scene_chosen_by只收user和default(tmp_path):
    """``"cli"`` / 乱值 / 空串 ⇒ 直接报错，且不落包（契约 §7.1）。"""
    for bad in ("cli", "hologram", ""):
        base = tmp_path / f"bad-{bad or 'empty'}"
        base.mkdir()
        book_dir = _packable_book(base)

        result = agentflow.pack(
            book_dir, edition="private", scene="text", scene_chosen_by=bad
        )
        assert result["ok"] is False, bad
        assert "scene_chosen_by" in result["error"]
        assert not list((book_dir / "pack").glob("*.ttfc.zip"))


def test_pack_from_cli传场景记cli(tmp_path):
    """``pack_from_cli(scene="text")`` ⇒ 两处都记 ``cli``（契约 §7.1）。"""
    book_dir = _packable_book(tmp_path)

    result = agentflow.pack_from_cli(book_dir, edition="private", scene="text")
    assert result["ok"] is True, result
    assert result["scene_mode"] == "text"
    assert result["scene_chosen_by"] == "cli"

    manifest = assert_scene_chosen_by_consistent(book_dir, "cli")
    assert manifest["scene_mode"] == "text"


def test_pack_from_cli不传场景记default(tmp_path):
    """``pack_from_cli()`` 不传 ``scene`` ⇒ 默认 image，记 ``default``。"""
    book_dir = _packable_book(tmp_path)

    result = agentflow.pack_from_cli(book_dir, edition="private")
    assert result["ok"] is True, result
    assert result["scene_mode"] == "image"
    assert result["scene_chosen_by"] == "default"

    manifest = assert_scene_chosen_by_consistent(book_dir, "default")
    assert manifest["scene_mode"] == "image"


# --------------------------------------------------------------------------- 命令行


def test_flow子命令输出JSON(tmp_path, capsys):
    """``bookcard flow status`` 把 JSON 打到 stdout。"""
    book_dir = _ready_book(tmp_path)

    exit_code = main(["flow", "status", str(book_dir)])
    assert exit_code == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["gates"]["toc"] == "approved"
    assert payload["next"]


def test_flow子命令交卷走文件(tmp_path, capsys):
    """``bookcard flow extract-submit --output-file`` 走文件交卷。"""
    book_dir = _ready_book(tmp_path)
    nxt = agentflow.extract_next(book_dir)
    segment = nxt["segments"][0]

    answer_path = tmp_path / "answer.json"
    answer_path.write_text(EXTRACT_ANSWERS[segment["page"]], encoding="utf-8")

    exit_code = main(
        [
            "flow",
            "extract-submit",
            str(book_dir),
            "--seg-id",
            segment["seg_id"],
            "--output-file",
            str(answer_path),
            "--extractor",
            EXTRACTOR,
        ]
    )
    assert exit_code == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["items"] == 1


def test_flow打包传场景记cli(tmp_path, capsys):
    """``bookcard flow pack --scene text`` 端到端：走 ``pack_from_cli`` 记 ``cli``。"""
    book_dir = _packable_book(tmp_path)

    exit_code = main(["flow", "pack", str(book_dir), "--scene", "text"])
    assert exit_code == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["scene_mode"] == "text"
    assert payload["scene_chosen_by"] == "cli"
    assert payload["book"]["scene_chosen_by"] == "cli"


def test_flow子命令失败时退出码非零(tmp_path, capsys):
    """业务失败时退出码是 1（JSON 照样打到 stdout）。"""
    book_dir = tmp_path / "books" / "nobody"
    book_dir.mkdir(parents=True)

    exit_code = main(["flow", "extract-next", str(book_dir)])
    assert exit_code == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]


@pytest.mark.parametrize(
    "command",
    [
        "book-add",
        "status",
        "toc-review",
        "toc-gate",
        "sections-build",
        "extract-next",
        "extract-submit",
        "cards-build",
        "pack",
    ],
)
def test_flow每个函数都有同名命令(command):
    """每个 agentflow 函数都有一个同名的 CLI 包装。"""
    assert any(name == command for name, _ in agentflow_cli_commands())


def agentflow_cli_commands() -> list[tuple[str, str]]:
    from bookcard.flow_cli import COMMANDS

    return list(COMMANDS)


def test_page_texts_for_gate_falls_back_to_tocv_ocr_cache(tmp_path):
    """整本取字还没做时，闸门用「看图目录」自己 OCR 过的缓存。"""
    from bookcard.engine import ocr as ocr_mod

    book_dir = tmp_path / "book"
    cache_dir = book_dir / "tocv" / ocr_mod.OCR_DIRNAME
    cache_dir.mkdir(parents=True)
    records = [{"text": "第7课 中国境内早期人类", "bbox": [0, 0, 1, 1]}]
    (cache_dir / "p0007.json").write_text(
        json.dumps(records, ensure_ascii=False), encoding="utf-8"
    )
    texts = agentflow._page_texts_for_gate(book_dir)
    assert 7 in texts
    assert "中国境内早期人类" in texts[7]


def test_page_number_of_cache_parses_names():
    assert agentflow._page_number_of_cache("p0007.json") == 7
    assert agentflow._page_number_of_cache("other.json") is None
