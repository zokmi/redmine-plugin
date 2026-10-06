"""站台註冊表與跨站扇出。

多站台的兩條核心規則都實作在這裡，且各只實作一次：
讀取省略站台即查詢全部（`resolve`），寫入在多站台時強制指定（`resolve_for_write`）。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from redmine_mcp.client import RedmineClient
from redmine_mcp.config import Settings
from redmine_mcp.errors import RedmineError, RedmineNotFoundError

#: 同時進行的站台請求數上限。每個請求佔一條 executor 執行緒並從 handle 池借一個
#: pycurl handle，站台數增長到十幾個時無上限的扇出會造成執行緒與 handle 過度佔用。
MAX_CONCURRENT_SITES = 8

#: 扇出時單一站台的逾時秒數，刻意明顯低於 client 層的 60 秒總逾時。
#:
#: 為什麼需要比 client 更早的一道把關：扇出要等所有站台都有結果才回傳，因此一個
#: 「連得上但回應很慢」的站台（Redmine 在跑大型報表、DB 被鎖）會讓整批一起等到
#: 60 秒。而 SETUP.md 記錄 MCP client 端的逾時只有 30 秒——結果是健康站台明明
#: 0.5 秒就回完資料，卻因為陪跑而整批被 client 判逾時丟棄，使用者拿到 0 筆而不是
#: 其他站的資料。設在這裡讓慢站只會讓自己缺席，並在 errors 中誠實標示。
#:
#: 只影響 fan_out（全部是讀取類查詢）；寫入與附件下載走各自的 client 呼叫，
#: 仍沿用 60 秒的總逾時。
PER_SITE_TIMEOUT_SECONDS = 20.0

#: 讀取類工具共用的 site 參數說明。
#: 集中定義在這裡而非某個工具模組，避免四個工具模組為了共用一段字串而互相匯入。
SITE_DESCRIPTION = "站台代號；省略則查詢所有已設定的站台。可用 list_sites 取得站台清單。"

#: 寫入類工具共用的 site 參數說明。
WRITE_SITE_DESCRIPTION = "要寫入的站台代號；設定多個站台時必填。可用 list_sites 取得站台清單。"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FanOutResult:
    """扇出結果。

    刻意把成功值與錯誤分開存放，而不是把錯誤混進 values 成為 {"error": ...}——
    後者無法區分「站台回傳的資料剛好有 error 欄位」與「這站失敗了」。

    屬性:
        values: 站台名稱 → payload；查無資料且允許時為 None。
        errors: 站台名稱 → 錯誤訊息；只有失敗的站台會出現在這裡。
    """

    values: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        """合併成要回傳給模型的 sites 字典，維持站台宣告順序。"""
        return {
            name: ({"error": self.errors[name]} if name in self.errors else value)
            for name, value in self.values.items()
        }

    @property
    def found_in(self) -> list[str]:
        """實際有命中資料的站台名稱（非 None 且未出錯）。"""
        return [
            name
            for name, value in self.values.items()
            if name not in self.errors and value is not None
        ]


class SiteRegistry:
    """站台名稱 → RedmineClient 的註冊表，並提供跨站扇出。"""

    def __init__(
        self,
        settings: Settings,
        transports: Mapping[str, httpx.AsyncBaseTransport] | None = None,
    ) -> None:
        """建立所有站台的 client。

        所有 client 於啟動時一次建好而非延遲建立——設定錯誤要在啟動就浮現，
        而不是等到第一次呼叫該站台的工具。

        參數:
            settings: 已驗證的整體設定。
            transports: 測試用的替代傳輸層，鍵為站台名稱；正式執行時留空。
                每站一份而非共用單一個，測試才能讓不同站台回傳不同結果。
        """
        overrides = transports or {}
        self._clients: dict[str, RedmineClient] = {
            name: RedmineClient(site, transport=overrides.get(name))
            for name, site in settings.sites.items()
        }

    @property
    def names(self) -> tuple[str, ...]:
        """所有站台名稱，維持參數檔中的宣告順序。"""
        return tuple(self._clients)

    def _available(self) -> str:
        """組出「可用站台」提示字串，供錯誤訊息使用。"""
        return "、".join(self.names)

    def resolve(self, site: str | None) -> dict[str, RedmineClient]:
        """讀取用的站台解析。

        參數:
            site: 站台名稱；None 代表查詢所有已設定的站台。
        回傳:
            站台名稱 → client 的對照表。
        例外:
            ValueError: 指定的站台名稱不存在。
        """
        if site is None:
            return dict(self._clients)
        client = self._clients.get(site)
        if client is None:
            raise ValueError(f"沒有名為 {site!r} 的站台（可用：{self._available()}）")
        return {site: client}

    def resolve_for_write(self, site: str | None) -> tuple[str, RedmineClient]:
        """寫入用的站台解析。

        寫入永不扇出：在多個站台各建一張單是無法用重試修掉的災難，
        因此設定多個站台時必須明寫要寫入哪一站。

        參數:
            site: 站台名稱；只有恰好一個站台時可為 None。
        回傳:
            (站台名稱, client)。
        例外:
            ValueError: 多站台時未指定站台，或指定的站台不存在。
        """
        if site is None:
            if len(self._clients) > 1:
                raise ValueError(
                    f"已設定多個站台，寫入前請明寫 site（可用：{self._available()}）"
                )
            name = self.names[0]
            return name, self._clients[name]
        return site, self.resolve(site)[site]

    async def aclose(self) -> None:
        """逐站關閉底層連線；單站失敗不影響其餘站台。"""
        for name, client in self._clients.items():
            try:
                await client.aclose()
            except Exception:
                logger.debug("關閉站台 %s 的 client 時發生錯誤，已忽略", name, exc_info=True)


async def fan_out(
    clients: Mapping[str, RedmineClient],
    fn: Callable[[RedmineClient], Awaitable[Any]],
    *,
    none_on_not_found: bool = False,
) -> FanOutResult:
    """對每個站台並行執行 fn，每站錯誤各自隔離。

    某站 API key 過期或連線失敗不應讓整個查詢失敗，因此各站結果獨立收集。

    參數:
        clients: 站台名稱 → client，通常來自 SiteRegistry.resolve()。
        fn: 對單一 client 執行的協程函式。
        none_on_not_found: 為 True 時把 404 視為「這站沒有」而回 None，
            而非錯誤。僅用於 id 查找情境。
    回傳:
        FanOutResult，站台順序與傳入的 clients 一致。
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_SITES)

    async def run(name: str, client: RedmineClient) -> tuple[str, Any, str | None]:
        """執行單一站台的查詢，回傳 (站台名, 值, 錯誤訊息)。"""
        async with semaphore:
            try:
                return name, await asyncio.wait_for(fn(client), PER_SITE_TIMEOUT_SECONDS), None
            except TimeoutError:
                # 訊息要能讓模型分辨「這站沒回應」與「這站沒有這筆資料」，
                # 兩者的後續處置完全不同（前者可重試，後者不該重試）。
                logger.warning("站台 %s 於 %.0f 秒內未回應", name, PER_SITE_TIMEOUT_SECONDS)
                return name, None, f"此站台於 {PER_SITE_TIMEOUT_SECONDS:.0f} 秒內未回應（查詢逾時）"
            except RedmineNotFoundError as exc:
                if none_on_not_found:
                    return name, None, None
                return name, None, str(exc)
            except RedmineError as exc:
                # RedmineError 的訊息已在 client 層濾除憑證資訊，可直接呈現。
                return name, None, str(exc)
            except Exception:
                # 非預期例外可能夾帶內部細節，只回通用訊息，完整內容寫入 log。
                logger.exception("查詢站台 %s 時發生非預期錯誤", name)
                return name, None, "查詢此站台時發生非預期錯誤"

    rows = await asyncio.gather(*(run(name, client) for name, client in clients.items()))

    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name, value, error in rows:
        values[name] = value
        if error is not None:
            errors[name] = error
    return FanOutResult(values=values, errors=errors)
