"""圖片附件內嵌引用的測試。

Redmine 只在內文出現 `![](檔名)` 時才把附件畫進內文；沒有這行引用，圖片只會躺在
單子底部的附件清單。這組測試釘住「送出前自動補上引用」這件事。
"""
from __future__ import annotations

import json

import httpx
import pytest

from redmine_mcp.embedding import embed_image_references
from redmine_mcp.server import create_server
from tests.conftest import call_tool as _call
from tests.conftest import json_response

VALID_DESCRIPTION = "測試用內文，長度足以通過內容檢核。"

PNG = {"token": "t1", "filename": "shot.png", "content_type": "image/png"}


def test_內文末尾補上圖片引用():
    result = embed_image_references("問題描述", [PNG])

    assert result.endswith("![](shot.png)")


def test_有佐證章節時插在該章節末尾():
    text = "## 佐證\n\n登入失敗的畫面\n\n## 其他\n\n後面還有內容"

    result = embed_image_references(text, [PNG])

    lines = result.splitlines()
    evidence = lines.index("## 佐證")
    other = lines.index("## 其他")
    embedded = lines.index("![](shot.png)")
    assert evidence < embedded < other, f"引用沒有落在佐證章節內：\n{result}"


def test_插入後與下個章節之間留空行():
    # 緊貼著下一個 `##` 仍解析得出標題，但與骨架其餘部分的排版不一致。
    text = "## 佐證\n\n畫面\n\n## 影響與急迫度\n\n整個部門"

    result = embed_image_references(text, [PNG])

    assert "![](shot.png)\n\n## 影響與急迫度" in result


def test_沒有佐證章節時退回文末():
    text = "## 問題描述\n\n登入失敗\n\n## 重現步驟\n\n1. 點登入"

    result = embed_image_references(text, [PNG])

    assert result.splitlines()[-1] == "![](shot.png)"


@pytest.mark.parametrize(
    "text",
    [
        "已經寫好了 ![](shot.png)",
        "角括號寫法 ![](<shot.png>)",
        "textile 殘留 !shot.png!",
        "純提到檔名 shot.png 也算引用",
    ],
)
def test_內文已提到檔名就不重複插入(text: str):
    # 模型自己寫對了位置時，工具再補一次會變成同一張圖出現兩遍。
    assert embed_image_references(text, [PNG]) == text


def test_非圖片附件不內嵌():
    pdf = {"token": "t2", "filename": "spec.pdf", "content_type": "application/pdf"}

    assert embed_image_references("內文", [pdf]) == "內文"


def test_沒帶檔名的附件不內嵌():
    # uploads 未帶 filename 時，落地檔名由上傳當下決定，這裡查不到，
    # 猜一個引用只會產生一行顯示不出來的壞語法。
    assert embed_image_references("內文", [{"token": "t3"}]) == "內文"


def test_缺content_type時以副檔名判斷():
    assert embed_image_references("內文", [{"token": "t4", "filename": "a.jpg"}]).endswith(
        "![](a.jpg)"
    )


def test_檔名含空白改用角括號包住():
    # `![](my shot.png)` 在 CommonMark 會被解析成「網址 my」加標題，圖片顯示不出來。
    named = {"token": "t5", "filename": "my shot.png", "content_type": "image/png"}

    assert embed_image_references("內文", [named]).endswith("![](<my shot.png>)")


def test_多張圖片各補一行且順序不變():
    second = {"token": "t6", "filename": "second.png", "content_type": "image/png"}

    result = embed_image_references("內文", [PNG, second])

    assert result.splitlines()[-2:] == ["![](shot.png)", "![](second.png)"]


def test_同一份uploads出現同名檔案只補一行():
    # 模型可能在 uploads 帶兩筆同名附件；補兩行相同的 ![](檔名) 會讓同一張圖顯示兩遍。
    result = embed_image_references("內文", [PNG, dict(PNG, token="t2")])

    assert result.splitlines().count("![](shot.png)") == 1


