"""MCP 客戶端設定檔（JSON）的改寫邏輯。

只負責「字串進、字串出」，不碰檔案系統：這是本次唯一會動到使用者既有檔案的
邏輯，與 IO 分離才逐個邊界情境測得起來。實際的讀寫由呼叫端搭配
`fsops.write_config()` 的原子替換完成。

`upsert_mcp_server()`／`remove_mcp_server()` 回傳 None 的語意一致：**這份 JSON
不是能安全修改的形狀**（解析失敗、頂層不是物件、`mcpServers` 存在但不是物件）。
呼叫端據此決定完全不碰檔案、改印手動步驟——猜測要怎麼修一份看不懂的設定檔，
等於拿使用者的設定去賭。

`讀出設定()` 的 None 則有兩種意思（詳見其自身 docstring）：上述「不是能安全
修改的形狀」，或單純「這個名稱本來就沒有登記」；呼叫端若需要區分，請自行先
驗證形狀。
"""
from __future__ import annotations

import json
from typing import Any

#: MCP server 清單在設定檔裡的鍵名。Gemini CLI 的 ~/.gemini/settings.json 用這個名字。
_SERVERS_KEY = "mcpServers"


def _載入(原文: str) -> dict[str, Any] | None:
    """把設定檔內容解析成可安全修改的 dict。

    參數:
        原文: 檔案內容；空字串或全空白視為「還沒有任何設定」。
    回傳:
        頂層物件；不是能安全修改的形狀時為 None。
    """
    文字 = 原文.lstrip("﻿").strip()
    if not 文字:
        return {}
    try:
        資料 = json.loads(文字)
    except ValueError:
        return None
    if not isinstance(資料, dict):
        return None
    servers = 資料.get(_SERVERS_KEY)
    if servers is not None and not isinstance(servers, dict):
        return None
    return 資料


def _輸出(資料: dict[str, Any]) -> str:
    """把 dict 寫回設定檔格式。

    縮排兩格、尾端補換行、中文不跳脫——與 Gemini CLI 自己寫出來的格式一致，
    使用者之後看 diff 才不會整份翻掉。

    參數:
        資料: 頂層物件。
    回傳:
        可直接寫入檔案的字串。
    """
    return json.dumps(資料, indent=2, ensure_ascii=False) + "\n"


def upsert_mcp_server(原文: str, 名稱: str, 指令: str) -> str | None:
    """在設定檔裡新增或覆蓋一個 MCP server。

    只動 `mcpServers[名稱]` 一鍵，其餘鍵原封寫回。

    參數:
        原文: 現有檔案內容；檔案不存在時傳空字串。
        名稱: MCP server 名稱。
        指令: 啟動指令。
    回傳:
        改寫後的完整內容；原文不是能安全修改的形狀時為 None。
    """
    資料 = _載入(原文)
    if 資料 is None:
        return None
    servers = 資料.setdefault(_SERVERS_KEY, {})
    servers[名稱] = {"command": 指令}
    return _輸出(資料)


def remove_mcp_server(原文: str, 名稱: str) -> str | None:
    """從設定檔裡移除一個 MCP server。

    參數:
        原文: 現有檔案內容。
        名稱: 要移除的 MCP server 名稱；本來就不存在時視為已移除，不算失敗。
    回傳:
        改寫後的完整內容；原文不是能安全修改的形狀時為 None。
    """
    資料 = _載入(原文)
    if 資料 is None:
        return None
    servers = 資料.get(_SERVERS_KEY)
    if isinstance(servers, dict):
        servers.pop(名稱, None)
    return _輸出(資料)


def 讀出設定(原文: str, 名稱: str) -> Any | None:
    """讀出某個 MCP server 目前登記的完整設定值。

    用來判斷「已經有同名 server，而且內容跟這次要寫的不一樣」——那種情況要當面
    問過使用者才可以覆蓋。刻意回傳整個值（可能是 dict、字串、或任何 JSON 值），
    不只挑出 `command` 一個欄位：只比對 `command` 會讓 `args`／`trust` 等其餘欄位
    在「command 剛好相同」時被靜默蓋掉，也會讓 `{"url": ...}` 這種遠端型設定被
    誤判成「本來就沒有」。

    回傳 `None` 有兩種意思——**這個名稱本來就沒有登記**，或**整份 JSON 不是能
    安全修改的形狀**（解析失敗、頂層不是物件、`mcpServers` 存在但不是物件）。
    呼叫端若需要區分這兩種情況（例如「讀不懂就不動檔案、跟本來沒有走不同分支」），
    請自行先用 `_載入()` 對等的形狀檢查，或直接呼叫 `upsert_mcp_server()`／
    `remove_mcp_server()` 觀察其回傳是否為 None 來判斷。

    參數:
        原文: 現有檔案內容。
        名稱: MCP server 名稱。
    回傳:
        該名稱目前登記的值；不存在、或原文不是能安全修改的形狀時皆為 None。
    """
    資料 = _載入(原文)
    if 資料 is None:
        return None
    servers = 資料.get(_SERVERS_KEY)
    if not isinstance(servers, dict):
        return None
    return servers.get(名稱)
