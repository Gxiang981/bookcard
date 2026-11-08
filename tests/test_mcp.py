"""``python -m bookcard.mcp`` / ``bookcard mcp`` 的测试：stdio JSON-RPC。

覆盖验收要求的那几条：

- 子进程起服务，走 ``initialize`` → ``tools/list`` → ``book_add`` → ``book_status``；
- 再走一个 ``extract_next`` / ``extract_submit`` 来回（模型回答用夹具里写死的 JSON）；
- 协议边界：未知方法、参数缺失、通知（没有 id）不回包。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentflow_fixtures import EXTRACT_ANSWERS, make_pdf, write_toc
from pipeline_fixtures import assert_scene_chosen_by_consistent

#: 交卷时用的 agent 标识（格式：agent:<harness>:<模型名>[:<提示词版本>]）。
EXTRACTOR = "agent:test-harness:test-model:ex-v1"
REVIEWER = "agent:test-harness:test-model"

#: 工具清单（契约 §3）。
TOOL_NAMES = [
    "book_add",
    "book_list",
    "book_status",
    "toc_review",
    "toc_gate",
    "sections_build",
    "extract_next",
    "extract_submit",
    "cards_build",
    "pack",
    "browse_open",
]


# --------------------------------------------------------------------------- 客户端


class McpClient:
    """子进程里的 MCP 客户端（写一行、读一行）。"""

    def __init__(self, home: Path) -> None:
        env = dict(os.environ)
        env["BOOKCARD_HOME"] = str(home)
        env["PYTHONUNBUFFERED"] = "1"
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "bookcard.mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            encoding="utf-8",
        )
        self._next_id = 0

    # -- 底层 ------------------------------------------------------------

    def send(self, message: dict) -> None:
        """发一条消息（不读回包）。"""
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def read(self) -> dict:
        """读一条消息（通知也会读到）。"""
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        assert line, "服务没回消息就退了"
        return json.loads(line)

    def read_response(self, msg_id: int) -> dict:
        """读回给某个 id 的回包（跳过通知）。"""
        while True:
            message = self.read()
            if message.get("id") == msg_id:
                return message

    def request(self, method: str, params: dict | None = None) -> dict:
        """发一条请求并读回包。"""
        self._next_id += 1
        msg_id = self._next_id
        self.send(
            {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
        )
        return self.read_response(msg_id)

    def call(self, name: str, arguments: dict | None = None) -> dict:
        """调一个工具，返回 ``tools/call`` 的 result。"""
        response = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        assert "result" in response, response
        return response["result"]

    def text(self, result: dict) -> str:
        """工具结果里的文字。"""
        return "\n".join(block.get("text", "") for block in result.get("content", []))

    def close(self) -> None:
        """关掉子进程。"""
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - 关不掉就硬杀
            self.proc.kill()


@pytest.fixture
def client(tmp_path):
    """一个连着子进程的客户端（``BOOKCARD_HOME`` 指向临时目录）。"""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    instance = McpClient(home)
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def book_pdf(tmp_path) -> Path:
    """一本十页的小 PDF。"""
    pdf = tmp_path / "book.pdf"
    make_pdf(pdf)
    return pdf


@pytest.fixture
def book_pdf_with_toc(tmp_path) -> Path:
    """一本十页的小 PDF（带读书签，能从书签拿到目录）。"""
    pdf = tmp_path / "book_toc.pdf"
    make_pdf(pdf, bookmarks=True)
    return pdf


# --------------------------------------------------------------------------- 协议


def test_initialize回协议版本和能力(client):
    """``initialize`` 回客户端要的协议版本、能力和服务器信息。"""
    response = client.request(
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}},
    )
    result = response["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert result["serverInfo"]["name"] == "bookcard"
    assert result["instructions"]


def test_initialize回旧版协议版本(client):
    """客户端要 2025-03-26 就回 2025-03-26。"""
    response = client.request("initialize", {"protocolVersion": "2025-03-26"})
    assert response["result"]["protocolVersion"] == "2025-03-26"


def test_initialize不认识版本就回默认(client):
    """客户端要一个不认识的版本，就回当前支持的最新版本。"""
    response = client.request("initialize", {"protocolVersion": "1999-01-01"})
    assert response["result"]["protocolVersion"] == "2025-06-18"


def test_ping回空对象(client):
    """``ping`` 回空对象。"""
    assert client.request("ping")["result"] == {}


def test_通知不回包(client):
    """没有 id 的通知**不回包**（后面那条请求的回包才是唯一的一条）。"""
    client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    client.send({"jsonrpc": "2.0", "id": 77, "method": "ping"})
    message = client.read()
    assert message["id"] == 77
    assert "result" in message


def test_未知方法回方法没找到(client):
    """未知方法回 JSON-RPC 错误 ``-32601``。"""
    response = client.request("does/not/exist")
    assert response["error"]["code"] == -32601
    assert "does/not/exist" in response["error"]["message"]


def test_不是合法JSON回解析错误(client):
    """一行不是合法 JSON：回 ``-32700``，服务继续活着。"""
    assert client.proc.stdin is not None
    client.proc.stdin.write("这不是 JSON\n")
    client.proc.stdin.flush()
    message = client.read()
    assert message["error"]["code"] == -32700
    # 服务还活着。
    assert client.request("ping")["result"] == {}


def test_未知工具回参数错误(client):
    """调一个不存在的工具：协议层错误 ``-32602``。"""
    response = client.request("tools/call", {"name": "没有这个工具", "arguments": {}})
    assert response["error"]["code"] == -32602


# --------------------------------------------------------------------------- 工具表


def test_tools_list给出全部工具(client):
    """``tools/list`` 给出契约 §3 的工具表，每个都带 JSON Schema。"""
    result = client.request("tools/list")["result"]
    names = [tool["name"] for tool in result["tools"]]
    assert names == TOOL_NAMES
    for tool in result["tools"]:
        assert tool["description"]
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert isinstance(schema["properties"], dict)
        assert isinstance(schema["required"], list)


def test_book_status缺参数时中文报错(client):
    """参数缺失：``isError: true``，错误文字用中文并写明下一步。"""
    result = client.call("book_status", {})
    assert result["isError"] is True
    text = client.text(result)
    assert "book" in text
    assert "下一步" in text


def test_book参数不是fp12时拒掉(client):
    """``book`` 必须是 12 位十六进制——不许拿路径穿越。"""
    result = client.call("book_status", {"book": "../../etc"})
    assert result["isError"] is True
    assert "fp12" in client.text(result)


# ------------------------------------------------------- 工具层单测（不起子进程）


def test_pack工具的scene是可选的():
    """``pack`` 的 ``scene`` 不再必填，默认 ``image``；``scene_chosen_by`` 可选。"""
    from bookcard.mcp.tools import TOOLS

    pack_tool = next(tool for tool in TOOLS if tool["name"] == "pack")
    schema = pack_tool["inputSchema"]

    assert "scene" not in schema["required"]
    assert schema["properties"]["scene"]["enum"] == ["text", "image"]
    assert schema["properties"]["scene"]["default"] == "image"
    assert schema["properties"]["scene_chosen_by"]["enum"] == ["user", "default"]
    assert schema["properties"]["scene_chosen_by"]["default"] == "default"
    assert "scene_chosen_by" not in schema["required"]


def _ready_book_dir(home: Path, pdf: Path) -> str:
    """在本机把一本书走完流水线，返回 fp12（模型回答用夹具里写死的）。"""
    from bookcard import agentflow

    added = agentflow.book_add(pdf, home=home)
    assert added["ok"], added
    book_dir = Path(added["book_dir"])
    write_toc(book_dir)
    assert agentflow.toc_gate(book_dir, approve=True, approved_by=REVIEWER)["ok"]
    assert agentflow.sections_build(book_dir)["ok"]

    nxt = agentflow.extract_next(book_dir, max_segments=4)
    while nxt["segments"]:
        for segment in nxt["segments"]:
            agentflow.extract_submit(
                book_dir,
                segment["seg_id"],
                EXTRACT_ANSWERS[segment["page"]],
                EXTRACTOR,
            )
        nxt = agentflow.extract_next(book_dir, max_segments=4)

    nxt = agentflow.extract_next(book_dir, max_segments=4)
    while nxt["segments"]:
        for segment in nxt["segments"]:
            agentflow.extract_submit(
                book_dir,
                segment["seg_id"],
                EXTRACT_ANSWERS[segment["page"]],
                EXTRACTOR,
            )
        nxt = agentflow.extract_next(book_dir, max_segments=4)

    assert agentflow.cards_build(book_dir)["ok"]
    return str(added["fp12"])


def test_pack工具不带scene能调通(tmp_path, book_pdf):
    """工具层直接调 ``pack``（不带 scene）⇒ 走默认图片场景，不报错。"""
    from bookcard.mcp.tools import ToolBox

    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    fp12 = _ready_book_dir(home, book_pdf)

    toolbox = ToolBox(home=home)
    result = toolbox.call("pack", {"book": fp12, "edition": "private"})

    assert result.get("isError") is not True, result
    payload = json.loads("\n".join(block.get("text", "") for block in result["content"]))
    assert payload["ok"] is True, payload
    assert payload["scene_mode"] == "image"
    assert payload["scene_chosen_by"] == "default"

    manifest = assert_scene_chosen_by_consistent(home / "books" / fp12, "default")
    assert manifest["scene_mode"] == "image"


def test_pack工具带用户选定的场景(tmp_path, book_pdf):
    """工具层传 ``scene=text`` + ``scene_chosen_by=user`` ⇒ 两处都记 user。"""
    from bookcard.mcp.tools import ToolBox

    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    fp12 = _ready_book_dir(home, book_pdf)

    toolbox = ToolBox(home=home)
    result = toolbox.call(
        "pack",
        {"book": fp12, "edition": "private", "scene": "text", "scene_chosen_by": "user"},
    )

    assert result.get("isError") is not True, result
    payload = json.loads("\n".join(block.get("text", "") for block in result["content"]))
    assert payload["ok"] is True, payload
    assert payload["scene_mode"] == "text"
    assert payload["scene_chosen_by"] == "user"

    manifest = assert_scene_chosen_by_consistent(home / "books" / fp12, "user")
    assert manifest["scene_mode"] == "text"


def test_pack工具的scene_chosen_by乱值被拒(tmp_path, book_pdf):
    """工具层传 ``scene_chosen_by`` 乱值（``"cli"`` / ``"hologram"``）⇒ isError。

    schema 枚举只收 ``user`` / ``default``；乱值就算绕过 schema 也得被
    ``agentflow.pack`` 挡下（契约 §7.1）。
    """
    from bookcard.mcp.tools import ToolBox

    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    fp12 = _ready_book_dir(home, book_pdf)

    toolbox = ToolBox(home=home)
    for bad in ("cli", "hologram"):
        result = toolbox.call(
            "pack",
            {
                "book": fp12,
                "edition": "private",
                "scene": "text",
                "scene_chosen_by": bad,
            },
        )
        assert result.get("isError") is True, (bad, result)
        text = "\n".join(block.get("text", "") for block in result["content"])
        assert "scene_chosen_by" in text, (bad, text)


# --------------------------------------------------------------------------- 全流程


def test_从加书走到交卷(client, book_pdf, tmp_path):
    """子进程里走一遍：initialize → tools/list → book_add → book_status → 出题交卷。"""
    client.request("initialize", {"protocolVersion": "2025-06-18"})

    # ---- book_add ----
    added = client.call("book_add", {"pdf_path": str(book_pdf)})
    assert added["isError"] is False
    payload = json.loads(client.text(added))
    assert payload["ok"] is True
    fp12 = payload["fp12"]
    assert len(fp12) == 12
    assert payload["page_count"] == 10
    assert payload["scene_suggested"] == "image"
    assert payload["text_eligible"] is True
    assert payload["scene_reasons"]

    # ---- book_status ----
    state = json.loads(client.text(client.call("book_status", {"book": fp12})))
    assert state["ok"] is True
    assert state["step"] == "toc_review"
    assert state["gates"] == {"toc": "pending"}
    assert state["next"]

    # ---- book_list 能列出这本书 ----
    listing = json.loads(client.text(client.call("book_list", {})))
    assert [book["fp12"] for book in listing["books"]] == [fp12]
    assert listing["books"][0]["next"]

    # ---- 目录：夹具没有书签，直接写 toc.json 再过闸门 1 ----
    book_dir = tmp_path / "home" / "books" / fp12
    write_toc(book_dir)
    gated = client.call(
        "toc_gate",
        {"book": fp12, "approve": True, "approved_by": REVIEWER, "notes": "逐条核对过"},
    )
    assert gated["isError"] is False
    assert json.loads(client.text(gated))["gate"]["status"] == "approved"

    built = client.call("sections_build", {"book": fp12})
    assert built["isError"] is False
    assert json.loads(client.text(built))["sections"] == 3

    # ---- 出题 → 交卷 ----
    nxt = json.loads(client.text(client.call("extract_next", {"book": fp12})))
    assert nxt["ok"] is True
    assert nxt["prompt_version"] == "ex-v2"
    assert nxt["system"]
    assert nxt["segments"]

    segment = nxt["segments"][0]
    submitted = client.call(
        "extract_submit",
        {
            "book": fp12,
            "seg_id": segment["seg_id"],
            "output": EXTRACT_ANSWERS[segment["page"]],
            "extractor": EXTRACTOR,
        },
    )
    assert submitted["isError"] is False
    result = json.loads(client.text(submitted))
    assert result["ok"] is True
    assert result["items"] == 1
    assert result["dropped"]["not_in_text"] == 0


def _make_book(home: Path, fp12: str, title: str, *, legacy: bool = False) -> Path:
    """在 home 下造一本书的工作目录。

    只写 ``book.json``——``book_list`` / ``book_status`` 认书靠的就是它。
    ``legacy=True`` 造旧位置（home 根下），否则造新位置（``home/books/`` 下）。
    """
    book_dir = (home / fp12) if legacy else (home / "books" / fp12)
    book_dir.mkdir(parents=True, exist_ok=True)
    (book_dir / "book.json").write_text(
        json.dumps({"fp12": fp12, "title": title}, ensure_ascii=False),
        encoding="utf-8",
    )
    return book_dir


def test_book_list同时列新旧两处(client, tmp_path):
    """旧位置（home 根下）和新位置（home/books/ 下）的书都列出来，location 分得清。"""
    client.request("initialize", {"protocolVersion": "2025-06-18"})
    home = tmp_path / "home"
    _make_book(home, "aaaaaaaaaaaa", "旧书", legacy=True)
    _make_book(home, "bbbbbbbbbbbb", "新书")

    listing = json.loads(client.text(client.call("book_list", {})))
    books = {book["fp12"]: book for book in listing["books"]}
    assert set(books) == {"aaaaaaaaaaaa", "bbbbbbbbbbbb"}
    assert books["aaaaaaaaaaaa"]["location"] == "legacy"
    assert books["aaaaaaaaaaaa"]["title"] == "旧书"
    assert books["bbbbbbbbbbbb"]["location"] == "books"
    assert books["bbbbbbbbbbbb"]["title"] == "新书"
    assert listing["count"] == 2


def test_book_status能按fp12找到旧位置的书(client, tmp_path):
    """旧位置的书，``book_status`` 按 fp12 照样找得到。"""
    client.request("initialize", {"protocolVersion": "2025-06-18"})
    home = tmp_path / "home"
    _make_book(home, "cccccccccccc", "旧书", legacy=True)

    state = json.loads(client.text(client.call("book_status", {"book": "cccccccccccc"})))
    assert state["ok"] is True
    assert state["fp12"] == "cccccccccccc"
    assert state["title"] == "旧书"


def test_两处都有时取新位置(client, tmp_path):
    """同一个 fp12 新旧两处都有：``book_list`` 只列新位置那个，``book_status`` 也走新位置。"""
    client.request("initialize", {"protocolVersion": "2025-06-18"})
    home = tmp_path / "home"
    _make_book(home, "dddddddddddd", "旧的那本", legacy=True)
    _make_book(home, "dddddddddddd", "新的那本")

    listing = json.loads(client.text(client.call("book_list", {})))
    assert [book["fp12"] for book in listing["books"]] == ["dddddddddddd"]
    assert listing["books"][0]["location"] == "books"
    assert listing["books"][0]["title"] == "新的那本"

    state = json.loads(client.text(client.call("book_status", {"book": "dddddddddddd"})))
    assert state["title"] == "新的那本"


def test_book_add只往新位置写(client, book_pdf, tmp_path):
    """加书落在 ``<home>/books/<fp12>/``，不写旧位置。"""
    client.request("initialize", {"protocolVersion": "2025-06-18"})
    added = json.loads(client.text(client.call("book_add", {"pdf_path": str(book_pdf)})))
    fp12 = added["fp12"]

    home = tmp_path / "home"
    assert (home / "books" / fp12 / "book.json").is_file()
    assert not (home / fp12).exists()

    listing = json.loads(client.text(client.call("book_list", {})))
    assert [book["location"] for book in listing["books"]] == ["books"]


def test_交卷格式不对时让重试(client, book_pdf, tmp_path):
    """agent 的回包解析不出来：``isError`` 且文字里说清要重答。"""
    client.request("initialize", {"protocolVersion": "2025-06-18"})
    added = json.loads(client.text(client.call("book_add", {"pdf_path": str(book_pdf)})))
    fp12 = added["fp12"]
    book_dir = tmp_path / "home" / "books" / fp12
    write_toc(book_dir)
    client.call("toc_gate", {"book": fp12, "approve": True, "approved_by": REVIEWER})
    client.call("sections_build", {"book": fp12})

    nxt = json.loads(client.text(client.call("extract_next", {"book": fp12})))
    segment = nxt["segments"][0]
    result = client.call(
        "extract_submit",
        {
            "book": fp12,
            "seg_id": segment["seg_id"],
            "output": "这不是 JSON",
            "extractor": EXTRACTOR,
        },
    )
    assert result["isError"] is True
    text = client.text(result)
    assert "重答" in text
    assert "下一步" in text


def test_闸门没过时工具直接拒绝(client, book_pdf):
    """闸门 1 没过：``sections_build`` 被拒，错误文字说清下一步。"""
    client.request("initialize", {"protocolVersion": "2025-06-18"})
    added = json.loads(client.text(client.call("book_add", {"pdf_path": str(book_pdf)})))
    fp12 = added["fp12"]

    result = client.call("sections_build", {"book": fp12})
    assert result["isError"] is True
    text = client.text(result)
    assert "闸门 1" in text
    assert "toc_gate" in text


def test_跳过toc_gate不许切章节(client, book_pdf_with_toc):
    """目录只到 ``pending``：agent 跳过 ``toc_gate`` 调 ``sections_build`` 要报错。"""
    client.request("initialize", {"protocolVersion": "2025-06-18"})
    added = json.loads(client.text(client.call("book_add", {"pdf_path": str(book_pdf_with_toc)})))
    fp12 = added["fp12"]

    # 目录摊出来了，但还没人拍板。
    review = json.loads(client.text(client.call("toc_review", {"book": fp12})))
    assert review["gate"]["status"] == "pending"
    assert review["gate"]["approved_by"] == ""

    result = client.call("sections_build", {"book": fp12})
    assert result["isError"] is True
    text = client.text(result)
    assert "闸门 1" in text
    assert "toc_gate" in text


def test_走到打包再打开浏览页(client, book_pdf, tmp_path):
    """一路走到 ``pack`` 再 ``browse_open``：包出来了，浏览页也起了。"""
    client.request("initialize", {"protocolVersion": "2025-06-18"})
    added = json.loads(client.text(client.call("book_add", {"pdf_path": str(book_pdf)})))
    fp12 = added["fp12"]
    book_dir = tmp_path / "home" / "books" / fp12
    write_toc(book_dir)
    client.call("toc_gate", {"book": fp12, "approve": True, "approved_by": REVIEWER})
    client.call("sections_build", {"book": fp12})

    # 试抽：出题 → 交卷（夹具里模型回答按页给）。
    while True:
        nxt = json.loads(client.text(client.call("extract_next", {"book": fp12})))
        if not nxt["segments"]:
            break
        for segment in nxt["segments"]:
            client.call(
                "extract_submit",
                {
                    "book": fp12,
                    "seg_id": segment["seg_id"],
                    "output": EXTRACT_ANSWERS[segment["page"]],
                    "extractor": EXTRACTOR,
                },
            )

    # 全量。
    while True:
        nxt = json.loads(client.text(client.call("extract_next", {"book": fp12})))
        if not nxt["segments"]:
            break
        for segment in nxt["segments"]:
            client.call(
                "extract_submit",
                {
                    "book": fp12,
                    "seg_id": segment["seg_id"],
                    "output": EXTRACT_ANSWERS[segment["page"]],
                    "extractor": EXTRACTOR,
                },
            )

    built = json.loads(client.text(client.call("cards_build", {"book": fp12})))
    assert built["ok"] is True

    # 内部分享版不给场景也能调通——默认走书页图（the maintainer 2026-11-06）。
    packed = json.loads(client.text(client.call("pack", {"book": fp12, "edition": "private"})))
    assert packed["ok"] is True
    assert packed["scene_mode"] == "image"
    assert packed["scene_chosen_by"] == "default"
    assert Path(packed["pack"]).exists()

    # 用户主动要文本块时才传 scene=text、scene_chosen_by=user。
    packed_text = json.loads(
        client.text(
            client.call(
                "pack",
                {
                    "book": fp12,
                    "edition": "private",
                    "scene": "text",
                    "scene_chosen_by": "user",
                },
            )
        )
    )
    assert packed_text["ok"] is True
    assert packed_text["scene_mode"] == "text"
    assert packed_text["scene_chosen_by"] == "user"

    # 浏览页：起得来，重复调用复用同一个服务。
    opened = json.loads(client.text(client.call("browse_open", {"book": fp12})))
    assert opened["url"].startswith("http://127.0.0.1:")
    assert opened["reused"] is False
    again = json.loads(client.text(client.call("browse_open", {"book": fp12})))
    assert again["url"] == opened["url"]
    assert again["reused"] is True

    import urllib.request

    with urllib.request.urlopen(opened["url"], timeout=10) as response:
        assert response.status == 200


# --------------------------------------------------------------------------- 进度通知


def _call_collecting(client: McpClient, params: dict) -> tuple[dict, list[dict]]:
    """发一条 ``tools/call``，返回（回包，回包之前收到的通知列表）。"""
    client._next_id += 1
    msg_id = client._next_id
    client.send({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call", "params": params})
    notices: list[dict] = []
    while True:
        message = client.read()
        if message.get("id") == msg_id:
            return message, notices
        notices.append(message)


def test_progressToken在meta里就发进度(client, book_pdf):
    """``_meta.progressToken`` 给了就发 ``notifications/progress``，都在回包前且不递减。"""
    client.request("initialize", {"protocolVersion": "2025-06-18"})
    response, notices = _call_collecting(
        client,
        {
            "name": "book_add",
            "arguments": {"pdf_path": str(book_pdf)},
            "_meta": {"progressToken": "tok-1"},
        },
    )
    assert "result" in response, response

    progress = [
        n["params"]
        for n in notices
        if n.get("method") == "notifications/progress"
    ]
    assert progress, "一条进度通知都没有"
    assert all(p["progressToken"] == "tok-1" for p in progress)
    values = [p["progress"] for p in progress]
    assert values == sorted(values)


def test_没给progressToken就不发进度(client, book_pdf):
    """不带 ``_meta`` 的 ``tools/call``：一条进度通知都不发。"""
    client.request("initialize", {"protocolVersion": "2025-06-18"})
    response, notices = _call_collecting(
        client, {"name": "book_add", "arguments": {"pdf_path": str(book_pdf)}}
    )
    assert "result" in response, response
    assert not [n for n in notices if n.get("method") == "notifications/progress"]


def test_客户端发的进度通知不粘到后面的请求(client, book_pdf):
    """客户端发的 ``notifications/progress`` 是通知：不回包，也不当 token 存起来。"""
    client.request("initialize", {"protocolVersion": "2025-06-18"})
    client.send(
        {
            "jsonrpc": "2.0",
            "method": "notifications/progress",
            "params": {"progressToken": "x", "progress": 1},
        }
    )
    client.send({"jsonrpc": "2.0", "id": 77, "method": "ping"})
    # 通知不回包：读到的第一条是 ping 的回包。
    assert client.read()["id"] == 77

    response, notices = _call_collecting(
        client, {"name": "book_add", "arguments": {"pdf_path": str(book_pdf)}}
    )
    assert "result" in response, response
    assert not [n for n in notices if n.get("method") == "notifications/progress"]


def test_run_with_progress瞬间完成的任务也补一条():
    """``run_with_progress``：任务瞬间跑完，结束也补一条「完成」通知。"""
    from bookcard.mcp.progress import ProgressReporter, run_with_progress

    sent: list[dict] = []
    reporter = ProgressReporter(sent.append, token="tok-u")
    value = run_with_progress(reporter, lambda: (1.0, 1.0, "done"), lambda: "结果")
    assert value == "结果"
    assert len(sent) >= 1
    assert sent[-1]["params"]["progressToken"] == "tok-u"


# --------------------------------------------------------------------------- 命令行


def test_bookcard_mcp子命令能起来(tmp_path):
    """``bookcard mcp``（控制台脚本）能起来并回包。"""
    env = dict(os.environ)
    env["BOOKCARD_HOME"] = str(tmp_path / "home")
    script = Path(sys.executable).parent / "bookcard"
    if not script.exists():  # pragma: no cover - 没装控制台脚本就跳过
        pytest.skip("控制台脚本 bookcard 不在这个虚拟环境里")

    proc = subprocess.Popen(
        [str(script), "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        encoding="utf-8",
    )
    try:
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        )
        assert proc.stdin is not None
        proc.stdin.write(payload + "\n")
        proc.stdin.flush()
        assert proc.stdout is not None
        message = json.loads(proc.stdout.readline())
        assert message["result"]["serverInfo"]["name"] == "bookcard"
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
        proc.wait(timeout=10)
