# pycurl 傳輸層 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以 pycurl（executor + handle 池、連線重用）取代每請求 fork 子行程的 `CurlTransport`，同時解決連線無法重用與 OS curl 相依／暫存檔／每請求 fork。

**Architecture:** 新增 `PyCurlTransport`（`httpx.AsyncBaseTransport`），把同步的 `pycurl.Curl.perform()` 丟進 `anyio.to_thread` 執行；介面與例外型別維持不變，故 `RedmineClient` 與所有工具測試零改動。完全取代子行程模組，不留 fallback。

**Tech Stack:** Python 3.11、httpx（`AsyncBaseTransport`）、pycurl（libcurl 綁定）、anyio（`to_thread`）、pytest + pytest-asyncio。

**Spec:** `docs/superpowers/specs/2026-08-19-pycurl-transport-design.md`

## Global Constraints

- 例外型別維持不變且仍繼承 `httpx.TransportError`：`CurlError`、`CurlTimeoutError`、`CurlFileSizeExceeded`。
- 可匯入名稱維持不變（`client.py` 依賴）：`EXT_TOTAL_TIMEOUT = "redmine_total_timeout"`、`EXT_MAX_FILESIZE = "redmine_max_filesize"`。
- 不跟隨轉址（不設 `FOLLOWLOCATION`）、不關 TLS 驗證（`SSL_VERIFYPEER=1`、`SSL_VERIFYHOST=2`）。
- 例外訊息只含 code 對應的中文提示，絕不含 libcurl 原文。
- 跳過轉發的 request header：`host`、`content-length`、`accept-encoding`、`connection`、`transfer-encoding`（小寫比對）。
- 回應 header 丟棄：`content-encoding`、`content-length`（小寫比對）。
- 逾時：`_CONNECT_TIMEOUT_SECONDS = 10.0`、`_TOTAL_TIMEOUT_SECONDS = 60.0`；下載覆寫 `_DOWNLOAD_TOTAL_TIMEOUT_SECONDS = 300.0`（來自 `client.py`，經 `EXT_TOTAL_TIMEOUT` 傳入）。
- 每站一個 `RedmineClient` → 每個 transport 只打單一 host；handle 池預設 4，不引入 `CurlShare`。
- 全程遵守 TDD：先寫失敗測試、看它失敗、最小實作、看它通過、commit。每個 commit 全套測試（`uv run pytest -q`）、`uv run ruff check .`、`uv run mypy` 皆須綠。
- 中文註解規範：新增 helper／公開方法補中文說明，公開欄位補說明。

---

### Task 1: 新模組的穩定介面（例外、extension 常數、error 對應、header 過濾）

建立 `pycurl_transport.py` 的純函式與常數表面 —— 這是 `client.py` 之後會依賴的可匯入面，以及可獨立單元測試的邏輯。此時舊模組仍在、`client.py` 仍用舊模組，全套測試維持綠。

**Files:**
- Create: `src/redmine_mcp/pycurl_transport.py`
- Test: `tests/test_pycurl_transport.py`

**Interfaces:**
- Consumes: 無（第一個任務）。
- Produces:
  - `EXT_TOTAL_TIMEOUT: str`、`EXT_MAX_FILESIZE: str`（值與現行相同）
  - `class CurlError(httpx.TransportError)`、`class CurlTimeoutError(CurlError)`、`class CurlFileSizeExceeded(CurlError)`
  - `_pycurl_failure(code: int) -> CurlError`
  - `_filter_response_headers(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]`
  - `_request_headers(request: httpx.Request) -> list[str]`（回傳 `"Name: value"` 字串清單，已跳過 `_SKIP_REQUEST_HEADERS`、保留原始大小寫）

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_pycurl_transport.py
"""PyCurlTransport 的單元與 loopback 整合測試。"""
from __future__ import annotations

import httpx
import pycurl
import pytest

from redmine_mcp import pycurl_transport as pt
from redmine_mcp.pycurl_transport import (
    CurlError,
    CurlFileSizeExceeded,
    CurlTimeoutError,
)


def test_extension_常數值不變():
    # client.py 以字串鍵傳入 extensions，值改了會靜默失效。
    assert pt.EXT_TOTAL_TIMEOUT == "redmine_total_timeout"
    assert pt.EXT_MAX_FILESIZE == "redmine_max_filesize"


def test_例外型別階層不變():
    assert issubclass(CurlError, httpx.TransportError)
    assert issubclass(CurlTimeoutError, CurlError)
    assert issubclass(CurlFileSizeExceeded, CurlError)


