# 多站台支援設計

日期：2026-08-10
狀態：待審閱

## 背景

目前 `create_server(client)` 綁死單一 `RedmineClient`，四個 `register()` 都閉包在該 client 上，`MetadataCache` 也跟著 client 走。要接第二個 Redmine 站台，只能重複註冊多個 MCP server（各帶不同的 `REDMINE_CONFIG`），代價是每站一個常駐行程、多份參數檔，且模型無法在單次回答中跨站操作。

## 目標

1. 單一 MCP server 可同時連線多個 Redmine 站台，站台數量不預設上限。
2. 新增站台只需修改參數檔，不改程式、不重跑註冊指令。
3. 查詢時若未指定站台，自動查詢所有已設定的站台——使用者手上常常只有一個單號，不知道它屬於哪一站。
4. 寫入操作絕不因為站台判斷失誤而落在錯誤的站台。
5. 現有單站台參數檔不修改即可繼續使用。

## 非目標

- **不做網路探索。** 不掃描內網尋找 Redmine 主機。API key 只能人工取得，自動找到主機也連不上。
- **不做參數檔熱重載。** 新增站台後於 `/mcp` 重新連線即可，不值得為此引入檔案監看與執行中重讀金鑰的複雜度。
- **不做跨站合併排序。** 見「分頁語義」一節。
- **不做展開式環境變數**（如 `REDMINE_SITES_CLIENT_B_URL`）。多站設定應落在版控管理的檔案裡。

## 核心規則

整個設計由兩條規則決定，其餘皆為推論：

> **讀取工具省略 `site` → 查詢所有已設定站台，回傳按站分組。明寫 `site` → 只查該站。**
>
> **寫入工具在多站台時 `site` 必填，且永不接受跨站執行。**

刻意**不設預設站台**。有預設站就會產生「省略 `site` 到底是用預設站還是查全部」的歧義，而該歧義只能靠使用者記憶規則來消除。移除預設站後，省略只有一個意思。

## 參數檔格式

```toml
# ── 全域層級（選填，作為各站台未指定時的預設值）
download_dir = "D:/redmine-downloads"
upload_dir   = "D:/redmine-uploads"
max_attachment_mb = 10

# ── 站台層級
[sites.main]
url     = "https://redmine.company.com/redmine"
api_key = "abc123..."
description = "公司正式站"

[sites.client-b]
url     = "https://redmine.client-b.com"
api_key = "def456..."
description = "客戶 B 的正式站，只有 PM 有建立權限"
download_dir = "D:/dl-client-b"     # 覆寫全域
```

### 鍵名

| 層級 | 鍵 | 必填 | 說明 |
|---|---|---|---|
| 全域 | `download_dir` | 否 | 各站台的預設下載目錄 |
| 全域 | `upload_dir` | 否 | 各站台的預設上傳來源目錄；未設定時沿用 `download_dir` |
| 全域 | `max_attachment_mb` | 否 | 預設 10 |
| 站台 | `url` | **是** | 站台根位址 |
| 站台 | `api_key` | **是** | 該站台的 API 存取金鑰 |
| 站台 | `description` | 否 | 給模型把口語對應到站台代號用，例如「客戶 B 那邊」→ `client-b` |
| 站台 | `download_dir` / `upload_dir` / `max_attachment_mb` | 否 | 覆寫全域同名值 |

未知鍵沿用現行處理：記 warning 並忽略，訊息只提鍵名不提值（拼錯的鍵可能是 `api_kye`，其值就是真金鑰）。

### 站台名稱規則

`^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$`，於啟動時驗證，不合法即拋 `ConfigError`。

這是**安全邊界而非格式偏好**：站台名稱會直接作為下載子目錄名，一個名為 `..` 的站台即構成路徑穿越。性質等同現有的 `validate_identifier()`。

### 模式判定與向後相容

