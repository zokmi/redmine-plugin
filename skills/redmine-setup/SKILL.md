---
name: redmine-setup
description: 當使用者首次設定 Redmine plugin、設定站台與 API 金鑰，或安裝後 MCP 因缺少設定而無法啟動時使用。連線中斷先用 redmine-doctor 診斷。
---

# Redmine plugin 站台初始化

1. 從實際載入的這份 SKILL.md 所在目錄解析 `../../redmine-mcp-node` 的絕對路徑。確認 `package.json` 的套件名稱為 `redmine-mcp-node`，並確認 Node.js 至少為 22 版且 npm 可執行。
2. 在該 Node 套件目錄執行 `npm ci --no-audit --no-fund` 與 `npm run build`，確認成功後，以該目錄下 `dist/cli.js` 的絕對路徑產生 `node '<絕對路徑>/dist/cli.js' setup`。套件快取唯讀時，先將該版的 `package.json`、`package-lock.json`、`tsconfig.json` 與 `src` 複製到使用者可寫的新目錄並在那裡建置。引用路徑時保留空白及特殊字元。
3. 請使用者在自己的互動終端機執行。精靈需要網址、站台代號與 API 金鑰；不要要求把金鑰貼到對話，也不要用命令列參數傳金鑰。不在模型工具的輸出裡擷取金鑰。
4. 參數檔保存在 Windows `%APPDATA%/redmine-mcp/config.toml`，其他系統 `~/.config/redmine-mcp/config.toml`（遵循 XDG_CONFIG_HOME），也可用 `REDMINE_CONFIG` 覆寫。設定精靈與共用服務必須使用同一設定路徑。只查看精靈結果或遮罩後的 config show。
5. 依 [README 的共用服務指引](../../README.md#啟動本機共用服務) 啟動服務。Windows 執行內附的 `scripts/install-local-service.ps1`，以無視窗方式啟動 Node.js 共用服務。讓 Codex 和 Claude 繼承同一權杖，再重新連線。

遇到連接埠占用時，先確認監聽程序的身分，再依 README 切換。站台設定修改會在共用服務下次啟動時載入。
6. 呼叫 `get_current_user` 驗證（多站台帶明確 site）。尚未取得驗證結果就標示「待驗證」。

Node.js 缺少或版本過舊時，請使用者安裝含 npm 的 Node.js 22 或更新版本。
無法定位已安裝 skill 時，請使用者重新安裝或啟用 plugin；不以另一份 GitHub server 取代內附套件，也不註冊獨立 MCP。
維護站台時同樣沿用套件絕對路徑，將 setup 改為 config show／config set-site／config remove-site；只執行使用者要求的操作。

Windows 安裝與升級時直接執行 `scripts/install-local-service.ps1`。要從舊 Python worker 遷移也使用此命令。`-Restart` 會沿用已安裝的引擎。

當本機 MCP 出現 ECONNREFUSED、重連失敗或啟動逾時時，自動使用 redmine-doctor skill；成功後重連並以 get_current_user 驗證，再繼續任務。
