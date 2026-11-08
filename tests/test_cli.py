"""bookcard CLI 的测试。"""

import json

import pytest

from bookcard import __version__
from bookcard.cli import main


def test_version_flag_prints_version(capsys):
    """``--version`` 打印版本号并以 0 退出。"""
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_probe_prints_chinese_summary(tmp_path, capsys):
    """``probe`` 打中文摘要，不再是占位的 not implemented。"""
    book = tmp_path / "book.pdf"
    book.write_bytes(b"%PDF-1.7\n")

    code = main(["probe", str(book)])

    # 这不是有效 PDF，报告照样出来，退出码 3（坏文件）。
    assert code == 3
    out = capsys.readouterr().out
    assert "not implemented" not in out
    assert "书：book.pdf" in out


def test_probe_requires_path():
    """``probe`` 缺参数时报错退出（退出码 2）。"""
    with pytest.raises(SystemExit) as excinfo:
        main(["probe"])

    assert excinfo.value.code == 2


def test_probe_json_output_is_parseable(tmp_path, capsys):
    """``--json`` 的 stdout 是合法 JSON。"""
    book = tmp_path / "book.pdf"
    book.write_bytes(b"%PDF-1.7\n")

    main(["probe", str(book), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["probe_version"] == 1
    assert payload["verdict"] == "broken"


def test_no_command_is_an_error():
    """不给子命令时 argparse 直接报错退出。"""
    with pytest.raises(SystemExit) as excinfo:
        main([])

    assert excinfo.value.code == 2
