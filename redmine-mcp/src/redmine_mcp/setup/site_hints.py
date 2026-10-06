"""由使用者貼的一個網址，推出參數檔需要的其餘欄位。

精靈瘦身的基礎：非 IT 使用者只會從瀏覽器位址欄複製一個網址，「站台代號」與
「網址要帶 /redmine 子路徑」這兩件事都不該由他負責。本模組刻意只放純函式——
不碰網路、不碰檔案、不碰終端，因此可以逐案例釘死行為。
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urlsplit

from redmine_mcp.config import _SITE_NAME_PATTERN

#: 推不出任何可用代號時的退路。合法且語意中立，總比讓使用者看到驗證錯誤好。
_FALLBACK_NAME = "site"

#: 主機名前綴中不帶識別資訊的部分，推代號時去掉。
_NOISE_PREFIXES = ("www.", "redmine.")

#: 代號中不允許的字元，一律轉為減號。
_ILLEGAL = re.compile(r"[^a-zA-Z0-9_-]+")

#: 判斷字串開頭有沒有 scheme。
_HAS_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def normalize_url(raw: str) -> str:
    """把使用者貼進來的網址整理成可寫進參數檔的形式。

    做三件事：去前後空白、沒有 scheme 時補 https://、去掉尾隨斜線。
    刻意**不**動大小寫——有些反向代理對路徑大小寫敏感，改了反而連不上。

    參數:
        raw: 使用者輸入的原始字串。
    回傳:
        正規化後的網址；輸入全為空白時回傳空字串。
    """
    url = raw.strip()
    if not url:
        return ""
    if not _HAS_SCHEME.match(url):
        url = f"https://{url}"
    return url.rstrip("/")


def subpath_candidate(url: str) -> str | None:
    """網址沒有子路徑時，給出補上 `/redmine` 的候選。

    這是 README FAQ 第一名的失敗成因：使用者只填網域，Redmine 實際掛在
    `/redmine` 底下，請求打到反向代理而回傳非 JSON，訊息看起來像 401／403。

    參數:
        url: 已正規化的網址。
    回傳:
        候選網址；已經有子路徑時為 None。
    """
    path = urlsplit(url).path.strip("/")
    return None if path else f"{url}/redmine"


def derive_site_label(url: str) -> str:
    """由網址推導站台代號，不處理與既有代號的衝突。

    取主機名去掉 `www.`／`redmine.` 前綴後的第一段標籤，非法字元轉減號；推不出
    東西時退回 `site`。**刻意不做 -2 遞增**：bundle 模式（ZIP 一鍵安裝）重跑時，
    推導出的代號若剛好與既有站台相同，代表使用者要覆蓋原本那一站的設定（例如
    換一把新金鑰），而不是要疊出一個 `xxx-2` 的重複站台——去重與否是呼叫端依
    情境決定的事，這裡只單純回答「這個網址看起來該叫什麼名字」。

    參數:
        url: 已正規化的網址。
    回傳:
        符合 `_SITE_NAME_PATTERN` 的代號；可能與既有代號重複。
    """
    host = (urlsplit(url).hostname or "").lower()
    for prefix in _NOISE_PREFIXES:
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    label = host.split(".")[0]
    name = _ILLEGAL.sub("-", label).strip("-")
    if not _SITE_NAME_PATTERN.match(name):
        return _FALLBACK_NAME
    return name


def derive_site_name(url: str, taken: Sequence[str]) -> str:
    """由網址推導一個合法且未被占用的站台代號。

    在 `derive_site_label()` 的基礎上處理去重：與既有代號衝突時依序加
    `-2`、`-3`。供非 bundle 模式的「新增站台」預設值使用——那裡「新增」二字
    本身就代表不該覆蓋既有站台，衝突就該讓使用者換一個代號，而不是覆蓋。

    參數:
        url: 已正規化的網址。
        taken: 參數檔中已存在的代號。
    回傳:
        符合 `_SITE_NAME_PATTERN` 且不在 `taken` 內的代號。
    """
    name = derive_site_label(url)
    if name not in taken:
        return name
    for suffix in range(2, 100):
        candidate = f"{name}-{suffix}"
        if candidate not in taken:
            return candidate
    return _FALLBACK_NAME
