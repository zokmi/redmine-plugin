# 圖片必須上傳為附件：設計

日期：2026-08-18
狀態：已與需求方確認，待實作

## 背景與問題

`upload_attachment` 工具早已存在，且 `create_issue`／`update_issue`／`add_issue_note`
都支援 `uploads` 參數（`src/redmine_mcp/tools/issues.py`），token 鏈路完整。缺的是兩件事：

1. **server 指示詞完全沒提到圖片。** 模型被告知「佐證可以是截圖」
   （`guides/bug_report/report.md`），卻沒有任何一句要求「使用者給了圖，就必須上傳為附件」。
   結果是圖片常被用文字描述帶過，開單品質下降。
2. **只有磁碟上的檔案能上傳。** `safe_source` 限定檔案必須位於 `upload_dir` 內。
   當模型手上只有圖片內容、沒有合法路徑時，無路可走。

## 已確認的取捨

需求方已知悉並接受以下事實與風險：

- **模型拿不到使用者貼在對話裡圖片的原始 bytes。** 對話圖片以 image content block
  進入上下文，模型無法將其重新輸出為 base64。因此 `content_base64` 的實際使用者是
  「模型自己讀取某個磁碟檔案後編碼」的情境。
- **因此 `content_base64` 的實質效果是繞過 `upload_dir` 目錄限制。** 該限制存在的理由，
  是避免帶著 API key 的上傳請求把本機任意檔案（金鑰、憑證）送上 Redmine。
- **緩解措施**：新入口只接受圖像型別，並以 magic bytes 驗證，降低（但無法消除）
  「送出非圖片機敏檔案」的風險。需求方在知情下選擇此方案。

## 設計

### 1. `upload_attachment` 介面

| 參數 | 變更 | 說明 |
|---|---|---|
| `file_path` | 必填 → 選填 | 語意與防護完全不動 |
| `content_base64` | 新增 | 圖片內容的 base64；與 `file_path` 互斥 |
| `filename` | 不變 | 走 base64 時仍為選填 |
| `site` | 不變 | 寫入類工具，多站台時必填 |

驗證順序：

1. `sites.resolve_for_write(site)` 取得 client。
2. **停用開關維持全域**：`upload_dir`（含回退的 `download_dir`）未設定時，兩條路都停用。
   理由：管理者刻意不設目錄即是關閉上傳功能；若 base64 可繞過，該開關形同失效。
3. **互斥檢查**：兩者都給 → 拒絕；都沒給 → 拒絕。錯誤訊息明說該給哪一個。
4. **分支 A（`file_path`）**：現行邏輯原封不動
   （`safe_source` → 空檔拒絕 → `max_attachment_bytes`）。此路徑安全性零變更。
5. **分支 B（`content_base64`）**：
   - 剝除 `data:image/png;base64,` 這類 data URI 前綴（模型常這樣給，不剝會 decode 失敗）。
   - 以字串長度 `× 3/4` 做早期大小拒絕，避免為拒絕超大 payload 而多複製一份記憶體。
   - 嚴格模式 decode（`validate=True`），失敗回明確錯誤而非拋出原始 traceback。
   - 空內容拒絕；解碼後 bytes 再對 `max_attachment_bytes` 正式比對。
   - 交給型別偵測；非白名單圖像則拒絕。
6. 兩條路匯流至同一段 `post_binary` 上傳；回傳欄位不變
   （`site`／`token`／`filename`／`content_type`／`bytes`）。

### 2. 型別偵測與檔名改寫

新增兩個模組級純函式，與 `safe_source`、`guess_content_type` 同層，維持既有可單測風格。

`detect_image_type(data: bytes) -> tuple[str, str] | None`
依 magic bytes 判定，回傳 `(mime, 正規副檔名)`，判不出來回 `None`：

| 型別 | 特徵 | 回傳 |
|---|---|---|
| PNG | `89 50 4E 47 0D 0A 1A 0A` | `image/png`, `.png` |
| JPEG | `FF D8 FF` | `image/jpeg`, `.jpg` |
| GIF | `GIF87a` / `GIF89a` | `image/gif`, `.gif` |
| WebP | 前 4 byte `RIFF` 且第 8–11 byte `WEBP` | `image/webp`, `.webp` |

白名單刻意排除 **SVG**：文字格式、可內嵌 script，附件被瀏覽時有 XSS 風險，
且 magic bytes 無從驗證。BMP／TIFF 亦不收（YAGNI，有需要再加）。

`image_filename(candidate: str | None, ext: str) -> str`

1. 先以既有 `safe_upload_filename` 淨化（`candidate` 為空時用 `attachment`）。
2. 取 stem，一律接上偵測到的正規副檔名。
   Redmine 依副檔名決定 `content_type`，故 `螢幕截圖.jpg`（內容實為 PNG）
   會改寫成 `螢幕截圖.png`，確保上傳後能正確顯示。
3. 唯一例外：同型別的等價副檔名不動（`.jpeg` 判定為 JPEG 時保留 `.jpeg`）。

回傳的 `content_type` 採**偵測結果**，不對此路呼叫 `guess_content_type`；
`file_path` 路徑仍沿用 `guess_content_type`，行為不變。

預估 `tools/attachments.py` 再長約 40 行，仍屬單一職責，不需拆檔。

### 3. 指示詞規則

`server.py` 的 `_BASE_INSTRUCTIONS`，接在「開單的人多半不是 IT」段之後新增：

> 開單者提供的圖片（截圖、錯誤畫面）必須以 `upload_attachment` 上傳，取得 token 後帶入
> `create_issue`／`update_issue`／`add_issue_note` 的 `uploads` 參數，不可只在文字裡描述
> 畫面內容。圖片已在磁碟上時給 `file_path`（限 `upload_dir` 內）；只有圖片內容而無檔案
> 時給 `content_base64`。

`guides/bug_report/report.md` 的佐證章節與其檢核項補一句：截圖類佐證要走附件上傳，
description 內只留文字說明。change／feature guide 不動——其骨架沒有佐證章節，
加規則只是雜訊。

## 測試

擴充 `tests/test_upload_tools.py`，沿用既有中文測試名與 `MockTransport` 風格，
以 TDD 先紅後綠。

純函式層：

- `detect_image_type` 四種型別各一
- 非圖像內容（純文字、PDF magic）回 `None`
- 內容不足 4 byte 不拋例外
- `image_filename` 涵蓋副檔名改寫、無檔名、`.jpeg` 保留、路徑穿越檔名淨化

工具層：

- base64 成功上傳，回傳偵測到的 `content_type`
- `data:` URI 前綴可正確剝除
- 非法 base64 給明確錯誤
- 非圖像內容被拒
- 解碼後超過 `max_attachment_bytes` 被拒（含只靠字串長度即應早退的超大 payload）
- 空內容被拒
- `file_path` 與 `content_base64` 同時給／都不給，各自被拒
- `upload_dir` 未設定時 base64 亦停用
- 多站台未指定 `site` 仍被拒

另於 `tests/test_server.py` 加一項斷言：指示詞含圖片附件規則的關鍵字。

**不得退化的邊界**：既有 `file_path` 路徑的所有測試必須保持不變且繼續通過。
