# Redmine Plugin

本套件僅提供 **plugin 安裝**，整合 Redmine MCP 、三個工作流程 skills 與站台初始化 skill。比照 `bdd-skills`，由 Claude Code／Codex 的 plugin 管理器統一載入、更新與移除。

## 內容

- `redmine-mcp/`：Redmine MCP 原始碼，共 16 個工具，支援多站台與附件。
- `skills/redmine-issue-writing/`：公司標準 Redmine 單子格式。
- `skills/issue-code-consistency-check/`：需求與程式異動一致性查核。
- `skills/release-change-items/`：版更項目與重大功能檢核彙整。

## 前置需求

安裝支援 plugin 的 Claude Code 或 Codex，以及提供 `uvx` 的 [uv 套件](https://docs.astral.sh/uv/getting-started/installation/)。在執行設定與共用服務的終端機確認 `uvx --version` 可執行。Python 3.13 與 MCP 相依套件由 `uvx` 按需準備，服務使用 plugin 內附原始碼。

## 安裝到 Claude Code

在 Claude Code 互動工作階段執行：

```text
/plugin marketplace add https://github.com/zokmi/redmine-plugin.git
/plugin install redmine@redmine-plugins
```

依提示選擇安裝範圍。安裝由 GitHub 取得，無需自行 clone 或提供本機 marketplace 路徑。
首次設定執行 `/redmine:redmine-setup`；工作流程指令為 `/redmine:redmine-issue-writing`、`/redmine:issue-code-consistency-check`、`/redmine:release-change-items`。

## 安裝到 Codex

```text
codex plugin marketplace add https://github.com/zokmi/redmine-plugin.git --ref main
codex plugin add redmine@redmine-plugins
```

安裝後重開 Codex，請它「使用 redmine-setup 設定 Redmine 站台」。Plugin 管理器負責下載與快取，不需要本機原始碼目錄。

## 設定 Redmine 站台

`redmine-setup` 會依已安裝 skill 的位置，找到同一份 plugin 內的 MCP 套件，產生可從任何工作目錄執行的設定命令。
在自己的終端機執行該命令，依提示輸入站台代號、網址與個人 API 金鑰；不需要把金鑰貼到對話。

設定只寫入站台參數，不安裝獨立工具、不複製 skills、不執行 `claude mcp add`。
金鑰保存在 plugin 目錄外：Windows `%APPDATA%\redmine-mcp\config.toml`；macOS／Linux `~/.config/redmine-mcp/config.toml`（有 `XDG_CONFIG_HOME` 時沿用）。更新或移除 plugin 不會刪除站台設定。

## 啟動本機共用服務

Codex 與 Claude 的 Redmine plugin 都連到 `http://127.0.0.1:18765/mcp`。服務啟動一次後，可同時開啟兩個工具的多個工作階段。升級時先將兩邊的 plugin 更新到支援共用服務的版本，並結束舊工作階段，讓原有 stdio 程序退出。

### Windows

首次安裝及每次更新 plugin 後，在 PowerShell 執行：

```powershell
& '<已安裝 plugin 根目錄>/scripts/install-local-service.ps1'
```

腳本需要 Node.js 22 以上和 `uv`，會把服務與依賴安裝到目前使用者的 `%LOCALAPPDATA%\redmine-mcp\service`，並無視窗啟動 Node.js。Node.js 持有共用端點並管理內部 Python 工具程序。關閉 Claude、Codex 或 PowerShell 後，Node.js 仍會在目前的 Windows 登入階段持續提供服務。登出、重開機或程序退出後，可重新執行腳本啟動。

腳本會印出 Node.js 的 PID 與日誌位置。更新時，先確認 PID 對應 Redmine MCP，結束該程序後執行新版腳本。若從前景 `serve` 切換，先結束前景程序。若舊版曾建立 `Redmine MCP (<SID>)` 工作，先在工作排程器停用並結束該工作，釋出 18765。

腳本沿用使用者的 `REDMINE_MCP_LOCAL_TOKEN`，首次安裝才在缺少權杖時建立。首次建立後請完整重開兩個客戶端，必要時重新登入 Windows，讓客戶端取得權杖。權杖與 Redmine API 金鑰分開。

### macOS／Linux

將 `REDMINE_MCP_LOCAL_TOKEN` 設定到服務及客戶端的環境，再以前景命令啟動。若需要登出終端機後持續運作，請交由系統的使用者服務管理器執行同一命令。

```sh
uvx --python 3.13 --from '<已安裝 plugin 根目錄>/redmine-mcp' redmine-mcp serve
```

## 更新與移除

遇到連接埠占用時，先確認監聽程序的身分。切換或更新共用服務會中斷連線，請選擇兩個客戶端都沒有進行 Redmine 操作的時段。

新版本發布後，透過 plugin 管理器更新 marketplace 與 plugin，重開工具載入新版本。發布前同步更新 `plugin.json`、`.claude-plugin/plugin.json`、`.codex-plugin/plugin.json` 與 `redmine-mcp/pyproject.toml` 的版本，避免舊版本快取。

Claude Code 可在 `/plugin` 的 Installed 管理更新與移除；Codex 使用 plugin 管理介面，或 `codex plugin remove redmine@redmine-plugins`。移除後若不再使用 Redmine，可自行刪除上述參數檔。

## GitHub Release

發布前同步更新 `plugin.json`、`.claude-plugin/plugin.json`、`.codex-plugin/plugin.json` 與 `redmine-mcp/pyproject.toml` 的版本，並更新 `redmine-mcp/uv.lock`。將變更提交到 `main` 後，建立對應 tag：

```sh
git tag -a v0.14.2 -m "Redmine plugin v0.14.2"
git push origin v0.14.2
```

`vMAJOR.MINOR.PATCH` tag 會觸發 Release workflow：確認 tag 位於 `main`、四處版本一致、plugin 資源存在，通過相關測試後建立 GitHub Release 並自動產生發行說明。安裝仍透過 plugin marketplace；Release 不提供舊版獨立安裝包。範例版本請換成實際版本。

若 tag 已推送但 Release 尚未建立，可在 Actions 手動執行 Release workflow，輸入既有 tag。

## 舊版遷移

ZIP 安裝腳本、獨立 CLI 安裝命令與手動複製 skills 的方式已移除。先移除舊版手動註冊的 Redmine MCP，以及舊版放在使用者 skills 目錄的同名 skills，再以 plugin 安裝，避免載入兩份。現有 `config.toml` 可沿用；本套件不會自動改動工具的全域設定或刪除既有安裝。

## 維護與驗證

`redmine-mcp/` 的 Python 套件保留作為 plugin 的執行元件與開發測試用途，不提供獨立安裝流程。`redmine-issue-skills/` 保留格式參考與既有測試資料，plugin 技能來源為 `skills/`。

Plugin 格式參考：[OpenAI plugin 文件](https://developers.openai.com/plugins/build/plugins)、[Claude Code plugin 文件](https://code.claude.com/docs/en/plugins-reference)。


## 附件與自動暫存

`upload_attachment.file_path` 預設只接受套件管理的下載附件。若要上傳其他本機檔案，請在目標站台的 `[sites.<代號>]` 設定 `attachment_upload_root` 為專用上傳目錄的絕對路徑，並將要上傳的檔案放進該目錄。圖片也可直接傳入 `content_base64`。

站台預設要求 HTTPS。仍使用 HTTP 的舊站須在自己的站台設定加入 `allow_insecure_http = true`，HTTP 會以明文傳送 API key 與工單資料。修改設定後請重新啟動服務。

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

本套件採用 [Apache License 2.0](LICENSE)。

Copyright (c) 2026 Kenny Zheng
