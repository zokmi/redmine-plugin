# Redmine Plugin

本套件僅提供 **plugin 安裝**，整合 Redmine MCP 、三個工作流程 skills 與站台初始化 skill。比照 `bdd-skills`，由 Claude Code／Codex 的 plugin 管理器統一載入、更新與移除。

## 內容

- `redmine-mcp-node/` 提供正式的 Node.js MCP 服務、16 個工具、多站台查詢與附件功能。
- `skills/redmine-issue-writing/`：公司標準 Redmine 單子格式。
- `skills/issue-code-consistency-check/`：需求與程式異動一致性查核。
- `skills/release-change-items/`：版更項目與重大功能檢核彙整。

## 前置需求

安裝支援 plugin 的 Claude Code 或 Codex、Node.js 22 以上及 npm。在執行設定與共用服務的終端機，用 `node --version` 與 `npm --version` 確認版本。

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

v0.19.0 起，直接執行上方腳本即可安裝或升級到 Node.js。腳本依 npm 鎖檔建置新部署，沿用目前的站台設定、服務權杖與附件目錄。舊部署會保留供升級失敗時回復。使用舊 Python 部署者請在遷移成功前保留該部署的執行環境。

服務安裝於目前使用者的 `%LOCALAPPDATA%\redmine-mcp\service`。Node.js worker 退出後，共用端點會在兩秒後重啟 worker。復原期間端點回傳 HTTP 503。既有自動化可繼續使用 `-UseNode`。`-Restart` 會重啟已安裝的引擎；從 Python 升級時請執行不加參數的安裝命令。

當 agent 發現 MCP 的 `ECONNREFUSED`、重連失敗或啟動逾時時，使用 `redmine-doctor` skill 執行診斷。Windows 可以從已安裝 plugin 直接執行：

```powershell
& '<已安裝 plugin 根目錄>/scripts/doctor.ps1'
```

Node CLI 也提供 `node '<套件絕對路徑>/dist/cli.js' doctor`。Doctor 會檢查部署紀錄、程序身分、監聽連接埠及 MCP 授權初始化。服務已健康時回報結果；確認已安裝服務停止且連接埠空閒時，自動啟動同一份服務並驗證。已登錄程序仍在，但未監聽或持續回應 502／503 時，doctor 會核對啟動身分及設定後重啟同一部署。遇到外部程序占用、PID 重用、權杖或設定不相容時，回報具體原因並結束。Agent 在成功後重新連線，以 `get_current_user` 驗證再接續任務。

v0.18.5 起，安裝或成功重啟會以 Windows 目前使用者的 DPAPI 加密保存啟動環境。Doctor 在不同 PowerShell、Git Bash 或 agent 工作階段執行時，會暫時還原該環境，核對原有環境指紋及設定後恢復服務；結束時還原呼叫端的環境變數。使用者權杖、設定內容、程序身分與埠占用檢查仍然保留；快照無法解密或設定改變時不會強行重啟。

0.18.2 以前的狀態檔只有雜湊，無法還原原環境。若服務已停止，且 doctor 回報 `ENVIRONMENT_CHANGED`，可明確採用目前終端機的環境：

```powershell
& '<已安裝 plugin 根目錄>/scripts/install-local-service.ps1' -Restart -AdoptEnvironment
```

此操作會驗證目前設定，僅允許已登錄的服務停止且埠空閒時執行，不會停止仍在執行的程序。先核對目前站台、設定來源及環境，再執行；之後的 doctor 可直接使用加密快照。快照僅可由同一 Windows 使用者解密。一般設定內容修改仍使用 `-Restart`。

安裝及手動 `-Restart` 會保存固定的 doctor 入口與設定驗證腳本。已安裝舊版的使用者可在原有設定環境執行一次 `-Restart` 採用最新診斷功能。設定檔修改後同樣使用 `-Restart`，它會先驗證設定，並沿用原引擎。診斷引數與狀態檔不含明文權杖或 API 金鑰。