def test_圍欄區塊裡的檔名不算引用():
    # 錯誤訊息原文常會帶到檔名。那是資料不是引用，圖片仍然要補。
    text = "## 佐證\n\n```\nfailed to load shot.png\n```"

    assert embed_image_references(text, [PNG]).endswith("![](shot.png)")


def test_行內程式碼裡的檔名不算引用():
    # 概述常以 `檔名` 的行內 code 提到附件（「附件清單看得到 `shot.png`」）。
    # 那是在描述檔名這筆資料，不是圖片引用，圖片仍然要補。
    text = """## 佐證

附件清單應該看得到 `shot.png`。"""

    assert embed_image_references(text, [PNG]).endswith("![](shot.png)")


def test_行內程式碼與真正的引用並存時不重複補():
    # 同時出現行內 code 與正確的引用：後者才算引用，不可再補一行。
    text = """## 佐證

![](shot.png)

檔名是 `shot.png`。"""

    assert embed_image_references(text, [PNG]) == text


def test_空白內文不插入():
    # description 為 None／空字串時另有必填檢核擋著，這裡不製造內容。
    assert embed_image_references("", [PNG]) == ""
    assert embed_image_references(None, [PNG]) is None


async def test_create_issue_送出的內文帶圖片引用(registry):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return json_response(201, {"issue": {"id": 7, "subject": "含圖片"}})

    sites = registry(handler)
    mcp = create_server(sites)
    await _call(
        mcp,
        "create_issue",
        {
            "project_id": "mcp-test",
            "subject": "含圖片",
            "due_date": "2026-09-30",
            "description": f"{VALID_DESCRIPTION}\n\n## 佐證\n\n登入失敗的畫面",
            "uploads": [PNG],
        },
    )
    await sites.aclose()

    assert "![](shot.png)" in seen["body"]["issue"]["description"]


async def test_add_issue_note_送出的註記帶圖片引用(registry):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(204)

    sites = registry(handler)
    mcp = create_server(sites)
    await _call(mcp, "add_issue_note", {"issue_id": 7, "notes": "補圖", "uploads": [PNG]})
    await sites.aclose()

    assert "![](shot.png)" in seen["body"]["issue"]["notes"]


async def test_update_issue_有帶內文時補上引用(registry):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            seen["body"] = json.loads(request.content)
            return httpx.Response(204)
        return json_response(200, {"issue": {"id": 7, "description": "x"}})

    sites = registry(handler)
    mcp = create_server(sites)
    await _call(
        mcp,
        "update_issue",
        {"issue_id": 7, "description": VALID_DESCRIPTION, "uploads": [PNG]},
    )
    await sites.aclose()

    assert "![](shot.png)" in seen["body"]["issue"]["description"]


async def test_update_issue_沒帶內文時不動既有內文(registry):
    # 只補附件的更新沒有內文可改；為了內嵌而去改寫既有 description，
    # 等於用一次讀回來的舊值覆寫別人可能剛改過的內容。
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(204)

    sites = registry(handler)
    mcp = create_server(sites)
    await _call(mcp, "update_issue", {"issue_id": 7, "uploads": [PNG]})
    await sites.aclose()

    assert "description" not in seen["body"]["issue"]


async def test_update_issue_同時有註記時圖片跟著註記走(registry):
    # 附件是隨這次註記掛上去的，圖片應該出現在註記裡，而不是被塞進概述。
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            seen["body"] = json.loads(request.content)
            return httpx.Response(204)
        return json_response(200, {"issue": {"id": 7}})

    sites = registry(handler)
    mcp = create_server(sites)
    await _call(
        mcp,
        "update_issue",
        {
            "issue_id": 7,
            "description": VALID_DESCRIPTION,
            "notes": "補上畫面",
            "uploads": [PNG],
        },
    )
    await sites.aclose()

    issue = seen["body"]["issue"]
    assert "![](shot.png)" in issue["notes"]
    assert "![](shot.png)" not in issue["description"]
