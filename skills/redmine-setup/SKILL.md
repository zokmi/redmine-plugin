---
name: redmine-setup
description: 當使用者首次設定 Redmine plugin、設定站台與 API 金鑰，或安裝後 MCP 因缺少設定而無法啟動時使用。
---

# Redmine plugin 站台初始化

1. 以實際載入的這份 SKILL.md 所在目錄為基準，解析 `../../redmine-mcp` 的絕對路徑；確認該處有 `pyproject.toml`，且套件名稱為 redmine-mcp。不要搜尋開發者 checkout 或假設目前工作目錄就是 plugin 根目錄。
2. 產生使用該套件絕對路徑的命令：`uvx --python 3.13 --from '<套件絕對路徑>' redmine-mcp setup`。依使用者 shell 安全引用路徑：PowerShell 使用單引號並將內含單引號加倍；bash／zsh 使用單引號，內含單引號改成 ` '\'' ` 的拼接形式（不加入兩側空白）。不可讓路徑裡的 `$` 或反引號被展開。
3. 請使用者在自己的互動終端機執行。精靈需要網址、站台代號與 API 金鑰；不要要求把金鑰貼到對話，也不要用命令列參數傳金鑰。不在模型工具的輸出裡擷取金鑰。
4. 參數檔保存在 Windows `%APPDATA%/redmine-mcp/config.toml`，其他系統 `~/.config/redmine-mcp/config.toml`（遵循 XDG_CONFIG_HOME）；可用 REDMINE_CONFIG 覆寫。覆寫時 setup 終端機與 plugin MCP 程序必須使用同一設定路徑，不能假設重開工具會繼承終端機環境。只查看精靈結果或遮罩後的 config show，不讀出完整金鑰。
5. 設定成功後重開工具／重新連線 MCP，呼叫 `get_current_user` 驗證（多站台帶明確 site）。尚未取得驗證結果就說「待驗證」，不要宣稱連線成功。

`uvx` 不存在時提供官方 uv 安裝文件：https://docs.astral.sh/uv/getting-started/installation/。
無法定位已安裝 skill 時，請使用者重新安裝或啟用 plugin；不以另一份 GitHub server 取代內附套件，也不註冊獨立 MCP。
維護站台時同樣沿用套件絕對路徑，將 setup 改為 config show／config set-site／config remove-site；只執行使用者要求的操作。
