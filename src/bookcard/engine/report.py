"""CLI 输出的中文摘要。

摘要函数放在引擎这边，``cli.py`` 只做注册和分发。
"""

from __future__ import annotations


def summarize_text(summary: dict) -> str:
    """``bookcard text`` 的摘要。"""
    kinds = summary["kinds"]
    lines = [
        f"书：{summary['fp12']}（{summary['page_count']} 页，{summary['language']}）",
        f"输出：{summary['out_dir']}",
        "页面分布："
        f"封面 {kinds['cover']} / 前置 {kinds['front']} / 目录 {kinds['toc']}"
        f" / 正文 {kinds['body']} / 练习 {kinds['exercise']}"
        f" / 附录 {kinds['appendix']} / 空白 {kinds['blank']}",
    ]
    if summary.get("ocr"):
        lines.append(
            f"取字：本地 OCR（--ocr {summary.get('ocr_mode')}，"
            f"探针判定 {summary.get('verdict') or '强制'}），"
            f"{summary.get('ocr_pages', 0)} 页 / {summary.get('ocr_lines', 0)} 行，"
            f"耗时 {summary.get('ocr_seconds', 0.0):.1f} 秒"
        )
    else:
        lines.append(f"取字：PDF 文本层（--ocr {summary.get('ocr_mode', 'auto')}）")
    if summary["toc_range"]:
        toc_range = summary["toc_range"]
        lines.append(
            f"目录页：{toc_range[0]}–{toc_range[-1]}，条目 {summary['toc_entries']} 条"
        )
    else:
        lines.append("目录页：没认出来")
    if summary["printed_offset"] is not None:
        lines.append(
            f"印刷页码：偏移 {summary['printed_offset']}，认出来 {summary['printed_pages']} 页"
        )
    else:
        lines.append("印刷页码：没认出来")
    lines.append(
        f"正文：第 {summary['first_body_page']}–{summary['last_body_page']} 页"
    )
    if summary["watermarks"]:
        lines.append(f"水印：{'；'.join(summary['watermarks'])}")
    return "\n".join(lines)


def summarize_toc(summary: dict) -> str:
    """``bookcard toc`` 的摘要。"""
    lines = [
        f"目录来源：{summary['source']}，条目 {summary['entries']} 条，"
        f"切分粒度第 {summary['level']} 层，切出 {summary['sections']} 节",
        f"正文：第 {summary['first_body_page']}–{summary['last_body_page']} 页",
        f"闸门 1：{summary['gate']}",
        f"输出：{summary['toc_path']}",
    ]
    for reason in summary["reasons"]:
        lines.append(f"  · {reason}")
    return "\n".join(lines)


def summarize_sections(summary: dict) -> str:
    """``bookcard sections`` 的摘要。"""
    lines = [
        f"切出 {summary['sections']} 节（粒度第 {summary['level']} 层），"
        f"覆盖第 {summary['first_body_page']}–{summary['last_body_page']} 页",
        f"输出：{summary['sections_path']}",
        "前 5 节：",
    ]
    for section in summary["sample"]:
        lines.append(
            f"  {section['sid']} {section['title']}"
            f"（第 {section['page_start']}–{section['page_end']} 页）"
        )
    return "\n".join(lines)
