# pycurl 傳輸層設計（取代子行程 CurlTransport）

日期：2026-08-19
狀態：設計已核准，待寫實作計畫
範圍：`redmine-mcp` 的 HTTP 傳輸層

## 動機

現行 `CurlTransport`（`src/redmine_mcp/curl_transport.py`）以「每次 HTTP 請求 fork
一個系統 curl 子行程」的方式送出請求。這帶來兩類問題，本設計要同時解決（使用者確認
「兩者都要」）：

1. **效能**：每請求約 80–140ms 的固定額外開銷（行程建立 + 每次重新 TCP／TLS 交握 +
   暫存檔），連線無法重用。實測（本機 loopback，不含 TLS 交握）：
   - 子行程模型：單請求中位數 ~90ms、8 站扇出 ~426ms。
   - pycurl 同一 handle 重用連線：單請求中位數 **0.42ms**。
2. **架構脆弱**：依賴 OS 層級的 curl 執行檔（無法列進 `pyproject` 相依）、每請求建立
   2–3 個暫存檔、把 API key 從子行程 stdin 餵入。

## 背景事實（決定方案的關鍵證據）

以下皆為本次調查實測所得，推翻了原模組記錄的理由：

- **403 的成因不是 header，是 TLS ClientHello 指紋**。curl 與 httpx 送出逐位元組
  相同的 header，前者通過、後者被 Cloudflare managed challenge 攔下；curl 把 header
  全轉小寫仍通過。原 commit（6e20d44）「httpx 轉小寫被當指紋」的歸因作廢。
- **不是「OpenSSL vs Schannel」那麼粗**。Python `ssl`（OpenSSL）被攔，但 Node
  （OpenSSL）與 **pycurl（OpenSSL 後端的 libcurl）通過**。分界在 libcurl 組出的
  ClientHello 與 Python `ssl` 不同。
- **pycurl 穩定通過站台 WAF**：對 `redmine-eos.tavuron.com` 帶假金鑰實測，
  HTTP/1.1、HTTP/2、預設共 6/6 通過（HTTP 401、無 challenge）。
- **pycurl 可裝**：PyPI 有 Windows／Linux／macOS 現成 wheel，`uv pip install pycurl`
  秒裝，無須自行編譯。其 libcurl 為 multi-SSL build（OpenSSL + Schannel）。

結論：**不能退回 httpx 原生**（Python `ssl` 的 ClientHello 會被 WAF 擋，全站 403）；
必須經由 libcurl 送出。pycurl 是唯一「有連線池 + 指紋被放行」且已驗證的路。

## 決策

- **方案 A：執行緒池 executor + Curl handle 池**（已核准）。pycurl 是同步的，把
  `perform()` 丟進 `anyio.to_thread` 跑，不阻塞事件迴圈。
  - 排除方案 B（`CurlMulti` + 事件迴圈 `add_reader`／`add_writer`）：`add_reader` 在
    Windows 的 ProactorEventLoop 上不支援，而 MCP 以 stdio 在 Windows 上必用該迴圈；
    且 MCP SDK 用 anyio，可能跑 trio 後端，該 API 根本不存在。方案 A 與事件迴圈後端
    無關，這是它的決定性優勢。
- **完全取代，不留退路**（已核准）：刪除子行程 `CurlTransport`，pycurl 成為唯一傳輸
  層。接受「某平台＋Python 版本無 wheel 就裝不起來」的風險，換取真正消除 OS curl
  相依的乾淨架構。

## §1 元件架構與整合點

新增 `PyCurlTransport`（`httpx.AsyncBaseTransport` 實作），取代 `CurlTransport`。
**介面不變**是核心：`RedmineClient`、`stream_to_file`、錯誤轉譯層、所有工具測試
（靠注入 `transport=httpx.MockTransport(...)`）全部零改動，它們只認
`handle_async_request` 介面與 `httpx.TransportError` 子類。

```
RedmineClient (不動)
  └─ httpx.AsyncClient(transport=PyCurlTransport(...))   ← 唯一替換點
       └─ PyCurlTransport.handle_async_request(request)
            ├─ 從 handle 池借一個 pycurl.Curl
            ├─ anyio.to_thread.run_sync(handle.perform)   ← 不阻塞事件迴圈
            └─ 組回 httpx.Response（狀態碼 + headers + body 串流）
```