def test_逾時_code_轉成_timeout_例外():
    exc = pt._pycurl_failure(pycurl.E_OPERATION_TIMEDOUT)
    assert isinstance(exc, CurlTimeoutError)


def test_filesize_code_轉成_filesize_例外():
    exc = pt._pycurl_failure(pycurl.E_FILESIZE_EXCEEDED)
    assert isinstance(exc, CurlFileSizeExceeded)


def test_未知_code_轉成通用_CurlError_且不洩漏原文():
    exc = pt._pycurl_failure(999)
    assert isinstance(exc, CurlError)
    assert "999" in str(exc)


def test_回應_header_丟棄_content_encoding_與_length():
    pairs = [("Content-Type", "application/json"), ("Content-Encoding", "gzip"), ("Content-Length", "12")]
    assert pt._filter_response_headers(pairs) == [("Content-Type", "application/json")]


def test_request_header_跳過_host_等並保留大小寫():
    req = httpx.Request(
        "GET", "https://x.example.com/a.json",
        headers={"X-Redmine-API-Key": "k", "Accept": "application/json"},
    )
    lines = pt._request_headers(req)
    assert "X-Redmine-API-Key: k" in lines
    assert "Accept: application/json" in lines
    assert not any(line.lower().startswith("host:") for line in lines)
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_pycurl_transport.py -q`
Expected: FAIL（`ModuleNotFoundError: redmine_mcp.pycurl_transport`）

- [ ] **Step 3: 寫最小實作**

```python
# src/redmine_mcp/pycurl_transport.py
"""以 pycurl（libcurl 綁定）作為 httpx 傳輸層。

取代早期的子行程 curl 傳輸：libcurl 在行程內送出請求，連線可重用，且 API key
經 HTTPHEADER 在行程內傳遞，不進 argv、不落暫存檔。

為什麼是 pycurl 而非 httpx 原生：站台的 Cloudflare WAF 依 TLS ClientHello 指紋
攔截，Python ssl 產生的 ClientHello 會被擋（實測），libcurl 的則放行。詳見
docs/superpowers/specs/2026-08-19-pycurl-transport-design.md。
"""
from __future__ import annotations

import httpx
import pycurl

#: 單次請求可覆寫逾時與大小上限的 `httpx.Request.extensions` 鍵名。
EXT_TOTAL_TIMEOUT = "redmine_total_timeout"
EXT_MAX_FILESIZE = "redmine_max_filesize"

#: 不轉發給 libcurl 的 request header；host/content-length 等由 libcurl 自算。
_SKIP_REQUEST_HEADERS = frozenset(
    {"host", "content-length", "accept-encoding", "connection", "transfer-encoding"}
)

#: 不轉回 httpx 的 response header：libcurl 已解壓縮，保留會讓 httpx 二次解壓。
_SKIP_RESPONSE_HEADERS = frozenset({"content-encoding", "content-length"})

#: libcurl error code → 中文提示；只列值得給使用者不同排查方向的幾個。
_CURL_HINTS = {
    pycurl.E_COULDNT_RESOLVE_HOST: (
        "無法解析主機名稱，請確認該站台參數檔 [sites.<代號>] 的 url 網域與 DNS 設定"
    ),
    pycurl.E_COULDNT_CONNECT: "無法建立連線，請確認網路、防火牆或 proxy 設定",
    pycurl.E_SSL_CONNECT_ERROR: "TLS 交握失敗，請確認站台憑證與 TLS 版本",
    pycurl.E_PEER_FAILED_VERIFICATION: "無法驗證伺服器憑證，請確認站台憑證是否由受信任的 CA 簽發",
}


class CurlError(httpx.TransportError):
    """libcurl 傳輸失敗（連線、DNS、TLS 等），非 HTTP 狀態碼錯誤。

    繼承 httpx.TransportError 讓 RedmineClient 既有的 except httpx.TransportError
    轉譯層能一併涵蓋。訊息只含 code 對應提示，不含 libcurl 原文。
    """


class CurlTimeoutError(CurlError):
    """libcurl 因逾時中止（E_OPERATION_TIMEDOUT）。"""


class CurlFileSizeExceeded(CurlError):
    """回應超過大小上限，libcurl 主動中止（E_FILESIZE_EXCEEDED）。

    這不是連線問題，呼叫端應轉譯成「附件過大」而非「連線失敗」。
    """


