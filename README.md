# Redmine Plugin

本套件僅提供 **plugin 安裝**，整合 Redmine MCP 與三個 skills。比照 `bdd-skills`，由 Claude Code／Codex 的 plugin 管理器統一載入、更新與移除。

## 內容

- `redmine-mcp/`：Redmine MCP 原始碼，共 16 個工具，支援多站台與附件。
- `skills/redmine-issue-writing/`：公司標準 Redmine 單子格式。
- `skills/issue-code-consistency-check/`：需求與程式異動一致性查核。
- `skills/release-change-items/`：版更項目與重大功能檢核彙整。

## 前置需求

安裝支援 plugin 的 Claude Code 或 Codex，以及 [uv](https://docs.astral.sh/uv/getting-started/installation/)。`uvx` 必須在該工具的 PATH 中；Python 3.13 與 MCP 相依套件由 uv 管理。MCP 使用 plugin 內附原始碼，不從 GitHub 抓另一份 server。

## 安裝到 Claude Code（本機 marketplace）

在 Claude Code 互動工作階段執行：

```text
/plugin marketplace add "C:/Users/kenny/Desktop/redmine-mcp-獨立套件"
/plugin install redmine@redmine-plugins
```

依提示選擇安裝範圍。設定站台後重開 Claude Code，以 `/mcp` 確認 Redmine 已連線。技能指令為 `/redmine:redmine-issue-writing`、`/redmine:issue-code-consistency-check`、`/redmine:release-change-items`。

## 安裝到 Codex（本機 marketplace）

在終端機執行：

```powershell
codex plugin marketplace add "C:/Users/kenny/Desktop/redmine-mcp-獨立套件"
codex plugin add redmine@redmine-plugins
```

設定站台後重開 Codex，在 plugins／MCP 清單確認 Redmine 已啟用，再請它呼叫 `get_current_user`。三個 skills 由 plugin 提供。

上述路徑供本機測試使用。公開 marketplace 位於 [zokmi/redmine-plugin](https://github.com/zokmi/redmine-plugin)。

Claude Code 可直接使用：

```text
/plugin marketplace add zokmi/redmine-plugin
/plugin install redmine@redmine-plugins
```

Codex 可直接使用：

```text
codex plugin marketplace add zokmi/redmine-plugin
codex plugin add redmine@redmine-plugins
```

## 設定 Redmine 站台

在本套件根目錄開啟終端機：

```powershell
uvx --python 3.13 --from ./redmine-mcp redmine-mcp setup
```

依提示輸入站台代號、網址與個人 API 金鑰。這是**參數設定**，不安裝獨立工具、不複製 skills、不執行 `claude mcp add`。首次執行也會預先準備 server 相依套件，以降低首次 MCP 啟動的等待時間。

金鑰保存在 plugin 目錄外：Windows `%APPDATA%\redmine-mcp\config.toml`；macOS／Linux `~/.config/redmine-mcp/config.toml`（有 `XDG_CONFIG_HOME` 時沿用）。更新或移除 plugin 不會刪除站台設定。詳細欄位與排錯見 [SETUP.md](redmine-mcp/SETUP.md)。

## 更新與移除

本機修改後，透過 plugin 管理器更新 marketplace 與 plugin，重開工具載入新版本。發布前同步更新 `plugin.json`、`.claude-plugin/plugin.json`、`.codex-plugin/plugin.json` 與 `redmine-mcp/pyproject.toml` 的版本，避免舊版本快取。

Claude Code 可在 `/plugin` 的 Installed 管理更新與移除；Codex 使用 plugin 管理介面，或 `codex plugin remove redmine@redmine-plugins`。移除後若不再使用 Redmine，可自行刪除上述參數檔。

## GitHub Release

發布前同步更新 `plugin.json`、`.claude-plugin/plugin.json`、`.codex-plugin/plugin.json` 與 `redmine-mcp/pyproject.toml` 的版本，並更新 `redmine-mcp/uv.lock`。將變更提交到 `main` 後，建立對應 tag：

```sh
git tag -a v0.14.1 -m "Redmine plugin v0.14.1"
git push origin v0.14.1
```

`vMAJOR.MINOR.PATCH` tag 會觸發 Release workflow：確認 tag 位於 `main`、四處版本一致、plugin 資源存在，通過相關測試後建立 GitHub Release 並自動產生發行說明。安裝仍透過 plugin marketplace；Release 不提供舊版獨立安裝包。範例版本請換成實際版本。

若 tag 已推送但 Release 尚未建立，可在 Actions 手動執行 Release workflow，輸入既有 tag。

## 舊版遷移

ZIP 安裝腳本、獨立 CLI 安裝命令與手動複製 skills 的方式已移除。先移除舊版手動註冊的 Redmine MCP，以及舊版放在使用者 skills 目錄的同名 skills，再以 plugin 安裝，避免載入兩份。現有 `config.toml` 可沿用；本套件不會自動改動工具的全域設定或刪除既有安裝。

## 維護與驗證

`redmine-mcp/` 的 Python 套件保留作為 plugin 的執行元件與開發測試用途，不提供獨立安裝流程。`redmine-issue-skills/` 保留格式參考與既有測試資料，plugin 技能來源為 `skills/`。

Plugin 格式參考：[OpenAI plugin 文件](https://developers.openai.com/plugins/build/plugins)、[Claude Code plugin 文件](https://code.claude.com/docs/en/plugins-reference)。

## 授權

本套件採用 [MIT License](LICENSE)。
