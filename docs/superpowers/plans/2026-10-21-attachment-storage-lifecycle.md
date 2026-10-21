# Attachment Storage Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 自動管理附件下載目錄，直接上傳對話取得的本機附件，並安全執行 7 天壓縮與封存後 30 天清除。

**Architecture:** 將平台路徑、檔案安全及 OS 鎖、儲存狀態與清理排程拆為附件儲存套件。設定層移除路徑決策；附件工具使用可注入的 AttachmentStore，server lifecycle 管理背景清理。清理只處理可驗明歸屬的紀錄，任何不確定狀態保留檔案。

**Tech Stack:** Python >=3.11、既有 MCP 2.x／AnyIO、標準函式庫 pathlib/json/zipfile/hashlib/threading、Windows msvcrt／POSIX fcntl 鎖、pytest、ruff、mypy；不新增產品依賴。

**Spec:** `docs/superpowers/specs/2026-10-21-attachment-storage-lifecycle-design.md`（2026-10-21 使用者已確認）。

## Global Constraints

- Windows：%LOCALAPPDATA%/redmine-mcp/attachments；變數缺漏時用使用者家目錄下 AppData/Local。
- macOS：~/Library/Application Support/redmine-mcp/attachments。
- Linux：$XDG_DATA_HOME/redmine-mcp/attachments；未設定或非絕對路徑時用 ~/.local/share/redmine-mcp/attachments。
- 只管理套件建立的本機暫存：閒置滿 7 天後壓縮，壓縮完成後保留 30 天再刪除。
- 使用者原始檔、既有自訂目錄與 Redmine 伺服器附件均不納入清理。期限固定，不新增管理參數。
- 行程內最多每小時掃描一次，啟動掃描例外；沒有常駐行程或操作時不承諾準點清理，不新增排程器。
- 保留 max_attachment_mb、工具輸入與既有回傳欄位；base64 圖片不必落地。
- 不跟隨符號連結／junction／reparse point；清理不得遞迴刪除整個目錄。
- 日誌送 stderr；不可包含 API key 或檔案內容。
- 執行前讀取規格及工作樹狀態；工作樹已有其他變更，隔離實作且不得納入他人檔案。
- 下列命令除 Git 路徑外均在 `redmine-mcp/` 執行；以 `uv run` 使用專案環境。不可連到正式 Redmine 執行破壞性驗證。

## Review Focus

1. 站台 URL 的子路徑與連接埠不可錯誤合併；Task 1 驗證 scheme/host 大小寫、預設 port、尾斜線及子路徑。
2. 來源檔在 stat 與讀取間被替換或變大，不可讀取裝置或超額資料；Task 2、5 驗證 handle 身分與有限讀取。
3. 封存發布後原件又被修改，中斷恢復不可刪除新版內容；Task 4 驗證重新比對 fingerprint。
4. 直接連線建立 server 而未走 CLI 時仍有啟動清理；Task 6 驗證 MCP lifecycle，不只 main()。
5. 一般設定更新及舊設定轉換可能重寫棄用路徑；Task 7 驗證保留舊值且不存取其目錄。

## File Structure

新增 `redmine-mcp/src/redmine_mcp/attachment_storage/`：

- `paths.py`：平台根目錄、URL 儲存鍵。
- `file_ops.py`：拒絕連結的一般檔案開啟、受限讀取、跨行程鎖及原子 JSON 寫入。
- `store.py`：下載項目與狀態紀錄、管理來源借用；公開 AttachmentStore。
- `lifecycle.py`：驗證 ZIP、狀態恢復、到期清理；只透過 store 的紀錄處理項目。
- `scheduler.py`：每小時節流、背景工作與停止訊號。
- `__init__.py`：匯出 AttachmentStore；避免 import 時建立目錄或開始執行緒。

