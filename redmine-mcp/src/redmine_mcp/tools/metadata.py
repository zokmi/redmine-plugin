"""Metadata 工具：讓模型把名稱翻成 id，並驗證 API key 是否有效。"""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, TypeVar

from mcp.types import ToolAnnotations
from pydantic import Field

from redmine_mcp.client import RedmineClient, clamp_limit
from redmine_mcp.formatting import format_named_list, neutralize_untrusted_markers, paginated
from redmine_mcp.sites import SITE_DESCRIPTION, SiteRegistry, fan_out

READ_ONLY = ToolAnnotations(read_only_hint=True)

#: MetadataCache.fetch 的輸出型別，由 build 函式決定。
_T = TypeVar("_T")


#: 單一自訂欄位最多輸出幾個可選值。
#:
#: 企業 Redmine 常有「客戶名單」「模組清單」這類下拉式自訂欄位，單一欄位的
#: possible_values 可達數百至數千項；三十個欄位裡只要有幾個是這種，單次呼叫就是
#: 上萬字的清單。超過時附上 possible_values_total，需要知道實際值改查 get_issue。
POSSIBLE_VALUES_LIMIT = 50


def _build_custom_fields_payload(raw: dict) -> dict[str, Any]:
    """把 /custom_fields.json 的回應整理成輸出格式，並限制每個欄位的可選值筆數。"""
    fields = []
    for item in raw.get("custom_fields") or []:
        # possible_values 與 name 皆為管理員可自訂的參照名稱，
        # 只中和圍籬標記、不包圍籬（與 flatten_ref 的裁定一致）。
        values = item.get("possible_values") or []
        possible = [
            neutralize_untrusted_markers(
                str(value.get("value") if isinstance(value, dict) else value)
            )
            for value in values[:POSSIBLE_VALUES_LIMIT]
        ]
        field: dict[str, Any] = {
            "id": item.get("id"),
            "name": neutralize_untrusted_markers(str(item.get("name") or "")),
            "customized_type": item.get("customized_type"),
            "field_format": item.get("field_format"),
            "possible_values": possible,
        }
        if len(values) > len(possible):
            field["possible_values_total"] = len(values)
            field["possible_values_truncated"] = True
        fields.append(field)
    return {"custom_fields": fields}


class MetadataCache:
    """單一站台、單次 server 生命週期內的 metadata 快取。

    trackers / statuses / priorities / custom_fields 幾乎不變動，
    快取可省下重複往返。
    各站台的 id 體系彼此獨立，因此快取以站台為單位各建一份，不可共用。
    """

    def __init__(self, client: RedmineClient) -> None:
        self._client = client
        self._store: dict[str, Any] = {}

    async def fetch(self, path: str, build: Callable[[dict], _T]) -> _T:
        """取得 path 的回應並以 build 整理成輸出格式，同一 path 只實際請求一次。

        快取的是**整理後**的結果而非原始回應：原始回應體積大得多，而整理邏輯
        對同一份輸入是固定的，沒有理由重算也沒有理由多留一份。

        參數:
            path: API 路徑，同時作為快取鍵。
            build: 把原始回應轉成輸出格式的函式。
        """
        if path not in self._store:
            raw = await self._client.get(path)
            self._store[path] = build(raw)
        cached: _T = self._store[path]
        return cached

    async def fetch_named(self, path: str, key: str) -> list[dict]:
        """取得只含 id 與 name 的清單，同一 path 只實際請求一次。"""
        result = await self.fetch(path, lambda raw: format_named_list(raw, key))
        return list(result)


