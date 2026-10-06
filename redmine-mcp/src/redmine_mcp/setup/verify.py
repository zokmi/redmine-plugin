"""寫檔前先驗一次金鑰。

這是精靈價值最高的一步：把「裝完、重開 Claude Code、呼叫工具才發現網址少了
/redmine」提前到還能立刻重填的時候。走的是與 get_current_user 完全相同的路徑，
因此這裡通過就代表實際使用時也會通過。
"""
from __future__ import annotations

from typing import Any

import httpx

from redmine_mcp.client import RedmineClient
from redmine_mcp.config import SiteSettings
from redmine_mcp.errors import RedmineError, RedmineServerError


async def verify_credentials(
    url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None
) -> str:
    """以 /users/current.json 驗證站台網址與金鑰。

    參數:
        url: 站台根位址。
        api_key: 該站台的 API key。
        transport: 測試用的替代傳輸層；正式執行時留空，會走既有的 PyCurlTransport。
    回傳:
        該金鑰對應的登入帳號（login）。
    例外:
        RedmineError 及其子類：認證失敗、連線失敗、回應不是 JSON 等。訊息一律
            不含金鑰內容。
    """
    settings = SiteSettings(
        name="setup",
        url=url.rstrip("/"),
        api_key=api_key,
        description=None,
        download_dir=None,
        upload_dir=None,
        max_attachment_bytes=0,
    )
    client = RedmineClient(settings, transport=transport)
    try:
        raw: dict[str, Any] = await client.get("/users/current.json")
    except RedmineError as exc:
        # 「少了子路徑」提示只能加在真正對應這個成因的情境，不能無條件加在任何
        # RedmineError 上——對照 SETUP.md 排錯表：
        #   - 401/403 且回應是合法 JSON → 是真的 API key 錯誤或權限不足，
        #     client.py 的 _raise_for_status() 已給出正確訊息，此時若還提示
        #     「網址少子路徑」，等於同一句話同時叫使用者查金鑰又查網址，
        #     而金鑰打錯才是這種情境最常見的原因。
        #   - 401/403/404 且回應不是 JSON → client.py 的 _non_json_hint()
        #     已經在例外訊息裡附上同一句子路徑提示，這裡不必也不該再加一次。
        #   - 狀態碼在 2xx（例如網址少子路徑常見的 200 + HTML 登入頁），但 body
        #     解不出 JSON → client.py 的 _parse_json() 對這種情況完全沒有附加
        #     任何提示，是本函式唯一真正需要補提示的情境。
        # 這種情境在型別上的獨有特徵：RedmineServerError 且其 __cause__ 是
        # response.json() 失敗時的原始 ValueError（_parse_json 用
        # `raise RedmineServerError(...) from exc` 保留了它）。用型別／因果鏈
        # 判斷而不比對訊息字串，避免 client.py 未來調整措辭就讓這裡失效或誤判。
        if isinstance(exc, RedmineServerError) and isinstance(exc.__cause__, ValueError):
            raise type(exc)(
                f"{exc}（最常見的原因是站台網址少了子路徑，"
                "例如應為 https://redmine.example.com/redmine 而非只有網域）"
            ) from exc
        raise
    finally:
        await client.aclose()

    login = (raw.get("user") or {}).get("login")
    if not login:
        raise RedmineError(
            "Redmine 有回應，但內容不是預期的使用者資料；"
            "請確認網址指向的是 Redmine 本身而非入口頁或反向代理"
        )
    return str(login)
