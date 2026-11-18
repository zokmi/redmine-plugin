---
name: redmine-doctor
description: Redmine MCP 出現 ECONNREFUSED、Couldn't reconnect、連線中斷或啟動逾時時，自動診斷並安全恢復已安裝 server，重連後繼續原任務。
---

# Redmine MCP doctor

遇到本機 Redmine MCP 連線錯誤，先執行 doctor。此流程依賴本機 CLI，即使 MCP 無法連線也能使用。

1. 從實際載入的本 SKILL.md 解析 plugin 根目錄 `../..`，確認 `scripts/doctor.ps1` 存在。
2. Windows 執行 `& '<plugin 絕對路徑>/scripts/doctor.ps1'`。用 PowerShell 單引號引用路徑，內含單引號加倍。Doctor 可自動啟動已停止的同一份已安裝 server，毋須再次詢問例行重啟。保留原本的環境與使用者權杖。
3. Doctor 成功後重新連接 plugin MCP，呼叫 `get_current_user`（多站指定 site）確認，然後繼續原任務。若前一次寫入沒有收到結果，先查詢是否已寫入，再決定是否補送。
4. Doctor 若回報埠占用、程序身分不符、權杖／設定不相容或缺少部署，將具體診斷回報使用者。依診斷使用 `redmine-setup` 處理設定；自動修復限制為本次一次，失敗後保留現場資訊。

macOS／Linux 依 README 的前景啟動或使用者服務管理方式檢查，Windows doctor 尚未涵蓋這些平台。輸出僅需狀態、處理結果及後續動作，避免金鑰、token 或完整設定內容。
