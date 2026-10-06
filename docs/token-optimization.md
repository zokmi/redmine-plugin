# Token 用量優化驗證

以 o200k_base 計數；文字載入量估算，不代表實際帳單。基準為本機 HEAD（8754d79）。

|內容|優化前|優化後|
|---|---:|---:|
|`redmine-mcp/src/redmine_mcp/server.py`|1138|208|
|`skills/redmine-issue-writing/SKILL.md`|1842|997|
|`skills/redmine-issue-writing/references/feature_request/feature.md`|6558|1563|
|`skills/redmine-issue-writing/references/bug_report/diagnose.md`|4911|1227|
|`skills/issue-code-consistency-check/SKILL.md`|3073|2940|
|`skills/release-change-items/SKILL.md`|2860|2863|

新需求基本載入（MCP base＋寫單 skill＋feature 骨架）：9538 → 2768，下降 71.0%。不含工具 schema、issue 資料、對話；需要詳細範例時另載 details，節省幅度會降低。

## 使用方式

- get_issue 預設完整模式保持相容；summary=true 只供初步瀏覽，長內文／留言提供 600 字預覽與截斷標記，編輯前重讀完整模式。
- journals_offset 從最新紀錄跳過 N 筆，使用回傳 journals_next_offset 往前讀。需完整查核時讀完相關討論，未讀範圍明列待確認。此分批只減少模型回傳量，Redmine 上游仍回傳完整歷程。
- collect_issue_changes.py --with-diff 預設整批 1200 行／48000 字元，各檔仍最多 400 行；重複 patch 只輸出一次，截斷或省略需按 commit／檔案補讀。可用 --max-total-diff-lines 與 --max-total-diff-chars 調整。
- 兩個 collector 預設緊湊 JSON；人工閱讀可加 --pretty。
- 版更預設摘要＋TSV＋必檢清單，要求完整表格時才追加，避免重複輸出全文。

## 驗證

- 針對摘要、留言分批、工具 schema、格式化與 diff 預算的 76 項測試通過。
- 完整回歸：835 passed、2 skipped、1 failed；失敗為既有 test_SETUP_的安裝步驟數與實際註冊數一致，仍以正規表示式找手動安裝步驟數。測試與 SETUP.md 均與 HEAD 一致，本次未修改。
- MCP src 與修改測試的 Ruff 通過；兩個 collector 的 F 規則通過；src Mypy 通過（29 個檔案）。
- 真實 repo 的 collector 緊湊／pretty JSON 解析結果一致；5 行／100 字元的整批 patch 上限驗證通過。
- 新功能 DB 不動、既有 bug 診斷、無附件工具三個提示詞情境已唯讀檢查，並修正按需 details 的重複確認與 DB 檢核適用條件。
