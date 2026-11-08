"""最终产物的落地位置（``bookcard.paths``）。"""

from __future__ import annotations

from bookcard.paths import OUTPUT_DIR_ENV, downloads_dir, output_dir, safe_name


def test_downloads_dir_是下载文件夹():
    d = downloads_dir()
    assert d.name == "Downloads"
    assert d.is_absolute()


def test_output_dir_默认就是下载文件夹(monkeypatch):
    monkeypatch.delenv(OUTPUT_DIR_ENV, raising=False)
    assert output_dir() == downloads_dir()


def test_output_dir_可以用环境变量覆盖(monkeypatch, tmp_path):
    monkeypatch.setenv(OUTPUT_DIR_ENV, str(tmp_path))
    assert output_dir() == tmp_path


def test_safe_name_去掉路径分隔符和非法字符():
    assert "/" not in safe_name("a/b")
    assert "\\" not in safe_name("a\\b")
    assert ":" not in safe_name("a:b")
    assert safe_name("", "86ca1c33eb9a") == "86ca1c33eb9a"
    assert safe_name("智能体设计模式", "86ca1c33eb9a") == "智能体设计模式-86ca1c33eb9a"


def test_safe_name_空输入有兜底():
    assert safe_name("", "") == "bookcard"


def test_windows_的_downloads_优先_oneDrive(monkeypatch, tmp_path):
    """Windows 上「下载」被重定向到 OneDrive 时要能找到。"""
    one_drive = tmp_path / "OneDrive" / "Downloads"
    one_drive.mkdir(parents=True)
    expected = str(one_drive)
    monkeypatch.setattr("bookcard.paths._is_windows", lambda: True)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert str(downloads_dir()) == expected


def test_打包后卡包复制到输出目录(monkeypatch, tmp_path):
    """最终产出（卡包）落到「下载」文件夹，名字带书名。"""
    from bookcard.agentflow import _export_pack

    monkeypatch.setenv(OUTPUT_DIR_ENV, str(tmp_path / "dl"))
    src = tmp_path / "86ca1c33eb9a.ttfc.zip"
    src.write_bytes(b"PK")
    out = _export_pack(
        tmp_path,
        {"pack": str(src), "edition": "private"},
        {"title": "智能体设计模式", "fp12": "86ca1c33eb9a"},
    )
    target = tmp_path / "dl" / "智能体设计模式-86ca1c33eb9a.ttfc.zip"
    assert target.exists()
    assert out["pack"] == str(target)
