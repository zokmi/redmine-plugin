"""list_sites 工具的測試。"""
from __future__ import annotations

from dataclasses import replace

import httpx

from redmine_mcp.config import Settings
from redmine_mcp.server import create_server
from redmine_mcp.sites import SiteRegistry
from tests.conftest import call_tool


def _never_called(request: httpx.Request) -> httpx.Response:
    """任何 HTTP 請求都代表 list_sites 實作錯誤。"""
    raise AssertionError(f"list_sites 不應發出 HTTP 請求，卻打了 {request.url}")


async def test_list_sites_回報設定中的站台(settings):
    sites = SiteRegistry(
        Settings(
            sites={
                "main": replace(settings, name="main", url="https://a.example.com", description="正式站"),
                "client-b": replace(settings, name="client-b", url="https://b.example.com"),
            }
        ),
        transports={"main": httpx.MockTransport(_never_called), "client-b": httpx.MockTransport(_never_called)},
    )
    mcp = create_server(sites)
    result = await call_tool(mcp, "list_sites")
    await sites.aclose()

    assert result == {
        "configured_sites": [
            {"name": "main", "url": "https://a.example.com", "description": "正式站"},
            {"name": "client-b", "url": "https://b.example.com", "description": None},
        ]
    }


async def test_list_sites_不發出_HTTP_請求(settings):
    sites = SiteRegistry(
        Settings(sites={"main": replace(settings, name="main")}),
        transports={"main": httpx.MockTransport(_never_called)},
    )
    mcp = create_server(sites)
    await call_tool(mcp, "list_sites")   # _never_called 會在有請求時拋出
    await sites.aclose()
