# Redmine MCP Node.js 遷移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** 在兩個已配置站台提供與 v0.17.4 相容的 Node.js Redmine MCP 服務。

**Architecture:** TypeScript worker 管理 MCP、站台路由與附件儲存，由目前的 Node.js 監督程序管理背景執行。每站的 `node:https` Agent 承載 Redmine 請求，明確允許的明文舊站點走 `node:http`。

**Tech Stack:** Node.js 22+、TypeScript ESM、官方 MCP TypeScript SDK、`node:https`、`node:test`、npm lockfile、PowerShell 安裝程式。

**Spec:** [設計規格](../specs/2026-11-16-redmine-node-https-design.md)

## Global Constraints

- 基準為 `747bd4a`，發布驗收保留 v0.17.4 的工具契約、stdio 與 Streamable HTTP 邊界、設定相容性、既有附件資料及回復路徑。
- Node.js 22+，Redmine HTTPS 使用 `node:https`。API key、Bearer token 與 upload token 不得出現在命令參數、測試輸出或日誌。
- 保持 `127.0.0.1:18765/mcp`、`REDMINE_MCP_LOCAL_TOKEN`、worker 恢復間隔 2 秒與跨站併發上限 8。
- 測試中的 Redmine 寫入只限使用者授權的 main 測試專案。單元及故障測試使用本機 fixtures，附件清理測試使用複本。

## Review Focus

- HTTP 200 的截斷串流不可產生已發布檔。由任務 2、4 驗證。
- 拒絕跨來源附件 URL 及 redirect，外部伺服器不得收到 API key。由任務 2 驗證。
- 已經送出的工單更新若遇逾時，後續診斷避免再送一次寫入。由任務 2、3 驗證。
- 舊附件的資料夾或檔案被 reparse point 替換時，清理不得越界。由任務 4 驗證。
- Worker 退出前已有 MCP 連線時，監督程序應保留對外端口並讓客戶端重新建立 session。由任務 5 驗證。

## 任務 1 — 建立工具契約基準與 Node 專案

**檔案** `redmine-mcp-node/package.json`、`package-lock.json`、`tsconfig.json`、`tests/contracts.test.mjs`、`tests/fixtures/v0.17.4-tools.json`，後四個路徑位於 Node 專案內。

- [ ] 從 `747bd4a` 的 Python stdio 取得 `tools/list` 與測試 fixture，固定工具名稱、schema、描述與註解。記錄 JSON schema 中僅 SDK 表達方式有差異的正規化規則。匯出限使用測試站台設定。
- [ ] 寫 `v0.17.4 tool contract is available` 測試，確認 fixture 的工具集合與 Python 四個註冊模組一致，先執行並檢查缺少 fixture 時的失敗。
- [ ] 加入 Node 套件與鎖檔。固定明確支援兩種 MCP 傳輸的 SDK 版本。定義 `npm run build` 編譯 `src` 至 `dist`，`npm test` 先 build 再執行 `node --test`。
- [ ] 從 `redmine-mcp-node` 執行 `npm ci` 與 `npm test`，確認契約 fixture 測試通過並提交。

## 任務 2 — 站台設定與 Redmine 傳輸

**檔案** `redmine-mcp-node/src/config.ts`、`transport.ts`、`tests/config.test.mjs`、`tests/transport.test.mjs`（後三個位於 Node 專案對應目錄）。

**介面** `loadSettings(env: NodeJS.ProcessEnv): Promise<Settings>`。 `createTransport(site: SiteSettings): RedmineTransport` 提供 `json(method, path, options)`、`upload(filename, bytes, signal?)`、`download(url, target, maxBytes, signal?)`、`close()`。JSON 回傳狀態碼和內容，下載回傳寫入位元組數，失敗回傳可辨識的例外分類。

- [ ] 寫 `legacy config priority`、`redirect does not receive credentials`、`challenge differs from API forbidden`、`oversize and truncated response terminate`、`write socket timeout sends one request`。加入解壓後 16 MiB 限制、TLS 驗證、Content-Length 與 chunked 限制、連線重用、取消測試。執行 `npm test`，確認缺少對應實作時失敗。
- [ ] 移植 Python 的 TOML 路徑、設定驗證、單站覆寫和多站環境變數規則。沿用 User-Agent、10/60/300 秒逾時、無自動轉址與同來源限制。上傳傳送原始 bytes，下載以 backpressure 串流到呼叫方指定的 Writable。
- [ ] 執行 `npm test`，確認 fixtures 覆蓋 API 錯誤、無效 JSON、同來源限制、串流取消與連線釋放後提交。

## 任務 3 — MCP 工具及站台路由

