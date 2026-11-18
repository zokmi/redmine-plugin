# MCP doctor 與連線異常復原

2026-11-18 初始檢查確認 127.0.0.1:18765 無監聽者，active.json 的 Node supervisor PID 已不存在。現有兩層服務只監督 worker，無法在 supervisor 消失後自行恢復。日誌未記錄退出原因。調查期間另一個 Python supervisor 已開始監聽，但狀態檔仍指向舊 PID，診斷必須保護未登錄的監聽者。

依使用者修正，移除排程復原，改為本機 doctor 指令。Windows 提供 scripts/doctor.ps1 及 Node CLI doctor。Agent 在 MCP 回報 ECONNREFUSED、重連失敗或啟動逾時時，自動載入 redmine-doctor skill 並執行一次診斷。

Doctor 檢查已安裝 manifest、PID／啟動時間／映像、埠所有權及 MCP 授權初始化。健康時結束；原程序消失且埠空閒時，以相同引擎與可驗證的設定啟動已安裝版本並驗證。PID 重用、外部監聽者、設定或權杖不相容則回報原因。健康但有錯誤的程序需要可證明身分後才可安全重啟。診斷不用 MCP 工具，服務無法連線時亦可執行。固定 doctor 入口放在使用者安裝目錄，避免 plugin 快取更新而失效。

Agent 修復成功後重連 MCP 並呼叫 get_current_user，繼續原任務。未確定結果的前次寫入，必須先查詢，再判斷是否補送。失敗後回報一次具體診斷，避免無限重啟。

測試使用隔離 Windows 部署、測試權杖與臨時埠，涵蓋停止後 doctor 恢復、健康服務不重啟、外部埠占用、PID 重用、中文路徑及授權檢查。移除 Register-ScheduledTask 及排程 XML。另保留 Python proxy 用戶端中途斷線的連線清理與實測。
