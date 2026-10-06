"""server 組裝的 smoke test。"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from redmine_mcp.server import build_instructions, create_server

EXPECTED_TOOLS = {
    "list_issues",
    "get_issue",
    "create_issue",
    "update_issue",
    "add_issue_note",
    "list_attachments",
    "download_attachment",
    "upload_attachment",
    "list_projects",
    "create_project",
    "list_trackers",
    "list_issue_statuses",
    "list_priorities",
    "list_custom_fields",
    "get_current_user",
    "list_sites",
}


async def test_註冊十六個工具(registry):
    sites = registry(lambda r: httpx.Response(200))
    mcp = create_server(sites)
    tools = await mcp.list_tools()
    await sites.aclose()

    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    assert len(tools) == 16


async def test_每個工具都有中文說明與可用_schema(registry):
    sites = registry(lambda r: httpx.Response(200))
    mcp = create_server(sites)
    tools = await mcp.list_tools()
    await sites.aclose()

    for tool in tools:
        assert tool.description, f"{tool.name} 缺少說明"
        assert tool.input_schema is not None, f"{tool.name} 無法生成 schema"


async def test_server_指示詞包含不可信輸入警語(registry):
    sites = registry(lambda r: httpx.Response(200))
    mcp = create_server(sites)
    await sites.aclose()

    assert "untrusted" in (mcp.instructions or "")


async def test_指示詞列出所有站台代號(make_registry):
    sites = make_registry(
        {"main": lambda r: httpx.Response(200), "client-b": lambda r: httpx.Response(200)}
    )
    mcp = create_server(sites)
    await sites.aclose()

    instructions = mcp.instructions or ""
    assert "main" in instructions
    assert "client-b" in instructions


async def test_指示詞保留不可信輸入警語(make_registry):
    sites = make_registry({"main": lambda r: httpx.Response(200)})
    mcp = create_server(sites)
    await sites.aclose()

    assert "untrusted" in (mcp.instructions or "")


def test_過渡用的_first_已移除():
    from redmine_mcp.sites import SiteRegistry

    assert not hasattr(SiteRegistry, "first")


async def test_指示詞要求把圖片上傳為附件(make_registry):
    sites = make_registry({"main": lambda r: httpx.Response(200)})
    mcp = create_server(sites)
    await sites.aclose()

    instructions = mcp.instructions or ""
    assert "upload_attachment" in instructions
    assert "uploads" in instructions
    assert "content_base64" in instructions


# --- manifest 成本：instructions 與 tool descriptions 每次對話都會完整送出 ---

#: manifest（instructions + 所有 tool description）的字元上限。
#:
#: 這筆成本每一次對話都要付一遍，且目前協定版下 ttlMs／cacheScope 快取宣告不生效
#: （見 create_server 的說明），無法攤銷。使用者常同時掛多個 MCP server，每個都在
#: 競爭同一份預算。
#:
#: 這是一道棘輪而不是目標值：訂在目前實際值（4,187）之上約 5%，要加新規範時先確認
#: 有沒有可以合併的重複，餘裕用完再談調高。餘裕刻意不留太少——只差十來個字的上限，
#: 下一個人只會直接把數字改大，棘輪就失去意義。
MANIFEST_CHAR_BUDGET = 4400


async def test_manifest_不超過字元預算(registry):
    sites = registry(lambda r: httpx.Response(200))
    mcp = create_server(sites)
    tools = await mcp.list_tools()
    instructions = build_instructions(sites)
    await sites.aclose()

    total = len(instructions) + sum(len(tool.description or "") for tool in tools)
    assert total <= MANIFEST_CHAR_BUDGET, f"manifest 已膨脹到 {total} 字元"


async def test_撰寫規範不在工具說明重述(registry):
    """server instructions 與工具說明都是每次對話必送，同一條規範重複一遍不會讓
    模型讀得更準，只是把成本付兩次。

    六組落點列舉原本是刻意的重複（一份在指示詞、一份在 get_issue_guide 的說明）；
    工具刪除後只剩指示詞一份，另一份移到 SKILL.md。"""
    sites = registry(lambda r: httpx.Response(200))
    mcp = create_server(sites)
    tools = {tool.name: tool.description or "" for tool in await mcp.list_tools()}
    instructions = build_instructions(sites)
    await sites.aclose()

    create = tools["create_issue"]
    for rule in ("不可拆散到自訂欄位", "一次問一題", "不可自行臆測", "不覆寫 description"):
        assert rule in instructions, f"指示詞應保留「{rule}」"
        assert rule not in create, f"create_issue 不應重述「{rule}」"
    for rule in ("功能壞了用 kind=bug", "沒讀過原始碼用 stage=report"):
        assert rule in instructions, f"指示詞應保留「{rule}」"
        assert rule not in create, f"create_issue 不應重述「{rule}」"


def test_原始碼不再有_get_issue_guide_字樣():
    """get_issue_guide 已刪除。六處引用漏一處就是指向不存在的工具，而那種錯
    不會讓任何測試變紅，除非明確去驗。這裡掃整個 src，比逐處斷言更難漏。"""
    src = Path(__file__).resolve().parents[1] / "src" / "redmine_mcp"
    hits = [
        f.relative_to(src).as_posix()
        for f in src.rglob("*.py")
        if "get_issue_guide" in f.read_text(encoding="utf-8")
    ]

    assert not hits, f"這些檔案仍提到已刪除的 get_issue_guide：{hits}"


async def test_指示詞與寫入工具改指向載體中立的格式來源(registry):
    """刪掉工具不等於刪掉資訊：模型仍須知道去哪裡拿格式。措辭不可只寫 skill
    名稱——用 MCP 的 harness 不一定有 skill 機制，所以要同時給檔案路徑。"""
    sites = registry(lambda r: httpx.Response(200))
    mcp = create_server(sites)
    tools = {
        tool.name: (tool.description or "")
        + json.dumps(tool.input_schema, ensure_ascii=False)
        for tool in await mcp.list_tools()
    }
    instructions = build_instructions(sites)
    await sites.aclose()

    assert "redmine-issue-writing" in instructions, "指示詞未指向 skill 名稱"
    assert "redmine-issue-guides" in instructions, "指示詞未給沒有 skill 機制時的路徑"
    for name in ("create_issue", "update_issue", "add_issue_note"):
        assert "get_issue_guide" not in tools[name], f"{name} 仍指向已刪除的工具"


async def test_指示詞要求新功能開發拆成三張子單(make_registry):
    """新功能開發只開一張單時，UI、API、DB 三層的進度混在同一份驗收裡，誰做完了
    分不出來。規則本體寫在骨架，指示詞只要讓模型在建單當下知道「要建四張、子單
    用 parent_issue_id 掛母單」——這件事漏了不會有任何東西報錯。"""
    sites = make_registry({"main": lambda r: httpx.Response(200)})
    mcp = create_server(sites)
    await sites.aclose()

    instructions = mcp.instructions or ""
    assert "UI／API／DB" in instructions, "指示詞未交代新功能開發要拆三張子單"
    assert "parent_issue_id" in instructions, "指示詞未交代子單要以 parent_issue_id 掛母單"


async def test_指示詞交代新需求的概述是活文件(make_registry):
    """新需求會被反覆修正。少了這條，模型會把每次修正寫成一則新註記，
    概述停在第一版，讀單的人得自己把散在十幾則註記裡的修正拼回去。
    資料表與關聯已改由 DB 子單的概述承載，落點列舉裡 feature+assess 那格若還寫 notes，
    模型就會在母單開一則註記再放一份，兩處遲早不同步。"""
    sites = make_registry({"main": lambda r: httpx.Response(200)})
    mcp = create_server(sites)
    await sites.aclose()

    instructions = mcp.instructions or ""
    assert "概述是活文件" in instructions, "指示詞未交代 feature 的概述要回頭改寫"
    assert "回頭改寫母單的 description" in instructions, "指示詞未指定改寫概述的工具"
    assert "kind=feature 沒有 notes 落點" in instructions, "指示詞未交代 feature 沒有註記落點"
    assert "feature+assess → DB 子單的 description" in instructions, (
        "落點列舉仍把 feature 的資料表與關聯指向註記"
    )
