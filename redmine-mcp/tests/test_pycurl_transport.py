"""PyCurlTransport 的單元與 loopback 整合測試。"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pycurl
import pytest

from redmine_mcp import pycurl_transport as pt
from redmine_mcp.pycurl_transport import (
    EXT_MAX_FILESIZE,
    CurlError,
    CurlFileSizeExceeded,
    CurlTimeoutError,
    PyCurlTransport,
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


@pytest.mark.parametrize(
    ("code", "hint_keyword"),
    [
        (pycurl.E_COULDNT_RESOLVE_HOST, "無法解析主機名稱"),
        (pycurl.E_COULDNT_CONNECT, "無法建立連線"),
        (pycurl.E_SSL_CONNECT_ERROR, "TLS"),
        (pycurl.E_PEER_FAILED_VERIFICATION, "憑證"),
    ],
)
def test_已知_hint_code_轉成通用_CurlError_且含中文提示(code: int, hint_keyword: str) -> None:
    exc = pt._pycurl_failure(code)
    # 這四個 code 只是給更明確的排查方向，仍是通用連線類錯誤，
    # 不應被誤判為逾時或檔案過大這兩種有專屬處理邏輯的子類。
    assert isinstance(exc, CurlError)
    assert not isinstance(exc, CurlTimeoutError)
    assert not isinstance(exc, CurlFileSizeExceeded)
    assert hint_keyword in str(exc)


def test_回應_header_丟棄_content_encoding_與_length():
    pairs = [
        (b"Content-Type", b"application/json"),
        (b"Content-Encoding", b"gzip"),
        (b"Content-Length", b"12"),
    ]
    assert pt._filter_response_headers(pairs) == [(b"Content-Type", b"application/json")]


def test_request_header_跳過_host_等並保留大小寫():
    req = httpx.Request(
        "GET", "https://x.example.com/a.json",
        headers={"X-Redmine-API-Key": "k", "Accept": "application/json"},
    )
    lines = pt._request_headers(req)
    assert "X-Redmine-API-Key: k" in lines
    assert "Accept: application/json" in lines
    assert not any(line.lower().startswith("host:") for line in lines)


def test_request_header_名稱含非_ascii_位元組時直接爆炸而非靜默置換():
    # HTTP header 名稱理論上必為 ASCII；若出現非法位元組，應盡早拋例外，
    # 不可用 errors="replace" 悄悄置換成 U+FFFD 後送給 libcurl。
    headers = httpx.Headers([(b"X-Bad-\xff-Header", b"v")])
    req = httpx.Request("GET", "https://x.example.com/a.json", headers=headers)
    with pytest.raises(UnicodeDecodeError):
        pt._request_headers(req)


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


class _AbortHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # 4MB：遠超佇列容量（16 slots），確保消費端提早中止讀取時，背景 perform()
    # 執行緒必定還卡在 WRITEFUNCTION 的 q.put()（佇列已滿、無人再消費）。
    payload = b"x" * (4 * 1024 * 1024)

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *a):
        pass


@pytest.fixture
def abort_loopback():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _AbortHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


async def test_串流下載提早中止不會卡住整個池(abort_loopback):
    # 對應 client.py::stream_to_file 在累計超過 max_bytes 時主動中止讀取的情境：
    # pool_size=1，若第一個請求中止時 handle 沒安全歸還（或背景執行緒卡死沒結
    # 束），第二個請求會拿不到可用 handle 而逾時／出錯。
    async with httpx.AsyncClient(transport=PyCurlTransport(pool_size=1)) as c:
        async with c.stream(
            "GET", f"{abort_loopback}/f",
            extensions={EXT_MAX_FILESIZE: 10 * 1024 * 1024},
        ) as r:
            async for _ in r.aiter_bytes():
                break  # 模擬 stream_to_file 超限時中途主動中止讀取

        async def second_request() -> httpx.Response:
            return await c.get(f"{abort_loopback}/g")

        r2 = await asyncio.wait_for(second_request(), timeout=10)
    assert r2.status_code == 200


class _SlowHandler(BaseHTTPRequestHandler):
    """收到請求後先睡 2 秒才送出 header 與 body。

    讓取消（asyncio.wait_for 逾時、MCP 的 notifications/cancelled）能穩定落在
    perform() 仍在背景執行緒裡跑的窗口內——這正是 handle 生命週期出錯的窗口。
    """

    protocol_version = "HTTP/1.1"

    def do_GET(self):
        time.sleep(2)
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def slow_loopback():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


async def test_buffered_請求被取消後同站後續請求仍可成功(slow_loopback, loopback):
    # anyio.to_thread 的 worker thread 無法被中斷：取消發生時 perform() 仍在跑，
    # 此時歸還 handle 會讓下一個請求拿到同一個 handle，pycurl 一律拒絕
    # （cannot invoke reset() - perform() is currently running），且 LifoQueue
    # 保證被污染的 handle 待在堆疊頂端，同站請求會連續失敗直到 perform 自己結束。
    transport = PyCurlTransport(pool_size=1, total_timeout=5)
    async with httpx.AsyncClient(transport=transport) as c:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(c.get(f"{slow_loopback}/slow"), 0.3)
        # 取消後池子必須維持可用容量，且拿到的 handle 不能是還在被執行緒使用的那個。
        r = await asyncio.wait_for(c.get(f"{loopback}/x"), 5)
    assert r.status_code == 200


async def test_串流下載被取消後_handle_不會永久洩漏(slow_loopback, loopback):
    # 串流路徑的 handle 只由 stream 的 aclose() → release() 歸還。若在 header 收完
    # 之前就被取消，Response 從未交出去、release() 永遠不會被呼叫，handle 就永久
    # 從池中消失；池耗盡後無界的 _pool.get() 會讓該站所有請求永久掛住。
    transport = PyCurlTransport(pool_size=1, total_timeout=5)
    async with httpx.AsyncClient(transport=transport) as c:
        req = httpx.Request(
            "GET", f"{slow_loopback}/slow", extensions={EXT_MAX_FILESIZE: 10 * 1024 * 1024}
        )
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(c.send(req, stream=True), 0.3)
        # 池沒有被永久掏空：後續請求拿得到 handle（不會卡在 _pool.get()）。
        r = await asyncio.wait_for(c.get(f"{loopback}/x"), 5)
    assert r.status_code == 200


def test_非整數_code_不被塞進_code_佔位():
    # pycurl.error 的 args[0] 一般是整數 code，但 handle 被誤用時（perform() 仍在跑）
    # args[0] 會是字串，塞進「code {}」佔位會產生
    # 「libcurl 執行失敗（code cannot invoke reset() - ...）」這種完全誤導的訊息。
    exc = pt._pycurl_failure("cannot invoke reset() - perform() is currently running")
    assert isinstance(exc, CurlError)
    assert "code cannot invoke" not in str(exc)


async def test_池耗盡時以明確錯誤結束而非永久掛住():
    # 縱深防禦：handle 生命週期已修好，但若未來又有路徑漏了歸還，無界的 _pool.get()
    # 會讓該站所有請求永久掛住、必須重啟才能恢復——那是最難診斷的失敗形態。
    # 寧可以可讀的錯誤結束（本專案的 Never Suppress Silently）。
    transport = PyCurlTransport(pool_size=1, pool_wait=0.2)
    # 把唯一一顆 handle 抽走且不歸還，模擬洩漏後的池耗盡狀態。
    await transport._pool.get()

    with pytest.raises(CurlError) as exc:
        await transport.handle_async_request(httpx.Request("GET", "http://127.0.0.1:1/x"))
    assert "連線" in str(exc.value) or "忙碌" in str(exc.value)


def test_非_ascii_回應_header_可交給_httpx_不炸():
    """中文檔名附件的 Content-Disposition 含非 ASCII 位元組，須能原樣交給 httpx。

    header 值若以 str 形式傳給 httpx.Response，httpx 會用 ascii 重新編碼而拋
    UnicodeEncodeError，導致所有中文檔名的附件下載失敗。
    """
    collector = pt._HeaderCollector()
    collector.feed(b"HTTP/1.1 200 OK\r\n")
    raw = 'attachment; filename="績效未滿6隔月.png"'.encode()
    collector.feed(b"Content-Disposition: " + raw + b"\r\n")

    response = httpx.Response(
        collector.status_code,
        headers=pt._filter_response_headers(collector.pairs),
        content=b"",
    )
    assert response.headers.raw == [(b"Content-Disposition", raw)]
