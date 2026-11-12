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

Windows PowerShell 首次執行下列命令，建立目前使用者專用的本機連線權杖。權杖與 Redmine API 金鑰分開，應只留在使用者環境中。

```powershell
$redmineLocalToken = [Environment]::GetEnvironmentVariable('REDMINE_MCP_LOCAL_TOKEN', 'User')
if (-not $redmineLocalToken) {
    $redmineTokenBytes = New-Object byte[] 32
    $redmineTokenGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $redmineTokenGenerator.GetBytes($redmineTokenBytes) }
    finally { $redmineTokenGenerator.Dispose() }
    $redmineLocalToken = ([BitConverter]::ToString($redmineTokenBytes)).Replace('-', '')
    [Environment]::SetEnvironmentVariable('REDMINE_MCP_LOCAL_TOKEN', $redmineLocalToken, 'User')
}
$env:REDMINE_MCP_LOCAL_TOKEN = $redmineLocalToken
uvx --python 3.13 --from '<已安裝 plugin 根目錄>/redmine-mcp' redmine-mcp serve
```

macOS／Linux 可執行 `export REDMINE_MCP_LOCAL_TOKEN="$(openssl rand -hex 32)"`，再以同一份權杖啟動服務及兩個客戶端。Windows 設定權杖後，從已載入新環境的程序完整重開 Codex 與 Claude。若仍讀到舊環境，重新登入 Windows。服務所在的終端機需保持開啟，以 Ctrl+C 停止。

啟動後在 Claude 的 `/mcp`、Codex 的 MCP 清單確認連線，再呼叫 `get_current_user` 驗證站台。第二次執行 `serve` 會回報連接埠已被占用。詳細欄位與排錯見 [SETUP.md](redmine-mcp/SETUP.md)。

## 更新與移除

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

本套件採用 [MIT License](LICENSE)。
