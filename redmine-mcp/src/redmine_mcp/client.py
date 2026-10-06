"""Redmine REST API 的 HTTP 封裝，集中處理認證與錯誤轉譯。"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import httpx

from redmine_mcp import DIST_NAME, __version__
from redmine_mcp.config import SiteSettings
from redmine_mcp.errors import (
    RedmineAuthError,
    RedmineConnectionError,
    RedmineNotFoundError,
    RedmineServerError,
    RedmineValidationError,
)
from redmine_mcp.formatting import neutralize_untrusted_markers
from redmine_mcp.pycurl_transport import (
    EXT_MAX_FILESIZE,
    EXT_TOTAL_TIMEOUT,
    CurlFileSizeExceeded,
    PyCurlTransport,
)

MAX_LIMIT = 100
DEFAULT_LIMIT = 25

#: 跨站台查詢時所有站台加總的列數上限。
#:
#: limit 的語意是「每站各取 N 筆」而非全域 N 筆，因此 8 站 × 100 筆會是 800 列；
#: 以每列約 180 字（含不可信圍籬的固定開銷）估算約 100K token，單次呼叫就吃掉
#: 半個 context。這道上限只在扇出時生效，單站查詢完全不受影響。
MAX_TOTAL_ROWS = 200

#: 對外表明身分的 User-Agent。httpx 預設的 `python-httpx/x.y` 常被 WAF（例如
#: Cloudflare）當成自動化流量而擋下挑戰頁，導致所有請求收到 403 HTML 而非 API 回應。
#: 這裡誠實標示工具名稱與版本，不偽裝成瀏覽器。版本取自套件 metadata，不另寫一份。
USER_AGENT = f"{DIST_NAME}/{__version__}"

#: 逾時設定的單一來源。實際把關的是 `PyCurlTransport`（正式路徑一律經由 libcurl 送出，
#: httpx 自己的 timeout 在自訂 transport 下不會生效），這裡用同一組數值同時設定
#: httpx 與 transport，避免「httpx 寫 30 秒、curl 寫 60 秒」這種兩邊各說一套的狀況；
#: 測試注入 MockTransport 時走的則是 httpx 這組。
_CONNECT_TIMEOUT_SECONDS = 10.0
#: 一般 API 呼叫（GET／POST／PUT）的整體逾時。
_TOTAL_TIMEOUT_SECONDS = 60.0
#: 附件下載的整體逾時；下載可能傳輸數 MB，比一般 API 呼叫需要更多時間。
_DOWNLOAD_TOTAL_TIMEOUT_SECONDS = 300.0

_TIMEOUT = httpx.Timeout(
    connect=_CONNECT_TIMEOUT_SECONDS,
    read=_TOTAL_TIMEOUT_SECONDS,
    write=_TOTAL_TIMEOUT_SECONDS,
    pool=_CONNECT_TIMEOUT_SECONDS,
)


def clamp_limit(limit: int | None) -> int:
    """把分頁 limit 夾在 Redmine 允許的 1–100 範圍內，未指定時回傳預設值。"""
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), MAX_LIMIT))


def clamp_limit_for_fanout(limit: int | None, site_count: int) -> int:
    """在 `clamp_limit` 之上，再依站台數把每站筆數壓到全域總量之內。

    只往下調、不往上調：呼叫端要 5 筆就是 5 筆，站台少不代表它想要更多。
    均分後至少保留 1 筆，否則查詢等同無效。

    參數:
        limit: 呼叫端指定的每站筆數；None 代表使用預設值。
        site_count: 這次會實際查詢的站台數。
    """
    per_site = clamp_limit(limit)
    if site_count <= 1:
        return per_site
    # 無條件進位：3 站時 200/3 = 66.7，取 67 讓總量貼近上限而非低於它。
    allowance = max(1, -(-MAX_TOTAL_ROWS // site_count))
    return min(per_site, allowance)


def _clean_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """剔除值為 None 的查詢參數，避免送出 `foo=None`。"""
    if not params:
        return {}
    return {k: v for k, v in params.items() if v is not None}


def _wrap_transport_error(exc: httpx.TransportError) -> RedmineConnectionError:
    """把 httpx 傳輸層例外（含 `CurlError` 系列）統一轉成 RedmineConnectionError。

    會把原始例外訊息附在後面：`CurlError` 的訊息已在 pycurl_transport 內過濾成
    「exit code + 中文提示」（不含 stderr 原文、不含任何 header），httpx 自身的
    傳輸例外訊息則只有 errno 與網址，兩者都不含 API key，可安全呈現給使用者，
    否則所有連線失敗都只會看到同一句無從排查的通用訊息。

    只負責組出例外物件，由呼叫端自行 `raise ... from exc`，
    收尾動作（例如刪除半成品檔案）仍留在各自呼叫處處理。
    """
    base = (
        "連線 Redmine 失敗，請確認該站台參數檔 [sites.<代號>] 的 url 設定與網路狀態"
        "（只設定單一站台時亦可用環境變數 REDMINE_URL）"
    )
    detail = str(exc).strip()
    return RedmineConnectionError(f"{base}（{detail}）" if detail else base)


class RedmineClient:
    """封裝 Redmine REST API 呼叫。

    所有請求都帶 X-Redmine-API-Key header，不跟隨轉址，不關閉 TLS 驗證。
    """

    def __init__(
        self, settings: SiteSettings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """建立 client。

        參數:
            settings: 單一站台的位址與金鑰等設定。
            transport: 測試用的替代傳輸層；正式執行時留空，會改用 `PyCurlTransport`
                （以 libcurl 送出，繞過 WAF 的 TLS 指紋偵測，詳見 pycurl_transport.py 模組說明）。
        """
        self.settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.url,
            headers={
                "X-Redmine-API-Key": settings.api_key,
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=_TIMEOUT,
            follow_redirects=False,
            transport=transport
            if transport is not None
            else PyCurlTransport(
                connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                total_timeout=_TOTAL_TIMEOUT_SECONDS,
            ),
        )

    async def aclose(self) -> None:
        """關閉底層連線池。"""
        await self._http.aclose()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """發出 GET 請求並回傳解析後的 JSON。"""
        response = await self._send("GET", path, params=_clean_params(params))
        return self._parse_json(response)

    async def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """發出 POST 請求並回傳解析後的 JSON。"""
        response = await self._send("POST", path, json=payload)
        return self._parse_json(response)

    async def post_binary(
        self, path: str, data: bytes, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """以 application/octet-stream 上傳原始位元組並回傳解析後的 JSON。

        Redmine 的 `/uploads.json` 不吃 multipart，只接受原始 body，
        檔名透過 query string 的 `filename` 傳遞。

        參數:
            path: API 路徑，例如 `/uploads.json`。
            data: 要上傳的檔案內容。
            params: query 參數，例如 `{"filename": "a.png"}`。
        """
        response = await self._send(
            "POST",
            path,
            params=_clean_params(params),
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        return self._parse_json(response)

    async def put(self, path: str, payload: dict[str, Any]) -> None:
        """發出 PUT 請求；Redmine 成功時回 204 無內容。"""
        await self._send("PUT", path, json=payload)
        return None

    async def stream_to_file(self, url: str, dest: Path, max_bytes: int) -> int:
        """串流下載至指定路徑，超過上限即中止並清除半成品，不影響既有同名檔案。

        因為附件下載路徑帶有 attachment id 前綴，重複下載同一附件永遠會打到同一個
        `dest`；若下載失敗時直接刪除 `dest`，會連同「先前已成功下載、非本次寫入」
        的檔案一併刪掉（例如使用者重試下載時剛好遇到 Redmine 5xx）。
        因此一律先寫到與 dest 同目錄的暫存檔，只有整個下載成功才用 os.replace
        原子性地覆蓋到 dest；任何失敗路徑都只刪暫存檔，dest 本身完全不受影響。

        大小上限有兩道防線：一是把 max_bytes 交給傳輸層（libcurl 的
        MAXFILESIZE），依 Content-Length 提前擋下；二是這裡逐塊累加把關。
        body 全程以有界佇列串流、邊下載邊消費、不落任何暫存檔；第二道防線保護的
        是記憶體用量，並涵蓋伺服器未提供或少報 Content-Length（此時第一道防線
        失效）的情況——這也是帶上限的請求刻意不協商壓縮的原因。

        參數:
            url: 完整下載網址；呼叫端必須先驗證其來源與站台相同。
            dest: 目的檔案路徑；呼叫端必須先確認位於允許的下載目錄內。
            max_bytes: 大小上限（位元組）。

        回傳:
            實際寫入的位元組數。
        """
        written = 0
        completed = False
        tmp_path = dest.with_name(f"{dest.name}.{uuid.uuid4().hex}.part")
        try:
            async with self._http.stream(
                "GET",
                url,
                extensions={
                    EXT_TOTAL_TIMEOUT: _DOWNLOAD_TOTAL_TIMEOUT_SECONDS,
                    EXT_MAX_FILESIZE: max_bytes,
                },
            ) as response:
                self._raise_for_status(response)
                with tmp_path.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        written += len(chunk)
                        if written > max_bytes:
                            raise RedmineServerError(
                                f"附件超過大小上限 {max_bytes} 位元組，已中止下載"
                            )
                        handle.write(chunk)
            completed = True
        except CurlFileSizeExceeded as exc:
            # 傳輸層依 Content-Length 提前擋下；這是「附件過大」而非連線問題，
            # 訊息要與上面逐塊把關的那條路徑一致，避免同一個原因出現兩種說法。
            raise RedmineServerError(
                f"附件超過大小上限 {max_bytes} 位元組，已中止下載"
            ) from exc
        except httpx.TransportError as exc:
            raise _wrap_transport_error(exc) from exc
        finally:
            # 任何失敗路徑（網路中斷、超過上限、本機寫入失敗）都只清掉暫存檔，
            # 絕不觸碰 dest——dest 可能是先前已成功下載、與本次無關的既有檔案。
            if not completed:
                tmp_path.unlink(missing_ok=True)
        try:
            os.replace(tmp_path, dest)
        except OSError:
            # os.replace 本身也可能失敗（例如 dest 被防毒軟體掃描鎖定、或目的
            # 目錄權限問題），此時暫存檔已無用途，須清除避免殘留堆積；
            # 例外原樣往上拋，讓呼叫端知道下載未成功（不包裝成 Redmine 例外，
            # 因為這是本機檔案系統問題，與 Redmine 伺服器無關）。
            tmp_path.unlink(missing_ok=True)
            raise
        return written

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """送出請求並套用統一的錯誤轉譯。"""
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise _wrap_transport_error(exc) from exc
        self._raise_for_status(response)
        return response

    def _raise_for_status(self, response: httpx.Response) -> None:
        """把 HTTP 狀態碼轉成具語意的例外，訊息不含任何憑證資訊。"""
        status = response.status_code
        if status < 300:
            return
        if 300 <= status < 400:
            raise RedmineServerError(
                f"Redmine 回應非預期的轉址（HTTP {status}），基於安全考量不跟隨"
            )
        if status in (401, 403):
            raise RedmineAuthError(
                f"API key 無效或權限不足（HTTP {status}），請確認該站台參數檔 [sites.<代號>] 的 "
                "api_key 與該帳號權限（只設定單一站台時亦可用環境變數 REDMINE_API_KEY）"
                + self._non_json_hint(response)
            )
        if status == 404:
            raise RedmineNotFoundError(
                f"找不到指定資源（HTTP 404）：{response.request.url.path}"
                + self._non_json_hint(response)
            )
        if status == 422:
            raise RedmineValidationError(
                "Redmine 驗證失敗（HTTP 422）：" + "；".join(self._extract_errors(response))
            )
        raise RedmineServerError(f"Redmine 伺服器錯誤（HTTP {status}），請稍後再試")

    @staticmethod
    def _non_json_hint(response: httpx.Response) -> str:
        """回應不是 JSON 時補一句提示。

        Redmine 的 API 錯誤一律回 JSON；若收到 HTML，代表請求根本沒到達 Redmine
        （常見於 WAF／反向代理攔截，或 REDMINE_URL 少了子路徑），此時把矛頭指向
        API key 會誤導排查方向。
        """
        content_type = response.headers.get("content-type", "")
        if "json" in content_type.lower():
            return ""
        return (
            "。注意：回應不是 JSON 而是 "
            f"{content_type.split(';')[0] or '未知型態'}，"
            "代表請求可能被 WAF／反向代理攔截，或該站台參數檔 [sites.<代號>] 的 url 路徑不正確"
            "（例如 Redmine 架在 /redmine 子路徑下卻只填了網域根位址；"
            "只設定單一站台時亦可用環境變數 REDMINE_URL）"
        )

    @staticmethod
    def _extract_errors(response: httpx.Response) -> list[str]:
        """從 422 回應中取出 errors 陣列，解析失敗時回傳通用訊息。

        errors 的內容由 Redmine 依使用者填寫的值產生（自訂欄位「XXX 已經被使用」會
        帶原值、外掛自訂 validator 更是任意文字），而這個字串會經 sites.fan_out 的
        errors 或寫入工具的 verified.error 進入模型 context。因此一律先中和圍籬
        標記：與 formatting 的正常路徑是同一個威脅模型，錯誤路徑不能是例外。
        中和而非包圍籬，理由與 flatten_ref 一致——錯誤訊息需要能被直接閱讀比對。
        """
        try:
            payload = response.json()
        except ValueError:
            return ["無法解析錯誤內容"]
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if isinstance(errors, list) and errors:
            return [neutralize_untrusted_markers(str(item)) for item in errors]
        return ["未提供詳細原因"]

    @staticmethod
    def _parse_json(response: httpx.Response) -> dict[str, Any]:
        """解析 JSON body；無內容時回傳空字典。"""
        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise RedmineServerError("Redmine 回應不是合法的 JSON") from exc
        return payload if isinstance(payload, dict) else {"data": payload}
