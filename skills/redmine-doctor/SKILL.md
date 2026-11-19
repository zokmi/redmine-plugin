---
name: redmine-doctor
description: Redmine MCP 出現 ECONNREFUSED、Couldn't reconnect、連線中斷或啟動逾時時，自動診斷並安全恢復已安裝 server，重連後繼續原任務。
---

# Redmine MCP doctor

遇到本機 Redmine MCP 連線錯誤，先執行 doctor。此流程依賴本機 CLI，即使 MCP 無法連線也能使用。

1. 從實際載入的本 SKILL.md 解析 plugin 根目錄 `../..`，確認 `scripts/doctor.ps1` 存在。
2. Windows 執行 `& '<plugin 絕對路徑>/scripts/doctor.ps1'`。用 PowerShell 單引號引用路徑，內含單引號加倍。Doctor 可自動啟動已停止的同一份已安裝 server；已確認身分的程序未監聽或持續回 502／503 時，會核對設定後重啟，毋須再次詢問例行重啟。以同一 Windows 使用者的加密快照暫時還原原本啟動環境，保留使用者權杖並核對設定；結束後恢復呼叫端環境。
3. Doctor 成功後重新連接 plugin MCP，呼叫 `get_current_user`（多站指定 site）確認，然後繼續原任務。若前一次寫入沒有收到結果，先查詢是否已寫入，再決定是否補送。
4. Doctor 若回報埠占用、程序身分不符、權杖／設定不相容或缺少部署，將具體診斷回報使用者。依診斷使用 `redmine-setup` 處理設定；自動修復限制為本次一次，失敗後保留現場資訊。

舊版 manifest 沒有可還原快照且回報 `ENVIRONMENT_CHANGED` 時，不要反覆執行 doctor 或猜測原 shell。說明缺少原環境快照；服務已停止且埠空閒時，可依使用者授權執行 `install-local-service.ps1 -Restart -AdoptEnvironment`，驗證並採用目前環境。該明確遷移不屬於 doctor 的例行重試；不得自行繞過設定或權杖核對。

macOS／Linux 依 README 的前景啟動或使用者服務管理方式檢查，Windows doctor 尚未涵蓋這些平台。輸出僅需狀態、處理結果及後續動作，避免金鑰、token 或完整設定內容。
