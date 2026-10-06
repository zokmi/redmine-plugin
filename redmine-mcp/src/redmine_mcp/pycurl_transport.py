"""以 pycurl（libcurl 綁定）作為 httpx 傳輸層。

取代早期的子行程 curl 傳輸：libcurl 在行程內送出請求，連線可重用，且 API key
經 HTTPHEADER 在行程內傳遞，不進 argv、不落暫存檔。

為什麼是 pycurl 而非 httpx 原生：站台的 Cloudflare WAF 依 TLS ClientHello 指紋
攔截，Python ssl 產生的 ClientHello 會被擋（實測），libcurl 的則放行。詳見
docs/superpowers/specs/2026-08-19-pycurl-transport-design.md。
"""
from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from io import BytesIO

import anyio
import httpx
import pycurl

#: 佇列結束哨符：生產者放進佇列表示 body 已全部送完（非例外中止）。
_SENTINEL = object()

#: 單次請求可覆寫逾時與大小上限的 `httpx.Request.extensions` 鍵名。
EXT_TOTAL_TIMEOUT = "redmine_total_timeout"
EXT_MAX_FILESIZE = "redmine_max_filesize"

#: 不轉發給 libcurl 的 request header；host/content-length 等由 libcurl 自算。
_SKIP_REQUEST_HEADERS = frozenset(
    {"host", "content-length", "accept-encoding", "connection", "transfer-encoding"}
)

#: 不轉回 httpx 的 response header：libcurl 已解壓縮，保留會讓 httpx 二次解壓。
#: 以 bytes 表示，因為 response header 全程維持 bytes（見 _HeaderCollector 說明）。
_SKIP_RESPONSE_HEADERS = frozenset({b"content-encoding", b"content-length"})

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


def _pycurl_failure(code: object) -> CurlError:
    """把 libcurl error code 轉成對應例外；訊息只含 code 與中文提示。

    型別刻意放寬到 object：pycurl.error 的 args[0] 一般是整數 code，但 handle 被
    誤用時（perform() 仍在跑就對它 reset/setopt）會是字串。把字串塞進「code {}」
    佔位會產生完全誤導使用者的訊息，因此先擋掉——那是內部不變量被破壞，不是
    使用者能排查的網路問題。
    """
    if not isinstance(code, int):
        return CurlError("libcurl 傳輸層狀態異常，已中止本次請求")
    if code == pycurl.E_OPERATION_TIMEDOUT:
        return CurlTimeoutError("請求逾時（libcurl code 28），請確認網路狀況或站台回應速度")
    if code == pycurl.E_FILESIZE_EXCEEDED:
        return CurlFileSizeExceeded("回應內容超過允許的大小上限，已中止傳輸（libcurl code 63）")
    hint = _CURL_HINTS.get(code)
    suffix = f"：{hint}" if hint else ""
    return CurlError(f"libcurl 執行失敗（code {code}）{suffix}")


