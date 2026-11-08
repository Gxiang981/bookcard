"""提示词跟着包走的测试（契约 §1 / ``docs/mcp-v1.md`` §1）。

- ``prompts/extract_v2.md`` 挪进包内 ``bookcard/prompts/``，版本号仍是 ``ex-v1``；
- ``bookcard.judge`` 的 ``SYSTEM`` 常量逐字搬进 ``bookcard/prompts/judge_v1.md``，
  读出来的文字和**原来的常量逐字相等**（下面 :data:`ORIGINAL_SYSTEM` 就是搬走
  之前的那份原文，抄在这里做对照）。
"""

from __future__ import annotations

from pathlib import Path

from bookcard import extract, judge, prompts

#: 搬家之前 ``bookcard/judge.py`` 里的 ``SYSTEM`` 常量原文（逐字抄）。
ORIGINAL_SYSTEM = """你是一名为学习卡片系统做质检的编辑。读者是正在学这本书的学生。

给你若干张卡，每张卡有：
- front：卡片正面（书里的一个词或短语）；
- back：卡片背面（解释，中文）；
- evidence：解释的依据句（应该一字不差地来自书页原文）；
- page：书页原文。

请对每张卡按下面四个维度判定，各给一个判定值和一句简短理由（中文，20 字以内）。

1. kw（关键词）: accept / boundary / reject
   accept = 原文里一字不差出现、有独立含义、普通读者不一定已掌握、值得单独记；
   boundary = 是关键词，但边界多了或少了字（比如把修饰语带进来了、把固定说法拆开了）；
   reject = 不该标：功能词、泛泛常用词、教材版式词（第一章、练习、图 3-2 等）、
     纯数字变量、乱码，或者只是普通词组不值得记。

2. evidence（依据）: ok / missing / wrong
   ok = 依据句确实在书页原文里，而且确实在讲这个词；
   missing = 原文里找不到这句依据；
   wrong = 依据句虽然可能是原文，但讲的其实不是这个词。

3. expl（解释）: 2 / 1 / 0
   2 = 准确，被依据或常识支持，15 到 60 字，不是照抄依据原句；
   1 = 大体对，但含糊、啰嗦，或字数略微超出；
   0 = 有错、答非所问、与原文矛盾，或者只是把依据原句复述一遍。

4. fatal（严重错误）: true / false
   true = 解释有事实错误，或者 front 不是原文里的词；
   false = 没有上述问题。

另外，如果这一组里明显漏掉了值得学的词（原文里很重要但没被列出来的术语、
人名、地名、作品名），在 missed 里列出来（最多 3 个），写成 surface + 页码；
没有就空数组。

只输出一个 JSON 对象，不要说明、不要 Markdown 代码块：
{"verdicts": [{"index": 0, "kw": "accept", "evidence": "ok",
               "expl": 2, "fatal": false, "note": "简短理由"}],
 "missed": [{"surface": "词", "page": 12}]}"""

# --------------------------------------------------------------------------- 抽词提示词


def test_抽词提示词在包内():
    """提示词跟着包走：路径在 ``bookcard/prompts/`` 下，不在仓库根。"""
    path = extract.default_prompt_path()
    assert path.name == "extract_v2.md"
    assert path.parent.name == "prompts"
    assert path.parent.parent.name == "bookcard"


def test_抽词提示词版本号没变():
    """版本号跟着文件名走，还是 ``ex-v1``（moved 不改版本）。"""
    assert extract.version_from_filename("extract_v2.md") == "ex-v2"
    prompt = extract.parse_prompt(extract.default_prompt_path())
    assert prompt.version == "ex-v2"


def test_抽词提示词正文没被改过():
    """提示词正文一个字都不动（改动要升版本号）。"""
    raw = prompts.read_prompt(prompts.EXTRACT_PROMPT_NAME)
    assert "ex-v2" in raw
    assert "===USER===" in raw
    assert set(extract.PLACEHOLDER_RE.findall(raw)) <= {
        "book_title",
        "subject",
        "section_title",
        "page",
        "text",
    }


# --------------------------------------------------------------------------- 审卡提示词


def test_审卡提示词和原常量逐字相等():
    """``judge.SYSTEM`` 和搬家之前的常量**逐字相等**。"""
    assert judge.SYSTEM == ORIGINAL_SYSTEM


def test_审卡提示词文件内容就是SYSTEM():
    """包内的 ``judge_v1.md`` 就是 ``SYSTEM`` 的正文（末尾多一个换行）。"""
    raw = prompts.read_prompt(prompts.JUDGE_PROMPT_NAME)
    assert raw == ORIGINAL_SYSTEM + "\n"


def test_审卡提示词在包内():
    path = prompts.prompt_path(prompts.JUDGE_PROMPT_NAME)
    assert path.name == "judge_v1.md"
    assert path.parent.name == "prompts"
    assert path.parent.parent.name == "bookcard"


# --------------------------------------------------------------------------- 打包


def test_提示词按package_data打包():
    """``pyproject.toml`` 里要把 ``bookcard/prompts/*.md`` 声明成 package data。"""
    root = Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert '"bookcard.prompts" = ["*.md"]' in text
