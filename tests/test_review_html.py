"""bookcard.review 的测试。

夹具是手写的：3 个 section、30 张卡、各种 agent 判定（accept / boundary /
reject、expl 0/1/2、fatal true、依据 missing / wrong、还有一张 agent 没审），
正文是中文教材体，偏移按 Python 码点算。

覆盖：抽样确定性、导出格式、HTML 自包含（不联网）、CLI 入口、审核记录校验。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bookcard import cli, review
from bookcard._review_assets import JS_CORE

sys.path.insert(0, str(Path(__file__).parent))
from review_fixture import build_demo_dir  # noqa: E402

NODE = shutil.which("node")


@pytest.fixture
def work_dir(tmp_path):
    """手写夹具铺出来的工作目录。"""
    return build_demo_dir(tmp_path / "book")


def _payload(html: str) -> dict:
    """从生成的 HTML 里抠出内嵌的数据。"""
    match = re.search(
        r'<script type="application/json" id="bookcard-data">(.*?)</script>',
        html,
        re.S,
    )
    assert match is not None, "HTML 里没有内嵌数据"
    return json.loads(match.group(1))


def _card_by_id(payload: dict, card_id: str) -> dict:
    for card in payload["cards"]:
        if card["card_id"] == card_id:
            return card
    raise AssertionError(f"HTML 里没有卡 {card_id}")


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #


def test_build_embeds_cards_pages_and_agent_verdicts(work_dir, tmp_path):
    """HTML 里内嵌了全部卡、页正文和 agent 判定。"""
    out = tmp_path / "review.html"
    summary = review.build_review_html(work_dir, out, sample=0)
    data = _payload(out.read_text(encoding="utf-8"))

    assert summary["cards"] == 30
    assert len(data["cards"]) == 30
    assert len(data["pages"]) == 9  # 夹具里 9 页正文（10–18）

    with_agent = [card for card in data["cards"] if card["agent"]]
    assert len(with_agent) == 29  # 有一张 agent 没审

    # 关键词偏移和依据句偏移都要落在页正文里
    for card in data["cards"]:
        page_text = data["pages"][str(card["page"])]
        assert page_text[card["kw_start"] : card["kw_end"]] == card["front"]
        assert page_text[card["evidence_start"] : card["evidence_end"]] == card["evidence"]


def test_build_summary_numbers(work_dir, tmp_path):
    """汇总数字按 §6 的合格口径算。"""
    out = tmp_path / "review.html"
    summary = review.build_review_html(work_dir, out, sample=0)
    data = _payload(out.read_text(encoding="utf-8"))

    # 夹具：29 张有 agent 判定，其中不合格的 8 张是
    # expl=0（井田制）、boundary（分封制、军机处）、reject（青铜器、崇文抑武）、
    # evidence=missing（铁制农具）、evidence=wrong（九品中正制）、fatal=true（中央集权）
    assert summary["agent_judged"] == 29
    assert summary["agent_pass"] == 21
    assert summary["agent_pass_rate"] == round(21 / 29, 4)
    assert data["summary"]["cards_total"] == 30
    assert len(data["summary"]["by_section"]) == 3


def test_build_is_self_contained(work_dir, tmp_path):
    """不联网、不依赖 CDN：HTML 里不能有外部地址。"""
    out = tmp_path / "review.html"
    review.build_review_html(work_dir, out, sample=10)
    html = out.read_text(encoding="utf-8")

    assert "http://" not in html
    assert "https://" not in html
    assert "//" not in html.replace("http://", "").replace("https://", "")
    assert "<link" not in html
    assert "src=" not in html
    assert "@import" not in html
    assert "url(" not in html
    # CSS / JS / 数据都在文件里
    assert "<style>" in html
    assert "Array.from" in html


def test_html_has_chinese_ui(work_dir, tmp_path):
    """界面是中文的，四个判定和漏标都在。"""
    out = tmp_path / "review.html"
    review.build_review_html(work_dir, out, sample=0)
    html = out.read_text(encoding="utf-8")

    for needle in ("卡片审核", "agent 合格率", "人机一致率", "漏标", "关键词 kw", "依据 evidence"):
        assert needle in html, f"HTML 里少了「{needle}」"


# --------------------------------------------------------------------------- #
# 抽样
# --------------------------------------------------------------------------- #


def test_sample_is_reproducible(work_dir):
    """同 seed 结果一致，换 seed 结果不同，抽全量时不受影响。"""
    payload = review.build_payload(work_dir, sample=0)

    def picked(seed: int) -> list[str]:
        data = review.build_payload(work_dir, sample=12, seed=seed)
        return [card["card_id"] for card in data["cards"] if card["sampled"]]

    assert picked(0) == picked(0)
    assert len(picked(0)) == 12
    assert set(picked(0)) <= {card["card_id"] for card in payload["cards"]}
    assert picked(0) != picked(1)
    # sample >= 总数 → 全部
    everything = review.build_payload(work_dir, sample=999)
    assert all(card["sampled"] for card in everything["cards"])
    assert everything["summary"]["cards_sampled"] == 30


def test_sample_is_stratified_by_section_and_verdict(work_dir):
    """分层抽样：每个 section 都要抽到，合格与不合格都要抽到。"""
    data = review.build_payload(work_dir, sample=9, seed=3)
    sampled = [card for card in data["cards"] if card["sampled"]]

    assert len(sampled) == 9
    assert len({card["sid"] for card in sampled}) == 3

    classes = set()
    for card in sampled:
        agent = card["agent"]
        classes.add("none" if agent is None else ("pass" if review.is_pass(agent) else "fail"))
    assert "pass" in classes
    assert "fail" in classes


def test_sample_cards_is_pure_function():
    """抽样函数是纯的：同参数同结果，且是子集。"""

    class FakeCard:
        def __init__(self, index: int, sid: str, passed: bool):
            self.card_id = f"c{index}"
            self.sid = sid
            self.front = f"词{index}"
            self.agent = (
                {"kw": "accept", "evidence": "ok", "expl": 2, "fatal": False}
                if passed
                else {"kw": "reject", "evidence": "ok", "expl": 2, "fatal": False}
            )

    cards = [FakeCard(i, f"s{i % 3}", i % 2 == 0) for i in range(30)]
    first = review.sample_cards(cards, 10, seed=7)
    second = review.sample_cards(cards, 10, seed=7)
    assert [card.card_id for card in first] == [card.card_id for card in second]
    assert len(first) == 10
    assert {card.card_id for card in first} <= {card.card_id for card in cards}
    assert review.sample_cards(cards, 0, seed=7) == cards  # 0 = 全部
    assert review.sample_cards(cards, None, seed=7) == cards


# --------------------------------------------------------------------------- #
# 导出格式
# --------------------------------------------------------------------------- #


def test_validate_review_rows_accepts_contract_shape():
    """§6 的记录形状要能通过校验。"""
    row = {
        "card_id": "abc:s01:1234abcd",
        "kw": "accept",
        "evidence": "ok",
        "expl": 2,
        "fatal": False,
        "note": "",
        "reviewer": "human:gxiang",
        "at": "2026-11-06T10:00:00+08:00",
    }
    assert review.validate_review_rows([row]) == []


def test_validate_review_rows_catches_bad_values():
    """取值不在 §6 约定内的要报错。"""
    bad = {
        "card_id": "abc",
        "kw": "unsure",
        "evidence": "maybe",
        "expl": 3,
        "fatal": "yes",
        "note": "",
        "reviewer": "gxiang",
        "at": "",
    }
    errors = review.validate_review_rows([bad])
    joined = "\n".join(errors)
    assert "kw" in joined
    assert "evidence" in joined
    assert "expl" in joined
    assert "fatal" in joined
    assert "reviewer" in joined
    assert "at" in joined


def test_merge_review_rows_human_overrides_agent():
    """人工判定覆盖 agent 判定。"""
    merged = review.merge_review_rows(
        [
            {
                "card_id": "a",
                "kw": "accept",
                "evidence": "ok",
                "expl": 2,
                "fatal": False,
                "note": "agent 的",
                "reviewer": "agent:agent",
                "at": "2026-11-06T09:00:00+08:00",
            },
            {
                "card_id": "b",
                "kw": "accept",
                "evidence": "ok",
                "expl": 2,
                "fatal": False,
                "note": "",
                "reviewer": "agent:agent",
                "at": "2026-11-06T09:00:00+08:00",
            },
        ],
        [
            {
                "card_id": "a",
                "kw": "boundary",
                "evidence": "ok",
                "expl": 1,
                "fatal": False,
                "note": "人工改的",
                "reviewer": "human:gxiang",
                "at": "2026-11-06T10:00:00+08:00",
            }
        ],
    )
    assert [row["card_id"] for row in merged] == ["a", "b"]
    assert merged[0]["kw"] == "boundary"
    assert merged[0]["note"] == "人工改的"
    assert merged[0]["reviewer"] == "human:gxiang"
    assert merged[1]["reviewer"] == "agent:agent"


@pytest.mark.skipif(NODE is None, reason="本机没有 node，跳过 JS 逻辑单测")
def test_js_export_rows_match_contract(work_dir, tmp_path):
    """浏览器导出的行要符合 §6：字段、取值、reviewer 全部对得上。"""
    data = review.build_payload(work_dir, sample=0)
    card_id = data["cards"][0]["card_id"]
    state = {
        card_id: {
            "kw": "boundary",
            "evidence": "missing",
            "expl": 1,
            "fatal": False,
            "note": "人工改的",
            "missed": [{"surface": "漏掉的词", "page": 10}],
            "touched": True,
        }
    }
    script = tmp_path / "harness.js"
    script.write_text(
        JS_CORE
        + "\n"
        + "const cards = "
        + json.dumps(data["cards"], ensure_ascii=False)
        + ";\n"
        + "const state = "
        + json.dumps(state, ensure_ascii=False)
        + ";\n"
        + "const rows = bc_buildReviewRows(cards, state, 'human:gxiang', "
        + json.dumps("2026-11-06T10:00:00+08:00")
        + ");\n"
        + "console.log(JSON.stringify(rows));\n",
        encoding="utf-8",
    )
    proc = subprocess.run([NODE, str(script)], capture_output=True, text=True, check=True)
    rows = json.loads(proc.stdout)

    assert len(rows) == 1
    row = rows[0]
    assert row["card_id"] == card_id
    assert row["kw"] == "boundary"
    assert row["evidence"] == "missing"
    assert row["expl"] == 1
    assert row["fatal"] is False
    assert row["note"] == "人工改的"
    assert row["reviewer"] == "human:gxiang"
    assert row["at"] == "2026-11-06T10:00:00+08:00"
    assert row["missed"] == [{"surface": "漏掉的词", "page": 10}]
    assert review.validate_review_rows(rows) == []


@pytest.mark.skipif(NODE is None, reason="本机没有 node，跳过 JS 逻辑单测")
def test_js_export_skips_untouched_cards(work_dir, tmp_path):
    """没动过的卡不导出（人工判定覆盖 agent 判定，但没动就不算人工判定）。"""
    data = review.build_payload(work_dir, sample=0)
    card_id = data["cards"][0]["card_id"]
    state = {
        card_id: {
            "kw": "accept",
            "evidence": "ok",
            "expl": 2,
            "fatal": False,
            "touched": False,
        }
    }
    script = tmp_path / "harness.js"
    script.write_text(
        JS_CORE
        + "\n"
        + "const rows = bc_buildReviewRows("
        + json.dumps(data["cards"], ensure_ascii=False)
        + ", "
        + json.dumps(state, ensure_ascii=False)
        + ", 'human:gxiang', '2026-11-06T10:00:00+08:00');\n"
        + "console.log(JSON.stringify(rows));\n",
        encoding="utf-8",
    )
    proc = subprocess.run([NODE, str(script)], capture_output=True, text=True, check=True)
    assert json.loads(proc.stdout) == []


@pytest.mark.skipif(NODE is None, reason="本机没有 node，跳过 JS 逻辑单测")
def test_js_consistency_counts_agreement(work_dir, tmp_path):
    """一致率按四个判定逐项比。"""
    data = review.build_payload(work_dir, sample=0)
    card = data["cards"][0]
    state = {
        card["card_id"]: {
            "kw": card["agent"]["kw"],
            "evidence": "missing",  # 改了一项
            "expl": card["agent"]["expl"],
            "fatal": card["agent"]["fatal"],
            "touched": True,
        }
    }
    script = tmp_path / "harness.js"
    script.write_text(
        JS_CORE
        + "\n"
        + "const out = bc_consistency("
        + json.dumps(data["cards"], ensure_ascii=False)
        + ", "
        + json.dumps(state, ensure_ascii=False)
        + ");\n"
        + "console.log(JSON.stringify(out));\n",
        encoding="utf-8",
    )
    proc = subprocess.run([NODE, str(script)], capture_output=True, text=True, check=True)
    out = json.loads(proc.stdout)

    assert out["reviewed"] == 1
    assert out["compared"] == 4
    assert out["matched"] == 3
    assert out["exact"] == 0
    assert out["byField"]["evidence"]["matched"] == 0


@pytest.mark.skipif(NODE is None, reason="本机没有 node，跳过 JS 逻辑单测")
def test_js_split_by_ranges_marks_overlap():
    """关键词落在依据句里时，两段高亮都要在。"""
    script = Path(__file__).parent / "_tmp_split_harness.js"
    script.write_text(
        JS_CORE
        + "\n"
        + "const out = bc_splitByRanges('甲乙丙丁戊', "
        + json.dumps([{"start": 1, "end": 3, "cls": "ev"}, {"start": 2, "end": 4, "cls": "kw"}])
        + ");\n"
        + "console.log(JSON.stringify(out));\n",
        encoding="utf-8",
    )
    try:
        proc = subprocess.run([NODE, str(script)], capture_output=True, text=True, check=True)
        out = json.loads(proc.stdout)
    finally:
        script.unlink()

    assert "".join(piece["text"] for piece in out) == "甲乙丙丁戊"
    assert out[0] == {"text": "甲", "cls": ""}
    assert out[1] == {"text": "乙", "cls": "ev"}
    assert out[2] == {"text": "丙", "cls": "ev kw"}
    assert out[3] == {"text": "丁", "cls": "kw"}


@pytest.mark.skipif(NODE is None, reason="本机没有 node，跳过 JS 语法检查")
def test_embedded_js_is_valid(work_dir, tmp_path):
    """页面里的 JS 必须语法正确（转义错了页面会整个不动，而且不报错）。"""
    out = tmp_path / "review.html"
    review.build_review_html(work_dir, out, sample=10)
    html = out.read_text(encoding="utf-8")
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert blocks, "页面里没有内联脚本"

    script = tmp_path / "app.js"
    script.write_text(blocks[-1], encoding="utf-8")
    proc = subprocess.run([NODE, "--check", str(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_review_html(work_dir, tmp_path, capsys):
    """``bookcard review-html`` 能跑通并写文件。"""
    out = tmp_path / "out.html"
    code = cli.main(
        [
            "review-html",
            str(work_dir),
            "--sample",
            "12",
            "--seed",
            "5",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert out.exists()

    stdout = capsys.readouterr().out
    assert "已生成" in stdout
    assert "30 张卡" in stdout

    data = _payload(out.read_text(encoding="utf-8"))
    assert sum(1 for card in data["cards"] if card["sampled"]) == 12


def test_cli_review_html_reports_missing_dir(tmp_path, capsys):
    """目录里没有卡 → 报错但仍给出告警。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    out = tmp_path / "out.html"
    code = cli.main(["review-html", str(empty), "--out", str(out)])
    err = capsys.readouterr().err
    assert code == 0  # 生成了一页空的，告警说明
    assert "警告" in err
    assert out.exists()


def test_module_main_review_html(work_dir, tmp_path):
    """``python -m bookcard.review`` 也能跑。"""
    out = tmp_path / "out.html"
    code = review.main([str(work_dir), "--sample", "0", "--out", str(out)])
    assert code == 0
    assert out.exists()
