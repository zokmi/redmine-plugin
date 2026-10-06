# 設計：以套件自帶的 config.toml 取代環境變數作為設定來源

日期：2026-08-05
狀態：已核准，待實作

## 背景與問題

目前所有設定都來自 `os.environ`（`src/redmine_mcp/config.py` 的 `load_settings()`），API key 依設計只放使用者層級環境變數，不進任何設定檔。

這個做法在實務上踩到一個反覆出現的坑：`[Environment]::SetEnvironmentVariable(..., "User")` 只寫入登錄檔，**既有行程不會重讀**。VSCode／Claude Code 若在設定變數之前就已啟動，MCP 子行程繼承不到 `REDMINE_API_KEY`，`load_settings()` 拋 `ConfigError` 後行程立刻結束，前端只看到 `MCP error -32000: Connection closed`，錯誤原因完全被遮蔽。唯一解法是完整關閉所有 VSCode 視窗再重開，`/mcp reconnect` 無效——因為缺的東西在行程層級補不回來。

實測證據：`User` scope 有值（40 字元）、`Process` scope 為 `UNSET`；手動注入該值後 server 可正常回應 `initialize`，確認程式碼與站台設定本身無誤。

## 目標

- 設定改由套件自帶的參數檔提供，不再依賴行程繼承環境變數
- 改設定後不需完整重啟 VSCode，`/mcp reconnect redmine` 即生效
- 保留臨時覆寫能力（測試、雙帳號）
- 不破壞既有的驗證邏輯與測試

## 非目標

- 不改動 Redmine API 呼叫、附件處理、傳輸層等任何既有行為
- 不做設定熱重載（MCP server 生命週期內設定固定）
- 不支援多組具名 profile

## 決策

| 項目 | 決定 | 理由 |
|---|---|---|
| 檔案位置 | 專案根目錄 `config.toml` | 符合「套件自帶」，與程式碼同處易於維護 |
| 格式 | TOML | Python 3.11 內建 `tomllib`，零新增依賴；支援註解 |
| 是否含金鑰 | 含 | 單一檔案搞定，消除環境變數繼承問題 |
| 優先序 | 參數檔為主，環境變數覆寫同名鍵 | 日常只維護一個檔；保留臨時切換能力，且不破壞現有 `.claude.json` 的 `env` 設定 |
| 實作形態 | 新增 `config_file.py`，攤平後餵給現有 `load_settings()` | 職責單一；既有驗證邏輯與測試零改動 |

### 已評估但不採用的方案

- **改寫 `load_settings()` 直接讀 TOML 巢狀結構**：檔案可讀性略佳，但驗證邏輯需搬遷，`load_settings` 同時扛讀檔與驗證兩個職責，既有測試全部要改寫。收益不足。
- **引入 `pydantic-settings`**：多一個依賴（現僅 `mcp` 與 `httpx`），且其錯誤訊息會 echo 欄位值，與「錯誤訊息不得含金鑰」的既有約束（`tests/test_config.py` 的 `test_錯誤訊息不含_API_key`）衝突。

## 架構

```
redmine-mcp/
├─ config.toml            ← 實際設定（列入 .gitignore）
├─ config.example.toml    ← 範例，進版控，只含佔位字串
└─ src/redmine_mcp/
   ├─ config.py           ← 不修改
   └─ config_file.py      ← 新增：讀檔 + 攤平
```

### 元件：`config_file.py`

單一職責：把 TOML 檔案讀成 `dict[str, str]`，鍵名轉為環境變數形式。不做語意驗證（那是 `config.py` 的事）。

對外介面：

- `DEFAULT_CONFIG_PATH: Path` — 以套件位置推算（`Path(__file__).resolve().parents[2] / "config.toml"`），不依賴 CWD，Claude Code 從任何目錄啟動都找得到
- `resolve_config_path(env: Mapping[str, str] | None = None) -> Path` — 若 `REDMINE_CONFIG` 有值則用它，否則回 `DEFAULT_CONFIG_PATH`
- `load_config_file(path: Path) -> dict[str, str]` — 讀檔攤平；檔案不存在回 `{}`
- `build_env(env: Mapping[str, str] | None = None, path: Path | None = None) -> dict[str, str]` — 合併結果：參數檔為底，`os.environ` 同名鍵覆寫；`path` 供測試顯式指定參數檔位置，未給時依 `resolve_config_path()` 決定

### 檔案格式

扁平鍵，鍵名即環境變數去掉 `REDMINE_` 前綴後轉小寫，一對一對應，不另造名稱體系：