| 參數檔內容 | 行為 |
|---|---|
| 有 `[sites.*]` 區塊 | 多站台模式 |
| 無 `[sites.*]`，最外層有 `url` / `api_key` | 舊格式；視為單一站台，名稱固定為 `default` |
| 兩者同時存在 | 拋 `ConfigError`，要求擇一 |

第三列刻意報錯而非挑一邊：兩者並存代表使用者改到一半，靜默採用其中一組會讓「參數檔寫 A、實際連 B」這種最難排查的狀況發生。

舊格式參數檔不需修改即可運作。唯一的可見差異是下載檔案會多一層 `default/` 子目錄（見「附件」一節）。

### 環境變數

| 環境變數 | 行為 |
|---|---|
| `REDMINE_CONFIG` | 不變，指定參數檔路徑 |
| `REDMINE_DOWNLOAD_DIR` / `REDMINE_UPLOAD_DIR` / `REDMINE_MAX_ATTACHMENT_MB` | 覆寫**全域層級**預設值 |
| `REDMINE_URL` / `REDMINE_API_KEY` | 僅在**恰好一個站台**時有效，覆寫該站台的對應欄位；站台數 > 1 時忽略並記 warning |

`REDMINE_URL` / `REDMINE_API_KEY` 的既有用途是「臨時切換站台或帳號」。站台多於一個時它們無法明確指涉任何一站，因此忽略——靜默覆寫某個任選的站台，會製造與上述「兩者並存」同一類的排查地獄。

沿用現行規則：空白值視為未提供，不覆寫參數檔既有的值。

## 資料結構

```python
@dataclass(frozen=True)
class SiteSettings:
    """單一 Redmine 站台的執行期設定。"""
    name: str
    url: str
    api_key: str
    description: str | None
    download_dir: Path | None
    upload_dir: Path | None
    max_attachment_bytes: int


@dataclass(frozen=True)
class Settings:
    """整體執行期設定。"""
    sites: Mapping[str, SiteSettings]   # 至少一項，且維持參數檔中的宣告順序
```

`sites` 保證非空——沒有任何站台的設定應在 `load_settings()` 就拋 `ConfigError`。

### `RedmineClient` 的建構參數變更

`RedmineClient.__init__` 現行接收 `Settings`，改為接收 `SiteSettings`。client 本來就只服務單一站台，需要的是 url、api_key 與該站的目錄與大小上限，不該看到其他站台的設定。`client.settings.url`、`client.settings.download_dir` 等既有存取點的寫法不變，只是型別由 `Settings` 改為 `SiteSettings`。

### `config_file.py` 的模型變更

現行 `build_env()` 把參數檔攤平成 `REDMINE_*` 形式的扁平字典再餵給 `load_settings()`。巢狀表格無法用扁平鍵表達（除非採用已排除的展開式命名），因此該模型必須改變：

- `load_config_file(path)` 改為回傳**結構化字典**（分離後的全域區與 `sites` 區），僅做鍵名驗證與純量檢查，不做語意驗證。
- `load_settings(raw, env)` 接收「參數檔結構」與「環境變數來源」兩個參數，套用上表的覆寫規則後產出 `Settings`。

維持現行的職責分工不變：`config_file.py` 只負責讀檔與結構整理，所有語意驗證（URL 格式、路徑、大小、站台名稱）集中在 `load_settings()` 一處。

## SiteRegistry

```python
class SiteRegistry:
    """站台名稱 → RedmineClient 的註冊表，並提供跨站扇出。"""

    def __init__(
        self,
        settings: Settings,
        transports: Mapping[str, httpx.AsyncBaseTransport] | None = None,
    ): ...

    @property
    def names(self) -> tuple[str, ...]:
        """所有站台名稱，維持參數檔宣告順序。"""

    def resolve(self, site: str | None) -> dict[str, RedmineClient]:
        """讀取用。None 回傳全部；指定名稱回傳單一站；名稱不存在則拋 ValueError 並列出可用名稱。"""

    def resolve_for_write(self, site: str | None) -> tuple[str, RedmineClient]:
        """寫入用。站台數 > 1 且 site 為 None 時拒絕；恰好一站時 None 放行。"""

    async def aclose(self) -> None:
        """逐站關閉，單站失敗不影響其他站。"""
```

