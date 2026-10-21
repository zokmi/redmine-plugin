# Redmine MCP Server

這個 MCP server 是根目錄 Redmine plugin 的執行元件，提供 Redmine issue、專案、附件與 metadata 的查詢及維護能力，共 16 個工具，支援多個 Redmine 站台。

請先依 [根目錄 README](../README.md) 透過 Claude Code 或 Codex 安裝 plugin，再依 [SETUP.md](SETUP.md) 建立站台參數。這個目錄不提供獨立安裝流程；plugin 會以 `uvx --from` 載入這份原始碼。

## 設定

```powershell
uvx --python 3.13 --from ./redmine-mcp redmine-mcp setup
```

參數檔使用者目錄如下：

| 平台 | 位置 |
| --- | --- |
| Windows | `%APPDATA%\redmine-mcp\config.toml` |
| macOS／Linux | `~/.config/redmine-mcp/config.toml` |

## 開發

```powershell
uv sync
uv run pytest -q
uv run ruff check .
uv run mypy
```

MCP 啟動路徑會保持 stdout 只傳 MCP 訊息，診斷資訊寫到 stderr。API key 只放在 header，不會出現在 URL 或錯誤訊息中；附件路徑與大小也會在傳輸前檢查。


## 附件與自動暫存

上傳普通檔案時，將使用者提供的本機檔案路徑直接傳給 `upload_attachment.file_path`，不必先複製到特定目錄。對話附件只有在主程式實際提供可讀的本機路徑時才能直接使用；主程式未提供時，需先取得可讀路徑，或使用 `content_base64`。Base64 圖片不必落地。

下載的附件與套件建立的暫存依站台網址隔離，存放在下列使用者資料目錄，無需設定：

| 平台 | 附件根目錄 |
| --- | --- |
| Windows | `%LOCALAPPDATA%/redmine-mcp/attachments`；未設定或非絕對路徑時用 `~/AppData/Local/redmine-mcp/attachments` |
| macOS | `~/Library/Application Support/redmine-mcp/attachments` |
| Linux | `$XDG_DATA_HOME/redmine-mcp/attachments`；未設定或非絕對路徑時用 `~/.local/share/redmine-mcp/attachments` |

套件只以自身觀察到的最後使用時間判定閒置；直接在其他程式開啟檔案不會更新這個時間。套件建立的暫存閒置滿 7 天後壓成 ZIP，驗證完成才移除未壓縮的暫存；從壓縮完成起再保留 30 天，之後刪除 ZIP 與對應記錄。期限固定，沒有新增管理參數；`max_attachment_mb` 仍控制單一附件大小上限。

清理在 server 啟動時觸發，附件操作也會觸發，但同一行程最多每小時掃描一次。沒有常駐行程或附件操作時不保證準點清理。需要舊暫存時，可在刪除前手動解開 ZIP，或從 Redmine 重新下載附件。

使用者的原始檔、舊自訂目錄與 Redmine 伺服器附件不會被遷移或清理。舊 TOML 的 `download_dir`、`upload_dir`（含全域、站台與扁平格式）及 `REDMINE_DOWNLOAD_DIR`、`REDMINE_UPLOAD_DIR` 環境變數仍可存在，但值一律忽略，載入時只警告名稱。留白或刪除舊設定不再停用附件功能。一般設定修改與舊格式轉換會保留未修改的舊路徑文字；舊 CLI 路徑選項與清空舊鍵會回報已改自動管理，且不寫檔。

## 授權

[MIT](LICENSE)
