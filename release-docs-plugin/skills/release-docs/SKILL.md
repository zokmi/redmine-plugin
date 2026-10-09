---
name: release-docs
description: 當使用者要先確認 Git 差異範圍，再以舊版資料庫來源與排除意圖產製上線 SQL、參數與操作文件時使用；不執行正式部署。
---

# Release Docs

## 輸入與證據

先從已提供內容取得 repository、base／target 或明確 commit 範圍、diff mode、工作區是否納入、舊版 DB 結構來源、release 識別與排除意圖。缺少影響範圍的必要資訊時標示待確認；不得自行將 HEAD 或工作區異動視為本次 scope。

執行唯讀工具（SCRIPT_DIR 為此技能的 scripts 目錄）：

```text
python SCRIPT_DIR/collect_release_evidence.py --repo PATH --base REV --target REV --diff-mode direct
```

選擇 `merge-base` 時以共同祖先作為實際比較起點。讀取 JSON 的完整 SHA、Git 根目錄與 A/M/D/R 等路徑證據；將 staged、unstaged、untracked 與 committed changes 分開。工具不輸出內容，後續 source 分析必須再取得對應 revision 的來源，不可從檔名或 commit 標題猜測 SQL。

## 產製與審查邊界

此骨架目前只實作 Git 證據收集。缺少來源分析、SQL execution artifact、lifecycle 產製或審核能力時須明確標示待確認，不得宣稱已完成上線套件。

完整工作流需依 Database Project／dacpac／migration／SQL 與 codebase 的實際消費，建立可追溯的 SCHEMA、REPAIR、DATA、VALIDATION execution units。不得從 ORM Up/Down 自行猜出 SQL。排除意圖須分析完整 unit 與 dependency impact，無法安全切出單位時阻擋產出。

上板檔案固定為 `00_上線指引.md`、唯一人工 SQL `01_部署SQL.sql`，以及有參數異動時的 `02_參數異動.md`。source、工具輸出、完整 mapping 與排除真相 `lifecycle_exclusion_manifest.json` 保存在 `.release-docs/runs/<run-id>/`，不得交給上板人員操作、不得放入 release 目錄或版控，也不得寫入 `docs/release-artifacts`。`00` 的排除摘要由 lifecycle 真相投影，不反向改寫排除範圍。

單一 release-level transaction 必須完整執行與驗證後，在 ValidateOnly=1 rollback，在 ValidateOnly=0 從 fresh session 重新執行後 commit。任何錯誤 rollback、THROW 並停止；不可交易 DDL 需受控分類，不能宣稱外層 transaction 可回復。不得直接連線或修改正式資料庫。

分開回報 SQL 內容審核與部署驗證狀態。證據不足用「待確認」，不得以文件存在或 Git 收集成功代替 SQL／部署驗證。
