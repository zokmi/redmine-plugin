# Redmine MCP 設定

Codex 與 Claude 的 plugin 都連到同一個本機 Redmine MCP 服務。請先依根目錄 [README](../README.md) 安裝 plugin 並設定共用服務；本文件只說明站台參數與連線排錯。

## 建立參數檔

安裝後，在 Claude Code 執行 `/redmine:redmine-setup`，或請 Codex「使用 redmine-setup 設定 Redmine 站台」。
Skill 會產生使用已安裝 plugin 絕對路徑的命令，無需 clone 或切換到原始碼目錄。

以下命令中的 `<已安裝 plugin 根目錄>` 請換成 skill 提供的實際位置；API 金鑰只在自己的終端機輸入：

```powershell
uvx --python 3.13 --from "<已安裝 plugin 根目錄>/redmine-mcp" redmine-mcp setup
```

精靈會詢問站台代號、Redmine 根網址與 API 金鑰，驗證成功後寫入參數檔。精靈不會執行 `claude mcp add`、`codex mcp add`，也不會複製 skills；MCP 連線完全由 plugin manifest 管理。

參數檔位置如下：

| 平台 | 位置 |
| --- | --- |
| Windows | `%APPDATA%\redmine-mcp\config.toml` |
| macOS／Linux | `~/.config/redmine-mcp/config.toml` |

設定 `REDMINE_CONFIG` 可指定其他路徑。金鑰只透過 `X-Redmine-API-Key` header 傳送，寫入時會收緊檔案權限。

## 維護站台

```powershell
uvx --python 3.13 --from "<已安裝 plugin 根目錄>/redmine-mcp" redmine-mcp config show
uvx --python 3.13 --from "<已安裝 plugin 根目錄>/redmine-mcp" redmine-mcp config set-site main --url https://redmine.example.com/redmine --key
uvx --python 3.13 --from "<已安裝 plugin 根目錄>/redmine-mcp" redmine-mcp config remove-site client-b
```

`config set-site` 會在寫入前驗證網址與金鑰；`config show` 只顯示遮罩後的金鑰。多站台時工具的 `site` 參數會要求明確指定站台，避免寫入錯誤的 Redmine。


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

## Plugin 啟動疑難排解

確認執行服務的終端機能使用 `uvx`，並且服務與兩個客戶端都取得同一份 `REDMINE_MCP_LOCAL_TOKEN`。服務固定監聽本機 loopback，兩個 plugin 的預設連接埠為 `18765`。

```powershell
uvx --python 3.13 --from "<已安裝 plugin 根目錄>/redmine-mcp" redmine-mcp serve
```

若顯示設定錯誤，確認參數檔存在、TOML 語法正確，且每個站台都有 `url` 與 `api_key`。修改站台設定後重啟共用服務，使所有工作階段讀到新設定。若遇到 401，確認服務和客戶端都繼承了相同權杖。若連接埠已被占用，確認既有監聽者是否為共用服務。

## 更新與移除

更新與移除一律透過 Claude Code／Codex plugin 管理器；不要把 MCP server 另行安裝成全域 Python 工具。plugin 更新不會刪除使用者設定目錄的 `config.toml`。