def _filter_response_headers(pairs: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """丟棄 content-encoding／content-length 之外原樣保留回應 header。

    參數:
        pairs: _HeaderCollector 收集到的 (名稱, 值) bytes 配對。
    回傳:
        可直接交給 httpx.Response 的 bytes 配對清單。
    """
    return [(name, value) for name, value in pairs if name.lower() not in _SKIP_RESPONSE_HEADERS]


def _request_headers(request: httpx.Request) -> list[str]:
    """把 request header 轉成 libcurl HTTPHEADER 的 "Name: value" 清單。

    使用 request.headers.raw 保留原始大小寫（如實轉發呼叫端意圖）；跳過由 libcurl
    自算的 host/content-length 等。

    名稱與值刻意採用不同的解碼策略（依 RFC 7230）：
    - 名稱（header field-name）依規範必為 ASCII token，用 errors="strict"；
      一旦出現非法位元組代表上游已產生壞資料，應盡早拋出 UnicodeDecodeError
      讓問題在源頭曝光，不可用 errors="replace" 悄悄置換成 U+FFFD 後送給
      libcurl（即「Never Suppress Silently」——見專案 CLAUDE.md）。
    - 值（header field-value）依規範允許任意 obs-text（0x80-0xFF）位元組，
      latin-1 對任何位元組序列都有一對一映射、不會失敗，是轉發任意合法值
      的慣例作法，故維持 errors="replace" 純屬保險，實務上不會觸發。
    """
    lines: list[str] = []
    for raw_name, raw_value in request.headers.raw:
        name = raw_name.decode("ascii", errors="strict")
        value = raw_value.decode("latin-1", errors="replace")
        if name.lower() in _SKIP_REQUEST_HEADERS:
            continue
        lines.append(f"{name}: {value}")
    return lines


class _HeaderCollector:
    """收集 libcurl HEADERFUNCTION 吐出的 header 行，解析出狀態碼與 header 清單。

    libcurl 對每一行 header（含狀態列 "HTTP/x.y NNN ..."）呼叫一次；空行代表
    header 區塊結束。轉址已停用，故只會有一組。

    名稱與值全程維持 bytes、不做任何解碼：header field-value 依規範允許 obs-text
    （0x80-0xFF）位元組，而 httpx.Response 收到 str 型別的 header 值時會以 ascii
    重新編碼，中文檔名附件的 Content-Disposition 會因此拋 UnicodeEncodeError，
    讓整筆下載失敗。交出 bytes 則由 httpx 原樣採用，完全不經編碼。
    """

    def __init__(self) -> None:
        self.status_code = 0
        self.pairs: list[tuple[bytes, bytes]] = []

    def feed(self, line: bytes) -> None:
        """吃進一行原始 header 位元組。

        參數:
            line: libcurl 給的單行 header，含結尾換行。
        """
        raw = line.strip(b"\r\n")
        if not raw:
            return
        if raw.startswith(b"HTTP/"):
            # "HTTP/1.1 200 OK" → 200；中介回應（如 100）會被後一組覆蓋。
            # 狀態列依規範必為 ASCII，此處解碼僅為取出數字，不影響 header 值。
            parts = raw.decode("latin-1", errors="replace").split(" ", 2)
            if len(parts) >= 2 and parts[1].isdigit():
                self.status_code = int(parts[1])
                self.pairs = []
            return
        name, sep, value = raw.partition(b":")
        if sep:
            self.pairs.append((name.strip(), value.strip()))


def _notify_loop(loop: asyncio.AbstractEventLoop, fn: Callable[[], None]) -> None:
    """從背景執行緒通知事件迴圈；迴圈已關閉時安靜略過。

    請求被取消後 perform() 仍在背景跑（執行緒無法被中斷），而事件迴圈可能已經
    關閉——此時 call_soon_threadsafe 會拋 RuntimeError，在執行緒裡無人接手就變成
    印到 stderr 的裸 traceback。迴圈都關了就沒有任何人在等這個通知，略過即可。
    """
    try:
        loop.call_soon_threadsafe(fn)
    except RuntimeError:
        pass


def _new_handle() -> pycurl.Curl:
    """建立一個裸的 Curl handle。

    刻意不在此設任何選項：每次請求的 ``run()`` 會在 ``h.reset()`` 之後統一設定
    安全與請求選項，避免兩處重複設定造成混淆（單一來源原則）。
    """
    return pycurl.Curl()


class _QueueByteStream(httpx.AsyncByteStream):
    """從有界佇列逐塊取出下載 body 的 httpx stream。

    生產者（executor 執行緒裡的 perform()）把 chunk put 進佇列，滿了就阻塞形成
    背壓；完成推 _SENTINEL；失敗推例外物件。消費端 aiter 逐塊取，遇例外即拋出。
    消費者不保證會把流讀到底：client.py::stream_to_file 在累計超過大小上限時會
    主動中止讀取，httpx 的 stream() context manager 會在 finally 無條件呼叫
    aclose()，此時背景 perform() 可能還卡在 WRITEFUNCTION 的 q.put()（佇列已滿、
    消費端已停讀）。release callback（見 _streaming_request）負責安全地中止並
    等待背景執行緒真正結束，避免 handle 被併發操作或執行緒洩漏。

    註：本類別假設事件迴圈為 asyncio（server 以 anyio.run 的預設 asyncio
    backend 啟動），故消費端可放心用 anyio.to_thread 搭配 asyncio 佇列消費模式；
    若日後改用 trio backend，需重寫 _streaming_request 內以 asyncio 原語
    （call_soon_threadsafe、ensure_future）通知主端的部分。
    """

    def __init__(self, q: queue.Queue[object], release: Callable[[], Awaitable[None]]) -> None:
        self._q = q
        self._release = release  # 讀完/關閉時安全歸還 handle 的 callback（冪等、async）

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            while True:
                item = await anyio.to_thread.run_sync(self._q.get)
                if item is _SENTINEL:
                    return
                if isinstance(item, pycurl.error):
                    raise _pycurl_failure(item.args[0]) from item
                if isinstance(item, BaseException):
                    # _configure 等非 pycurl.error 例外（如 MAXFILESIZE 轉型失敗）：
                    # 一律轉成通用 CurlError，訊息不含內部例外原文。
                    raise CurlError("下載過程發生非預期錯誤，已中止傳輸") from item
                yield item  # type: ignore[misc]
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        await self._release()


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
        pool_wait: float = 30.0,
    ) -> None:
        self._connect_timeout = connect_timeout
        self._pool_wait = pool_wait
        self._total_timeout = total_timeout
        self._pool: asyncio.LifoQueue[pycurl.Curl] = asyncio.LifoQueue()
        for _ in range(pool_size):
            self._pool.put_nowait(_new_handle())
        # 串流下載背景 perform() 的 task 參照；只為避免被 GC 提前回收，由各自的
        # release() 在確認執行緒結束後透過 done callback 清除，不需外部使用。
        self._pending: set[asyncio.Task[None]] = set()

    async def _take_handle(self) -> pycurl.Curl:
        """從池中取一顆 handle；等太久就以明確錯誤結束。

        縱深防禦：正常情況下 handle 一定會被歸還（見 _return_handle），池不會耗盡。
        但若未來又有路徑漏了歸還，無界的 LifoQueue.get() 會讓該站所有請求永久掛住、
        必須重啟 server 才能恢復——那是最難診斷的失敗形態。寧可以可讀的錯誤結束。
        """
        try:
            return await asyncio.wait_for(self._pool.get(), self._pool_wait)
        except TimeoutError as exc:
            raise CurlError(
                "連線資源忙碌逾時：本站台的連線都還在使用中。"
                "請稍後重試；若持續發生請回報，這通常代表 server 內部的連線未被正確釋放"
            ) from exc

    def _return_handle(self, h: pycurl.Curl, perform_task: asyncio.Task[None] | None) -> None:
        """歸還 handle；背景執行緒仍在使用它時改為延後丟棄並補一個新 handle。

        anyio.to_thread 的 worker thread 無法被中斷：請求被取消時（fan_out 的
        per-site 逾時、MCP 的 notifications/cancelled）perform() 仍在跑，直到
        libcurl 自己的 TIMEOUT 到期才結束。此時 handle 絕不能回池——pycurl 會
        拒絕對它的任何操作（cannot invoke reset()/setopt()/close() - perform()
        is currently running），而 LifoQueue 保證下一個請求先拿到堆疊頂端這顆，
        於是同站請求在整個逾時窗口內連續失敗，池裡健康的 handle 完全用不到。

        取消過的連線狀態本就不明，因此延後到執行緒結束時直接 close 而非回池，
        並立刻補一顆新 handle 維持池容量——少了這一步，每次取消都會讓池永久
        少一顆，耗盡後無界的 _pool.get() 會讓該站所有請求永久掛住。
        """
        if perform_task is None or perform_task.done():
            self._pool.put_nowait(h)
            return
        self._pool.put_nowait(_new_handle())
        self._pending.add(perform_task)
        perform_task.add_done_callback(self._pending.discard)
        perform_task.add_done_callback(lambda _: h.close())

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
        h = await self._take_handle()
        perform_task: asyncio.Task[None] | None = None
        try:
            collector = _HeaderCollector()
            buf = BytesIO()

            def run() -> None:
                # curl_easy_reset 會清掉先前設定的選項，但保留 live connections／
                # DNS／session 快取，所以「每請求 reset 後重設選項」不會破壞連線
                # 重用，仍能吃到 handle 自己的連線快取。
                h.reset()
                h.setopt(pycurl.SSL_VERIFYPEER, 1)
                h.setopt(pycurl.SSL_VERIFYHOST, 2)
                h.setopt(pycurl.NOSIGNAL, 1)  # 多執行緒下必要，避免 libcurl 用 signal 計時
                # 刻意不設 FOLLOWLOCATION（不跟隨轉址）、不關 TLS 驗證。
                self._configure(h, request, body)
                h.setopt(pycurl.HEADERFUNCTION, collector.feed)
                h.setopt(pycurl.WRITEFUNCTION, buf.write)
                h.perform()

            # shield：外層取消時不連帶取消這個 task，讓它跑到執行緒真正結束，
            # _return_handle 掛的 done callback 才有機會安全 close 這顆 handle。
            perform_task = asyncio.ensure_future(anyio.to_thread.run_sync(run))
            try:
                await asyncio.shield(perform_task)
            except pycurl.error as exc:
                raise _pycurl_failure(exc.args[0]) from exc

            return httpx.Response(
                collector.status_code,
                headers=_filter_response_headers(collector.pairs),
                content=buf.getvalue(),
                request=request,
            )
        finally:
            self._return_handle(h, perform_task)

    async def _streaming_request(self, request: httpx.Request, body: bytes) -> httpx.Response:
        """附件下載路徑：背景執行緒跑 perform()，header 收完就先回 Response，
        body 由 _QueueByteStream 邊下載邊消費（有界佇列形成背壓）。

        消費端可能提早中止讀取（client.py::stream_to_file 累計超過大小上限時
        主動中止，或呼叫端 break），此時 httpx 會呼叫 stream 的 aclose()。此
        方法用 threading.Event 讓 WRITEFUNCTION 感知中止：中止後回傳 0（≠ 資料
        長度）讓 libcurl 以 CURLE_WRITE_ERROR 結束 perform()，而不是讓生產者
        永久卡在已滿又無人消費的佇列裡；release() 會先中止、清空佇列解除卡住
        的 put()，再等待背景執行緒真正結束才把 handle 歸還池，避免下一個請求
        拿到還在被背景執行緒操作的同一個 handle（libcurl 未定義行為）。

        註：本方法用 asyncio.get_running_loop()／call_soon_threadsafe／
        ensure_future 等 asyncio 原語跨執行緒通知主端；本 server 以
        anyio.run(run_stdio_async) 的預設 asyncio backend 啟動，測試亦在
        pytest-asyncio 下跑，故此處直接假設 asyncio backend 安全可用——若日後
        改用 trio backend 需重寫此方法。
        """
        h = await self._take_handle()
        released = False
        abort = threading.Event()
        perform_task: asyncio.Task[None] | None = None

        collector = _HeaderCollector()
        q: queue.Queue[object] = queue.Queue(maxsize=16)
        headers_done = anyio.Event()
        loop = asyncio.get_running_loop()

        def on_header(line: bytes) -> None:
            collector.feed(line)
            if line in (b"\r\n", b"\n"):  # header 區塊結束
                _notify_loop(loop, headers_done.set)

        def on_write(data: bytes) -> int:
            if abort.is_set():
                return 0  # ≠ len(data) → libcurl 以 CURLE_WRITE_ERROR 中止 perform()
            q.put(data)  # 滿了阻塞生產者執行緒 → 背壓
            return len(data)

        def run() -> None:
            try:
                # 與 _buffered_request 一致：reset 後統一重設安全與請求選項。
                h.reset()
                h.setopt(pycurl.SSL_VERIFYPEER, 1)
                h.setopt(pycurl.SSL_VERIFYHOST, 2)
                h.setopt(pycurl.NOSIGNAL, 1)
                self._configure(h, request, body)
                h.setopt(pycurl.HEADERFUNCTION, on_header)
                h.setopt(pycurl.WRITEFUNCTION, on_write)
                h.perform()
                q.put(_SENTINEL)
            except Exception as exc:
                # 不只 pycurl.error：_configure 內的型別轉換等錯誤也要能終止
                # 消費端的 q.get()，否則消費端會永久阻塞（既無 sentinel 也無
                # 例外物件）。統一推例外物件進佇列，交給 __aiter__ 轉譯。
                q.put(exc)
            finally:
                # perform 若在 header 前就失敗，主端不能永久卡在 headers_done.wait()。
                _notify_loop(loop, headers_done.set)

        # 在背景執行 perform；不 await 它，讓 body 邊下載邊被消費。task 參照放進
        # self._pending 避免被 GC；done_callback 在背景執行緒結束時自動清除。
        perform_task = asyncio.ensure_future(anyio.to_thread.run_sync(run))
        self._pending.add(perform_task)
        perform_task.add_done_callback(self._pending.discard)

        async def release() -> None:
            # 冪等：stream 讀完（sentinel）路徑與提早中止的 aclose 都會呼叫，
            # 只實際處理一次。
            nonlocal released
            if released:
                return
            released = True
            abort.set()
            # 清空佇列，解除可能正阻塞在 q.put() 的生產者（消費端提早中止時，
            # 佇列可能已滿、生產者正等著把下一個 chunk put 進去）。
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
            # run() 已把所有例外攔在內部轉成佇列項目、正常結束，理論上不會再
            # 往外拋；仍以 try/except 兜底，避免未來改動讓例外意外逃逸而卡住
            # release（下面的 self._pool.put_nowait(h) 也就永遠不會執行）。
            try:
                await perform_task
            except Exception:
                pass
            # 確認背景執行緒真的已經結束、不會再操作這個 handle，才歸還池；
            # 否則下一個請求可能拿到同一個 handle，與仍在跑的背景執行緒並發
            # 操作它，是 libcurl 的未定義行為（可能污染連線狀態甚至 crash）。
            self._pool.put_nowait(h)

        # 這段必須包在 try 裡：handle 只由 release() 歸還，而 release() 只由 stream
        # 的 aclose() 觸發。若在 Response 交出去之前就被取消（headers_done.wait()
        # 是主要窗口，下載總逾時可達 300 秒），release() 永遠不會被呼叫，handle 就
        # 永久從池中消失；池耗盡後無界的 _pool.get() 會讓該站所有請求永久掛住。
        try:
            await headers_done.wait()

            # perform 若在 header 前就失敗，佇列首個項目會是例外，交給 stream 拋出。
            return httpx.Response(
                collector.status_code or 200,
                headers=_filter_response_headers(collector.pairs),
                stream=_QueueByteStream(q, release),
                request=request,
            )
        except BaseException:
            # 通知生產者中止（on_write 回 0 → libcurl 以 WRITE_ERROR 結束 perform），
            # 並清空佇列解除可能正阻塞在 q.put() 的生產者，讓執行緒得以盡快結束。
            released = True  # release() 之後若再被呼叫不重複處理
            abort.set()
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
            self._return_handle(h, perform_task)
            raise

    async def aclose(self) -> None:
        """關閉池內所有 handle。"""
        while not self._pool.empty():
            self._pool.get_nowait().close()