修改既有 `tools/attachments.py`、`server.py`、`config.py`、`config_file.py`、`cli.py`、`setup/{config_ops,toml_edit,wizard,verify}.py`。測試新增 `test_attachment_{storage_paths,file_ops,store,lifecycle,scheduler}.py`，並調整既有附件、設定、精靈及 server 測試與 conftest.py。文件修改根 README.md、redmine-mcp/{README.md,SETUP.md,config.example.toml}；只有搜尋找到過時附件指示時才更新對應 skill，不做無關改寫。

### Task 1: 自動路徑與站台隔離

**Files:** Create `attachment_storage/paths.py`, `attachment_storage/__init__.py`（上述 src/redmine_mcp 下）；Test `tests/test_attachment_storage_paths.py`。

**Interfaces:** `user_attachment_dir() -> Path`；`site_storage_key(url: str) -> str`。URL key 為正規化 URL 的完整 SHA-256 hex，正規化 scheme/host 小寫、預設 port 省略、移除尾斜線，保留非預設 port 與子路徑；拒絕 userinfo/query/fragment，與既有 URL 驗證對齊。

- [x] 寫失敗測試：三平台根目錄等於規格值；缺漏變數回退；Linux 相對 XDG 被忽略；改 cwd 不改根目錄；相同正規 URL key 相等，異子路徑／port key 不等，key 不含憑證或站台代號。
- [x] 執行 `uv run pytest tests/test_attachment_storage_paths.py -q`，確認新模組缺漏造成 FAIL。
- [x] 實作上述函式，純計算且不建立目錄；Windows 相對 LOCALAPPDATA 同樣回退家目錄。
- [x] 重跑同命令，預期全部 PASS。
- [x] 只提交此任務檔案：`feat: resolve automatic attachment storage paths`。

### Task 2: 安全檔案操作與 OS 鎖

**Files:** Create `attachment_storage/file_ops.py`；Test `tests/test_attachment_file_ops.py`。

**Interfaces:** `try_lock(path: Path) -> ContextManager[bool]`；`open_regular(path: Path) -> ContextManager[BinaryIO]`；`read_limited(stream: BinaryIO, max_bytes: int) -> bytes`；`atomic_json(path: Path, payload: dict[str, object]) -> None`；`assert_managed_path(root: Path, path: Path) -> None`。

- [x] 寫失敗測試：子行程持鎖時第二行程取得 False，終止持鎖者後取得 True；拒絕連結及 reparse ancestor、root 外路徑、管線、裝置與目錄；max_bytes+1 輸入拒絕；atomic_json 替換前失敗保留舊紀錄。
- [x] 執行 `uv run pytest tests/test_attachment_file_ops.py -q`，確認 FAIL。
- [x] 實作 Windows handle 開啟並檢查 reparse／file identity、POSIX no-follow 非阻塞開啟及 fstat；開啟後驗證一般檔案，避免管線阻塞。鎖使用 msvcrt／fcntl 且非阻塞，鎖檔不刪除以免 inode 競爭。JSON 同目錄唯一臨時檔、flush/fsync 後 replace；受限讀取最多 max_bytes+1。
- [x] 重跑測試；Windows 實測 junction/reparse 與子行程鎖，不可以 skip 當成通過。POSIX 專屬分支在 POSIX 環境驗證，無環境時明列未實測限制。
- [x] 提交：`feat: add guarded attachment file operations`。

### Task 3: 管理項目與原子下載發布

**Files:** Create `attachment_storage/store.py`；Modify `attachment_storage/__init__.py`；Test `tests/test_attachment_store.py`。

**Interfaces:** `AttachmentStore(root: Path | None = None, *, clock: Callable[[], datetime] = utc_now)`；`store.begin_download(url: str, filename: str) -> ContextManager[PendingDownload]`；`PendingDownload.path: Path` 為下載臨時位置；`PendingDownload.publish() -> Path`；`store.borrow_source(path: Path, max_bytes: int) -> ContextManager[bytes]`；`store.sweep(stop: threading.Event | None = None) -> None`（Task 4 實作）。

