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

## 授權

[MIT](LICENSE)