`transports` 為**每站一份**的測試替身對照表（鍵為站台名稱），正式執行時留空、各站各自使用 `CurlTransport`。測試需要讓不同站台回傳不同結果，共用單一 transport 無法表達這件事。

`resolve()` 與 `resolve_for_write()` 是唯二的解析入口，讀寫規則各只實作一次。

所有 `RedmineClient` 於**啟動時**一次建好，不延遲建立——設定錯誤要在啟動就浮現，而不是等到第一次呼叫該站台的工具。

`resolve_for_write(None)` 在多站台時的錯誤訊息需列出可用站台名稱，讓模型能直接修正重試：

```
已設定多個站台，寫入前請明寫 site（可用：main、client-b、staging）
```

## 扇出

```python
async def fan_out(
    clients: dict[str, RedmineClient],
    fn: Callable[[RedmineClient], Awaitable[Any]],
    *,
    none_on_not_found: bool = False,
) -> dict[str, Any]:
    """對每站並行執行 fn，每站錯誤各自隔離。"""
```

### 錯誤隔離

某站 API key 過期或連線失敗，不應讓整個查詢失敗。各站結果獨立：

| 情況 | 該站的值 |
|---|---|
| 成功 | 該站的 payload |
| 404 且 `none_on_not_found=True` | `null` |
| 其他 `RedmineError` | `{"error": "<例外訊息>"}` |
| 非預期例外 | `{"error": "查詢此站台時發生非預期錯誤"}`，完整例外寫入 log |

`RedmineError` 的訊息已在既有設計中濾除憑證資訊，可直接呈現。非預期例外改用通用訊息，避免把堆疊或內部細節送進模型 context。

### 404 不是錯誤

id 查找時某站回 404，語意是「這裡沒有這張單」而非查詢失敗，因此回 `null`。這是 `none_on_not_found` 的唯一用途，僅用於 `get_issue`、`list_attachments`、`download_attachment` 的定位階段。

### 並行度上限

以 `asyncio.Semaphore` 限制同時進行的請求數（上限 8）。每個請求是一個 curl 子行程，站台數增長到十幾個時無上限的扇出會造成子行程風暴。

## 工具層

共 16 個工具（現有 15 + 新增 `list_sites`）。

### 讀取工具（10 個）

`list_issues`、`get_issue`、`list_attachments`、`download_attachment`、`list_projects`、`list_trackers`、`list_issue_statuses`、`list_priorities`、`list_custom_fields`、`get_current_user`

每個新增參數：

```python
site: Annotated[str | None, Field(description="站台代號；省略則查詢所有已設定的站台。")] = None
```

工具本體改動僅兩行，原有邏輯抽成與站台無關的私有函式以便單獨測試：

```python
async def get_issue(site=None, issue_id=..., include=None):
    clients = sites.resolve(site)
    return await fan_out(clients, lambda c: _fetch_issue(c, issue_id, include),
                         none_on_not_found=True)
```

### 寫入工具（5 個）

`create_issue`、`update_issue`、`add_issue_note`、`create_project`、`upload_attachment`

```python
site: Annotated[str | None, Field(description="站台代號；設定多個站台時必填。")] = None
```

一律走 `resolve_for_write()`，永不扇出。工具說明需明確寫出「這會實際寫入指定站台」。

### `list_sites`（新增）

```json
{
  "configured_sites": [
    {"name": "main", "url": "https://redmine.company.com/redmine", "description": "公司正式站"},
    {"name": "client-b", "url": "https://redmine.client-b.com", "description": "客戶 B 的正式站"}
  ]
}
```

鍵名刻意用 `configured_sites` 而非 `sites`：其他讀取工具的 `sites` 是「站台名稱 → payload」的物件，這裡是陣列。同一個鍵名對應兩種結構會讓模型難以穩定解析。

