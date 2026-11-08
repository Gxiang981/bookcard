"""benchmark v1 金标准（``docs/bench-v1.md`` §2）的测试。

重点测几条容易出错的口径：

- ``text_sha1`` 对不上就报错停下，不许跳过；
- 补漏词里重叠的、不在原文的丢掉并计数，``fixes`` 非空的记成 ``source: "human"``；
- 判定失败重试 2 次以后不进金标准；
- 重写后还不到 2 分就保留两次里分数高的那条，``ref_expl_score`` 写实际分数；
- 输出行里没有书页全文，目录段的 ``spans`` 是空的。

模型调用全部打桩（``llm.complete``），不发真请求。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from kwtrain import bench_gold, llm

# --------------------------------------------------------------------------- 假书

PAGE1 = (
    "第一章 功和能\n"
    "电功率表示电流做功的快慢，功率等于电压与电流的乘积。\n"
    "欧姆定律是电学的基本定律，电流与电压成正比。\n"
    "新石器时代晚期文化以玉器著称。\n"
)
PAGE2 = "目 录\n第一章 走进化学世界\n第二章 我们周围的空气\n"

FP12 = "abcdef123456"
SEG1 = f"book:{FP12}:p0001"
SEG2 = f"book:{FP12}:p0002"


def _item(surface: str, ktype: str, text: str, explanation: str | None, evidence: str) -> dict:
    start = text.index(surface)
    ev_start = text.index(evidence)
    return {
        "surface": surface,
        "type": ktype,
        "start": start,
        "end": start + len(surface),
        "explanation": explanation,
        "evidence": {"start": ev_start, "end": ev_start + len(evidence)},
        "learn_value": 2,
    }


def _candidates() -> dict:
    return {
        "seg_id": SEG1,
        "page": 1,
        "chunk": 0,
        "sid": "s01",
        "text_sha1": bench_gold.text_sha1(PAGE1),
        "extractor": "llm:minimax:MiniMax-M3.1-Flash:ex-v1",
        "scope": "full",
        "items": [
            _item(
                "电功率",
                "term",
                PAGE1,
                "电做功快慢的物理量，等于电压与电流的乘积，单位是瓦特。",
                "电功率表示电流做功的快慢，功率等于电压与电流的乘积。",
            ),
            _item(
                "欧姆定律",
                "term",
                PAGE1,
                "描述电流与电压、电阻之间关系的电学基本定律。",
                "欧姆定律是电学的基本定律，电流与电压成正比。",
            ),
            _item(
                "新石器时代",
                "concept",
                PAGE1,
                "旧石器时代之后、约一万年前开始的新石器时代。",
                "新石器时代晚期文化以玉器著称。",
            ),
            # explanation 为 null 的不算候选（不是首次出现那条）。
            _item("第一章", "term", PAGE1, None, "第一章 功和能"),
        ],
        "dropped": {"not_in_text": 0, "bad_evidence": 0},
    }


def _write_book(data_root: Path) -> Path:
    book_dir = data_root / FP12
    book_dir.mkdir(parents=True, exist_ok=True)
    (book_dir / "book.json").write_text(
        json.dumps(
            {
                "fp": FP12 + "0" * 28,
                "fp12": FP12,
                "title": "测试书",
                "subject": "历史",
                "page_count": 2,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    pages = []
    for number, text, kind in ((1, PAGE1, "body"), (2, PAGE2, "toc")):
        pages.append(
            {
                "page": number,
                "printed_page": str(number),
                "text": text,
                "text_sha1": bench_gold.text_sha1(text),
                "kind": kind,
                "width": 595.0,
                "height": 841.0,
            }
        )
    (book_dir / "pages.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in pages) + "\n",
        encoding="utf-8",
    )
    (book_dir / "sections.json").write_text(
        json.dumps(
            [
                {
                    "sid": "s01",
                    "title": "第一章",
                    "level": 1,
                    "page_start": 1,
                    "page_end": 1,
                    "sort_order": 1,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (book_dir / "candidates.jsonl").write_text(
        json.dumps(_candidates(), ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return book_dir


def _write_pages(pages_path: Path, *, sha1_override: str | None = None) -> Path:
    payload = {
        "version": "bench-v1",
        "books": [
            {
                "fp": FP12 + "0" * 28,
                "fp12": FP12,
                "title": "测试书",
                "pages": [
                    {
                        "page": 1,
                        "stratum": "body",
                        "kind": "body",
                        "text_sha1": sha1_override or bench_gold.text_sha1(PAGE1),
                        "seg_ids": [SEG1],
                    },
                    {
                        "page": 2,
                        "stratum": "toc",
                        "kind": "toc",
                        "text_sha1": bench_gold.text_sha1(PAGE2),
                        "seg_ids": [SEG2],
                    },
                ],
            }
        ],
    }
    pages_path.parent.mkdir(parents=True, exist_ok=True)
    pages_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return pages_path


def _v(kw: str, expl: int, note: str = "") -> dict[str, Any]:
    """一条判定（测试里省得写一长串）。"""
    return {"kw": kw, "evidence": "ok", "expl": expl, "fatal": False, "note": note}


# --------------------------------------------------------------------------- 打桩


class StubLLM:
    """按 system 段分流的假模型。"""

    def __init__(
        self,
        *,
        judge_spec: Any = None,
        judge_broken: bool = False,
        missed_reply: str = '{"keywords": []}',
        rewrite_reply: str = '{"rewrites": []}',
    ) -> None:
        self.judge_spec = judge_spec or {}
        self.judge_broken = judge_broken
        self.missed_reply = missed_reply
        self.rewrite_reply = rewrite_reply
        self.calls: list[tuple[str, str]] = []

    def __call__(self, system: str, user: str, **kwargs: Any) -> str:
        self.calls.append((system, user))
        if "补标关键词" in system:
            return self.missed_reply
        if "改写词语解释" in system:
            return self.rewrite_reply
        return self._judge_reply(user)

    def _judge_reply(self, user: str) -> str:
        if self.judge_broken:
            return "这不是 JSON，回包坏了"
        blocks = re.findall(r"\[(\d+)\] front：(.+)\n\s+back：(.+)", user)
        verdicts: list[dict[str, Any]] = []
        for index, front, back in blocks:
            spec = self.judge_spec
            if callable(spec):
                spec = spec(front, back)
            else:
                spec = spec.get(front)
            if spec is None:
                continue  # 模型漏判这一条
            verdicts.append({"index": int(index), **spec})
        return json.dumps({"verdicts": verdicts}, ensure_ascii=False)

    def judge_calls(self) -> list[str]:
        return [user for system, user in self.calls if "质检的编辑" in system]


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> StubLLM:
    """默认：三个候选都 accept；电功率解释 0 分（触发重写）。"""
    table = {
        "电功率": _v("accept", 0, "解释太短"),
        "欧姆定律": _v("accept", 2, "准确"),
        "新石器时代": _v("boundary", 1, "边界少了字"),
    }
    fake = StubLLM(judge_spec=table)
    monkeypatch.setattr(llm, "complete", fake)
    return fake


def _run(tmp_path: Path, stub_llm: StubLLM, **kwargs: Any) -> tuple[list[dict], dict]:
    data_root = tmp_path / "data"
    _write_book(data_root)
    pages_path = _write_pages(tmp_path / "pages.json")
    return bench_gold.run(
        pages_path,
        data_root,
        tmp_path / "work",
        workers=1,
        missed_prompt_path=bench_gold.default_prompt("bench_missed_v1.md"),
        rewrite_prompt_path=bench_gold.default_prompt("bench_rewrite_v1.md"),
        **kwargs,
    )


def _spans(rows: list[dict], seg_id: str) -> list[dict]:
    for row in rows:
        if row["seg_id"] == seg_id:
            return row["spans"]
    raise AssertionError(f"没有这一段：{seg_id}")


# --------------------------------------------------------------------------- 测试


def test_sha1对不上就报错停下(tmp_path: Path, stub: StubLLM) -> None:
    """pages.json 记的 text_sha1 和重建出来的对不上 → 报错，不许跳过。"""
    data_root = tmp_path / "data"
    _write_book(data_root)
    pages_path = _write_pages(tmp_path / "pages.json", sha1_override="0" * 40)
    with pytest.raises(bench_gold.BenchGoldError) as excinfo:
        bench_gold.run(pages_path, data_root, tmp_path / "work", workers=1)
    assert "对不上" in str(excinfo.value)


def test_输出行里没有页面全文且目录段spans为空(tmp_path: Path, stub: StubLLM) -> None:
    """金标准只存偏移和 surface，不存书页全文；目录段固定空。"""
    rows, _stats = _run(tmp_path, stub)
    assert [row["seg_id"] for row in rows] == [SEG1, SEG2]
    toc = _spans(rows, SEG2)
    assert toc == []
    for row in rows:
        assert "text" not in row
        assert "evidence" not in row
        assert PAGE1 not in json.dumps(row, ensure_ascii=False)
        assert PAGE2 not in json.dumps(row, ensure_ascii=False)
        for span in row["spans"]:
            assert set(span) == {
                "start",
                "end",
                "surface",
                "type",
                "ref_explanation",
                "source",
                "ref_expl_score",
            }
            assert PAGE1[span["start"] : span["end"]] == span["surface"]
            assert span["ref_expl_score"] in (0, 1, 2)


def test_补漏重叠和不在原文的丢掉并计数(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """补漏词里和已收录 span 重叠的、不在原文里的都丢掉，并且计数。"""
    table = {
        "电功率": _v("accept", 2),
        "欧姆定律": _v("accept", 2),
        "新石器时代": _v("boundary", 1),
        "新石器时代晚期": _v("accept", 1),
        "玉器": _v("accept", 1),
    }
    missed = json.dumps(
        {
            "keywords": [
                {
                    "surface": "欧姆定律",
                    "type": "term",
                    "evidence": "欧姆定律是电学的基本定律，电流与电压成正比。",
                    "explanation": "描述电流与电压、电阻关系的定律。",
                    "fixes": None,
                },
                {
                    "surface": "根本不存在的词",
                    "type": "term",
                    "evidence": "无",
                    "explanation": "原文里没有这个词。",
                    "fixes": None,
                },
                {
                    "surface": "新石器时代晚期",
                    "type": "name",
                    "evidence": "新石器时代晚期文化以玉器著称。",
                    "explanation": "新石器时代晚期的考古学文化阶段。",
                    "fixes": "新石器时代",
                },
                {
                    "surface": "玉器",
                    "type": "term",
                    "evidence": "新石器时代晚期文化以玉器著称。",
                    "explanation": "用玉制成的礼器和工具。",
                    "fixes": None,
                },
            ]
        },
        ensure_ascii=False,
    )
    fake = StubLLM(judge_spec=table, missed_reply=missed)
    monkeypatch.setattr(llm, "complete", fake)
    rows, stats = _run(tmp_path, fake)
    book_stats = stats["books"][FP12]
    assert book_stats["missed_proposed"] == 4
    assert book_stats["missed_dropped"]["not_in_text"] == 1
    assert book_stats["missed_dropped"]["overlap_candidate"] == 1
    assert book_stats["missed_accepted"] == 2
    assert book_stats["human_fixed"] == 1
    surfaces = {span["surface"]: span for span in _spans(rows, SEG1)}
    assert "欧姆定律" in surfaces  # 候选里已收录，没被补漏顶掉
    assert "新石器时代晚期" in surfaces
    assert surfaces["新石器时代晚期"]["source"] == "human"
    assert "玉器" in surfaces
    assert surfaces["玉器"]["source"] == "missed"


def test_判定失败重试两次后不进金标准(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """模型一直漏判某一条 → 重试 2 次以后不进金标准，计入 judge_failed。"""
    table = {
        "电功率": _v("accept", 2),
        "欧姆定律": _v("accept", 2),
        "新石器时代": _v("boundary", 1),
    }

    def spec(front: str, back: str) -> dict | None:
        if front == "电功率":
            return None  # 一直漏判
        return table[front]

    fake = StubLLM(judge_spec=spec)
    monkeypatch.setattr(llm, "complete", fake)
    rows, stats = _run(tmp_path, fake)
    surfaces = {span["surface"] for span in _spans(rows, SEG1)}
    assert "电功率" not in surfaces
    assert stats["books"][FP12]["judge_failed"] == 1
    # 首次 + 2 次重试 = 3 次
    power_calls = [user for user in fake.judge_calls() if "front：电功率" in user]
    assert len(power_calls) == 3


def test_回包坏了重试两次后不进金标准(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """回包不是 JSON → 重试 2 次以后还是不行，就不进金标准。"""
    fake = StubLLM(judge_broken=True)
    monkeypatch.setattr(llm, "complete", fake)
    rows, stats = _run(tmp_path, fake)
    assert _spans(rows, SEG1) == []
    assert stats["books"][FP12]["judge_failed"] == 3


def test_重写后还不到2分保留分数高的那条(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """重写后还是不到 2 分 → 保留两次里分数高的那条，ref_expl_score 写实际分数。"""

    def spec(front: str, back: str) -> dict:
        if front == "电功率":
            # 原解释判 0 分；重写后的新解释判 1 分 → 保留新的。
            expl = 0 if "单位是瓦特" in back else 1
            return _v("accept", expl, "改写")
        return _v("accept", 2)

    rewrite = json.dumps(
        {"rewrites": [{"index": 0, "explanation": "电做功快慢的物理量，等于电压与电流的乘积。"}]},
        ensure_ascii=False,
    )
    fake = StubLLM(judge_spec=spec, rewrite_reply=rewrite)
    monkeypatch.setattr(llm, "complete", fake)
    rows, stats = _run(tmp_path, fake)
    spans = {span["surface"]: span for span in _spans(rows, SEG1)}
    power = spans["电功率"]
    assert power["ref_expl_score"] == 1
    assert power["ref_explanation"] == "电做功快慢的物理量，等于电压与电流的乘积。"
    assert stats["books"][FP12]["ref_expl_score"]["1"] == 1
    assert stats["books"][FP12]["rewritten"] == 1


def test_重写比原解释差时保留原解释(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """重写后分数比原来低 → 保留原解释和原来的分数。"""

    def spec(front: str, back: str) -> dict:
        if front == "电功率":
            expl = 1 if "单位是瓦特" in back else 0
            return _v("accept", expl, "打分")
        return _v("accept", 2)

    rewrite = json.dumps(
        {"rewrites": [{"index": 0, "explanation": "重写之后更差的一句解释。"}]},
        ensure_ascii=False,
    )
    fake = StubLLM(judge_spec=spec, rewrite_reply=rewrite)
    monkeypatch.setattr(llm, "complete", fake)
    rows, _stats = _run(tmp_path, fake)
    power = {span["surface"]: span for span in _spans(rows, SEG1)}["电功率"]
    assert power["ref_expl_score"] == 1
    assert power["ref_explanation"] != "重写之后更差的一句解释。"


def test_补漏词判不出来也计入judge_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """补漏出来的词判不出来 → 重试 2 次以后不进金标准，也计入 judge_failed。"""
    table = {
        "电功率": _v("accept", 2),
        "欧姆定律": _v("accept", 2),
        "新石器时代": _v("boundary", 1),
    }
    missed = json.dumps(
        {
            "keywords": [
                {
                    "surface": "玉器",
                    "type": "term",
                    "evidence": "新石器时代晚期文化以玉器著称。",
                    "explanation": "用玉制成的礼器和工具。",
                    "fixes": None,
                }
            ]
        },
        ensure_ascii=False,
    )

    def spec(front: str, back: str) -> dict | None:
        if front == "玉器":
            return None  # 补漏的这条一直判不出来
        return table[front]

    fake = StubLLM(judge_spec=spec, missed_reply=missed)
    monkeypatch.setattr(llm, "complete", fake)
    rows, stats = _run(tmp_path, fake)
    surfaces = {span["surface"] for span in _spans(rows, SEG1)}
    assert "玉器" not in surfaces
    book_stats = stats["books"][FP12]
    assert book_stats["judge_failed_missed"] == 1
    assert book_stats["judge_failed"] == 1


def test_解析补漏和重写回包(tmp_path: Path) -> None:
    """补漏回包里的 fixes 留着；重写回包按 index 对回。"""
    raw = json.dumps(
        {
            "keywords": [
                {
                    "surface": "玉器",
                    "type": "term",
                    "evidence": "原文里的句子",
                    "explanation": "解释",
                    "fixes": "新石器时代",
                }
            ]
        },
        ensure_ascii=False,
    )
    items = bench_gold.parse_missed(raw)
    assert items[0].surface == "玉器"
    assert items[0].fixes == "新石器时代"
    rewrites = bench_gold.parse_rewrites(
        json.dumps({"rewrites": [{"index": 2, "explanation": "新的解释"}]}, ensure_ascii=False)
    )
    assert rewrites == {2: "新的解释"}


def test_补漏偏移按契约落(tmp_path: Path) -> None:
    """补漏词取首次出现；不在原文里的、和已收录重叠的都丢掉并计数。"""
    seg = bench_gold.SegSpec(
        seg_id=SEG1,
        fp12=FP12,
        page=1,
        chunk=0,
        stratum="body",
        kind="body",
        text=PAGE1,
        text_sha1=bench_gold.text_sha1(PAGE1),
        section_title="第一章",
        sid="s01",
        book_index=0,
    )
    accepted = [(PAGE1.index("欧姆定律"), PAGE1.index("欧姆定律") + 4)]
    items = [
        bench_gold.MissedItem("欧姆定律", "term", None, "解释", None),
        bench_gold.MissedItem("不存在的词", "term", None, "解释", None),
        bench_gold.MissedItem("玉器", "term", None, "解释", None),
        bench_gold.MissedItem("玉器", "term", None, "解释", None),
    ]
    placed, dropped = bench_gold.place_missed(seg, items, accepted)
    assert [word.surface for word in placed] == ["玉器"]
    assert dropped == {"not_in_text": 1, "overlap_candidate": 1, "overlap_missed": 1}
    assert placed[0].start == PAGE1.index("玉器")