紀錄為每項目 JSON：schema_version=1、URL key、UUID 項目 id、單層檔名、UTC created_at/last_used_at、state、size、sha256、可空 archived_at；state 為 active/archive_published/archived。路徑由經驗證的 id 推導，不採信紀錄中的任意路徑。鎖放固定 locks 子目錄，紀錄與項目一對一。

- [x] 寫失敗測試：同名下載並行仍得到不同路徑；publish 之前不可取得完成路徑；失敗下載保留未登記半成品且不列入清理；紀錄寫失敗仍保留完成檔案並警告；借用外部檔不登記或修改，借用管理檔刷新時間且持鎖。
- [x] 執行 `uv run pytest tests/test_attachment_store.py -q`，確認 FAIL。
- [x] 實作 Task 1、2 介面的組合，項目 UUID 獨立目錄；記錄發布失敗時完成路徑仍可回傳，警告說明未納管；使用鎖期間讀取 bytes 後刷新時間，外部來源不建立管理根。
- [x] 重跑上述三個新增測試檔，預期 PASS。
- [x] 提交：`feat: track managed attachment downloads and usage`。

### Task 4: 可復原的壓縮與到期清理

**Files:** Create `attachment_storage/lifecycle.py`；Modify `attachment_storage/store.py`；Test `tests/test_attachment_lifecycle.py`。

**Interfaces:** `sweep_store(store: AttachmentStore, stop: threading.Event | None = None) -> None`；由 `AttachmentStore.sweep` 委派，沿用 clock/root。政策常數 `IDLE_SECONDS=604800`、`ARCHIVE_SECONDS=2592000`。

- [x] 寫失敗測試：以固定 UTC 時間驗證 7 天及封存後 30 天的前一微秒、整點與後一微秒；讀取刷新、未來時間跳過、重啟保留 archived_at；ZIP 解出內容與原件相等，CRC/大小/hash 全部吻合才移除原件。
- [x] 寫失敗注入測試：壓縮中、replace 後、archive_published 寫入後、刪原件後中止再跑；磁碟滿、權限錯誤、CRC/hash 不符、來源改變、原件遭替換均不得誤刪。archive_published 復原重新驗證原件與 ZIP；若原件已變動則保留雙方並警告，不沿用舊 hash 刪除。
- [x] 寫清理範圍測試：未知檔、毀損／未知 schema 紀錄、外部原件、舊路徑、junction 及外部誘導路徑完全不變；持鎖項目跳過；清理無遠端 HTTP 呼叫。
- [x] 執行 `uv run pytest tests/test_attachment_lifecycle.py -q`，確認缺功能造成 FAIL。
- [x] 實作非阻塞 sweep 鎖與逐項目鎖；ZIP_DEFLATED 同目錄臨時輸出，完整串流驗證後發布，持久寫 archive_published，再 unlink 經重新驗證的原件，最後寫 archived。到期刪 ZIP 後再刪紀錄；中斷重跑容忍 ZIP 已不存在，但不得據此刪 active 原件。stop 於項目間檢查，半成品及失敗紀錄保留。
- [x] 重跑新增測試；以子行程驗證兩個 sweep 互斥，borrow_source 期間 sweep 不刪除來源，預期 PASS。
- [x] 提交：`feat: archive and expire managed attachment files safely`。

### Task 5: 附件工具接入與直接上傳

**Files:** Modify `tools/attachments.py`, `tests/{conftest,test_attachment_tools,test_upload_tools,test_inline_images}.py`。

**Interfaces:** `attachments.register(mcp, sites, *, store: AttachmentStore | None = None, on_operation: Callable[[], None] | None = None) -> None`，保留所有工具參數與回傳欄位；store 在 register 時建立但不做 I/O。新增 fixture 將所有測試的預設管理根導向 tmp_path。

