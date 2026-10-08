# BDD 全通過後的 Redmine 一致性審核與逐項證據設計

## 目標

BDD 全部通過後，更新 Redmine 前先由唯讀子代理依當下 issue、repo 與變更範圍動態選擇最合適的 skill，完成需求與程式異動一致性審核；審核通過後，按照「修正項目 N → 圖片 N」逐項追加 Redmine 註記。

## 範圍

- `bdd-skills` 負責收尾流程與審核閘門的呼叫契約。
- `redmine` plugin 負責一致性審核 skill 的選擇條件、結果契約與 Redmine 註記格式。
- 子代理只讀 issue、git 事實與證據，不修改程式、不更新 issue。

## 動態 skill 選擇

收尾流程提供 issue 系統、單號、repo、分支或 commit 範圍，以及可用 skill 的名稱與描述。選擇器依 skill 的觸發條件評分；Redmine issue 且需要比對需求與程式時，優先選 `issue-code-consistency-check`。若沒有適用 skill，結果為 `UNAVAILABLE`，不得更新 issue。

## 審核結果

子代理回傳 `PASS`、`FAIL`、`UNVERIFIED` 或 `UNAVAILABLE`，並附逐項需求、來源、判定與 `檔案:行號 (commit)` 證據。只有 `PASS` 可以進入 Redmine 更新；其他結果都保留 BDD 證據並阻擋寫入。

## Redmine 註記格式

每個修正項目必須緊接其對應證據圖片：

```markdown
## 修正與驗證結果

### 修正項目 1：<名稱>

- 問題／需求：
- 根因：
- 修正內容：
- 一致性審核：與 issue 要求一致
- 驗證結果：通過
- 驗證情境：<Scenario>
- 驗證版本：<commit SHA>

圖片 1：<附件>
```

純 API／DB 情境沒有畫面時，圖片欄填「截圖不適用」，並附可重現的請求、回應或查詢證據。

## 失敗處理

一致性審核未通過、skill 不可用、圖片未遮罩、附件上傳失敗或註記儲存未確認時，停止後續 Redmine 寫入或結案流程，回報已完成與未完成的部分。