def _pycurl_failure(code: int) -> CurlError:
    """把 libcurl error code 轉成對應例外；訊息只含 code 與中文提示。"""
    if code == pycurl.E_OPERATION_TIMEDOUT:
        return CurlTimeoutError("請求逾時（libcurl code 28），請確認網路狀況或站台回應速度")
    if code == pycurl.E_FILESIZE_EXCEEDED:
        return CurlFileSizeExceeded("回應內容超過允許的大小上限，已中止傳輸（libcurl code 63）")
    hint = _CURL_HINTS.get(code)
    suffix = f"：{hint}" if hint else ""
    return CurlError(f"libcurl 執行失敗（code {code}）{suffix}")


def _filter_response_headers(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """丟棄 content-encoding／content-length 之外原樣保留回應 header。"""
    return [(name, value) for name, value in pairs if name.lower() not in _SKIP_RESPONSE_HEADERS]


def _request_headers(request: httpx.Request) -> list[str]:
    """把 request header 轉成 libcurl HTTPHEADER 的 "Name: value" 清單。

    使用 request.headers.raw 保留原始大小寫（如實轉發呼叫端意圖）；跳過由 libcurl
    自算的 host/content-length 等。
    """
    lines: list[str] = []
    for raw_name, raw_value in request.headers.raw:
        name = raw_name.decode("ascii", errors="replace")
        value = raw_value.decode("latin-1", errors="replace")
        if name.lower() in _SKIP_REQUEST_HEADERS:
            continue
        lines.append(f"{name}: {value}")
    return lines
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_pycurl_transport.py -q`
Expected: PASS（7 passed）

- [ ] **Step 5: 全套檢查並 commit**

```bash
uv run ruff check src/redmine_mcp/pycurl_transport.py tests/test_pycurl_transport.py
uv run mypy
git add src/redmine_mcp/pycurl_transport.py tests/test_pycurl_transport.py
git commit -m "feat(redmine-mcp): pycurl 傳輸層的例外、常數與純函式面"
```

---

### Task 2: PyCurlTransport 非串流路徑 + handle 池 + executor

實作 `handle_async_request` 的一般 API 請求路徑（body 全 buffer）、handle 池與 executor 橋接。以 loopback HTTP server 做端到端整合測試。舊模組仍在、`client.py` 仍用舊模組。

**Files:**
- Modify: `src/redmine_mcp/pycurl_transport.py`
- Modify: `tests/test_pycurl_transport.py`

**Interfaces:**
- Consumes: Task 1 的 `_pycurl_failure`、`_filter_response_headers`、`_request_headers`、例外、常數。
- Produces:
  - `class PyCurlTransport(httpx.AsyncBaseTransport)`，建構子
    `PyCurlTransport(connect_timeout: float = 10.0, total_timeout: float = 60.0, pool_size: int = 4)`
  - `async def handle_async_request(self, request: httpx.Request) -> httpx.Response`
  - `async def aclose(self) -> None`（關閉池內所有 handle）
  - `class _HeaderCollector`（供 Task 3 重用）：收集 status_code 與 `(name, value)` header 清單

- [ ] **Step 1: 寫失敗測試**

```python
# 追加到 tests/test_pycurl_transport.py
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from redmine_mcp.pycurl_transport import PyCurlTransport


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _respond(self):
        body = json.dumps({"ok": True, "method": self.command}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Echo-Auth", self.headers.get("X-Redmine-API-Key", ""))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _respond
    do_POST = _respond
    do_PUT = _respond

    def log_message(self, *a):
        pass


@pytest.fixture
def loopback():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


async def test_get_回傳狀態碼與_json_body(loopback):
    async with httpx.AsyncClient(transport=PyCurlTransport()) as c:
        r = await c.get(f"{loopback}/issues.json")
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_request_header_有送到伺服器(loopback):
    async with httpx.AsyncClient(transport=PyCurlTransport()) as c:
        r = await c.get(f"{loopback}/x", headers={"X-Redmine-API-Key": "secret-k"})
    # 伺服器把收到的金鑰回聲到 X-Echo-Auth，證明 header 有經 libcurl 送出。
    assert r.headers["X-Echo-Auth"] == "secret-k"


async def test_post_body_有送達(loopback):
    async with httpx.AsyncClient(transport=PyCurlTransport()) as c:
        r = await c.post(f"{loopback}/x", content=b'{"a":1}')
    assert r.json()["method"] == "POST"


async def test_連線被拒轉成_CurlError(loopback):
    # 連一個關閉的 port：127.0.0.1:1 幾乎不可能有服務。
    async with httpx.AsyncClient(transport=PyCurlTransport(connect_timeout=2)) as c:
        with pytest.raises(CurlError):
            await c.get("http://127.0.0.1:1/x")


async def test_連線重用同一連線(loopback):
    # 連續兩次請求應重用連線（不拋錯即代表 handle 池運作）。
    async with httpx.AsyncClient(transport=PyCurlTransport(pool_size=1)) as c:
        r1 = await c.get(f"{loopback}/a")
        r2 = await c.get(f"{loopback}/b")
    assert r1.status_code == r2.status_code == 200
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_pycurl_transport.py -q -k "loopback or 回傳狀態 or header_有送 or post_body or 連線"`
Expected: FAIL（`PyCurlTransport` 未定義）

- [ ] **Step 3: 寫最小實作**

```python
# 追加到 src/redmine_mcp/pycurl_transport.py
import asyncio
from io import BytesIO

import anyio


class _HeaderCollector:
    """收集 libcurl HEADERFUNCTION 吐出的 header 行，解析出狀態碼與 header 清單。

    libcurl 對每一行 header（含狀態列 "HTTP/x.y NNN ..."）呼叫一次；空行代表
    header 區塊結束。轉址已停用，故只會有一組。
    """

    def __init__(self) -> None:
        self.status_code = 0
        self.pairs: list[tuple[str, str]] = []

    def feed(self, line: bytes) -> None:
        text = line.decode("latin-1", errors="replace").strip("\r\n")
        if not text:
            return
        if text.startswith("HTTP/"):
            # "HTTP/1.1 200 OK" → 200；中介回應（如 100）會被後一組覆蓋。
            parts = text.split(" ", 2)
            if len(parts) >= 2 and parts[1].isdigit():
                self.status_code = int(parts[1])
                self.pairs = []
            return
        name, sep, value = text.partition(":")
        if sep:
            self.pairs.append((name.strip(), value.strip()))


def _new_handle() -> pycurl.Curl:
    """建立一個帶固定安全選項的 Curl handle。"""
    h = pycurl.Curl()
    h.setopt(pycurl.SSL_VERIFYPEER, 1)
    h.setopt(pycurl.SSL_VERIFYHOST, 2)
    h.setopt(pycurl.NOSIGNAL, 1)  # 多執行緒下必要，避免 libcurl 用 signal 計時
    # 刻意不設 FOLLOWLOCATION（不跟隨轉址）、不關 TLS 驗證。
    return h


class PyCurlTransport(httpx.AsyncBaseTransport):
    """以 pycurl 送出 HTTP 請求的 httpx 傳輸層。

    perform() 是同步阻塞呼叫，透過 anyio.to_thread 執行以不阻塞事件迴圈。每個
    transport 只對單一 host 說話（RedmineClient 每站一個），故 handle 池內每個
    handle 自己的連線快取即可保活該站連線，無需 CurlShare。
    """

    def __init__(
        self,
        connect_timeout: float = 10.0,
        total_timeout: float = 60.0,
        pool_size: int = 4,
    ) -> None:
        self._connect_timeout = connect_timeout
        self._total_timeout = total_timeout
        self._pool: asyncio.LifoQueue[pycurl.Curl] = asyncio.LifoQueue()
        for _ in range(pool_size):
            self._pool.put_nowait(_new_handle())

    def _total_timeout_for(self, request: httpx.Request) -> float:
        override = request.extensions.get(EXT_TOTAL_TIMEOUT)
        if isinstance(override, (int, float)) and override > 0:
            return float(override)
        return self._total_timeout

    def _configure(self, h: pycurl.Curl, request: httpx.Request, body: bytes) -> None:
        """把 httpx.Request 的 URL、method、header、body、逾時設上 handle。"""
        h.setopt(pycurl.URL, str(request.url))
        h.setopt(pycurl.CUSTOMREQUEST, request.method)
        h.setopt(pycurl.HTTPHEADER, _request_headers(request))
        h.setopt(pycurl.CONNECTTIMEOUT, int(self._connect_timeout))
        h.setopt(pycurl.TIMEOUT, int(self._total_timeout_for(request)))
        if body:
            h.setopt(pycurl.POSTFIELDS, body)
        max_filesize = request.extensions.get(EXT_MAX_FILESIZE)
        if max_filesize:
            # 下載：依 Content-Length 提前擋；且不協商壓縮，讓上限比對未壓縮位元組。
            h.setopt(pycurl.MAXFILESIZE_LARGE, int(max_filesize))
        else:
            # 一般 API：協商壓縮，libcurl 自行解壓。空字串 = 接受所有支援的編碼。
            h.setopt(pycurl.ACCEPT_ENCODING, "")

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        if request.extensions.get(EXT_MAX_FILESIZE):
            return await self._streaming_request(request, body)  # Task 3 實作
        return await self._buffered_request(request, body)

    async def _buffered_request(self, request: httpx.Request, body: bytes) -> httpx.Response:
        """一般 API 請求：body 全 buffer，perform() 跑完才回。"""
        h = await self._pool.get()
        try:
            collector = _HeaderCollector()
            buf = BytesIO()

            def run() -> None:
                h.reset()
                # reset 會清掉安全選項，重設之。
                h.setopt(pycurl.SSL_VERIFYPEER, 1)
                h.setopt(pycurl.SSL_VERIFYHOST, 2)
                h.setopt(pycurl.NOSIGNAL, 1)
                self._configure(h, request, body)
                h.setopt(pycurl.HEADERFUNCTION, collector.feed)
                h.setopt(pycurl.WRITEFUNCTION, buf.write)
                h.perform()

            try:
                await anyio.to_thread.run_sync(run)
            except pycurl.error as exc:
                raise _pycurl_failure(exc.args[0]) from exc

            return httpx.Response(
                collector.status_code,
                headers=_filter_response_headers(collector.pairs),
                content=buf.getvalue(),
                request=request,
            )
        finally:
            self._pool.put_nowait(h)

    async def _streaming_request(self, request: httpx.Request, body: bytes) -> httpx.Response:
        raise NotImplementedError  # Task 3

    async def aclose(self) -> None:
        """關閉池內所有 handle。"""
        while not self._pool.empty():
            self._pool.get_nowait().close()
```

註：`h.reset()` 後選項會被清空，故 `run()` 內重設安全選項。`_new_handle()` 的選項是首次借用前的預設，實務上每次 `run()` 都重設，因此 `_new_handle` 可簡化為只 `pycurl.Curl()`；保留重設在 `run()` 內即可。實作時擇一，勿兩處都設造成混淆。

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_pycurl_transport.py -q`
Expected: PASS（Task 1 的 7 個 + 本任務 5 個）

- [ ] **Step 5: 全套檢查並 commit**

```bash
uv run ruff check src/redmine_mcp/pycurl_transport.py tests/test_pycurl_transport.py
uv run mypy
git add src/redmine_mcp/pycurl_transport.py tests/test_pycurl_transport.py
git commit -m "feat(redmine-mcp): PyCurlTransport 非串流路徑與 handle 池"
```

---

### Task 3: 串流下載路徑（有界佇列 + headers-first + MAXFILESIZE）

實作 `_streaming_request`：背景執行緒跑 `perform()`，`WRITEFUNCTION` 把 chunk 推進有界佇列（背壓），`HEADERFUNCTION` 收完 header 後通知主端組出 `httpx.Response`，`aiter_bytes()` 逐塊取。

**Files:**
- Modify: `src/redmine_mcp/pycurl_transport.py`
- Modify: `tests/test_pycurl_transport.py`

**Interfaces:**
- Consumes: Task 2 的 `_HeaderCollector`、`_configure`、`_pycurl_failure`、`_filter_response_headers`、池。
- Produces: `_streaming_request` 的完整實作；`class _QueueByteStream(httpx.AsyncByteStream)`。

- [ ] **Step 1: 寫失敗測試**

```python
# 追加到 tests/test_pycurl_transport.py
from redmine_mcp.pycurl_transport import EXT_MAX_FILESIZE, EXT_TOTAL_TIMEOUT


class _BigHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    payload = b"x" * (256 * 1024)  # 256KB

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *a):
        pass


@pytest.fixture
def big_loopback():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _BigHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


async def test_串流下載能完整取回內容(big_loopback):
    async with httpx.AsyncClient(transport=PyCurlTransport()) as c:
        async with c.stream(
            "GET", f"{big_loopback}/f",
            extensions={EXT_MAX_FILESIZE: 10 * 1024 * 1024},
        ) as r:
            chunks = [chunk async for chunk in r.aiter_bytes()]
    assert sum(len(x) for x in chunks) == 256 * 1024


async def test_串流下載超過_Content_Length_上限被擋(big_loopback):
    # 上限 1KB，但 Content-Length 是 256KB → libcurl 應以 E_FILESIZE_EXCEEDED 中止。
    async with httpx.AsyncClient(transport=PyCurlTransport()) as c:
        with pytest.raises(CurlFileSizeExceeded):
            async with c.stream(
                "GET", f"{big_loopback}/f",
                extensions={EXT_MAX_FILESIZE: 1024},
            ) as r:
                async for _ in r.aiter_bytes():
                    pass
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_pycurl_transport.py -q -k "串流"`
Expected: FAIL（`NotImplementedError`）

- [ ] **Step 3: 寫最小實作**

```python
# src/redmine_mcp/pycurl_transport.py：新增 import
import queue
from collections.abc import AsyncIterator

# 佇列結束哨符。
_SENTINEL = object()


class _QueueByteStream(httpx.AsyncByteStream):
    """從有界佇列逐塊取出下載 body 的 httpx stream。

    生產者（executor 執行緒裡的 perform()）把 chunk put 進佇列，滿了就阻塞形成
    背壓；完成推 _SENTINEL；失敗推例外物件。消費端 aiter 逐塊取，遇例外即拋出。
    唯一消費者是 client.stream_to_file，它會把流讀到底或因超過上限中止。
    """

    def __init__(self, q: "queue.Queue[object]", release) -> None:
        self._q = q
        self._release = release  # 讀完/關閉時歸還 handle 的 callback

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            while True:
                item = await anyio.to_thread.run_sync(self._q.get)
                if item is _SENTINEL:
                    return
                if isinstance(item, pycurl.error):
                    raise _pycurl_failure(item.args[0]) from item
                yield item  # type: ignore[misc]
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        self._release()


# 取代 Task 2 的佔位 _streaming_request：
    async def _streaming_request(self, request: httpx.Request, body: bytes) -> httpx.Response:
        h = await self._pool.get()
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                self._pool.put_nowait(h)

        collector = _HeaderCollector()
        q: "queue.Queue[object]" = queue.Queue(maxsize=16)
        headers_done = anyio.Event()
        loop = asyncio.get_running_loop()

        def on_header(line: bytes) -> None:
            collector.feed(line)
            if line in (b"\r\n", b"\n"):  # header 區塊結束
                loop.call_soon_threadsafe(headers_done.set)

        def run() -> None:
            try:
                h.reset()
                h.setopt(pycurl.SSL_VERIFYPEER, 1)
                h.setopt(pycurl.SSL_VERIFYHOST, 2)
                h.setopt(pycurl.NOSIGNAL, 1)
                self._configure(h, request, body)
                h.setopt(pycurl.HEADERFUNCTION, on_header)
                h.setopt(pycurl.WRITEFUNCTION, q.put)  # 滿了阻塞 → 背壓
                h.perform()
                q.put(_SENTINEL)
            except pycurl.error as exc:
                q.put(exc)
            finally:
                loop.call_soon_threadsafe(headers_done.set)  # 錯誤時也解除等待

        # 在背景執行 perform；不 await 它，讓 body 邊下載邊被消費。
        perform_task = asyncio.ensure_future(anyio.to_thread.run_sync(run))
        await headers_done.wait()

        # perform 若在 header 前就失敗，佇列首個項目會是例外，交給 stream 拋出。
        return httpx.Response(
            collector.status_code or 200,
            headers=_filter_response_headers(collector.pairs),
            stream=_QueueByteStream(q, release),
            request=request,
            extensions={"perform_task": perform_task},
        )
```

註：`perform_task` 放進 `extensions` 只為保留參照避免被 GC；不需外部使用。`q.put` 作為 `WRITEFUNCTION` 時，libcurl 傳入 bytes、`Queue.put` 回傳 None → libcurl 視為「已消費全部位元組」，正確。若要在 body 階段就中止（本設計不需要），才需回傳長度。

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_pycurl_transport.py -q`
Expected: PASS（全部）。若串流測試 flaky（headers_done 競態），檢查 `on_header` 對空行的比對與 `call_soon_threadsafe` 是否正確排入迴圈。

- [ ] **Step 5: 全套檢查並 commit**

```bash
uv run ruff check src/redmine_mcp/pycurl_transport.py tests/test_pycurl_transport.py
uv run mypy
git add src/redmine_mcp/pycurl_transport.py tests/test_pycurl_transport.py
git commit -m "feat(redmine-mcp): PyCurlTransport 串流下載路徑與大小上限"
```

---

### Task 4: 切換 client.py 使用 PyCurlTransport

把 `RedmineClient` 的預設傳輸層從 `CurlTransport` 換成 `PyCurlTransport`。工具測試靠注入 `MockTransport`，不受影響 → 全套維持綠。

**Files:**
- Modify: `src/redmine_mcp/client.py:12-17`（import）、`:137-144`（transport 建構）、`:124-127`（docstring）

**Interfaces:**
- Consumes: `PyCurlTransport`、`EXT_MAX_FILESIZE`、`EXT_TOTAL_TIMEOUT`、`CurlFileSizeExceeded`（來自 `pycurl_transport`）。

- [ ] **Step 1: 改 import**

把 `client.py` 頂部的
```python
from redmine_mcp.curl_transport import (
    EXT_MAX_FILESIZE,
    EXT_TOTAL_TIMEOUT,
    CurlFileSizeExceeded,
    CurlTransport,
)
```
改成
```python
from redmine_mcp.pycurl_transport import (
    EXT_MAX_FILESIZE,
    EXT_TOTAL_TIMEOUT,
    CurlFileSizeExceeded,
    PyCurlTransport,
)
```

- [ ] **Step 2: 改預設 transport 建構**

把 `__init__` 中的
```python
            else CurlTransport(
                connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                total_timeout=_TOTAL_TIMEOUT_SECONDS,
            ),
```
改成
```python
            else PyCurlTransport(
                connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                total_timeout=_TOTAL_TIMEOUT_SECONDS,
            ),
```
並把該處 docstring 對「CurlTransport／curl 執行檔」的描述改為「PyCurlTransport（以 libcurl 送出，繞過 WAF 的 TLS 指紋偵測，詳見 pycurl_transport.py 模組說明）」。

- [ ] **Step 3: 執行全套測試確認通過**

Run: `uv run pytest -q`
Expected: PASS（工具測試走 MockTransport，不受影響；傳輸層測試走 pycurl_transport）

- [ ] **Step 4: 全套檢查並 commit**

```bash
uv run ruff check .
uv run mypy
git add src/redmine_mcp/client.py
git commit -m "refactor(redmine-mcp): RedmineClient 改用 PyCurlTransport"
```

---

### Task 5: 刪除子行程模組與相關程式碼

移除 `curl_transport.py`、其測試、`ensure_curl_available`／`resolve_curl_path` 與其在 `__main__.py` 的呼叫及 packaging 測試。

**Files:**
- Delete: `src/redmine_mcp/curl_transport.py`
- Delete: `tests/test_curl_transport.py`
- Modify: `src/redmine_mcp/__main__.py`（移除 `ensure_curl_available` 定義、其呼叫、`resolve_curl_path` import、未用的 `shutil` import）
- Modify: `tests/test_packaging.py`（移除 `ensure_curl_available` 相關測試與 import）

- [ ] **Step 1: 移除 __main__.py 的 curl 檢查**

刪除 `ensure_curl_available` 函式定義、`main()` 中的 `ensure_curl_available()` 呼叫、`from redmine_mcp.curl_transport import resolve_curl_path`，以及若因此未使用的 `import shutil`。

- [ ] **Step 2: 移除 packaging 測試中的 curl 檢查**

在 `tests/test_packaging.py` 刪除 `from redmine_mcp.__main__ import ensure_curl_available`（若僅此用途）與所有名稱含 `curl` 的測試函式（`test_找不到_curl_時結束並提示安裝`、`test_找得到_curl_時不中斷`、`test_啟動檢查與傳輸層用同一套解析`）。

- [ ] **Step 3: 刪除舊模組與其測試**

```bash
git rm src/redmine_mcp/curl_transport.py tests/test_curl_transport.py
```

- [ ] **Step 4: 確認無殘留參照**

Run: `uv run python - <<'PY'
import subprocess
out = subprocess.run(["git","grep","-n","curl_transport\\|CurlTransport\\|ensure_curl_available\\|resolve_curl_path","--","*.py"],capture_output=True,text=True).stdout
print(out or "無殘留參照")
PY`
Expected: 無殘留參照（`git grep` 無輸出）

- [ ] **Step 5: 執行全套測試與檢查**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: 全 PASS、無 lint／型別錯誤

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(redmine-mcp): 移除子行程 curl 傳輸層與 curl 前置檢查"
```

---

### Task 6: 打包、文件與版號

把 pycurl 加入相依、移除 curl 為 OS 前置需求的說法、更新文件、升版。

**Files:**
- Modify: `pyproject.toml`（dependencies 加 pycurl、移除 curl OS-dep 註記、version）
- Modify: `uv.lock`（`uv lock` 產生）
- Modify: `README.md`、`SETUP.md`（移除 curl 前置需求、curl_transport.py 參照）

- [ ] **Step 1: 加入 pycurl 相依並移除 curl 註記**

在 `pyproject.toml` 的 `dependencies` 加入 `"pycurl>=7.45,<8"`（下界取目前 PyPI wheel 廣泛涵蓋的版本，上界擋 major）。刪除檔案中「HTTP 請求經由系統 curl 執行檔送出／curl 是作業系統層級的相依」那段註解。

- [ ] **Step 2: 升版**

把 `pyproject.toml` 的 `version` 由 `0.7.0` 改為 `0.8.0`。

- [ ] **Step 3: 更新 lock 並驗證版本一致性測試**

Run: `uv lock && uv run pytest tests/test_packaging.py -q`
Expected: PASS（`test_三處版本宣告皆來自同一來源` 等仍綠）

- [ ] **Step 4: 更新文件**

在 `README.md` 與 `SETUP.md` 移除「curl（Windows 10/11 內建）為前置需求」的說法，改為「HTTP 請求經由隨套件安裝的 pycurl（libcurl）送出，使用者無須另裝 curl」；把指向 `src/redmine_mcp/curl_transport.py` 的參照改為 `src/redmine_mcp/pycurl_transport.py`。

- [ ] **Step 5: 全套檢查並 commit**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy
git add pyproject.toml uv.lock README.md SETUP.md
git commit -m "chore(redmine-mcp): 相依改用 pycurl、升版 0.8.0、更新安裝文件"
```

---

### Task 7: Windows rollout 驗證（手動，非 CI）

CI 只跑 ubuntu；主要部署是 Windows。方案 A 事件迴圈無關，風險低，但須一次真實驗證。此任務不改碼，產出是一份驗證紀錄。

**Files:**
- 無（手動驗證；結果記入 PR 說明或 commit 訊息）

- [ ] **Step 1: 在乾淨 Windows 環境安裝**

Run: `uv tool install --from "git+https://github.com/Shinspire/MCP.git#subdirectory=redmine-mcp" redmine-mcp`（或依 README 的實際安裝指令）
Expected: pycurl 解析到 wheel 安裝成功（非退回原始碼編譯）；安裝日誌出現 `pycurl==...`（wheel）。

- [ ] **Step 2: 啟動 server 冒煙測試**

Run: `redmine-mcp`（或既有的 smoke test 指令），對真實站台跑一次 `get_current_user`／`list_projects`。
Expected: 在 Windows ProactorEventLoop 下正常回應，無 `add_reader`／event loop 相關錯誤，且能穿過站台 WAF（HTTP 200／401，非 403 challenge）。

- [ ] **Step 3: 記錄結果**

把安裝與冒煙結果記入 PR 說明。若 pycurl 在目標 Python 版本無 wheel 而退回編譯失敗，停止 rollout 並回報 —— 此時需重新評估 spec 的「完全取代不留退路」決策。

---

## Self-Review

**1. Spec coverage：**
- §1 架構與可匯入名稱 → Task 1（例外／常數）、Task 2（PyCurlTransport）、Task 4（切換）。
- §2 行為契約（header 大小寫、跳過集合、TLS、逾時、壓縮、body 串流、filesize、錯誤不洩漏）→ Task 1（header 過濾、錯誤對應）、Task 2（`_configure` 的 TLS／逾時／壓縮／POSTFIELDS、header 送出測試）、Task 3（串流、filesize）。
- §3 handle 池、單 host、不用 CurlShare → Task 2（池、`_new_handle`）。
- §4 混合策略（buffer vs 佇列串流、背壓、headers-first）→ Task 2（buffer）、Task 3（佇列串流）。
- §5 error 對應表 → Task 1（`_pycurl_failure` + `_CURL_HINTS`）。
- §6 測試策略（純函式 + loopback，工具測試不受影響）→ Task 1（純函式）、Task 2／3（loopback）、Task 4（全套綠證明工具測試不受影響）。
- §7 打包／清理／文件／CI 缺口 → Task 5（清理）、Task 6（打包／文件／版號）、Task 7（Windows rollout）。
- 明確不做（CurlShare／fallback／windows CI／HTTP3）→ 計畫未納入，一致。

**2. Placeholder scan：** 無 TBD／TODO；每個 code step 有具體程式碼；Task 3 的串流實作完整給出。Task 2 對 `_streaming_request` 的 `NotImplementedError` 佔位是刻意的階段性狀態，Task 3 Step 3 明確取代它。

**3. Type consistency：** `PyCurlTransport`、`handle_async_request`、`_HeaderCollector`、`_pycurl_failure`、`_filter_response_headers`、`_request_headers`、`_configure`、`_streaming_request`、`_buffered_request`、`_QueueByteStream`、`EXT_TOTAL_TIMEOUT`／`EXT_MAX_FILESIZE`、例外三型別 —— 跨任務名稱一致。`client.py` 匯入的 `CurlFileSizeExceeded`／`PyCurlTransport`／兩個 EXT 常數皆在 Task 1／2 定義。

## Execution Handoff

計畫已存到 `docs/superpowers/plans/2026-08-19-pycurl-transport.md`。