- [x] 更新實際 MCP call_tool 測試：無目錄設定可下載及上傳，外部一般檔直接上傳內容正確且原件不變，base64 不建立檔案；同站同附件重下載不同 path，跨站相同 id 不覆蓋；不存在／空檔／超限／連結／不可讀來源明確失敗。
- [x] 執行 `uv run pytest tests/test_attachment_tools.py tests/test_upload_tools.py tests/test_inline_images.py -q`，確認行為變更測試 FAIL。
- [x] 移除下載與上傳設定閘門，下載串流寫入 PendingDownload.path、成功 publish；來源讀取放 worker thread，透過 borrow_source 在鎖內完成有限讀取。沿用同源、檔名、MIME 與圖片驗證。管理來源不可借用時回報暫時使用中，外部來源不可達時回報實際錯誤；finally 釋放後呼叫 on_operation，觸發錯誤不得遮蔽原操作結果。
- [x] 更新工具描述為直接使用對話附件可存取路徑、暫存有期限；刪除 safe_source 舊目錄白名單邏輯並轉移有意義防護測試，不能只刪測試換 PASS。
- [x] 重跑上述測試及 `tests/test_issue_write_tools.py`，預期 PASS，token 掛載與圖片內嵌維持原行為。
- [x] 提交：`feat: upload conversation attachments without directory setup`。

### Task 6: server 啟動與節流背景清理

**Files:** Create `attachment_storage/scheduler.py`, `tests/test_attachment_scheduler.py`；Modify `server.py`, `tests/test_server.py`。

**Interfaces:** `CleanupScheduler(store: AttachmentStore, *, monotonic: Callable[[], float] = time.monotonic)`；`start() -> None`、`trigger() -> None`、`close() -> None`。start 啟動一次強制清理；trigger 每 3600 秒最多啟動一次；close 設 stop、不無限等待。create_server 以可選 `store: AttachmentStore | None = None` 注入，共用同一 store 與 scheduler。

- [x] 寫失敗測試：Fake clock 驗證啟動不受節流、3599 秒不重掃、3600 秒可掃；慢掃描期間 trigger 不疊加；掃描例外不外洩，close 快速返回且不再排新工作。
- [x] 寫 MCP lifecycle 測試：不經 main() 啟動 create_server 仍執行 start/close，慢壓縮不阻擋工具，清理錯誤不阻擋 server，stdout 無日誌。僅建構 server 而未進入 lifecycle 時不可產生背景執行緒。
- [x] 執行 `uv run pytest tests/test_attachment_scheduler.py tests/test_server.py -q`，確認 FAIL。
- [x] 依本機 MCP SDK 的 lifecycle 介面接入，保留既有 server 組裝；背景使用可停止 daemon thread，finally 釋放鎖，附件 register 注入 scheduler.trigger。初始化與掃描例外只 stderr warning。
- [x] 重跑同命令及附件工具測試，預期 PASS。
- [x] 提交：`feat: run attachment cleanup through server lifecycle`。

### Task 7: 移除路徑設定與完成回歸

**Files:** Modify `config.py`, `config_file.py`, `cli.py`, `setup/{config_ops,toml_edit,wizard,verify}.py`, `tests/conftest.py` 與受影響測試；文件見 File Structure。

**Interfaces:** SiteSettings 移除 download_dir/upload_dir；分開 `LEGACY_ATTACHMENT_KEYS`（只讀相容）與 `GLOBAL_KEYS`／`SITE_KEYS`（現行可寫鍵）。`render_new_config(block: str) -> str`；`set_global` 移除兩個路徑參數；cli 移除公開選項，明寫舊選項時顯示已改自動管理並非零退出。

