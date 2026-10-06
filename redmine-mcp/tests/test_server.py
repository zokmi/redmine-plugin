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


# 指示詞與工具說明的字元上限；實際載入與快取成本依 client 而定。
MANIFEST_CHAR_BUDGET = 3000


async def test_manifest_不超過字元預算(registry):
    sites = registry(lambda r: httpx.Response(200))
    mcp = create_server(sites)
    tools = await mcp.list_tools()
    instructions = build_instructions(sites)
    await sites.aclose()

    total = len(instructions) + sum(len(tool.description or "") for tool in tools)
    assert total <= MANIFEST_CHAR_BUDGET, f"manifest 已膨脹到 {total} 字元"


async def test_寫單規範按需從_plugin_skill_載入(registry):
    sites = registry(lambda r: httpx.Response(200))
    instructions = build_instructions(sites)
    await sites.aclose()
    assert "redmine-issue-writing" in instructions
    assert "summary=true" in instructions
    skill = Path(__file__).resolve().parents[2] / "skills/redmine-issue-writing/SKILL.md"
    content = skill.read_text(encoding="utf-8")
    for rule in ("不可拆散到自訂欄位", "不臆測", "report", "diagnose", "UI／API／DB", "parent_issue_id"):
        assert rule in content


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
    """plugin 提供格式來源，不再依賴手動安裝路徑。"""
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
    assert "redmine-issue-guides" not in instructions, "plugin 安裝不應指向舊手動安裝路徑"
    for name in ("create_issue", "update_issue", "add_issue_note"):
        assert "get_issue_guide" not in tools[name], f"{name} 仍指向已刪除的工具"
