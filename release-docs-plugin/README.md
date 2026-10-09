# Release Docs Plugin

獨立的 Codex／Claude 技能套件，以本機 Git 與資料庫來源證據支援版更文件工作流。本階段提供套件骨架與唯讀 Git 證據收集器；SQL 產製、lifecycle 管理與審核由後續工作流實作，不能將 metadata 收集成功視為部署驗證通過。

## 輸入與執行

需提供 Git repository 路徑、base／target commit revision、diff mode、舊版 DB 結構來源、release 識別及排除意圖。工作區是否納入需明確指定。

Python 3 標準函式庫與 Git CLI 即可執行，不需連接 Redmine 或資料庫。在此套件目錄執行：

```powershell
python ./skills/release-docs/scripts/collect_release_evidence.py --repo 'C:/work/中文 project' --base 'release/old' --target 'release/new' --diff-mode direct
```

`direct` 比較指定 base 與 target；`merge-base` 比較共同祖先與 target。指定舊 target 時不會改用 HEAD。從 repository 子目錄執行仍回傳 Git 根目錄。

JSON 欄位：`schema_version`（1）、`repo_root`、`base_sha`、`target_sha`、`diff_base_sha`（實際比較起點）、`diff_mode`、`committed_changes` 與 `working_tree_changes`。每個異動含 `status` 與 repository-relative `path`；rename／copy 加上 `old_path` 與百分比 `similarity`。工作區的 `staged`、`unstaged`、`untracked` 分列，基於目前 index／工作目錄，與指定 target 的 commit diff 分開；未追蹤檔使用 `?`。忽略檔不納入。工具不讀取或輸出 SQL、設定內容、密碼、token 或連線字串；檔案路徑為證據，檔名本身不應含敏感資訊。相同 revisions 與相同工作區狀態產生相同 JSON。

未知 revision、非 repository 與 Git 失敗回傳非零 exit code 和不含原始 Git stderr 的清楚錯誤。JSON 以 ASCII escape 無損保留中文路徑，避免主控台編碼影響。

## 上板輸出契約

上板人員接收：

```text
00_上線指引.md
01_部署SQL.sql
02_參數異動.md  # 僅有參數異動時產出
```

`01_部署SQL.sql` 是唯一人工執行 SQL，整合 SCHEMA、REPAIR、DATA、VALIDATION units。單一 release transaction 的 ValidateOnly=1 完整執行後 rollback；ValidateOnly=0 從 fresh session 重新執行並 commit。錯誤需 rollback、THROW 並停止。不可交易 DDL 必須明確分類與提供維護限制。

`lifecycle_exclusion_manifest.json` 只保存於 `.release-docs/runs/<run-id>/` 作內部 lifecycle 證據，是排除範圍真相；不得放到上板目錄、要求人工執行或修改。`00_上線指引.md` 只呈現其可操作排除摘要。source mapping、hash、工具輸出及審核輸入也屬 lifecycle 資料，暫存不可納入版控，不得寫入 `docs/release-artifacts`。

## 驗證

```powershell
pytest ./tests/test_collect_release_evidence.py -q
```

測試使用隔離本機 repository，不連線或修改正式資料庫。Git metadata 驗證與 SQL 內容審核、部署驗證是不同狀態。

### LocalDB 四輪驗證

LocalDB 驗證是選用的隔離步驟。以 `run_local_validation.py` 執行時必須指定
`(localdb)` server 與 disposable database；沒有注入 LocalDB executor 時會產生
`status=not_run` 及原因，不能當作部署通過。runner 不接受 production server，亦不
保存連線密碼。

提供 executor 後，runner 會以四個新 session／新 baseline 依序執行：

1. `validate_only`：完整 SQL、查核後 rollback。
2. `commit`：獨立 fresh baseline 與 session，完整 SQL、查核後 commit。
3. `rerun`：再次確認收斂與冪等性。
4. `injected_failure`：注入錯誤，確認 rollback、`THROW` 與後續 unit 未執行。

證據寫入 `.release-docs/runs/<run-id>/localdb_validation.json`，交付目錄仍只含
`00_上線指引.md`、`01_部署SQL.sql` 及必要的 `02_參數異動.md`。每輪都記錄 artifact
SHA-256、server/database、provider/tool version、fresh baseline、fixture、command、
exit code、checks 與 error output summary；四輪不完整時維持「待確認」。
