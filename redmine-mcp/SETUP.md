# Redmine MCP 設定

Redmine MCP 由本套件的 plugin 啟動。請先依根目錄 [README](../README.md) 安裝 plugin；本文件只說明站台參數，不提供獨立安裝、ZIP 安裝或手動 MCP 註冊流程。

## 建立參數檔

在 plugin 根目錄執行：

```powershell
uvx --python 3.13 --from ./redmine-mcp redmine-mcp setup
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
uvx --python 3.13 --from ./redmine-mcp redmine-mcp config show
uvx --python 3.13 --from ./redmine-mcp redmine-mcp config set-site main --url https://redmine.example.com/redmine --key
uvx --python 3.13 --from ./redmine-mcp redmine-mcp config remove-site client-b
```

`config set-site` 會在寫入前驗證網址與金鑰；`config show` 只顯示遮罩後的金鑰。多站台時工具的 `site` 參數會要求明確指定站台，避免寫入錯誤的 Redmine。

## Plugin 啟動疑難排解

確認 `uvx` 在 Claude Code 或 Codex 的 PATH，並從 plugin 管理介面重新啟用 Redmine。再重開工具，在 `/mcp` 或 MCP 清單確認 server 已連線。若 server 啟動失敗，可直接執行：

```powershell
uvx --python 3.13 --from ./redmine-mcp redmine-mcp
```

這個命令只作為診斷，不是另一種安裝方式。若顯示設定錯誤，確認參數檔存在、TOML 語法正確，且每個站台都有 `url` 與 `api_key`。

## 更新與移除

更新與移除一律透過 Claude Code／Codex plugin 管理器；不要把 MCP server 另行安裝成全域 Python 工具。plugin 更新不會刪除使用者設定目錄的 `config.toml`。
