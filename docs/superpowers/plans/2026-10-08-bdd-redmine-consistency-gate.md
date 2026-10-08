# BDD Redmine Consistency Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 建立 BDD 全通過後的動態一致性審核契約與逐項 Redmine 證據格式。

**Architecture:** BDD 收尾流程在更新 issue 前提供上下文並選擇適用 skill；Redmine plugin 定義首選 skill、審核結果契約與逐項註記模板。審核代理維持唯讀，只有 PASS 可放行 issue 寫入。

**Tech Stack:** Markdown skills、既有 Python pytest 文件測試。

**Spec:** `docs/superpowers/specs/2026-10-08-bdd-redmine-consistency-gate-design.md`

## Global Constraints

- 子代理不得修改程式、測試、證據或 Redmine issue。
- `PASS` 以外的審核結果不得更新 Redmine。
- 每個修正項目必須緊接對應圖片或「截圖不適用」證據。
- 保留使用者既有未提交的 `README.md` 修改。

## Review Focus

- 沒有任何適用 skill：測試選擇器回傳 `UNAVAILABLE` 並阻擋更新。
- 一致性結果為 `FAIL` 或 `UNVERIFIED`：測試收尾流程不呼叫 issue 寫入。
- 多個修正項目與圖片：測試格式保持編號一對一配對。
- API／DB 情境沒有圖片：測試要求替代證據而非產生空圖片連結。
- 附件或註記儲存未確認：測試要求回報部分完成，不宣稱結案。

### Task 1: Extend consistency skill contract

**Files:**
- Modify: `skills/issue-code-consistency-check/SKILL.md`
- Modify: `skills/issue-code-consistency-check/references/report-example.md`

- [ ] **Step 1: Add failing documentation tests** for dynamic selection context, `UNAVAILABLE`, and explicit PASS gate.
- [ ] **Step 2: Run the focused pytest tests and verify they fail** because the contract sections are missing.
- [ ] **Step 3: Add the selector input, result contract, and read-only subagent rules** to the skill.
- [ ] **Step 4: Add a report example** containing per-item evidence and file/line/commit references.
- [ ] **Step 5: Run focused tests and verify they pass.**

### Task 2: Add Redmine item/evidence template

**Files:**
- Modify: `skills/redmine-issue-writing/SKILL.md`
- Create: `skills/redmine-issue-writing/references/bdd-verification-note.md`

- [ ] **Step 1: Add failing documentation tests** for the paired `修正項目 N` / `圖片 N` format and API/DB fallback.
- [ ] **Step 2: Run the focused tests and verify they fail.**
- [ ] **Step 3: Add the canonical note template and attachment ordering rules.**
- [ ] **Step 4: Run focused tests and verify they pass.**

### Task 3: Document BDD integration handoff

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the BDD handoff contract**: dynamic skill selection, child-agent gate, and Redmine update ordering.
- [ ] **Step 2: Run the complete documentation test suite.**
- [ ] **Step 3: Review the diff to confirm the existing README changes remain intact.**

### Verification

- [ ] Run the repository's full pytest suite from `redmine-issue-skills`.
- [ ] Confirm only intended plugin documentation and tests changed.
