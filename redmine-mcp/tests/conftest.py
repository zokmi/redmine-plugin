"""測試共用 fixture。"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace

import httpx
import pytest

from redmine_mcp.config import Settings, SiteSettings
from redmine_mcp.sites import SiteRegistry

BASE_URL = "https://redmine.example.com"


@pytest.fixture
def settings(tmp_path) -> SiteSettings:
    """指向假站台的單站設定，下載與上傳目錄都使用 tmp_path。"""
    return SiteSettings(
        name="default",
        url=BASE_URL,
        api_key="test-key",
        description=None,
        download_dir=tmp_path,
        upload_dir=tmp_path,
        max_attachment_bytes=1024,
    )


@pytest.fixture
def make_transport() -> Callable[[Callable[[httpx.Request], httpx.Response]], httpx.MockTransport]:
    """以 handler 函式建立 MockTransport 的工廠。"""

    def factory(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
        return httpx.MockTransport(handler)

    return factory


def json_response(status: int, payload: dict | None = None) -> httpx.Response:
    """組出帶 JSON body 的假回應。"""
    return httpx.Response(
        status,
        content=json.dumps(payload or {}).encode(),
        headers={"Content-Type": "application/json"},
    )


class ToolCallError(RuntimeError):
    """工具回傳 is_error 時由 call_tool 輔助函式轉拋，方便用 pytest.raises 斷言。"""


async def call_tool(mcp, name: str, args: dict | None = None):
    """透過 MCP server 呼叫工具並取回結構化結果。

    MCP SDK 2.0 的 call_tool 回傳 CallToolResult，工具內丟出的例外會被
    包成 is_error=True 的結果而不是往外拋，因此這裡還原成例外，
    讓測試能用 pytest.raises 斷言錯誤訊息。
    """
    result = await mcp.call_tool(name, args or {})
    if getattr(result, "is_error", False):
        text = " ".join(
            getattr(block, "text", "") for block in (result.content or [])
        ).strip()
        raise ToolCallError(text or f"{name} 呼叫失敗")
    return result.structured_content


@pytest.fixture
def make_registry(settings) -> Callable[..., SiteRegistry]:
    """以站台名稱 → handler 的對照表建立 SiteRegistry 的工廠。

    每站各自一份 MockTransport，測試才能讓不同站台回傳不同結果。
    未指定 handler 的站台沿用 settings fixture 的目錄與大小上限設定。
    """

    def factory(handlers: dict[str, Callable[[httpx.Request], httpx.Response]]) -> SiteRegistry:
        sites = {
            name: replace(settings, name=name, url=f"https://{name}.example.com")
            for name in handlers
        }
        return SiteRegistry(
            Settings(sites=sites),
            transports={
                name: httpx.MockTransport(handler) for name, handler in handlers.items()
            },
        )

    return factory


@pytest.fixture
def registry(settings) -> Callable[[Callable[[httpx.Request], httpx.Response]], SiteRegistry]:
    """建立只有一個名為 default 站台的 SiteRegistry，供既有單站測試沿用。

    站台名稱固定為 default，與舊格式參數檔轉換後的名稱一致，
    既有測試的斷言只需在原路徑前多一層 sites["default"]。
    """

    def factory(handler: Callable[[httpx.Request], httpx.Response]) -> SiteRegistry:
        return SiteRegistry(
            Settings(sites={"default": settings}),
            transports={"default": httpx.MockTransport(handler)},
        )

    return factory