舊版狀態檔沒有可驗證的環境指紋，不能直接用 `-UseNode` 升級；請先在與既有服務相同的設定環境中執行 `-Restart`，確認服務正常後再切換。舊版未記錄啟動時環境，因此首次採用無法證明環境完全相同。早期 Node 候選版使用混合指紋；若當時的設定已修改，請先恢復服務啟動時的設定並重啟一次，以採用新指紋格式。

腳本沿用使用者的 `REDMINE_MCP_LOCAL_TOKEN`，首次安裝才在缺少權杖時建立。首次建立後請完整重開兩個客戶端，必要時重新登入 Windows，讓客戶端取得權杖。權杖與 Redmine API 金鑰分開。

### macOS／Linux

首次安裝與每次更新 plugin 後，在已安裝的套件目錄建置 Node.js 服務。已有站台設定可直接沿用。

```sh
runtime='<已安裝 plugin 根目錄>/redmine-mcp-node'
npm --prefix "$runtime" ci --no-audit --no-fund
npm --prefix "$runtime" run build
# 首次設定站台時執行
node "$runtime/dist/cli.js" setup
```

將同一個 `REDMINE_MCP_LOCAL_TOKEN` 設定到服務、Codex 和 Claude 的環境。升級時沿用現有權杖。首次啟用時可用 `node -p "require('node:crypto').randomBytes(32).toString('base64url')"` 在自己的終端機產生權杖。停止舊服務後，在上述終端機執行 `node "$runtime/dist/cli.js" serve`。若使用系統的使用者服務管理器，將它的啟動命令改為 Node 與 `dist/cli.js serve` 的絕對路徑。

## 更新與移除

遇到連接埠占用時，先確認監聽程序的身分。切換或更新共用服務會中斷連線，請選擇兩個客戶端都沒有進行 Redmine 操作的時段。

新版本發布後，透過 plugin 管理器更新 marketplace 與 plugin，再更新本機共用服務並重開客戶端。

Claude Code 可在 `/plugin` 的 Installed 管理更新與移除；Codex 使用 plugin 管理介面，或 `codex plugin remove redmine@redmine-plugins`。若不再使用 Windows 共用服務，可停止服務程序。若不再使用 Redmine，可自行刪除上述參數檔。

## GitHub Release

發布前同步更新三個 plugin manifest、`redmine-mcp-node/package.json` 與 `redmine-mcp-node/package-lock.json` 的版本，以 `node scripts/check-plugin-release.mjs vMAJOR.MINOR.PATCH` 驗證，再將已驗證的提交與對應 tag 推送到 `main`。

```sh
git tag -a v0.19.0 -m "Redmine plugin v0.19.0"
git push origin v0.19.0
```

`vMAJOR.MINOR.PATCH` tag 會觸發 Release workflow。工作流程驗證 tag 位於 `main`、版本與資源一致，通過 Node 測試後建立 GitHub Release。安裝仍透過 plugin marketplace。範例版本請換成實際版本。

若 tag 已推送但 Release 尚未建立，可在 Actions 手動執行 Release workflow，輸入既有 tag。

## 舊版遷移

ZIP 安裝腳本、獨立 CLI 安裝命令與手動複製 skills 的方式已移除。先移除舊版手動註冊的 Redmine MCP，以及舊版放在使用者 skills 目錄的同名 skills，再以 plugin 安裝，避免載入兩份。現有 `config.toml` 可沿用；本套件不會自動改動工具的全域設定或刪除既有安裝。

## 維護與驗證

`redmine-mcp/` 保留已棄用的 Python v0.18.6 原始碼，供舊部署診斷與比對。正式版本、設定命令及服務由 `redmine-mcp-node/` 提供。`redmine-issue-skills/` 保留格式參考與既有測試資料，plugin 技能來源為 `skills/`。

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