唯讀、不發出任何 HTTP 請求，純粹回報本機設定。用途是讓模型把口語（「客戶 B 那邊」）對應到站台代號。站台名稱清單同時寫進 server instructions，`list_sites` 是讓模型能主動確認的備援。

### `MetadataCache`

改為每站一份。跨站 metadata 查詢只有第一次付 N 次請求的成本。

## 回傳格式

### 讀取工具：一律按站分組

即使只設定一個站台也分組。結構穩定優先於訊息精簡——模型永遠知道資料來自哪一站，不需要依設定推斷回傳形狀。

```json
{
  "sites": {
    "main":     { "...": "該站的 payload" },
    "client-b": null,
    "staging":  { "error": "API key 無效或權限不足（HTTP 403）" }
  }
}
```

`get_issue` 與 `list_attachments` 這兩個 id 查找工具額外附上 `found_in`（值為 payload 非 `null` 且非錯誤的站台名稱），省去模型自行掃描：

```json
{
  "issue_id": 1111,
  "found_in": ["main"],
  "sites": { "main": {...}, "client-b": null }
}
```

### `download_attachment` 不分組

`download_attachment` 雖屬讀取工具，但最終必定落在單一站台（多站命中即拒絕執行），因此回傳沿用現有結構並補上 `site`，不使用 `sites` 分組信封：

```json
{ "site": "main", "attachment_id": 42, "filename": "42_需求規格.pdf",
  "path": "D:/redmine-downloads/main/42_需求規格.pdf", "bytes": 183422 }
```

多站命中或無站命中時不回傳此結構，而是拋出錯誤（見「附件」一節）。

### 寫入工具：不分組

寫入必定針對單一站台，回傳沿用現有結構並補上 `site` 欄位：

```json
{ "site": "main", "id": 4521, "subject": "...", "uploads": 0 }
```

### 分頁語義

跨站時 `limit` 為**每站各取** N 筆，非全域 N 筆。各站有各自的 `total_count` 與 `offset`：

```json
{
  "sites": {
    "main":     { "total_count": 312, "offset": 0, "limit": 25, "issues": [...] },
    "client-b": { "total_count": 8,   "offset": 0, "limit": 25, "issues": [...] }
  }
}
```

不做跨站合併排序。正確的全域排序需要先撈回所有站台的完整資料，代價與正確性都不划算；分組結果對模型而言反而更清楚，也避免產生看似正確、實則漏項的分頁。

## 附件

### 下載路徑隔離

落地路徑改為 `<download_dir>/<站台名>/<附件id>_<淨化後檔名>`。

附件 id 是每站各自編號的，共用下載目錄時 A 站的附件 42 會靜默覆蓋 B 站的附件 42。現行設計已特地用暫存檔加 `os.replace` 避免「下載失敗弄壞既有同名檔案」，多站台等於從另一個入口把同一類問題放回來。

`safe_destination()` 的目錄邊界檢查基準點改為站台子目錄。既有的檔名淨化、Windows 保留裝置名處理、暫存檔與原子替換一律不動。

舊格式參數檔的站台名為 `default`，因此其下載檔案會落在 `<download_dir>/default/`。

### 同源檢查

`assert_same_origin()` 改用該站台自己的 `url` 比對。這條檢查本來就是每站獨立的，多站後更形重要：A 站的附件網址不應通過 B 站的同源檢查。

### 有副作用的操作不自動跨站執行

`download_attachment` 會寫入本機檔案，是唯一的例外處理：省略 `site` 時先跨站定位「哪些站有這個附件 id」，

- 恰好一站命中 → 直接下載
- 多站命中 → **不下載**，回報並要求明寫 `site`
- 無站命中 → 回報找不到

```
附件 42 在多個站台都存在（main、client-b），請明寫 site 指定要下載哪一個
```

查詢可以廣撒，落地檔案不行。