**保留可匯入的名稱**（`client.py` 依賴）：`EXT_TOTAL_TIMEOUT`、`EXT_MAX_FILESIZE`、
`CurlError`、`CurlTimeoutError`、`CurlFileSizeExceeded`（型別階層不變，仍繼承
`httpx.TransportError`）。

## §2 必須原樣接手的行為契約

| 現行行為 | pycurl 對應 |
|---|---|
| API key 從 stdin、不進 argv／磁碟 | `HTTPHEADER` 行程內傳遞 —— 無 argv、無暫存檔 |
| 不跟隨轉址、不關 TLS 驗證 | 不設 `FOLLOWLOCATION`；`SSL_VERIFYPEER=1`、`SSL_VERIFYHOST=2` |
| 一般請求協商壓縮、下載不協商 | 一般：`ACCEPT_ENCODING=""`（全部）；帶 `EXT_MAX_FILESIZE` 的下載：不設 |
| 回應丟棄 `content-encoding`／`content-length` | 同樣丟棄（libcurl 已解壓縮，保留會讓 httpx 二次解壓） |
| header 原始大小寫、跳過 host 等 5 個 | `HTTPHEADER` 帶原樣 header；host／content-length 交給 libcurl 自算 |
| body 串流、不整包進記憶體 | `WRITEFUNCTION` 回呼推 chunk，httpx `aiter_bytes()` 逐塊取 —— 無暫存檔 |
| 逾時可逐請求覆寫（`EXT_TOTAL_TIMEOUT`） | `CONNECTTIMEOUT` + `TIMEOUT`（下載走 300 秒覆寫值） |
| `--max-filesize` 依 Content-Length 提前擋 | `MAXFILESIZE_LARGE`；超過拋 `CurlFileSizeExceeded` |
| 例外訊息不洩漏 curl 輸出 | `pycurl.error` 只取 code → 對應中文提示（見 §5） |

跳過的 request header 集合沿用現行 `_SKIP_REQUEST_HEADERS`
（host、content-length、accept-encoding、connection、transfer-encoding）。

## §3 連線重用與並行模型

關鍵簡化：`RedmineClient` 每站一個，故**每個 `PyCurlTransport` 只對單一 host 說話**。

- 每個 transport 內含小 handle 池（預設 4 個 `pycurl.Curl`），以 `asyncio.Queue`／
  anyio 號誌管借還；每次請求借一個、`perform()` 跑完歸還。
- 因為同 transport 永遠打同 host，**每個 handle 自己的連線快取自動保活該站連線**，
  重複呼叫直接重用（實測 0.4ms）。
- **不引入 `CurlShare`**：跨 handle 共享連線只在「多 handle 打同 host」時有意義，而同
  站呼叫幾乎序列；扇出並行是跨不同 transport（不同站）發生，天生各走各的連線。省掉
  CurlShare 免了跨執行緒鎖回呼的複雜度。日後若量測出同站爭用再加。
- 並行由 executor 承載（`anyio.to_thread.run_sync`）。單站並行低，池開 4 足夠；扇出
  8 站是 8 個 transport 各借自己的 handle，互不相干。

## §4 串流、大小上限、逾時

`perform()` 是阻塞呼叫、body 邊到邊透過 `WRITEFUNCTION` 吐出，但 httpx 要先拿到
狀態碼＋header 才讀 body。用**混合策略**依請求類型分兩條路：

- **一般 API 請求（無 `EXT_MAX_FILESIZE`）**：body 是小 JSON，在 executor 執行緒裡
  `perform()` 跑完、`WRITEFUNCTION` 累進記憶體 buffer，回
  `httpx.Response(status, headers, content=body)`。無串流、無暫存檔。
- **附件下載（帶 `EXT_MAX_FILESIZE`）**：真串流。背景執行緒跑 `perform()`，
  `WRITEFUNCTION` 把 chunk 推進**有界佇列**（滿了阻塞 → 背壓，記憶體有界）；
  `HEADERFUNCTION` 收完 header 後以 event 通知主端，主端組出 `httpx.Response` 並讓
  `aiter_bytes()` 從佇列逐塊取，`perform()` 背景續跑。撐得住 100MB 附件而 RSS 只吃
  佇列大小，且無暫存檔。
  - 複雜度被一個事實夾住：串流 body 的**唯一消費者是 `stream_to_file`**，它必然把流
    讀到底或因超過上限中止，不會有「讀一半擱著」的狀態，producer／consumer 收尾可控。

