# Redmine issue skills 相容資料

唯一維護來源是 `skills/redmine-issue-writing/`。本目錄的 `SKILL.md` 與 `references/` 是同步副本，保留給歷史指標與內容測試使用；不要獨立修改。修改來源後同步相同相對路徑，並執行 `tests/test_canonical_sync.py` 驗證。

對外安裝一律使用根目錄的 `redmine` plugin；請依根目錄 [README](../README.md) 安裝。plugin 載入 `skills/` 下的四個技能，由 plugin 管理器更新與移除。工作區同步不會更新使用者家目錄或已安裝的 plugin。

## 測試範圍

執行 python -m pytest tests 驗證相容文件、骨架與 plugin 發佈契約。舊 install.ps1／install.sh 已隨 plugin 化移除；舊安裝器的複製、全域指標寫入與移除行為不再是產品契約，由 tests/test_plugin_distribution.py 檢查舊入口不存在、plugin 載入四個維護中技能與有效 MCP 設定，以及文件導向現行安裝流程。完整安裝與設定測試另見 ../redmine-mcp/tests/test_plugin_installation.py。
