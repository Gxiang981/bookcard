#!/usr/bin/env python3
"""SM-2 的 JS ↔ Python 对拍。

静态页里的 SM-2 是从引擎的 ``src/bookcard/browse/sm2.py`` 抄到 JS 的。两边一旦
漂移，静态页排出来的复习计划和引擎排的不一样，用户看到的就不一样。

这个脚本把 ``assets/page.js`` 里那段 JS 抠出来，用 node 跑一遍，跟 Python 的
``compute_sm2`` 逐个输入对比。

跑法::

    ../.venv/bin/python tests/check_sm2_parity.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "src"))

from sharepack.build import _make_zip  # noqa: E402,F401  （只是确保包能导入）

try:
    from bookcard.browse import sm2 as engine_sm2
except ImportError:  # pragma: no cover
    print("没装 bookcard 引擎，跳过（pip install -e ..）")
    raise SystemExit(0) from None


def extract_js_sm2() -> str:
    """从 assets/page.js 里抠出 SM-2 那段（不含 DOM 相关代码）。"""
    text = (ROOT / "assets" / "page.js").read_text(encoding="utf-8")
    start = text.index("/* ---- SM-2")
    end = text.index("/* ============================ state")
    return text[start:end]


def main() -> int:
    cases = [
        ("again", 0, 1.8, 0),
        ("again", 0, 1.8, 1),
        ("again", 3, 2.1, 10),
        ("again", 3, 1.3, 7),
        ("good", 0, 1.8, 0),
        ("good", 1, 1.9, 1),
        ("good", 2, 2.0, 3),
        ("good", 5, 2.3, 7),
        ("good", 9, 2.5, 200),
    ]
    now = 1_700_000_000_000

    harness = extract_js_sm2() + f"""
const CASES = {json.dumps(cases)};
const NOW = {now};
const out = CASES.map(([rating, rep, ease, interval]) => {{
  const r = computeSm2(rating, rep, ease, interval, NOW);
  return {{repetition: r.repetition, ease: r.easeFactor,
          interval_days: r.intervalDays, next: r.nextReviewAtMs}};
}});
process.stdout.write(JSON.stringify(out));
"""
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "sm2.mjs"
        script.write_text(harness, encoding="utf-8")
        raw = subprocess.run(
            ["node", str(script)], capture_output=True, text=True, check=True
        ).stdout
    js_rows = json.loads(raw)

    ok = True
    print(
        f"{'rating':>6} {'rep':>4} {'ease':>5} {'int':>5} | "
        f"{'py (rep/ease/int)':>22} | {'js (rep/ease/int)':>22}"
    )
    print("-" * 88)
    for (rating, rep, ease, interval), js in zip(cases, js_rows, strict=True):
        py = engine_sm2.compute_sm2(rating, rep, ease, interval, now_ms=now)
        same = (
            py.repetition == js["repetition"]
            and abs(py.ease_factor - js["ease"]) < 1e-9
            and py.interval_days == js["interval_days"]
            and py.next_review_at_ms == js["next"]
        )
        ok = ok and same
        print(
            f"{rating:>6} {rep:>4} {ease:>5} {interval:>5} | "
            f"{py.repetition:>8}/{py.ease_factor:>6}/{py.interval_days:>6} | "
            f"{js['repetition']:>8}/{js['ease']:>6}/{js['interval_days']:>6} | "
            f"{'OK' if same else '❌ 不一致'}"
        )
    print()
    print("全部一致 ✅" if ok else "有分歧 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