大小上限兩道防線不變：`MAXFILESIZE_LARGE`（依 Content-Length 提前擋，拋
`CurlFileSizeExceeded`）＋ `client.py` 逐塊硬上限（保護記憶體）。逾時：`CONNECTTIMEOUT`
+ `TIMEOUT`，下載走 300 秒覆寫值。

## §5 錯誤轉譯

`perform()` 失敗拋 `pycurl.error(code, msg)`，在 transport 內轉成既有型別，訊息只取
code 對應中文提示、不含 libcurl 原文：

| libcurl code | 轉成 | 提示 |
|---|---|---|
| 6 COULDNT_RESOLVE_HOST | `CurlError` | 無法解析主機名稱… |
| 7 COULDNT_CONNECT | `CurlError` | 無法建立連線… |
| 28 OPERATION_TIMEDOUT | `CurlTimeoutError` | 逾時 |
| 35 SSL_CONNECT_ERROR | `CurlError` | TLS 交握失敗… |
| 60 PEER_FAILED_VERIFICATION | `CurlError` | 無法驗證伺服器憑證… |
| 63 FILESIZE_EXCEEDED | `CurlFileSizeExceeded` | 附件過大 |
| 其他 | `CurlError` | 通用訊息＋code |

沿用現行 `_CURL_EXIT_HINTS`（code 一致，直接搬）。

## §6 測試策略

前提：**絕大多數測試不受影響**。工具測試靠 `SiteRegistry(transports={...})` 注入
`httpx.MockTransport`，不碰真傳輸層。設計保留 `RedmineClient(settings, transport=...)`
注入縫，換預設 transport 不動它們。

需重寫的只有傳輸層自己的測試（現行 `test_curl_transport.py` 靠 monkeypatch
`create_subprocess_exec`，隨子行程模型作廢），改用兩層：

- **純函式單元測試**：header 組裝（跳過集合過濾、大小寫保留）、`_pycurl_failure(code)`
  的 code→例外對應、回應 header 過濾（丟棄 `content-encoding`／`content-length`）。
- **loopback 整合測試**：fixture 起 in-process `127.0.0.1` HTTP server，讓
  `PyCurlTransport` 打它，斷言端到端行為：狀態碼／header 重建、下載串流與背壓、
  `MAXFILESIZE` 超過中止、逾時（server sleep）、連線被拒（連沒開的 port →
  `CurlError`）。http:// loopback 即可，CI 在 ubuntu 上跑得動。

## §7 打包、清理、文件、rollout

- **相依**：`pyproject.toml` 加 `pycurl`（版本上下界，比照其他相依）；移除「curl 是 OS
  層級相依、無法列進 dependencies」的註記。
- **刪除程式碼**：`resolve_curl_path`、`ensure_curl_available`（含 `__main__.py`
  呼叫）、`_build_config`／`_quote_config_value`／暫存檔機制／`_TempFileByteStream`／
  `_parse_curl_headers`，及對應測試。
- **文件**：README／SETUP 移除「curl 為前置需求／Windows 內建 curl」的說法，改為
  「pycurl 隨套件安裝，使用者無須另裝」。
- **CI 缺口**：CI 只跑 ubuntu，主要部署是 Windows。方案 A 事件迴圈無關，風險低，但
  rollout 必做一次真實的 Windows `uv tool install`，確認 pycurl 解析到 wheel（非退回
  原始碼編譯）、且 server 在 ProactorEventLoop 下正常。可選：加 `windows-latest` CI
  job（超出本次範圍，列為建議）。
- **版號**：外部行為不變、內部傳輸層抽換，建議 0.7.0 → 0.8.0；最終依專案版號慣例由
  維護者定。

## 明確不做（YAGNI）

- 不引入 `CurlShare`（見 §3）。
- 不保留子行程 transport 作為 fallback（完全取代）。
- 不加 `windows-latest` CI job（列為建議，非本次範圍）。
- 不追求 HTTP/3／QUIC 或其他 libcurl 進階特性；預設協商即可（實測已通過）。
