"""站台註冊表與扇出的測試。"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from redmine_mcp import sites as sites_module
from redmine_mcp.client import RedmineClient
from redmine_mcp.config import Settings, SiteSettings
from redmine_mcp.errors import RedmineServerError
from redmine_mcp.sites import SiteRegistry, fan_out


def _site(name: str, host: str) -> SiteSettings:
    """組出測試用的單站設定。"""
    return SiteSettings(
        name=name,
        url=f"https://{host}",
        api_key=f"key-{name}",
        description=None,
        download_dir=None,
        upload_dir=None,
        max_attachment_bytes=1024,
    )


def _settings(*names: str) -> Settings:
    """依名稱組出多站台設定，每站對應不同 host。"""
    return Settings(sites={name: _site(name, f"{name}.example.com") for name in names})


def _ok(payload: dict) -> httpx.MockTransport:
    """回傳固定 JSON 的假傳輸層。"""
    return httpx.MockTransport(lambda request: httpx.Response(200, json=payload))


def test_resolve_省略時回傳全部站台():
    registry = SiteRegistry(_settings("main", "client-b"))
    assert list(registry.resolve(None)) == ["main", "client-b"]


def test_resolve_指定時只回傳該站():
    registry = SiteRegistry(_settings("main", "client-b"))
    resolved = registry.resolve("client-b")
    assert list(resolved) == ["client-b"]


def test_resolve_站台不存在時訊息列出可用名稱():
    registry = SiteRegistry(_settings("main", "client-b"))
    with pytest.raises(ValueError) as exc:
        registry.resolve("typo")
    message = str(exc.value)
    assert "main" in message
    assert "client-b" in message


def test_resolve_for_write_多站台省略時拒絕():
    registry = SiteRegistry(_settings("main", "client-b"))
    with pytest.raises(ValueError) as exc:
        registry.resolve_for_write(None)
    message = str(exc.value)
    assert "site" in message
    assert "main" in message


def test_resolve_for_write_單站台省略時放行():
    registry = SiteRegistry(_settings("only"))
    name, client = registry.resolve_for_write(None)
    assert name == "only"
    assert isinstance(client, RedmineClient)


def test_resolve_for_write_指定時回傳該站():
    registry = SiteRegistry(_settings("main", "client-b"))
    name, _ = registry.resolve_for_write("client-b")
    assert name == "client-b"


def test_names_維持宣告順序():
    registry = SiteRegistry(_settings("z-site", "a-site", "m-site"))
    assert registry.names == ("z-site", "a-site", "m-site")


async def test_扇出並行取得各站結果():
    settings = _settings("main", "client-b")
    registry = SiteRegistry(
        settings,
        transports={"main": _ok({"n": 1}), "client-b": _ok({"n": 2})},
    )
    result = await fan_out(registry.resolve(None), lambda c: c.get("/x.json"))
    await registry.aclose()

    assert result.as_payload() == {"main": {"n": 1}, "client-b": {"n": 2}}
    assert result.found_in == ["main", "client-b"]


async def test_某站失敗不影響其他站():
    settings = _settings("main", "broken")
    registry = SiteRegistry(
        settings,
        transports={
            "main": _ok({"n": 1}),
            "broken": httpx.MockTransport(lambda r: httpx.Response(500)),
        },
    )
    result = await fan_out(registry.resolve(None), lambda c: c.get("/x.json"))
    await registry.aclose()

    payload = result.as_payload()
    assert payload["main"] == {"n": 1}
    assert "error" in payload["broken"]
    assert result.found_in == ["main"]


async def test_404_在_none_on_not_found_時轉為_None():
    settings = _settings("main", "empty")
    registry = SiteRegistry(
        settings,
        transports={
            "main": _ok({"n": 1}),
            "empty": httpx.MockTransport(lambda r: httpx.Response(404)),
        },
    )
    result = await fan_out(
        registry.resolve(None), lambda c: c.get("/x.json"), none_on_not_found=True
    )
    await registry.aclose()

    assert result.as_payload() == {"main": {"n": 1}, "empty": None}
    assert result.found_in == ["main"]


async def test_404_未開啟時視為錯誤():
    registry = SiteRegistry(
        _settings("only"),
        transports={"only": httpx.MockTransport(lambda r: httpx.Response(404))},
    )
    result = await fan_out(registry.resolve(None), lambda c: c.get("/x.json"))
    await registry.aclose()

    assert "error" in result.as_payload()["only"]


async def test_非預期例外不把原始訊息帶進回傳():
    registry = SiteRegistry(_settings("only"), transports={"only": _ok({})})

    async def boom(client: RedmineClient) -> dict:
        raise RuntimeError("內部細節-super-secret")

    result = await fan_out(registry.resolve(None), boom)
    await registry.aclose()

    message = result.as_payload()["only"]["error"]
    assert "super-secret" not in message


async def test_Redmine例外的訊息會呈現():
    registry = SiteRegistry(_settings("only"), transports={"only": _ok({})})

    async def fail(client: RedmineClient) -> dict:
        raise RedmineServerError("Redmine 伺服器錯誤（HTTP 503），請稍後再試")

    result = await fan_out(registry.resolve(None), fail)
    await registry.aclose()

    assert "503" in result.as_payload()["only"]["error"]


async def test_並行度受上限限制(monkeypatch: pytest.MonkeyPatch):
    names = [f"s{i}" for i in range(12)]
    registry = SiteRegistry(
        _settings(*names), transports={name: _ok({}) for name in names}
    )
    monkeypatch.setattr("redmine_mcp.sites.MAX_CONCURRENT_SITES", 3)

    active = 0
    peak = 0

    async def slow(client: RedmineClient) -> dict:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {}

    await fan_out(registry.resolve(None), slow)
    await registry.aclose()

    assert peak <= 3


async def test_aclose_在某站失敗時仍關閉其餘站台():
    registry = SiteRegistry(_settings("a", "b"), transports={"a": _ok({}), "b": _ok({})})

    async def boom() -> None:
        raise RuntimeError("關閉失敗")

    registry.resolve("a")["a"].aclose = boom  # type: ignore[method-assign]
    await registry.aclose()  # 不應拋出

    # b 已被關閉，再次請求會失敗
    with pytest.raises(RuntimeError):
        await registry.resolve("b")["b"].get("/x.json")


async def test_扇出時每站帶自己的金鑰():
    seen: dict[str, str] = {}

    def make(name: str) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            seen[name] = request.headers["X-Redmine-API-Key"]
            return httpx.Response(200, json={})

        return httpx.MockTransport(handler)

    registry = SiteRegistry(
        _settings("main", "client-b"),
        transports={"main": make("main"), "client-b": make("client-b")},
    )
    await fan_out(registry.resolve(None), lambda c: c.get("/x.json"))
    await registry.aclose()

    assert seen == {"main": "key-main", "client-b": "key-client-b"}


# --- 單站逾時：慢站不得拖垮整批 -------------------------------------------


async def test_fan_out_慢站逾時但健康站的資料仍回得來(monkeypatch):
    """連得上但回應極慢的站台（Redmine 在跑報表、DB 鎖住）不應讓整次查詢歸零。

    沒有這道把關時，asyncio.gather 會等到 client 層的 60 秒總逾時，
    而 MCP client 端的逾時只有 30 秒——健康站台明明 0.5 秒就回完，
    整批卻會被 client 判逾時丟棄，使用者拿到 0 筆而不是 2 站的資料。
    """
    monkeypatch.setattr(sites_module, "PER_SITE_TIMEOUT_SECONDS", 0.05)

    async def fn(client):
        if client is None:
            await asyncio.sleep(5)
        return "ok"

    result = await fan_out({"fast": object(), "slow": None}, fn)

    assert result.values["fast"] == "ok"
    assert result.values["slow"] is None
    assert "slow" in result.errors
    assert "逾時" in result.errors["slow"]
    assert "fast" not in result.errors


async def test_fan_out_逾時訊息指出是哪一站(monkeypatch):
    """錯誤訊息要讓模型知道「這站沒回應」而非「這站沒有資料」，兩者處置不同。"""
    monkeypatch.setattr(sites_module, "PER_SITE_TIMEOUT_SECONDS", 0.05)

    async def fn(client):
        await asyncio.sleep(5)

    result = await fan_out({"main": object()}, fn)

    assert result.values == {"main": None}
    assert "main" in result.errors