### 上傳

`upload_attachment` 屬寫入工具，`site` 必填，不存在歧義。`upload_dir` 依全域／站台繼承規則解析，`safe_source()` 的邊界基準為該站台實際生效的 `upload_dir`。

## 伺服器組裝

```python
def create_server(sites: SiteRegistry) -> MCPServer:
    ...
```

`create_server` 改收 `SiteRegistry`，四個 `register(mcp, sites)` 隨之調整。

Server instructions 加入站台清單。既有的不可信輸入警語完全保留。

`tools/list` 的快取提示維持 `scope="public"`：工具清單仍然完全靜態，`site` 只是一個字串參數，不隨呼叫者情境改變。

## 測試計畫

現行 198 個測試中，`conftest.py` 的 `settings` fixture 產出 `Settings` 並直接餵給 `RedmineClient`。由於 `RedmineClient` 改收 `SiteSettings`，該 fixture 改為產出 `SiteSettings`（欄位幾乎相同，多一個 `name="default"`），既有測試的使用方式不變。另新增 `multi_site_registry` fixture 提供含多站台與各自 MockTransport 的 `SiteRegistry`，新行為以它覆蓋。

必須涵蓋的新情境：

**設定層**
- 舊格式參數檔仍可用，站台名為 `default`
- 新舊格式並存時拋 `ConfigError`
- 站台缺 `url` 或 `api_key` 時拋 `ConfigError` 並指出是哪一站
- 站台名稱含 `..`、`/`、`\`、空白、中文時拋 `ConfigError`
- 站台層級設定正確覆寫全域層級
- `upload_dir` 未設定時沿用同層級的 `download_dir`
- `REDMINE_URL` 在單站時覆寫、多站時忽略並記 warning
- 站台清單為空時拋 `ConfigError`

**SiteRegistry**
- `resolve(None)` 回傳全部；`resolve("不存在")` 拋錯且訊息列出可用名稱
- `resolve_for_write(None)` 在多站時拒絕、單站時放行
- `aclose()` 在某站失敗時仍關閉其餘站台

**扇出**
- 某站 5xx 時其他站結果照常回傳
- 404 在 `none_on_not_found=True` 時轉 `null`、否則為 `{"error": ...}`
- 非預期例外不把原始訊息帶進回傳
- 並行度上限生效

**工具層**
- 讀取工具省略 `site` 時查詢全部站台
- 讀取工具回傳一律含 `sites` 鍵，單站台時亦然
- 寫入工具省略 `site` 在多站時被拒絕，錯誤訊息含可用站台名稱
- 寫入工具回傳含 `site` 欄位
- `list_sites` 不發出 HTTP 請求
- 工具總數為 16

**附件**
- 下載落地於 `<download_dir>/<站台名>/`，不同站台的同 id 附件不互相覆蓋
- 多站命中時拒絕下載並列出命中的站台
- 同源檢查使用該站台自己的 url——B 站的附件網址不通過 A 站的檢查

## 文件更新

- `README.md`：參數表改為全域／站台兩層，新增多站台範例
- `SETUP.md`：新增多站台設定章節；排錯表新增「寫入被拒：未指定 site」「站台名稱不合法」兩列
- `config.example.toml`：改為多站台格式，並保留舊格式仍可用的說明
- 工具清單由 15 改為 16

## 風險與取捨

**每次讀取為 N 次 HTTP 請求。** 並行送出並限制並行度為 8，但每個請求是一個 curl 子行程，站台數到十幾個時延遲會有感。這是「省略 `site` 即查全部」的直接代價，換得的是使用者不必先知道單號屬於哪一站。

**回傳結構改變。** 讀取工具一律分組是破壞性變更。目前此套件僅內部使用且版本為 0.1.0，現在改的成本最低。

**站台名稱同時是識別碼與目錄名。** 兩個用途綁在一起，因此名稱規則必須以較嚴格的那個（檔案系統安全）為準，並在啟動時驗證。