def register(mcp: Any, sites: SiteRegistry) -> None:
    """在 MCP server 上註冊 metadata 與站台工具。"""
    # 每站一份快取：各站的 tracker/status id 體系不同，不可共用。
    caches = {name: MetadataCache(client) for name, client in sites.resolve(None).items()}

    async def _named(site: str | None, path: str, key: str) -> dict[str, Any]:
        """跨站取得只含 id 與 name 的清單，走各站自己的快取。"""
        clients = sites.resolve(site)

        async def fetch(client: RedmineClient) -> dict[str, Any]:
            return {key: await caches[client.settings.name].fetch_named(path, key)}

        result = await fan_out(clients, fetch)
        return {"sites": result.as_payload()}

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "列出 Redmine 專案清單，取得專案的 id 與 identifier，供其他工具的 project_id 參數使用，"
            "按站台分組。"
        ),
    )
    async def list_projects(
        offset: Annotated[int, Field(description="起始位置，用於翻頁。")] = 0,
        limit: Annotated[int, Field(description="每頁筆數，自動夾在 1–100。")] = 25,
        site: Annotated[str | None, Field(description=SITE_DESCRIPTION)] = None,
    ) -> dict[str, Any]:
        """列出專案。

        參數:
            offset: 起始位置，用於翻頁。
            limit: 每頁筆數，自動夾在 1–100。
            site: 站台代號；省略則查詢所有已設定的站台。
        """

        async def fetch(client: RedmineClient) -> dict[str, Any]:
            raw = await client.get(
                "/projects.json", {"offset": offset, "limit": clamp_limit(limit)}
            )
            rows = [
                {
                    "id": item.get("id"),
                    # name 是 PM／管理員可自訂的專案名稱，屬使用者可控的參照名稱，
                    # 與 format_named_list／flatten_ref 的裁定一致：只中和圍籬標記、
                    # 不包圍籬。這裡沒有直接用 format_named_list 是因為多要
                    # identifier 欄位，但中和動作不能因此被漏掉。
                    "name": neutralize_untrusted_markers(str(item.get("name") or "")),
                    # identifier 受 Redmine 限制只能是 [a-z0-9_-]，不需中和。
                    "identifier": item.get("identifier"),
                }
                for item in raw.get("projects") or []
            ]
            return paginated(raw, "projects", rows)

        result = await fan_out(sites.resolve(site), fetch)
        return {"sites": result.as_payload()}

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "列出追蹤標籤（Tracker，例如 Bug、Feature），回傳 id 與名稱，按站台分組。"
            "各站台的 id 體系不同，套用時務必使用同一站台的 id。"
        ),
    )
    async def list_trackers(
        site: Annotated[str | None, Field(description=SITE_DESCRIPTION)] = None,
    ) -> dict[str, Any]:
        """列出追蹤標籤。

        參數:
            site: 站台代號；省略則查詢所有已設定的站台。
        """
        return await _named(site, "/trackers.json", "trackers")

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "列出 issue 狀態（例如 新建立、進行中、已解決），回傳 id 與名稱，按站台分組。"
            "各站台的 id 體系不同，套用時務必使用同一站台的 id。"
        ),
    )
    async def list_issue_statuses(
        site: Annotated[str | None, Field(description=SITE_DESCRIPTION)] = None,
    ) -> dict[str, Any]:
        """列出 issue 狀態。

        參數:
            site: 站台代號；省略則查詢所有已設定的站台。
        """
        return await _named(site, "/issue_statuses.json", "issue_statuses")

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "列出 issue 優先權（例如 低、普通、高），回傳 id 與名稱，按站台分組。"
            "各站台的 id 體系不同，套用時務必使用同一站台的 id。"
        ),
    )
    async def list_priorities(
        site: Annotated[str | None, Field(description=SITE_DESCRIPTION)] = None,
    ) -> dict[str, Any]:
        """列出優先權。

        參數:
            site: 站台代號；省略則查詢所有已設定的站台。
        """
        clients = sites.resolve(site)

        async def fetch(client: RedmineClient) -> dict[str, Any]:
            priorities = await caches[client.settings.name].fetch_named(
                "/enumerations/issue_priorities.json", "issue_priorities"
            )
            return {"priorities": priorities}

        result = await fan_out(clients, fetch)
        return {"sites": result.as_payload()}

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "列出自訂欄位定義，包含 id、名稱、資料格式與可選值，"
            "供建立或更新 issue 時填寫 custom_fields，按站台分組。"
            "需要管理員權限，一般帳號呼叫會收到認證錯誤（401/403）；"
            "若只是要知道某張既有 issue 上某個自訂欄位的 id，改呼叫 get_issue 即可，"
            "回傳的 custom_fields[名稱].id 不需管理員權限。"
        ),
    )
    async def list_custom_fields(
        site: Annotated[str | None, Field(description=SITE_DESCRIPTION)] = None,
    ) -> dict[str, Any]:
        """列出自訂欄位定義。需要管理員權限，權限不足時會回傳認證錯誤。

        參數:
            site: 站台代號；省略則查詢所有已設定的站台。
        """

        clients = sites.resolve(site)

        async def fetch(client: RedmineClient) -> dict[str, Any]:
            return await caches[client.settings.name].fetch(
                "/custom_fields.json", _build_custom_fields_payload
            )

        result = await fan_out(clients, fetch)
        return {"sites": result.as_payload()}

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "取得目前 API key 對應的使用者資訊，可用來驗證連線設定是否正確，"
            "並取得自己的 user id 供 assigned_to_id 使用，按站台分組。"
        ),
    )
    async def get_current_user(
        site: Annotated[str | None, Field(description=SITE_DESCRIPTION)] = None,
    ) -> dict[str, Any]:
        """取得目前使用者。

        參數:
            site: 站台代號；省略則查詢所有已設定的站台。
        """

        async def fetch(client: RedmineClient) -> dict[str, Any]:
            raw = await client.get("/users/current.json")
            user = raw.get("user") or {}
            return {
                "id": user.get("id"),
                # login／mail 同樣由使用者自行維護，與 name 一視同仁先中和。
                "login": neutralize_untrusted_markers(str(user.get("login") or "")),
                # firstname／lastname 由使用者自行維護，屬不可信輸入，交給模型前先中和圍籬標記。
                "name": neutralize_untrusted_markers(
                    f"{user.get('firstname', '')}{user.get('lastname', '')}".strip()
                ),
                "mail": neutralize_untrusted_markers(str(user.get("mail") or "")),
            }

        result = await fan_out(sites.resolve(site), fetch)
        return {"sites": result.as_payload()}

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "列出本服務已設定的所有 Redmine 站台，回傳站台代號、網址與說明。"
            "其他工具的 site 參數要填的就是這裡的站台代號。"
            "本工具只讀取本機設定，不會連線任何站台。"
        ),
    )
    async def list_sites() -> dict[str, Any]:
        """列出已設定的站台。"""
        return {
            "configured_sites": [
                {
                    "name": client.settings.name,
                    "url": client.settings.url,
                    "description": client.settings.description,
                }
                for client in sites.resolve(None).values()
            ]
        }