- [x] 寫失敗測試：舊全域／站台 TOML 及環境變數接受但忽略，棄用提示一次且不輸出值；一般修改 max_attachment_mb 或站台資訊保留舊路徑文字與目錄內容；舊格式轉換也保留。精靈互動與 assume-yes 均不詢問或生成路徑，help 不列舊選項；顯式舊選項與舊鍵寫入／clear 回報自動管理而不寫檔。
- [x] 執行 `uv run pytest tests/test_config.py tests/test_config_file.py tests/test_cli.py tests/test_setup_config_ops.py tests/test_setup_toml_edit.py tests/test_setup_wizard.py tests/test_setup_verify.py -q`，確認 FAIL。
- [x] 實作設定相容及建構子調整；更新所有 SiteSettings 建立點與 fixtures。精靈只說明自動管理；既有文字編輯器保留未編輯舊鍵。舊環境變數辨識只用於棄用提示，不做路徑展開或存取。
- [x] 同步文件列出平台目錄、一般檔直接讀取、僅套件觀察的最後使用時間、固定期限、ZIP 可手動解開、重新下載及原設定停用語意已移除；搜尋 `rg -n 'download_dir|upload_dir|REDMINE_DOWNLOAD_DIR|REDMINE_UPLOAD_DIR'`，逐筆確認僅剩相容實作／測試／升級說明或歷史設計文件。
- [x] 執行 `uv run pytest -q`、`uv run ruff check .`、`uv run mypy` 及 `git diff --check`；全部 PASS 才宣稱通過。確認平台專屬分支實測範圍，不把 skip 當成功證據。
- [x] 提交只屬本任務檔案：`feat: retire attachment directory configuration`。

## Plan Self-Review

規格的路徑、站台隔離、直接上傳、設定相容、原子下載、使用時間、壓縮驗證、清理期限、並行鎖、中斷復原與文件驗收分別對應 Tasks 1–7。五項 Review Focus 已納入其負責任務測試。所有新模組介面於本計畫定義；生命週期與工具共用同一 store，外部來源從不登記。本計畫已由使用者核准並選擇子代理實作，七項任務與審查均完成；最終驗證如下。

## 最終驗證與交付（2026-10-21）

七項任務均完成逐項規格及品質審查；整體審查發現的設定編輯語意、POSIX 持久化順序、下載完成時間、未來時間警告與重複測試問題均已修正，獨立複審無未解的重要發現。

- 功能提交：e383d2094c8ff2c18917db83f0c08a49464956c9。
- 格式提交：dc2766b9ef384872cda8656970307b9c91485669，只刪除測試檔尾端多餘空白行，未變更行為。
- 最終完整測試：`python -m pytest -q -rs`，1045 passed、3 skipped，67.94 秒。
- 全專案 Ruff：`python -m ruff check .`，All checks passed。
- 全專案 mypy：`python -m mypy`，37 個來源檔通過；修正代理另完成 Linux 目標靜態檢查。
- 測試使用既有 Python 環境，PYTHONPATH 明確指向隔離工作區的 redmine-mcp/src。未以原工作目錄的舊程式代替驗證。
- 三個略過分支：POSIX FIFO／device、POSIX chmod，以及缺少專案 config.toml 的條件分支。

原生 Windows 檔案鎖、junction、重解析競爭、原子發布及資料保留均有實測。POSIX syscall 順序以模擬測試與 Linux 靜態檢查驗證；未進行原生 POSIX、斷電或真實聊天宿主附件整合測試，不宣稱這些環境已通過。宿主附件仍須提供 MCP 執行環境可讀的實際路徑。

實作調整：新增 PendingDownload.open_writer 與 client.download_to_stream，避免受控下載使用裸路徑寫入；Windows 原生 helper 獨立分檔；預設管理根延後解析；清理入口使用 lifecycle.sweep。設定編輯採完整 TOML 陳述式及寫入前語意核對，保留未修改的舊值。

成果保留在本次原生受管隔離工作區；原工作目錄的既有變更未納入，尚待使用者選擇整合方式。
