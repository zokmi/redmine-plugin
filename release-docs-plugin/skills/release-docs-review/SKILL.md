---
name: release-docs-review
description: Use when release-docs 已產出必要版更文件，需要必要語意審查、修正複審或確認舊審查報告是否仍有效。
---

# Lifecycle 版更文件審查

重新讀取已確認的 repo、source_scope、operator output 與 `.release-docs/runs/<run-id>/`。此技能只審核，不連線或修改正式資料庫，不修改原始 Database Project，也不直接修補 execution SQL。發現 SQL 問題時回到可追溯來源／repair unit 重建。

## 唯一人工輸出契約

`00_上線指引.md`、`01_部署SQL.sql` 必須存在；只有確定參數異動適用時才要求 `02_參數異動.md`。拒絕 `02_資料SQL.sql`、`03_例外排除.json`、`04_參數異動.md`、未宣告檔案與目錄，以及輸出中的 lifecycle evidence。不要將舊檔名視為缺件。

`lifecycle_exclusion_manifest.json` 必須從 run 讀取，是唯一排除真相；缺少或無法解析時不得假設沒有排除。完整 metadata、原始工具紀錄、fingerprint 與 `05_版更審查報告.md` 都留在 run。

## 執行順序

1. 呼叫 `scripts/validate_release_output.py --output-dir <operator> --run-root <run>`。工具回傳 `list[Finding]`，最後兩筆的 code 為 `sql_content_status` 與 `deployment_validation_status`，status 僅有 `通過`／`待確認`／`未通過`。CLI 有 blocking finding 時以 exit 1 結束；exit 0 仍可能待確認。
2. 比對 lifecycle → `01` → `00` 排除摘要。核對完整 included/excluded unit、來源 revision/hash、相依順序、SCHEMA／REPAIR／DATA／VALIDATION 區段，以及 excluded object 沒有 DROP、DELETE 或欄位補建。每個 excluded ID 必須能解析到 source unit；manifest 的完整 units、source evidence、object/issue 範圍與 dependency impact 必須與 source metadata 一致，缺 provenance 或未知 ID 即阻擋。`00` 只能是 manifest 的操作投影，不得新增排除範圍。
3. 審閱 authoritative source 與 execution artifact，逐一核對完整 table/column/index/FK/constraint/extended property 定義、constraint trust、資料前置條件與保留資料。確認已存在且正確時跳過、缺少時補建、不相容時停止；不能只比名稱、history 或 table 是否存在。工具對未受 guard 保護的 mutation 與重複欄位會阻擋，但靜態 guard 不證明安全重跑。語意證據不足時將 SQL 狀態降為待確認；確認資料／SQL 缺陷時未通過。
4. 核對單一 release transaction、XACT_ABORT、TRY/CATCH、ValidateOnly=1 完整 rollback、ValidateOnly=0 fresh session commit、ROLLBACK＋THROW 停止後續 unit。核對結構化 ReleaseId、database/server、phase/unit、source location、ERROR_*、XACT_STATE、@@TRANCOUNT 與 transaction action。GO、獨立 COMMIT、不可交易 DDL、吞錯或僅 RAISERROR 都阻擋。
5. LocalDB 以四輪同 artifact evidence 獨立判定部署狀態；缺證據維持待確認，失敗是未通過，只有明確證明 SQL 缺陷的 evidence 才連動 SQL 狀態。不得把靜態通過說成部署通過，也不得以隔離結果取代正式 provider、版本、權限、備份與維護窗口確認。
6. 呼叫 `review_fingerprint(repo, output_dir, run_root, source_scope)`，重新計算 `00`、`01`、適用時 `02`、manifest、source/unit metadata、lifecycle metadata、source revision/tree IDs、declared database source worktree hash 與 execution artifact hash。fingerprint 只回傳摘要，不讀取 repository secret/configuration 原值。不得用 HEAD 取代指定 source revision。任何覆蓋異動都使舊報告失效。
7. 合併工具 finding 與來源語意審閱結果，呼叫 `write_review_report(run_root, findings, fingerprint)` 寫入 lifecycle 的 `05_版更審查報告.md`。語意審查可用 `semantic_review_pending` 或 `semantic_sql_defect` finding 降低 SQL 狀態；writer 會以最嚴重既有 summary 與完整合併 finding 重算狀態，不保留過時的通過，也不將 SQL 缺陷變成部署證據失敗。對外只提供狀態、缺失與需修正來源；不貼 SQL literal、連線字串、密碼、token 或原始工具錯誤值。

## Lifecycle evidence 介面

Task 2 的 manifest、`source_unit_metadata.json` 與 `lifecycle_metadata.json` 都是 schema_version=1。Task 3 artifact 使用原始 SQL bytes 與 unit mapping；Task 4 只複製該 bytes 並投影 manifest。

產製協調器在 lifecycle metadata 宣告：

```json
{
  "operator_contract": {"parameters_applicable": false},
  "execution_artifact": {"path": "assembly/01_部署SQL.sql", "sha256": "<sha256>"},
  "localdb_validation": {
    "status": "not_run",
    "artifact_sha256": "<sha256>",
    "rounds": {}
  }
}
```

參數適用性也可取自 source metadata 的 `parameter_changes` list；沒有宣告而出現 `02` 時待確認。Execution artifact path 可以是 run-relative 或已確認的絕對路徑；禁止 traversal、symlink、junction、reparse、hardlink 與替代資料流。

LocalDB 的 rounds 必須有 `validate_only`、`commit`、`rerun`、`injected_failure`。每輪需 status=passed、exit_code=0、server/database、provider_version/tool_version、baseline_source/fixture_source、command、非空 checks 與字串 error_output_summary；注入錯誤輪必須記錄預期錯誤、rollback 與停止摘要。頂層 artifact_sha256 必須匹配重新讀取的 SQL。這些欄位是證據格式檢查，仍需審閱 checks 是否實際證明 fresh baseline、新 session、完整定義、排除、rollback／commit 與收斂。失敗 runner 可提供 `sql_defect: true` 表示經審查明確證明 SQL 缺陷。

## 複審與 lifecycle 清理

先計算新 fingerprint，再比對舊報告；不同即失效，缺必要證據即待確認。報告不納入自身 fingerprint，避免循環依賴。待成功產製、來源語意審核、隔離驗證與交付完成後，才交由 lifecycle manager 清除暫存；失敗／中斷保留七天。此審核器不刪除 run、不執行 SQL，也不以手改 manifest 解決 finding。