**檔案** `redmine-mcp-node/src/server.ts`、`src/sites.ts`、`src/tools/{issues,projects,metadata}.ts`、`tests/tools.test.mjs`。

**介面** `createServer(settings: Settings, dependencies: ServerDependencies): McpServer`。 `createSiteRegistry(settings, factory)` 使用任務 2 的 transport factory 管理每站連線。 `ServerDependencies` 允許測試注入站台 registry，附件註冊在任務 4 接上。

- [ ] 寫 `multi-site write requires site`、`fanout preserves successful sites on timeout`、`issue detail preserves journal pagination`、`issue writes preserve validation and payload`，斷言第九站在前八站其中一站結束前尚未開始。執行 `npm test`，確認新測試先失敗。
- [ ] 逐一移植站台、中繼資料、工單與專案工具，對照 Python 模組、既有 fixtures 與契約。沿用摘要、journals 分頁、metadata 快取、不受信任文字標記及 issue 寫入欄位驗證。
- [ ] 透過 MCP client fixture 呼叫工具並執行 `npm test`。使用基準契約比對已移植的工具集合，提交。

## 任務 4 — 附件工具與既有資料相容

**檔案** `redmine-mcp-node/src/tools/attachments.ts`、`src/storage/{store,lifecycle}.ts`、`tests/attachments.test.mjs`、`tests/windows-storage.test.mjs`。

**介面** `createAttachmentStore(options: StorageOptions): AttachmentStore` 提供 `stageDownload(site, attachment)`、`publish(download)`、`discard(download)` 和 `sweep(now)`。 `registerAttachments(server, registry, store)` 在 server 組裝時註冊附件工具，依任務 2 的傳輸介面上傳與下載。

- [ ] 寫 `v1 Python cache opens and round-trips`、`interrupted stream leaves no published file`、`concurrent download publishes once`、`junction replacement cannot escape managed root`、`7-day archive and 30-day expiry`、`upload token attaches exactly once`，包含檔案來源、Base64、大小限制與上傳後圖片嵌入。執行 `npm test`，確認新測試先失敗。
- [ ] 以舊版產生的 fixtures 驗證儲存鍵、JSON schema、日期、檔案鎖、原子發布及 ZIP 歸檔完整性。若 Windows 防護需額外平台相依，採直接可用的鎖定版本 helper 或 addon。Windows 測試檢查私有目錄 ACL、舊版鎖相容性、junction 置換及交付品完整性，全部通過才切換。實作與現有 MCP 上傳及下載格式相容的工具。
- [ ] 執行 `npm test`，在 Windows 執行 reparse point 測試並檢查完整工具 schema 對照。將 Node 寫出的 schema v1 fixture 交由舊版讀取，雙向成功後提交。

## 任務 5 — CLI、服務切換與驗收

**檔案** `redmine-mcp-node/src/{cli,worker}.ts`、`tests/service.test.mjs`、`scripts/local-service.mjs`、`scripts/install-local-service.ps1`、根目錄 `README.md`、`skills/redmine-setup/SKILL.md`、`scripts/check_plugin_release.py`。

- [ ] 寫 `stdio works with existing configuration`、`both clients can initialize`、`unexpected worker exit returns 503 then reconnects`、`local and worker tokens isolated`、`upgrade and rollback preserve config and storage`。執行 `npm test`，確認新測試先失敗。
- [ ] 實作 `setup`、`serve` 與無參數 stdio，沿用 v0.17.4 的 setup 指令、客戶端註冊與既有 config，驗證保留金鑰及其他使用者設定。辨識既有安裝 manifest 與 PID 的程序身分，演練啟動失敗後恢復舊部署。監督程序以 Node.js 啟動 worker，保留 loopback、動態埠、就緒通知、父程序退出後收尾、401／503、兩秒重啟。發布前建出可直接啟動的 JavaScript，更新 Windows 安裝腳本及 README 的平台安裝入口。
- [ ] 執行 `npm ci`、`npm test`、現有 release 檢查與 Windows 安裝／重啟測試。驗證來源、發布檔案及 lockfile 一致，且安裝測試環境可在無 Python／uv 的情況下啟動 MCP。
- [ ] 以 Codex 和 Claude Code 各完成 initialize、工具列舉、讀工單及 worker 復原後重連。兩站確認身分、讀工單、附件串流；獲授權的 main 測試專案驗證上傳、掛載與 SHA-256。保存去敏後的 HTTP 狀態、附件大小、相符雜湊與 MCP 測試結果。
- [ ] 用設定與附件資料複本演練切換及回復，確認 v0.17.4 可以讀取既有 schema v1。完整驗收後提交切換異動，保留 v0.17.4 作回復版本。