```toml
# Redmine 站台根位址，要含子路徑
url = "https://redmine-eos.tavuron.com/redmine"
# 個人 API 存取金鑰（Redmine → 我的帳戶 → API 存取金鑰）
api_key = "<your-api-key>"
# 附件下載目錄；留空或移除則停用下載工具
download_dir = "C:/Users/kenny/Desktop/MCP/downloads"
# 允許上傳的來源目錄；未設定時沿用 download_dir
upload_dir = "C:/Users/kenny/Desktop/MCP/downloads"
# 單一附件下載／上傳大小上限（MB）
max_attachment_mb = 10
```

鍵名映射規則：`url` → `REDMINE_URL`、`api_key` → `REDMINE_API_KEY`、`max_attachment_mb` → `REDMINE_MAX_ATTACHMENT_MB`，即 `"REDMINE_" + key.upper()`。

## 資料流

```
config.toml ──load_config_file──┐
                                ├─► build_env() ──► load_settings(merged) ──► Settings
os.environ ────同名鍵覆寫────────┘
```

`max_attachment_mb` 在 TOML 中是整數，攤平時一律以 `str()` 轉為字串，讓 `load_settings()` 現有的數字解析與錯誤處理照舊生效——驗證只有一處。

`__main__.py` 的改動僅一行：`load_settings()` → `load_settings(build_env())`，並在啟動 log 增印實際使用的參數檔路徑。

## 錯誤處理

| 情況 | 行為 |
|---|---|
| `config.toml` 不存在 | 不視為錯誤，回空字典；純環境變數的舊用法仍可運作 |
| TOML 語法錯誤 | 拋 `ConfigError`，帶 `tomllib` 的行號與欄號 |
| 未知鍵（如 `api_kye`） | 輸出 stderr 警告列出鍵名，不中斷執行——避免設定悄悄失效 |
| 值為巢狀 table 或陣列 | 拋 `ConfigError`，指出該鍵只接受單一值 |
| 合併後仍缺 `url` 或 `api_key` | 沿用現有 `ConfigError`，訊息改為引導「複製 config.example.toml 為 config.toml 並填入」 |

**約束：所有錯誤訊息、警告與 log 一律不得輸出 `api_key` 的值。** TOML 解析錯誤只透出 `tomllib` 的位置資訊，不回傳原始行內容。

啟動時 log 印出實際讀取的參數檔路徑（未讀到檔則明確註明），讓排錯第一步就能確認讀的是哪個檔。

## 測試策略

新增 `tests/test_config_file.py`，全部使用 `tmp_path`，不觸碰真實檔案：

1. 正常讀取並正確映射鍵名（`url` → `REDMINE_URL` 等）
2. 檔案不存在時回空字典，不拋錯
3. TOML 語法錯誤拋 `ConfigError`
4. 未知鍵產生警告但不中斷，且警告內容含該鍵名
5. 整數值（`max_attachment_mb = 10`）被轉為字串 `"10"`
6. 巢狀 table 或陣列值拋 `ConfigError`
7. 環境變數覆寫參數檔的同名值
8. `REDMINE_CONFIG` 可改變讀取路徑
9. 錯誤訊息與警告皆不含 `api_key` 的值

**驗收條件：既有 `tests/test_config.py` 一行不改且全部通過**——這是「`load_settings()` 未被破壞」的直接證據，也是選擇此實作方案的核心驗證點。

完整驗證指令：`uv run pytest -q`、`uv run ruff check .`、`uv run mypy`。

## 安全考量

金鑰進入檔案是刻意的取捨，以下為配套措施：

- `config.toml` 列入 `.gitignore`；`config.example.toml` 只含佔位字串
- **本 repo 位於 `Desktop` 底下，若啟用 OneDrive 資料夾備份，`config.toml` 會被同步上雲。** 文件須明確警示，並建議收緊 ACL：
  ```powershell
  icacls config.toml /inheritance:r /grant:r "$env:USERNAME:(R)"
  ```
- 維持既有約束：錯誤訊息不含金鑰；金鑰僅走 curl stdin 設定檔，不出現在 argv
- 不得使用 `--scope project`（`.mcp.json` 會進版控）

## 文件更新

- `SETUP.md`：第 2 節改為說明 `config.toml`；第 3 節註冊步驟移除設定環境變數的前置動作；第 6 節安全注意事項改寫，寫明新風險與 ACL 建議
- `SETUP.md` 排錯表：新增「改了 `config.toml` 需重新連線 MCP server 才生效」；原「缺少必填環境變數」該列改為指向參數檔
- `README.md`：設定表格改以參數檔為主

## 附帶效果

原本的 `Connection closed` 問題根治：金鑰不再依賴行程繼承，改設定後不需完整重啟 VSCode，`/mcp reconnect redmine` 即可。
