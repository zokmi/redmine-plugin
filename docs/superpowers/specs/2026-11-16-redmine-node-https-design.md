# Redmine MCP Server 遷移至 Node.js 設計規格

## 目標與範圍

使用 Node.js 22 以上與 TypeScript 執行 Redmine MCP Server、設定 CLI 和 Windows 背景服務。對 Redmine 的 HTTPS 請求使用 `node:https`。完成切換後，MCP 服務的安裝和執行可移除 uv、Python、Uvicorn 與 PycURL。需求查核及版更整理的獨立 skill 腳本有各自的 Python 執行需求，移植範圍限於 MCP 服務及其安裝設定。

工作分支自本機 `origin/main` 的 `747bd4a` 起建，對應 v0.17.4 已提交的服務版本。

## 方案與實測

選定 TypeScript MCP SDK、`node:https` 與現有 Node.js 監督程序。保留監督程序與 worker 的程序隔離，保留端點在 worker 退出期間回 503 的行為。現有 Python／PycURL 部署提供切換前的基準及正式回復版本。

內建 `fetch` 在第一站使用同一 API key 與客戶端 User-Agent 的交錯測試為 0/3，回 `403` HTML 與 `cf-mitigated: challenge`。相同 HTTP API 用 `node:https` 為 3/3，HTTP/1.1 與 TLS 1.3。僅憑對照無法識別 Cloudflare 觸發規則或 TLS 指紋。

`node:https` 已在兩站完成身分、工單清單與明細、新建及重用連線，以及 PNG 串流下載。兩站分別為 153,013 與 127,105 bytes，重複下載 SHA-256 一致。1 KiB 測試分別涵蓋 Content-Length 提前中止及接收中超限中止。使用者授權的 `main` → `mcp-test` → #94184 完成上傳 201、掛載 204、下載 200、SHA-256 一致，附件 ID 260967。上述為獨立探測，整合後仍需驗證。

## 對外行為

- Codex 與 Claude Code 繼續連線到 `http://127.0.0.1:18765/mcp`，維持 `REDMINE_MCP_LOCAL_TOKEN`、Streamable HTTP 與無參數 stdio 相容入口。以 v0.17.4 固定工具名稱、參數必要性、預設值、工具註解及可觀察的輸出與錯誤語意。遷移工單、專案、中繼資料、站台與附件工具。
- 讀取既有 TOML、`REDMINE_CONFIG`、單站環境變數覆寫與多站設定。多站寫入明確指定站台，跨站查詢並行上限 8 並保留各站的部分失敗資訊。
- 保留現有 User-Agent 版本格式、各站 API key、TLS 驗證、同來源附件 URL 檢查及禁止自動跟隨轉址。只有已設定 `allow_insecure_http = true` 的舊站使用 `node:http`。Redmine 403 HTML 挑戰與 401/403 API 權限錯誤可區分。
- Windows 監督程序維持本機端點，以獨立 token 轉送到動態埠上的 worker。Worker 崩潰時端點回 503，兩秒後重啟。支援同時連線及重連。輸入的 Base64 附件大小上限、綁定 loopback 與 Bearer 驗證均沿用現行規則。

## 傳輸與附件資料

依站台建立連線池。連線逾時 10 秒、一般請求總逾時 60 秒、下載總逾時 300 秒。一般 API 解壓後最多 16 MiB。附件串流同時檢查 Content-Length 與實際接收大小，傳輸取消或失敗時關閉資源並清除未發布檔。上傳以 binary body 呼叫 `/uploads.json`，取得 token 後由工單請求掛載。對逾時、挑戰與連線中斷的寫入保留可歸因的錯誤，避免自動重送造成重複寫入。

沿用本機受管目錄、站台儲存鍵及 schema v1 中繼資料格式。以 Python 已建立的檔案做雙向讀取驗證。保留檔案鎖、同目錄原子發布、路徑及符號連結／Windows reparse point 防護、歸檔校驗與清理順序。閒置滿 7 天可歸檔，封存滿 30 天才到期清理。驗收前以複本或沙箱資料測清理和故障恢復，切換及回復時先停止舊服務，避免兩版同時清理相同儲存區。

### 平台檔案操作

允許隨服務交付鎖定版本的原生 helper 或 addon，以保留 Windows handle-relative 操作、ACL、reparse point 防護與相容鎖。安裝端須能在目標平台驗證交付品完整性並直接使用。若目標平台無法保留這些語意，該平台維持既有發布版。

## 發布門檻

先以本機 HTTP/TLS fixtures、v0.17.4 工具契約及 Windows 安裝／重啟測試取得證據，再用兩站做唯讀 smoke test，並在獲准的 main 測試專案驗證附件寫入與下載。Codex 和 Claude Code 都需完成 MCP 初始化、工具列舉、呼叫及 worker 重啟後重連。整合驗收包含現有 config、附件資料和發布升級回復。發布版若需回復，先停止新服務，再啟動 v0.17.4；沿用現有 config 與受管附件資料。設定 CLI 以 v0.17.4 的命令及 setup 行為為契約，涵蓋客戶端註冊、既有金鑰及其他使用者設定的保留。Windows 升級先檢查既有部署 manifest 與 PID 的程序身分，停止舊服務後啟動新服務，健康檢查失敗時停止新服務並恢復舊部署及啟動入口。非 Windows 安裝方式維持 README 宣告的平台支援。
